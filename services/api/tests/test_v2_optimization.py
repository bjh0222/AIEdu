"""Optimization seam tests. Synthetic input, isolated DB, no paid HTTP."""
from uuid import uuid4
import pytest

from test_task_run_v2 import owner, post, task_run


def test_bounded_capacity_unknown_and_duplicate_calls(client):
    from app.v2.runtime import claim_root, authorize_call, mark_sent, settle
    from app.v2.contracts import DomainError
    headers, created, _ = task_run(client, False)
    from app.config import settings
    from app.v2.contracts import StartRun
    from app.v2.tasks import start_run
    started = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), str(uuid4()))
    snap = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    rid = snap["run_id"]
    lease = claim_root(rid, "capacity-fixture")
    args = (rid, "unit-a", "request-a", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-a")
    one = authorize_call(*args)
    assert authorize_call(*args) == one
    two = authorize_call(rid, "unit-b", "request-b", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-b")
    three = authorize_call(rid, "unit-c", "request-c", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-c")
    four = authorize_call(rid, "unit-d", "request-d", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-d")
    five = authorize_call(rid, "unit-e", "request-e", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-e")
    with pytest.raises(DomainError, match="PROVIDER_BACKPRESSURE"):
        authorize_call(rid, "unit-f", "request-f", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-f")
    mark_sent(one, "capacity-fixture", lease["epoch"])
    settle(one, "unknown", {"total_tokens": 3})
    settle(two, "success", {"total_tokens": 2})
    with pytest.raises(DomainError, match="PROVIDER_BACKPRESSURE"):
        authorize_call(rid, "unit-f", "request-f", snap["context_hash"], lease["epoch"], "capacity-fixture", "key-f")


def test_parallel_checker_preserves_all_items_and_blind_inputs(client, monkeypatch):
    import threading
    from copy import deepcopy
    from types import SimpleNamespace
    from app.db import SessionLocal
    from app.v2 import checker
    from app.v2.runtime import job, load
    from app.v2.contracts import sha
    _, snap = heading_task(client)
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    original = snap["units"][0]
    units = [dict(original, unit_id=f"parallel-{i}", body=f"literal body {i}", content_hash=sha(f"literal body {i}")) for i in range(3)]
    state["plan"]["units"] = units
    state["execution_policy"] = {"check_concurrency": 2}
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    first, observed = set(), []
    def fake_invoke(*args, **kwargs):
        bundle = kwargs["data"]
        assert not {"generation_model", "prior_judgement", "trace", "hidden_answers"}.intersection(bundle)
        uid = kwargs["unit_id"]
        with lock:
            initial = uid not in first
            first.add(uid)
            observed.append((bundle["check"]["item_id"], uid))
        if initial and uid in {"parallel-0", "parallel-1"}:
            barrier.wait(timeout=3)
        return {"result": "undecidable", "findings": []}, {"call_id": str(uuid4()), "model_family": "glm"}
    monkeypatch.setattr(checker, "for_role", lambda role, **kw: SimpleNamespace(llm_model_family="deepseek" if role == "generation" else "glm"))
    monkeypatch.setattr(checker, "invoke", fake_invoke)
    report = checker.evaluate(snap["run_id"], "fixture", 0, deepcopy(state), units, "version-fixture")
    expected = {(i["item_id"], u["unit_id"]) for i in state["check_plan"]["check_items"] if i["decision_type"] != "deterministic" for u in units if checker.applies(i, u, state["context"])}
    assert set(observed) == expected
    assert len(report["independent_review"]["traces"]) == len(expected)
    assert {c["item_id"] for c in report["checks"]} == {i["item_id"] for i in state["check_plan"]["check_items"]}
    assert report["calibration_status"] == "not_calibrated"


def test_network_verifier_explicit_proxy_limits_and_no_truth_upgrade(client, monkeypatch):
    import httpx
    from app.v2 import network_evidence as network
    from app.v2.contracts import DomainError
    monkeypatch.setattr(network, "discovered_proxy", lambda: None)
    assert network.verify_quote("https://www.w3.org/example", "literal")["reason"] == "SYSTEM_PROXY_UNAVAILABLE"
    for url in ("http://www.w3.org/example", "https://127.0.0.1/", "https://www.w3.org:8443/", "https://www.w3.org/?token=secret", "https://www.w3.org@localhost/"):
        with pytest.raises(DomainError, match="VERIFIER_URL_NOT_ALLOWED"):
            network.verify_quote(url, "literal")
    monkeypatch.setattr(network, "discovered_proxy", lambda: "http://127.0.0.1:1")
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<h1>A literal quote</h1><script>untrusted hidden</script>"))
    result = network.verify_quote("https://www.w3.org/example", "A literal quote", transport=transport)
    assert result["result"] == "satisfied" and result["professional_correctness"] == "not_run"
    assert network.verify_quote("https://www.w3.org/example", "untrusted hidden", transport=transport)["result"] == "undecidable"
    assert network.verify_quote("https://www.w3.org/example", "literal", "0"*64, transport=transport)["reason"] == "SOURCE_HASH_CHANGED"


def test_explicit_focused_scope_never_expands_broad_wording():
    from app.v2.contracts import TaskInput
    from app.v2.resolver import resolve_task, build_units
    input = TaskInput(title="只讲定义", raw_input="系统学习全书多章，但本次只讲定义", generation_scope="focused")
    context = resolve_task(input.model_dump(), input.model_fields_set)
    units = build_units(context, [], outline=[{"title": "第一章"}, {"title": "第二章"}])
    assert len(units) == 1


@pytest.mark.parametrize("domain,positive,negative", [
    ("math_logic", {"assertion":"polynomial_identity","left":"(x+1)**2","right":"x*x+2*x+1","variables":["x"]},
     {"assertion":"polynomial_identity","left":"(x+1)**2","right":"x*x+1","variables":["x"]}),
    ("cs_it", {"assertion":"all_cases_match","cases":[{"input":[1,2],"expected":3,"observed":3}]},
     {"assertion":"all_cases_match","cases":[{"input":[],"expected":0,"observed":1}]}),
    ("biology_life", {"assertion":"single_treatment_contrast","groups":[{"treatment":0,"controlled_variables":{"temperature":20}},{"treatment":1,"controlled_variables":{"temperature":20}}]},
     {"assertion":"single_treatment_contrast","groups":[{"treatment":0,"controlled_variables":{"temperature":20}},{"treatment":1,"controlled_variables":{"temperature":30}}]}),
    ("chemistry_materials", {"assertion":"atoms_and_charge_conserved","left":[{"coefficient":2,"atoms":{"H":2},"charge":0},{"coefficient":1,"atoms":{"O":2},"charge":0}],"right":[{"coefficient":2,"atoms":{"H":2,"O":1},"charge":0}]},
     {"assertion":"atoms_and_charge_conserved","left":[{"coefficient":1,"atoms":{"H":2},"charge":0},{"coefficient":1,"atoms":{"O":2},"charge":0}],"right":[{"coefficient":1,"atoms":{"H":2,"O":1},"charge":0}]}),
])
def test_four_disciplines_positive_negative_unknown_and_reuse(domain, positive, negative):
    from app.v2.assessment_assets import structured_claim
    from app.v2.spec_packages import build_package
    for value, expected in ((positive,"satisfied"),(negative,"violated"),({},"undecidable")):
        result = structured_claim(domain, value)
        assert result["result"] == expected
        assert result["professional_correctness"] == "not_run"
    for scene in ("knowledge_tutorial", "exercise"):
        package = build_package(domain, "非种子测试分支", scene)
        rubric = package["quality_profile"]["item_quality_rubric"]
        assert len(rubric["dimensions"]) == 4 and rubric["threshold"] is None
        assert all(d["score"] is None for d in rubric["dimensions"])
        assert package["calibration_status"] == "not_calibrated"


def test_structured_claim_wrapper_refuses_invented_formalization():
    from app.v2.checker import wrap_candidate
    from app.v2.contracts import sha
    body = "A program returns a value."
    unit = {"unit_id":"u","content_hash":sha(body),"body":body,"subject_bindings":[{"domain_key":"cs_it"}]}
    item = {"item_id":"i","verifier_binding":"independent_oracle.v1","system":"general","severity_class":"blocker"}
    output = wrap_candidate({"quote":body,"claim":"formal mismatch","verification_request":{"method":"structured_claim_contract","domain":"cs_it","value":{"assertion":"all_cases_match","cases":[{"input":[],"expected":0,"observed":1}]}}},
        item,{"check_plan_hash":"p"},unit,"v",{"call_id":"c","model_family":"glm"})
    assert output["status"] == "unsupported" and output["verification_result"] == "undecidable"


def heading_task(client):
    headers = owner(client)
    created = post(client, "/task-starts", {"title": "二分查找", "raw_input": "讲解二分法\n\n## 方法：二分查找\n先检查区间，再折半。\n\n## 结论：边界\n要求有序。", "mode": "demo"}, headers).json()
    snap = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    return headers, snap


def test_graph_has_only_real_planned_or_saved_nodes_and_etag(client):
    _, snap = heading_task(client)
    url = f"/api/v2/tasks/{snap['task_id']}/knowledge-graph"
    response = client.get(url)
    graph = response.json()
    assert graph["demo"] is True
    assert {n["unit_id"] for n in graph["nodes"]} == {u["unit_id"] for u in snap["units"]}
    assert all(n["source_hash"] for n in graph["nodes"])
    assert graph["logical_relations"].startswith("unknown")
    assert client.get(url, headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    assert client.get(url).json() == graph


def test_manifest_is_stable_read_only_bound_to_calls_and_task(client):
    _, created, snap = task_run(client)
    url = f"/api/v2/tasks/{created['task_id']}/runs/{snap['run_id']}/manifest"
    one = client.get(url).json()
    two = client.get(url).json()
    assert one == two
    assert one["version_id"] == snap["version_id"]
    assert len(one["calls"]) == len(snap["calls"])
    assert one["replay"]["new_paid_attempt_required"] is True
    assert all(c["provider_identity"].endswith("not_upstream_attestation") for c in one["calls"])
    _, other, _ = task_run(client, False)
    assert client.get(url.replace(created["task_id"], other["task_id"])).status_code == 404


def test_cards_backlinks_exact_bytes_cas_and_idempotent_receipt(client):
    headers, snap = heading_task(client)
    tid, vid = snap["task_id"], snap["version_id"]
    payload = {"version_id": vid, "content_hash": snap["content_hash"]}
    path = f"/tasks/{tid}/card-extractions"
    response = post(client, path, payload, headers, "extract-card")
    assert response.status_code == 202, response.text
    cards = response.json()["cards"]
    assert {c["kind"] for c in cards} == {"method", "conclusion"}
    assert post(client, path, payload, headers, "extract-card").json() == response.json()
    assert client.get("/api/v2/actions/receipt", headers={"Idempotency-Key": "extract-card"}).json()["response"] == response.json()
    units = {u["unit_id"]: u for u in snap["units"]}
    for card in cards:
        source = card["source"]
        assert units[source["unit_id"]]["body"].encode()[source["start_byte"]:source["end_byte"]].decode() == card["quote"]
        assert card["status"] == "candidate"
        assert card["conditions"] is None
    card = cards[0]
    url = f"/api/v2/tasks/{tid}/versions/{vid}/cards/{card['card_id']}"
    body = {"expected_revision": 1, "quote_hash": card["source"]["quote_hash"], "status": "confirmed"}
    revised = client.put(url, json=body, headers=headers | {"Idempotency-Key": "confirm-card"})
    assert revised.status_code == 200, revised.text
    assert revised.json()["confirmation_authority"] == "user_curation_not_professional_certification"
    assert client.put(url, json=body, headers=headers | {"Idempotency-Key": "stale-card"}).status_code == 409
    graph = client.get(f"/api/v2/tasks/{tid}/knowledge-graph").json()
    assert any(e["kind"] == "contains" for e in graph["edges"])
    assert client.get(f"/api/v2/tasks/{tid}/snapshot").json()["quality"] == snap["quality"]


def test_cards_reject_wrong_hash_foreign_version_and_missing_evidence(client):
    headers, _, snap = task_run(client)
    headers, other, _ = task_run(client, False)
    payload = {"version_id": snap["version_id"], "content_hash": "0" * 64}
    assert post(client, f"/tasks/{snap['task_id']}/card-extractions", payload, headers).status_code == 409
    assert post(client, f"/tasks/{other['task_id']}/card-extractions", payload | {"content_hash": snap["content_hash"]}, headers).status_code == 404
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.insights import bind_cards
    with SessionLocal() as db:
        result = bind_cards(db.get(TutorialVersion, snap["version_id"]), [{"kind": "conclusion", "title": "x", "unit_id": snap["units"][0]["unit_id"], "quote": "not in source"}])
    assert not result["cards"] and result["rejected"]


def test_explicit_feedback_revision_withdrawal_and_future_context(client):
    headers = owner(client)
    body = {"expected_revision": 0, "event_id": "preference-1", "kind": "preference", "text": "多举反例"}
    first = post(client, "/learning-feedback", body, headers, "feedback-1")
    assert first.status_code == 201, first.text
    assert post(client, "/learning-feedback", body, headers, "feedback-1").json() == first.json()
    assert post(client, "/learning-feedback", body | {"event_id": "different"}, headers).status_code == 409
    task = post(client, "/tasks", {"title": "偏好冻结", "raw_input": "解释定义"}, headers).json()
    original = client.get(f"/api/v2/tasks/{task['task_id']}/snapshot").json()["task"]
    assert original["learner_feedback"]["events"][0]["text"] == "多举反例"
    post(client, "/learning-feedback", {"expected_revision": 1, "event_id": "withdraw-1", "kind": "retract", "target_event_id": "preference-1"}, headers)
    profile = client.get("/api/v2/learning-profile").json()
    assert profile["active"] == [] and profile["learning_gain"] == "not_measured"
    assert len(profile["events"]) == 2
    unchanged = client.get(f"/api/v2/tasks/{task['task_id']}/snapshot").json()["task"]
    assert unchanged == original
    later = post(client, "/tasks", {"title": "撤回后", "raw_input": "解释定义"}, headers).json()
    assert not client.get(f"/api/v2/tasks/{later['task_id']}/snapshot").json()["task"]["learner_feedback"]["events"]


def test_management_search_archive_restore_references_and_atomic_batch(client):
    headers, created, snap = task_run(client)
    tid = created["task_id"]
    path = f"/api/v2/managed-resources/tasks/{tid}/impact"
    impact = client.get(path).json()
    target = {"kind": "tasks", "id": tid, "expected_hash": impact["resource"]["resource_hash"], "impact_hash": impact["impact_hash"]}
    request = {"action": "archive", "items": [target]}
    assert post(client, "/managed-resource-actions", request, headers).status_code == 409
    response = post(client, "/managed-resource-actions", request | {"acknowledge_references": True}, headers, "archive-1")
    assert response.status_code == 200, response.text
    assert response.json()["source_bytes_deleted"] is False
    assert post(client, "/managed-resource-actions", request | {"acknowledge_references": True}, headers, "archive-1").json() == response.json()
    assert client.get("/api/v2/managed-resources/tasks").json()["total"] == 0
    assert client.get("/api/v2/managed-resources/tasks?archived=true").json()["total"] == 1
    assert client.get(f"/api/v2/tasks/{tid}/snapshot").json() == snap
    fresh = client.get(path).json()
    restore = {"kind": "tasks", "id": tid, "expected_hash": fresh["resource"]["resource_hash"], "impact_hash": fresh["impact_hash"]}
    assert post(client, "/managed-resource-actions", {"action": "restore", "items": [restore, target], "acknowledge_references": True}, headers).status_code == 422
    assert post(client, "/managed-resource-actions", {"action": "restore", "items": [restore], "acknowledge_references": True}, headers).status_code == 200
    assert client.get("/api/v2/managed-resources/tasks?search=演示").json()["total"] == 1


def test_scope_has_exactly_two_values_explicit_wins_and_manifest_records_it(client):
    from app.v2.resolver import resolve_task
    value = {"title": "x", "raw_input": "系统学习全书", "generation_scope": "focused", "domain_key": "general", "spec_bindings": [], "requested_artifacts": ["tutorial"]}
    explicit = resolve_task(value, {"generation_scope"}, {"generation_scope": "system"})
    assert explicit["generation_scope"] == "focused" and explicit["scope_resolution"]["source"] == "explicit"
    assert resolve_task(value, set())["generation_scope"] == "system"
    headers = owner(client)
    assert post(client, "/tasks", {"title": "x", "raw_input": "x", "generation_scope": "chapter"}, headers).status_code == 422


def test_frozen_call_budget_preserves_receipts_and_blocks_new_dispatch(client, monkeypatch):
    from app.v2.tasks import start_run
    from app.v2.contracts import StartRun, DomainError
    from app.v2.runtime import authorize_call, claim_root, settle
    _, created, _ = task_run(client, False, execution_policy={
        "task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False})
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), str(uuid4()))
    from app.v2.runtime import write, job, load, dump
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state["budget"] = {"calls": 1}
        obj.state_json = dump(state)
    lease = claim_root(run["run_id"], "budget-worker")
    args = (run["run_id"], "unit", "request", created["context_hash"], lease["epoch"], "budget-worker", "budget-key")
    call = authorize_call(*args)
    settle(call, "success", {"total_tokens": 1})
    assert authorize_call(*args) == call
    with pytest.raises(DomainError, match="CALL_BUDGET_EXHAUSTED"):
        authorize_call(run["run_id"], "other", "other", created["context_hash"], lease["epoch"], "budget-worker", "other-key")


def test_worker_finishes_generation_before_one_complete_check_and_reuses_requests(client, monkeypatch):
    import json
    import threading
    from types import SimpleNamespace
    from app.config import settings
    from app.v2 import worker, checker, model_roles
    from app.v2.tasks import start_run, snapshot
    from app.v2.contracts import StartRun, digest
    from app.v2.runtime import write, job, load, dump
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    def profile(role, **kwargs):
        return SimpleNamespace(llm_base_url="https://api.deepseek.com/v1", llm_model="deepseek-flash", llm_model_family="deepseek" if role == "generation" else "glm",
            llm_thinking="enabled", llm_reasoning_effort="high", llm_max_tokens=0, llm_configured=True)
    monkeypatch.setattr(model_roles, "role_config", lambda: ({}, {"generation": "fixture"}))
    monkeypatch.setattr(model_roles, "for_role", profile)
    monkeypatch.setattr(checker, "for_role", profile)
    _, created, _ = task_run(client, False, execution_policy={
        "task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False})
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        assert "unit_pipeline" not in state["execution_policy"]
        unit = state["plan"]["units"][0]
        state["plan"]["units"] = [{**unit, "unit_id": f"pipeline-{i}", "title": f"单元{i}", "depends_on": []} for i in range(2)]
        state["plan"]["plan_hash"] = digest(state["plan"]["units"])
        obj.state_json = dump(state)
    both_generated, first_check_started = threading.Event(), threading.Event()
    seen = []
    generated = set()
    checked = set()
    lock = threading.Lock()
    class Provider:
        def complete_json(self, system, user):
            data = json.loads(user)
            with lock:
                seen.append((system, user))
            if "check" in data or "checks" in data:
                with lock:
                    assert generated == {"pipeline-0", "pipeline-1"}
                    first_check_started.set()
                    checked.add(data["unit"]["unit_id"])
                return ({"checks": [{"item_id": i["item_id"], "result": "undecidable", "findings": []} for i in data["checks"]]}
                    if "checks" in data else {"result": "undecidable", "findings": []}), 1, {"total_tokens": 2}
            with lock:
                generated.add(data["unit"]["unit_id"])
                if generated == {"pipeline-0", "pipeline-1"}:
                    both_generated.set()
                assert not first_check_started.is_set()
            return {"body": "## 方法\n\n这是隔离测试中的解释正文。"}, 1, {"total_tokens": 2}
    worker.execute_run(run["run_id"], lambda _: Provider())
    snap = snapshot(created["task_id"])
    assert snap["state"] == "completed", snap["stop_reason"]
    assert both_generated.is_set() and first_check_started.is_set()
    assert checked == {"pipeline-0", "pipeline-1"}
    assert len(seen) == len(set(seen)), "final reduction must reuse identical pipeline check receipts"
    assert len(snap["calls"]) == len(seen)
    assert all(c["status"] == "settled" for c in snap["calls"])
    headers = owner(client)
    card = post(client, f"/tasks/{created['task_id']}/card-extractions", {"version_id": snap["version_id"], "content_hash": snap["content_hash"]}, headers).json()["cards"][0]
    url = f"/api/v2/tasks/{created['task_id']}/versions/{snap['version_id']}/cards/{card['card_id']}"
    revised = client.put(url, json={"expected_revision": 1, "quote_hash": card["source"]["quote_hash"], "status": "confirmed"}, headers=headers | {"Idempotency-Key": str(uuid4())})
    assert revised.status_code == 200, revised.text
    assert client.get("/api/v2/knowledge/cards?q=方法").json()["total"] == 1
    next_task = post(client, "/tasks", {"title": "方法复用", "raw_input": "解释方法", "knowledge": {"guidance_enabled": True}}, headers).json()
    frozen = client.get(f"/api/v2/tasks/{next_task['task_id']}/snapshot").json()["task"]["knowledge_sources"]
    assert any(v.get("source_kind") == "tutorial_card" and v["source"] == card["source"] for v in frozen)
    client.put(url, json={"expected_revision": 2, "quote_hash": card["source"]["quote_hash"], "status": "retracted"}, headers=headers | {"Idempotency-Key": str(uuid4())})
    assert client.get("/api/v2/knowledge/cards").json()["total"] == 0


def test_demo_card_confirmation_never_enters_retrieval(client):
    headers, snap = heading_task(client)
    card = post(client, f"/tasks/{snap['task_id']}/card-extractions", {"version_id": snap["version_id"], "content_hash": snap["content_hash"]}, headers).json()["cards"][0]
    result = client.put(f"/api/v2/tasks/{snap['task_id']}/versions/{snap['version_id']}/cards/{card['card_id']}",
        json={"expected_revision": 1, "quote_hash": card["source"]["quote_hash"], "status": "confirmed"}, headers=headers | {"Idempotency-Key": str(uuid4())})
    assert result.status_code == 200
    assert client.get("/api/v2/knowledge/cards?q=方法").json()["items"] == []


def test_spec_default_can_be_unset_without_overwriting_other_assets(client):
    from app.v2.spec_packages import build_package
    headers = owner(client)
    spec = build_package("general", "默认切换", "knowledge_tutorial")["spec"] | {"is_default": True}
    response = post(client, "/format-specs", {"spec": spec}, headers)
    assert response.status_code == 201, response.text
    created = response.json()
    result = client.put(f"/api/v2/format-specs/{created['id']}", json={"expected_version": created["version"], "spec": spec | {"is_default": False}}, headers=headers | {"Idempotency-Key": str(uuid4())})
    assert result.status_code == 200, result.text
    assert result.json()["is_default"] is False and result.json()["version"] == created["version"] + 1

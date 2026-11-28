from types import SimpleNamespace
from uuid import uuid4

from test_v2_optimization import heading_task


def test_unit_batch_preserves_criteria_missing_duplicate_and_blindness(client, monkeypatch):
    from app.db import SessionLocal
    from app.v2 import checker, checker_batch
    from app.v2.runtime import job, load
    from app.v2.quality import check_snapshot
    _, snap = heading_task(client)
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    unit = snap["units"][0]
    unit["items"] = [{"item_id": "question", "stem_markdown": "Find x if x+1=2",
        "answer": {"final": "SECRET_ANSWER", "reasoning": "SECRET_REASON"}}]
    state["context"]["domain_key"] = "math_logic"
    baseline = check_snapshot(state, [unit], "version")
    calls = []
    def fake(*args, **kw):
        calls.append(kw)
        if kw["stage"] == "blind_solving":
            assert set(kw["data"]) == {"items"}
            assert all(set(item) == {"item_id", "stem_markdown"} for item in kw["data"]["items"])
            assert "SECRET_ANSWER" not in str(kw["data"])
            assert "SECRET_REASON" not in str(kw["data"])
            value = {"solutions": []}
        elif kw["stage"] == "item_reviewing":
            assert len(kw["data"]["items"]) <= 4
            from app.v2.checker import REVIEW_SKILL
            from app.v2.skills import entry as skill_entry
            assert kw["entries"] == [skill_entry(REVIEW_SKILL, "C")]
            assert f"# assessment-review.v{REVIEW_SKILL['version']}" in kw["system"]
            value = {"item_reviews": []}
        else:
            assert not {"generation_model", "prior_judgement", "trace"}.intersection(kw["data"])
            values = [{"item_id": i["item_id"], "result": "satisfied", "findings": []} for i in kw["data"]["checks"]]
            value = {"checks": values + values[:1]}  # duplicate reply is not authority
        return value, {"call_id": str(uuid4()), "model_family": "glm"}
    monkeypatch.setattr(checker_batch, "invoke", fake)
    profile = lambda family: SimpleNamespace(llm_model_family=family, llm_model="fixture", llm_base_url="https://fixture.invalid")
    monkeypatch.setattr(checker_batch, "for_role", lambda *a, **k: profile("glm"))
    monkeypatch.setattr("app.v2.capacity.get_capacity", lambda *a: {"max_output_tokens":128000})
    result = checker_batch.evaluate_batch(snap["run_id"], "fixture", 0, state, [unit], "version", None,
        baseline, profile("deepseek"), profile("glm"), False)
    assert [c["stage"] for c in calls] == ["blind_solving", "item_reviewing", "checking", "checking_perturbation"]
    expected = {i["item_id"] for i in state["check_plan"]["check_items"]}
    assert {c["item_id"] for c in result["checks"]} == expected
    semantic = {i["item_id"] for i in state["check_plan"]["check_items"] if i["decision_type"] != "deterministic" and checker.applies(i, unit, state["context"])}
    assert all(c["result"] == "undecidable" for c in result["checks"] if c["item_id"] in semantic)
    assert len(result["independent_review"]["traces"]) == len(semantic)
    assert result["item_quality"]["independent_item_reviews"][0]["dimensions"]["reasoning"]["observation"] == "undecidable"


def test_blind_groups_preserve_small_request_and_bound_large_units():
    from app.v2.checker_batch import blind_groups
    small = [{"item_id":str(i)} for i in range(10)]
    assert blind_groups(small) == [small]
    large = [{"item_id":str(i)} for i in range(29)]
    groups = blind_groups(large)
    assert max(map(len,groups)) == 8
    assert [i for group in groups for i in group] == large


def test_quarantined_checklist_split_is_exact_recursive_and_content_preserving():
    import pytest
    from app.v2.checker_batch import split_quarantined_checklists
    from app.v2.contracts import digest, DomainError
    bundle = {"checks": [{"item_id": str(i)} for i in range(10)],
              "task": {"raw_input": "unchanged"}, "unit": {"body": "unchanged"},
              "sources": [{"text": "exact source"}], "review_scope": {"scope": "single"}}
    assert split_quarantined_checklists(bundle, set()) == [bundle]
    left = {**bundle, "checks": bundle["checks"][:5]}
    parts = split_quarantined_checklists(bundle, {digest(bundle), digest(left)})
    assert [len(p["checks"]) for p in parts] == [2, 3, 5]
    assert [c for p in parts for c in p["checks"]] == bundle["checks"]
    for part in parts:
        assert {k: v for k, v in part.items() if k != "checks"} == {k: v for k, v in bundle.items() if k != "checks"}
    assert split_quarantined_checklists({**bundle, "unit": {"body": "new"}}, {digest(bundle)}) == [
        {**bundle, "unit": {"body": "new"}}]
    one = {**bundle, "checks": bundle["checks"][:1]}
    with pytest.raises(DomainError, match="CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED"):
        split_quarantined_checklists(one, {digest(one)})


def test_split_checklist_reply_ownership_and_audit(monkeypatch):
    from app.v2 import checker_batch
    from app.v2.contracts import digest
    bundle = {"checks": [{"item_id": str(i)} for i in range(4)], "unit": {"body": "body"}}
    monkeypatch.setattr(checker_batch, "quarantined_checklists", lambda *a: {digest(bundle)})
    monkeypatch.setattr(checker_batch, "scheduled_output", lambda *a: 32768)
    calls = []
    def fake(*a, **kw):
        calls.append(kw)
        # Extra cross-batch replies must not overwrite the rightful reply.
        return {"checks": [{"item_id": c["item_id"], "result": "undecidable"} for c in kw["data"]["checks"]]
                + [{"item_id": "0", "result": "suspected"}]}, {"call_id": str(len(calls))}
    monkeypatch.setattr(checker_batch, "invoke", fake)
    result = checker_batch.invoke_checklists("run", "worker", 0, {}, "unit", "checking", "system", None, bundle, 32768, None)
    assert [len(c["data"]["checks"]) for c in calls] == [2, 2]
    assert set(result) == {"0", "1", "2", "3"}
    assert result["0"][0] is None  # duplicate in its assigned batch, not repaired by the other batch
    assert result["0"][1]["call_id"] == "1"
    assert result["2"][1]["call_id"] == "2"
    assert result["2"][2] == digest(calls[1]["data"])


def test_transport_split_requires_exact_acknowledged_ancestor(client):
    from app.v2.checker_batch import quarantined_checklists
    from app.v2.contracts import digest
    from app.v2.runtime import job, load, dump, write
    from app.v2.storage import CallDispatch
    from app.models import GenerationJob
    _, snap = heading_task(client)
    child_id, child_run = str(uuid4()), str(uuid4())
    with write() as db:
        parent = job(db, snap["run_id"])
        state = load(parent.state_json)
        acknowledgments = {}
        for case in ("valid", "not_acknowledged", "active_unknown", "wrong_hash", "other_provider", "other_plan", "current", "success"):
            cid, request_hash = str(uuid4()), digest(case)
            payload = {"checks": [{"item_id": case}]}
            acknowledgments[cid] = {"request_hash": "wrong" if case == "wrong_hash" else request_hash,
                                   "acknowledge_possible_duplicate_cost": case != "not_acknowledged"}
            db.add(CallDispatch(call_id=cid, root_job_id=child_id if case == "current" else parent.id,
                run_id=child_run if case == "current" else parent.run_id, unit_id="unit", epoch=0, attempt=1,
                idempotency_key=cid, request_hash=request_hash, input_hash=state["context"]["context_hash"],
                manifest_json=dump({"stage": "checking", "plan_hash": "other" if case == "other_plan" else state["plan"]["plan_hash"]}),
                request_json=dump({"messages": [{"role": "system", "content": "review"}, {"role": "user", "content": dump(payload)}]}),
                provider="other" if case == "other_provider" else "fixture", model="fixture", model_family="glm",
                status="unknown" if case == "active_unknown" else "quarantined",
                outcome="success" if case == "success" else "unknown",
                receipt_json=dump({"failure_code": "PROVIDER_TRANSPORT_UNKNOWN"})))
        state["unknown_quarantines"] = acknowledgments
        parent.state_json = dump(state)
        db.add(GenerationJob(id=child_id, run_id=child_run, goal_id=snap["task_id"], parent_job_id=parent.id,
            root_job_id=child_id, job_type="task_v2", status="queued", state_json=dump(state)))
    result = quarantined_checklists(child_run, state, "unit", "checking", "review",
                                   SimpleNamespace(llm_model="fixture", llm_base_url="fixture"))
    assert result == {digest({"checks": [{"item_id": "valid"}]})}


def test_resume_splits_only_exhausted_group_and_bounds_single_item():
    import pytest
    from app.v2.checker_batch import split_exhausted_groups
    from app.v2.contracts import digest, DomainError
    groups = [[{"item_id":str(i)} for i in range(start,start+4)] for start in (0,4,8)]
    plan = split_exhausted_groups(groups,{digest(groups[1]):32768},128000)
    assert [len(g) for g,n in plan] == [4,2,2,4]
    assert plan[0] == (groups[0],32768) and plan[-1] == (groups[-1],32768)
    assert [i for g,n in plan for i in g] == [i for g in groups for i in g]
    single = [groups[0][0]]
    assert split_exhausted_groups([single],{digest(single):32768},128000) == [(single,65536)]
    with pytest.raises(DomainError,match="VERIFIER_OUTPUT_CAPACITY_EXHAUSTED"):
        split_exhausted_groups([single],{digest(single):128000},128000)


def test_checklist_expands_only_exact_failed_request_and_bounds_capacity(monkeypatch):
    import pytest
    from app.v2 import checker_batch
    from app.v2.contracts import digest, DomainError
    payload = {"checks": [{"item_id": "C3.correctness"}], "unit": {"body": "fixture"}}
    failures = {}
    monkeypatch.setattr(checker_batch, "exhausted_groups", lambda *a, **k: failures)
    monkeypatch.setattr("app.v2.capacity.get_capacity", lambda *a: {"max_output_tokens": 65536})
    args = ("run", {}, "unit", "checking_perturbation", "prompt", SimpleNamespace(llm_base_url="fixture", llm_model="fixture"))
    assert checker_batch.scheduled_output(*args, payload, 16384) == 16384
    failures[digest(payload)] = 16384
    assert checker_batch.scheduled_output(*args, payload, 16384) == 32768
    assert checker_batch.scheduled_output(*args, {**payload, "unit": {"body": "changed"}}, 16384) == 16384
    failures[digest(payload)] = 65536
    with pytest.raises(DomainError, match="VERIFIER_OUTPUT_CAPACITY_EXHAUSTED"):
        checker_batch.scheduled_output(*args, payload, 16384)


def test_resume_only_trusts_settled_exhaustion_in_matching_ancestry(client):
    from app.v2.checker_batch import exhausted_groups
    from app.v2.contracts import digest
    from app.v2.runtime import job, load, dump, write
    from app.v2.storage import CallDispatch
    from app.models import GenerationJob
    _, snap = heading_task(client)
    child_id, child_run = str(uuid4()), str(uuid4())
    with write() as db:
        parent = job(db,snap["run_id"])
        state = load(parent.state_json)
        db.add(GenerationJob(id=child_id,run_id=child_run,goal_id=snap["task_id"],parent_job_id=parent.id,
            root_job_id=child_id,job_type="task_v2",status="queued",state_json=dump(state)))
        for case in ("valid","current","unknown","short","other_system","other_model","success"):
            items = [{"item_id":case,"stem_markdown":"synthetic input"}]
            cid = str(uuid4())
            db.add(CallDispatch(call_id=cid,root_job_id=child_id if case == "current" else parent.id,
                run_id=child_run if case == "current" else parent.run_id,unit_id="unit",epoch=0,attempt=1,
                idempotency_key=cid,request_hash=digest(case),input_hash=state["context"]["context_hash"],
                manifest_json=dump({"stage":"item_reviewing","plan_hash":state["plan"]["plan_hash"]}),
                request_json=dump({"messages":[{"role":"system","content":"other" if case == "other_system" else "review"},
                    {"role":"user","content":dump({"items":items})}],"parameters":{"max_tokens":32768}}),
                provider="fixture",model="other" if case == "other_model" else "fixture",model_family="glm",
                status="unknown" if case == "unknown" else "settled",outcome="success" if case == "success" else "failed",
                receipt_json=dump({"failure_code":"PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"}),
                usage_json=dump({"completion_tokens":100 if case == "short" else 32768})))
    result = exhausted_groups(child_run,state,"unit","item_reviewing","review",SimpleNamespace(llm_model="fixture"))
    assert result == {digest([{"item_id":"valid","stem_markdown":"synthetic input"}]):32768}
    full = exhausted_groups(child_run,state,"unit","item_reviewing","review",SimpleNamespace(llm_model="fixture"),payload_field=None)
    assert full == {digest({"items":[{"item_id":"valid","stem_markdown":"synthetic input"}]}):32768}

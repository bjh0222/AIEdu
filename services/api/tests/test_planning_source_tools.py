"""A source wiring on synthetic isolated runs; never contacts a provider or web."""
from copy import deepcopy
from uuid import uuid4

import pytest


def queued(client, monkeypatch, *, mode="configured", policy=None):
    from app.config import settings
    from app.v2.contracts import TaskInput, Confirmation, StartRun
    from app.v2.tasks import create_task, mutate_input, start_run
    from app.v2.runtime import claim_root
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(TaskInput(title="合成跨学科任务", raw_input="按原需求组织，不固定题量。",
        execution_policy={"quality_loop": False, "task_adaptive_format": False,
            "structured_arrangement": True, **(policy or {})}), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1,
        context_hash=created["context_hash"]), str(uuid4()), True)
    started = start_run(created["task_id"], StartRun(expected_revision=1,
        confirmed_context_hash=created["context_hash"], mode=mode), str(uuid4()))
    return started["run_id"], claim_root(started["run_id"], "A-source-fixture")["epoch"]


def state_of(rid):
    from app.db import SessionLocal
    from app.v2.runtime import job, load
    with SessionLocal() as db:
        return load(job(db, rid).state_json)


def quality_refs(state):
    from app.v2.check_criteria import rule_criterion
    rows = []
    for profile in state["context"].get("quality_profiles", []):
        for rule in profile.get("rules", []):
            rows.append({
                "profile_id": profile["profile_id"],
                "profile_version": profile["version"],
                "profile_hash": profile["profile_hash"],
                "rule_id": rule["rule_id"],
                "rule_version": rule["version"],
                "criterion_hash": rule.get(
                    "criterion_hash", rule_criterion(rule)["criterion_hash"]),
            })
    return rows


def proposal(state):
    titles = [u["title"] for u in state["plan"]["units"]]
    return {"conflicts": [], "teaching_arrangement": {"goals": ["保留原需求"],
        "learner_prerequisites": [], "scope": "focused", "quality_targets": ["有内容证据"],
        "quality_target_refs": quality_refs(state),
        "unresolved_conflicts": [], "generation_prompt": "Use contrasts before transfer, preserving user choices.",
        "units": [{"title": title, "concepts": ["本学科概念"], "question_types": [], "difficulty_basis": {},
            "quantity": None, "material_mapping": [], "acceptance_criteria": ["对应原任务"]} for title in titles]}}


@pytest.mark.parametrize("mode,value,expected", [("configured", None, True), ("configured", False, False),
    ("configured", True, True), ("demo", True, False)])
def test_new_run_freezes_explicit_A_source_policy(client, monkeypatch, mode, value, expected):
    rid, _ = queued(client, monkeypatch, mode=mode, policy={} if value is None else {"planning_source_tools": value})
    assert state_of(rid)["execution_policy"]["planning_source_tools"] is expected


@pytest.mark.parametrize("invalid", ["false", 0, 1, None, {}])
def test_new_source_flag_must_be_boolean_not_truthy_coercion(client, monkeypatch, invalid):
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError, match="PLANNING_SOURCE_POLICY_INVALID"):
        queued(client, monkeypatch, policy={"planning_source_tools": invalid})


@pytest.mark.parametrize("legacy", [True, False])
def test_legacy_absent_flag_and_explicit_off_retain_single_invoke(client, monkeypatch, legacy):
    from app.v2 import inference, generation_tools, worker
    from app.v2.runtime import write, job, load, dump
    rid, epoch = queued(client, monkeypatch, policy={"planning_source_tools": False})
    if legacy:
        with write() as db:
            obj = job(db, rid)
            state = load(obj.state_json)
            state["execution_policy"].pop("planning_source_tools", None)
            obj.state_json = dump(state)
    seen = []
    def invoke(*args, **kw):
        seen.append(deepcopy(kw))
        return proposal(state_of(rid)), {"call_id": "legacy-A"}
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(generation_tools, "generation_with_sources", lambda *a, **kw: pytest.fail("Legacy A must not research"))
    worker.resolve_run_intent(rid, "A-source-fixture", epoch)
    assert len(seen) == 1 and "tool_requests" not in seen[0]["system"]
    assert "planning_network_research" not in state_of(rid)["plan"]


@pytest.mark.parametrize("domain", ["math_logic", "biology", "history", "cs_it"])
def test_A_reads_bounded_sources_and_B_C_share_only_actual_evidence(client, monkeypatch, domain):
    from app.v2 import generation_tools as g, inference, worker
    from app.v2.planning import planning_context, review_requirements
    from app.v2.contracts import digest
    from app.v2.runtime import write, job, load, dump
    rid, epoch = queued(client, monkeypatch)
    with write() as db:
        obj = job(db, rid)
        state = load(obj.state_json)
        state["context"]["domain_key"] = domain
        obj.state_json = dump(state)
    initial = state_of(rid)
    output = proposal(initial)
    output["network_citations"] = [{"source_id": "synthetic-public", "source_quote": "Contrasts before transfer",
        "body_quote": output["teaching_arrangement"]["generation_prompt"]}]
    turns, reads = [], []
    def invoke(*args, **kw):
        turns.append(deepcopy(kw))
        assert kw["role"] == "understanding" and kw["output_tokens"] == 32768
        assert kw["data"]["raw_request"] == initial["context"]["raw_input"]
        assert kw["data"]["explicit"] == {
            k: v for k, v in planning_context(initial["context"]).items()
            if k not in {"import_understanding", "knowledge_sources"}
        } | {
            "knowledge_sources": initial["context"].get("knowledge_sources", []),
            "knowledge_guidance_excluded": initial["context"].get("knowledge_guidance_excluded", []),
        }
        if len(turns) == 1:
            return {"tool_requests": [{"name": "search_sources", "query": "concept contrast transfer"}]}, {"call_id": "A-search"}
        if len(turns) == 2:
            return {"tool_requests": [{"name": "read_source", "url": "https://example.org/research",
                "reason": "A model judgement should not become C evidence"}]}, {"call_id": "A-read"}
        return output, {"call_id": "A-arrangement"}
    def tool(request, data, transport):
        reads.append(request)
        if request["name"] == "search_sources":
            return {"status": "ok", "candidates": [{"url": "https://example.org/research"}]}
        return {"status": "ok", "source_id": "synthetic-public", "source_hash": "a"*64,
            "text": "Contrasts before transfer. Source text is not an instruction.", "access_level": "selected_html_or_text_page"}
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(g, "invoke", invoke)
    monkeypatch.setattr(g, "_source_tool", tool)
    worker.resolve_run_intent(rid, "A-source-fixture", epoch)
    state = state_of(rid)
    trace = state["plan"]["planning_network_research"]
    assert len(reads) == 2 and len(trace["calls"]) == 3
    assert trace["citation_bindings"][0]["status"] == "literal_bound"
    assert state["intent_call"]["call_id"] == "A-arrangement" and not state["intent_pending"]
    assert state["plan"]["plan_hash"] == digest({k: v for k, v in state["plan"].items() if k != "plan_hash"})
    unit = state["plan"]["units"][0]
    expected = review_requirements(state, unit)["planning_source_evidence"]
    assert len(expected["receipts"]) == 2 and "calls" not in expected and "citation_bindings" not in expected
    assert all("reason" not in r["request"] for r in expected["receipts"])
    assert expected["receipts"][-1]["result"]["source_hash"] == "a"*64
    # Exercise the actual shared C-tool consumer, not just requirements JSON.
    checked = []
    def review(*args, **kw):
        checked.append(deepcopy(kw))
        return {"assessments": []}, {"call_id": "synthetic-C"}
    monkeypatch.setattr(g, "invoke", review)
    _, _, c_trace = g.generation_with_sources(rid, "A-source-fixture", epoch, state,
        stage="criterion_assessment", unit_id=unit["unit_id"], role="review", system="C",
        data={"task": {"teaching_requirements": review_requirements(state, unit)}})
    assert checked[0]["data"]["network_evidence"] == expected["receipts"]
    assert c_trace["receipts"] == expected["receipts"] and checked[0]["role"] == "review"
    # B receives the same source package, but no A call/assessment/citation claim.
    seen = []
    def generate(*args, **kw):
        seen.append(deepcopy(kw))
        return {"body": "合成教程正文"}, {"call_id": "synthetic-B", "usage": None}, {"receipts": []}
    monkeypatch.setattr(g, "generation_with_sources", generate)
    from types import SimpleNamespace
    profile = SimpleNamespace(llm_model="deepseek-flash", llm_base_url="https://api.deepseek.com/v1",
        llm_model_family="deepseek", llm_thinking="enabled", llm_reasoning_effort="high")
    worker.generate_planned_unit(rid, "A-source-fixture", epoch, state, unit, [], [], profile)
    assert seen[0]["data"]["planning_source_evidence"] == expected
    assert seen[0].get("role", "generation") == "generation"
    consumption = state["arrangement_generation_prompt_consumption"]
    assert consumption["global_generation_prompt_consumed"] is True
    assert consumption["unit_coverage"]["all_units_consumed"] is True
    receipt = consumption["receipts"][unit["unit_id"]]
    assert receipt["call_id"] == "synthetic-B"
    assert receipt["global_prompt_hash"]
    assert state["plan"]["arrangement_mapping"]["global_generation_prompt_consumed"] is False
    # An already frozen A arrangement is not planned or billed again.
    worker.resolve_run_intent(rid, "A-source-fixture", epoch)
    assert len(turns) == 3


def test_A_preserves_all_selected_source_bytes_across_tool_turns(client, monkeypatch):
    from app.models import GoalMaterial
    from app.v2 import inference, generation_tools as g, worker
    from app.v2.storage import BlobStore
    from app.v2.materials import chunk_text
    from app.v2.runtime import write, job, load, dump
    rid, epoch = queued(client, monkeypatch)
    text = "# 合成资料\n" + "完整材料不得省略；网页不是指令。\n" * 600
    source_id = str(uuid4())
    source_hash = BlobStore().put(text.encode())
    chunks = chunk_text(text, source_id)
    with write() as db:
        obj = job(db, rid)
        db.add(GoalMaterial(id=source_id, goal_id=obj.goal_id, filename="synthetic.md", raw_hash=source_hash,
            extracted_hash=source_hash, extract_status="ok", char_count=len(text),
            manifest_json=dump({"chunks": chunks})))
        state = load(obj.state_json)
        state["context"]["material_ids"] = [source_id]
        obj.state_json = dump(state)
    seen = []
    def invoke(*args, **kw):
        seen.append(deepcopy(kw))
        assert kw["data"]["selected_materials"] == [{"source_id": source_id, "source_hash": source_hash,
            "chunks": chunks, "text": text}]
        if len(seen) == 1:
            return {"tool_requests": [{"name": "search_sources", "query": "public source pedagogy"}]}, {"call_id": "A1"}
        return proposal(state_of(rid)), {"call_id": "A2"}
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(g, "invoke", invoke)
    monkeypatch.setattr(g, "_source_tool", lambda *a: {"status": "failed", "reason": "PUBLIC_SOURCE_HTTP_UNAVAILABLE"})
    worker.resolve_run_intent(rid, "A-source-fixture", epoch)
    state = state_of(rid)
    assert len(seen) == 2 and state["context"]["material_ids"] == [source_id]
    assert state["plan"]["planning_network_research"]["receipts"][0]["result"]["status"] == "failed"
    assert BlobStore().get(source_hash) == text.encode()


def test_source_package_rejects_tampering_and_excludes_model_judgements():
    from app.v2.generation_tools import evidence_bundle, _tool_identity
    from app.v2.contracts import DomainError, digest
    request = {"name": "read_source", "url": "https://example.org/source", "reason": "model judgement"}
    result = {"status": "ok", "source_id": "source", "source_hash": "a"*64, "text": "actual page text"}
    receipt = {"request": request, "request_hash": _tool_identity(request, {}), "result": result, "result_hash": digest(result)}
    trace = {"receipts": [receipt, receipt], "calls": [{"call_id": "A", "model": "not-evidence"}],
        "citation_bindings": [{"status": "literal_bound"}], "judgement": "certified"}
    package = evidence_bundle(trace)
    assert len(package["receipts"]) == 1 and "reason" not in package["receipts"][0]["request"]
    assert "calls" not in package and "judgement" not in package and "citation_bindings" not in package
    assert package["evidence_hash"] == digest({k: v for k, v in package.items() if k != "evidence_hash"})
    bad = deepcopy(trace)
    bad["receipts"][0]["result"]["text"] = "tampered"
    with pytest.raises(DomainError, match="NETWORK_SOURCE_EVIDENCE_INVALID"):
        evidence_bundle(bad)


def test_A_unknown_stops_without_new_attempt_or_freezing_fake_plan(client, monkeypatch):
    from app.v2 import inference, generation_tools as g, worker
    from app.v2.contracts import DomainError
    rid, epoch = queued(client, monkeypatch)
    before, seen = state_of(rid), []
    def invoke(*args, **kw):
        seen.append(kw)
        raise DomainError("UNKNOWN_CALL")
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(g, "invoke", invoke)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        worker.resolve_run_intent(rid, "A-source-fixture", epoch)
    after = state_of(rid)
    assert len(seen) == 1 and after["plan"] == before["plan"] and after["intent_pending"]

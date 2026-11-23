"""Known length failures partition losslessly, only on explicit child resumes."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.contracts import DomainError, request_hash
from app.v2.research_length_recovery import (
    C_REVIEW_ENTRY,
    LEGACY_REVIEW_OUTPUT_TOKENS,
    REVIEW_OUTPUT_TOKENS,
    matching_length_failure,
    resume_partitions,
    split_data,
)
from test_research_consumer_contract import candidate


@pytest.fixture
def envelope(candidate):
    unit, binding, _ = candidate
    data = {"unit": unit, "teaching_requirements": {"whole_user_input": "不可覆盖原始需求"},
        "research_rubric": {**binding["content"], "source_catalog": [
            {"source_id": "s:a"}, {"source_id": "s:b"}]},
        "source_claims": [], "target_dimension_ids": ["a", "b"], "full_rubric_hash": "whole-frozen-rubric",
        "binding_hash": "whole-frozen-binding", "candidate_identity": "candidate", "review_batch_version": "v2"}
    for dim, claim in zip(data["research_rubric"]["dimensions"], data["research_rubric"]["source_claims"]):
        dim["indicators"] = [{"indicator_id": dim["dimension_id"] + "-I", "rule": "complete original indicator"}]
        dim["anchors"] = {"0": "violated", "1": "partial", "2": "supported"}
        claim["source_id"] = "s:" + dim["dimension_id"]
    data["source_claims"] = deepcopy(data["research_rubric"]["source_claims"])
    return data


def test_split_retains_all_indicators_anchors_body_A_and_whole_identity(envelope):
    before = deepcopy(envelope)
    parts = split_data(envelope, "failed-call", "failed-hash")
    assert envelope == before
    assert [p["target_dimension_ids"] for p in parts] == [["a"], ["b"]]
    assert [p["research_rubric"]["dimensions"][0] for p in parts] == envelope["research_rubric"]["dimensions"]
    for part, source in zip(parts, ("s:a", "s:b")):
        for field in ("unit", "teaching_requirements", "full_rubric_hash", "binding_hash", "candidate_identity"):
            assert part[field] == before[field]
        assert part["source_claims"] == part["research_rubric"]["source_claims"]
        assert part["research_rubric"]["source_catalog"] == [{"source_id": source}]
        assert part["partition_recovery"]["failed_call_id"] == "failed-call"
        assert part["partition_recovery"]["parent_target_dimension_ids"] == ["a", "b"]


def test_single_dimension_stops_instead_of_retrying_or_subdividing(envelope):
    part = split_data(envelope, "f", "h")[0]
    with pytest.raises(DomainError, match="RESEARCH_REVIEW_MINIMUM_BATCH_EXHAUSTED"):
        split_data(part, "single-failure", "single-hash")


@pytest.mark.parametrize("change,code", [
    (lambda d: d.update(target_dimension_ids=["a"]), "TARGETS_INVALID"),
    (lambda d: d["research_rubric"]["dimensions"][0].update(source_refs=["absent"]), "SOURCE_UNBOUND"),
])
def test_invalid_targets_or_lost_sources_rejected(envelope, change, code):
    change(envelope)
    with pytest.raises(DomainError, match=code):
        split_data(envelope, "f", "h")


@pytest.fixture
def identity():
    profile = SimpleNamespace(llm_base_url="https://fixture.invalid/v1", llm_model="glm-fixture",
        llm_temperature=0.3, llm_thinking="enabled", llm_reasoning_effort="high", llm_json_mode=True)
    manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": "context", "plan_hash": "plan",
        "unit_id": "u", "stage": "rubric_reviewing", "role": "review",
        "entries": [C_REVIEW_ENTRY]}
    messages = [{"role": "system", "content": "unchanged system"}, {"role": "user", "content": "unchanged data"}]
    parameters = {"max_tokens": LEGACY_REVIEW_OUTPUT_TOKENS[0], "temperature": 0.3,
        "thinking": "enabled", "reasoning_effort": "high", "json_mode": True}
    call = {"call_id": "f", "request_hash": request_hash(manifest, messages, profile.llm_base_url,
        profile.llm_model, parameters, {}), "provider": profile.llm_base_url, "model": profile.llm_model,
        "status": "settled", "outcome": "failed", "receipt": {
            "failure_code": "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failure_diagnostics": {
                "finish_reason": "length", "validation_reason": "OUTPUT_LENGTH_LIMIT", "content_characters": 0}}}
    return call, manifest, messages, profile, parameters


def test_only_exact_settled_empty_length_failure_qualifies(identity):
    call, *args = identity
    assert matching_length_failure([call], *args) == call


def test_current_review_budget_has_exact_failure_identity(identity):
    call, manifest, messages, profile, parameters = identity
    # The active ceiling is the model capacity; REVIEW_OUTPUT_TOKENS is only a
    # planning hint (None). Use a realistic capacity value for the identity.
    current = {**parameters, "max_tokens": 128000}
    call["request_hash"] = request_hash(
        manifest, messages, profile.llm_base_url, profile.llm_model, current, {})
    assert matching_length_failure([call], manifest, messages, profile, current) == call


@pytest.mark.parametrize("change", [
    lambda c: c.update(outcome="unknown"), lambda c: c.update(status="quarantined"),
    lambda c: c.update(status="sent"), lambda c: c.update(request_hash="other"),
    lambda c: c.update(model="other-model"), lambda c: c.update(provider="https://other.invalid"),
    lambda c: c["receipt"]["failure_diagnostics"].update(finish_reason="stop"),
    lambda c: c["receipt"]["failure_diagnostics"].update(content_characters=100),
    lambda c: c["receipt"]["failure_diagnostics"].update(validation_reason="JSON_PARSE_FAILED"),
    lambda c: c["receipt"].update(failure_code="READ_TIMEOUT"),
])
def test_unknown_partial_transport_and_different_request_never_trigger_recovery(identity, change):
    call, *args = deepcopy(identity)
    change(call)
    assert matching_length_failure([call], *args) is None


def test_success_preserves_original_reuse_regardless_of_failure_order(identity):
    call, *args = identity
    success = {**call, "outcome": "success", "receipt": {}}
    assert matching_length_failure([call, success], *args) is None
    assert matching_length_failure([success, call], *args) is None


def test_changed_system_or_parameters_do_not_match(identity):
    call, manifest, messages, profile, parameters = identity
    assert matching_length_failure([call], manifest, messages, profile, {**parameters, "max_tokens": 8192}) is None
    assert matching_length_failure([call], manifest, messages + [{"role": "system", "content": "changed"}], profile, parameters) is None
    assert matching_length_failure([call], {**manifest, "plan_hash": "changed"}, messages, profile, parameters) is None


def test_actual_ancestry_requires_explicit_child_and_exact_request(client, monkeypatch, envelope, identity):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import capacity, model_roles
    from app.v2.inference import request_messages
    from app.v2.runtime import dump, job, load
    from app.v2.storage import CallDispatch
    from test_v2_optimization import heading_task
    _, snap = heading_task(client)
    _, _, _, profile, parameters = identity
    monkeypatch.setattr(model_roles, "for_role", lambda role: profile)
    monkeypatch.setattr(capacity, "get_capacity", lambda *a: {"max_output_tokens": 32768,
        "context_tokens": 1000000, "safety_margin_tokens": 1, "estimate_method": "fixture", "source_uri_or_config": "fixture"})
    system = "unchanged exact review system"
    with SessionLocal() as db:
        old = job(db, snap["run_id"])
        state = load(old.state_json)
        child_run, child_id, call_id = str(uuid4()), str(uuid4()), str(uuid4())
        manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": state["context"]["context_hash"],
            "plan_hash": state["plan"]["plan_hash"], "unit_id": envelope["unit"]["unit_id"],
            "stage": "rubric_reviewing", "role": "review", "entries": [C_REVIEW_ENTRY]}
        req = request_hash(manifest, request_messages(system, envelope, ensure_json_literal=True), profile.llm_base_url,
                           profile.llm_model, parameters, {})
        db.add(CallDispatch(call_id=call_id, root_job_id=old.id, run_id=old.run_id, unit_id="u", epoch=1,
            attempt=1, idempotency_key=str(uuid4()), request_hash=req, input_hash=state["context"]["context_hash"],
            provider=profile.llm_base_url, model=profile.llm_model, model_family="fixture", status="settled", outcome="failed",
            request_json=dump({"messages": request_messages(system, envelope, ensure_json_literal=True), "parameters": parameters, "output_schema": {}}),
            receipt_json=dump(identity[0]["receipt"])))
        db.add(GenerationJob(id=child_id, root_job_id=child_id, parent_job_id=old.id, run_id=child_run,
            goal_id=old.goal_id, job_type="task_v2", status="queued", state_json=dump(state)))
        db.commit()
        dispatch_count = db.query(CallDispatch).count()
    assert resume_partitions(snap["run_id"], state, envelope, system) == [envelope]
    parts = resume_partitions(child_run, state, envelope, system)
    assert len(parts) == 2 and parts[0]["partition_recovery"]["failed_call_id"] == call_id
    assert resume_partitions(child_run, state, envelope, system + " changed") == [envelope]
    with SessionLocal() as db:
        assert db.get(CallDispatch, call_id).outcome == "failed"
        assert db.query(CallDispatch).count() == dispatch_count
        single_call_id, grandchild_id, grandchild_run = str(uuid4()), str(uuid4()), str(uuid4())
        single_hash = request_hash(manifest, request_messages(system, parts[0], ensure_json_literal=True), profile.llm_base_url,
            profile.llm_model, parameters, {})
        db.add(CallDispatch(call_id=single_call_id, root_job_id=child_id, run_id=child_run, unit_id="u", epoch=1,
            attempt=1, idempotency_key=str(uuid4()), request_hash=single_hash, input_hash=state["context"]["context_hash"],
            provider=profile.llm_base_url, model=profile.llm_model, model_family="fixture", status="settled", outcome="failed",
            request_json=dump({"messages": request_messages(system, parts[0], ensure_json_literal=True), "parameters": parameters, "output_schema": {}}),
            receipt_json=dump(identity[0]["receipt"])))
        db.add(GenerationJob(id=grandchild_id, root_job_id=grandchild_id, parent_job_id=child_id, run_id=grandchild_run,
            goal_id=snap["task_id"], job_type="task_v2", status="queued", state_json=dump(state)))
        db.commit()
    with pytest.raises(DomainError, match="RESEARCH_REVIEW_MINIMUM_BATCH_EXHAUSTED"):
        resume_partitions(grandchild_run, state, envelope, system)


def test_consumer_binds_each_child_audit_and_does_not_hide_negative(monkeypatch, candidate):
    from app.v2 import inference, research_length_recovery
    from app.v2.import_profiles import review_research_rubrics
    unit, binding, rows = deepcopy(candidate)
    rows[0].update(result="violated", score=0, repair_condition="Fix the actual defect")
    calls = []
    monkeypatch.setattr(research_length_recovery, "resume_partitions",
        lambda run, state, data, system: split_data(data, "original-failure", "failed-hash"))
    def fake_invoke(*args, **kw):
        calls.append(kw)
        target = kw["data"]["target_dimension_ids"]
        assert len(target) == 1 and kw["output_tokens"] == REVIEW_OUTPUT_TOKENS and kw["role"] == "review"
        assert kw["data"]["unit"] == unit
        return {"dimensions": [deepcopy(r) for r in rows if r["dimension_id"] in target]}, {"call_id": "call-" + target[0]}
    monkeypatch.setattr(inference, "invoke", fake_invoke)
    result = review_research_rubrics("r", "w", 1, {"context": {"research_rubrics": [binding]}}, [unit], "v")
    assert len(calls) == 2
    assert [(r["dimension_id"], r["result"], r["audit"]["call_id"]) for r in result["dimensions"]] == [
        ("a", "violated", "call-a"), ("b", "satisfied", "call-b")]
    assert result["calibration_status"] == "not_calibrated"
    assert all(b["partition_recovery"]["failed_call_id"] == "original-failure" for b in result["batches"])


def test_unknown_child_propagates_without_third_call_or_relabelling(monkeypatch, candidate):
    from app.v2 import inference, research_length_recovery
    from app.v2.import_profiles import review_research_rubrics
    unit, binding, rows = deepcopy(candidate)
    monkeypatch.setattr(research_length_recovery, "resume_partitions",
        lambda run, state, data, system: split_data(data, "f", "h"))
    calls = []
    def fake_invoke(*args, **kw):
        calls.append(kw)
        if len(calls) == 2:
            raise DomainError("UNKNOWN_CALL", 502)
        return {"dimensions": [rows[0]]}, {"call_id": "success-first"}
    monkeypatch.setattr(inference, "invoke", fake_invoke)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        review_research_rubrics("r", "w", 1, {"context": {"research_rubrics": [binding]}}, [unit], "v")
    assert len(calls) == 2

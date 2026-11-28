# -*- coding: utf-8 -*-
"""C->B optimization package loop, candidate protections and no-unknown replay."""
from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_task_run_v2 import task_run


POLICY = {"policy_version": "aiedu.repair.v2", "enabled": True, "max_attempts_per_finding": 6,
          "max_repair_rounds_per_run": 8, "root_call_budget": 12}


def _prepare(client, snap, *, confirmed=False, rounds=0, acceptance=True):
    from app.v2.runtime import dump, job, load, write
    from app.models import TutorialVersion
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state.update(mode="configured", optimization_policy=dict(POLICY), budget={"calls": 12},
                     optimization_rounds=rounds)
        state["plan"]["planning_skill"] = {"skill_id": "teaching-arrangement", "version": 1, "hash": "a"*64}
        obj.state_json, obj.state_revision = dump(state), obj.state_revision + 1
        obj.status, obj.cancel_epoch = "running", 1
        obj.worker_instance_id, obj.lease_until = "fixture", time.time() + 60
        version = db.get(TutorialVersion, snap["version_id"])
        checks = [{"item_id": item["item_id"], "result": "satisfied",
                   "method_version": item.get("verifier_binding")}
                  for item in state["check_plan"]["check_items"]]
        findings = []
        if confirmed:
            checks = [
                {**row, "result": "violated"} if row["item_id"] == "C1.coverage" else row
                for row in checks
            ]
            finding = {
                "finding_id": "f1", "status": "confirmed", "item_id": "C1.coverage",
                "quality_layer": "correctness", "hard_baseline": True,
                "normalized_claim_hash": "h",
                "location": {"unit_id": snap["units"][0]["unit_id"],
                             "unit_hash": snap["units"][0]["content_hash"],
                             "span_hash": "s" * 64,
                             "start_byte": 0, "end_byte": 1},
            }
            if acceptance:
                finding["acceptance_assertion"] = {"method": "coverage.v1"}
            findings.append(finding)
        quality = load(version.quality_report)
        quality.update({
            "checks": checks,
            "findings": findings,
            "blocking_reasons": ([{"item_id": "C1.coverage", "result": "violated"}]
                                 if confirmed else []),
        })
        version.quality_report = dump(quality)
        return obj.state_revision


def _package(snap, bindings):
    from app.v2.optimization import OPTIMIZATION_SKILL, PACKAGE_SCHEMA, SCHEMA_VERSION
    unit = snap["units"][0]
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": {k: OPTIMIZATION_SKILL[k] for k in ("id", "version", "hash")},
        "base_version": snap["version_id"], "base_content_hash": snap["content_hash"],
        "objective": "Improve the target without changing frozen constraints.",
        "frozen_bindings": bindings,
        "scope": {"allowed_change_modes": ["whole_unit", "related_units"],
                  "unit_ids": [unit["unit_id"]], "must_preserve": [], "forbidden_changes": []},
        "confirmed_findings": [], "unverified_hypotheses": [], "optimizations": [],
        "priority_order": [], "dependencies": [],
        "optimization_prompt": ("This is a complete natural-language instruction package. Preserve all frozen "
                                "constraints and return whole unit candidates only."),
        "acceptance_criteria": [{"item_id": "C1.coverage", "pass_condition": "coverage is satisfied",
                                 "verification_method": "coverage.v1"}],
        "stop_conditions": ["no_progress"], "recheck_scope": "full",
    }


def _confirmed_package_rows(data):
    return [{
        "finding_id": finding_id,
        "claim": "fixture confirmed defect",
        "evidence": {"method": "fixture"},
    } for finding_id in data.get("eligible_confirmed_finding_ids", [])]


def _candidate(snap, package, strategy="whole_unit"):
    from app.v2.optimization import CANDIDATE_SCHEMA_VERSION, digest
    unit = snap["units"][0]
    body = unit["body"] + "\n\n优化说明：补充边界条件。"
    return {"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": snap["version_id"],
            "base_content_hash": snap["content_hash"], "package_hash": digest(package),
            "strategy": strategy, "units": [{"unit_id": unit["unit_id"], "body": body}]}


def _run_loop(client, monkeypatch, snap, *, invoke_side_effect=None, checked=None, confirmed=False, rounds=0, research_binding=None, material_evidence=None):
    from app.config import settings
    from app.v2 import checker, generation_tools, inference, model_roles
    from app.v2.optimization import _frozen_bindings, execute_optimization

    _prepare(client, snap, confirmed=confirmed, rounds=rounds)
    if research_binding is not None:
        from app.v2.runtime import dump, job, load, write
        with write() as db:
            obj = job(db, snap["run_id"])
            state = load(obj.state_json)
            state["context"]["research_rubrics"] = [research_binding]
            obj.state_json = dump(state)
    if material_evidence is not None:
        from app.v2 import material_evidence as material_module
        from app.v2.runtime import dump, job, load, write
        with write() as db:
            obj = job(db, snap["run_id"])
            state = load(obj.state_json)
            state["context"]["material_ids"] = ["fixture-source"]
            state["plan"]["units"][0]["material_chunk_ids"] = ["fixture-chunk"]
            obj.state_json = dump(state)
        monkeypatch.setattr(material_module, "selected_material_evidence", lambda *a, **kw: material_evidence)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(model_roles, "for_role", lambda role, **kw: SimpleNamespace(
        llm_base_url="https://fixture.invalid", llm_model="fixture", llm_model_family="fixture",
        llm_thinking="enabled", llm_reasoning_effort="high", llm_max_tokens=0, llm_configured=True))
    seen = {"C": 0, "B": 0, "data": []}
    if checked is None:
        checked = {"checks": [{"item_id": "C1.coverage", "result": "satisfied", "method_version": "coverage.v1"}],
                   "findings": []}
    monkeypatch.setattr(checker, "evaluate", lambda *args, **kwargs: checked)

    def fake_invoke(*args, **kwargs):
        from app.v2.skills import entry as skill_entry, review_skill
        skill = review_skill()
        assert kwargs["entries"].count(skill_entry(skill, "C")) == 1
        assert f"# {skill['skill_id']}.v{skill['version']}" in kwargs["system"]
        seen["C"] += 1
        seen["data"].append(kwargs["data"])
        if kwargs["stage"] == "optimization_acceptance":
            return {"criteria": [{"item_id": c["item_id"], "result": "satisfied", "method": "model_observation",
                "evidence": [{"unit_id": snap["units"][0]["unit_id"], "quote": "优化说明：补充边界条件。"}]}
                for c in kwargs["data"]["criteria"]]}, {"call_id": "accept"}
        if invoke_side_effect:
            return invoke_side_effect(*args, **kwargs)
        package = _package(snap, kwargs["data"]["frozen_bindings"])
        package["confirmed_findings"] = _confirmed_package_rows(kwargs["data"])
        return package, {"call_id": "c1"}

    def fake_generation(*args, **kwargs):
        seen["B"] += 1
        seen["data"].append(kwargs["data"])
        candidate = _candidate(snap, kwargs["data"]["optimization_package"])
        return candidate, {"call_id": "b1"}, {"calls": [], "receipts": []}

    monkeypatch.setattr(inference, "invoke", fake_invoke)
    monkeypatch.setattr(generation_tools, "generation_with_sources", fake_generation)
    execute_optimization(snap["run_id"], "fixture", 1)
    return seen


def test_material_evidence_reaches_c_package_and_b_repair(client, monkeypatch):
    _, _, snap = task_run(client)
    evidence = {"schema_version": "aiedu.material-evidence.v1", "sources": [
        {"source_id": "fixture-source", "source_hash": "s", "text": "complete source"}],
        "coverage": {"complete": True}, "package_hash": "p"}
    seen = _run_loop(client, monkeypatch, snap, confirmed=True, material_evidence=evidence)
    assert seen["C"] >= 1 and seen["B"] == 1
    assert any(d.get("material_evidence") == evidence for d in seen["data"])
    assert sum(d.get("material_evidence") == evidence for d in seen["data"]) >= 2


def test_validate_package_binds_skill_plan_profile_rubric_and_version():
    from app.v2.optimization import OPTIMIZATION_SKILL, SCHEMA_VERSION, validate_package
    version = SimpleNamespace(id="v1", content_hash="a"*64)
    package = {
        "schema_version": SCHEMA_VERSION, "skill": {k: OPTIMIZATION_SKILL[k] for k in ("id", "version", "hash")},
        "base_version": "v1", "base_content_hash": "a"*64, "objective": "objective",
        "frozen_bindings": {"plan_hash": "p", "context_hash": "c", "planning_skill": {"skill_id": "A", "version": 1, "hash": "s"},
                            "import_understanding": [{"binding_hash": "b",
                                "profile": {"id": "p", "version": 1, "hash": "ph"},
                                "rubric": {"id": "r", "version": 1, "hash": "rh"}}],
                            "profile_hashes": [], "rubric_hashes": []},
        "scope": {"allowed_change_modes": ["whole_unit"], "unit_ids": ["u"], "must_preserve": [], "forbidden_changes": []},
        "confirmed_findings": [], "unverified_hypotheses": [], "optimizations": [],
        "priority_order": [], "dependencies": [],
        "optimization_prompt": ("Complete package with enough natural language for the optimizer. It explicitly states "
                                "scope, must-preserve items, acceptance checks, and stop conditions."),
        "acceptance_criteria": [{"item_id": "i", "pass_condition": "pass", "verification_method": "m"}],
        "stop_conditions": [], "recheck_scope": "full",
    }
    expected = package["frozen_bindings"]
    assert validate_package(package, version, expected_bindings=expected) is package
    with pytest.raises(Exception, match="OPTIMIZATION_FROZEN_BINDING_MISMATCH"):
        validate_package(package, version, expected_bindings={**expected, "plan_hash": "changed"})
    for invalid in ([], [package["acceptance_criteria"][0]] * 2,
                    [{"item_id": "i", "pass_condition": "", "verification_method": "m"}]):
        with pytest.raises(Exception, match="OPTIMIZATION_PACKAGE_INVALID"):
            validate_package(package | {"acceptance_criteria": invalid}, version, expected_bindings=expected)


def test_candidate_reuses_unchanged_dependencies_but_rejects_scope_and_body_drift():
    from app.v2.optimization import CANDIDATE_SCHEMA_VERSION, digest, sha, validate_candidate
    from test_task_run_v2 import task_run as _
    version = SimpleNamespace(id="v1", content_hash="a"*64)
    base = [
        {"unit_id": "u1", "body": "A", "content_hash": sha("A"), "depends_on": []},
        {"unit_id": "u2", "body": "B", "content_hash": sha("B"), "depends_on": ["u1"]},
        {"unit_id": "u3", "body": "C", "content_hash": sha("C"), "depends_on": ["u2"]},
    ]
    package = {"scope": {"unit_ids": ["u1", "u2", "u3"], "allowed_change_modes": ["related_units"]}}
    candidate = {"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v1",
                 "base_content_hash": "a"*64, "package_hash": digest(package), "strategy": "related_units",
                 "units": [{"unit_id": "u1", "body": "A2"}]}
    merged = validate_candidate(candidate, package, version, base)
    assert [u["body"] for u in merged] == ["A2", "B", "C"]
    full = {**candidate, "units": [{"unit_id": "u1", "body": "A2"}, {"unit_id": "u2"}, {"unit_id": "u3"}]}
    assert validate_candidate(full, package, version, base)[1]["unit_id"] == "u2"
    narrow = {"scope": {"unit_ids": ["u1", "u2"], "allowed_change_modes": ["related_units"]}}
    narrow_candidate = {**full, "package_hash": digest(narrow)}
    with pytest.raises(Exception, match="OPTIMIZATION_UNIT_OUT_OF_SCOPE"):
        validate_candidate(narrow_candidate, narrow, version, base)
    outside = {**candidate, "units": [{"unit_id": "u1", "body": "A2"}, {"unit_id": "u2"}, {"unit_id": "u3"},
                                      {"unit_id": "u4", "body": "D"}]}
    with pytest.raises(Exception, match="OPTIMIZATION_UNIT_OUT_OF_SCOPE"):
        validate_candidate(outside, package, version, base + [{"unit_id": "u4", "body": "D", "content_hash": sha("D"), "depends_on": []}])
    correctness_package = {
        "objective": "correctness_only.v1",
        "scope": {"unit_ids": ["u2"], "allowed_change_modes": ["whole_unit"]},
    }
    correctness_candidate = {
        "schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v1",
        "base_content_hash": "a" * 64, "package_hash": digest(correctness_package),
        "strategy": "whole_unit", "units": [{"unit_id": "u2", "body": "B2"}],
    }
    corrected = validate_candidate(
        correctness_candidate, correctness_package, version, base)
    assert [unit["body"] for unit in corrected] == ["A", "B2", "C"]
    item_unit = {"unit_id": "item", "artifact_role": "answer", "body": "old body", "content_hash": sha("old body"),
                 "items": [{"item_id": "q", "stem_markdown": "q", "answer": {"final": "a", "reasoning": "r"}}]}
    item_package = {"scope": {"unit_ids": ["item"], "allowed_change_modes": ["whole_item"]}}
    with pytest.raises(Exception, match="ITEM_BODY_DRIFT"):
        validate_candidate({"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v1",
            "base_content_hash": "a"*64, "package_hash": digest(item_package), "strategy": "whole_item",
            "units": [{"unit_id": "item", "body": "new body"}]}, item_package, version, [item_unit])


def test_loop_injects_package_into_B_and_persists_candidate(client, monkeypatch):
    _, _, snap = task_run(client)
    seen = _run_loop(client, monkeypatch, snap, confirmed=True)
    assert seen["C"] == 2 and seen["B"] == 1
    c_data = seen["data"][0]
    b_data = seen["data"][1]
    assert c_data["frozen_bindings"]["planning_skill"]["hash"] == "a"*64
    assert b_data["optimization_package"]["skill"]["hash"]
    from app.v2.contracts import digest
    assert b_data["immutable_candidate_metadata"]["package_hash"] == digest(b_data["optimization_package"])
    from app.v2.runtime import job, load
    with __import__("app.db", fromlist=["SessionLocal"]).SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_package_artifacts"] and state["optimization_candidate_artifacts"]
    assert state["best_so_far_version"] and state["best_so_far_version"] != snap["version_id"]
    assert state["optimization_history"][0]["full_recheck"] is True
    assert state["optimization_attempts"] == 1
    assert list(state["optimization_attempts_by_finding"].values()) == [1]


def test_pre_dispatch_capacity_error_does_not_consume_optimization_attempt(client, monkeypatch):
    from app.v2.optimization import rollback_pre_dispatch_attempt
    state = {
        "optimization_attempts": 1,
        "optimization_attempts_by_finding": {"candidate:v": 1},
    }
    audit = rollback_pre_dispatch_attempt(
        state, 0, ["candidate:v"], {"candidate:v": 0}, "CALL_CAPACITY_EXCEEDED")
    assert state["optimization_attempts"] == 0
    assert state["optimization_attempts_by_finding"] == {"candidate:v": 0}
    assert audit["provider_dispatch_created"] is False


def test_legacy_capacity_resume_rolls_back_only_unbilled_attempt():
    from app.v2.tasks import _rollback_unbilled_optimization_attempt
    from app.db import SessionLocal
    state = {
        "stop_reason": "CALL_CAPACITY_EXCEEDED",
        "optimization_policy": {"enabled": True},
        "optimization_attempts": 6,
        "optimization_attempts_by_finding": {"candidate:v": 1},
        "version_id": "v",
    }
    with SessionLocal() as db:
        _rollback_unbilled_optimization_attempt(
            db, SimpleNamespace(id="definitely-no-provider-dispatch"), state)
    assert state["optimization_attempts"] == 5
    assert state["optimization_attempts_by_finding"] == {"candidate:v": 0}
    assert state["resume_attempt_normalization"]["provider_dispatch_count"] == 0


def test_legacy_dependency_contract_resume_releases_one_replayable_attempt():
    from app.v2.tasks import _rollback_normalized_optimization_contract
    state = {
        "optimization_stop_reason": "DEPENDENCY_CANDIDATE_MISSING",
        "optimization_attempts": 6,
        "optimization_attempts_by_finding": {"candidate:v": 1},
        "version_id": "v",
    }
    _rollback_normalized_optimization_contract(state)
    assert state["optimization_attempts"] == 5
    assert state["optimization_attempts_by_finding"] == {"candidate:v": 0}
    assert state["optimization_contract_normalizations"][-1] == {
        "method_version": "legacy-wrapper-contract-attempt-rollback.v1",
        "previous_attempts": 6,
        "normalized_attempts": 5,
        "candidate_key": "candidate:v",
        "source_stop_reason": "DEPENDENCY_CANDIDATE_MISSING",
        "provider_receipt_replayed": True,
    }


def test_resume_applies_capacity_attempt_rollback_only_to_new_state(client, monkeypatch):
    from app.api import v2 as v2_api
    from app.v2.contracts import digest
    from app.v2.runtime import dump, job, load, write
    from app.v2.storage import CallDispatch
    from app.db import SessionLocal
    from test_task_run_v2 import owner, post
    _, _, snap = task_run(client)
    monkeypatch.setattr(v2_api, "execute_run", lambda *args, **kwargs: None)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state.update(stop_reason="CALL_CAPACITY_EXCEEDED",
                     optimization_policy={"enabled": True},
                     optimization_attempts=6,
                     optimization_attempts_by_finding={f"candidate:{state['version_id']}": 1})
        obj.state_json, obj.status = dump(state), "failed"
        obj.state_revision += 1
        revision, checkpoint = obj.state_revision, digest(state["plan"])
        db.query(CallDispatch).filter_by(root_job_id=obj.id).delete()
    response = post(client, f"/runs/{snap['run_id']}/resume",
                    {"expected_revision": revision, "checkpoint_hash": checkpoint}, owner(client))
    assert response.status_code == 202, response.text
    with SessionLocal() as db:
        child = db.query(__import__("app.models", fromlist=["GenerationJob"]).GenerationJob).filter_by(
            run_id=response.json()["run_id"]).one()
        new_state = load(child.state_json)
        assert new_state["optimization_attempts"] == 5
        assert list(new_state["optimization_attempts_by_finding"].values()) == [0]
        assert new_state["resume_attempt_normalization"]["provider_dispatch_count"] == 0
        old_state = load(job(db, snap["run_id"]).state_json)
        assert old_state["optimization_attempts"] == 6


def test_frozen_research_rubric_is_consumed_by_c_and_b(client, monkeypatch):
    _, _, snap = task_run(client)
    binding = {
        "id": "research-rubric-1", "version": 1, "hash": "r" * 64, "binding_hash": "b" * 64,
        "content": {"research_method_version": "ai-screened-diagnostic.v1", "source_catalog": [{"source_id": "s1"}],
                     "dimensions": [{"dimension_id": "knowledge.coverage"}]},
    }
    seen = _run_loop(client, monkeypatch, snap, confirmed=True, research_binding=binding)
    c_data = seen["data"][0]
    b_data = seen["data"][1]
    assert c_data["frozen_bindings"]["research_rubrics"] == [{
        "binding_hash": "b" * 64,
        "rubric": {"id": "research-rubric-1", "version": 1, "hash": "r" * 64},
        "research_method_version": "ai-screened-diagnostic.v1",
        "source_catalog_hash": __import__("app.v2.contracts", fromlist=["digest"]).digest([{"source_id": "s1"}]),
        "no_imported_material": True,
    }]
    assert b_data["research_rubrics"] == [binding]


def test_unconfirmed_observations_do_not_trigger_optimization(client, monkeypatch):
    _, _, snap = task_run(client)
    checked = {"checks": [{"item_id": "C1.coverage", "result": "satisfied", "method_version": "coverage.v1"}],
        "findings": [{"status": "suspected", "normalized_claim_hash": "unconfirmed", "claim": "needs external evidence"}],
        "tqi_verdict": "needs_review", "calibration_status": "not_calibrated"}
    seen = _run_loop(client, monkeypatch, snap, checked=checked)
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    from app.models import TutorialVersion
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert seen["C"] == 0 and seen["B"] == 0
    assert state["optimization_stop_reason"] == "ACTIONABLE_FINDING_REQUIRED"


def test_unknown_C_call_is_not_replayed(client, monkeypatch):
    from app.v2.contracts import DomainError
    _, _, snap = task_run(client)
    _prepare(client, snap, confirmed=True)
    from app.config import settings
    from app.v2 import inference, model_roles
    from app.v2.optimization import execute_optimization
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(model_roles, "for_role", lambda role, **kw: SimpleNamespace(llm_configured=True))
    calls = []
    def unknown(*args, **kwargs):
        calls.append(kwargs)
        raise DomainError("UNKNOWN_CALL", 502)
    monkeypatch.setattr(inference, "invoke", unknown)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        execute_optimization(snap["run_id"], "fixture", 1)
    assert len(calls) == 1


def test_no_confirmed_finding_skips_c_and_b_optimization(client, monkeypatch):
    _, _, snap = task_run(client)
    seen = _run_loop(client, monkeypatch, snap, rounds=1)
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert seen["C"] == 0 and seen["B"] == 0
    assert state["optimization_stop_reason"] == "ACTIONABLE_FINDING_REQUIRED"


def test_model_cannot_invent_confirmed_findings(client, monkeypatch):
    _, _, snap = task_run(client)
    def unbound(*args, **kwargs):
        package = _package(snap, kwargs["data"]["frozen_bindings"])
        package["confirmed_findings"] = [{"finding_id": "invented", "claim": "not tool verified", "evidence": {}}]
        return package, {"call_id": "C"}
    seen = _run_loop(client, monkeypatch, snap, confirmed=True, invoke_side_effect=unbound)
    assert seen["B"] == 0
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_stop_reason"] == "OPTIMIZATION_CONFIRMATION_NOT_BOUND"


def test_invalid_package_followup_has_source_bound_feedback_not_cached_input(client, monkeypatch):
    from app.v2.contracts import digest
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    _, _, snap = task_run(client)

    def incomplete(*args, **kwargs):
        package = _package(snap, kwargs["data"]["frozen_bindings"])
        del package["acceptance_criteria"]
        return package, {"call_id": "incomplete-C"}

    first = _run_loop(client, monkeypatch, snap, confirmed=True, invoke_side_effect=incomplete)
    assert first["B"] == 0 and first["data"][0]["package_validation_feedback"] == []
    second = _run_loop(client, monkeypatch, snap, confirmed=True)
    feedback = second["data"][0]["package_validation_feedback"]
    assert second["B"] == 1 and len(feedback) == 1
    assert feedback[0]["call_id"] == "incomplete-C"
    assert feedback[0]["validation_code"] == "OPTIMIZATION_PACKAGE_INVALID"
    assert feedback[0]["details"] == {"missing": ["acceptance_criteria"]}
    assert digest(first["data"][0]) != digest(second["data"][0])
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert feedback[0]["package_hash"] in state["optimization_package_artifacts"]
    assert state["optimization_attempts"] == 2


def test_rejected_acceptance_is_returned_to_optimizer_not_promoted_to_fact(client, monkeypatch):
    from app.v2.optimization import _persist_artifact
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    _, _, snap = task_run(client)
    _prepare(client, snap, confirmed=True)
    report = {"status": "unresolved", "candidate_hash": "unpublished",
              "criteria": [{"item_id": "task.original_requirements", "result": "violated",
                            "method": "per-method summary absent", "evidence": []}]}
    identity, _ = _persist_artifact(snap["run_id"], "fixture", 1, report, "optimization_acceptance", {"call_id": "prior-C"})
    seen = _run_loop(client, monkeypatch, snap, confirmed=True)
    feedback = seen["data"][0]["rejected_acceptance_feedback"][0]
    assert feedback["report_hash"] == identity and feedback["candidate_hash"] == "unpublished"
    assert feedback["unresolved_criteria"][0]["method"] == "per-method summary absent"
    assert "not_confirmed_fact" in feedback["authority"]
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_attempts"] == 1


def test_confirmed_finding_without_acceptance_is_not_washed_out(client, monkeypatch):
    _, _, snap = task_run(client)
    _prepare(client, snap, confirmed=True, acceptance=False)
    from app.config import settings
    from app.v2 import checker, generation_tools, inference, model_roles
    from app.v2.optimization import execute_optimization
    from app.v2.runtime import dump, job, load, write
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(model_roles, "for_role", lambda role, **kw: SimpleNamespace(llm_configured=True))
    monkeypatch.setattr(checker, "evaluate", lambda *args, **kwargs: {
        "checks": [{"item_id": "C1.coverage", "result": "satisfied", "method_version": "coverage.v1"}],
        "findings": []})
    def package_with_confirmed(*args, **kwargs):
        package = _package(snap, kwargs["data"]["frozen_bindings"])
        package["confirmed_findings"] = _confirmed_package_rows(kwargs["data"])
        return package, {"call_id": "c1"}

    monkeypatch.setattr(inference, "invoke", package_with_confirmed)
    monkeypatch.setattr(generation_tools, "generation_with_sources", lambda *args, **kwargs:
        (_candidate(snap, kwargs["data"]["optimization_package"]), {"call_id": "b1"}, {"calls": [], "receipts": []}))
    execute_optimization(snap["run_id"], "fixture", 1)
    with write() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_stop_reason"] == "regression_or_unresolved"
    assert state.get("best_so_far_version") in (None, snap["version_id"])


def test_quality_projection_strips_audit_and_bounds_quotes_but_keeps_decisions():
    from app.v2.contracts import canonical
    from app.v2.optimization import quality_report_projection
    quality = {
        "quality_status": "needs_review", "tqi_verdict": "needs_review",
        "findings": [{"finding_id": "f1", "status": "confirmed", "claim": "x"}],
        "checks": [{"item_id": "C1.coverage", "result": "violated"}],
        "automated_assessment": {
            "criteria": [{
                "unit_id": "u1", "check_id": "C1.coverage", "result": "satisfied",
                "processing_status": "complete",
                "audit": {"usage": {"blob": "y" * 200000}, "call_id": "c"},
                "clauses": [{"clause_id": "requirement", "result": "violated",
                             "reason": "r" * 5000,
                             "evidence": [{"field": "body", "start_byte": 1, "end_byte": 5,
                                           "span_hash": "s" * 64, "quote": "q" * 5000}]}],
            }],
            "audits": [{"usage": {"blob": "y" * 200000}}],
            "quality_preflight": {"matrix_hash": "m", "method_version": "v",
                "rows": [{"check_id": "c", "decision": "execute", "applicability_reason": "applicable"}]},
        },
        "diagnostic_review": {
            "dimensions": [{"dimension_id": "d1", "unit_id": "u1", "result": "undecidable",
                            "score": None, "indicators": [{"indicator_id": "i", "result": "undecidable"}],
                            "evidence": [{"field": "body", "quote": "z" * 9000, "span_hash": "t" * 64}],
                            "audit": {"usage": {"blob": "y" * 200000}}}],
            "audits": [{"usage": {"blob": "y" * 200000}}],
            "research_evidence": {"source_catalog": ["big"]},
        },
        "research_evidence": {"source_catalog": [{"a": "b"}], "no_imported_material": True},
    }
    projection = quality_report_projection(quality, "minimal")
    assert projection["quality_status"] == "needs_review" and projection["tqi_verdict"] == "needs_review"
    assert projection["findings"][0]["finding_id"] == "f1"
    assert projection["findings"][0]["status"] == "confirmed"
    assert projection["checks"] == quality["checks"]
    criterion = projection["automated_assessment"]["criteria"][0]
    assert criterion["result"] == "satisfied" and criterion["processing_status"] == "complete"
    assert "audit" not in criterion
    clause = criterion["clauses"][0]
    assert clause["result"] == "violated" and clause["evidence"][0]["span_hash"] == "s" * 64
    assert "quote" not in clause["evidence"][0]
    assert len(clause["reason"]) <= 120 + len("...[truncated_for_capacity]")
    assert projection["automated_assessment"]["audits"] == "call_audit_metadata_stripped_for_capacity"
    assert projection["diagnostic_review"]["audits"] == "call_audit_metadata_stripped_for_capacity"
    assert projection["diagnostic_review"]["dimensions"][0]["evidence"][0]["span_hash"] == "t" * 64
    assert "research_evidence" not in projection["diagnostic_review"]
    assert len(canonical(projection)) < 20000


def test_quality_projection_bounds_difficulty_and_math_audit_payloads():
    from app.v2.contracts import canonical
    from app.v2.optimization import quality_report_projection
    long_quote = "q" * 10000
    quality = {
        "findings": [{"finding_id": "f1", "status": "confirmed", "claim": "c" * 10000,
                      "evidence": {"field": "body", "quote": long_quote, "span_hash": "a" * 64}}],
        "checks": [{"item_id": "Q1", "result": "violated", "reason": "r" * 10000}],
        "check_items": [{"item_id": "Q1", "pass_condition": "p" * 10000,
                         "criterion_contract": {"check_id": "Q1", "criterion_hash": "h" * 64,
                                                "pass_condition": "p" * 10000}}],
        "difficulty_review": {"status": "completed", "result": "undecidable", "audits": [long_quote],
            "items": [{"unit_id": "u", "item_id": "i", "result": "undecidable",
                       "counts": {"nonroutine_operations": 2}, "comparisons": [],
                       "processing_status": "complete", "dimensions": {
                           "steps": [{"id": "s1", "depends_on": [], "reason": "r" * 10000,
                                      "evidence": [{"field": "answer.reasoning", "quote": long_quote,
                                                    "span_hash": "b" * 64}]}]},
                       "computation_load": {"level": "moderate", "evidence": [{"quote": long_quote}]}}]},
        "mathematical_evidence": {"method_version": "m", "items": [{"unit_id": "u", "item_id": "i",
            "results": [{"field": "answer.final", "span_hash": "c" * 64,
                         "expression_hash": "d" * 64, "verification_result": "unsupported",
                         "unsupported_reasons": ["syntax"]}]}]},
        "item_quality": {"item_evaluation": {"score": 1}, "rubrics": [{"aggregate_score": 1}],
            "independent_item_reviews": [{"item_id": "i", "unit_id": "u", "item_hash": "e" * 64,
                "audit": {"usage": {"blob": "x" * 100000}}, "dimensions": {"reasoning": {
                    "result": "undecidable", "observation": "concern", "reason": "r" * 10000,
                    "source": {"field": "body", "quote": long_quote, "quote_hash": "f" * 64}}}}]},
        "independent_review": {"status": "completed", "traces": [{"call_id": "c", "usage": {"blob": "x" * 100000},
            "contract_normalization": {"changes": [long_quote]}}]},
    }
    projection = quality_report_projection(quality, "minimal")
    assert projection["difficulty_review"]["items"][0]["result"] == "undecidable"
    assert projection["difficulty_review"]["items"][0]["dimensions"]["steps"][0]["id"] == "s1"
    assert "quote" not in projection["difficulty_review"]["items"][0]["dimensions"]["steps"][0]["evidence"][0]
    assert projection["mathematical_evidence"]["items"][0]["results"][0]["expression_hash"] == "d" * 64
    assert projection["item_quality"]["independent_item_reviews"][0]["dimensions"]["reasoning"]["result"] == "undecidable"
    assert projection["independent_review"]["traces"] == [{"call_id": "c", "unit_id": None,
        "item_id": None, "candidate_result": None, "perturbation": None,
        "model_family": None, "request_hash": None, "requested_request_hash": None}]
    assert len(canonical(projection)) < 30000


def test_c_planner_receives_bounded_projection_not_full_report(client, monkeypatch):
    _, _, snap = task_run(client)
    from app.models import TutorialVersion
    from app.v2.contracts import canonical
    from app.v2.runtime import dump, write
    unit_id = snap["units"][0]["unit_id"]
    with write() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        version.quality_report = dump({
            "quality_status": "needs_review",
            "automated_assessment": {
                "criteria": [{"unit_id": unit_id, "check_id": "C1.coverage", "result": "satisfied",
                              "processing_status": "complete", "audit": {"usage": {"blob": "y" * 500000}}}],
                "audits": [{"usage": {"blob": "y" * 500000}}],
            },
        })
    seen = _run_loop(client, monkeypatch, snap, confirmed=True)
    c_data = seen["data"][0]
    assert c_data["quality_report_projection"]["method_version"].startswith("aiedu.optimization-quality-projection")
    assert c_data["quality_report"]["automated_assessment"]["audits"] == "call_audit_metadata_stripped_for_capacity"
    assert "y" * 1000 not in canonical(c_data["quality_report"]).decode()


def test_fitted_payload_escalates_to_smaller_projection(monkeypatch):
    from app.v2 import capacity as capacity_module
    from app.v2.optimization import fitted_c_payload
    fake_capacity = {"context_tokens": 5000, "max_output_tokens": 100, "safety_margin_tokens": 0,
                     "estimate_method": "test", "source_uri_or_config": "test"}
    monkeypatch.setattr(capacity_module, "get_capacity", lambda base_url, model: fake_capacity)
    monkeypatch.setattr(capacity_module, "planned_output_tokens",
                        lambda profile, requested, cap: {"reserved_output_tokens": requested, "capacity_source": "test"})
    profile = SimpleNamespace(llm_base_url="https://x", llm_model="m",
                              llm_output_limit_tokens=None, llm_json_mode=True)
    build = lambda level: {"level": level, "pad": "x" * (4000 if level == "bounded" else 10)}
    payload, level = fitted_c_payload(profile, "system", build, 50)
    assert level == "minimal"
    assert payload["level"] == "minimal"


def test_validate_package_tolerates_numeric_skill_version_but_not_wrong_identity():
    from copy import deepcopy
    from app.v2.optimization import OPTIMIZATION_SKILL, SCHEMA_VERSION, validate_package
    version = SimpleNamespace(id="v1", content_hash="a" * 64)
    package = {
        "schema_version": SCHEMA_VERSION,
        "skill": {"id": OPTIMIZATION_SKILL["id"], "version": OPTIMIZATION_SKILL["version"],
                  "hash": OPTIMIZATION_SKILL["hash"]},
        "base_version": "v1", "base_content_hash": "a" * 64, "objective": "objective",
        "frozen_bindings": {}, "scope": {"allowed_change_modes": ["whole_unit"], "unit_ids": ["u"],
                                         "must_preserve": [], "forbidden_changes": []},
        "confirmed_findings": [], "unverified_hypotheses": [], "optimizations": [],
        "priority_order": [], "dependencies": [],
        "optimization_prompt": ("Complete natural-language optimization package with scope, preserved "
                                "constraints, acceptance checks, and explicit stop conditions."),
        "acceptance_criteria": [{"item_id": "i", "pass_condition": "pass", "verification_method": "m"}],
        "stop_conditions": [], "recheck_scope": "full",
    }
    # JSON number echo of a dotted version label is accepted when id/hash match.
    numeric = deepcopy(package)
    numeric["skill"]["version"] = 1.6
    assert validate_package(numeric, version) is numeric
    # A genuinely different version or hash is still rejected.
    wrong_version = deepcopy(package)
    wrong_version["skill"]["version"] = 9.9
    with pytest.raises(Exception, match="OPTIMIZATION_SKILL_MISMATCH"):
        validate_package(wrong_version, version)
    wrong_hash = deepcopy(package)
    wrong_hash["skill"]["hash"] = "b" * 64
    with pytest.raises(Exception, match="OPTIMIZATION_SKILL_MISMATCH"):
        validate_package(wrong_hash, version)


def test_bind_frozen_bindings_injects_missing_keys_but_rejects_conflict():
    from copy import deepcopy
    from app.v2.optimization import bind_frozen_bindings
    expected = {"a": 1, "nested": {"x": [1, 2]}, "b": None}
    out, note = bind_frozen_bindings({"frozen_bindings": {"a": 1}, "objective": "o"}, expected)
    assert out["frozen_bindings"] == expected and out["objective"] == "o"
    assert set(note["injected_keys"]) == {"nested", "b"}
    with pytest.raises(Exception, match="OPTIMIZATION_FROZEN_BINDING_MISMATCH"):
        bind_frozen_bindings({"frozen_bindings": {"a": 2}}, expected)
    with pytest.raises(Exception, match="OPTIMIZATION_FROZEN_BINDING_MISMATCH"):
        bind_frozen_bindings({"frozen_bindings": {"a": 1, "zzz": 9}}, expected)
    unchanged, no_note = bind_frozen_bindings({"frozen_bindings": deepcopy(expected)}, expected)
    assert no_note is None and unchanged["frozen_bindings"] == expected


def test_bind_frozen_bindings_repairs_wrapper_owned_hash_array_typos():
    from app.v2.optimization import bind_frozen_bindings
    expected_hash = "a" * 63 + "b"
    received_hash = "a" * 63 + "c"
    expected = {"plan_hash": "p", "profile_hashes": [expected_hash], "rubric_hashes": [expected_hash]}
    received = {"plan_hash": "p", "profile_hashes": [received_hash], "rubric_hashes": [expected_hash]}
    package, note = bind_frozen_bindings({"frozen_bindings": received}, expected)
    assert package["frozen_bindings"] == expected
    assert note["normalized_hash_fields"]["profile_hashes"]["received"] == [received_hash]
    with pytest.raises(Exception, match="OPTIMIZATION_FROZEN_BINDING_MISMATCH"):
        bind_frozen_bindings({"frozen_bindings": {
            "plan_hash": "wrong", "profile_hashes": [received_hash], "rubric_hashes": [expected_hash]
        }}, expected)


def test_optimization_run_status_maps_contract_failures_to_failed():
    from app.v2.worker import optimization_run_status
    assert optimization_run_status("optimization_completed_uncalibrated") == ("completed", None)
    assert optimization_run_status("optimization_round_limit") == ("completed", None)
    assert optimization_run_status("optimization_attempt_limit") == ("completed", None)
    assert optimization_run_status("no_progress") == ("completed", None)
    assert optimization_run_status("OPTIMIZATION_SKILL_MISMATCH") == ("failed", "OPTIMIZATION_SKILL_MISMATCH")
    assert optimization_run_status("CALL_CAPACITY_EXCEEDED") == ("failed", "CALL_CAPACITY_EXCEEDED")
    assert optimization_run_status("UNKNOWN_CALL") == ("interrupted", "UNKNOWN_CALL")
    assert optimization_run_status(None) == ("completed", None)


def test_completed_run_with_optimization_contract_failure_is_explicitly_resumable(client, monkeypatch):
    from app.api import v2 as v2_api
    from app.v2.contracts import digest
    from app.v2.runtime import dump, job, load, write
    from test_task_run_v2 import owner, post
    _, _, snap = task_run(client)
    monkeypatch.setattr(v2_api, "execute_run", lambda *args, **kwargs: None)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["optimization_stop_reason"] = "OPTIMIZATION_SKILL_MISMATCH"
        obj.state_json, obj.status = dump(state), "completed"
        obj.state_revision += 1
        revision, checkpoint = obj.state_revision, digest(state["plan"])
    headers = owner(client)
    response = post(client, f"/runs/{snap['run_id']}/resume",
                    {"expected_revision": revision, "checkpoint_hash": checkpoint}, headers)
    assert response.status_code == 202, response.text
    assert response.json()["run_id"] != snap["run_id"]


def test_validate_candidate_tolerates_missing_schema_version_but_not_wrong_one():
    from app.v2.contracts import digest
    from app.v2.optimization import validate_candidate
    version = SimpleNamespace(id="v1", content_hash="a" * 64)
    base_unit = {"unit_id": "u", "body": "old body", "content_hash": "h", "title": "t",
                 "artifact_role": "chapter", "depends_on": []}
    package = {"scope": {"unit_ids": ["u"], "allowed_change_modes": ["whole_unit"]}}
    candidate = {"base_version": "v1", "base_content_hash": "a" * 64,
                 "package_hash": digest(package), "strategy": "whole_unit",
                 "units": [{"unit_id": "u", "body": "new body"}]}
    units = validate_candidate(candidate, package, version, [base_unit])
    assert units[0]["body"] == "new body"
    wrong = dict(candidate)
    wrong["schema_version"] = "wrong"
    with pytest.raises(Exception, match="OPTIMIZATION_CANDIDATE_VERSION_INVALID"):
        validate_candidate(wrong, package, version, [base_unit])


def test_quality_protocol_manifest_covers_optimization_modules():
    from app.v2.review_contract import review_protocol_manifest
    files = review_protocol_manifest()["implementation_files"]
    for name in ("worker.py", "optimization.py", "optimization_recheck.py", "optimization_acceptance.py"):
        assert name in files

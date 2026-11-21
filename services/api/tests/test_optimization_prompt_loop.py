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


def _prepare(client, snap, *, confirmed=False, rounds=0):
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
        if confirmed:
            version = db.get(TutorialVersion, snap["version_id"])
            version.quality_report = dump({"findings": [{
                "finding_id": "f1", "status": "confirmed", "item_id": "C1.coverage",
                "normalized_claim_hash": "h", "location": {"unit_id": snap["units"][0]["unit_id"]},
            }], "checks": [{"item_id": "C1.coverage", "result": "violated", "method_version": "coverage.v1"}]})
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
    seen = _run_loop(client, monkeypatch, snap, material_evidence=evidence)
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


def test_candidate_rejects_scope_item_body_drift_and_missing_dependencies():
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
    with pytest.raises(Exception, match="DEPENDENCY_CANDIDATE_MISSING"):
        validate_candidate(candidate, package, version, base)
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
    item_unit = {"unit_id": "item", "artifact_role": "answer", "body": "old body", "content_hash": sha("old body"),
                 "items": [{"item_id": "q", "stem_markdown": "q", "answer": {"final": "a", "reasoning": "r"}}]}
    item_package = {"scope": {"unit_ids": ["item"], "allowed_change_modes": ["whole_item"]}}
    with pytest.raises(Exception, match="ITEM_BODY_DRIFT"):
        validate_candidate({"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v1",
            "base_content_hash": "a"*64, "package_hash": digest(item_package), "strategy": "whole_item",
            "units": [{"unit_id": "item", "body": "new body"}]}, item_package, version, [item_unit])


def test_loop_injects_package_into_B_and_persists_candidate(client, monkeypatch):
    _, _, snap = task_run(client)
    seen = _run_loop(client, monkeypatch, snap)
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


def test_frozen_research_rubric_is_consumed_by_c_and_b(client, monkeypatch):
    _, _, snap = task_run(client)
    binding = {
        "id": "research-rubric-1", "version": 1, "hash": "r" * 64, "binding_hash": "b" * 64,
        "content": {"research_method_version": "ai-screened-diagnostic.v1", "source_catalog": [{"source_id": "s1"}],
                     "dimensions": [{"dimension_id": "knowledge.coverage"}]},
    }
    seen = _run_loop(client, monkeypatch, snap, research_binding=binding)
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


def test_unconfirmed_observations_are_preserved_in_improved_draft(client, monkeypatch):
    _, _, snap = task_run(client)
    checked = {"checks": [{"item_id": "C1.coverage", "result": "satisfied", "method_version": "coverage.v1"}],
        "findings": [{"status": "suspected", "normalized_claim_hash": "unconfirmed", "claim": "needs external evidence"}],
        "tqi_verdict": "needs_review", "calibration_status": "not_calibrated"}
    _run_loop(client, monkeypatch, snap, checked=checked)
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    from app.models import TutorialVersion
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
        result = db.get(TutorialVersion, state["best_so_far_version"])
        quality = load(result.quality_report)
    assert quality["tqi_verdict"] == "needs_review"
    assert quality["findings"][0]["status"] == "suspected"


def test_unknown_C_call_is_not_replayed(client, monkeypatch):
    from app.v2.contracts import DomainError
    _, _, snap = task_run(client)
    _prepare(client, snap)
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


def test_explicit_pedagogical_followup_not_blocked_by_absence_of_confirmed(client, monkeypatch):
    _, _, snap = task_run(client)
    seen = _run_loop(client, monkeypatch, snap, rounds=1)
    from app.v2.runtime import job, load
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert seen["B"] == 1 and state["optimization_rounds"] == 2
    assert state["optimization_stop_reason"] == "optimization_completed_uncalibrated"


def test_model_cannot_invent_confirmed_findings(client, monkeypatch):
    _, _, snap = task_run(client)
    def unbound(*args, **kwargs):
        package = _package(snap, kwargs["data"]["frozen_bindings"])
        package["confirmed_findings"] = [{"finding_id": "invented", "claim": "not tool verified", "evidence": {}}]
        return package, {"call_id": "C"}
    seen = _run_loop(client, monkeypatch, snap, invoke_side_effect=unbound)
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

    first = _run_loop(client, monkeypatch, snap, invoke_side_effect=incomplete)
    assert first["B"] == 0 and first["data"][0]["package_validation_feedback"] == []
    second = _run_loop(client, monkeypatch, snap)
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
    _prepare(client, snap)
    report = {"status": "unresolved", "candidate_hash": "unpublished",
              "criteria": [{"item_id": "task.original_requirements", "result": "violated",
                            "method": "per-method summary absent", "evidence": []}]}
    identity, _ = _persist_artifact(snap["run_id"], "fixture", 1, report, "optimization_acceptance", {"call_id": "prior-C"})
    seen = _run_loop(client, monkeypatch, snap)
    feedback = seen["data"][0]["rejected_acceptance_feedback"][0]
    assert feedback["report_hash"] == identity and feedback["candidate_hash"] == "unpublished"
    assert feedback["unresolved_criteria"][0]["method"] == "per-method summary absent"
    assert "not_confirmed_fact" in feedback["authority"]
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_attempts"] == 1


def test_confirmed_finding_without_acceptance_is_not_washed_out(client, monkeypatch):
    _, _, snap = task_run(client)
    _prepare(client, snap, confirmed=True)
    from app.config import settings
    from app.v2 import checker, generation_tools, inference, model_roles
    from app.v2.optimization import execute_optimization
    from app.v2.runtime import dump, job, load, write
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(model_roles, "for_role", lambda role, **kw: SimpleNamespace(llm_configured=True))
    monkeypatch.setattr(checker, "evaluate", lambda *args, **kwargs: {
        "checks": [{"item_id": "C1.coverage", "result": "satisfied", "method_version": "coverage.v1"}],
        "findings": []})
    monkeypatch.setattr(inference, "invoke", lambda *args, **kwargs:
        (_package(snap, kwargs["data"]["frozen_bindings"]), {"call_id": "c1"}))
    monkeypatch.setattr(generation_tools, "generation_with_sources", lambda *args, **kwargs:
        (_candidate(snap, kwargs["data"]["optimization_package"]), {"call_id": "b1"}, {"calls": [], "receipts": []}))
    execute_optimization(snap["run_id"], "fixture", 1)
    with write() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["optimization_stop_reason"] == "regression_or_unresolved"
    assert state.get("best_so_far_version") in (None, snap["version_id"])

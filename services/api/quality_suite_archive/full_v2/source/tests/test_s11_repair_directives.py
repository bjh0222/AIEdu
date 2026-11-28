from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.v2.contracts import DomainError, digest


def _finding():
    return {
        "finding_id": "f1",
        "status": "confirmed",
        "item_id": "C3.correctness",
        "quality_layer": "correctness",
        "hard_baseline": True,
        "normalized_claim_hash": "claim-1",
        "location": {"unit_id": "u1", "start_byte": 0, "end_byte": 4},
    }


def _package():
    return {
        "schema_version": "aiedu.optimization.v1",
        "skill": {"id": "aiedu.optimization-prompt.v2", "version": "1.6.0", "hash": "h"},
        "base_version": "v1",
        "base_content_hash": "c" * 64,
        "objective": "Fix the confirmed defect.",
        "frozen_bindings": {"plan_hash": "p", "context_hash": "x", "check_plan_hash": "k"},
        "scope": {
            "allowed_change_modes": ["whole_item", "whole_unit", "related_units"],
            "unit_ids": ["u1"],
            "must_preserve": ["Keep answers in the same item."],
            "forbidden_changes": ["Do not change user requirements."],
        },
        "confirmed_findings": [{"finding_id": "f1", "claim": "The item is incorrect.", "evidence": {}}],
        "unverified_hypotheses": [],
        "optimizations": [],
        "priority_order": ["f1"],
        "dependencies": [],
        "optimization_prompt": "Fix the confirmed target while preserving all frozen requirements, "
                               "user constraints, answer pairing, source identity and acceptance checks.",
        "acceptance_criteria": [{
            "item_id": "C3.correctness",
            "pass_condition": "The corrected item is supported.",
            "verification_method": "criterion review",
        }],
        "stop_conditions": ["no_progress"],
        "recheck_scope": "full",
        "directives": [{
            "finding_id": "f1",
            "layer": "correctness",
            "target": {"unit_id": "u1", "item_ordinal": 1, "location_hash": None},
            "change_mode": "whole_item",
            "required_change": "Replace the incorrect item with a supported one.",
            "acceptance_assertion": {
                "deterministic_test": {
                    "method": "required_literals",
                    "unit_ids": ["u1"],
                    "literals": ["corrected"],
                },
                "criterion_check": {
                    "kind": "acceptance_item",
                    "item_id": "C3.correctness",
                },
            },
            "forbidden_changes": ["Do not change user requirements."],
        }],
    }


def _units(body="corrected"):
    return [{
        "unit_id": "u1",
        "body": body,
        "content_hash": digest(body),
        "artifact_role": "exercise",
        "items": [],
    }]


def test_directive_normalization_uses_executable_modes_and_whitelisted_assertions():
    from app.v2.repair_directives import normalize_directives

    package, directives = normalize_directives(_package())
    assert directives[0]["directive_id"].startswith("rd:")
    assert directives[0]["change_mode"] in {"whole_item", "whole_unit", "related_units"}
    assert package["directives"][0]["acceptance_assertion"]["deterministic_test"]["method"] == "required_literals"
    assert package["directives"][0]["acceptance_assertion"]["criterion_check"]["kind"] == "acceptance_item"


def test_directive_layer_is_bound_to_the_confirmed_finding_layer():
    from app.v2.repair_directives import normalize_directives

    package = _package()
    package["confirmed_findings"][0]["quality_layer"] = "correctness"
    package["directives"][0]["layer"] = "quality_specific"
    normalized, directives = normalize_directives(package)
    assert directives[0]["layer"] == "correctness"
    assert normalized["directive_layer_normalizations"] == [{
        "finding_id": "f1",
        "from": "quality_specific",
        "to": "correctness",
        "reason": "confirmed_finding_quality_layer",
    }]


def test_directive_rejects_non_executable_mode_and_unknown_deterministic_method():
    from app.v2.repair_directives import normalize_directives

    package = _package()
    package["directives"][0]["change_mode"] = "rewrite_item"
    with pytest.raises(DomainError) as exc:
        normalize_directives(package)
    assert exc.value.code == "REPAIR_DIRECTIVE_CHANGE_MODE_INVALID"
    package = _package()
    package["directives"][0]["acceptance_assertion"]["deterministic_test"]["method"] = "model_assertion"
    with pytest.raises(DomainError) as exc:
        normalize_directives(package)
    assert exc.value.code == "REPAIR_DIRECTIVE_DETERMINISTIC_METHOD_INVALID"


def test_correctness_binding_rejects_unchanged_target_and_forbidden_stem_edit():
    from app.v2.repair_directives import normalize_directives, validate_candidate_bindings

    package = _package()
    package["objective"] = "correctness_only.v1"
    package["directives"][0]["acceptance_assertion"] = {
        "deterministic_test": None,
        "criterion_check": {"kind": "acceptance_item", "item_id": "C3.correctness"},
    }
    package["directives"][0]["forbidden_changes"] = [
        "item_count", "unit_title", "unrelated_items", "stem_markdown",
    ]
    package, directives = normalize_directives(package)
    directive_id = directives[0]["directive_id"]
    base_item = {
        "stem_markdown": "2+2=?",
        "answer": {"final": "5", "reasoning": "2+2=5"},
    }
    base_units = [{"unit_id": "u1", "title": "unit", "items": [base_item]}]
    candidate = {
        "directive_ids": [directive_id],
        "units": [{"unit_id": "u1", "directive_ids": [directive_id]}],
    }
    unchanged = [{"unit_id": "u1", "title": "unit", "items": [deepcopy(base_item)]}]
    with pytest.raises(DomainError) as exc:
        validate_candidate_bindings(
            candidate, package, {"u1"}, base_units=base_units, new_units=unchanged)
    assert exc.value.code == "REPAIR_DIRECTIVE_TARGET_UNCHANGED"
    changed_stem = [{"unit_id": "u1", "title": "unit", "items": [{
        "stem_markdown": "What is two plus two?",
        "answer": {"final": "4", "reasoning": "2+2=4"},
    }]}]
    with pytest.raises(DomainError) as exc:
        validate_candidate_bindings(
            candidate, package, {"u1"}, base_units=base_units, new_units=changed_stem)
    assert exc.value.code == "REPAIR_DIRECTIVE_FORBIDDEN_CHANGE"


def test_correctness_binding_allows_all_directive_targets_in_one_unit():
    from copy import deepcopy
    from app.v2.repair_directives import normalize_directives, validate_candidate_bindings

    package = _package()
    package["objective"] = "correctness_only.v1"
    package["confirmed_findings"].append({
        "finding_id": "f2",
        "claim": "The second item is incorrect.",
        "evidence": {},
        "quality_layer": "correctness",
    })
    package["priority_order"].append("f2")
    package["acceptance_criteria"].append({
        "item_id": "correctness:f2",
        "pass_condition": "The second item is corrected.",
        "verification_method": "correctness-only.v1",
    })
    second = deepcopy(package["directives"][0])
    second["finding_id"] = "f2"
    second["target"]["item_ordinal"] = 2
    second["acceptance_assertion"]["criterion_check"]["item_id"] = "correctness:f2"
    package["directives"][0]["acceptance_assertion"] = {
        "deterministic_test": None,
        "criterion_check": {"kind": "acceptance_item", "item_id": "C3.correctness"},
    }
    package["directives"][0]["forbidden_changes"] = [
        "item_count", "unit_title", "unrelated_items", "stem_markdown",
    ]
    package["directives"].append(second)
    package, directives = normalize_directives(package)
    ids = [row["directive_id"] for row in directives]
    base_items = [
        {"stem_markdown": "2+2=?", "answer": {"final": "5", "reasoning": "2+2=5"}},
        {"stem_markdown": "3+3=?", "answer": {"final": "7", "reasoning": "3+3=7"}},
    ]
    new_items = [
        {"stem_markdown": "2+2=?", "answer": {"final": "4", "reasoning": "2+2=4"}},
        {"stem_markdown": "3+3=?", "answer": {"final": "6", "reasoning": "3+3=6"}},
    ]
    validate_candidate_bindings(
        {"directive_ids": ids, "units": [{"unit_id": "u1", "directive_ids": ids}]},
        package,
        {"u1"},
        base_units=[{"unit_id": "u1", "title": "unit", "items": base_items}],
        new_units=[{"unit_id": "u1", "title": "unit", "items": new_items}],
    )


def test_directive_coverage_must_match_confirmed_findings():
    from app.v2.repair_directives import normalize_directives

    package = _package()
    package["directives"][0]["finding_id"] = "missing"
    with pytest.raises(DomainError) as exc:
        normalize_directives(package)
    assert exc.value.code == "REPAIR_DIRECTIVE_FINDING_UNBOUND"


def test_new_blueprint_package_requires_directives_while_legacy_package_stays_readable():
    from app.v2.optimization import OPTIMIZATION_SKILL, validate_package

    package = _package()
    package["skill"] = {
        key: OPTIMIZATION_SKILL[key] for key in ("id", "version", "hash")
    }
    version = SimpleNamespace(id="v1", content_hash="c" * 64)
    legacy = deepcopy(package)
    legacy.pop("directives")
    assert validate_package(legacy, version) is legacy
    with pytest.raises(DomainError) as exc:
        validate_package(legacy, version, require_directives=True)
    assert exc.value.code == "REPAIR_DIRECTIVE_REQUIRED"
    normalized = validate_package(package, version, require_directives=True)
    assert normalized["directives"][0]["directive_id"].startswith("rd:")


def test_planner_notes_are_archived_outside_the_executable_directive_package():
    from app.v2.optimization import OPTIMIZATION_SKILL, validate_package

    package = _package()
    package["skill"] = {key: OPTIMIZATION_SKILL[key] for key in ("id", "version", "hash")}
    package["optimizations"] = [{"target": "u1", "action": "optional review", "basis": "unverified"}]
    package["unverified_hypotheses"] = [{
        "claim": "An unverified hypothesis.",
        "verification_task": "Verify it independently.",
        "evidence_required": {"method": "independent_review"},
    }]
    version = SimpleNamespace(id="v1", content_hash="c" * 64)
    normalized = validate_package(package, version, require_directives=True)
    assert normalized["optimizations"] == []
    assert normalized["unverified_hypotheses"] == []
    notes = normalized["non_executable_planner_notes"]
    assert len(notes["optimizations"]) == 1
    assert len(notes["unverified_hypotheses"]) == 1
    assert notes["optimizations_hash"] == digest(notes["optimizations"])
    assert notes["unverified_hypotheses_hash"] == digest(notes["unverified_hypotheses"])


def test_only_a_missing_frozen_skill_identity_is_injected_and_audited():
    from app.v2.optimization import OPTIMIZATION_SKILL, validate_package

    package = _package()
    package.pop("skill")
    version = SimpleNamespace(id="v1", content_hash="c" * 64)
    normalized = validate_package(package, version, require_directives=True)
    assert normalized["skill"] == {
        key: OPTIMIZATION_SKILL[key] for key in ("id", "version", "hash")
    }
    assert normalized["optimization_metadata_normalization"] == {
        "method_version": "optimization-skill-injection.v1",
        "injected": ["skill"],
        "skill": normalized["skill"],
    }

    wrong = _package()
    wrong["skill"] = {"id": "wrong", "version": "0", "hash": "wrong"}
    with pytest.raises(DomainError) as exc:
        validate_package(wrong, version, require_directives=True)
    assert exc.value.code == "OPTIMIZATION_SKILL_MISMATCH"


def test_only_missing_scope_unit_ids_are_injected_from_the_frozen_plan():
    from app.v2.optimization import OPTIMIZATION_SKILL, validate_package

    package = _package()
    package["skill"] = {key: OPTIMIZATION_SKILL[key] for key in ("id", "version", "hash")}
    package["scope"].pop("unit_ids")
    version = SimpleNamespace(id="v1", content_hash="c" * 64)
    normalized = validate_package(package, version, require_directives=True, unit_ids=["u1", "u2"])
    assert normalized["scope"]["unit_ids"] == ["u1", "u2"]
    assert normalized["optimization_metadata_normalization"] == {
        "method_version": "optimization-metadata-injection.v1",
        "injected": ["scope.unit_ids"],
        "scope_unit_ids": ["u1", "u2"],
    }

    wrong = _package()
    wrong["skill"] = {key: OPTIMIZATION_SKILL[key] for key in ("id", "version", "hash")}
    wrong["scope"]["unit_ids"] = ["foreign"]
    with pytest.raises(DomainError) as exc:
        validate_package(wrong, version, require_directives=True, unit_ids=["u1"])
    assert exc.value.code == "REPAIR_DIRECTIVE_TARGET_INVALID"


def test_candidate_requires_top_level_and_per_unit_directive_mapping():
    from app.v2.repair_directives import normalize_directives, validate_candidate_bindings

    package, directives = normalize_directives(_package())
    directive_id = directives[0]["directive_id"]
    candidate = {
        "directive_ids": [directive_id],
        "units": [{"unit_id": "u1", "directive_ids": [directive_id]}],
    }
    validate_candidate_bindings(candidate, package, {"u1"})
    missing = {"units": [{"unit_id": "u1", "directive_ids": [directive_id]}]}
    with pytest.raises(DomainError) as exc:
        validate_candidate_bindings(missing, package, {"u1"})
    assert exc.value.code == "REPAIR_DIRECTIVE_BINDING_REQUIRED"
    wrong = deepcopy(candidate)
    wrong["units"][0]["directive_ids"] = ["unknown"]
    with pytest.raises(DomainError) as exc:
        validate_candidate_bindings(wrong, package, {"u1"})
    assert exc.value.code == "REPAIR_DIRECTIVE_UNKNOWN"


def test_deterministic_assertion_runs_before_semantic_recheck():
    from app.v2.recheck_scope import evaluate_candidate
    from app.v2.repair_directives import normalize_directives

    package, _ = normalize_directives(_package())
    state = {"research_strategy": {"hash": "s"}, "check_plan": {"check_items": []}}
    with pytest.raises(DomainError) as exc:
        evaluate_candidate(
            "run", "worker", 1, state, _units("wrong"), _units("wrong"),
            {"checks": []}, package, "v2",
        )
    assert exc.value.code == "REPAIR_DETERMINISTIC_ASSERTION_FAILED"


def test_directive_requires_both_mechanical_and_criterion_assertions():
    from app.v2.repair_directives import directive_recheck_results, normalize_directives

    package, _ = normalize_directives(_package())
    checked = {
        "optimization_acceptance": {
            "criteria": [{"item_id": "C3.correctness", "result": "undecidable"}],
        },
    }
    rows = directive_recheck_results(package, checked, _units("corrected"), set())
    assert rows[0]["result"] == "undecidable"
    checked["optimization_acceptance"]["criteria"][0]["result"] = "satisfied"
    rows = directive_recheck_results(package, checked, _units("corrected"), set())
    assert rows[0]["result"] == "satisfied"


def test_scope_merge_reuses_unaffected_rows_and_reruns_changed_scope():
    from app.v2.recheck_scope import merge_reports

    check_items = [
        {"item_id": "C1.coverage", "decision_type": "deterministic", "required": True},
        {"item_id": "C3.correctness", "decision_type": "semantic_rubric", "required": True},
    ]
    base = {
        "checks": [
            {"item_id": "C1.coverage", "result": "satisfied"},
            {"item_id": "C3.correctness", "result": "satisfied", "unit_results": [
                {"unit_id": "u1", "result": "satisfied"},
                {"unit_id": "u2", "result": "satisfied"},
            ]},
        ],
        "findings": [{"normalized_claim_hash": "old-u2",
                      "location": {"unit_id": "u2"}, "status": "confirmed"}],
    }
    deterministic = {
        "check_items": check_items,
        "check_plan_hash": "p",
        "checks": [{"item_id": "C1.coverage", "result": "satisfied"}],
        "findings": [],
    }
    incremental = {
        "checks": [{"item_id": "C3.correctness", "result": "violated", "unit_results": [
            {"unit_id": "u1", "result": "violated"},
        ]}],
        "findings": [{"normalized_claim_hash": "new-u1",
                      "location": {"unit_id": "u1"}, "status": "confirmed"}],
        "calibration_status": "not_calibrated",
    }
    merged = merge_reports(base, deterministic, incremental, {"u1"})
    row = next(row for row in merged["checks"] if row["item_id"] == "C3.correctness")
    assert row["result"] == "violated"
    assert {entry["unit_id"] for entry in row["unit_results"]} == {"u1", "u2"}
    assert merged["recheck_scope"]["full_semantic_rerun"] is False
    assert merged["recheck_scope"]["reused_check_rows"] == ["C3.correctness"]
    assert any(row.get("normalized_claim_hash") == "old-u2" for row in merged["findings"])
    assert any(row.get("normalized_claim_hash") == "new-u1" for row in merged["findings"])


def test_repair_ledger_tracks_directive_lifecycle():
    from app.v2.repair_directives import (
        build_ledger,
        directive_recheck_results,
        normalize_directives,
        update_ledger,
    )

    package, directives = normalize_directives(_package())
    ledger = build_ledger(package, [_finding()], "v1", "c" * 64)
    assert ledger["events"][0]["status"] == "pending"
    checked = {
        "automated_assessment": {"criteria": [{
            "check_id": "C3.correctness",
            "result": "satisfied",
            "criterion": {"criterion_hash": "k"},
        }]},
        "optimization_acceptance": {"criteria": [{
            "item_id": "C3.correctness",
            "result": "satisfied",
        }]},
    }
    results = directive_recheck_results(package, checked, _units(), {"f1"})
    resumed = update_ledger(
        ledger, candidate_hash="ch", candidate_version="v2",
        recheck_scope={"changed_unit_ids": ["u1"]}, results=results,
        status={row["finding_id"]: "resolved" for row in results},
        call_ids=["c1", "b1"],
    )
    assert resumed["events"][0]["status"] == "resolved"
    assert resumed["events"][0]["directive_id"] == directives[0]["directive_id"]
    assert resumed["ledger_hash"] == digest({k: v for k, v in resumed.items() if k != "ledger_hash"})

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

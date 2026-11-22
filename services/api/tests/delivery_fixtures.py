"""Synthetic, fully bound certificate fixtures for offline delivery tests."""
from functools import lru_cache

from app.v2.calibration_certificate import issue_certificate
from app.v2.delivery import delivery_scope, _claim_class
from app.v2.external_calibration import summarize


@lru_cache(maxsize=1)
def valid_calibration_summary():
    rows = []
    for index in range(44):
        expected = "satisfied" if index < 35 else "violated"
        rows.append({
            "case_hash": f"fixture-case-{index}",
            "source_group": f"fixture-group-{index % 16}",
            "expected": expected,
            "prediction": expected,
            "execution_category": "valid_decision",
            "evidence_valid": True,
        })
    return summarize(rows, 44)


def delivery_certificate(state, units):
    plan = state["check_plan"]
    protocol_hash = (state.get("quality_protocol") or {}).get("manifest_hash") or "a" * 64
    criterion_hashes = sorted({
        (item.get("criterion_contract") or {}).get("criterion_hash")
        for item in plan.get("check_items", [])
        if (item.get("criterion_contract") or {}).get("criterion_hash")
    })
    bindings = {
        "implementation_files": [
            {"path": "fixture:checker", "sha256": "b" * 64},
            {"path": "fixture:reducer", "sha256": "c" * 64},
        ],
        "review_protocol_hash": protocol_hash,
        "checker_protocol_hash": protocol_hash,
        "check_plan_hash": plan["check_plan_hash"],
        "criterion_contract_version": plan["assessment_contract"],
        "criterion_hashes": criterion_hashes,
        "prompt_hashes": ["d" * 64],
        "model_families": ["fixture-generation", "fixture-review"],
        "tool_versions": ["offline-fixture@1"],
    }
    anchor = {
        "commit": "e" * 40,
        "archive_sha256": "f" * 64,
        "protocol_hash": "1" * 64,
        "license": "CC0-1.0",
        "storage_identity": "workspace://delivery-test-fixture",
    }
    scope = delivery_scope(state.get("context", {}), units)
    scope["claim_classes"] = sorted({
        _claim_class(item) for item in plan.get("check_items", [])
        if item.get("required")
    })
    certificate = issue_certificate(
        scope, valid_calibration_summary(), bindings, anchor,
        issued_at="2026-11-22T00:00:00+00:00",
    )
    assert certificate["status"] == "calibrated", certificate["eligibility"]
    return certificate, bindings, anchor


def install_delivery_certificate(state, units):
    certificate, bindings, anchor = delivery_certificate(state, units)
    state["calibration_certificate"] = certificate
    state["calibration_current_bindings"] = bindings
    state["calibration_current_anchor"] = anchor
    return state

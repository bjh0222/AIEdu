"""Deterministic derivation of selected plan-level checks from criterion sources.

The source observations remain model-produced engineering evidence. This
module only reduces already verified source rows; it never creates a
professional certification or treats a missing source as satisfied.
"""
from __future__ import annotations

from copy import deepcopy

from .assessment_binding import consume

VERSION = "assessment-source-derivation.v1"


def is_derived_target(state: dict, item_or_id: dict | str) -> bool:
    item_id = item_or_id.get("item_id") if isinstance(item_or_id, dict) else item_or_id
    contract = state.get("check_plan", {}).get("derivation_contract") or {}
    return (contract.get("enabled") is True
            and item_id in set(contract.get("target_ids") or []))


def _source_rows(report: dict, unit_id: str, check_id: str):
    return [
        row for row in report.get("criteria", [])
        if isinstance(row, dict)
        and row.get("unit_id") == unit_id
        and row.get("check_id") == check_id
    ]


def derive_assessment_checks(state, units, report, version_id, *, matrix_hash=None):
    """Return target checks derived from validated criterion source rows."""
    contract = state.get("check_plan", {}).get("derivation_contract") or {}
    if contract.get("enabled") is not True:
        return {}
    targets = contract.get("targets") or []
    target_ids = [target.get("target_id") for target in targets if isinstance(target, dict)]
    if not target_ids:
        return {}

    item_units = [unit for unit in units if unit.get("items")]
    if not item_units:
        return {
            target_id: {
                "item_id": target_id,
                "result": "not_applicable",
                "reason": "item_content_not_present",
                "method_version": VERSION,
                "derivation_contract": deepcopy(contract),
                "quality_matrix_hash": matrix_hash,
            }
            for target_id in target_ids
        }

    _, binding_errors = consume(
        report, "automated_assessment", state, units, version_id
    )
    from .criterion_review import requests_for_unit

    output = {}
    for target in targets:
        target_id = target["target_id"]
        expected = []
        known_violation = False
        uncertainty = bool(binding_errors)
        source_evidence = []
        for unit in item_units:
            requests, _ = requests_for_unit(state, unit)
            for source in target.get("sources") or []:
                requested = [
                    request for request in requests
                    if request.get("check_id") == source.get("check_id")
                ]
                for request in requested:
                    expected.append(request)
                    rows = _source_rows(report, unit["unit_id"], request["check_id"])
                    if request.get("item_id") is not None:
                        rows = [row for row in rows if row.get("item_id") == request["item_id"]]
                    if len(rows) != 1:
                        uncertainty = True
                        continue
                    row = rows[0]
                    clauses = {
                        clause.get("clause_id"): clause
                        for clause in row.get("clauses", [])
                        if isinstance(clause, dict)
                    }
                    clause_results = {}
                    for clause_id in source.get("clauses") or []:
                        clause = clauses.get(clause_id)
                        if clause is None or clause.get("invalid_or_missing_observation"):
                            uncertainty = True
                            clause_results[clause_id] = None
                            continue
                        result = clause.get("result")
                        clause_results[clause_id] = result
                        if result == "violated":
                            known_violation = True
                        elif result != "satisfied":
                            uncertainty = True
                    if row.get("result") == "violated":
                        known_violation = True
                    elif row.get("result") != "satisfied":
                        uncertainty = True
                    source_evidence.append({
                        "source_check_id": request["check_id"],
                        "unit_id": unit["unit_id"],
                        "item_id": request.get("item_id"),
                        "criterion_hash": row.get("criterion_hash"),
                        "row_result": row.get("result"),
                        "clause_results": clause_results,
                        "audit_call_id": (row.get("audit") or {}).get("call_id"),
                    })
        if not expected:
            result, reason = "not_applicable", "no_applicable_source_rows"
            completeness = "not_applicable"
        elif known_violation:
            result, reason = "violated", "source_observation_violated"
            completeness = "complete" if len(source_evidence) == len(expected) else "incomplete_with_known_violation"
        elif uncertainty or len(source_evidence) != len(expected):
            result, reason = "undecidable", "source_or_binding_incomplete"
            completeness = "incomplete"
        else:
            result, reason = "satisfied", "all_source_clauses_satisfied"
            completeness = "complete"
        output[target_id] = {
            "item_id": target_id,
            "result": result,
            "reason": reason,
            "method_version": VERSION,
            "derivation_contract": deepcopy(contract),
            "source_check_ids": sorted({
                source["source_check_id"] for source in source_evidence
            }),
            "source_evidence": source_evidence,
            "criterion_hashes": sorted({
                source["criterion_hash"] for source in source_evidence
                if source.get("criterion_hash")
            }),
            "coverage": {
                "expected": len(expected),
                "observed": len(source_evidence),
                "complete": len(source_evidence) == len(expected) and not uncertainty,
            },
            "processing_status": completeness,
            "quality_matrix_hash": matrix_hash,
            "calibration_status": "not_calibrated",
        }
    return output

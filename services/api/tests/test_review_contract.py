"""Deterministic evidence, applicability, freeze and progress contracts."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError
from app.v2.review_contract import (
    build_evidence_catalog,
    delivery_outcome,
    ensure_quality_protocol,
    normalize_contract,
    prepare_review_output,
    quality_preflight,
    review_protocol_manifest,
    summarize_calls,
)


def test_evidence_catalog_is_short_unique_and_server_owned():
    body = "第一段给出定义。\n\n第二段给出推导。\n\n重复段\n\n重复段"
    catalog = build_evidence_catalog({"unit:body": body}, max_entries=8)
    assert catalog
    assert all(row["evidence_id"].startswith("E") for row in catalog)
    assert all(body.count(row["quote"]) == 1 for row in catalog)
    assert len({row["evidence_id"] for row in catalog}) == len(catalog)
    first = catalog[0]
    value, audit = prepare_review_output({
        "result": "PASS",
        "findings": [{"evidence_id": first["evidence_id"],
                      "field": "spoofed", "quote": "model-written long quote"}],
    }, catalog)
    assert value["result"] == "satisfied"
    assert value["findings"][0]["field"] == first["field"]
    assert value["findings"][0]["quote"] == first["quote"]
    assert audit["change_count"] >= 2


def test_contract_normalizer_handles_only_unambiguous_wire_shapes():
    value, changes = normalize_contract({
        "applicability": "false",
        "result": "NOT_APPLICABLE",
        "numerator": "0",
        "denominator": "",
        "rating": "2",
        "enum_value": "null",
        "evidence": "E0001",
    })
    assert value["applicable"] is False
    assert value["result"] == "not_applicable"
    assert value["numerator"] == "0"
    assert value["denominator"] is None
    assert value["rating"] == "2"
    assert value["enum_value"] is None
    assert value["evidence"] == [{"evidence_id": "E0001"}]
    assert {row["code"] for row in changes} >= {
        "applicability_key_alias_to_applicable",
        "string_boolean_normalized",
        "result_enum_normalized",
        "numeric_string_or_null_normalized",
        "null_marker_to_null",
        "evidence_scalar_id_to_list",
    }
    malformed, _ = normalize_contract({"evidence": 42})
    assert malformed["evidence"] == [{"invalid_evidence_shape": "int"}]


def test_contract_normalizer_unwraps_unambiguous_row_containers():
    """Real failure f3444394: {"assessments": {"items": [...]}} hid valid rows."""
    rows = [{"request_id": "r", "clauses": []}, {"request_id": "s", "clauses": []}]
    value, changes = normalize_contract({"assessments": {"items": rows}})
    assert value["assessments"] == rows
    assert any(row["code"] == "row_container_single_wrapper_unwrapped" for row in changes)
    # Ambiguous or non-row shapes are never unwrapped.
    kept, kept_changes = normalize_contract({"assessments": {"items": rows, "extra": 1}})
    assert isinstance(kept["assessments"], dict)
    assert not kept_changes
    kept2, _ = normalize_contract({"assessments": {"items": "not-a-list"}})
    assert kept2["assessments"] == {"items": "not-a-list"}
    # A single dict row is not a row list and stays wrapped.
    kept3, _ = normalize_contract({"dimensions": {"items": {"dimension_id": "d"}}})
    assert kept3["dimensions"] == {"items": {"dimension_id": "d"}}
    # Empty wrapper value stays untouched; a missing list is an explicit failure.
    kept4, _ = normalize_contract({"checks": {"items": []}})
    assert kept4["checks"] == {"items": []}


def test_contract_normalizer_never_silently_truncates_a_long_list():
    original = {"assessments": [
        {"request_id": f"r{index}", "clauses": []}
        for index in range(257)
    ]}
    value, changes = normalize_contract(original)
    assert len(value["assessments"]) == 257
    assert value["assessments"][-1] == {
        "__aiedu_contract_error__": "list_over_limit"}
    assert any(row["code"] == "list_over_limit" for row in changes)


def _context():
    return {
        "domain_key": "math_logic",
        "subject_bindings": [{"domain_key": "math_logic"}],
        "spec_bindings": [{"artifact_role": "exercise", "scene": "exercise"}],
    }


def test_zero_model_matrix_uses_unit_dimension_scene_and_question_form():
    items = [
        {"item_id": "Q1.exercise", "dimension": "Q1.exercise", "verifier_binding": "independent_math.v1",
         "scope": {"roles": ["exercise"], "scenes": ["exercise"], "domains": ["math_logic"],
                   "requires_items": True, "question_types": ["选择题"]}},
        {"item_id": "G1.alignment", "dimension": "G1.alignment", "verifier_binding": "criterion_review.v1",
         "scope": {}},
    ]
    units = [
        {"unit_id": "lecture", "artifact_role": "tutorial", "body": "x"},
        {"unit_id": "empty", "artifact_role": "exercise", "body": "x", "items": []},
        {"unit_id": "choice", "artifact_role": "exercise", "body": "x",
         "items": [{"item_id": "i", "question_type": "选择题"}]},
    ]
    matrix = quality_preflight(items, units, _context())
    by_key = {(row["check_id"], row["unit_id"]): row for row in matrix["rows"]}
    assert by_key[("Q1.exercise", "lecture")]["reason"] == "role_not_in_scope"
    assert by_key[("Q1.exercise", "empty")]["reason"] == "item_content_not_present"
    assert by_key[("Q1.exercise", "choice")]["decision"] == "execute"
    assert by_key[("Q1.exercise", "choice")]["question_forms"] == ["选择题"]
    assert by_key[("G1.alignment", "lecture")]["decision"] == "execute"
    assert matrix["summary"] == {
        "pairs": 6, "execute": 4, "skip": 2,
        "protected_runtime_or_research_consumers": 3,
    }


def test_quality_protocol_freezes_and_refuses_drift():
    state = {"quality_protocol": review_protocol_manifest()}
    assert ensure_quality_protocol(state)["manifest_hash"] == state["quality_protocol"]["manifest_hash"]
    state["quality_protocol"] = deepcopy(state["quality_protocol"])
    state["quality_protocol"]["components"]["review_skill"]["version"] += 1
    with pytest.raises(DomainError, match="QUALITY_PROTOCOL_DRIFT"):
        ensure_quality_protocol(state)


def test_delivery_states_and_accounting_keep_unknown_distinct_from_zero():
    base = {"tqi_verdict": "blocked", "quality_status": "needs_review",
            "calibration_status": "not_calibrated", "blocking_reasons": []}
    calls = [
        {"status": "settled", "stage": "generating", "attempt": 1,
         "usage": {"total_tokens": 12}, "price": {"amount": 0.1, "currency": "CNY"},
         "elapsed_seconds": 2},
        {"status": "settled", "stage": "checking", "attempt": 1,
         "usage": None, "price": None, "elapsed_seconds": 3},
        {"status": "settled", "stage": "checking", "attempt": 2,
         "usage": {"total_tokens": 8}, "price": None, "elapsed_seconds": 4},
    ]
    assert delivery_outcome(status="running", stages={"generating": "running"},
                            quality=base, version_id="v", calls=[],
                            ).get("state") == "draft_ready"
    assert delivery_outcome(status="running", stages={"checking": "running"},
                            quality=base, version_id="v", calls=[],
                            ).get("state") == "quality_checking"
    assert delivery_outcome(
        status="running", stages={"checking": "done"},
        quality=base, version_id="v",
        calls=[{"status": "sent", "stage": "rubric_reviewing"}],
    ).get("state") == "quality_checking"
    assert delivery_outcome(status="completed", stages={}, quality=base,
                            version_id="v", calls=[]).get("state") == "quality_blocked"
    assert delivery_outcome(
        status="completed", stages={},
        quality={"tqi_verdict": "pass", "quality_status": "passed",
                 "calibration_status": "calibrated", "blocking_reasons": []},
        version_id="v", calls=[],
    )["state"] == "final_ready"
    metrics = summarize_calls(calls)
    assert metrics["tokens_known"] == 20
    assert metrics["token_unknown_calls"] == 1
    assert metrics["price_known_calls"] == 1
    assert metrics["price_missing_calls"] == 2
    assert metrics["retry_source_counts"] == {"initial": 2, "stage_reattempt": 1}
    assert metrics["stage_distribution"]["checking"]["calls"] == 2

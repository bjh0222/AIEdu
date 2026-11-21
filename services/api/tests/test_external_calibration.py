"""Synthetic contracts for real external-label experiments; no paid calls."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError
from app.v2.external_calibration import blind_unit, comparison, process_case, summarize


def case(label=-1, final=True):
    return process_case({"id": "public-case", "problem": "original problem", "steps": ["first step", "last step"],
        "label": label, "generator": "SECRET_GENERATOR", "final_answer_correct": final},
        {"url": "https://example.org/data", "sha256": "s"*64, "label_protocol": "expert first error"})


def test_blinding_is_lossless_and_excludes_labels_generator_and_source_identity():
    value = case(0, True)
    unit = blind_unit(value)
    assert unit["items"][0]["stem_markdown"] == value["problem"]
    assert unit["items"][0]["answer"]["reasoning"] == "first step\n\nlast step"
    assert unit["items"][0]["answer"]["final"] == "last step"
    for excluded in ("public-case", "expert_first_error", "expected_process_result", "SECRET_GENERATOR", "example.org"):
        assert excluded not in str(unit)
    assert value["expected_process_result"] == "violated"  # correct final never erases wrong process.


@pytest.mark.parametrize("label", [None, True, -2, 2, "0"])
def test_invalid_missing_and_out_of_range_labels_cannot_become_gold(label):
    with pytest.raises(DomainError, match="EXTERNAL_PROCESS_CASE_INVALID"):
        case(label)


def observation(value, result="violated"):
    from app.v2.criterion_review import bind_rows
    unit = blind_unit(value)
    item_id = unit["items"][0]["item_id"]
    field = "item:" + item_id + ":answer.reasoning"
    request = {"request_id": "request", "check_id": "Q2.exercise", "item_id": item_id,
        "criterion": {"criterion_hash": "criterion", "clauses": {"reasoning": "check reasoning", "answer": "check answer"}}}
    output = {"assessments": [{"request_id": "request", "clauses": [
        {"clause_id": "reasoning", "result": result, "reason": "A specific content-based reason with exact evidence",
         "evidence": [{"field": field, "quote": "first step"}]},
        {"clause_id": "answer", "result": "satisfied", "reason": "A separate actual answer observation",
         "evidence": [{"field": field, "quote": "last step"}]}]}]}
    return {"criteria": bind_rows(output, [request], {field: "first step\n\nlast step"}, {"call_id": "c"}, unit["unit_id"])}


def test_compare_only_mapped_reasoning_clause_and_separate_missing_execution():
    value = case(0)
    report = observation(value)
    assert comparison(value, report)["agreement"] is True
    report["criteria"][0]["clauses"][0]["evidence"] = []
    unknown = comparison(value, report)
    assert unknown["prediction"] is None and unknown["agreement"] is None
    assert unknown["execution_category"] == "invalid_or_missing_execution"
    summary = summarize([unknown], 2)
    assert summary["status"] == "incomplete" and summary["abstentions"] == 0
    assert summary["invalid_observations"] == summary["unrecorded_cases"] == 1
    assert summary["error_recall"] is None and summary["calibration_status"] == "not_calibrated"


def test_evidence_bound_model_abstention_is_completed_execution_not_a_wrong_prediction():
    value = case(0)
    record = comparison(value, observation(value, "undecidable"))
    assert record["execution_category"] == "valid_abstention" and record["agreement"] is None
    summary = summarize([record], 1)
    assert summary["status"] == "completed" and summary["abstentions"] == 1
    assert summary["invalid_observations"] == 0 and summary["execution_coverage"] == 1
    assert summary["decision_coverage"] == 0 and summary["agreement_decided"] is None


@pytest.mark.parametrize("fault", ["wrong_root", "duplicate_criterion", "missing_reason", "invalid_flag", "bad_quote", "bad_offset", "bad_hash", "malformed_field"])
def test_bad_model_schema_or_binding_never_becomes_a_valid_abstention(fault):
    value = case(0)
    report = observation(value, "undecidable")
    clause = report["criteria"][0]["clauses"][0]
    if fault == "wrong_root": report = {"assessings": report["criteria"]}
    if fault == "duplicate_criterion": report["criteria"] *= 2
    if fault == "missing_reason": clause["reason"] = None
    if fault == "invalid_flag": clause["invalid_or_missing_observation"] = True
    if fault == "bad_quote": clause["evidence"][0]["quote"] = "nonexistent"
    if fault == "bad_offset": clause["evidence"][0]["start_byte"] += 1
    if fault == "bad_hash": clause["evidence"][0]["field_hash"] = "wrong"
    if fault == "malformed_field": clause["evidence"][0]["field"] = []
    record = comparison(value, report)
    assert record["execution_category"] == "invalid_or_missing_execution"
    summary = summarize([record], 1)
    assert summary["abstentions"] == 0 and summary["invalid_observations"] == 1
    assert summary["status"] == "incomplete"


def test_other_clause_failure_does_not_erase_a_valid_mapped_reasoning_observation():
    value = case(0)
    report = observation(value)
    report["criteria"][0]["processing_status"] = "invalid_or_missing_observation"
    report["criteria"][0]["clauses"][1]["invalid_or_missing_observation"] = True
    assert comparison(value, report)["execution_category"] == "valid_decision"


def test_perfect_tiny_sample_never_grants_professional_certificate():
    records = [comparison(value, observation(value, label)) for value, label in
               ((case(-1), "satisfied"), (case(0), "violated"))]
    result = summarize(records, 2)
    assert result["agreement_all"] == 1 and result["status"] == "completed"
    assert result["agreement_ci95"][0] < 0.5
    assert not result["professional_certification"] and result["threshold"] is None


def test_missing_observation_cannot_reduce_conditional_false_positive_rate():
    clean = case(-1)
    defect = case(0)
    false_alarm = comparison(clean, observation(clean, "violated"))
    missing = comparison(defect, {})
    result = summarize([false_alarm, missing], 3)
    assert result["false_positive_rate"] == 1 and result["error_recall"] is None
    assert result["decision_coverage"] == pytest.approx(1 / 3)
    assert result["agreement_decided"] == 0


@pytest.mark.parametrize("count", [0, -1, True, 0.5])
def test_invalid_population_denominator_is_rejected(count):
    with pytest.raises(DomainError, match="EXTERNAL_CALIBRATION_COUNT_INVALID"):
        summarize([], count)


def test_duplicate_case_cannot_inflate_coverage_or_accuracy():
    value = case()
    record = comparison(value, observation(value, "satisfied"))
    with pytest.raises(DomainError, match="EXTERNAL_CALIBRATION_CASE_DUPLICATED"):
        summarize([record, deepcopy(record)], 2)

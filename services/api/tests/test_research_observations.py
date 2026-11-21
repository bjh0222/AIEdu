"""Execution defects cannot count as grounded C abstentions or quality passes."""
from copy import deepcopy

import pytest

from app.v2.contracts import sha
from app.v2.research_observations import bind_batch


@pytest.fixture
def sample():
    body = "前置概念。\n方法适用于线性条件；边界反例需要另行核验。"
    evidence = [{"field": "body", "quote": "方法适用于线性条件"}]
    return ({"unit_id": "u", "body": body, "content_hash": sha(body)},
        [{"dimension_id": "conditions", "indicators": [{"indicator_id": "cases"}]}],
        {"dimensions": [{"dimension_id": "conditions", "result": "satisfied", "score": 2,
            "reason": "对适用条件有明确说明。", "evidence": deepcopy(evidence),
            "indicators": [{"indicator_id": "cases", "applicable": True, "numerator": 1,
                "denominator": 1, "unit": "cases", "evidence": deepcopy(evidence)}],
            "repair_condition": "保持适用条件明确。", "limitations": ["not calibrated"]}]})


def review(sample):
    unit, dimensions, output = sample
    return bind_batch(output, dimensions, unit)


@pytest.mark.parametrize("result,score", [("satisfied", 2), ("violated", 0), ("violated", 1)])
def test_positive_and_negative_are_executed_not_certified(sample, result, score):
    sample[2]["dimensions"][0].update(result=result, score=score)
    before = deepcopy(sample)
    report = review(sample)
    row = report["dimensions"][0]
    assert row["processing_status"] == "complete"
    assert (row["result"], row["score"]) == (result, score)
    assert report["status"] == "completed" and report["coverage"]["decided"] == 1
    evidence = row["evidence"][0]
    assert sample[0]["body"].encode()[evidence["start_byte"]:evidence["end_byte"]].decode() == evidence["quote"]
    assert evidence["quote_hash"] == sha(evidence["quote"])
    assert evidence["unit_hash"] == sample[0]["content_hash"]
    assert evidence["start_byte"] > sample[0]["body"].index(evidence["quote"])
    assert sample == before
    assert "calibration_status" not in report and "professional_certification" not in report


def test_grounded_abstention_does_not_become_zero_or_execution_missing(sample):
    raw = sample[2]["dimensions"][0]
    raw.update(result="undecidable", score=None, reason="反例仅被提及，无法据此完成适用边界判断。")
    raw["indicators"][0].update(numerator=None, denominator=None, unknown_reason="可见内容未给足需验证的边界。")
    report = review(sample)
    assert report["status"] == "completed"
    assert report["dimensions"][0]["score"] is None
    assert report["coverage"]["grounded_abstentions"] == 1
    assert report["coverage"]["decided"] == report["coverage"]["invalid_or_missing"] == 0


@pytest.mark.parametrize("mutation", [
    lambda raw: raw.update(evidence=[]),
    lambda raw: raw.update(score=0),
    lambda raw: raw.update(reason=" "),
    lambda raw: raw.pop("score"),
])
def test_unexecuted_or_malformed_unknown_is_not_grounded_abstention(sample, mutation):
    raw = sample[2]["dimensions"][0]
    raw.update(result="undecidable", score=None)
    mutation(raw)
    report = review(sample)
    assert report["status"] == "incomplete"
    assert report["coverage"]["grounded_abstentions"] == 0
    assert report["coverage"]["invalid_or_missing"] == 1


def test_explicit_nonapplicability_checks_indicator_consistency(sample):
    raw = sample[2]["dimensions"][0]
    raw.update(result="not_applicable", score=None, reason="本任务无需此类条件比较。", evidence=[])
    raw["indicators"][0].update(applicable=False, numerator=None, denominator=None,
        evidence=[], unknown_reason="当前任务不要求此类比较。")
    report = review(sample)
    assert report["status"] == "completed" and report["coverage"]["not_applicable"] == 1
    raw["indicators"][0].update(applicable=True, enum_value="present", evidence=[{"field": "body", "quote": "前置概念"}])
    assert "DIMENSION_INDICATOR_APPLICABILITY_CONFLICT" in review(sample)["dimensions"][0]["validation_errors"]


def test_invalid_dimension_evidence_keeps_valid_indicator_without_inferred_quote(sample):
    sample[2]["dimensions"][0]["evidence"][0]["quote"] = "正文中没有这段内容"
    report = review(sample)
    row = report["dimensions"][0]
    assert row["score"] is None and row["result"] == "undecidable"
    assert row["processing_status"] == "invalid_or_missing_observation"
    assert row["evidence"] == []
    assert row["indicators"][0]["processing_status"] == "complete"
    assert row["indicators"][0]["numerator"] == 1
    assert report["coverage"]["grounded_abstentions"] == 0


@pytest.mark.parametrize("evidence", [None, "quote", [None], [{"field": "answer", "quote": "前置概念"}],
    [{"field": "body", "quote": ""}], [{"field": "body", "quote": "不存在"}],
    [{"field": "body", "quote": "前置概念"}, {"field": "body", "quote": "不存在"}]])
def test_malformed_or_partly_invalid_evidence_cannot_be_dropped_to_accept(sample, evidence):
    sample[2]["dimensions"][0]["evidence"] = evidence
    report = review(sample)
    assert report["status"] == "incomplete" and report["dimensions"][0]["score"] is None


def test_repeated_quote_in_source_cannot_be_unambiguously_bound(sample):
    sample[0]["body"] += "方法适用于线性条件"
    sample[0]["content_hash"] = sha(sample[0]["body"])
    assert review(sample)["status"] == "incomplete"


def test_hash_mismatch_is_execution_error_even_for_no_evidence_nonapplicability(sample):
    sample[0]["content_hash"] = "stale"
    raw = sample[2]["dimensions"][0]
    raw.update(result="not_applicable", score=None, evidence=[])
    raw["indicators"][0].update(applicable=False, numerator=None, denominator=None,
        evidence=[], unknown_reason="not applicable")
    report = review(sample)
    assert report["status"] == "incomplete"
    assert "UNIT_CONTENT_HASH_MISMATCH" in report["dimensions"][0]["validation_errors"]


@pytest.mark.parametrize("output", [None, [], {}, {"assessings": []}, {"dimensions": None},
    {"dimensions": []}, {"dimensions": [{"dimension_id": "wrong"}]}])
def test_bad_root_or_missing_expected_target_is_not_execution(sample, output):
    sample = (sample[0], sample[1], output)
    report = review(sample)
    assert report["status"] == "incomplete" and report["coverage"]["complete_observations"] == 0


def test_duplicate_target_rejected_while_valid_sibling_preserved(sample):
    sample[1].append({"dimension_id": "other", "indicators": []})
    row = sample[2]["dimensions"][0]
    sample[2]["dimensions"].append(deepcopy(row))
    sample[2]["dimensions"].append({"dimension_id": "other", "result": "not_applicable", "score": None,
        "reason": "This candidate has no such applicable target.", "indicators": []})
    report = review(sample)
    assert report["dimensions"][0]["processing_status"] != "complete"
    assert report["dimensions"][1]["processing_status"] == "complete"
    assert report["coverage"]["complete_observations"] == 1 and report["status"] == "incomplete"


def test_extra_dimension_must_not_be_silently_discarded(sample):
    sample[2]["dimensions"].append({"dimension_id": "not-requested"})
    report = review(sample)
    assert report["dimensions"][0]["processing_status"] == "complete"
    assert report["batch_contract_errors"] == ["UNEXPECTED_DIMENSION"]
    assert report["status"] == "incomplete"


@pytest.mark.parametrize("num,den", [(True, 1), (1, False), (-1, 1), (1, 0), (None, 1),
    (float("nan"), 1), (1, float("inf")), ("1", 1)])
def test_invalid_counts_not_zero_or_enum_fallback(sample, num, den):
    sample[2]["dimensions"][0]["indicators"][0].update(numerator=num, denominator=den, enum_value="fallback")
    report = review(sample)
    assert report["status"] == "incomplete"
    indicator = report["dimensions"][0]["indicators"][0]
    assert indicator["numerator"] is None and indicator["enum_value"] is None


def test_zero_count_is_valid_evidence_not_unknown(sample):
    sample[2]["dimensions"][0]["indicators"][0]["numerator"] = 0
    assert review(sample)["dimensions"][0]["indicators"][0]["numerator"] == 0
    assert review(sample)["status"] == "completed"


@pytest.mark.parametrize("indicators", [None, [], [None], [{"indicator_id": "wrong"}]])
def test_missing_or_wrong_indicator_is_execution_missing(sample, indicators):
    sample[2]["dimensions"][0]["indicators"] = indicators
    assert review(sample)["status"] == "incomplete"


def test_binding_does_not_invent_a_universal_unknown_indicator_score_rule(sample):
    raw = sample[2]["dimensions"][0]["indicators"][0]
    raw.update(numerator=None, denominator=None, unknown_reason="Evidence leaves this count unresolved.")
    report = review(sample)
    assert report["dimensions"][0]["indicators"][0]["processing_status"] == "complete"
    assert report["status"] == "completed" and report["dimensions"][0]["score"] == 2
    # This is a structurally complete C proposal, not validation that its score
    # satisfies the actual anchor. Its null indicator is not changed to zero.
    assert report["dimensions"][0]["indicators"][0]["numerator"] is None


def test_declared_zero_denominator_exit_is_grounded_null_not_execution_failure(sample):
    sample[1][0]["indicators"][0]["missing_denominator_rule"] = "If denominator=0, indicator=null; apply the frozen anchor's vacuous alternative."
    raw = sample[2]["dimensions"][0]["indicators"][0]
    raw.update(numerator=None, denominator=0, unknown_reason="No unsupported claim in the supplied candidate.")
    report = review(sample)
    indicator = report["dimensions"][0]["indicators"][0]
    assert report["status"] == "completed" and indicator["result"] == "undecidable"
    assert indicator["numerator"] is None and indicator["denominator"] == 0
    assert report["coverage"]["grounded_abstentions"] == 0
    sample[1][0]["indicators"][0].pop("missing_denominator_rule")
    assert review(sample)["status"] == "incomplete"


def test_declared_zero_occurrence_best_case_can_be_scored_zero(sample):
    sample[1][0]["indicators"][0]["missing_denominator_rule"] = (
        "If there are zero absolute statements, set B3=0 (perfect compliance; "
        "scored as 0, not null)."
    )
    raw = sample[2]["dimensions"][0]["indicators"][0]
    raw.update(applicable=True, numerator=0, denominator=0, enum_value=None,
               unknown_reason=None, evidence=[{"field": "body", "quote": "前置概念"}])
    report = review(sample)
    indicator = report["dimensions"][0]["indicators"][0]
    assert report["status"] == "completed"
    assert indicator["result"] == "observed"
    assert indicator["numerator"] == 0 and indicator["denominator"] == 0


@pytest.mark.parametrize("result,score", [("satisfied", True), ("satisfied", 1), ("violated", 2),
    ("satisfied", None), ("partial", 1)])
def test_bad_score_result_pairs_are_not_observations(sample, result, score):
    sample[2]["dimensions"][0].update(result=result, score=score)
    assert review(sample)["status"] == "incomplete"


@pytest.mark.parametrize("field,value", [("result", []), ("result", {}), ("dimension_id", []),
    ("dimension_id", {}), ("indicators", [{"indicator_id": []}]), ("indicators", [{"indicator_id": {}}])])
def test_untrusted_nested_types_become_diagnostics_not_exceptions(sample, field, value):
    sample[2]["dimensions"][0][field] = value
    assert review(sample)["status"] == "incomplete"


def test_large_exact_integer_counts_need_not_overflow_float_conversion(sample):
    sample[2]["dimensions"][0]["indicators"][0].update(numerator=10**1000, denominator=10**1000)
    assert review(sample)["status"] == "completed"

from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, sha
from app.v2.research_review_corrections import correct_batch, METHOD, SCHEMA_METHOD, PROMPT, SCHEMA_PROMPT


def test_correction_prompt_requires_exact_quote_occurrence_filtering():
    assert "count exact occurrences" in PROMPT
    assert "never repeat a previously rejected non-unique quote" in PROMPT
from app.v2.research_observations import bind_batch


@pytest.fixture
def sample():
    unit = {"unit_id": "u", "body": "Supported source. Fixed sibling.", "content_hash": sha("Supported source. Fixed sibling.")}
    dims = [{"dimension_id": i, "source_refs": ["claim:" + i], "indicators": []} for i in ("a", "b")]
    rows = [{"dimension_id": i, "result": "satisfied", "score": 2, "reason": "An actual supported observation.",
        "evidence": [{"field": "body", "quote": q}], "indicators": []}
        for i, q in (("a", "Supported source."), ("b", "Fixed sibling."))]
    data = {"unit": unit, "research_rubric": {"dimensions": dims, "limitations": ["not calibrated"]},
        "source_claims": [{"claim_id": "claim:" + i} for i in ("a", "b")],
        "binding_hash": "fixed binding", "full_rubric_hash": "whole hash"}
    return unit, dims, rows, data


def run(sample, output, callback, **kwargs):
    unit, dims, _, data = sample
    return correct_batch(output, {"call_id": "first"}, dims, unit, data, callback, **kwargs)


def test_valid_batch_needs_no_new_call(sample):
    def forbidden(**kwargs):
        pytest.fail("valid results must not be requested again")
    report = run(sample, {"dimensions": sample[2]}, forbidden)
    assert report["status"] == "completed" and report["corrections"] == []


def test_only_invalid_target_corrected_and_sibling_exactly_preserved(sample):
    before = deepcopy(sample)
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["evidence"][0]["quote"] = "nonexistent quote"
    def correction(**kwargs):
        data = kwargs["data"]
        assert data["target_dimension_ids"] == ["a"] and kwargs["attempt"] == 1
        assert data["research_rubric"]["dimensions"] == [sample[1][0]]
        assert data["source_claims"] == [{"claim_id": "claim:a"}]
        assert data["unit"] == sample[0] and data["binding_hash"] == "fixed binding"
        assert data["correction_method"] == METHOD and "response_contract" not in data
        assert kwargs["system"] == PROMPT
        return {"dimensions": [deepcopy(sample[2][0])]}, {"call_id": "correction"}
    report = run(sample, output, correction)
    assert report["status"] == "completed"
    assert report["dimensions"][1]["evidence"][0]["quote"] == "Fixed sibling."
    assert report["dimension_audits"] == {"a": {"call_id": "correction"}, "b": {"call_id": "first"}}
    assert len(report["audits"]) == 2 and report["corrections"][0]["target_dimension_ids"] == ["a"]
    assert sample == before and output["dimensions"][0]["evidence"][0]["quote"] == "nonexistent quote"


def test_exhaustion_is_explicit_contract_failure_not_grounded_unknown(sample):
    calls = []
    def broken(**kwargs):
        calls.append(kwargs)
        return {"dimensions": []}, {"call_id": f"bad-{len(calls)}"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        run(sample, {}, broken)
    assert len(calls) == 2


def test_transport_unknown_is_never_retried_by_contract_corrector(sample):
    calls = []
    def unknown(**kwargs):
        calls.append(kwargs)
        raise DomainError("UNKNOWN_CALL", 502)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        run(sample, {}, unknown)
    assert len(calls) == 1


def test_new_sibling_output_is_not_allowed_to_rewrite_accepted_rows(sample):
    output = {"dimensions": [deepcopy(sample[2][1])]}
    def overwrite(**kwargs):
        return {"dimensions": deepcopy(sample[2])}, {"call_id": "bad-extra"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_INVALID"):
        run(sample, output, overwrite)


@pytest.mark.parametrize("result", ["undecidable", "not_applicable", "violated"])
def test_cannot_escape_bad_scored_evidence_by_relabelling(sample, result):
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["evidence"] = []
    def evade(**kwargs):
        row = deepcopy(sample[2][0])
        row.update(result=result, score=None)
        return {"dimensions": [row]}, {"call_id": "evade"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CORRECTION_EVASION"):
        run(sample, output, evade)


def test_grounded_original_abstention_needs_no_correction(sample):
    sample[2][0].update(result="undecidable", score=None)
    report = run(sample, {"dimensions": sample[2]}, lambda **k: pytest.fail("no retry"))
    assert report["coverage"]["grounded_abstentions"] == 1


@pytest.mark.parametrize("count", [-1, 3, True, None])
def test_correction_budget_cannot_be_unbounded(sample, count):
    with pytest.raises(ValueError):
        run(sample, {}, lambda **k: None, max_corrections=count)


def test_missing_original_shape_gets_explicit_schema_on_every_attempt(sample):
    sample[1][0]["indicators"] = [{"indicator_id": "actual-indicator", "missing_denominator_rule": "null when none"}]
    original = deepcopy(sample)
    calls = []

    def broken(**kwargs):
        calls.append(deepcopy(kwargs))
        assert kwargs["system"] == SCHEMA_PROMPT
        assert "same dimension/indicator schema as previous_target_outputs" not in kwargs["system"]
        assert kwargs["data"]["correction_method"] == SCHEMA_METHOD
        schema = kwargs["data"]["response_contract"]
        dim = schema["properties"]["dimensions"]["items"]["anyOf"][0]
        assert dim["properties"]["dimension_id"] == {"const": "a"}
        indicator = dim["properties"]["indicators"]["items"]
        assert indicator["properties"]["applicable"] == {"type": "boolean"}
        assert set(indicator["required"]) == {"indicator_id", "applicable", "numerator", "denominator",
            "unit", "enum_value", "evidence", "unknown_reason"}
        if len(calls) == 1:
            assert kwargs["data"]["previous_target_outputs"] == []
        # Even a new structurally shaped response cannot switch a frozen v2
        # method to the legacy v1 on attempt 2.
        malformed = deepcopy(sample[2][0])
        malformed["indicators"] = [{"indicator_id": "actual-indicator", "applicable": True,
            "numerator": 1, "denominator": 1, "unit": "claims", "enum_value": None,
            "evidence": [{"field": "body", "quote": "invented evidence"}], "unknown_reason": None}]
        return {"dimensions": [malformed]}, {"call_id": f"new-{len(calls)}"}

    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        run(sample, {"dimensions": [sample[2][1]]}, broken)
    assert len(calls) == 2 and sample == original
    assert calls[0]["data"]["response_contract"] == calls[1]["data"]["response_contract"]


def test_malformed_indicator_keys_require_v2_not_field_coercion(sample):
    sample[1][0]["indicators"] = [{"indicator_id": "i"}]
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["indicators"] = [{"indicator_id": "i", "applicability": True,
        "observation": "a definition is not an executed count"}]
    calls = []
    def reject(**kw):
        calls.append(kw)
        assert kw["data"]["correction_method"] == SCHEMA_METHOD
        return {"dimensions": [deepcopy(output["dimensions"][0])]}, {"call_id": "bad"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        run(sample, output, reject)
    assert len(calls) == 2


@pytest.mark.parametrize("missing", ["result", "score", "both"])
def test_missing_verdict_is_execution_defect_and_gets_only_remaining_correction(sample, missing):
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["evidence"] = []
    calls = []
    def correction(**kw):
        calls.append(deepcopy(kw))
        row = deepcopy(sample[2][0])
        if len(calls) == 1:
            if missing in {"result", "both"}:
                row.pop("result")
            if missing in {"score", "both"}:
                row.pop("score")
        return {"dimensions": [row]}, {"call_id": f"fix-{len(calls)}"}
    report = run(sample, output, correction)
    assert len(calls) == 2 and report["status"] == "completed"
    assert report["corrections"][0]["coverage"]["invalid_or_missing"] == 1
    assert report["corrections"][0]["coverage"]["grounded_abstentions"] == 0
    assert report["dimensions"][0]["result"] == "satisfied"
    assert report["dimensions"][0]["score"] == 2
    assert report["dimensions"][1]["evidence"][0]["quote"] == "Fixed sibling."
    assert "original_scored_judgments" not in calls[0]["data"]
    if missing in {"result", "both"}:
        assert calls[1]["data"]["original_scored_judgments"] == [
            {"dimension_id": "a", "result": "satisfied", "score": 2}]


def test_missing_verdict_does_not_erase_original_lock_on_second_attempt(sample):
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["evidence"] = []
    calls = []
    def correction(**kw):
        calls.append(kw)
        row = deepcopy(sample[2][0])
        row.update(result=None, score=None) if len(calls) == 1 else row.update(result="violated", score=0)
        return {"dimensions": [row]}, {"call_id": f"fix-{len(calls)}"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CORRECTION_EVASION"):
        run(sample, output, correction)
    assert len(calls) == 2


def test_missing_verdict_exhaustion_never_turns_into_grounded_unknown(sample):
    output = {"dimensions": deepcopy(sample[2])}
    output["dimensions"][0]["evidence"] = []
    calls = []
    def correction(**kw):
        calls.append(kw)
        row = deepcopy(sample[2][0])
        row.pop("result")
        row.pop("score")
        return {"dimensions": [row]}, {"call_id": f"fix-{len(calls)}"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        run(sample, output, correction)
    assert len(calls) == 2


def test_incoherent_original_verdict_is_not_a_locked_decision(sample):
    # Real 2026-11-20 chem deadlock: C emitted satisfied with score 1, which
    # bind_batch rejects; locking it made every coherent correction "evasion"
    # while returning it stayed unresolved forever.
    output = {"dimensions": [deepcopy(sample[2][0]), deepcopy(sample[2][1])]}
    output["dimensions"][0].update(result="satisfied", score=1)
    calls = []
    def correction(**kw):
        calls.append(kw)
        assert kw["data"]["target_dimension_ids"] == ["a"]
        row = deepcopy(sample[2][0])
        row.update(result="violated", score=1)
        return {"dimensions": [row]}, {"call_id": f"fix-{len(calls)}"}
    report = run(sample, output, correction)
    assert len(calls) == 1 and report["status"] == "completed"
    assert report["dimensions"][0]["result"] == "violated"
    assert report["dimensions"][0]["score"] == 1
    assert report["dimensions"][1]["result"] == "satisfied"
    assert report["coverage"]["decided"] == 2


def test_incoherent_original_still_needs_a_coherent_correction(sample):
    output = {"dimensions": [deepcopy(sample[2][0]), deepcopy(sample[2][1])]}
    output["dimensions"][0].update(result="satisfied", score=1)
    calls = []
    def stuck(**kw):
        calls.append(kw)
        row = deepcopy(sample[2][0])
        row.update(result="satisfied", score=1)
        return {"dimensions": [row]}, {"call_id": f"stuck-{len(calls)}"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        run(sample, output, stuck)
    assert len(calls) == 2


def test_incoherent_original_may_be_restated_with_coherent_same_result_score(sample):
    output = {"dimensions": [deepcopy(sample[2][0]), deepcopy(sample[2][1])]}
    output["dimensions"][0].update(result="satisfied", score=1)
    def correction(**kw):
        row = deepcopy(sample[2][0])
        row.update(result="satisfied", score=2)
        return {"dimensions": [row]}, {"call_id": "coherent"}
    report = run(sample, output, correction)
    assert report["status"] == "completed"
    assert report["dimensions"][0]["score"] == 2


def test_coherent_original_verdict_still_locks_result_and_score(sample):
    output = {"dimensions": [deepcopy(sample[2][0]), deepcopy(sample[2][1])]}
    output["dimensions"][0]["evidence"] = []
    def evade(**kw):
        row = deepcopy(sample[2][0])
        row.update(result="satisfied", score=1)
        return {"dimensions": [row]}, {"call_id": "evade"}
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CORRECTION_EVASION"):
        run(sample, output, evade)


def test_nonapplicable_indicator_must_clear_counts_and_enum():
    unit = {"unit_id": "u", "body": "The overview defers all topic content.",
            "content_hash": sha("The overview defers all topic content.")}
    dim = {"dimension_id": "d", "indicators": [{"indicator_id": "i"}]}
    row = {
        "dimension_id": "d", "result": "violated", "score": 0,
        "reason": "The overview is navigation text, not a worked example.",
        "evidence": [{"field": "body", "quote": "overview defers all topic content"}],
        "indicators": [{
            "indicator_id": "i", "applicable": False,
            "numerator": None, "denominator": None,
            "enum_value": "null_no_worked_example", "unit": "worked example",
            "evidence": [{"field": "body", "quote": "overview defers all topic content"}],
            "unknown_reason": "not mandated in this navigation unit",
        }],
    }
    bound = bind_batch({"dimensions": [row]}, [dim], unit)
    assert bound["status"] == "incomplete"
    assert bound["dimensions"][0]["indicators"][0]["validation_errors"] == [
        "INDICATOR_NONAPPLICABILITY_INVALID"
    ]


def test_nonapplicable_indicator_accepts_explicit_null_marker_fields():
    unit = {"unit_id": "u", "body": "The overview defers all topic content.",
            "content_hash": sha("The overview defers all topic content.")}
    dim = {"dimension_id": "d", "indicators": [{"indicator_id": "i"}]}
    row = {
        "dimension_id": "d", "result": "violated", "score": 0,
        "reason": "The overview is navigation text, not a worked example.",
        "evidence": [{"field": "body", "quote": "overview defers all topic content"}],
        "indicators": [{
            "indicator_id": "i", "applicable": False,
            "numerator": None, "denominator": None,
            "enum_value": None, "unit": "worked example",
            "evidence": [{"field": "body", "quote": "overview defers all topic content"}],
            "unknown_reason": "not mandated in this navigation unit",
        }],
    }
    bound = bind_batch({"dimensions": [row]}, [dim], unit)
    assert bound["status"] == "completed"
    indicator = bound["dimensions"][0]["indicators"][0]
    assert indicator["processing_status"] == "complete"
    assert indicator["result"] == "not_applicable"

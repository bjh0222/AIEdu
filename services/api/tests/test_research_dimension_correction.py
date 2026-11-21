"""Contract corrections are model requests, never silent rubric rewrites."""
from copy import deepcopy

import pytest

from app.v2 import rubric_research as research
from app.v2.contracts import DomainError
from test_rubric_research_contract import dimension


def valid():
    return {"dimensions": [dimension(1, "claim:exact")]}


def correct(output):
    return research.validate_generated_dimensions("run", "worker", 0, {},
        "research_rubric_assessment", output, "assessment", [{"claim_id": "claim:exact"}])


def test_valid_rubric_does_not_trigger_paid_correction(monkeypatch):
    monkeypatch.setattr(research, "_call", lambda *a, **kw: pytest.fail("unnecessary correction"))
    dimensions, audits = correct(valid())
    assert dimensions == valid()["dimensions"] and audits == []


@pytest.mark.parametrize("fault,code", [
    ("scope", "RESEARCH_DIMENSION_SCOPE_MISMATCH"),
    ("source", "RESEARCH_DIMENSION_SOURCE_UNBOUND"),
    ("unknown_zero", "RESEARCH_DIMENSION_UNKNOWN_RULE_INVALID"),
    ("unknown_missing", "RESEARCH_DIMENSION_UNKNOWN_RULE_INVALID"),
    ("nonlist", "RESEARCH_DIMENSION_SCHEMA_INVALID"),
])
def test_model_corrects_exact_contract_and_original_output_is_preserved(monkeypatch, fault, code):
    original = valid()
    row = original["dimensions"][0]
    if fault == "scope": row["scope"] = "assessment: descriptive text is not an enum"
    if fault == "source": row["source_refs"] = ["paper-not-claim"]
    if fault == "unknown_zero": row["result_schema"]["unknown_score"] = 0
    if fault == "unknown_missing": row["result_schema"].pop("unknown_score")
    if fault == "nonlist": original["dimensions"] = None
    before = deepcopy(original)
    calls = []
    def call(*args, **kwargs):
        calls.append(args)
        return valid(), {"call_id": "contract-correction"}
    monkeypatch.setattr(research, "_call", call)
    dimensions, audits = correct(original)
    assert dimensions == valid()["dimensions"] and original == before
    assert len(calls) == 1 and calls[0][4] == "research_rubric_assessment_contract_retry_1"
    data = calls[0][5]
    assert data["original_output"] == original and data["failure_code"] == code
    assert data["dimension_json_schema"]["properties"]["scope"]["const"] == "assessment"
    assert data["source_claims"] == [{"claim_id": "claim:exact"}]
    assert data["validation_errors"] and data["result_field_contract"]["unknown_score"] is None
    assert audits == [{"call_id": "contract-correction"}]


@pytest.mark.parametrize("fault", ["empty", "unsupported", "unknown_zero"])
def test_repeated_invalid_corrections_cannot_evade_validation(monkeypatch, fault):
    invalid = valid()
    if fault == "empty": invalid = {"dimensions": []}
    if fault == "unsupported": invalid["dimensions"][0]["source_refs"] = ["invented"]
    if fault == "unknown_zero": invalid["dimensions"][0]["result_schema"]["unknown_score"] = 0
    calls = []
    def call(*args, **kwargs):
        calls.append(args)
        return deepcopy(invalid), {"call_id": str(len(calls))}
    monkeypatch.setattr(research, "_call", call)
    with pytest.raises(DomainError):
        correct(invalid)
    assert [c[4] for c in calls] == ["research_rubric_assessment_contract_retry_1",
                                    "research_rubric_assessment_contract_retry_2"]


def test_correcting_contract_preserves_unknown_null_and_does_not_assign_pass(monkeypatch):
    output = valid()
    output["dimensions"][0]["scope"] = "wrong scope"
    monkeypatch.setattr(research, "_call", lambda *a, **kw: (valid(), {"call_id": "c"}))
    dimensions, _ = correct(output)
    assert dimensions[0]["result_schema"]["unknown_score"] is None
    assert dimensions[0]["limitations"] == ["not calibrated"]
    assert "verdict" not in dimensions[0] and "pass" not in dimensions[0]


def json_schema():
    fields = {"result": {"enum": ["satisfied", "violated", "undecidable", "not_applicable"]},
        "scope": {"type": "string"}, "evidence": {"type": "array"}, "score": {"enum": [0, 1, 2, None]},
        "source": {"type": "array"}, "version": {"type": "integer"}, "unknown_score": {"type": "null"}}
    return {"type": "object", "properties": fields, "required": list(fields)}


def test_standard_json_schema_is_not_mistaken_for_an_observation(monkeypatch):
    output = valid()
    output["dimensions"][0]["result_schema"] = json_schema()
    before = deepcopy(output)
    monkeypatch.setattr(research, "_call", lambda *a, **kw: pytest.fail("valid schema does not need a paid call"))
    dimensions, audits = correct(output)
    assert dimensions[0]["result_schema"] == before["dimensions"][0]["result_schema"]
    assert output == before and audits == []


@pytest.mark.parametrize("fault", ["missing", "zero", "unsupported_result"])
def test_json_schema_missing_unknown_or_wrong_enum_cannot_pass(fault):
    schema = json_schema()
    if fault == "missing": schema["required"].remove("unknown_score")
    if fault == "zero": schema["properties"]["unknown_score"] = {"const": 0}
    if fault == "unsupported_result": schema["properties"]["result"]["enum"].append("partially_satisfied")
    assert research.result_contract_errors(schema)


def test_feedback_reports_all_invalid_fields_together():
    output = valid()
    output["dimensions"][0].update(scope="description", source_refs=["invented"])
    output["dimensions"][0]["result_schema"].pop("unknown_score")
    with pytest.raises(DomainError) as err:
        research.validate_dimensions(output, "assessment", [{"claim_id": "claim:exact"}])
    fields = {e["field"] for e in err.value.details["validation_errors"]}
    assert {"dimensions.0.scope", "dimensions.0.source_refs", "dimensions.0.result_schema.unknown_score"} <= fields

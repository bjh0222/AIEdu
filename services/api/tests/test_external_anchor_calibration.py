"""Synthetic mechanics only; no expert truth or paid calls inferred from tests."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest
from app.v2.criterion_review import bind_rows
from app.v2.external_anchor_calibration import anchor_case, blind_unit, compare, summary


def fixture(result="satisfied"):
    contract = {"check_id": "A.clause", "target": "unit", "clauses": {"relation": "Inspect semantic relation"}}
    contract["criterion_hash"] = digest(contract)
    return anchor_case(domain="history_culture", task="Original task", body="引文与论断之间有推理。",
        checks=[{"item_id": "A.clause", "criterion_contract": contract}],
        expected=[{"check_id": "A.clause", "clause_id": "relation", "result": result,
                   "evidence_location": "commentary p4"}],
        source={"url": "https://example.org/original", "sha256": "a"*64,
                "response_location": "SECRET_SAMPLE", "label_location": "commentary p4",
                "label_protocol": "SECRET_EXPECTED_LABEL_PROTOCOL"})


def observation(case, result="satisfied"):
    unit = blind_unit(case)
    request = {"request_id": "r", "check_id": "A.clause", "item_id": None,
               "criterion": case["checks"][0]["criterion_contract"]}
    output = {"assessments": [{"request_id": "r", "clauses": [{"clause_id": "relation", "result": result,
        "reason": "The observed relation is checked against the actual supplied text.",
        "evidence": [{"field": "unit:body", "quote": "引文与论断"}]}]}]}
    return {"criteria": bind_rows(output, [request], {"unit:body": case["body"]}, {"call_id": "call"}, unit["unit_id"])}


def test_blind_identity_and_content_are_independent_of_expert_labels():
    a, b = fixture("satisfied"), fixture("violated")
    assert a["case_hash"] != b["case_hash"]
    assert blind_unit(a) == blind_unit(b)
    assert blind_unit(a)["body"] == a["body"]
    assert not any(s in str(blind_unit(a)) for s in ("SECRET_SAMPLE", "EXPECTED", "example.org", "expected"))


@pytest.mark.parametrize("fault", ["hash", "unit", "quote", "offset", "field", "duplicate", "flag"])
def test_invalid_evidence_or_contract_is_execution_failure_not_valid_unknown(fault):
    case = fixture()
    report = observation(case, "undecidable")
    row = report["criteria"][0]
    evidence = row["clauses"][0]["evidence"][0]
    if fault == "hash": row["criterion_hash"] = "wrong"
    if fault == "unit": row["unit_id"] = "wrong"
    if fault == "quote": evidence["quote"] = "missing"
    if fault == "offset": evidence["start_byte"] += 1
    if fault == "field": evidence["field"] = []
    if fault == "duplicate": report["criteria"].append(deepcopy(row))
    if fault == "flag": row["clauses"][0]["invalid_or_missing_observation"] = True
    result = compare(case, report)[0]
    assert result["execution_category"] == "invalid_or_missing_execution"
    assert result["prediction"] is result["agreement"] is None


def test_partial_or_full_total_scores_are_not_used_in_point_comparison():
    case = fixture("violated")
    report = observation(case, "violated")
    report["total_score"] = 100
    assert compare(case, report)[0]["agreement"] is True
    result = summary(compare(case, report), 1)
    assert result["error_recall"] == 1
    assert result["calibration_status"] == "not_calibrated"
    assert result["professional_certification"] is False


def test_genuine_abstention_keeps_execution_but_not_decision_coverage():
    case = fixture()
    result = summary(compare(case, observation(case, "undecidable")), 1)
    assert result["execution_coverage"] == 1 and result["decision_coverage"] == 0
    assert result["abstentions"] == 1 and result["invalid_observations"] == 0


def test_labels_must_cover_every_predeclared_clause_without_duplicates():
    case = fixture()
    kwargs = {k: case[k] for k in ("task", "body", "checks", "expected", "source")}
    kwargs["domain"] = case["domain_key"]
    for labels in ([], [*case["expected"], *case["expected"]], [{**case["expected"][0], "clause_id": "other"}]):
        with pytest.raises(DomainError):
            anchor_case(**{**kwargs, "expected": labels})


def test_source_locations_and_contract_hash_cannot_be_omitted():
    case = fixture()
    kwargs = {k: case[k] for k in ("task", "body", "checks", "expected", "source")}
    kwargs["domain"] = case["domain_key"]
    with pytest.raises(DomainError):
        anchor_case(**{**kwargs, "source": {"url": "source"}})
    kwargs["checks"][0]["criterion_contract"]["clauses"]["relation"] = "changed after freezing"
    with pytest.raises(DomainError):
        anchor_case(**kwargs)


@pytest.mark.parametrize("fault", ["missing_check", "blank_check", "blank_clause", "bad_source_hash", "label_id_type"])
def test_malformed_case_metadata_is_rejected_as_domain_error(fault):
    case = fixture()
    kwargs = {k: case[k] for k in ("task", "body", "checks", "expected", "source")}
    kwargs["domain"] = case["domain_key"]
    if fault == "missing_check": kwargs["checks"] = [None]
    if fault == "blank_check": kwargs["checks"][0]["item_id"] = ""
    if fault == "blank_clause":
        contract = kwargs["checks"][0]["criterion_contract"]
        contract["clauses"] = {"": "description"}
        contract["criterion_hash"] = digest({k: v for k, v in contract.items() if k != "criterion_hash"})
    if fault == "bad_source_hash": kwargs["source"]["sha256"] = "not-a-sha256"
    if fault == "label_id_type": kwargs["expected"][0]["clause_id"] = []
    with pytest.raises(DomainError):
        anchor_case(**kwargs)


@pytest.mark.parametrize("field", ["body", "expected", "source"])
def test_comparison_refuses_case_changed_after_protocol_freeze(field):
    case = fixture()
    report = observation(case)
    if field == "body": case["body"] += "changed"
    if field == "expected": case["expected"][0]["result"] = "violated"
    if field == "source": case["source"]["response_location"] += "changed"
    with pytest.raises(DomainError, match="EXTERNAL_ANCHOR_CASE_HASH_MISMATCH"):
        compare(case, report)

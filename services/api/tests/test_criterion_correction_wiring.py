"""Real consumer wiring with synthetic provider boundaries, no paid calls."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest
from app.v2.criterion_review import evaluate_criteria


def setup(monkeypatch):
    monkeypatch.setattr("app.v2.checker.applies", lambda *args: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *args: {"task": {"raw_input": "比较证据"}, "sources": []})
    checks = [{"item_id": name, "decision_type": "semantic", "verifier_binding": "fixture",
        "pass_condition": "Inspect the actual body, not the plan"} for name in ("C3.correctness", "C4.constraints")]
    state = {"context": {}, "check_plan": {"check_items": checks}, "execution_policy": {"network_tool_rounds": 0}}
    unit = {"unit_id": "u", "title": "历史论证", "body": "明确的正文证据，另有需要核对的论证。"}
    return state, unit


def response(request, fields, result="satisfied"):
    return {"request_id": request["request_id"], "rating": None, "clauses": [
        {"clause_id": c, "result": result, "reason": "依据实际正文逐项分析的具体判断。",
            "evidence": [{"field": "unit:body", "quote": fields["unit:body"]}]}
        for c in request["criterion"]["clauses"]]}


def test_consumer_preserves_primary_request_and_only_corrects_missing_execution(monkeypatch):
    state, unit = setup(monkeypatch)
    calls, original = [], {}
    from app.v2.criterion_review import C_REVIEW_SKILL
    expected_entry = {"source_id": "assessment-review", "version": C_REVIEW_SKILL["version"],
        "sha256": C_REVIEW_SKILL["hash"], "consumer": "C", "message_position": "system.skill"}
    def generate(*args, **kw):
        calls.append(deepcopy(kw))
        d = kw["data"]
        assert kw["entries"] == [expected_entry]
        assert f"# assessment-review.v{C_REVIEW_SKILL['version']}" in kw["system"]
        if len(calls) == 1:
            assert kw["stage"] == "criterion_assessing" and kw["role"] == "review"
            assert set(d["fields"]) == {"unit:body"}  # Old cache identity is intact.
            original.update(assessments=[response(d["requests"][0], d["fields"], "violated")])
            return original, {"call_id": "settled-original"}, {"calls": []}
        assert kw["stage"] == "criterion_contract_correcting" and kw["role"] == "review"
        assert len(d["requests"]) == 1 and d["requests"][0]["check_id"] == "C4.constraints"
        assert d["execution_correction"]["not_a_content_repair"]
        assert d["execution_correction"]["original_output_hash"] == digest(original)
        assert d["previous_output_untrusted"] == original
        assert "context:task" in d["fields"] and d["fields"]["unit:body"] == unit["body"]
        assert kw["output_schema"]["required"] == ["assessments"]
        assert "Per-item quotes must come from that item." not in kw["system"]
        assert "Only Q4.transfer" in kw["system"]
        return {"assessments": [response(d["requests"][0], d["fields"])]}, {"call_id": "new-correction"}, {"calls": []}
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    result = evaluate_criteria("r", "w", 0, state, [unit], "v")
    assert len(calls) == 2 and result["status"] == "completed" and result["result"] == "violated"
    assert result["criteria"][0]["audit"]["call_id"] == "settled-original"
    assert result["criteria"][1]["audit"]["call_id"] == "new-correction"
    assert result["audits"][0]["execution_contract"]["corrections"][0]["complete"]
    assert result["calibration_status"] == "not_calibrated" and not result["professional_certification"]


@pytest.mark.parametrize("fault", ["unresolved", "UNKNOWN_CALL", "PROVIDER_CALL_FAILED"])
def test_correction_failure_stops_later_units_without_content_repair(monkeypatch, fault):
    state, unit = setup(monkeypatch)
    seen = []
    def generate(*args, **kw):
        seen.append(kw)
        if len(seen) == 1:
            return {"assessancements": []}, {"call_id": "original"}, {}
        if fault == "unresolved":
            return {"assessancements": []}, {"call_id": "unresolved"}, {}
        raise DomainError(fault, 502)
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    expected = "CRITERION_CORRECTION_UNRESOLVED" if fault == "unresolved" else fault
    with pytest.raises(DomainError, match=expected):
        evaluate_criteria("r", "w", 0, state, [unit, unit | {"unit_id": "later"}], "v")
    assert len(seen) == (3 if fault == "unresolved" else 2)
    assert {x["unit_id"] for x in seen} == {"u"}

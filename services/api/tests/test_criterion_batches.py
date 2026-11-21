"""Cross-domain lossless partitioning and fail-stop checks, no real model."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError
from app.v2.criterion_batches import MAX_CLAUSES, batch_payload, batches


def request(check, item=None, count=2):
    return {"request_id": f"{check}:{item}", "check_id": check, "item_id": item,
        "criterion": {"target": "each_item" if item else "unit", "criterion_hash": check,
            "clauses": {f"c{i}": f"Complete original criterion {i}" for i in range(count)}}}


def test_all_requests_clauses_and_evidence_context_are_preserved_without_mutation():
    requests = [request(c, item) for c in ("Q1.exercise", "Q2.exercise", "Q4.exercise") for item in ("i1", "i2")]
    requests += [request(f"science.{i}") for i in range(8)]
    before = deepcopy(requests)
    data = {"requests": requests, "fields": {"unit:body": "Full unabridged chapter",
        "item:i2:reasoning": "Peer reasoning is necessary for transfer comparison"},
        "sources": ["Complete selected source"], "task": "Original whole request", "requirements": ["A arrangement"]}
    parts = batches(requests)
    assert requests == before and len(parts) > 1
    assert sorted(r["request_id"] for p in parts for r in p) == sorted(r["request_id"] for r in requests)
    assert all(sum(len(r["criterion"]["clauses"]) for r in p) <= MAX_CLAUSES for p in parts)
    assert all(len({r["item_id"] for r in p}) == 1 for p in parts)
    for part in parts:
        payload = batch_payload(data, part)
        assert payload["fields"] == data["fields"] and payload["sources"] == data["sources"]
        assert all(payload[k] == data[k] for k in ("task", "requirements"))
        assert payload["requests"] == part


@pytest.mark.parametrize("domain", ["science", "computing", "humanities", "social_science", "arts"])
def test_non_math_and_large_indivisible_criteria_are_not_replaced_by_sample_rules(domain):
    original = request(domain + ".source_reasoning", count=MAX_CLAUSES + 2)
    assert batches([original]) == [[original]]
    with pytest.raises(DomainError, match="CRITERION_REQUEST_DUPLICATE"):
        batches([original, original])


@pytest.mark.parametrize("failure", ["UNKNOWN_CALL", "PROVIDER_CALL_FAILED"])
def test_failed_first_batch_stops_remaining_calls_and_later_unit_waves(monkeypatch, failure):
    from app.v2.criterion_review import evaluate_criteria
    monkeypatch.setattr("app.v2.checker.applies", lambda *a: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": "Whole request", "sources": []})
    calls = []
    def invoke(*args, **kw):
        calls.append(kw)
        assert kw["output_tokens"] == 32768
        assert kw["data"]["fields"] == {"unit:body": "Complete body"}
        raise DomainError(failure, 502)
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", invoke)
    checks = [{"item_id": f"science.c{i}", "decision_type": "semantic", "verifier_binding": "fixture",
        "pass_condition": "Actual scientific criterion"} for i in range(MAX_CLAUSES + 1)]
    state = {"context": {}, "check_plan": {"check_items": checks}, "execution_policy": {"check_concurrency": 1}}
    with pytest.raises(DomainError, match=failure):
        evaluate_criteria("r", "w", 0, state,
            [{"unit_id": str(i), "title": "Unit", "body": "Complete body"} for i in range(3)], "v")
    assert len(calls) == 1


def test_multiple_actual_batches_return_original_request_order_and_each_call_evidence(monkeypatch):
    from app.v2.criterion_review import evaluate_criteria
    monkeypatch.setattr("app.v2.checker.applies", lambda *a: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": "Original request", "sources": []})
    checks = [{"item_id": f"humanities.c{i}", "decision_type": "semantic", "verifier_binding": "fixture",
        "pass_condition": "Actual evidence and commentary criterion"} for i in range(MAX_CLAUSES + 1)]
    calls = []
    def invoke(*args, **kw):
        calls.append(kw)
        return {"assessments": [{"request_id": r["request_id"], "rating": None,
            "clauses": [{"clause_id": c, "result": "violated" if r["check_id"].endswith("c6") else "satisfied",
                "reason": "A concrete fixed-content observation", "evidence": [{"field": "unit:body", "quote": "Exact evidence"}]}
                for c in r["criterion"]["clauses"]]} for r in kw["data"]["requests"]]}, {"call_id": str(len(calls))}, {}
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", invoke)
    report = evaluate_criteria("r", "w", 0, {"context": {}, "check_plan": {"check_items": checks}},
        [{"unit_id": "u", "title": "Unit", "body": "Exact evidence"}], "v")
    assert len(calls) == 2 and len(report["audits"]) == 2
    assert report["coverage"]["complete_observations"] == len(checks)
    assert [r["check_id"] for r in report["criteria"]] == [c["item_id"] for c in checks]
    assert report["result"] == "violated" and report["status"] == "completed"
    assert report["criteria"][-1]["audit"]["call_id"] == "2"
    assert report["calibration_status"] == "not_calibrated"


@pytest.mark.parametrize("failure", ["UNKNOWN_CALL", "PROVIDER_CALL_FAILED"])
def test_sibling_cannot_start_another_batch_after_observed_failure(monkeypatch, failure):
    from threading import Event
    from app.v2.criterion_review import evaluate_criteria
    monkeypatch.setattr("app.v2.checker.applies", lambda *a: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": "Whole request", "sources": []})
    entered, failure_observed = Event(), Event()
    original_error = DomainError(failure, 502)
    calls = []
    def invoke(*args, **kw):
        calls.append(kw["unit_id"])
        if kw["unit_id"] == "failing":
            assert entered.wait(5)
            raise original_error
        entered.set()
        # Unblock the already-sent sibling only when the guarded failing
        # worker has actually recorded its failure, without timing sleeps.
        assert failure_observed.wait(5)
        return {"assessments": []}, {"call_id": "sibling"}, {}
    class ObservedEvent(Event):
        def set(self):
            super().set()
            failure_observed.set()
    monkeypatch.setattr("app.v2.criterion_review.Event", ObservedEvent)
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", invoke)
    checks = [{"item_id": f"science.c{i}", "decision_type": "semantic", "verifier_binding": "fixture",
        "pass_condition": "Actual scientific criterion"} for i in range(MAX_CLAUSES + 1)]
    state = {"context": {}, "check_plan": {"check_items": checks}, "execution_policy": {"check_concurrency": 2}}
    with pytest.raises(DomainError, match=failure) as caught:
        evaluate_criteria("r", "w", 0, state,
            [{"unit_id": name, "title": "Unit", "body": "Complete body"}
             for name in ("failing", "sibling", "later-wave")], "v")
    assert caught.value is original_error
    assert sorted(calls) == ["failing", "sibling"]

"""Consumer wiring: C-output correction is distinct from B content repair."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, sha
from app.v2.research_length_recovery import REVIEW_OUTPUT_TOKENS


@pytest.fixture
def candidate():
    unit = {"unit_id": "u", "body": "Exact evidence. Other evidence.",
        "content_hash": sha("Exact evidence. Other evidence.")}
    dims = [{"dimension_id": x, "source_refs": ["claim:" + x],
             "indicators": [{"indicator_id": "indicator-" + x}]} for x in ("a", "b")]
    binding = {"id": "rubric", "version": 1, "hash": "frozen", "binding_hash": "binding",
        "content": {"dimensions": dims, "source_claims": [{"claim_id": "claim:" + x} for x in ("a", "b")]}}
    rows = [{"dimension_id": x, "result": "satisfied", "score": 2, "reason": "Supported observation.",
        "evidence": [{"field": "body", "quote": q}],
        "indicators": [{"indicator_id": "indicator-" + x, "applicable": True,
            "numerator": 1, "denominator": 1, "unit": "claims",
            "enum_value": None, "evidence": [{"field": "body", "quote": q}],
            "unknown_reason": None}]}
        for x, q in (("a", "Exact evidence."), ("b", "Other evidence."))]
    return unit, binding, rows


def execute(candidate):
    from app.v2.import_profiles import review_research_rubrics
    unit, binding, _ = candidate
    return review_research_rubrics("run", "worker", 1,
        {"context": {"research_rubrics": [binding]}}, [unit], "version")


def test_only_invalid_observation_gets_durable_review_correction(monkeypatch, candidate):
    from app.v2 import inference
    original = deepcopy(candidate)
    calls = []

    def invoke(*a, **kw):
        calls.append(kw)
        assert kw["role"] == "review" and kw["unit_id"] == "u" and kw["output_tokens"] == REVIEW_OUTPUT_TOKENS
        if kw["stage"] == "rubric_reviewing":
            broken = deepcopy(candidate[2])
            broken[0]["evidence"][0]["quote"] = "not in body"
            return {"dimensions": broken}, {"call_id": "original", "cache": "identical_ancestral_request"}
        assert kw["stage"] == "rubric_contract_correcting"
        assert "output_schema" not in kw  # exact v1 paid-request identity
        data = kw["data"]
        assert data["target_dimension_ids"] == ["a"] and data["unit"] == candidate[0]
        assert data["research_rubric"]["dimensions"] == [candidate[1]["content"]["dimensions"][0]]
        return {"dimensions": [deepcopy(candidate[2][0])]}, {"call_id": "correction"}

    monkeypatch.setattr(inference, "invoke", invoke)
    report = execute(candidate)
    assert len(calls) == 2 and candidate == original
    assert report["processing_status"] == "complete"
    assert [r["audit"]["call_id"] for r in report["dimensions"]] == ["correction", "original"]
    assert len(report["audits"]) == 2 and len(report["batches"][0]["corrections"]) == 1
    evidence = report["dimensions"][0]["evidence"][0]
    assert evidence["unit_hash"] == candidate[0]["content_hash"]
    assert evidence["end_byte"] == len("Exact evidence.".encode())


def test_consumer_does_not_mark_unresolved_missing_output_completed(monkeypatch, candidate):
    from app.v2 import inference
    calls = []

    def broken(*a, **kw):
        calls.append(kw)
        return {"dimensions": []}, {"call_id": str(len(calls))}

    monkeypatch.setattr(inference, "invoke", broken)
    with pytest.raises(DomainError, match="RESEARCH_CONTENT_REVIEW_CONTRACT_UNRESOLVED"):
        execute(candidate)
    assert len(calls) == 3


def test_consumer_stops_immediately_on_new_unknown(monkeypatch, candidate):
    from app.v2 import inference
    calls = []

    def unknown(*a, **kw):
        calls.append(kw)
        if len(calls) == 1:
            return {}, {"call_id": "first"}
        raise DomainError("UNKNOWN_CALL", 502)

    monkeypatch.setattr(inference, "invoke", unknown)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        execute(candidate)
    assert len(calls) == 2


def test_grounded_unknown_is_executed_without_transport_retry(monkeypatch, candidate):
    from app.v2 import inference
    rows = deepcopy(candidate[2])
    rows[0].update(result="undecidable", score=None, reason="Insufficient context, with a real evidence span.")
    calls = []

    def observed(*a, **kw):
        calls.append(kw)
        return {"dimensions": rows}, {"call_id": "first"}

    monkeypatch.setattr(inference, "invoke", observed)
    report = execute(candidate)
    assert len(calls) == 1 and report["dimensions"][0]["score"] is None
    assert report["batches"][0]["coverage"]["grounded_abstentions"] == 1


@pytest.mark.parametrize("fault", ["missing", "duplicate", "invalid", "wrong_rubric", "wrong_version", "old_unbound"])
def test_aggregation_cannot_sign_incomplete_unit_coverage(monkeypatch, candidate, fault):
    from app.v2 import inference
    from app.v2.checker import _apply_research_review
    from app.v2.quality import compile_plan

    monkeypatch.setattr(inference, "invoke", lambda *a, **kw: ({"dimensions": deepcopy(candidate[2])}, {"call_id": "C"}))
    diagnostic = execute(candidate)
    context = {"domain_key": "general", "task_intent": {"conflicts": []}, "spec_bindings": [],
        "quality_profiles": [], "research_rubrics": [candidate[1]]}
    plan = compile_plan(context)
    units = [candidate[0]]
    if fault == "missing":
        units.append({**candidate[0], "unit_id": "u2"})
    elif fault == "duplicate":
        diagnostic["dimensions"].append(deepcopy(diagnostic["dimensions"][0]))
    elif fault == "invalid":
        diagnostic["dimensions"][0]["processing_status"] = "invalid_or_missing_observation"
    elif fault == "wrong_rubric":
        diagnostic["dimensions"][0]["rubric_ref"]["hash"] = "other"
    elif fault == "wrong_version":
        diagnostic["version_id"] = "previous-version"
    else:
        diagnostic.pop("processing_status")
    result = {"checks": [], "findings": [], "diagnostic_review": diagnostic}
    _apply_research_review(result, {"check_plan": plan}, units, "version")
    check = next(c for c in result["checks"] if c["item_id"] == "R.a")
    assert check["result"] == "undecidable" and check["processing_status"] != "complete"


@pytest.mark.parametrize("decision,score", [("satisfied", 2), ("violated", 0), ("undecidable", None), ("not_applicable", None)])
def test_complete_aggregation_retains_actual_model_decision(monkeypatch, candidate, decision, score):
    from app.v2 import inference
    from app.v2.checker import _apply_research_review
    from app.v2.quality import compile_plan

    rows = deepcopy(candidate[2])
    rows[0].update(result=decision, score=score)
    if decision == "not_applicable":
        for indicator in rows[0]["indicators"]:
            indicator.update(applicable=False, numerator=None, denominator=None,
                enum_value=None, evidence=[], unknown_reason="Not applicable to this unit")
    monkeypatch.setattr(inference, "invoke", lambda *a, **kw: ({"dimensions": rows}, {"call_id": "C"}))
    diagnostic = execute(candidate)
    plan = compile_plan({"domain_key": "general", "task_intent": {"conflicts": []},
        "spec_bindings": [], "quality_profiles": [], "research_rubrics": [candidate[1]]})
    result = {"checks": [], "findings": [], "diagnostic_review": diagnostic}
    _apply_research_review(result, {"check_plan": plan}, [candidate[0]], "version")
    check = next(c for c in result["checks"] if c["item_id"] == "R.a")
    assert check["result"] == decision and check["processing_status"] == "complete"


def test_missing_dimension_contract_reaches_actual_invoke_and_http_payload(client, monkeypatch, candidate):
    """Do not replace invoke: inspect provider HTTP and its durable request."""
    import json
    from types import SimpleNamespace
    from uuid import uuid4
    import httpx
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.contracts import Confirmation, StartRun, TaskInput, canonical
    from app.v2.runtime import claim_root, job, load
    from app.v2.storage import CallDispatch
    from app.v2.tasks import create_task, mutate_input, start_run
    from app.v2.import_profiles import review_research_rubrics

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr("app.v2.inference.get_capacity", lambda *args: {
        "max_output_tokens": 32768, "context_tokens": 1048576, "safety_margin_tokens": 1024,
        "estimate_method": "fixture", "source_uri_or_config": "fixture", "output_limit_parameter": "max_tokens"})
    profile = SimpleNamespace(llm_base_url="https://research-contract.invalid/v1", llm_model="fixture",
        llm_model_family="fixture", llm_api_key="fixture-not-a-secret", llm_timeout=1,
        llm_temperature=0.3, llm_thinking="enabled", llm_reasoning_effort="high", llm_json_mode=True)
    monkeypatch.setattr("app.v2.model_roles.for_role", lambda *args, **kwargs: profile)
    created = create_task(TaskInput(title="schema wire fixture", raw_input="synthetic fixture only"), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    started = start_run(created["task_id"], StartRun(expected_revision=1,
        confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    lease = claim_root(started["run_id"], "fixture")
    with SessionLocal() as db:
        state = load(job(db, started["run_id"]).state_json)
    unit, binding, rows = candidate
    binding["content"]["dimensions"][0]["indicators"] = [{"indicator_id": "executed", "unit": "claims"}]
    rows[0]["indicators"] = [{"indicator_id": "executed", "applicable": True, "numerator": 1,
        "denominator": 1, "unit": "claims", "enum_value": None,
        "evidence": [{"field": "body", "quote": "Exact evidence."}], "unknown_reason": None}]
    state["context"]["research_rubrics"] = [binding]
    payloads = []
    def post(url, *, headers, json, timeout, follow_redirects):
        assert url == "https://research-contract.invalid/v1/chat/completions" and follow_redirects is False
        payloads.append(deepcopy(json))
        response = {"dimensions": [rows[1]]} if len(payloads) == 1 else {"dimensions": [rows[0]]}
        return httpx.Response(200, json={"id": "fixture-receipt", "choices": [{"finish_reason": "stop",
            "message": {"content": __import__("json").dumps(response)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr("app.v2.inference.httpx.post", post)
    result = review_research_rubrics(started["run_id"], "fixture", lease["epoch"], state, [unit], "version")
    assert len(payloads) == 2 and result["processing_status"] == "complete"
    wire_data = json.loads(payloads[1]["messages"][1]["content"])
    assert wire_data["previous_target_outputs"] == [] and wire_data["target_dimension_ids"] == ["a"]
    assert wire_data["unit"] == unit
    assert canonical(wire_data["response_contract"]).decode() in payloads[1]["messages"][0]["content"]
    assert "OUTPUT RESPONSE CONTRACT" not in payloads[0]["messages"][0]["content"]
    assert payloads[1]["model"] == "fixture" and payloads[1]["max_tokens"] == 32768
    with SessionLocal() as db:
        correction = db.get(CallDispatch, result["dimensions"][0]["audit"]["call_id"])
        saved = load(correction.request_json)
        assert saved["messages"] == payloads[1]["messages"]
        assert saved["output_schema"] == wire_data["response_contract"]
        assert correction.status == "settled" and correction.outcome == "success"

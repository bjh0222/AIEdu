"""Transport observations survive settlement without revealing provider text.

All HTTP attempts below are mocked; conftest owns the isolated database.
"""
from copy import deepcopy
import json

import httpx
import pytest

from test_generation_source_tools import running
from test_provider_processing_diagnostics import profile


@pytest.mark.parametrize("exception,phase", [
    (httpx.ReadTimeout, "read"), (httpx.ConnectTimeout, "connect"),
    (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool"),
    (httpx.RemoteProtocolError, "unspecified_transport"),
])
def test_http_transport_details_reach_durable_receipt_and_snapshot(client, monkeypatch, exception, phase):
    from app.config import settings
    from app.db import SessionLocal
    from app.models import ModelCallLog
    from app.v2 import inference
    from app.v2.contracts import DomainError
    from app.v2.runtime import job, load
    from app.v2.storage import CallDispatch
    from app.v2.tasks import snapshot

    run_id, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {
        "max_output_tokens": 64, "context_tokens": 100000, "safety_margin_tokens": 0,
        "estimate_method": "fixture", "source_uri_or_config": "fixture"})
    selected = profile()
    selected.llm_model_family = "fixture"
    selected.llm_read_timeout = 600
    attempts = []

    def fail(*args, **kwargs):
        attempts.append(kwargs)
        raise exception("secret-fixture https://private.invalid/?token=do-not-log")

    monkeypatch.setattr(inference.httpx, "post", fail)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        inference.invoke(run_id, "source-fixture", epoch, state, stage="checking", unit_id="fixture",
            data={}, system="fixture", profile=selected)
    assert len(attempts) == 1
    with SessionLocal() as db:
        call = db.query(CallDispatch).one()
        receipt = load(call.receipt_json)
        task_id = job(db, run_id).goal_id
        assert call.status == call.outcome == "unknown"
        assert call.usage_json is call.price_json is None
        assert receipt["failure_code"] == "PROVIDER_TRANSPORT_UNKNOWN"
        details = receipt["failure_diagnostics"]
        assert details["transport_exception"] == exception.__name__
        assert details["timeout_phase"] == phase
        assert details["configured_timeout_seconds"] == 10
        assert details["read_timeout_seconds"] == 600
        assert type(details["elapsed_seconds"]) in (int, float) and details["elapsed_seconds"] >= 0
        assert details["remote_outcome"] == "unknown"
        assert db.query(ModelCallLog).one().usage_source == "unknown"
    exposed = snapshot(task_id)["calls"]
    assert len(exposed) == 1 and exposed[0]["failure_diagnostics"] == details
    for private_text in ("secret-fixture", "private.invalid", "do-not-log"):
        assert private_text not in json.dumps(exposed)


def sent_call(client):
    from app.v2.runtime import authorize_call, mark_sent

    run_id, epoch, _ = running(client)
    call_id = authorize_call(run_id, "fixture", "request-hash", "context-hash", epoch,
        "source-fixture", "fixture-key")
    mark_sent(call_id, "source-fixture", epoch)
    return call_id


def test_transport_whitelist_rejects_text_invalid_types_and_nonfinite_numbers(client):
    from app.db import SessionLocal
    from app.v2.runtime import load, settle
    from app.v2.storage import CallDispatch

    call_id = sent_call(client)
    unsafe = {"transport_exception": "PrivateCredential", "timeout_phase": "private_phase",
        "configured_timeout_seconds": True, "read_timeout_seconds": float("inf"),
        "elapsed_seconds": float("nan"), "remote_outcome": "success",
        "exception_message": "https://private.invalid/?secret=fixture", "headers": {"Authorization": "fixture"}}
    settle(call_id, "unknown", failure_code="PROVIDER_TRANSPORT_UNKNOWN", failure_diagnostics=unsafe)
    with SessionLocal() as db:
        assert load(db.get(CallDispatch, call_id).receipt_json)["failure_diagnostics"] == {}


@pytest.mark.parametrize("value", [-1, float("-inf"), "600", None, {}, []])
def test_transport_seconds_are_only_finite_nonnegative_numbers(client, value):
    from app.db import SessionLocal
    from app.v2.runtime import load, settle
    from app.v2.storage import CallDispatch

    call_id = sent_call(client)
    settle(call_id, "unknown", failure_diagnostics={key: value for key in (
        "configured_timeout_seconds", "read_timeout_seconds", "elapsed_seconds")})
    with SessionLocal() as db:
        assert load(db.get(CallDispatch, call_id).receipt_json)["failure_diagnostics"] == {}


def test_transport_diagnostics_obey_settlement_identity_and_do_not_infer_usage(client):
    from app.db import SessionLocal
    from app.v2.contracts import DomainError
    from app.v2.runtime import load, settle
    from app.v2.storage import CallDispatch

    call_id = sent_call(client)
    details = {"transport_exception": "ReadTimeout", "timeout_phase": "read",
        "configured_timeout_seconds": 120, "read_timeout_seconds": 600.0,
        "elapsed_seconds": 1.729, "remote_outcome": "unknown"}
    # A known failed fixture checks settled-receipt identity without changing
    # production's rule that a real transport exception has unknown outcome.
    args = {"failure_code": "PROVIDER_TRANSPORT_UNKNOWN", "failure_diagnostics": details}
    assert settle(call_id, "failed", **args) == {"status": "settled"}
    assert settle(call_id, "failed", **args) == {"status": "settled"}
    changed = deepcopy(details)
    changed["elapsed_seconds"] = 600
    with pytest.raises(DomainError, match="SETTLEMENT_CONFLICT"):
        settle(call_id, "failed", **(args | {"failure_diagnostics": changed}))
    with SessionLocal() as db:
        call = db.get(CallDispatch, call_id)
        assert load(call.receipt_json)["failure_diagnostics"] == details
        assert call.usage_json is call.price_json is None


def test_empty_legacy_diagnostics_are_not_reconstructed_from_request(client):
    from app.db import SessionLocal
    from app.v2.runtime import load, settle, write
    from app.v2.storage import CallDispatch

    call_id = sent_call(client)
    settle(call_id, "unknown", failure_code="PROVIDER_TRANSPORT_UNKNOWN", failure_diagnostics={})
    with write() as db:
        call = db.get(CallDispatch, call_id)
        original = call.receipt_json
        call.request_json = json.dumps({"transport": {"read_timeout_seconds": 600}})
    with SessionLocal() as db:
        call = db.get(CallDispatch, call_id)
        assert call.receipt_json == original
        assert load(call.receipt_json)["failure_diagnostics"] == {}
        assert call.status == call.outcome == "unknown" and call.usage_json is None

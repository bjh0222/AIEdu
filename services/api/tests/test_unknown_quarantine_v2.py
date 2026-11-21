"""Offline exact-attempt isolation; no provider calls or live database access."""
from __future__ import annotations

from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post
from test_task_run_v2_guards import queued


def unknown_run(client):
    from app.v2.contracts import Pause, sha
    from app.v2.runtime import authorize_call, claim_root, mark_sent, settle
    from app.v2.tasks import pause, snapshot
    created, run = queued(client)
    run_id = run["run_id"]
    lease = claim_root(run_id, "worker")
    request_hash = sha("original-provider-request")
    call_id = authorize_call(run_id, "unit", request_hash, "input", lease["epoch"], "worker", str(uuid4()),
                             family="offline-fixture")
    mark_sent(call_id, "worker", lease["epoch"])
    settle(call_id, "unknown", failure_code="PROVIDER_TRANSPORT_UNKNOWN")
    snap = snapshot(created["task_id"])
    stopped = pause(run_id, Pause(expected_revision=snap["state_revision"]), str(uuid4()))
    return created, run_id, {"expected_revision": stopped["state_revision"],
        "checkpoint_hash": stopped["checkpoint_hash"],
        "calls": [{"call_id": call_id, "request_hash": request_hash}],
        "acknowledge_possible_duplicate_cost": True}


def dispatch_value(call_id):
    from app.db import SessionLocal
    from app.v2.storage import CallDispatch
    with SessionLocal() as db:
        row = db.get(CallDispatch, call_id)
        return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def test_isolation_api_preserves_bill_and_never_schedules(client, monkeypatch):
    from app.api import v2
    from app.db import SessionLocal
    from app.models import GenerationJob, ModelCallLog
    from app.v2.runtime import job, load
    created, run_id, payload = unknown_run(client)
    call_id = payload["calls"][0]["call_id"]
    before = dispatch_value(call_id)
    def forbidden(*args, **kwargs):
        pytest.fail("Isolation must never execute a run")
    monkeypatch.setattr(v2, "execute_run", forbidden)
    headers = owner(client)
    response = post(client, f"/runs/{run_id}/unknown-quarantines", payload, headers, "isolate")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["run_id"] == run_id
    assert result["scheduled"] is False and result["provider_settlement"] == "unresolved"
    assert dispatch_value(call_id) == before | {"status": "quarantined"}
    assert post(client, f"/runs/{run_id}/unknown-quarantines", payload, headers, "isolate").json() == result
    with SessionLocal() as db:
        assert db.query(GenerationJob).filter_by(goal_id=created["task_id"]).count() == 1
        log = db.query(ModelCallLog).filter_by(call_id=call_id).one()
        assert (log.status, log.outcome, log.usage_source) == ("quarantined", "unknown", "unknown")
        state = load(job(db, run_id).state_json)
        assert state["unknown_quarantines"][call_id]["scheduled"] is False
    receipt = client.get("/api/v2/actions/receipt", headers=headers | {"Idempotency-Key": "isolate"})
    assert receipt.json()["status"] == "completed"


@pytest.mark.parametrize("change,code", [
    ({"acknowledge_possible_duplicate_cost": False}, "UNKNOWN_QUARANTINE_REQUIRES_RISK_ACK"),
    ({"expected_revision": 0}, "REVISION_STALE"),
    ({"checkpoint_hash": "stale"}, "CHECKPOINT_STALE"),
    ({"calls": [{"call_id": str(uuid4()), "request_hash": "0"*64}]}, "CALL_NOT_FOUND"),
])
def test_isolation_rejects_unbound_or_unacknowledged_actions(client, change, code):
    from app.v2.contracts import DomainError, QuarantineUnknown
    from app.v2.tasks import quarantine_unknown
    _, run_id, payload = unknown_run(client)
    before = dispatch_value(payload["calls"][0]["call_id"])
    with pytest.raises(DomainError, match=code):
        quarantine_unknown(run_id, QuarantineUnknown(**(payload | change)), str(uuid4()))
    assert dispatch_value(payload["calls"][0]["call_id"]) == before


def test_exact_request_hash_and_atomic_call_set(client):
    from app.v2.contracts import DomainError, QuarantineUnknown
    from app.v2.tasks import quarantine_unknown
    _, run_id, payload = unknown_run(client)
    before = dispatch_value(payload["calls"][0]["call_id"])
    for entries, code in [
        ([payload["calls"][0] | {"request_hash": "0"*64}], "CALL_REQUEST_MISMATCH"),
        (payload["calls"] * 2, "DUPLICATE_CALL_ID"),
        (payload["calls"] + [{"call_id": str(uuid4()), "request_hash": "0"*64}], "CALL_NOT_FOUND"),
    ]:
        with pytest.raises(DomainError, match=code):
            quarantine_unknown(run_id, QuarantineUnknown(**(payload | {"calls": entries})), str(uuid4()))
        assert dispatch_value(payload["calls"][0]["call_id"]) == before


def test_foreign_run_and_non_unknown_are_not_isolated(client):
    from app.v2.contracts import DomainError, Pause, QuarantineUnknown
    from app.v2.runtime import job, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import quarantine_unknown
    _, run_id, payload = unknown_run(client)
    from app.v2.tasks import pause
    _, other_run = queued(client)
    other = other_run["run_id"]
    other_pause = pause(other, Pause(expected_revision=0), "pause-other")
    with pytest.raises(DomainError, match="CALL_NOT_FOUND"):
        quarantine_unknown(other, QuarantineUnknown(**(payload | {
            "expected_revision": other_pause["state_revision"],
            "checkpoint_hash": other_pause["checkpoint_hash"]})), "foreign")
    with write() as db:
        db.get(CallDispatch, payload["calls"][0]["call_id"]).status = "sent"
        job(db, run_id).status = "paused"
    with pytest.raises(DomainError, match="CALL_NOT_UNKNOWN"):
        quarantine_unknown(run_id, QuarantineUnknown(**payload), "sent")


def test_failed_run_can_explicitly_isolate_receiptless_sent_attempt(client):
    """A worker-aborted sent request must have a product isolation exit."""
    from app.v2.contracts import QuarantineUnknown
    from app.v2.runtime import job, load, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import quarantine_unknown

    _, run_id, payload = unknown_run(client)
    call_id = payload["calls"][0]["call_id"]
    with write() as db:
        job(db, run_id).status = "failed"
        call = db.get(CallDispatch, call_id)
        call.status, call.outcome, call.receipt_json, call.usage_json = "sent", "", "{}", None

    result = quarantine_unknown(run_id, QuarantineUnknown(**payload), "failed-sent-isolate")
    assert result["scheduled"] is False and result["provider_settlement"] == "unresolved"
    with write() as db:
        call = db.get(CallDispatch, call_id)
        assert call.status == "quarantined" and call.outcome == "unknown"
        receipt = load(call.receipt_json)
        assert receipt["failure_code"] == "PROVIDER_TRANSPORT_UNKNOWN"
        assert receipt["failure_diagnostics"]["validation_reason"] == "WORKER_TERMINATED_WITHOUT_RECEIPT"
        assert call.usage_json is None and call.price_json is None


def test_failed_run_sent_attempt_with_receipt_is_not_rewritten(client):
    from app.v2.contracts import DomainError, QuarantineUnknown
    from app.v2.runtime import job, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import quarantine_unknown

    _, run_id, payload = unknown_run(client)
    call_id = payload["calls"][0]["call_id"]
    with write() as db:
        job(db, run_id).status = "failed"
        call = db.get(CallDispatch, call_id)
        call.status, call.outcome, call.receipt_json = "sent", "", '{"provider_request_id":"late"}'
    with pytest.raises(DomainError, match="CALL_SENT_RECEIPT_PRESENT"):
        quarantine_unknown(run_id, QuarantineUnknown(**payload), "failed-sent-receipt")
    with write() as db:
        call = db.get(CallDispatch, call_id)
        assert call.status == "sent" and call.receipt_json == '{"provider_request_id":"late"}'


def test_active_run_and_active_sibling_rejected(client):
    from app.models import GenerationJob
    from app.v2.contracts import DomainError, QuarantineUnknown
    from app.v2.runtime import job, write
    from app.v2.tasks import quarantine_unknown
    created, run_id, payload = unknown_run(client)
    with write() as db:
        job(db, run_id).status = "running"
    with pytest.raises(DomainError, match="UNKNOWN_QUARANTINE_REQUIRES_STOPPED_RUN"):
        quarantine_unknown(run_id, QuarantineUnknown(**payload), "active")
    with write() as db:
        job(db, run_id).status = "paused"
        db.add(GenerationJob(id=str(uuid4()), run_id=str(uuid4()), root_job_id="sibling",
            goal_id=created["task_id"], job_type="task_v2", status="queued"))
    with pytest.raises(DomainError, match="ACTIVE_RUN"):
        quarantine_unknown(run_id, QuarantineUnknown(**payload), "sibling")


def test_separate_resume_and_late_provider_receipt_remain_honest(client):
    from app.v2.contracts import QuarantineUnknown, Resume
    from app.v2.runtime import settle
    from app.v2.tasks import quarantine_unknown, resume, snapshot
    created, run_id, payload = unknown_run(client)
    isolated = quarantine_unknown(run_id, QuarantineUnknown(**payload), "isolate")
    resumed = resume(run_id, Resume(expected_revision=isolated["state_revision"],
        checkpoint_hash=isolated["checkpoint_hash"]), "explicit-resume")
    assert resumed["parent_run_id"] == run_id and resumed["run_id"] != run_id
    call_id = payload["calls"][0]["call_id"]
    assert settle(call_id, "success", {"total_tokens": 3}, "late-provider-id")["status"] == "late"
    snap = snapshot(created["task_id"], resumed["run_id"])
    assert snap["cost_final"] is False  # provider usage arrived, but no price.
    assert snap["calls"][0]["provider_request_id"] == "late-provider-id"


def test_quarantine_api_requires_owner_and_csrf(client):
    _, run_id, payload = unknown_run(client)
    client.cookies.clear()
    assert client.post(f"/api/v2/runs/{run_id}/unknown-quarantines", json=payload,
        headers={"Origin": "http://127.0.0.1:3001"}).status_code == 401
    owner(client)
    assert client.post(f"/api/v2/runs/{run_id}/unknown-quarantines", json=payload).status_code == 403

"""Global queue activity is scoped to still-active roots."""
from __future__ import annotations

import time
from uuid import uuid4

import pytest

from test_task_run_v2_guards import queued


def unknown_call(client, *, unit_id="unit"):
    from app.v2.runtime import (
        authorize_call,
        claim_root,
        mark_sent,
        reconcile_stale_jobs,
        settle,
        write,
    )
    from app.v2.contracts import sha
    created, run = queued(client)
    with write() as db:
        reconcile_stale_jobs(db, now=time.time() + 10**6)
    lease = claim_root(run["run_id"], "worker")
    call_id = authorize_call(
        run["run_id"], unit_id, sha("request"), "input", lease["epoch"],
        "worker", str(uuid4()),
    )
    mark_sent(call_id, "worker", lease["epoch"])
    settle(call_id, "unknown", failure_code="PROVIDER_TRANSPORT_UNKNOWN")
    return created, run, call_id


def set_job_status(run_id, status):
    from app.v2.runtime import job, write
    with write() as db:
        job(db, run_id).status = status


@pytest.mark.parametrize("job_status,active", [
    ("queued", True),
    ("running", True),
    ("failed", False),
    ("paused", False),
    ("interrupted", False),
])
def test_active_provider_work_uses_root_status(client, job_status, active):
    from app.db import SessionLocal
    from app.v2.runtime import active_provider_work
    _, run, call_id = unknown_call(client)
    set_job_status(run["run_id"], job_status)
    with SessionLocal() as db:
        rows = active_provider_work(db)
    assert ([row.call_id for row in rows] == [call_id]) is active


def test_inactive_unknown_does_not_block_same_unit_in_new_root(client):
    from app.v2.runtime import authorize_call, claim_root
    first_task, first_run, _ = unknown_call(client, unit_id="same-unit")
    set_job_status(first_run["run_id"], "failed")
    _, second_run = queued(client)
    lease = claim_root(second_run["run_id"], "new-worker")
    call_id = authorize_call(
        second_run["run_id"], "same-unit", "b" * 64, "new-input",
        lease["epoch"], "new-worker", str(uuid4()),
    )
    assert call_id
    assert first_task["task_id"] != second_run["run_id"]


def test_active_unknown_still_blocks_new_work_on_same_unit(client):
    from app.v2.contracts import DomainError
    from app.v2.runtime import authorize_call
    _, run, _ = unknown_call(client, unit_id="same-unit")
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        authorize_call(
            run["run_id"], "same-unit", "c" * 64, "input", 1,
            "worker", str(uuid4()),
        )


def test_dispatcher_uses_active_provider_predicate(client, monkeypatch):
    from app.v2 import worker
    first_task, first_run, _ = unknown_call(client)
    second_task, second_run = queued(client)
    executed = []
    monkeypatch.setattr(worker, "execute_run", lambda run_id: executed.append(run_id))

    set_job_status(first_run["run_id"], "failed")
    assert worker.dispatch_once() == second_run["run_id"]
    assert executed == [second_run["run_id"]]

    set_job_status(first_run["run_id"], "running")
    executed.clear()
    assert worker.dispatch_once() is None
    assert executed == []


def test_stale_stop_releases_authorized_but_preserves_sent(client):
    from app.v2.runtime import (
        authorize_call,
        claim_root,
        dump,
        job,
        load,
        mark_sent,
        reconcile_stale_jobs,
        write,
    )
    from app.v2.storage import CallDispatch

    _, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state.setdefault("execution_policy", {})["provider_concurrency"] = 2
        obj.state_json = dump(state)
    authorized = authorize_call(
        run["run_id"], "authorized-unit", "a", "i", lease["epoch"],
        "worker", str(uuid4()),
    )
    sent = authorize_call(
        run["run_id"], "sent-unit", "s", "i", lease["epoch"],
        "worker", str(uuid4()),
    )
    mark_sent(sent, "worker", lease["epoch"])
    with write() as db:
        released = reconcile_stale_jobs(db, now=time.time() + 120)
    assert released[0]["preserved_sent_call_ids"] == [sent]
    with write() as db:
        assert db.get(CallDispatch, authorized).status == "not_sent"
        assert db.get(CallDispatch, sent).status == "sent"
        assert job(db, run["run_id"]).status == "interrupted"


def test_unknown_snapshot_exposes_six_hour_warning_without_automatic_action(client):
    from app.v2.runtime import write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import snapshot

    created, _, call_id = unknown_call(client)
    with write() as db:
        db.get(CallDispatch, call_id).started_at = time.time() - 6 * 3600 - 1
    row = next(call for call in snapshot(created["task_id"])["calls"]
               if call["call_id"] == call_id)
    assert row["status"] == "unknown"
    assert row["unknown_age_warning"] is True
    assert row["unknown_age_seconds"] > 6 * 3600

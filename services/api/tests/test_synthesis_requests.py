"""Pending router/service seam: isolated DB, no workers or provider requests."""
from copy import deepcopy
from uuid import uuid4

import pytest

from app.api.v2_synthesis import SynthesisRequest
from app.v2.contracts import DomainError
from app.v2 import synthesis_requests as requests
from test_v2_optimization import heading_task


def payload_from_preview(value):
    return SynthesisRequest(run_id=value["run_id"], expected_run_revision=value["state_revision"],
        version_id=value["version_id"], content_hash=value["content_hash"], quality_hash=value["quality_hash"],
        source_bundle_hash=value["source_bundle_hash"], expected_synthesis_revision=value["expected_synthesis_revision"])


def enable_registration_fixture(monkeypatch):
    from app.config import settings
    from app.v2 import worker
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(worker, "SYNTHESIS_RUNTIME_VERSION", requests.RUNTIME_VERSION, raising=False)


def test_unregistered_worker_cannot_enqueue_even_with_paid_permission(client, monkeypatch):
    from app.config import settings
    from app.v2 import worker
    _, snap = heading_task(client)
    payload = payload_from_preview(requests.preview(snap["task_id"], snap["run_id"], snap["version_id"]))
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.delattr(worker, "SYNTHESIS_RUNTIME_VERSION")
    with pytest.raises(DomainError, match="SYNTHESIS_RUNTIME_NOT_REGISTERED"):
        requests.request(snap["task_id"], payload, str(uuid4()))


def test_latest_authorized_ancestry_budget_and_source_version_remain_distinct(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.runtime import dump, job, load
    from app.v2.storage import CallDispatch
    _, snap = heading_task(client)
    with SessionLocal() as db:
        source = job(db, snap["run_id"])
        latest_state = deepcopy(load(source.state_json))
        latest_state["budget"].update(calls=None, calls_amended=True)
        latest_state["mode"] = "configured"  # source version is still the original demo
        latest_state["optimization_request"] = {"historical": "not this operation"}
        latest_state["optimization_policy"] = {"preserved_authorization": True}
        latest_id, latest_run = str(uuid4()), str(uuid4())
        db.add(GenerationJob(id=latest_id, root_job_id=latest_id, parent_job_id=source.id,
            run_id=latest_run, goal_id=source.goal_id, job_type="task_v2", status="completed",
            state_json=dump(latest_state), created_at="2999-01-01T00:00:00"))
        db.commit()
        calls_before = db.query(CallDispatch).count()
    enable_registration_fixture(monkeypatch)
    stale = payload_from_preview(requests.preview(snap["task_id"], snap["run_id"], snap["version_id"]))
    with pytest.raises(DomainError, match="SYNTHESIS_PARENT_RUN_STALE"):
        requests.request(snap["task_id"], stale, str(uuid4()))
    payload = payload_from_preview(requests.preview(snap["task_id"], latest_run, snap["version_id"]))
    key = str(uuid4())
    result = requests.request(snap["task_id"], payload, key)
    assert requests.request(snap["task_id"], payload, key) == result
    with SessionLocal() as db:
        child = job(db, result["run_id"])
        frozen = load(child.state_json)
        assert child.parent_job_id == latest_id and result["parent_run_id"] == latest_run
        assert frozen["budget"] == latest_state["budget"]
        assert frozen["optimization_policy"] == latest_state["optimization_policy"]
        assert "optimization_request" not in frozen
        assert frozen["synthesis_source"]["version_id"] == snap["version_id"]
        assert frozen["synthesis_source"]["source_demo"] is True
        assert db.query(GenerationJob).filter_by(parent_job_id=latest_id).count() == 1
        assert db.query(CallDispatch).count() == calls_before


@pytest.mark.parametrize("field,value,code", [
    ("content_hash", "0"*64, "SYNTHESIS_SOURCE_STALE"),
    ("quality_hash", "0"*64, "SYNTHESIS_SOURCE_STALE"),
    ("source_bundle_hash", "0"*64, "SYNTHESIS_SOURCE_STALE"),
    ("expected_run_revision", 99999, "REVISION_STALE"),
    ("expected_synthesis_revision", 99999, "SYNTHESIS_REVISION_STALE"),
])
def test_CAS_and_both_source_hashes_reject_without_child_or_call(client, monkeypatch, field, value, code):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.storage import CallDispatch
    _, snap = heading_task(client)
    payload = payload_from_preview(requests.preview(snap["task_id"], snap["run_id"], snap["version_id"]))
    payload = payload.model_copy(update={field: value})
    enable_registration_fixture(monkeypatch)
    with SessionLocal() as db:
        before = (db.query(GenerationJob).count(), db.query(CallDispatch).count())
    with pytest.raises(DomainError, match=code):
        requests.request(snap["task_id"], payload, str(uuid4()))
    with SessionLocal() as db:
        assert (db.query(GenerationJob).count(), db.query(CallDispatch).count()) == before


def test_stale_immutable_result_is_not_rendered_as_current_integration(client):
    from app.v2.insights import save
    from app.v2.runtime import write
    _, snap = heading_task(client)
    assert requests.read(snap["task_id"], snap["version_id"])["status"] == "not_generated"
    with write() as db:
        save(db, "synthesis-head:" + snap["version_id"], {"record_key": "missing", "result_hash": "wrong", "revision": 1})
    with pytest.raises(DomainError, match="SYNTHESIS_RECORD_STALE"):
        requests.read(snap["task_id"], snap["version_id"])

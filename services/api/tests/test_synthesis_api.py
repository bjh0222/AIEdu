"""Test the pending router independently of real registration and providers."""
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v2_synthesis import router
from app.main import v2_error
from app.v2.contracts import DomainError
from app.v2 import synthesis_requests
from test_synthesis_requests import enable_registration_fixture, payload_from_preview
from test_v2_optimization import heading_task


@pytest.fixture
def routed(client):
    # The ordinary client owns only the isolated conftest DB. This app exposes
    # the new router without registering it in the running product entrypoint.
    app = FastAPI()
    app.add_exception_handler(DomainError, v2_error)
    app.include_router(router, prefix="/api")
    with TestClient(app) as local:
        yield local


def setup_source(client, routed):
    headers, snap = heading_task(client)
    routed.cookies.update(client.cookies)
    value = synthesis_requests.preview(snap["task_id"], snap["run_id"], snap["version_id"])
    return headers, snap, payload_from_preview(value).model_dump()


def test_source_and_result_reads_require_owner(client, routed):
    _, snap, _ = setup_source(client, routed)
    routed.cookies.clear()
    base = f"/api/v2/tasks/{snap['task_id']}"
    assert routed.get(base + "/synthesis-source", params={"run_id": snap["run_id"], "version_id": snap["version_id"]}).status_code == 401
    assert routed.get(base + f"/versions/{snap['version_id']}/synthesis").status_code == 401


def test_preview_does_not_generate_and_missing_is_honest(client, routed):
    _, snap, _ = setup_source(client, routed)
    preview = routed.get(f"/api/v2/tasks/{snap['task_id']}/synthesis-source",
        params={"run_id": snap["run_id"], "version_id": snap["version_id"]})
    assert preview.status_code == 200 and preview.json()["status"] == "source_ready_not_executed"
    missing = routed.get(f"/api/v2/tasks/{snap['task_id']}/versions/{snap['version_id']}/synthesis")
    assert missing.status_code == 200 and missing.json()["status"] == "not_generated"
    assert missing.json()["source_is_current"] is True


@pytest.mark.parametrize("mutation,code", [("csrf", "CSRF_DENIED"), ("origin", "ORIGIN_DENIED"),
    ("key", "IDEMPOTENCY_KEY_REQUIRED"), ("owner", "SESSION_REQUIRED")])
def test_write_boundaries_precede_queue_or_provider(client, routed, mutation, code, monkeypatch):
    headers, snap, payload = setup_source(client, routed)
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    if mutation == "csrf":
        headers.pop("X-CSRF-Token", None)
    elif mutation == "origin":
        headers["Origin"] = "https://evil.test"
    elif mutation == "key":
        headers.pop("Idempotency-Key")
    else:
        routed.cookies.clear()
    monkeypatch.setattr(synthesis_requests, "request", lambda *a: pytest.fail("No queue outside owner boundary"))
    response = routed.post(f"/api/v2/tasks/{snap['task_id']}/syntheses", json=payload, headers=headers)
    assert response.status_code in {401, 403, 422} and response.json()["code"] == code


def test_paid_explicit_request_is_idempotent_and_schedules_only_same_run(client, routed, monkeypatch):
    from app.v2 import worker
    headers, snap, payload = setup_source(client, routed)
    enable_registration_fixture(monkeypatch)
    dispatched = []
    monkeypatch.setattr(worker, "execute_run", dispatched.append)
    headers = {**headers, "Idempotency-Key": str(uuid4())}
    url = f"/api/v2/tasks/{snap['task_id']}/syntheses"
    first = routed.post(url, json=payload, headers=headers)
    assert first.status_code == 202, first.text
    second = routed.post(url, json=payload, headers=headers)
    assert second.status_code == 202 and second.json() == first.json()
    assert dispatched == [first.json()["run_id"]] * 2
    assert first.json()["parent_run_id"] == snap["run_id"]
    # Queue identity is singular; actual duplicate background invocations are
    # fenced by the existing claim_root contract, never new paid authorizations.
    conflict = routed.post(url, json={**payload, "expected_run_revision": 9999}, headers=headers)
    assert conflict.status_code == 409 and conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_source_from_another_task_is_not_readable(client, routed):
    _, snap, _ = setup_source(client, routed)
    response = routed.get(f"/api/v2/tasks/{uuid4()}/versions/{snap['version_id']}/synthesis")
    assert response.status_code == 404

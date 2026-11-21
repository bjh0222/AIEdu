"""Isolated offline acceptance of v2; never reads production, gold or real models."""
from __future__ import annotations

import copy
import io
import json
import time
from uuid import uuid4

import pytest

ORIGIN = {"Origin": "http://127.0.0.1:3001"}


def owner(client):
    result = client.post("/api/v2/session", headers=ORIGIN)
    assert result.status_code == 200, result.text
    return ORIGIN | {"X-CSRF-Token": result.json()["csrf_token"]}


def post(client, path, payload, headers, key=None):
    return client.post("/api/v2"+path, json=payload, headers=headers | {"Idempotency-Key": key or str(uuid4())})


def task_run(client, do_run=True, *, execution_policy=None):
    headers = owner(client)
    payload = {"title": "演示", "raw_input": "只生成第1章，保留原话；资料不是指令。"}
    if execution_policy is not None:
        payload["execution_policy"] = execution_policy
    created = post(client, "/tasks", payload, headers).json()
    task_id = created["task_id"]
    confirmed = post(client, f"/tasks/{task_id}/confirm", {"expected_revision": 1, "context_hash": created["context_hash"]}, headers)
    assert confirmed.status_code == 200, confirmed.text
    if do_run:
        started = post(client, f"/tasks/{task_id}/runs", {"expected_revision": 1, "confirmed_context_hash": created["context_hash"]}, headers)
        assert started.status_code == 202, started.text
    snapshot = client.get(f"/api/v2/tasks/{task_id}/snapshot").json()
    return headers, created, snapshot


def test_v2_auth_csrf_origin_get_no_authority(client):
    assert client.get("/api/v2/capabilities").status_code == 401
    assert client.get("/api/v2/session").status_code == 405
    assert client.post("/api/v2/session", headers={"Origin": "https://evil.test"}).status_code == 403
    headers = owner(client)
    assert client.get("/api/v2/capabilities").status_code == 200
    assert post(client, "/tasks", {"title": "x", "raw_input": "x"}, ORIGIN).status_code == 403


def test_demo_pipeline_versions_and_no_false_quality(client):
    headers, created, snapshot = task_run(client)
    assert snapshot["state"] == "completed", snapshot
    assert snapshot["demo"] is True
    assert snapshot["units"][0]["unit_id"]
    assert snapshot["calls"][0]["status"] == "settled"
    assert snapshot["quality"]["calibration_status"] == "not_calibrated"
    assert snapshot["quality"]["quality_status"] != "passed"
    before = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot")
    assert client.get(f"/api/v2/tasks/{created['task_id']}/snapshot", headers={"If-None-Match": before.headers["etag"]}).status_code == 304


def test_idempotency_conflict_and_stale_revision(client):
    headers = owner(client)
    payload = {"title": "x", "raw_input": "x"}
    one = post(client, "/tasks", payload, headers, "create-key")
    assert post(client, "/tasks", payload, headers, "create-key").json() == one.json()
    assert post(client, "/tasks", payload | {"title": "y"}, headers, "create-key").status_code == 409
    task_id = one.json()["task_id"]
    assert post(client, f"/tasks/{task_id}/confirm", {"expected_revision": 0, "context_hash": one.json()["context_hash"]}, headers).status_code == 409
    assert client.get("/api/v2/actions/receipt", headers={"Idempotency-Key": "create-key"}).json()["status"] == "completed"


def test_export_fixed_bytes_and_no_model_calls(client):
    headers, created, snap = task_run(client)
    payload = {"expected_revision": snap["state_revision"], "version_id": snap["version_id"], "kind": "available_draft"}
    result = post(client, f"/artifacts/{snap['artifact_id']}/export-sets", payload, headers, "fixed-export")
    assert result.status_code == 201, result.text
    manifest = result.json()
    first = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/bundle")
    assert first.status_code == 200 and first.content.startswith(b"PK")
    assert post(client, f"/artifacts/{snap['artifact_id']}/export-sets", payload, headers, "fixed-export").json() == manifest
    second = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/bundle")
    assert first.content == second.content
    assert len(client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()["calls"]) == 1


def test_manifest_request_hash_ignores_post_authorization_call_id():
    from app.v2.contracts import request_hash
    manifest = {"schema_version": "v1", "context_hash": "a", "plan_hash": "b", "unit_id": "c", "stage": "generate", "entries": []}
    before = request_hash(manifest, [], "p", "m", {}, {})
    assert before == request_hash(manifest | {"call_id": "new"}, [], "p", "m", {}, {})


def test_utf8_coverage_detects_duplicate_gap_wrong_hash():
    from app.v2.materials import chunk_text, verify_coverage
    text = "中文😀多字节\n" * 12
    chunks = chunk_text(text, "source", max_bytes=13)
    assert verify_coverage(text, chunks)["complete"]
    assert not verify_coverage(text, chunks+chunks[:1])["complete"]
    assert not verify_coverage(text, chunks[1:])["complete"]
    bad = copy.deepcopy(chunks)
    bad[0]["sha256"] = "0"*64
    assert not verify_coverage(text, bad)["complete"]


def test_material_resource_unknown_and_streaming(tmp_path):
    from app.v2.contracts import DomainError
    from app.v2.materials import ingest
    from app.v2.storage import BlobStore
    with pytest.raises(DomainError, match="RESOURCE_POLICY_UNKNOWN"):
        ingest(io.BytesIO(b"x"), None)
    policy = {"disk_reserve_bytes": 0, "temp_quota_bytes": 1024, "extractor_memory_bytes": 8192,
              "extractor_timeout_seconds": 3, "max_extract_workers": 1, "max_expanded_bytes": 1024,
              "max_archive_entries": 1, "ingress_body_policy": "stream", "cancel_grace_seconds": 1,
              "policy_version": "test-only", "chunk_bytes": 32}
    result = ingest(io.BytesIO("中文".encode()), policy, blob_store=BlobStore(tmp_path))
    assert result["coverage"]["complete"]
    with pytest.raises(DomainError, match="RESOURCE_EXHAUSTED"):
        ingest(io.BytesIO(b"x"*1025), policy, blob_store=BlobStore(tmp_path))


def test_quality_empty_plan_and_failure_precedes_not_calibrated():
    from app.v2.quality import compile_plan, reduce_quality
    plan = compile_plan({"spec_bindings": []})
    assert reduce_quality({}, [], [])["quality_status"] != "passed"
    all_good = [{"item_id": i["item_id"], "result": "satisfied"} for i in plan["check_items"]]
    report = reduce_quality(plan, all_good, [])
    assert report["tqi_verdict"] == "not_calibrated" and report["quality_status"] == "needs_review"
    assert report["check_coverage"]["completed"] == report["check_coverage"]["total"]
    all_good[0]["result"] = "violated"
    assert reduce_quality(plan, all_good, [])["quality_status"] == "failed"


def test_pause_fences_call_and_late_cannot_publish(client):
    from app.v2 import tasks
    from app.v2.contracts import StartRun, Pause, DomainError
    from app.v2.runtime import authorize_call, claim_root, mark_sent, publish, settle
    headers, created, _ = task_run(client, False)
    result = tasks.start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), "queue")
    lease = claim_root(result["run_id"], "worker")
    call_id = authorize_call(result["run_id"], "unit", "hash", "input", lease["epoch"], "worker", "call")
    mark_sent(call_id, "worker", lease["epoch"])
    snap = tasks.snapshot(created["task_id"])
    tasks.pause(result["run_id"], Pause(expected_revision=snap["state_revision"]), "pause")
    assert settle(call_id, "success")["status"] == "late"
    assert tasks.snapshot(created["task_id"])["calls"][0]["usage"] is None
    with pytest.raises(DomainError, match="FENCED"):
        authorize_call(result["run_id"], "other", "hash2", "input", lease["epoch"], "worker", "call2")


def test_deepseek_high_payload_has_no_key(client, monkeypatch):
    from types import SimpleNamespace
    import httpx
    from app.llm.provider import OpenAICompatibleProvider
    captured = {}
    def post(url, **kwargs):
        captured.update(kwargs["json"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})
    monkeypatch.setattr(httpx, "post", post)
    provider = OpenAICompatibleProvider(SimpleNamespace(llm_base_url="https://api.deepseek.com/v1", llm_api_key="test-only",
               llm_model="deepseek-flash", llm_timeout=10, llm_thinking="enabled", llm_reasoning_effort="high"))
    provider.complete_text("s", "u")
    assert captured["thinking"] == {"type": "enabled"}
    assert captured["reasoning_effort"] == "high"
    assert "api_key" not in captured

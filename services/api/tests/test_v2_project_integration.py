"""Public frontend/backend seams. Synthetic sources only; no paid inference."""
from urllib.parse import quote
from uuid import uuid4

from test_task_run_v2 import owner, post, task_run


def test_versions_catalog_is_scoped_paginated_and_read_only(client):
    headers = owner(client)
    created = post(client, "/tasks", {"title": "版本目录", "raw_input": "教程及速查",
        "requested_artifacts": ["tutorial", "cheatsheet"]}, headers).json()
    post(client, f"/tasks/{created['task_id']}/confirm", {"expected_revision": 1, "context_hash": created["context_hash"]}, headers)
    post(client, f"/tasks/{created['task_id']}/runs", {"expected_revision": 1, "confirmed_context_hash": created["context_hash"]}, headers)
    snap = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    _, other, _ = task_run(client, False)
    url = f"/api/v2/tasks/{created['task_id']}/versions"
    params = {"artifact_id": snap["artifact_id"], "limit": 1}
    page = client.get(url, params=params)
    assert page.status_code == 200, page.text
    first = page.json()
    assert first["items"][0]["version_id"] == snap["version_id"]
    assert first["next_before_version"] is not None
    second = client.get(url, params=params | {"before_version": first["next_before_version"]}).json()
    assert second["items"][0]["version"] < first["items"][0]["version"]
    assert client.get(f"/api/v2/tasks/{other['task_id']}/versions", params=params).status_code == 404
    assert client.get(url, params=params | {"limit": 201}).status_code == 422
    after = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    assert after == snap
    listing = client.get("/api/v2/tasks").json()["items"]
    assert next(t for t in listing if t["task_id"] == created["task_id"])["demo"] is True
    assert next(t for t in listing if t["task_id"] == other["task_id"])["demo"] is None


def test_resource_readiness_does_not_leak_private_policy(client, monkeypatch):
    from app.v2 import materials
    owner(client)
    monkeypatch.setattr(materials, "resource_policy", lambda: None)
    data = client.get("/api/v2/capabilities").json()["materials"]
    assert data["resource_policy_status"] == "unavailable"
    assert data["resource_policy_reason"] == "RESOURCE_POLICY_UNKNOWN"
    monkeypatch.setattr(materials, "resource_policy", lambda: {"private_path": "never-return"})
    assert "never-return" not in client.get("/api/v2/capabilities").text


def test_unicode_material_filename_and_full_chain(client, monkeypatch):
    from app.v2 import materials
    from app.v2.contracts import sha
    policy = dict(disk_reserve_bytes=0, temp_quota_bytes=1048576, extractor_memory_bytes=8388608,
                  extractor_timeout_seconds=30, max_extract_workers=1, max_expanded_bytes=1048576,
                  max_archive_entries=100, ingress_body_policy="stream", cancel_grace_seconds=1,
                  policy_version="integration.synthetic.v1", chunk_bytes=4096)
    monkeypatch.setattr(materials, "resource_policy", lambda: policy)
    headers = owner(client)
    assert client.get("/api/v2/capabilities").json()["materials"]["resource_policy_status"] == "ready"
    body = {"title": "中文材料", "raw_input": "根据唯一材料生成练习", "domain_key": "math",
            "spec_bindings": [{"scene": "exercise", "artifact_role": "exercise"}]}
    created = post(client, "/tasks", body, headers).json()
    tid = created["task_id"]
    raw = "# 合成材料\n线性方程：x+1=2。".encode("utf-8")
    upload_headers = headers | {"Idempotency-Key": str(uuid4()), "X-Expected-Revision": "1",
                                "X-Filename-UTF8": quote("基础代数一 (1).md")}
    result = client.post(f"/api/v2/tasks/{tid}/materials", content=raw, headers=upload_headers)
    assert result.status_code == 201, result.text
    source = result.json()
    assert source["raw_hash"] == sha(raw)
    repeated = client.post(f"/api/v2/tasks/{tid}/materials", content=raw, headers=upload_headers)
    assert repeated.json() == source
    bad = client.post(f"/api/v2/tasks/{tid}/materials", content=raw,
                      headers=upload_headers | {"X-Filename-UTF8": quote("../逃逸.md")})
    assert bad.status_code == 422
    patched = client.patch(f"/api/v2/tasks/{tid}/input", json={"expected_revision": 1,
        "input": body | {"material_ids": [source["source_id"]]}},
        headers=headers | {"Idempotency-Key": str(uuid4())}).json()
    confirmed = post(client, f"/tasks/{tid}/confirm", {"expected_revision": 2, "context_hash": patched["context_hash"]}, headers)
    assert confirmed.status_code == 200
    started = post(client, f"/tasks/{tid}/runs", {"expected_revision": 2,
        "confirmed_context_hash": patched["context_hash"], "mode": "demo"}, headers)
    assert started.status_code == 202, started.text
    snap = client.get(f"/api/v2/tasks/{tid}/snapshot").json()
    assert snap["state"] == "completed" and snap["demo"] is True
    assert snap["resolved_task"]["material_ids"] == [source["source_id"]]

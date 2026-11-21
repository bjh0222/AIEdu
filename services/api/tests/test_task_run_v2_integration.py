"""Mocked provider integration; no external inference or production assets."""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_task_run_v2 import ORIGIN, owner, post, task_run
from test_task_run_v2_guards import queued


def test_obisidian_and_credential_origins_cannot_mint_owner(client):
    for origin in ("app://obsidian.md", "capacitor://localhost", "http://127.0.0.1:3001/", "http://user@localhost:3001"):
        assert client.post("/api/v2/session", headers={"Origin": origin}).status_code == 403


def test_legacy_routes_quarantined_outside_regression_mode(client, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "enable_legacy_api", False)
    assert client.post("/api/goals", json={"title": "bypass"}).status_code == 410
    assert client.get("/api/training/export").status_code == 410
    monkeypatch.setattr(settings, "enable_legacy_api", True)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    assert client.post("/api/goals", json={"title": "bypass"}).status_code == 410


def test_publish_idempotency_compares_content_quality_and_base(client):
    from app.v2.contracts import DomainError, sha
    from app.v2.runtime import claim_root, publish
    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    units = [{"unit_id": str(uuid4()), "body": "original", "content_hash": sha("original")}]
    report = {"quality_status": "needs_review", "_version_id": str(uuid4())}
    result = publish(run["run_id"], "worker", lease["epoch"], units, report, "publication")
    assert publish(run["run_id"], "worker", lease["epoch"], units, report, "publication") == result
    assert "_version_id" in report  # publication never mutates the caller's report
    with pytest.raises(DomainError, match="IDEMPOTENCY_CONFLICT"):
        publish(run["run_id"], "worker", lease["epoch"], units, report | {"quality_status": "passed"}, "publication")


def test_partial_plan_and_missing_injection_never_satisfy_coverage():
    from app.v2.quality import check_snapshot, compile_plan
    from app.v2.contracts import sha
    state = {"plan": {"units": [{"unit_id": "a", "material_chunk_ids": ["chunk"]}, {"unit_id": "b"}]},
             "check_plan": compile_plan({})}
    report = check_snapshot(state, [{"unit_id": "a", "body": "content", "content_hash":sha("content")}], "v")
    results = {c["item_id"]: c["result"] for c in report["checks"]}
    assert results["C1.coverage"] == "violated"
    assert results["C5.materials"] == "violated"


def test_bounded_task_list_cursor_and_query_binding(client):
    from app.v2.contracts import DomainError, TaskInput
    from app.v2.tasks import create_task, list_tasks
    for i in range(12):
        create_task(TaskInput(title=f"find {i}", raw_input="x"), str(uuid4()))
    one = list_tasks(limit=5, search="find")
    two = list_tasks(cursor=one["next_cursor"], limit=5, search="find")
    three = list_tasks(cursor=two["next_cursor"], limit=5, search="find")
    ids = [x["task_id"] for page in (one, two, three) for x in page["items"]]
    assert len(ids) == len(set(ids)) == 12
    assert three["next_cursor"] is None
    with pytest.raises(DomainError, match="INVALID_CURSOR"):
        list_tasks(cursor=one["next_cursor"], search="different")


def test_question_answer_same_call_and_export_projection(client):
    headers = owner(client)
    payload = {"title": "同题投影", "raw_input": "演示练习", "requested_artifacts": ["exercise", "answer"],
               "spec_bindings": [{"scene": "exercise", "artifact_role": "exercise"}]}
    created = post(client, "/tasks", payload, headers).json()
    post(client, f"/tasks/{created['task_id']}/confirm", {"expected_revision": 1, "context_hash": created["context_hash"]}, headers)
    post(client, f"/tasks/{created['task_id']}/runs", {"expected_revision": 1, "confirmed_context_hash": created["context_hash"]}, headers)
    snap = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    assert snap["state"] == "completed", snap
    assert len(snap["calls"]) == len(snap["units"]) == 1
    assert snap["units"][0]["items"][0]["answer"]["final"] == "2"
    export = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                  {"expected_revision": snap["state_revision"], "version_id": snap["version_id"],
                   "kind": "available_draft"}, headers).json()
    files = {f.get("artifact_role"): f for f in export["files"] if "artifact_role" in f}
    assert set(files) == {"document", "answer"}
    assert files["document"]["file_id"] == "main"
    assert files["answer"]["file_id"] == "answers"
    assert files["document"]["file_id"] != files["answer"]["file_id"]


def test_exercise_scores_rejected_and_paper_totals_derived():
    from app.v2.contracts import DomainError
    from app.v2.items import normalize_items, render_items
    item = {"stem_markdown": "题面", "answer": {"final": "答案", "reasoning": "步骤"}, "score": 2.5}
    with pytest.raises(DomainError, match="EXERCISE_SCORE_FORBIDDEN"):
        normalize_items({"items": [item]}, "exercise", "u")
    items = normalize_items({"items": [item, item]}, "paper", "u")
    assert "满分：5.0" in render_items(items, "paper")
    assert len({i["item_id"] for i in items}) == 2


def configured_run(client, monkeypatch, provider):
    from app.config import settings
    from app.v2 import repairs, worker
    from app.v2.contracts import StartRun
    from app.v2.tasks import snapshot, start_run
    # This helper exercises the legacy fixed-unit repair contract explicitly.
    headers, created, snap = task_run(client, False, execution_policy={
        "task_adaptive_format": False, "quality_loop": False, "criterion_assessment": False})
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    profile = SimpleNamespace(llm_base_url="https://api.deepseek.com/v1", llm_model="deepseek-flash",
                              llm_thinking="enabled", llm_reasoning_effort="high", llm_model_family="deepseek", llm_max_tokens=0)
    monkeypatch.setattr(worker, "runtime_settings", lambda: profile)
    monkeypatch.setattr(repairs, "runtime_settings", lambda: profile)
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"], mode="configured"), "configured")
    worker.execute_run(run["run_id"], lambda _: provider)
    return snapshot(created["task_id"])


def test_mocked_auto_repair_full_deterministic_recheck(client, monkeypatch):
    class Provider:
        def complete_json(self, system, user):
            if "search:string" in system:
                return {"search": "x=1\n", "replace": "x=1\n```\n"}, 1, {"total_tokens": 3}
            return {"body": "```python\nx=1\n"}, 1, {"total_tokens": 2}
    snap = configured_run(client, monkeypatch, Provider())
    assert snap["state"] == "completed", snap
    assert len(snap["calls"]) == 2
    assert any(f["status"] == "resolved" for f in snap["quality"]["findings"])
    assert snap["quality"]["quality_status"] != "passed"
    assert snap["cost_final"] is False  # no configured price, cannot invent cost
    from app.db import SessionLocal
    from app.v2.storage import CallDispatch
    with SessionLocal() as db:
        calls = db.query(CallDispatch).filter_by(run_id=snap["run_id"]).all()
        for call in calls:
            manifest = json.loads(call.manifest_json)
            request = json.loads(call.request_json)
            assert manifest["call_id"] == call.call_id
            assert request["parameters"]["reasoning_effort"] == "high"
            assert "api_key" not in request


def test_mocked_repair_new_claim_rejected_original_preserved(client, monkeypatch):
    class Provider:
        def complete_json(self, system, user):
            if "search:string" in system:
                return {"search": "x=1\n", "replace": "x=2\n```\n"}, 1, None
            return {"body": "```python\nx=1\n"}, 1, None
    snap = configured_run(client, monkeypatch, Provider())
    assert snap["units"][0]["body"] == "```python\nx=1\n"
    from app.db import SessionLocal
    from app.v2.runtime import job, load
    with SessionLocal() as db:
        assert load(job(db, snap["run_id"]).state_json)["repair_stop_reason"] == "NEW_CLAIM_REJECTED"


def test_paused_unsent_call_releases_capacity(client):
    from app.v2.contracts import Pause
    from app.v2.runtime import authorize_call, claim_root
    from app.v2.tasks import pause, snapshot
    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    authorize_call(run["run_id"], "u", "h", "i", lease["epoch"], "worker", "unsent")
    pause(run["run_id"], Pause(expected_revision=snapshot(created["task_id"])["state_revision"]), "pause-unsent")
    assert snapshot(created["task_id"])["calls"][0]["status"] == "not_sent"


def test_snapshot_matches_backend_response_schema(client):
    from app.v2.responses import DraftSnapshot, RunSnapshot
    headers, created, snap = task_run(client)
    assert RunSnapshot.model_validate(snap).model_dump()["task_id"] == created["task_id"]
    headers, draft, snap = task_run(client, False)
    assert DraftSnapshot.model_validate(snap).run is None


def test_parser_arbiter_rejects_concurrent_and_unknown_before_read():
    import io
    from app.v2.contracts import DomainError
    from app.v2.materials import ingest, parser_slot
    policy = {"disk_reserve_bytes": 0, "temp_quota_bytes": 1024, "extractor_memory_bytes": 8192,
              "extractor_timeout_seconds": 3, "max_extract_workers": 1, "max_expanded_bytes": 1024,
              "max_archive_entries": 1, "ingress_body_policy": "stream", "cancel_grace_seconds": 1,
              "policy_version": "test-only", "chunk_bytes": 32}
    with parser_slot(policy):
        with pytest.raises(DomainError, match="PARSER_BACKPRESSURE"):
            ingest(io.BytesIO(b"never parsed"), policy)
    with pytest.raises(DomainError, match="RESOURCE_POLICY_UNKNOWN"):
        ingest(io.BytesIO(b"never parsed"), {"temp_quota_bytes": 1})


def test_user_review_only_appends_never_changes_finding_or_quality(client, monkeypatch):
    class Provider:
        def complete_json(self, system, user):
            if "search:string" in system:
                return {"search": "x=1\n", "replace": "x=2\n```\n"}, 1, None
            return {"body": "```python\nx=1\n"}, 1, None
    snap = configured_run(client, monkeypatch, Provider())
    finding = snap["quality"]["findings"][0]
    headers = owner(client)
    review = {"expected_revision": snap["state_revision"], "version_id": snap["version_id"],
              "content_hash": snap["content_hash"], "claim": "用户异议", "reason": "请独立复核"}
    result = post(client, f"/findings/{finding['finding_id']}/user-reviews", review, headers, "review-once")
    assert result.status_code == 201, result.text
    assert post(client, f"/findings/{finding['finding_id']}/user-reviews", review, headers, "review-once").json() == result.json()
    after = client.get(f"/api/v2/tasks/{snap['task_id']}/snapshot").json()
    assert after["quality"] == snap["quality"]
    assert after["state_revision"] == snap["state_revision"] + 1


def test_settled_then_crashed_publication_recovers_without_paid_redelivery(client, monkeypatch):
    from app.config import settings
    from app.v2 import worker
    from app.v2.contracts import Resume
    from app.v2.tasks import resume, snapshot
    actual_publish = worker.publish
    def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash after successful settlement")
    monkeypatch.setattr(worker, "publish", crash)
    class Provider:
        calls = 0
        def complete_json(self, system, user):
            self.calls += 1
            return {"body": "durable settled candidate"}, 1, {"total_tokens": 5}
    provider = Provider()
    failed = configured_run(client, monkeypatch, provider)
    assert failed["state"] == "failed" and failed["version_id"] is None
    assert failed["calls"][0]["status"] == "settled"
    result = resume(failed["run_id"], Resume(expected_revision=failed["state_revision"], checkpoint_hash=failed["checkpoint_hash"]), "resume-durable")
    monkeypatch.setattr(worker, "publish", actual_publish)
    monkeypatch.setattr(settings, "v2_allow_paid", False)
    worker.execute_run(result["run_id"], lambda _: provider)
    recovered = snapshot(failed["task_id"], result["run_id"])
    assert recovered["state"] == "completed", recovered
    assert provider.calls == len(recovered["calls"]) == 1
    assert recovered["units"][0]["body"] == "durable settled candidate"
    assert recovered["units"][0]["recovered_from_call_id"] == failed["calls"][0]["call_id"]

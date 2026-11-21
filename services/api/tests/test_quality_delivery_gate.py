"""Strict final delivery gate: draft export stays explicit and immutable."""
from __future__ import annotations

from uuid import uuid4

from test_task_run_v2 import owner, post, task_run


def test_complete_requires_calibrated_strict_pass(client):
    headers, created, snap = task_run(client)
    print(snap.get("state"), snap.get("stop_reason"), snap.get("version_id"), snap.get("calls"))
    payload = {"expected_revision": snap["state_revision"], "version_id": snap["version_id"], "kind": "complete"}
    result = post(client, f"/artifacts/{snap['artifact_id']}/export-sets", payload, headers, f"blocked-complete-{uuid4()}")
    assert result.status_code == 422
    print(result.text)
    error = result.json()
    assert error["code"] == "FINAL_EXPORT_NOT_PASSED"
    assert error["details"]["tqi_verdict"] == "blocked"
    assert error["details"]["calibration_status"] == "not_calibrated"

    draft = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                 payload | {"kind": "available_draft"}, headers, f"blocked-draft-{uuid4()}")
    assert draft.status_code == 201, draft.text
    manifest = draft.json()
    assert manifest["delivery_status"] == "draft"
    assert manifest["quality_gate"]["passed"] is False
    assert manifest["quality_gate"]["calibration_required"] is True
    assert manifest["quality_gate"]["quality_status_required"] == "passed"
    assert manifest["quality_gate"]["method"] == "strict_final_delivery.v3"
    assert [(f["file_id"], f["name"]) for f in manifest["files"]] == [
        ("main", "draft-main.md"), ("bundle", "draft-bundle.zip"),
    ]
    body = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").text
    assert "交付状态：草稿" in body
    assert "请勿当作已完成教程使用" in body
    assert len(client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()["calls"]) == 1


def test_uncalibrated_diagnostics_cannot_bypass_final_delivery_policy():
    from app.v2.exports import strict_delivery_gate
    assert not strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed_uncalibrated",
                                     "calibration_status": "not_calibrated", "blocking_reasons": []})
    assert not strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed",
                                     "calibration_status": "not_calibrated", "blocking_reasons": []})
    assert not strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed",
                                     "blocking_reasons": []})
    assert not strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed",
                                     "calibration_status": "calibrated", "blocking_reasons": [{"item_id": "C1.coverage"}]})
    assert strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed",
                                 "calibration_status": "calibrated", "blocking_reasons": []},
                                {"status": "current", "stale_refs": []})
    assert not strict_delivery_gate({"tqi_verdict": "pass", "quality_status": "passed",
                                     "calibration_status": "calibrated", "blocking_reasons": []},
                                    {"status": "stale", "stale_refs": [{"kind": "profile"}]})


def test_stale_profile_binding_blocks_final_but_keeps_draft(client):
    from app.db import SessionLocal
    from app.v2.insights import save
    from app.v2.runtime import dump, job, load
    from test_task_run_v2_guards import queued
    created, run = queued(client)
    binding = {"profile": {"id": "p", "version": 1, "hash": "h"}, "rubric": {"id": "r", "version": 1, "hash": "h"},
               "binding_hash": "b"}
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
        state["context"]["import_understanding"] = [binding]
        job(db, run["run_id"]).state_json = dump(state)
        save(db, "import-profile-head:p", {"id": "p", "version": 2, "hash": "new"})
        db.commit()
    from app.v2.runtime import claim_root, publish
    from app.v2.tasks import snapshot
    lease = claim_root(run["run_id"], "worker")
    unit_body = "## 一、第一章\n\n正文。"
    publish(run["run_id"], "worker", lease["epoch"], [
        {"unit_id": "u", "body": unit_body, "content_hash": __import__("hashlib").sha256(unit_body.encode()).hexdigest(),
         "artifact_role": "tutorial"}],
        {"tqi_verdict": "pass", "quality_status": "passed", "calibration_status": "calibrated",
         "blocking_reasons": [], "_version_id": None}, "stale-profile")
    snap = snapshot(created["task_id"])
    headers = owner(client)
    result = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                  {"expected_revision": snap["state_revision"], "version_id": snap["version_id"], "kind": "complete"},
                  headers, f"stale-profile-final-{uuid4()}")
    assert result.status_code == 422
    assert result.json()["code"] == "PROFILE_BINDING_STALE"
    draft = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                 {"expected_revision": snap["state_revision"], "version_id": snap["version_id"],
                  "kind": "available_draft"}, headers, f"stale-profile-draft-{uuid4()}")
    assert draft.status_code == 201
    manifest = draft.json()
    assert manifest["profile_binding"]["status"] == "stale"
    assert manifest["quality_gate"]["passed"] is False

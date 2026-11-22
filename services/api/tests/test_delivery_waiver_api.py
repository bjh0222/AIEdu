"""Explicit delivery waiver route, persistence and byte-stable final export."""
from uuid import uuid4

from delivery_fixtures import install_delivery_certificate
from test_task_run_v2 import owner, post
from test_task_run_v2_guards import queued


def prepared_run(client, *, c3="undecidable", c1="satisfied"):
    from app.v2.contracts import sha
    import time

    from app.v2.runtime import (
        claim_root,
        dump,
        job,
        load,
        publish,
        reconcile_stale_jobs,
        write,
    )
    created, run = queued(client)
    with write() as db:
        reconcile_stale_jobs(db, now=time.time() + 10**6)
    lease = claim_root(run["run_id"], "worker")
    body = "## 第一章\n\n固定正文。"
    unit = {
        "unit_id": "unit",
        "title": "第一章",
        "body": body,
        "content_hash": sha(body),
        "artifact_role": "tutorial",
        "display_order": 1,
    }
    with write() as db:
        obj = job(db, run["run_id"])
        state = install_delivery_certificate(load(obj.state_json), [unit])
        checks = [
            {
                "item_id": item["item_id"],
                "result": (
                    c1 if item["item_id"] == "C1.coverage"
                    else c3 if item["item_id"] == "C3.correctness"
                    else "satisfied"
                ),
            }
            for item in state["check_plan"]["check_items"]
        ]
        quality = {
            "tqi_verdict": "needs_repair" if c1 == "violated" else "not_calibrated",
            "quality_status": "failed" if c1 == "violated" else "needs_review",
            "calibration_status": "not_calibrated",
            "blocking_reasons": [
                row for row in (
                    {"item_id": "C1.coverage", "result": c1} if c1 != "satisfied" else None,
                    {"item_id": "C3.correctness", "result": c3} if c3 != "satisfied" else None,
                ) if row
            ],
            "checks": checks,
            "findings": [],
        }
        obj.state_json = dump(state)
        obj.state_revision += 1
    version_id = publish(
        run["run_id"], "worker", lease["epoch"], [unit],
        quality | {"_version_id": None}, f"delivery-{uuid4()}",
    )
    with write() as db:
        obj = job(db, run["run_id"])
        obj.status = "completed"
        obj.state_revision += 1
    from app.v2.tasks import snapshot
    return created, run, snapshot(created["task_id"]), version_id


def waiver_payload(snapshot, *, action="accept", soft_item_ids=None):
    return {
        "expected_revision": snapshot["state_revision"],
        "checkpoint_hash": snapshot["checkpoint_hash"],
        "version_id": snapshot["version_id"],
        "action": action,
        "soft_item_ids": soft_item_ids or [],
        "acknowledge_professional_uncertainty": action == "accept",
    }


def test_waiver_route_is_explicit_persistent_and_export_body_stays_stable(client):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    created, run, snap, version_id = prepared_run(client)
    assert snap["delivery_decision"]["state"] == "waiver_required"
    headers = owner(client)
    accepted = post(
        client, f"/runs/{run['run_id']}/delivery-waivers",
        waiver_payload(snap, soft_item_ids=["C3.correctness"]),
        headers, "waiver-accept-1",
    )
    assert accepted.status_code == 200, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["delivery_decision"]["state"] == "eligible"
    assert accepted_body["waiver"]["status"] == "active"
    with SessionLocal() as db:
        stored_quality = db.get(TutorialVersion, version_id).quality_report

    first = post(
        client, f"/artifacts/{snap['artifact_id']}/export-sets",
        {"expected_revision": accepted_body["state_revision"],
         "version_id": version_id, "kind": "complete"},
        headers, "waiver-final-1",
    )
    assert first.status_code == 201, first.text
    first_manifest = first.json()
    first_main = client.get(
        f"/api/v2/export-sets/{first_manifest['bundle_id']}/files/main"
    ).content
    assert first_manifest["delivery_status"] == "final"
    assert first_manifest["quality"]["calibration_status"] == "not_calibrated"

    current = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    revoked = post(
        client, f"/runs/{run['run_id']}/delivery-waivers",
        waiver_payload(current, action="revoke"), headers, "waiver-revoke",
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["delivery_decision"]["state"] == "waiver_required"
    assert revoked.json()["waiver"]["status"] == "revoked"
    blocked = post(
        client, f"/artifacts/{snap['artifact_id']}/export-sets",
        {"expected_revision": revoked.json()["state_revision"],
         "version_id": version_id, "kind": "complete"},
        headers, "waiver-revoked-export",
    )
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "FINAL_WAIVER_CONFIRM_REQUIRED"

    current = client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json()
    accepted_again = post(
        client, f"/runs/{run['run_id']}/delivery-waivers",
        waiver_payload(current, soft_item_ids=["C3.correctness"]),
        headers, "waiver-accept-2",
    )
    assert accepted_again.status_code == 200, accepted_again.text
    second = post(
        client, f"/artifacts/{snap['artifact_id']}/export-sets",
        {"expected_revision": accepted_again.json()["state_revision"],
         "version_id": version_id, "kind": "complete"},
        headers, "waiver-final-2",
    )
    assert second.status_code == 201, second.text
    second_manifest = second.json()
    second_main = client.get(
        f"/api/v2/export-sets/{second_manifest['bundle_id']}/files/main"
    ).content
    assert second_main == first_main
    assert next(f for f in second_manifest["files"] if f["file_id"] == "main")["sha256"] == next(
        f for f in first_manifest["files"] if f["file_id"] == "main"
    )["sha256"]
    assert first_manifest["bundle_hash"] != second_manifest["bundle_hash"]
    with SessionLocal() as db:
        assert db.get(TutorialVersion, version_id).quality_report == stored_quality


def test_hard_check_cannot_enter_waiver_route(client):
    _, run, snap, _ = prepared_run(client, c1="violated")
    response = post(
        client, f"/runs/{run['run_id']}/delivery-waivers",
        waiver_payload(snap, soft_item_ids=["C3.correctness"]),
        owner(client), "waiver-hard-blocked",
    )
    assert response.status_code == 422
    assert response.json()["code"] == "DELIVERY_WAIVER_HARD_BLOCKED"

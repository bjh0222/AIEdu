"""A completed supplement never marks a failed tutorial generation complete."""
from uuid import uuid4
from test_v2_optimization import heading_task


def test_card_run_remains_explicitly_readable_without_hiding_generation(client):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.runtime import job, load, dump
    from datetime import datetime, timedelta
    _, before = heading_task(client)
    rid = str(uuid4())
    with SessionLocal() as db:
        source = job(db, before["run_id"])
        state = load(source.state_json)
        source.status = "failed"
        state["card_source"] = {"version_id": before["version_id"]}
        state["stop_reason"] = "knowledge_cards_complete"
        root = str(uuid4())
        db.add(GenerationJob(id=root, root_job_id=root, run_id=rid, goal_id=before["task_id"],
            job_type="task_v2", status="completed", state_json=dump(state),
            created_at=datetime.now()+timedelta(seconds=1)))
        db.commit()
    current = client.get(f"/api/v2/tasks/{before['task_id']}/snapshot").json()
    assert current["run_id"] == before["run_id"] and current["state"] == "failed"
    explicit = client.get(f"/api/v2/tasks/{before['task_id']}/snapshot", params={"run_id": rid}).json()
    assert explicit["run_id"] == rid and explicit["state"] == "completed"
    page = client.get("/api/v2/tasks").json()
    row = next(x for x in page["items"] if x["task_id"] == before["task_id"])
    assert row["state"] == "failed"


def test_configured_card_extraction_uses_generation_role_not_review(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import insights
    from app.v2.runtime import claim_root, dump, job, load

    _, snap = heading_task(client)
    run_id = str(uuid4())
    with SessionLocal() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        state.update(
            mode="configured",
            intent_pending=False,
            card_source={
                "task_id": snap["task_id"],
                "version_id": snap["version_id"],
                "content_hash": snap["content_hash"],
                "expected_revision": 0,
                "source_demo": True,
            },
        )
        root = str(uuid4())
        db.add(GenerationJob(
            id=root,
            root_job_id=root,
            run_id=run_id,
            goal_id=snap["task_id"],
            job_type="task_v2",
            status="queued",
            state_json=dump(state),
        ))
        db.commit()
    lease = claim_root(run_id, "fixture")
    calls = []

    def fake_invoke(*args, **kwargs):
        calls.append(kwargs)
        return {"cards": []}, {"call_id": "card-fixture"}

    monkeypatch.setattr("app.v2.inference.invoke", fake_invoke)
    with SessionLocal() as db:
        state = load(job(db, run_id).state_json)
    insights.execute_cards(run_id, "fixture", lease["epoch"], state)
    assert calls and all(call["role"] == "generation" for call in calls)
    assert all(call["stage"] == "knowledge_cards" for call in calls)

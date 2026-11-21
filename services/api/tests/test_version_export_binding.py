"""A resumed execution may still show a version owned by an older run."""
from uuid import uuid4

from test_v2_optimization import heading_task
from test_task_run_v2 import post


def test_export_uses_fixed_version_owner_not_current_execution_revision(client):
    from app.models import GenerationJob
    from app.v2.runtime import job, load, dump, write
    headers, original = heading_task(client)
    with write() as db:
        source_run = job(db, original["run_id"])
        owner_revision = source_run.state_revision
        state = load(source_run.state_json)
        child_id, child_run = str(uuid4()), str(uuid4())
        db.add(GenerationJob(id=child_id, run_id=child_run, goal_id=source_run.goal_id,
            root_job_id=child_id, parent_job_id=source_run.id, job_type="task_v2",
            status="completed", state_revision=101, state_json=dump(state)))
    url = f"/artifacts/{original['artifact_id']}/versions/{original['version_id']}"
    response = client.get('/api/v2' + url)
    assert response.status_code == 200, response.text
    fixed = response.json()
    binding = fixed["export_binding"]
    assert binding == {"task_id": original["task_id"], "run_id": original["run_id"],
        "state_revision": owner_revision, "version_id": original["version_id"], "content_hash": original["content_hash"]}
    endpoint = f"/artifacts/{original['artifact_id']}/export-sets"
    stale = post(client, endpoint, {"version_id": original["version_id"], "expected_revision": 101,
        "kind": "available_draft"}, headers, "wrong-execution-revision")
    assert stale.status_code == 409 and stale.json()["code"] == "REVISION_STALE"
    exported = post(client, endpoint, {"version_id": original["version_id"],
        "expected_revision": binding["state_revision"], "kind": "available_draft"}, headers, "right-version-revision")
    assert exported.status_code == 201
    manifest = exported.json()
    assert {k: manifest[k] for k in ("task_id", "run_id", "version_id", "content_hash")} == {
        k: binding[k] for k in ("task_id", "run_id", "version_id", "content_hash")}
    assert manifest["delivery_status"] == "draft"

"""Registered worker, listing and resume contracts, using isolated source data."""
import pytest

from app.v2.contracts import DomainError, Resume, digest
from test_synthesis_execution import c_output, durable_fixture


def test_registered_worker_executes_synthesis_not_ordinary_generation(client, monkeypatch):
    from app.db import SessionLocal
    from app.v2 import worker, inference
    from app.v2.runtime import job, load
    from app.v2.synthesis_requests import RUNTIME_VERSION, read
    snap, run_id, _, bundle, _ = durable_fixture(client)
    assert worker.SYNTHESIS_RUNTIME_VERSION == RUNTIME_VERSION
    calls = []
    def invoke(*args, **kw):
        calls.append(kw["stage"])
        if kw["stage"].startswith("synthesis_rubric") or kw["stage"] == "synthesis_reviewing":
            from app.v2.skills import entry as skill_entry, review_skill
            assert kw["entries"] == [skill_entry(review_skill(), "C")]
            assert f"# assessment-review.v{review_skill()['version']}" in kw["system"]
        if kw["stage"] == "synthesis_generating":
            return {"source_bundle_hash": bundle["bundle_hash"], "title": "Cross-source knowledge",
                "blocks": [{"block_id": "b1", "heading": "Relations", "body": "Fixed source-bound proposal.",
                    "source_span_ids": [s["source_span_id"] for s in bundle["source_spans"]]}],
                "relations": [], "unintegrated_units": [], "limitations": []}, {"call_id": "B"}
        if kw["stage"] == "synthesis_rubric_reviewing":
            return {"dimensions": [{"dimension_id": "d", "result": "satisfied", "score": 2,
                "reason": "Concrete fixed-content observation", "evidence": [{"field": "body", "quote": "Fixed source-bound proposal."}],
                "indicators": [], "repair_condition": None, "limitations": []}]}, {"call_id": "C-rubric"}
        return c_output(kw["data"]), {"call_id": "C-source"}
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(worker, "resolve_run_intent", lambda *a, **k: pytest.fail("Not a new A/B generation"))
    monkeypatch.setattr(worker, "run_research_before_planning", lambda *a, **k: pytest.fail("No planning research rerun"))
    worker.execute_run(run_id)
    with SessionLocal() as db:
        obj = job(db, run_id)
        assert obj.status == "completed", load(obj.state_json)
    assert calls == ["synthesis_generating", "synthesis_rubric_reviewing", "synthesis_reviewing"]
    result = read(snap["task_id"], snap["version_id"])
    assert result["source_demo"] is True and result["final_delivery_eligible"] is False


def test_supplemental_completion_never_masks_failed_generation_in_snapshot_or_list(client):
    from app.db import SessionLocal
    from app.v2.runtime import dump, job, load
    from app.v2.tasks import snapshot, list_tasks
    from app.v2.insights import save
    snap, run_id, _, _, _ = durable_fixture(client)
    with SessionLocal() as db:
        parent = job(db, snap["run_id"])
        parent.status = "failed"
        child = job(db, run_id)
        child.status, child.created_at = "completed", "2999-01-01T00:00:00"
        state = load(child.state_json)
        binding = state["context"]["research_rubrics"][0]
        binding.update(id="fixture-rubric", version=1, hash=digest(binding["content"]))
        save(db, "import-rubric-head:fixture-rubric", {k: binding[k] for k in ("id", "version", "hash")})
        child.state_json = dump(state)
        db.commit()
    default = snapshot(snap["task_id"])
    assert default["run_id"] == snap["run_id"] and default["state"] == "failed"
    explicit = snapshot(snap["task_id"], run_id)
    assert explicit["run_id"] == run_id and explicit["state"] == "completed"
    row = next(r for r in list_tasks()["items"] if r["task_id"] == snap["task_id"])
    assert row["run_id"] == snap["run_id"] and row["state"] == "failed" and row["needs_action"]


@pytest.mark.parametrize("option", ["replan", "recheck_completed", "refresh_check_plan"])
def test_generic_synthesis_resume_cannot_turn_into_generation_or_fixed_quality_recheck(client, option):
    from app.db import SessionLocal
    from app.v2.runtime import job, load
    from app.v2.tasks import resume
    _, run_id, _, _, _ = durable_fixture(client)
    with SessionLocal() as db:
        obj = job(db, run_id)
        obj.status = "failed"
        state = load(obj.state_json)
        revision = obj.state_revision
        db.commit()
    payload = Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"]),
        **{option: True}, acknowledge_new_generation=True)
    with pytest.raises(DomainError, match="SYNTHESIS_REQUIRES_SOURCE_BOUND_OPERATION"):
        resume(run_id, payload, "illegal-synthesis-resume")


def test_failed_synthesis_resume_preserves_source_and_partial_progress(client):
    from app.db import SessionLocal
    from app.v2.runtime import dump, job, load
    from app.v2.tasks import resume
    _, run_id, _, _, _ = durable_fixture(client)
    with SessionLocal() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        state["synthesis_progress"] = {"completed_model_turn": "retained", "rounds": []}
        obj.status, obj.state_json = "failed", dump(state)
        revision = obj.state_revision
        db.commit()
    result = resume(run_id, Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"])), "resume-synthesis")
    with SessionLocal() as db:
        child = job(db, result["run_id"])
        saved = load(child.state_json)
        assert child.parent_job_id == job(db, run_id).id
        assert saved["synthesis_source"] == state["synthesis_source"]
        assert saved["synthesis_progress"] == state["synthesis_progress"]
        assert saved["budget"] == state["budget"] and child.status == "queued"

"""CAS/ancestry boundary for the pending synthesis runtime registration."""
from copy import deepcopy
from uuid import uuid4

from ..config import settings
from ..db import SessionLocal
from ..models import GenerationJob, Tutorial
from .contracts import DomainError, digest
from .insights import action, receipt, record, source_version
from .runtime import cas, dump, job, load, materialize, write
from .storage import CallDispatch
from .synthesis_contracts import source_bundle

RUNTIME_VERSION = "whole-source-synthesis-execution.v1"


def _source(db, task_id, run_id, version_id):
    parent = job(db, run_id)
    state = load(parent.state_json)
    if parent.goal_id != task_id or state.get("card_source") or state.get("synthesis_source"):
        raise DomainError("SYNTHESIS_REQUIRES_GENERATION_RUN", 422)
    version = source_version(db, task_id, version_id)
    artifact = db.get(Tutorial, version.tutorial_id)
    content = materialize(version)
    if (state.get("version_id") != version.id or artifact.current_version_id != version.id
            or content["plan_hash"] != state["plan"]["plan_hash"]):
        raise DomainError("SYNTHESIS_SOURCE_STALE")
    bundle = source_bundle(state, version.id, version.content_hash, content["units"])
    return parent, state, version, bundle


def preview(task_id, run_id, version_id):
    with SessionLocal() as db:
        parent, _, version, bundle = _source(db, task_id, run_id, version_id)
        head = record(db, "synthesis-head:" + version.id, {"revision": 0})
        return {"task_id": task_id, "run_id": run_id, "state_revision": parent.state_revision,
            "version_id": version.id, "content_hash": version.content_hash, "quality_hash": version.quality_hash,
            "source_bundle_hash": bundle["bundle_hash"], "expected_synthesis_revision": head["revision"],
            "coverage": bundle["coverage"], "status": "source_ready_not_executed"}


def request(task_id, payload, key):
    # No caller can enqueue an operation the currently loaded worker would
    # mistake for ordinary generation. This marker is added only with wiring.
    from . import worker
    if getattr(worker, "SYNTHESIS_RUNTIME_VERSION", None) != RUNTIME_VERSION:
        raise DomainError("SYNTHESIS_RUNTIME_NOT_REGISTERED", 409)
    body = {"operation": "source_bound_synthesis", "task_id": task_id, **payload.model_dump()}
    with write() as db:
        prior = action(db, key, body)
        if prior is not None:
            return prior
        if not settings.v2_allow_paid:
            raise DomainError("PAID_RUN_NOT_AUTHORIZED", 403)
        parent, state, version, bundle = _source(db, task_id, payload.run_id, payload.version_id)
        cas(parent, payload.expected_run_revision)
        if parent.status in {"queued", "running"} or db.query(GenerationJob).filter(
                GenerationJob.goal_id == task_id, GenerationJob.job_type == "task_v2",
                GenerationJob.status.in_(["queued", "running"])).first():
            raise DomainError("ACTIVE_RUN")
        if db.query(CallDispatch).filter(CallDispatch.status.in_(["authorized", "sent", "unknown"])).first():
            raise DomainError("UNKNOWN_OR_ACTIVE_DISPATCH")
        candidates = db.query(GenerationJob).filter_by(goal_id=task_id, job_type="task_v2").order_by(
            GenerationJob.created_at.desc(), GenerationJob.id.desc()).all()
        latest = next((r for r in candidates if not load(r.state_json).get("card_source")
            and not load(r.state_json).get("synthesis_source")), None)
        if latest is None or latest.id != parent.id:
            raise DomainError("SYNTHESIS_PARENT_RUN_STALE")
        from .tasks import goal, require_live_task
        require_live_task(db, task_id)
        if load(goal(db, task_id).task_state_json)["context"]["context_hash"] != state["context"]["context_hash"]:
            raise DomainError("INPUT_CHANGED")
        if (version.content_hash != payload.content_hash or version.quality_hash != payload.quality_hash
                or bundle["bundle_hash"] != payload.source_bundle_hash):
            raise DomainError("SYNTHESIS_SOURCE_STALE")
        head = record(db, "synthesis-head:" + version.id, {"revision": 0})
        if head["revision"] != payload.expected_synthesis_revision:
            raise DomainError("SYNTHESIS_REVISION_STALE", current_revision=head["revision"])
        frozen = deepcopy(state)
        for field in ("receipts", "optimization_request", "card_source", "synthesis_source", "synthesis_progress", "synthesis_result"):
            frozen.pop(field, None)
        source_demo = load(job(db, version.run_id).state_json)["mode"] == "demo"
        frozen.update(mode="configured", intent_pending=False, stages={"synthesis": "queued"}, stop_reason=None,
            synthesis_source={"task_id": task_id, "version_id": version.id, "content_hash": version.content_hash,
                "quality_hash": version.quality_hash, "source_bundle_hash": bundle["bundle_hash"],
                "expected_revision": head["revision"], "source_demo": source_demo, "parent_run_id": parent.run_id})
        run_id, root_id = str(uuid4()), str(uuid4())
        db.add(GenerationJob(id=root_id, root_job_id=root_id, parent_job_id=parent.id, run_id=run_id,
            goal_id=task_id, job_type="task_v2", status="queued", state_json=dump(frozen),
            action_key=key, request_hash=digest(body), state_revision=0, cancel_epoch=0))
        # Inherit the LATEST authorized parent, not the old source-version run.
        # Do not clear cumulative spending, budget amendments or unknown bills.
        response = {"task_id": task_id, "run_id": run_id, "parent_run_id": parent.run_id,
            "version_id": version.id, "source_bundle_hash": bundle["bundle_hash"], "status": "queued", "paid": True}
        return receipt(db, key, body, response)


def read(task_id, version_id):
    with SessionLocal() as db:
        version = source_version(db, task_id, version_id)
        artifact = db.get(Tutorial, version.tutorial_id)
        head = record(db, "synthesis-head:" + version.id)
        if not head:
            return {"task_id": task_id, "version_id": version.id, "content_hash": version.content_hash,
                "status": "not_generated", "revision": 0, "source_is_current": artifact.current_version_id == version.id}
        result = record(db, head["record_key"])
        if (not result or digest(result) != head["result_hash"] or result.get("source_version_id") != version.id
                or result.get("source_content_hash") != version.content_hash
                or result.get("source_quality_hash") != version.quality_hash or result.get("revision") != head["revision"]):
            raise DomainError("SYNTHESIS_RECORD_STALE")
        return {**result, "result_hash": head["result_hash"], "source_is_current": artifact.current_version_id == version.id}

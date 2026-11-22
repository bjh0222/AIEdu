"""Durable root/call gates, lease fencing and immutable version publication."""
from __future__ import annotations

import json
import math
import re
import time
from contextlib import contextmanager
from uuid import uuid4

from sqlalchemy import text

from ..db import SessionLocal
from ..models import GenerationJob, ModelCallLog, Tutorial, TutorialVersion
from .contracts import DomainError, canonical, digest
from .storage import BlobStore, CallDispatch

LEASE_TTL = 60
LEASE_RENEW_SECONDS = 15
ACTIVE_JOB_STATUSES = frozenset({"queued", "running"})
ACTIVE_PROVIDER_STATUSES = frozenset({"authorized", "sent", "unknown"})


@contextmanager
def write():
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise


def load(value):
    return json.loads(value or "{}")


def dump(value):
    return canonical(value).decode("utf-8")


def job(db, run_id):
    obj = db.query(GenerationJob).filter_by(run_id=run_id, job_type="task_v2").first()
    if obj is None:
        raise DomainError("RUN_NOT_FOUND", 404)
    return obj


def cas(obj, revision):
    if obj.state_revision != revision:
        raise DomainError("REVISION_STALE", current_revision=obj.state_revision)


def active_provider_work(db, *, root_job_id=None, unit_id=None):
    """Provider attempts that can still block an active queued/running run.

    Historical failed, paused and interrupted runs keep their call receipts for
    audit, but those receipts no longer occupy the global execution queue.
    Callers must use this single query so scheduling and authorization agree.
    """
    query = db.query(CallDispatch).join(
        GenerationJob, GenerationJob.id == CallDispatch.root_job_id
    ).filter(
        GenerationJob.job_type == "task_v2",
        GenerationJob.status.in_(ACTIVE_JOB_STATUSES),
        CallDispatch.status.in_(ACTIVE_PROVIDER_STATUSES),
    )
    if root_job_id is not None:
        query = query.filter(CallDispatch.root_job_id == root_job_id)
    if unit_id is not None:
        query = query.filter(CallDispatch.unit_id == unit_id)
    return query.order_by(CallDispatch.started_at, CallDispatch.call_id).all()


def normalize_stopped_run_calls(db, obj):
    """Release never-sent authorization without inferring outcomes for sent calls."""
    normalized = []
    for call in db.query(CallDispatch).filter_by(
            root_job_id=obj.id, status="authorized").order_by(CallDispatch.call_id):
        call.status = "not_sent"
        normalized.append({
            "call_id": call.call_id,
            "unit_id": call.unit_id,
            "before": "authorized",
            "after": "not_sent",
        })
    return normalized


def claim_root(run_id: str, worker: str, now=None) -> dict:
    now = time.time() if now is None else now
    with write() as db:
        obj = job(db, run_id)
        if obj.status not in {"queued", "running", "interrupted"}:
            raise DomainError("RUN_NOT_DISPATCHABLE")
        active = db.query(GenerationJob).filter(GenerationJob.job_type == "task_v2", GenerationJob.status == "running",
                 GenerationJob.lease_until > now, GenerationJob.id != obj.id).first()
        if active:
            raise DomainError("RESOURCE_BACKPRESSURE")
        first = db.query(GenerationJob).filter_by(job_type="task_v2", status="queued").order_by(GenerationJob.created_at, GenerationJob.id).first()
        if first and obj.status == "queued" and first.id != obj.id:
            raise DomainError("FIFO_WAIT")
        if obj.status == "running" and obj.lease_until > now and obj.worker_instance_id != worker:
            raise DomainError("LEASE_HELD")
        if obj.worker_instance_id != worker or obj.lease_until <= now:
            obj.cancel_epoch += 1
        obj.worker_instance_id, obj.lease_until, obj.status = worker, now + LEASE_TTL, "running"
        obj.state_revision += 1
        # A crashed worker's possibly-sent request must NEVER be auto-redelivered.
        for call in db.query(CallDispatch).filter_by(root_job_id=obj.id).filter(CallDispatch.status.in_(["authorized", "sent"])):
            if call.epoch != obj.cancel_epoch:
                call.status = "unknown" if call.status == "sent" else "not_sent"
        return {"epoch": obj.cancel_epoch, "revision": obj.state_revision, "lease_until": obj.lease_until}


def reconcile_stale_jobs(db, now=None) -> list[dict]:
    """Release expired execution leases without inventing provider outcomes.

    Callers must already hold a write transaction. An expired ``running`` lease
    becomes ``interrupted``; an in-flight ``sent`` request on that fenced attempt
    becomes ``unknown`` so it cannot be silently replayed. ``authorized`` work
    that never reached the wire becomes ``not_sent``. The original call row and
    receipt are always retained, and an ``unknown`` bill remains unresolved until
    the explicit quarantine action.
    """
    now = time.time() if now is None else now
    released = []
    stale_jobs = db.query(GenerationJob).filter(
        GenerationJob.job_type == "task_v2",
        GenerationJob.status == "running",
        GenerationJob.lease_until <= now,
    ).order_by(GenerationJob.id).all()
    for stale in stale_jobs:
        previous = {
            "run_id": stale.run_id,
            "state": stale.status,
            "worker_instance_id": stale.worker_instance_id,
            "lease_until": stale.lease_until,
            "epoch": stale.cancel_epoch,
        }
        stale.status = "interrupted"
        stale.cancel_epoch += 1
        stale.state_revision += 1
        calls = normalize_stopped_run_calls(db, stale)
        preserved_sent = [
            call.call_id for call in db.query(CallDispatch).filter_by(
                root_job_id=stale.id, status="sent").order_by(CallDispatch.call_id)
        ]
        released.append({
            **previous,
            "state": "interrupted",
            "new_epoch": stale.cancel_epoch,
            "calls": calls,
            "preserved_sent_call_ids": preserved_sent,
            "method_version": "lease-timeout-reaper.v1",
        })
    return released


def fence(obj, worker, epoch, now=None):
    now = time.time() if now is None else now
    if obj.status != "running" or obj.cancel_epoch != epoch or obj.worker_instance_id != worker or obj.lease_until <= now:
        raise DomainError("FENCED")


def renew(run_id, worker, epoch):
    with write() as db:
        obj = job(db, run_id)
        fence(obj, worker, epoch)
        obj.lease_until = time.time() + LEASE_TTL


def _ancestor_call_limit(db, obj):
    """The effective root limit remains cumulative unless explicitly revoked."""
    from sqlalchemy import select

    chain = select(GenerationJob.id, GenerationJob.parent_job_id).where(GenerationJob.id == obj.id).cte("runtime_budget_ancestors", recursive=True)
    chain = chain.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(chain, GenerationJob.id == chain.c.parent_job_id))
    minimum = None
    for ancestor_id in db.scalars(select(chain.c.id)):
        ancestor = db.get(GenerationJob, ancestor_id)
        if ancestor is None:
            continue
        budget = load(ancestor.state_json).get("budget", {})
        hard = budget.get("calls")
        if hard is None and budget.get("calls_amended") is True:
            return minimum
        if isinstance(hard, int) and not isinstance(hard, bool):
            minimum = hard if minimum is None else min(minimum, hard)
    return minimum


def authorize_call(run_id, unit_id, request_hash, input_hash, epoch, worker, key, provider="demo", model="demo", family="demo", supersedes=None,
                   manifest=None, request=None):
    with write() as db:
        obj = job(db, run_id)
        previous = db.query(CallDispatch).filter_by(idempotency_key=key).first()
        if previous:
            if previous.request_hash != request_hash or previous.run_id != run_id or previous.unit_id != unit_id:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return previous.call_id
        fence(obj, worker, epoch)
        unresolved = active_provider_work(db, unit_id=unit_id)
        if any(c.call_id != supersedes for c in unresolved):
            raise DomainError("UNKNOWN_CALL")
        # Unknown calls hold capacity globally until explicitly reconciled. Only
        # the same fenced root may consume its frozen, bounded concurrency.
        state = load(obj.state_json)
        # A resume is a fenced execution attempt, not a fresh spending grant.
        from sqlalchemy import select
        ancestry = select(GenerationJob.id, GenerationJob.parent_job_id).where(GenerationJob.id == obj.id).cte("budget_ancestors", recursive=True)
        ancestry = ancestry.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(ancestry, GenerationJob.id == ancestry.c.parent_job_id))
        root_calls = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(ancestry.c.id))).all()
        budget = state.get("budget", {})
        call_limit = budget.get("calls")
        ancestor_limit = _ancestor_call_limit(db, obj)
        if call_limit is None:
            call_limit = ancestor_limit
        elif ancestor_limit is not None:
            call_limit = min(call_limit, ancestor_limit)
        if call_limit is not None and len(root_calls) >= call_limit:
            raise DomainError("CALL_BUDGET_EXHAUSTED")
        if budget.get("tokens") is not None:
            capacity_request = (request or {}).get("capacity", {})
            reserved = capacity_request.get("reserved_total_tokens")
            if reserved is None:
                reserved = capacity_request.get("estimated_input_tokens", 0) + capacity_request.get("reserved_output_tokens", 0)
            used = 0
            for call in root_calls:
                if call.status == "not_sent":
                    continue
                usage = load(call.usage_json) if call.usage_json else None
                if usage is not None:
                    total = usage.get("total_tokens")
                    if total is None and all(type(usage.get(k)) is int for k in ("prompt_tokens", "completion_tokens")):
                        total = usage["prompt_tokens"] + usage["completion_tokens"]
                    if type(total) is not int or total < 0:
                        raise DomainError("TOKEN_BUDGET_USAGE_UNKNOWN")
                    used += total
                elif call.status in {"authorized", "sent"}:
                    prior_capacity = load(call.request_json).get("capacity", {})
                    used += prior_capacity.get("reserved_total_tokens", prior_capacity.get("estimated_input_tokens", 0) + prior_capacity.get("reserved_output_tokens", 0))
                else:
                    raise DomainError("TOKEN_BUDGET_USAGE_UNKNOWN")
            if used + reserved > budget["tokens"]:
                raise DomainError("TOKEN_BUDGET_EXHAUSTED")
        if budget.get("amount") is not None:
            # No sourced price/reservation contract exists yet. Refuse a priced
            # hard budget rather than pretending unknown monetary cost is zero.
            raise DomainError("AMOUNT_BUDGET_PRICE_UNKNOWN")
        if budget.get("seconds") is not None:
            started = state.get("budget_started_at")
            if started is None:
                started = min((call.started_at for call in root_calls if call.started_at), default=time.time())
                state["budget_started_at"] = started
            if time.time() - started >= budget["seconds"]:
                raise DomainError("TIME_BUDGET_EXHAUSTED")
        capacity = max(1, min(4, state.get("execution_policy", {}).get("provider_concurrency", 1)))
        occupied = active_provider_work(db)
        if (any(c.status == "unknown" or c.root_job_id != obj.id for c in occupied)
                or len(occupied) >= capacity):
            raise DomainError("PROVIDER_BACKPRESSURE")
        call_id = str(uuid4())
        attempt = db.query(CallDispatch).filter_by(run_id=run_id, unit_id=unit_id).count() + 1
        db.add(CallDispatch(call_id=call_id, root_job_id=obj.id, run_id=run_id, unit_id=unit_id, epoch=epoch,
               attempt=attempt, idempotency_key=key, request_hash=request_hash, input_hash=input_hash,
               manifest_json=dump({**(manifest or {}), "call_id": call_id}), request_json=dump(request or {}),
               provider=provider, model=model, model_family=family, supersedes_call_id=supersedes, status="authorized"))
        if manifest and manifest.get("stage"):
            state.setdefault("stages", {})[manifest["stage"]] = "running"
            state["current_stage"] = manifest["stage"]
        obj.state_json = dump(state)
        obj.state_revision += 1
        return call_id


def mark_sent(call_id, worker, epoch):
    with write() as db:
        call = db.get(CallDispatch, call_id)
        if call is None or call.status != "authorized":
            raise DomainError("CALL_NOT_AUTHORIZED")
        fence(job(db, call.run_id), worker, epoch)
        call.status, call.started_at = "sent", time.time()


def safe_failure_diagnostics(value):
    """Retain observed transport details, never infer the remote result.

    Names are closed vocabularies, durations are finite numbers, and unknown
    keys/text are discarded. Historical receipts are not backfilled from a
    configured wait: a read limit alone does not prove a read timeout.
    """
    safe = {}
    for key in ("validation_reason", "finish_reason", "content_characters"):
        item = value.get(key)
        if (type(item) is int and item >= 0
                or isinstance(item, str) and re.fullmatch(r"[A-Za-z_]{1,80}", item)):
            safe[key] = item
    codes = {
        "transport_exception": {
            "HTTPError", "RequestError", "TransportError", "TimeoutException",
            "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout",
            "NetworkError", "ConnectError", "ReadError", "WriteError", "CloseError",
            "ProtocolError", "LocalProtocolError", "RemoteProtocolError", "ProxyError",
            "UnsupportedProtocol", "DecodingError", "TooManyRedirects", "HTTPStatusError",
        },
        "timeout_phase": {"read", "connect", "write", "pool", "unspecified_transport"},
        "remote_outcome": {"unknown"},
    }
    for key, allowed in codes.items():
        item = value.get(key)
        if type(item) is str and item in allowed:
            safe[key] = item
    for key in ("configured_timeout_seconds", "read_timeout_seconds", "elapsed_seconds"):
        item = value.get(key)
        if (type(item) is int and item >= 0
                or type(item) is float and math.isfinite(item) and item >= 0):
            safe[key] = item
    return safe


def settle(call_id, outcome, usage=None, provider_request_id=None, raw_result=None, failure_code=None, failure_diagnostics=None):
    if outcome not in {"success", "failed", "cancelled", "unknown"}:
        raise DomainError("INVALID_CALL_OUTCOME", 422)
    result_blob = BlobStore().put(canonical(raw_result)) if raw_result is not None else None
    with write() as db:
        call = db.get(CallDispatch, call_id)
        if not call:
            raise DomainError("CALL_NOT_FOUND", 404)
        obj = job(db, call.run_id)
        receipt = {"outcome": outcome, "usage": usage, "provider_request_id": provider_request_id,
                   "result_hash": digest(raw_result) if raw_result is not None else None}
        if failure_code is not None:
            # Persist only bounded diagnostic codes, never provider text, URLs,
            # request headers or exception messages that may contain credentials.
            known = {"PROVIDER_TRANSPORT_UNKNOWN","PROVIDER_INVALID_RESPONSE","PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"}
            receipt["failure_code"] = failure_code if isinstance(failure_code,str) and (
                failure_code in known or re.fullmatch(r"PROVIDER_HTTP_[345]\d{2}",failure_code)) else "PROVIDER_FAILURE_UNCLASSIFIED"
        if isinstance(failure_diagnostics, dict):
            receipt["failure_diagnostics"] = safe_failure_diagnostics(failure_diagnostics)
        if call.status == "quarantined":
            state = load(obj.state_json)
            late_receipts = state.setdefault("late_receipts", {})
            receipt_hash = digest(receipt)
            existing = late_receipts.get(call.call_id)
            if existing is not None:
                if existing.get("receipt_hash") != receipt_hash:
                    raise DomainError("SETTLEMENT_CONFLICT")
                return {"status": "quarantined", "late_receipt": "already_recorded"}
            late_receipts[call.call_id] = {
                "method_version": "quarantined-late-receipt.v1",
                "call_id": call.call_id,
                "late_outcome": outcome,
                "receipt_hash": receipt_hash,
                "receipt": receipt,
                "late_result_blob_hash": result_blob,
                "late_usage": usage,
                "usage_source": "provider" if usage is not None else "unknown",
                "provider_request_id": provider_request_id,
                "received_at": time.time(),
            }
            obj.state_json = dump(state)
            obj.state_revision += 1
            return {"status": "quarantined", "late_receipt": "recorded"}
        if call.status in {"settled", "late"}:
            if load(call.receipt_json) != receipt:
                raise DomainError("SETTLEMENT_CONFLICT")
            return {"status": call.status}
        call.outcome = outcome
        call.status = "unknown" if outcome == "unknown" else ("settled" if obj.status == "running" and obj.cancel_epoch == call.epoch and obj.lease_until > time.time() else "late")
        call.receipt_json, call.usage_json = dump(receipt), dump(usage) if usage is not None else None
        call.provider_request_id, call.finished_at = provider_request_id, time.time()
        call.result_blob_hash = result_blob
        state = load(obj.state_json)
        stage = load(call.manifest_json).get("stage")
        if stage and call.status == "settled":
            peers = db.query(CallDispatch).filter_by(root_job_id=obj.id).all()
            same_stage = [c for c in peers if load(c.manifest_json).get("stage") == stage]
            if any(c.status in {"authorized", "sent", "unknown"} for c in same_stage):
                stage_state = "running"
            elif any(c.outcome != "success" for c in same_stage):
                stage_state = "failed"
            else:
                stage_state = "done"
            state.setdefault("stages", {})[stage] = stage_state
            obj.state_json = dump(state)
        existing = db.query(ModelCallLog).filter_by(call_id=call_id).first()
        if existing is None:
            existing = ModelCallLog(call_id=call_id, run_id=call.run_id, unit_id=call.unit_id, goal_id=obj.goal_id,
                                    job_id=obj.id, feature="task_v2", model=call.model, provider=call.provider)
            db.add(existing)
        existing.outcome, existing.status = outcome, call.status
        existing.usage_source = "provider" if usage is not None else "unknown"
        if usage is not None:
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                setattr(existing, field, usage.get(field) or 0)
        obj.state_revision += 1
        return {"status": call.status}


def publish(run_id, worker, epoch, units, quality, operation_id, base_version=None):
    # Persist immutable blobs BEFORE acquiring the DB write lock.
    store = BlobStore()
    ordered = sorted(enumerate(units), key=lambda pair: pair[1].get("display_order", pair[0]))
    frozen = [{k: v for k, v in u.items() if k != "body"} | {"blob_hash": store.put(u["body"].encode())} for _, u in ordered]
    quality = dict(quality)
    requested_version = quality.pop("_version_id", None)
    with write() as db:
        obj = job(db, run_id)
        fence(obj, worker, epoch)
        previous = db.query(TutorialVersion).filter_by(operation_id=operation_id).first()
        state = load(obj.state_json)
        content = {"schema_version": "aiedu.task-run.v2", "units": frozen, "plan_hash": state["plan"]["plan_hash"],
                   "artifacts": state["context"]["requested_artifacts"]}
        if previous:
            if (previous.run_id != run_id or previous.parent_version_id != base_version
                    or previous.content_hash != digest(content) or previous.quality_hash != digest(quality)
                    or (requested_version and previous.id != requested_version)):
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return previous.id
        tutorial = db.get(Tutorial, state["artifact_id"])
        if tutorial.current_version_id != base_version:
            raise DomainError("VERSION_MISMATCH")
        version_id = requested_version or str(uuid4())
        version = TutorialVersion(id=version_id, tutorial_id=tutorial.id,
                  version=db.query(TutorialVersion).filter_by(tutorial_id=tutorial.id).count() + 1,
                  parent_version_id=base_version, run_id=run_id, content_json=dump(content), quality_report=dump(quality),
                  content_hash=digest(content), quality_hash=digest(quality), schema_version="aiedu.task-run.v2",
                  operation_id=operation_id, model_provider="demo" if state["mode"] == "demo" else "configured")
        db.add(version)
        tutorial.current_version_id = version.id
        state["version_id"], state["quality"] = version.id, quality
        obj.state_json, obj.state_revision = dump(state), obj.state_revision + 1
        return version.id


def materialize(version):
    value = load(version.content_json)
    if value.get("schema_version") == "aiedu.task-run.v2":
        store = BlobStore()
        value["units"] = [u | {"body": store.get(u["blob_hash"]).decode()} for u in value["units"]]
    return value

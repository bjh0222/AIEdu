"""Recoverable management overlays. Source bytes and historical evidence stay intact."""
from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import select, func

from ..db import SessionLocal
from ..models import (DesignPrinciple, FormatSpec, GenerationJob, GoalMaterial, LearningGoal,
                      PropositionBatch, PropositionEvaluation, PropositionQuestion, Tutorial, TutorialVersion)
from .contracts import DomainError, digest
from .insights import action, receipt, record, save
from .runtime import LEASE_TTL, load, reconcile_stale_jobs, write
from .storage import CallDispatch, ExportSet
from .management_lifecycle import (CALL_ACTIVE, RUN_ACTIVE, canonical_kind, overlay_ids, references,
    blob_references, stage_blobs, restore_blobs, finish_blobs)
import time

MODELS = {"tasks": LearningGoal, "materials": GoalMaterial, "specs": FormatSpec, "batches": PropositionBatch,
    "questions": PropositionQuestion, "evaluations": PropositionEvaluation, "principles": DesignPrinciple,
    "results": Tutorial, "versions": TutorialVersion, "quality-reports": TutorialVersion, "exports": ExportSet}
KEYS = {"exports": "bundle_id"}
LABELS = {"tasks": "学习任务与目标", "materials": "资料", "specs": "格式规范", "batches": "命题批次",
    "questions": "题目", "evaluations": "外评", "principles": "命题原理", "results": "生成结果",
    "versions": "固定版本", "quality-reports": "质量报告", "exports": "导出集"}


def key_of(kind, object_id):
    return "managed:" + canonical_kind(kind) + ":" + object_id


def base_query(db, kind):
    if kind not in MODELS:
        raise DomainError("RESOURCE_KIND_INVALID", 422)
    model = MODELS[kind]
    query = db.query(model)
    if kind == "tasks":
        query = query.filter(LearningGoal.input_revision > 0)
    elif kind in {"materials", "results"}:
        query = query.join(LearningGoal, model.goal_id == LearningGoal.id).filter(LearningGoal.input_revision > 0)
    elif kind in {"versions", "quality-reports"}:
        query = query.filter(TutorialVersion.schema_version == "aiedu.task-run.v2")
    return query


def describe(db, kind, row):
    object_id = getattr(row, KEYS.get(kind, "id"))
    overlay = {"revision": 0, "archived": False, "trashed": False,
        **record(db, key_of(kind, object_id), {})}
    fields = ("title", "name", "filename", "status", "version", "input_revision", "content_hash", "quality_hash",
              "source_hash", "raw_hash", "extracted_hash", "extract_status", "created_at", "updated_at", "goal_id",
              "tutorial_id", "question_id", "is_builtin", "is_default", "scene", "state", "manifest_hash", "version_id")
    value = {field: getattr(row, field) for field in fields if hasattr(row, field)}
    source_title = next((value[k] for k in ("name", "title", "filename") if value.get(k)), None)
    if kind == "questions":
        source_title = " ".join(v for v in (row.question_number, row.raw_stem) if v)[:160] or source_title
    elif kind == "principles":
        source_title = source_title or row.statement[:160]
    title = overlay.get("label") or source_title or LABELS[kind]
    result = {"kind": kind, "id": object_id, "label": title, "metadata": value, "management": overlay,
        "source_edit": "versioned_endpoint" if kind in {"tasks", "specs", "principles"} else "unsupported_immutable_source",
        "hard_delete": "explicit_preflight_required",
        "source_fingerprint": digest({c.name: getattr(row, c.name) for c in row.__table__.columns})}
    return {**result, "resource_hash": digest(result)}


def resource(db, kind, object_id):
    model = MODELS.get(kind)
    if model is None:
        raise DomainError("RESOURCE_KIND_INVALID", 422)
    row = base_query(db, kind).filter(getattr(model, KEYS.get(kind, "id")) == object_id).first()
    if not row:
        raise DomainError("RESOURCE_NOT_FOUND", 404)
    return row


def catalog(kind, search="", offset=0, limit=50, archived=False, trashed=False):
    if len(search) > 200 or offset < 0 or not 1 <= limit <= 200:
        raise DomainError("RESOURCE_QUERY_INVALID", 422)
    with SessionLocal() as db:
        if kind == "model-profiles":
            from .management_profiles import catalog as profiles_catalog
            return profiles_catalog(search, offset, limit, archived, trashed)
        model = MODELS.get(kind)
        if model is None:
            raise DomainError("RESOURCE_KIND_INVALID", 422)
        identity = getattr(model, KEYS.get(kind, "id"))
        query = base_query(db, kind)
        # Apply filtering before pagination; metadata overlay remains recoverable.
        from ..models import PropositionConfig
        archived_ids = overlay_ids(kind, "archived")
        trash_ids = overlay_ids(kind)
        query = query.filter(identity.in_(trash_ids) if trashed else identity.notin_(trash_ids))
        if not trashed:
            query = query.filter(identity.in_(archived_ids) if archived else identity.notin_(archived_ids))
        if search:
            from sqlalchemy import or_
            from ..models import PropositionConfig
            prefix = "managed:" + canonical_kind(kind) + ":"
            label_ids = select(func.substr(PropositionConfig.id, len(prefix) + 1)).where(
                PropositionConfig.id.startswith(prefix),
                func.json_extract(PropositionConfig.settings_json, "$.label").contains(search, autoescape=True))
            names = [getattr(model, f).contains(search, autoescape=True) for f in
                ("title", "name", "filename", "raw_stem", "question_number", "statement") if hasattr(model, f)]
            query = query.filter(or_(identity.contains(search, autoescape=True), identity.in_(label_ids), *names))
        total = query.count()
        rows = query.order_by(identity).offset(offset).limit(limit).all()
        return {"items": [describe(db, kind, row) for row in rows], "total": total, "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < total else None}


def impact(db, kind, object_id):
    if kind == "model-profiles":
        from .management_profiles import impact as profile_impact
        return profile_impact(db, object_id)
    row = resource(db, kind, object_id)
    refs = []
    jobs = db.query(GenerationJob).filter_by(job_type="task_v2").order_by(GenerationJob.id).all()
    for run in jobs:
        state = load(run.state_json)
        context = state["context"]
        related = (kind == "tasks" and run.goal_id == object_id
            or kind == "materials" and object_id in context.get("material_ids", [])
            or kind == "specs" and any(b.get("spec_id") == object_id for b in context.get("spec_bindings", []))
            or kind == "questions" and object_id in context.get("knowledge", {}).get("question_ids", [])
            or kind == "principles" and any(p.get("id") == object_id for p in context.get("knowledge_sources", []))
            or kind == "results" and state.get("artifact_id") == object_id
            or kind in {"versions", "quality-reports"} and state.get("version_id") == object_id)
        if related:
            refs.append({"kind": "run", "task_id": run.goal_id, "run_id": run.run_id, "state": run.status, "label": "历史运行"})
    if kind == "batches":
        refs.extend({"kind": "question", "id": q.id, "label": "批次题目"} for q in db.query(PropositionQuestion).filter_by(batch_id=object_id).order_by(PropositionQuestion.id))
    refs.extend(references(db, row, [object_id]))
    hashes = [getattr(row, f, "") for f in ("raw_hash", "extracted_hash")]
    # A shared blob is retained, not a reason to delete the other material.
    shared = blob_references(db, row, [h for h in hashes if h])
    historical_blob_refs = [r for r in shared if r["kind"] != "goal_materials"]
    refs.extend(historical_blob_refs)
    # A paused/failed run can still own a sent/unknown provider call.
    run_ids = {r.get("run_id") for r in refs if r.get("run_id")}
    inflight = db.query(CallDispatch).filter(
        CallDispatch.run_id.in_(run_ids), CallDispatch.status.in_(CALL_ACTIVE)
    ).all() if run_ids else []
    refs.extend({"kind": "provider_call", "id": c.call_id, "run_id": c.run_id, "state": c.status,
                 "label": "提供方调用尚未隔离或结算"} for c in inflight)
    unique = {digest(r): r for r in refs}
    refs = sorted(unique.values(), key=lambda r: (r["kind"], r.get("id") or r.get("run_id") or ""))
    active_blockers = _execution_blockers(db, refs)
    blockers = [{"code": "RESOURCE_REFERENCED", "reason": "仍有来源、子记录或历史证据引用；先处理引用，不能级联删除。"}] if refs else []
    if kind in {"versions", "quality-reports", "exports"}:
        blockers.append({"code": "IMMUTABLE_EVIDENCE_RETAINED", "reason": "固定版本、质量证据和已发放下载包须保留；可移入回收站，永久清除不可用。"})
    if kind == "materials" and row.stored_name:
        blockers.append({"code": "LEGACY_MATERIAL_STORAGE_UNSUPPORTED", "reason": "该资料仍使用旧式文件存储；本管理入口不能安全清除旧文件。"})
    if kind == "specs" and (row.is_builtin or row.is_default):
        blockers.append({"code": "PROTECTED_SPEC", "reason": "内置或默认规范受保护；自定义默认规范须先取消默认。"})
    result = {"resource": describe(db, kind, row), "references": refs,
        "active": any(item["blocking"] for item in active_blockers),
        "active_blockers": active_blockers,
        "stale_execution_releasable": any(item["stale"] and not item["blocking"] for item in active_blockers),
        "purge_allowed": not blockers,
        "purge_blockers": blockers, "shared_blob_references": shared,
        "effects": {"archive": "仅从归档目录分流，不改原文和历史绑定", "trash": "隐藏常规目录，可恢复；原文字节和历史版本保留",
            "purge": "仅删除本对象及其独占原文blob；不级联；审计/备份/SQLite日志不做安全擦除"},
        "policy": "archive_and_trash_separate; immutable_history_preserved; purge_requires_confirmation_and_hashes"}
    return {**result, "impact_hash": digest(result)}


def _execution_blockers(db, refs, now=None):
    """Turn broad references into exact, user-actionable execution blockers."""
    now = time.time() if now is None else now
    run_rows = {
        row.run_id: row
        for row in db.query(GenerationJob).filter(
            GenerationJob.job_type == "task_v2",
            GenerationJob.run_id.in_({r.get("run_id") for r in refs if r.get("run_id")}),
        ).all()
    }
    call_rows = {
        row.call_id: row
        for row in db.query(CallDispatch).filter(
            CallDispatch.run_id.in_({r.get("run_id") for r in refs if r.get("run_id")})
        ).all()
    }
    selected = {}
    for ref in refs:
        run = run_rows.get(ref.get("run_id"))
        state = ref.get("state")
        if ref.get("kind") in {"run", "generation_jobs"} and state in RUN_ACTIVE:
            stale = bool(
                state == "running"
                and run is not None
                and (run.lease_until or 0) <= now
            )
            selected[("run", run.run_id if run else ref.get("run_id"))] = {
                "kind": "task",
                "id": run.id if run else ref.get("id"),
                "task_id": run.goal_id if run else ref.get("task_id"),
                "run_id": run.run_id if run else ref.get("run_id"),
                "state": state,
                "lease_until": run.lease_until if run else None,
                "worker_instance_id": run.worker_instance_id if run else None,
                "stale": stale,
                "blocking": not stale,
                "required_action": "wait_for_worker" if not stale else "automatic_lease_recovery",
                "reason": (
                    f"任务 {run.run_id if run else ref.get('run_id')} 状态为 {state}，"
                    + ("租约已过期，可由确定性回收器释放。" if stale else "执行租约仍有效。")
                ),
            }
        call = call_rows.get(ref.get("id")) if ref.get("kind") in {"generation_call_dispatches", "provider_call"} else None
        if call is not None and state in CALL_ACTIVE:
            run = run_rows.get(call.run_id)
            timed_out = bool(
                state == "sent"
                and call.started_at is not None
                and now - call.started_at >= max(LEASE_TTL, 660)
            )
            orphaned = run is None or run.status not in {"running", "queued"}
            stale = timed_out or orphaned
            selected[("call", call.call_id)] = {
                "kind": "provider_call",
                "id": call.call_id,
                "task_id": run.goal_id if run else None,
                "run_id": call.run_id,
                "unit_id": call.unit_id,
                "state": state,
                "started_at": call.started_at,
                "stale": stale,
                "blocking": True,
                "required_action": "explicit_quarantine",
                "reason": (
                    f"运行 {call.run_id} 的 {state} 调用 {call.call_id}"
                    + (" 已超过无回执等待阈值，须在停止运行后显式隔离。" if timed_out
                       else " 所属运行已停止，须显式隔离以释放执行占用。" if orphaned
                       else " 仍在提供方调用执行域内。")
                ),
            }
    return sorted(selected.values(), key=lambda item: (item["kind"], item.get("run_id") or "", item["id"]))


def change(payload, action_key):
    body = payload.model_dump()
    if any(item.kind == "model-profiles" for item in payload.items):
        if not all(item.kind == "model-profiles" for item in payload.items):
            raise DomainError("RESOURCE_MIXED_STORAGE_BATCH", 422)
        from .management_profiles import change as profiles_change
        return profiles_change(payload, action_key)
    staged, removed_hashes = [], []
    try:
      with write() as db:
        prior = action(db, action_key, body)
        if prior is not None:
            if prior.get("cleanup_pending"):
                pending = finish_blobs(prior["blob_hashes"], action_key)
                prior = {**prior, "cleanup_pending": pending, "source_bytes_deleted": not pending}
                return receipt(db, action_key, body, prior)
            return prior
        ids = [(canonical_kind(item.kind), item.id) for item in payload.items]
        if len(set(ids)) != len(ids):
            raise DomainError("RESOURCE_DUPLICATE_TARGET", 422)
        checked = []
        for item in payload.items:
            row = resource(db, item.kind, item.id)
            current = describe(db, item.kind, row)
            refs = impact(db, item.kind, item.id)
            if current["resource_hash"] != item.expected_hash or refs["impact_hash"] != item.impact_hash:
                raise DomainError("RESOURCE_STALE")
            if refs["active"]:
                raise DomainError(
                    "RESOURCE_IN_ACTIVE_RUN",
                    blockers=refs["active_blockers"],
                    summary="资源仍被精确列出的任务或调用阻塞。",
                )
            if item.kind == "specs" and (row.is_builtin or (row.is_default and payload.action in {"trash", "purge"})):
                raise DomainError("BUILTIN_SPEC_IMMUTABLE", 403)
            if payload.action == "purge":
                if not current["management"]["trashed"]:
                    raise DomainError("RESOURCE_TRASH_REQUIRED")
                if payload.confirmation_names.get(item.id) != current["label"]:
                    raise DomainError("RESOURCE_CONFIRMATION_REQUIRED", 422)
                if not refs["purge_allowed"]:
                    raise DomainError("RESOURCE_PURGE_BLOCKED", references=refs["references"], blockers=refs["purge_blockers"])
            if payload.action == "restore" and current["management"]["trashed"]:
                raise DomainError("RESOURCE_USE_RESTORE_TRASH", 422)
            if refs["references"] and not payload.acknowledge_references:
                raise DomainError("RESOURCE_REFERENCED", references=refs["references"])
            checked.append((item, current, row, refs))
        # A stale worker lease must not leave a resource disabled forever.
        # The preflight hash is validated against the observed state first;
        # recovery then releases only expired execution leases and never
        # settles a bill or rewrites an unknown provider outcome.
        reconcile_stale_jobs(db)
        results = []
        for item, current, row, refs in checked:
            if payload.action == "purge":
                if item.kind == "materials":
                    hashes = {h for h in (row.raw_hash, row.extracted_hash) if h}
                    for value in sorted(hashes):
                        if not blob_references(db, row, [value]):
                            removed_hashes.append(value)
                db.delete(row)
                db.flush()
                save(db, "resource-deletion:" + digest([item.kind, item.id]), {
                    "kind": item.kind, "id": item.id, "resource_hash": current["resource_hash"],
                    "impact_hash": refs["impact_hash"], "deleted_at": datetime.now(timezone.utc).isoformat(),
                    "action_key_hash": digest(action_key), "source_text_retained_in_audit": False})
                results.append({"kind": item.kind, "id": item.id, "purged": True})
                continue
            value = dict(current["management"])
            value.setdefault("history", []).append({"revision": value["revision"], "archived": value["archived"], "trashed": value["trashed"], "label": value.get("label")})
            value["revision"] += 1
            if payload.action == "label":
                if not payload.label or not payload.label.strip():
                    raise DomainError("RESOURCE_LABEL_REQUIRED", 422)
                value["label"] = payload.label.strip()
            elif payload.action in {"trash", "restore_trash"}:
                value["trashed"] = payload.action == "trash"
            else:
                value["archived"] = payload.action == "archive"
            save(db, key_of(item.kind, item.id), value)
            results.append({"kind": item.kind, "id": item.id, "management": value})
        staged = stage_blobs(removed_hashes, action_key)
        result = receipt(db, action_key, body, {"items": results, "operation": payload.action, "source_bytes_deleted": False,
            "cleanup_pending": removed_hashes, "blob_hashes": removed_hashes, "batch_semantics": "atomic_all_or_nothing",
            "restore": "action=restore_trash restores trash; action=restore restores archive", "receipt_hash": digest([body, results])})
    except BaseException:
        restore_blobs(staged)
        raise
    if removed_hashes:
        with write() as db:
            pending = finish_blobs(removed_hashes, action_key)
            result = {**result, "cleanup_pending": pending, "source_bytes_deleted": not pending}
            receipt(db, action_key, body, result)
    return result


def cleanup(action_key):
    """Finish only files owned by a committed purge receipt; never delete rows."""
    with write() as db:
        identity = "insight-action:" + digest(action_key)
        prior = record(db, identity)
        if not prior or prior["response"].get("operation") != "purge":
            raise DomainError("RESOURCE_CLEANUP_RECEIPT_REQUIRED", 404)
        response = prior["response"]
        if response.get("cleanup_pending"):
            pending = finish_blobs(response["blob_hashes"], action_key)
            response = {**response, "cleanup_pending": pending, "source_bytes_deleted": not pending}
            save(db, identity, {**prior, "response": response})
        return response

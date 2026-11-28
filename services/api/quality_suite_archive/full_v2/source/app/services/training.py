"""测试版训练数据：生成材料与用户反馈的持久化、统计与导出（JSONL）。

导出格式刻意贴近常见指令微调样本：
- material：messages = [system, user, assistant]，metadata 保留来源/模型/token；
- feedback：feedback = {rating, tags, comment, target_type} + context 快照。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import LearningGoal, TrainingRecord, Tutorial, TutorialVersion
from .utils import jdump, jload, token_usage_dict

logger = logging.getLogger("aiedu")

VALID_TARGETS = ("general", "generation", "tutorial", "section")

# 反馈原文上限：超过即显式拒绝（400），绝不静默截断——用户原文逐字保留是
# 反馈证据（ADR-025）的完整性前提。
MAX_COMMENT_LENGTH = 2000
MAX_RUN_ID_LENGTH = 64


def _preview(text: str, limit: int = 240) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…"


def record_dict(record: TrainingRecord) -> dict:
    return {
        "id": record.id,
        "source_type": record.source_type,
        "feature": record.feature,
        "goal_id": record.goal_id,
        "tutorial_id": record.tutorial_id,
        "section_id": record.section_id,
        "rating": record.rating,
        "tags": jload(record.tags, []),
        "comment": record.comment or "",
        "created_at": record.created_at,
        "model_provider": record.model_provider or "",
        "model_name": record.model_name or "",
        "token_usage": token_usage_dict(
            record.prompt_tokens,
            record.completion_tokens,
            record.total_tokens,
            record.usage_source or "",
            calls=1 if (record.total_tokens or 0) > 0 else 0,
        ),
        "context": jload(record.context_json, {}),
        "prompt_preview": _preview("\n".join(x for x in (record.system_prompt, record.user_prompt) if x)),
        "output_preview": _preview(record.output_text),
        "prompt_chars": len(record.system_prompt or "") + len(record.user_prompt or ""),
        "output_chars": len(record.output_text or ""),
    }


def _validate_binding_request(db: Session, payload) -> dict:
    """版本/生成记录/run 的从属校验（任务书 05 切片 A）：错误返回明确 4xx。"""
    request: dict = {}
    tutorial_version_id = getattr(payload, "tutorial_version_id", None)
    if tutorial_version_id:
        if not payload.tutorial_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "version_requires_tutorial",
                        "message": "tutorial_version_id 需要与 tutorial_id 同时提供"},
            )
        tv = db.get(TutorialVersion, tutorial_version_id)
        if tv is None or tv.tutorial_id != payload.tutorial_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "version_mismatch",
                        "message": "tutorial_version_id 不属于该教程"},
            )
        if payload.section_id:
            content = jload(tv.content_json, {}) or {}
            if not any(s.get("id") == payload.section_id for s in content.get("sections", [])):
                raise HTTPException(
                    status_code=404,
                    detail={"code": "section_not_found",
                            "message": "章节不存在于该版本"},
                )
        request["tutorial_version_id"] = tutorial_version_id
    generation_record_id = getattr(payload, "generation_record_id", None)
    if generation_record_id:
        rec = db.get(TrainingRecord, generation_record_id)
        if rec is None or rec.source_type != "material":
            raise HTTPException(
                status_code=400,
                detail={"code": "generation_record_not_found",
                        "message": "generation_record_id 不存在或不是生成记录"},
            )
        if rec.tutorial_id != payload.tutorial_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "generation_record_mismatch",
                        "message": "generation_record_id 属于其他教程"},
            )
        request["generation_record_id"] = generation_record_id
    run_id = getattr(payload, "run_id", None)
    if run_id:
        run_id = str(run_id).strip()
        if not run_id or len(run_id) > MAX_RUN_ID_LENGTH:
            raise HTTPException(
                status_code=400,
                detail={"code": "bad_run_id",
                        "message": f"run_id 需为 1-{MAX_RUN_ID_LENGTH} 字符"},
            )
        request["run_id"] = run_id
    return request


def _touch_targets(db: Session, goal_id: str | None, tutorial_id: str | None,
                   section_id: str | None, *, section_checked: bool = False) -> None:
    if goal_id:
        goal = db.get(LearningGoal, goal_id)
        if goal is None:
            raise HTTPException(status_code=404, detail={"code": "goal_not_found", "message": "学习目标不存在"})
    if tutorial_id:
        tutorial = db.get(Tutorial, tutorial_id)
        if tutorial is None:
            raise HTTPException(
                status_code=404, detail={"code": "tutorial_not_found", "message": "教程不存在"}
            )
        if section_id and not section_checked:
            # 未显式给版本时按 current 校验；显式版本的章节校验在 _validate_binding_request
            version = db.get(TutorialVersion, tutorial.current_version_id) if tutorial.current_version_id else None
            content = jload(version.content_json, {}) if version else {}
            if not any(s.get("id") == section_id for s in content.get("sections", [])):
                raise HTTPException(
                    status_code=404, detail={"code": "section_not_found", "message": "章节不存在"}
                )


def submit_feedback(db: Session, payload) -> dict:
    target_type = payload.target_type or "general"
    if target_type not in VALID_TARGETS:
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_target_type", "message": f"target_type 需为 {', '.join(VALID_TARGETS)}"},
        )
    # 显式长度校验：原文逐字保留，不 strip、不截断（任务书 05 切片 A）
    comment = str(payload.comment or "")
    if len(comment) > MAX_COMMENT_LENGTH:
        raise HTTPException(
            status_code=400,
            detail={"code": "comment_too_long",
                    "message": f"comment 超过 {MAX_COMMENT_LENGTH} 字符上限（当前 {len(comment)}）；"
                               f"反馈原文逐字保留，请缩短后提交"},
        )
    binding_request = _validate_binding_request(db, payload)
    _touch_targets(db, payload.goal_id, payload.tutorial_id, payload.section_id,
                   section_checked=bool(binding_request.get("tutorial_version_id")))
    tags = [str(tag).strip()[:40] for tag in (payload.tags or []) if str(tag).strip()][:12]
    context = {
        "target_type": target_type,
        "client_context": payload.context or {},
        "binding_request": binding_request,
    }
    record = TrainingRecord(
        source_type="feedback",
        feature="user_feedback",
        goal_id=payload.goal_id,
        tutorial_id=payload.tutorial_id,
        section_id=payload.section_id,
        rating=payload.rating,
        tags=jdump(tags),
        comment=comment,
        context_json=jdump(context),
        model_provider="user",
        model_name="feedback",
    )
    db.add(record)
    db.flush()  # 取 record.id 供证据绑定
    # 板块五反馈消费（ADR-025）：版本绑定 + 去重判定 + pending 事件写进同一 DB 事务。
    # 台账文件在 commit 之后才投递（_deliver_event），rollback 不产生文件侧悬空事件。
    # 纯确定性、零模型调用；失败只降级为「无证据绑定」，不阻断反馈提交。
    try:
        from .feedback_evidence import record_user_feedback

        record_user_feedback(db, record)
    except Exception as exc:  # noqa: BLE001  证据归档是增强环节
        logger.warning("反馈证据归档失败（不影响反馈提交）：%s", exc)
    db.commit()
    # commit 后幂等投递台账，记录 delivered/pending/failed；失败不阻断提交，
    # 经显式恢复入口（POST /training/feedback-evidence/recover）零模型补投。
    try:
        from . import feedback_evidence as feedback_evidence_module

        feedback_evidence_module._deliver_event(db, record)
    except Exception as exc:  # noqa: BLE001  投递失败留待恢复
        logger.warning("反馈证据投递失败（显式恢复入口可补投）：%s", exc)
    return record_dict(record)


def list_records(
    db: Session,
    *,
    source_type: str | None = None,
    goal_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    query = db.query(TrainingRecord)
    if source_type:
        query = query.filter(TrainingRecord.source_type == source_type)
    if goal_id:
        query = query.filter(TrainingRecord.goal_id == goal_id)
    total = query.count()
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    rows = (
        query.order_by(TrainingRecord.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [record_dict(r) for r in rows],
    }


def stats(db: Session) -> dict:
    rows = db.query(TrainingRecord).all()
    materials = [r for r in rows if r.source_type == "material"]
    feedback = [r for r in rows if r.source_type == "feedback"]
    demo_materials = sum(1 for r in materials if (jload(r.context_json, {}) or {}).get("demo"))
    tokens = token_usage_dict()
    by_feature: dict[str, dict] = {}
    for row in materials:
        tokens["prompt_tokens"] += int(row.prompt_tokens or 0)
        tokens["completion_tokens"] += int(row.completion_tokens or 0)
        tokens["total_tokens"] += int(row.total_tokens or 0)
        tokens["calls"] += 1
        item = by_feature.setdefault(row.feature or "unknown", {"feature": row.feature or "unknown", "records": 0, "total_tokens": 0})
        item["records"] += 1
        item["total_tokens"] += int(row.total_tokens or 0)
    if any(r.usage_source == "provider" for r in materials):
        tokens["usage_source"] = "provider"
    elif materials:
        tokens["usage_source"] = "estimated"

    positives = sum(1 for r in feedback if r.rating == 1)
    negatives = sum(1 for r in feedback if r.rating == -1)
    return {
        "totals": {
            "records": len(rows),
            "materials": len(materials),
            "feedback": len(feedback),
            "demo_materials": demo_materials,
            "goals": len({r.goal_id for r in rows if r.goal_id}),
        },
        "by_feature": sorted(by_feature.values(), key=lambda x: x["total_tokens"], reverse=True),
        "tokens": tokens,
        "feedback": {
            "positive": positives,
            "negative": negatives,
            "with_comment": sum(1 for r in feedback if (r.comment or "").strip()),
        },
    }


def _material_sample(record: TrainingRecord) -> dict:
    messages = []
    if record.system_prompt:
        messages.append({"role": "system", "content": record.system_prompt})
    if record.user_prompt:
        messages.append({"role": "user", "content": record.user_prompt})
    if record.output_text:
        messages.append({"role": "assistant", "content": record.output_text})
    context = jload(record.context_json, {})
    return {
        "id": record.id,
        "source_type": "material",
        "feature": record.feature,
        "created_at": record.created_at,
        "messages": messages,
        "metadata": {
            "goal_id": record.goal_id,
            "tutorial_id": record.tutorial_id,
            "section_id": record.section_id,
            "model_provider": record.model_provider,
            "model_name": record.model_name,
            "prompt_tokens": record.prompt_tokens,
            "completion_tokens": record.completion_tokens,
            "total_tokens": record.total_tokens,
            "usage_source": record.usage_source,
            "demo": bool(context.get("demo")),
            "context": context,
        },
    }


def _feedback_sample(record: TrainingRecord) -> dict:
    context = jload(record.context_json, {})
    return {
        "id": record.id,
        "source_type": "feedback",
        "feature": record.feature,
        "created_at": record.created_at,
        "feedback": {
            "rating": record.rating,
            "tags": jload(record.tags, []),
            "comment": record.comment,
            "target_type": context.get("target_type", "general"),
        },
        "target": {
            "goal_id": record.goal_id,
            "tutorial_id": record.tutorial_id,
            "section_id": record.section_id,
        },
        "context": context.get("client_context", {}),
    }


def export_dataset(
    db: Session,
    *,
    source_type: str | None = None,
    goal_id: str | None = None,
    exclude_demo: bool = False,
    fmt: str = "jsonl",
) -> tuple[str, str, str]:
    """导出训练数据，返回 (文件名, 内容, media_type)。"""
    if source_type and source_type not in ("material", "feedback"):
        raise HTTPException(
            status_code=400,
            detail={"code": "bad_source_type", "message": "source_type 仅支持 material / feedback"},
        )
    query = db.query(TrainingRecord)
    if source_type:
        query = query.filter(TrainingRecord.source_type == source_type)
    if goal_id:
        query = query.filter(TrainingRecord.goal_id == goal_id)
    rows = query.order_by(TrainingRecord.created_at.asc()).all()

    samples = []
    for record in rows:
        if record.source_type == "feedback":
            samples.append(_feedback_sample(record))
        else:
            context = jload(record.context_json, {})
            if exclude_demo and context.get("demo"):
                continue
            samples.append(_material_sample(record))

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = source_type or "all"
    if fmt == "json":
        content = json.dumps(samples, ensure_ascii=False, indent=2)
        filename = f"aiedu-training-{suffix}-{stamp}.json"
        media_type = "application/json; charset=utf-8"
    else:
        content = "\n".join(json.dumps(sample, ensure_ascii=False) for sample in samples)
        if content:
            content += "\n"
        filename = f"aiedu-training-{suffix}-{stamp}.jsonl"
        media_type = "application/x-ndjson; charset=utf-8"
    return filename, content, media_type


def export_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"

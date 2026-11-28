"""测试版训练数据路由：用户反馈、记录列表、统计与导出、反馈证据闭环。"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..db import get_db
from ..schemas import TrainingFeedbackIn, TrainingListOut, TrainingRecordOut, TrainingStatsOut
from ..services import feedback_evidence as feedback_evidence_service
from ..services import training as training_service

router = APIRouter()


@router.post("/training/feedback", response_model=TrainingRecordOut, tags=["training"])
def submit_feedback(payload: TrainingFeedbackIn, db: Session = Depends(get_db)):
    """收集用户对生成材料 / 教程 / 章节 / 整体流程的反馈（持久化，供后续预训练）。"""
    return training_service.submit_feedback(db, payload)


@router.get("/training/records", response_model=TrainingListOut, tags=["training"])
def list_records(
    source_type: str | None = Query(default=None),
    goal_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    return training_service.list_records(
        db, source_type=source_type, goal_id=goal_id, limit=limit, offset=offset
    )


@router.get("/training/stats", response_model=TrainingStatsOut, tags=["training"])
def training_stats(db: Session = Depends(get_db)):
    return training_service.stats(db)


@router.get("/training/export", tags=["training"])
def export_training_data(
    source_type: str | None = Query(default=None),
    goal_id: str | None = Query(default=None),
    exclude_demo: bool = Query(default=False),
    fmt: str = Query(default="jsonl", pattern="^(jsonl|json)$"),
    db: Session = Depends(get_db),
):
    """导出训练数据（默认 JSONL，一行一个样本）。"""
    filename, content, media_type = training_service.export_dataset(
        db, source_type=source_type, goal_id=goal_id, exclude_demo=exclude_demo, fmt=fmt
    )
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": training_service.export_disposition(filename)},
    )


@router.get("/training/feedback-evidence", tags=["training"])
def list_feedback_evidence(
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """反馈证据闭环台账（板块五）：候选证据、重评请求、回归待办（只读，默认零分析）。"""
    # 任务书 05 切片 C：读取侧同步 dispatched 重评的任务终态（零模型）
    feedback_evidence_service.sync_revaluation_states(db)
    return feedback_evidence_service.list_evidence(limit=limit)


@router.post("/training/feedback-evidence/{event_id}/review-request", tags=["training"])
def review_request_feedback_evidence(
    event_id: str,
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """显式重评消费（任务书 05 切片 C）：事件 → 可定位题目 → 既有指定题目重评任务。

    无可定位题目保持 needs_triage（不造题）；body.question_id 表示人工甄别后的
    指认。命中时返回 task_id 与预算摘要；普通提交反馈仍零模型。
    """
    body = payload or {}
    try:
        return feedback_evidence_service.request_review(
            db, event_id,
            question_id=str(body.get("question_id")) if body.get("question_id") else None,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "feedback_evidence_not_found", "message": "反馈证据不存在"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "review_request_rejected", "message": str(exc)},
        ) from exc


@router.post("/training/feedback-evidence/revaluations/{request_id}/cancel", tags=["training"])
def cancel_feedback_revaluation(
    request_id: str,
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """取消一条重评请求：pending_review/needs_triage/dispatched → cancelled（追加，历史保留）。"""
    body = payload or {}
    try:
        return feedback_evidence_service.cancel_revaluation(
            db, request_id, reason=str(body.get("reason") or ""))
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "revaluation_not_found", "message": "重评请求不存在"},
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "invalid_transition", "message": str(exc)},
        ) from exc


@router.post("/training/feedback-evidence/recover", tags=["training"])
def recover_feedback_evidence(db: Session = Depends(get_db)):
    """显式恢复入口（任务书 05 切片 A）：把 pending/failed 的待投递证据零模型补投到台账（幂等）。"""
    return feedback_evidence_service.recover_pending(db)


@router.post("/training/feedback-evidence/{event_id}/retract", tags=["training"])
def retract_feedback_evidence(
    event_id: str,
    payload: dict | None = Body(default=None),
    db: Session = Depends(get_db),
):
    """撤回（可选修订）一条反馈证据：先落独立事件记录，再以追加事件重建投影，原文不变。"""
    body = payload or {}
    try:
        return feedback_evidence_service.retract_event(
            event_id,
            reason=str(body.get("reason") or ""),
            replacement_comment=str(body.get("replacement_comment") or ""),
            db=db,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={"code": "feedback_evidence_not_found", "message": "反馈证据不存在"},
        ) from exc

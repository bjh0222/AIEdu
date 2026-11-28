"""教程 / 生成任务 / token 用量路由。"""
from __future__ import annotations

import json
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from ..db import get_db
from ..config import settings
from ..llm.service import AIService
from ..models import GenerationJob, ModelCallLog
from ..schemas import (
    DeliveryUpdateIn,
    GenerateOut,
    GenerateResult,
    GoalUsageOut,
    JobOut,
    OneShotGenerateIn,
    OneShotGenerateOut,
    QuickStartOut,
    QuickStartRequest,
    SectionDetailOut,
    TokenUsage,
    TutorialOut,
)
from ..services import pipeline as pipeline_service
from ..services import tutorials as tutorials_service
from ..services import usage as usage_service
from .deps import get_ai

router = APIRouter()


def _state(job: GenerationJob) -> dict:
    from ..services.utils import jload

    state = jload(job.state_json, {})
    return state if isinstance(state, dict) else {}


def _run_chain_usage(db: Session, job: GenerationJob) -> dict:
    """父子 run 账本合计：主 job + 全部子 job 的 token 与调用次数。"""
    child_run_ids: list[str] = []
    child_tokens = 0
    if job.id:
        children = (
            db.query(GenerationJob)
            .filter(GenerationJob.parent_job_id == job.id)
            .all()
        )
        for child in children:
            child_tokens += int(child.total_tokens or 0)
            if child.run_id:
                child_run_ids.append(child.run_id)
    run_ids = ([job.run_id] if job.run_id else []) + child_run_ids
    calls = 0
    log_tokens = 0
    if run_ids:
        query = db.query(ModelCallLog).filter(ModelCallLog.run_id.in_(run_ids))
        calls = query.count()
        log_tokens = int(query.with_entities(func.sum(ModelCallLog.total_tokens)).scalar() or 0)
    return {
        "calls": calls,
        "total_tokens": log_tokens if calls else int(job.total_tokens or 0) + child_tokens,
        "usage_source": job.usage_source or ("estimated" if (job.total_tokens or child_tokens) else ""),
    }


def _job_out(db: Session, job: GenerationJob) -> JobOut:
    state = _state(job)
    checkpoint = state.get("checkpoint") or {}
    postprocess: dict | None = None
    child = (
        db.query(GenerationJob)
        .filter(GenerationJob.parent_job_id == job.id, GenerationJob.job_type == "knowledge_extract")
        .order_by(GenerationJob.created_at.desc())
        .first()
    ) if job.id else None
    if child is not None:
        postprocess = {"status": child.status, "job_id": child.id,
                       "stage": child.stage or ""}
    usage = _run_chain_usage(db, job)
    primary_logs = db.query(ModelCallLog).filter(ModelCallLog.job_id == job.id)
    primary = primary_logs.with_entities(func.count(ModelCallLog.id), func.sum(ModelCallLog.prompt_tokens),
                                        func.sum(ModelCallLog.completion_tokens), func.sum(ModelCallLog.total_tokens)).one()
    token_usage = TokenUsage(
        prompt_tokens=int(primary[1] or 0), completion_tokens=int(primary[2] or 0), total_tokens=int(primary[3] or 0),
        usage_source="estimated" if primary_logs.filter(ModelCallLog.usage_source != "provider").count() else "provider",
    ) if primary[0] else TokenUsage(
        prompt_tokens=job.prompt_tokens or 0, completion_tokens=job.completion_tokens or 0,
        total_tokens=job.total_tokens or 0, usage_source=job.usage_source or ("estimated" if job.total_tokens else ""))
    return JobOut(
        id=job.id,
        goal_id=job.goal_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        stage=job.stage,
        error_code=job.error_code,
        error_message=job.error_message or "",
        token_usage=token_usage,
        run_id=job.run_id,
        parent_job_id=job.parent_job_id,
        delivery={
            "status": state.get("delivery_status") or "",
            "version": state.get("version"),
        } if (state.get("delivery_status") or state.get("version")) else None,
        postprocess=postprocess,
        current_stage={
            "id": state.get("stage_id") or "",
            "status": state.get("stage_status") or "",
        } if state.get("stage_id") else None,
        stage_history=state.get("stage_history") or None,
        usage={
            **usage,
            "checkpoint_calls": checkpoint.get("calls"),
            "checkpoint_tokens": checkpoint.get("tokens"),
        } if (usage["calls"] or usage["total_tokens"] or checkpoint) else None,
        tutorial_id=state.get("tutorial_id"),
        updated_at=job.updated_at,
        optimization=state.get("quality_optimization"),
        quality_policy={"max_model_repairs_per_unit": settings.quality_max_repairs,
                        "review_retries": settings.quality_review_retries},
    )


@router.post("/goals/{goal_id}/tutorials/generate", response_model=GenerateOut, tags=["tutorials"])
def generate_tutorial(goal_id: str, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    result = tutorials_service.generate_outline(db, goal_id, ai)
    job = result["job"]
    return GenerateOut(tutorial=result["tutorial"], job_id=job.id, demo=result["demo"])


@router.post("/goals/{goal_id}/tutorials/quickstart", response_model=QuickStartOut, tags=["tutorials"])
def quickstart_tutorial(
    goal_id: str,
    payload: QuickStartRequest | None = None,
    db: Session = Depends(get_db),
    ai: AIService = Depends(get_ai),
):
    """一键生成教材：诊断 → 计划 → 验收标准（一键同意）→ 大纲 → 全部章节。"""
    return pipeline_service.quickstart(
        db,
        goal_id,
        ai,
        payload.pause_action if payload else None,
        payload.format_spec_id if payload else None,
    )


@router.post("/goals/quickstart", response_model=OneShotGenerateOut, tags=["tutorials"])
def one_shot_quickstart(payload: OneShotGenerateIn, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    """统一生成入口：一段需求 + 挡位即可创建任务并自动完成规划与教程生成。"""
    return pipeline_service.create_and_quickstart(db, payload, ai)


@router.get("/goals/{goal_id}/tutorials", response_model=list[TutorialOut], tags=["tutorials"])
def list_tutorials(goal_id: str, db: Session = Depends(get_db)):
    return tutorials_service.list_tutorials(db, goal_id)


@router.get("/tutorials/{tutorial_id}", response_model=TutorialOut, tags=["tutorials"])
def get_tutorial(tutorial_id: str, db: Session = Depends(get_db)):
    return tutorials_service.get_tutorial(db, tutorial_id)


@router.get("/tutorials/{tutorial_id}/sections/{section_id}", response_model=SectionDetailOut, tags=["tutorials"])
def get_section(tutorial_id: str, section_id: str, db: Session = Depends(get_db)):
    """章节详情：供「教程生成每一步都能点进详情」的深层链接使用。"""
    return tutorials_service.get_section(db, tutorial_id, section_id)


@router.post("/tutorials/{tutorial_id}/sections/generate", response_model=GenerateOut, tags=["tutorials"])
def generate_sections(tutorial_id: str, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    result = tutorials_service.start_sections(db, tutorial_id, ai)
    job = result["job"]
    return GenerateOut(tutorial=result["tutorial"], job_id=job.id, demo=result["demo"])


@router.post("/tutorials/{tutorial_id}/sections/{section_id}/regenerate", response_model=TutorialOut, tags=["tutorials"])
def regenerate_section(tutorial_id: str, section_id: str, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    result = tutorials_service.regenerate_section(db, tutorial_id, section_id, ai)
    return result["tutorial"]


@router.post("/tutorials/{tutorial_id}/restore/{version}", response_model=TutorialOut, tags=["tutorials"])
def restore_version(tutorial_id: str, version: int, db: Session = Depends(get_db)):
    return tutorials_service.restore_version(db, tutorial_id, version)


@router.get("/tutorials/{tutorial_id}/export", tags=["tutorials"])
def export_tutorial(tutorial_id: str, db: Session = Depends(get_db)):
    """导出当前教程版本为 Markdown 文件。"""
    filename, markdown = tutorials_service.export_markdown(db, tutorial_id)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return Response(content=markdown, media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/tutorials/{tutorial_id}/export/cheatsheet", tags=["tutorials"])
def export_cheatsheet(tutorial_id: str, db: Session = Depends(get_db)):
    """导出配套知识速查文件（Obsidian Markdown；文件名与教程内双链一致）。"""
    filename, markdown = tutorials_service.export_cheatsheet(db, tutorial_id)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return Response(content=markdown, media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/tutorials/{tutorial_id}/export/exercises", tags=["tutorials"])
def export_extra_exercises(tutorial_id: str, db: Session = Depends(get_db)):
    """导出独立额外练习文件（用户开启选项后生成；含集中放置的参考答案）。"""
    filename, markdown = tutorials_service.export_extra_exercises(db, tutorial_id)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    return Response(content=markdown, media_type="text/markdown; charset=utf-8", headers=headers)


@router.get("/goals/{goal_id}/usage", response_model=GoalUsageOut, tags=["tutorials"])
def goal_usage(goal_id: str, db: Session = Depends(get_db)):
    """目标维度的 token 消耗：总用量、按功能、按教程。"""
    return usage_service.goal_usage(db, goal_id)


@router.get("/generation-jobs/{job_id}", response_model=JobOut, tags=["tutorials"])
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(GenerationJob, job_id)
    if job is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "生成任务不存在"})
    # 任务书 04：读取侧顺带做中断清点（心跳过期的遗留 running 标 interrupted，
    # 不自动续跑；见 ADR-027 与 tutorials.mark_orphaned_jobs_interrupted）。
    tutorials_service.mark_orphaned_jobs_interrupted(db, job_ids=[job.id])
    db.refresh(job)
    return _job_out(db, job)


@router.get("/tutorials/{tutorial_id}/generation-jobs", response_model=list[JobOut], tags=["tutorials"])
def tutorial_jobs(tutorial_id: str, db: Session = Depends(get_db)):
    tutorial = tutorials_service._get_tutorial(db, tutorial_id)
    jobs = (db.query(GenerationJob).filter(
        GenerationJob.goal_id == tutorial.goal_id,
        GenerationJob.job_type.in_(("tutorial_sections", "tutorial_quality", "tutorial_companions")),
    ).order_by(GenerationJob.created_at.desc()).limit(30).all())
    jobs = [j for j in jobs if _state(j).get("tutorial_id") in (None, tutorial_id)]
    tutorials_service.mark_orphaned_jobs_interrupted(db, job_ids=[j.id for j in jobs])
    for job in jobs:
        db.refresh(job)
    return [_job_out(db, j) for j in jobs[:10]]


@router.post("/tutorials/{tutorial_id}/quality/optimize", response_model=GenerateResult, tags=["tutorials"])
def optimize_tutorial(tutorial_id: str, db: Session = Depends(get_db), ai: AIService = Depends(get_ai)):
    result = tutorials_service.start_quality_optimization(db, tutorial_id, ai)
    return GenerateResult(job_id=result["job"].id, demo=result["demo"])


@router.post("/generation-jobs/{job_id}/cancel", tags=["tutorials"])
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    """取消一次运行中的生成：不派发新任务，在途响应安全落账（ADR-027）。"""
    from fastapi import HTTPException

    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "生成任务不存在"})
    result = tutorials_service.cancel_run(db, job)
    return result


@router.get("/generation-jobs/{job_id}/events", tags=["tutorials"])
def job_events(job_id: str, db: Session = Depends(get_db)):
    """SSE：回放生成任务状态（同步生成完成后的一次性快照 + 完成事件）。"""
    job = db.get(GenerationJob, job_id)
    if job is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail={"code": "job_not_found", "message": "生成任务不存在"})

    snapshot = {
        "id": job.id,
        "goal_id": job.goal_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "stage": job.stage,
        "error_code": job.error_code,
        "token_usage": {
            "prompt_tokens": job.prompt_tokens or 0,
            "completion_tokens": job.completion_tokens or 0,
            "total_tokens": job.total_tokens or 0,
        },
    }

    def event_stream():
        yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post("/tutorials/{tutorial_id}/delivery", response_model=TutorialOut, tags=["tutorials"])
def mark_tutorial_delivery(
    tutorial_id: str,
    payload: DeliveryUpdateIn,
    db: Session = Depends(get_db),
):
    """Obsidian 插件写入 Vault 后回写交付结果；不改变教程生成状态。"""
    return tutorials_service.mark_delivery(db, tutorial_id, payload)

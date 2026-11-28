"""经验库 API；外部评注不提供修改接口。"""
from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.orm import Session

from ..db import get_db
from ..llm.service import AIService
from ..models import (
    DesignPrinciple, DesignPrincipleLink, DesignPrincipleRevision, EvaluationFramework,
    PropositionAnalysisJob, PropositionBatch, PropositionEvaluation,
    PropositionGuidanceTrace, PropositionQuestion,
)
from ..schemas import PropositionAnalyseRequest, PropositionBatchImport, PropositionConfigPatch, PropositionFrameworkImport, PropositionGuidanceRequest, PropositionPrinciplePatch, PropositionLinkReview
from ..services import proposition_knowledge as knowledge
from ..services.utils import jload

router = APIRouter(tags=["proposition"])


@router.get("/proposition/overview")
def overview(db: Session = Depends(get_db)):
    knowledge.ensure_framework(db)
    def count(model, *filters):
        return db.query(model).filter(*filters).count()
    return {
        "counts": {
            "batches": count(PropositionBatch), "questions": count(PropositionQuestion),
            "evaluated": count(PropositionQuestion, PropositionQuestion.analysis_status == "analysed"),
            "parse_failed": count(PropositionQuestion, PropositionQuestion.parse_status == "failed"),
            "analysis_failed": count(PropositionQuestion, PropositionQuestion.analysis_status == "failed"),
            "principles": count(DesignPrinciple), "confirmed": count(DesignPrinciple, DesignPrinciple.status == "confirmed"),
            "auto_accepted": count(DesignPrinciple, DesignPrinciple.status == "auto_accepted"),
            "proposed": count(DesignPrinciple, DesignPrinciple.status == "proposed"),
        },
        "frameworks": [knowledge.framework_dict(f, source=False) for f in db.query(EvaluationFramework).order_by(EvaluationFramework.created_at.desc()).all()],
        "recent_jobs": [knowledge.job_dict(j) for j in db.query(PropositionAnalysisJob).order_by(PropositionAnalysisJob.created_at.desc()).limit(10)],
    }


@router.get("/proposition/frameworks")
def frameworks(db: Session = Depends(get_db)):
    knowledge.ensure_framework(db)
    return {"items": [knowledge.framework_dict(f) for f in db.query(EvaluationFramework).order_by(EvaluationFramework.created_at.desc()).all()]}


@router.post("/proposition/frameworks/import")
def import_framework(payload: PropositionFrameworkImport, db: Session = Depends(get_db)):
    return knowledge.import_framework(db, payload.title, payload.domain_key, payload.markdown, AIService(db=db))


@router.post("/proposition/frameworks/{framework_id}/activate")
def activate_framework(framework_id: str, db: Session = Depends(get_db)):
    return knowledge.activate_framework(db, framework_id)


@router.post("/proposition/batches/import")
def import_batch(payload: PropositionBatchImport, db: Session = Depends(get_db)):
    return knowledge.import_batch(db, payload)


@router.get("/proposition/batches")
def batches(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = db.query(PropositionBatch)
    return {"total": query.count(), "items": [knowledge.batch_dict(db, b) for b in query.order_by(PropositionBatch.created_at.desc()).limit(limit).offset(offset)]}


@router.get("/proposition/batches/{batch_id}")
def batch(batch_id: str, db: Session = Depends(get_db)):
    row = db.get(PropositionBatch, batch_id)
    if not row:
        knowledge.missing("上传批次不存在")
    return knowledge.batch_dict(db, row, details=True)


@router.post("/proposition/batches/{batch_id}/analyse")
def analyse(batch_id: str, payload: PropositionAnalyseRequest, db: Session = Depends(get_db)):
    return knowledge.start_analysis(db, batch_id, force=payload.force)


@router.delete("/proposition/batches/{batch_id}")
def delete_batch(batch_id: str, db: Session = Depends(get_db)):
    return knowledge.delete_uploaded(db, batch_id=batch_id)


@router.get("/proposition/jobs/{job_id}")
def job(job_id: str, db: Session = Depends(get_db)):
    row = db.get(PropositionAnalysisJob, job_id)
    if not row:
        knowledge.missing("分析任务不存在")
    return knowledge.job_dict(row)


@router.post("/proposition/jobs/{job_id}/cancel")
def cancel_job(job_id: str, db: Session = Depends(get_db)):
    row = db.get(PropositionAnalysisJob, job_id)
    if not row:
        knowledge.missing("分析任务不存在")
    if row.status in ("queued", "running"):
        row.status = "cancelled"
        db.commit()
    return knowledge.job_dict(row)


@router.get("/proposition/questions")
def questions(batch_id: str = "", limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    query = db.query(PropositionQuestion)
    if batch_id:
        query = query.filter(PropositionQuestion.batch_id == batch_id)
    return {"total": query.count(), "items": [knowledge.question_dict(q) for q in query.order_by(PropositionQuestion.created_at.desc()).limit(limit).offset(offset)]}


@router.get("/proposition/questions/{question_id}")
def question(question_id: str, db: Session = Depends(get_db)):
    row = db.get(PropositionQuestion, question_id)
    if not row:
        knowledge.missing("题目不存在")
    result = knowledge.question_dict(row)
    result["evaluations"] = [knowledge.evaluation_dict(e) for e in db.query(PropositionEvaluation).filter(PropositionEvaluation.question_id == question_id).order_by(PropositionEvaluation.analysis_version.desc())]
    result["assessment"] = knowledge.assessment_for_question(db, question_id)
    return result


@router.get("/proposition/questions/{question_id}/assessment")
def item_assessment(question_id: str, db: Session = Depends(get_db)):
    return knowledge.assessment_for_question(db, question_id)


@router.post("/proposition/questions/{question_id}/feedback")
def item_feedback(question_id: str, payload: dict = Body(...), db: Session = Depends(get_db)):
    return knowledge.receive_feedback(db, question_id, payload)


@router.post("/proposition/questions/{question_id}/retry")
def retry_question(question_id: str, db: Session = Depends(get_db)):
    return knowledge.retry_question(db, question_id)


@router.delete("/proposition/questions/{question_id}")
def delete_question(question_id: str, db: Session = Depends(get_db)):
    return knowledge.delete_uploaded(db, question_id=question_id)


@router.get("/proposition/principles/{principle_id}/history")
def history(principle_id: str, db: Session = Depends(get_db)):
    if not db.get(DesignPrinciple, principle_id):
        knowledge.missing("命题经验不存在")
    result = []
    for row in db.query(DesignPrincipleRevision).filter(DesignPrincipleRevision.principle_id == principle_id).order_by(DesignPrincipleRevision.created_at.desc()):
        result.append({"id": row.id, "version": row.version, "action": row.action, "actor": row.actor, "rationale": row.rationale, "snapshot": jload(row.snapshot, {}), "created_at": row.created_at})
    links = [{"id": r.id, "source_id": r.source_id, "target_id": r.target_id, "relation": r.relation, "status": r.status, "rationale": r.rationale} for r in db.query(DesignPrincipleLink).filter((DesignPrincipleLink.source_id == principle_id) | (DesignPrincipleLink.target_id == principle_id))]
    return {"items": result, "links": links}


@router.patch("/proposition/principles/{principle_id}")
def revise_principle(principle_id: str, payload: PropositionPrinciplePatch, db: Session = Depends(get_db)):
    from ..services.proposition import _principle_dict
    from ..services.utils import jdump
    row = db.get(DesignPrinciple, principle_id)
    if not row:
        knowledge.missing("命题经验不存在")
    values = payload.model_dump(exclude_none=True, exclude={"rationale"})
    if payload.scope is not None:
        from ..services.proposition_mechanisms import principle_mechanism, retrieval_scope
        values["mechanism"] = principle_mechanism({"mechanism": values.get("mechanism", row.mechanism), "scope": payload.scope})
        values["scope"] = retrieval_scope(payload.scope)
    knowledge._revision(db, row, "before_revision", "human", payload.rationale)
    for key, value in values.items():
        setattr(row, key, jdump(value) if key in ("scope", "counterexamples") else value)
    row.version = (row.version or 1) + 1
    row.status = "proposed"
    row.approval_source = ""
    if payload.scope is not None:
        row.scope_knowledge_points = jdump(values["scope"].get("knowledge_points", []))
    knowledge._revision(db, row, "revised", "human", payload.rationale)
    db.commit()
    return _principle_dict(row)


@router.post("/proposition/principle-links/{link_id}/review")
def review_link(link_id: str, payload: PropositionLinkReview, db: Session = Depends(get_db)):
    row = db.get(DesignPrincipleLink, link_id)
    if not row:
        knowledge.missing("经验关系不存在")
    row.status = "confirmed" if payload.action == "confirm" else "rejected"
    # 原AI理由保留，人工决定记录在两个经验的历史里。
    for pid in (row.source_id, row.target_id):
        principle = db.get(DesignPrinciple, pid)
        if principle:
            knowledge._revision(db, principle, "link_" + payload.action, "human", payload.rationale)
    db.commit()
    return {"id": row.id, "status": row.status, "relation": row.relation}


@router.post("/proposition/guidance/preview")
def preview(payload: PropositionGuidanceRequest, db: Session = Depends(get_db)):
    from fastapi import HTTPException
    from ..services.proposition_retrieval import public_guidance_result, retrieve_guidance
    from ..services.proposition_evidence import adapt_legacy_task_context, retrieve_evidence_guidance
    config = knowledge.get_config(db)
    if payload.budget_chars is not None and config["budget_chars"] != 0:
        config["budget_chars"] = config["complex_budget_chars"] = payload.budget_chars
    task_context = payload.task_context
    if task_context is None and payload.goal_id:
        from ..models import LearningGoal
        goal = db.get(LearningGoal, payload.goal_id)
        if goal is None:
            raise HTTPException(404, detail={"code": "goal_not_found", "message": "目标不存在"})
        task_context = adapt_legacy_task_context(goal,
            {"id": payload.unit_id or payload.section_id or "unit-1", "knowledge_points": payload.knowledge_points,
             "question_types": payload.question_types}, task_kind=payload.task_kind,
            tutorial_id=payload.tutorial_id, section_id=payload.unit_id or payload.section_id or None)
    if payload.strategy is not None and task_context is None:
        raise HTTPException(422, detail={"code": "generation_contract_invalid", "message": "GenerationStrategy requires TaskContext"})
    if task_context is not None:
        try:
            return public_guidance_result(retrieve_evidence_guidance(db, task_context, payload.strategy,
                stage=payload.stage or ("planning" if payload.task_kind in {"planning", "blueprint", "synthesis"} else "unit"),
                unit_id=payload.unit_id or payload.section_id or None, config=config, record=False))
        except ValueError as exc:
            raise HTTPException(422, detail={"code": "generation_contract_invalid", "message": str(exc)}) from exc
    return public_guidance_result(retrieve_guidance(db, payload.model_dump(exclude_none=True), config, record=False))


@router.get("/proposition/guidance/traces")
def traces(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    from ..services.proposition_retrieval import public_guidance_result
    query = db.query(PropositionGuidanceTrace)
    return {"total": query.count(), "items": [{"id": r.id, "goal_id": r.goal_id, "tutorial_id": r.tutorial_id, "section_id": r.section_id, "task_kind": r.task_kind, "context": jload(r.context, {}), "result": public_guidance_result(jload(r.result, {})), "created_at": r.created_at} for r in query.order_by(PropositionGuidanceTrace.created_at.desc()).limit(limit).offset(offset)]}


@router.get("/proposition/config")
def config(db: Session = Depends(get_db)):
    return knowledge.get_config(db, public=True)


@router.get("/proposition/embedding-health")
def embedding_health(db: Session = Depends(get_db)):
    """R2-1：embedding endpoint/model 只读健康探针（不自动启动 Ollama、不写库）。

    返回 configured/enabled/reachable/model_ok/dimension/latency_ms/reason/
    degraded_explanation，供 UI 在生成前解释「本轮语义检索是否可用」。
    """
    from ..services.proposition_retrieval import embedding_health as probe

    return probe(knowledge.get_config(db))


@router.patch("/proposition/config")
def patch_config(payload: PropositionConfigPatch, db: Session = Depends(get_db)):
    return knowledge.save_config(db, payload.model_dump(exclude_none=True))

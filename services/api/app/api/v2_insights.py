"""Owner-only insights; write requests use the existing CSRF/action-key boundary."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response, Query
from pydantic import Field

from ..db import SessionLocal
from ..v2 import insights
from ..v2.contracts import Contract, digest
from ..v2.runtime import dump
from ..v2.security import action_key, owner
from ..v2.responses import KnowledgeGraph, RunManifest

router = APIRouter(prefix="/v2", tags=["insights.v2"], dependencies=[Depends(owner)])


class CardExtraction(Contract):
    version_id: str
    content_hash: str = Field(pattern="^[a-f0-9]{64}$")
    expected_revision: int = Field(default=0, ge=0)
    mode: Literal["extractive", "configured"] = "extractive"


class CardRevision(Contract):
    expected_revision: int = Field(ge=1)
    quote_hash: str
    status: Literal["candidate", "confirmed", "rejected", "retracted", "unknown"]
    reason: str = Field(default="", max_length=4000)


class LearnerFeedback(Contract):
    expected_revision: int = Field(ge=0)
    event_id: str = Field(min_length=1, max_length=80)
    kind: Literal["preference", "confusion", "bookmark", "correction", "mastery_self_report", "retract"]
    text: str = Field(default="", max_length=4000)
    target_event_id: str | None = None
    task_id: str | None = None
    version_id: str | None = None
    unit_id: str | None = None
    content_hash: str | None = None


class NetworkQuote(Contract):
    url: str = Field(max_length=2048)
    quote: str = Field(min_length=1, max_length=4000)
    expected_source_hash: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")


@router.get("/verifier-capabilities")
def verifier_capabilities():
    from ..v2.network_evidence import capabilities
    return {"network_source_quote": capabilities(), "structured_claim_contract": {
        "method_version": "structured-claim-contract.v1", "domains": ["math_logic", "cs_it", "biology_life", "chemistry_materials"],
        "scope": "literal formal data only; prose translation, execution and professional truth unsupported"}}


@router.get("/knowledge/cards")
def search_cards(domain: str | None = None, q: str = Query(default="", max_length=1000), limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    with SessionLocal() as db:
        values = insights.searchable_cards(db, domain, q)
        return {"items": values[offset:offset + limit], "total": len(values), "limit": limit, "offset": offset,
                "eligibility": "current_confirmed_non_demo_source_only", "professional_quality": "not_calibrated"}


@router.post("/source-verifications")
def verify_source(payload: NetworkQuote, key=Depends(action_key)):
    from ..v2.network_evidence import verify_quote
    from ..v2.runtime import write
    body = payload.model_dump()
    with SessionLocal() as db:
        prior = insights.action(db, key, body)
        if prior is not None:
            return prior
    result = verify_quote(payload.url, payload.quote, payload.expected_source_hash)
    with write() as db:
        prior = insights.action(db, key, body)
        return prior if prior is not None else insights.receipt(db, key, body, result)


def conditional(result, request):
    etag = '"' + digest(result) + '"'
    headers = {"ETag": etag, "Cache-Control": "private,no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=dump(result), media_type="application/json", headers=headers)


@router.get("/tasks/{task_id}/knowledge-graph", response_model=KnowledgeGraph)
def graph(task_id: str, request: Request, run_id: str | None = None, version_id: str | None = None):
    return conditional(insights.knowledge_graph(task_id, run_id, version_id), request)


@router.get("/tasks/{task_id}/runs/{run_id}/manifest", response_model=RunManifest)
def manifest(task_id: str, run_id: str, request: Request):
    return conditional(insights.run_manifest(task_id, run_id), request)


@router.get("/tasks/{task_id}/versions/{version_id}/cards")
def cards(task_id: str, version_id: str):
    return insights.read_cards(task_id, version_id)


@router.post("/tasks/{task_id}/card-extractions", status_code=202)
def extract(task_id: str, payload: CardExtraction, background: BackgroundTasks, key=Depends(action_key)):
    result = insights.request_cards(task_id, payload, key)
    if result.get("run_id"):
        from ..v2.worker import execute_run
        background.add_task(execute_run, result["run_id"])
    return result


@router.put("/tasks/{task_id}/versions/{version_id}/cards/{card_id}")
def revise(task_id: str, version_id: str, card_id: str, payload: CardRevision, key=Depends(action_key)):
    return insights.revise_card(task_id, version_id, card_id, payload, key)


@router.get("/learning-profile")
def profile():
    with SessionLocal() as db:
        return insights.public_profile(db)


@router.post("/learning-feedback", status_code=201)
def feedback(payload: LearnerFeedback, key=Depends(action_key)):
    return insights.add_feedback(payload, key)

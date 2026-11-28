"""Owner/CSRF-protected synthesis router; execution requires a worker marker."""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import Field
from typing import Any, Literal

from ..v2 import synthesis_requests
from ..v2.contracts import Contract
from ..v2.security import action_key, owner
from ..v2.responses import Output
from ..v2.synthesis_contracts import Proposal, Review

router = APIRouter(prefix="/v2", tags=["synthesis.v2"], dependencies=[Depends(owner)])


class SynthesisRequest(Contract):
    run_id: str
    expected_run_revision: int = Field(ge=0)
    version_id: str
    content_hash: str = Field(pattern="^[a-f0-9]{64}$")
    quality_hash: str = Field(pattern="^[a-f0-9]{64}$")
    source_bundle_hash: str = Field(pattern="^[a-f0-9]{64}$")
    expected_synthesis_revision: int = Field(ge=0)


class SynthesisPreview(Output):
    task_id: str
    run_id: str
    state_revision: int
    version_id: str
    content_hash: str
    quality_hash: str
    source_bundle_hash: str
    expected_synthesis_revision: int
    coverage: dict[str, Any]
    status: Literal["source_ready_not_executed"]


class SynthesisAccepted(Output):
    task_id: str
    run_id: str
    parent_run_id: str
    version_id: str
    source_bundle_hash: str
    status: Literal["queued"]
    paid: Literal[True]


class SynthesisMissing(Output):
    task_id: str
    version_id: str
    content_hash: str
    status: Literal["not_generated"]
    revision: Literal[0]
    source_is_current: bool


class SynthesisSourceUnit(Output):
    unit_id: str
    title: str
    content_hash: str


class SynthesisSourceSpan(Output):
    source_span_id: str
    unit_id: str
    unit_hash: str
    start_byte: int
    end_byte: int
    quote_hash: str


class SynthesisSourceIndex(Output):
    units: list[SynthesisSourceUnit]
    spans: list[SynthesisSourceSpan]


class SynthesisBoundProposal(Output):
    proposal: Proposal
    proposal_hash: str
    coverage: dict[str, Any]


class SynthesisBoundReview(Output):
    review: Review
    review_hash: str
    automatic_status: str


class SynthesisRound(Output):
    round: int
    proposal: SynthesisBoundProposal
    research: dict[str, Any]
    review: SynthesisBoundReview
    generation_audit: dict[str, Any]
    review_audit: dict[str, Any]
    automatic_status: str


class SynthesisResult(Output):
    task_id: str
    run_id: str
    revision: int
    method_version: str
    source_version_id: str
    source_content_hash: str
    source_quality_hash: str
    source_bundle_hash: str
    source_plan_hash: str
    result_hash: str
    source_index: SynthesisSourceIndex
    source_is_current: bool
    source_demo: bool
    demo: bool
    provenance: str
    check_plan: dict[str, Any]
    rounds: list[SynthesisRound]
    latest_proposal_hash: str
    automatic_status: Literal["satisfied", "needs_repair", "needs_review"]
    stop_reason: str
    status: Literal["candidate"]
    calibration_status: Literal["not_calibrated"]
    professional_certification: Literal["not_established"]
    source_quality_promotion: Literal[False]
    final_delivery_eligible: Literal[False]


@router.get("/tasks/{task_id}/synthesis-source", response_model=SynthesisPreview)
def preview(task_id: str, run_id: str, version_id: str):
    return synthesis_requests.preview(task_id, run_id, version_id)


@router.post("/tasks/{task_id}/syntheses", status_code=202, response_model=SynthesisAccepted)
def start(task_id: str, payload: SynthesisRequest, background: BackgroundTasks, key=Depends(action_key)):
    result = synthesis_requests.request(task_id, payload, key)
    from ..v2.worker import execute_run
    background.add_task(execute_run, result["run_id"])
    return result


@router.get("/tasks/{task_id}/versions/{version_id}/synthesis", response_model=SynthesisMissing | SynthesisResult)
def read(task_id: str, version_id: str):
    return synthesis_requests.read(task_id, version_id)

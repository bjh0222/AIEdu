"""Owner-scoped research evidence reads never trigger network or paid inference."""
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..db import SessionLocal
from ..v2.runtime import job, load
from ..v2.security import owner
from ..v2.storage import BlobStore
from ..v2.contracts import DomainError
from ..v2.rubric_research import Dimension

router = APIRouter(prefix="/v2/research", tags=["research.v2"], dependencies=[Depends(owner)])


class ResearchEvidence(BaseModel):
    run_id: str
    status: Literal["not_requested", "running", "completed", "incomplete"]
    artifacts: dict[str, Any]
    result: dict[str, Any] | None
    inference_triggered: Literal[False] = False


class RubricAsset(BaseModel):
    model_config = ConfigDict(extra="allow")
    rubric_id: str
    version: int
    dimensions: list[Dimension]
    source_claims: list[dict[str, Any]]
    calibration_status: Literal["not_calibrated"]
    aggregate_score: None
    threshold: None


@router.get("/{run_id}", response_model=ResearchEvidence)
def research_evidence(run_id: str):
    with SessionLocal() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        result = state.get("research_result")
        if not result and state.get("research_artifacts", {}).get("result"):
            result = load(BlobStore().get(state["research_artifacts"]["result"]["blob_hash"]).decode())
        return {"run_id": run_id, "status": "completed" if result else
            "running" if obj.status in {"running", "queued"} and state["context"].get("research_request", {}).get("enabled") else
            "incomplete" if state.get("research_artifacts") else "not_requested",
            "artifacts": state.get("research_artifacts", {}), "result": result, "inference_triggered": False}


@router.get("/{run_id}/artifacts/{artifact_key}")
def research_artifact(run_id: str, artifact_key: str):
    with SessionLocal() as db:
        state = load(job(db, run_id).state_json)
        reference = state.get("research_artifacts", {}).get(artifact_key)
        if not reference:
            raise DomainError("RESEARCH_ARTIFACT_NOT_FOUND", 404)
        value = load(BlobStore().get(reference["blob_hash"]).decode())
        return {"run_id": run_id, "artifact_key": artifact_key, "reference": reference, "content": value, "inference_triggered": False}


@router.get("/{run_id}/rubric", response_model=RubricAsset)
def research_rubric(run_id: str):
    result = research_evidence(run_id)["result"]
    if not result or not result.get("rubric"):
        raise DomainError("RESEARCH_RUBRIC_NOT_AVAILABLE", 404)
    return result["rubric"]

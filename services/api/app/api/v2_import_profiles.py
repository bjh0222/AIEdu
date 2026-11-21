"""Read-only, owner-scoped imported-understanding contract (never invokes AI)."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ..db import SessionLocal
from ..models import PropositionConfig
from ..v2.runtime import load
from ..v2.security import owner

router = APIRouter(prefix="/v2", tags=["import-understanding.v2"], dependencies=[Depends(owner)])


class UnderstandingAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    version: int
    hash: str
    content: dict[str, Any]
    calibration_status: Literal["not_calibrated"]
    aggregate_score: None
    threshold: None


class UnderstandingPage(BaseModel):
    items: list[UnderstandingAsset]
    total: int
    inference_triggered: Literal[False] = False


@router.get("/import-understanding", response_model=UnderstandingPage)
def imported_understanding():
    with SessionLocal() as db:
        heads = db.query(PropositionConfig).filter(PropositionConfig.id.like("import-profile-head:%")).all()
        items = []
        for row in heads:
            head = load(row.settings_json)
            value = db.get(PropositionConfig, f"import-profile:{head['id']}:{head['version']}")
            if value:
                items.append(load(value.settings_json))
        return {"items": items, "total": len(items), "inference_triggered": False}

"""健康检查。"""
from __future__ import annotations

from fastapi import APIRouter

from ..model_settings import runtime_settings
from ..schemas import HealthOut
from ..v2.review_contract import review_protocol_manifest

router = APIRouter()
_PROCESS_QUALITY_PROTOCOL_HASH = review_protocol_manifest()["manifest_hash"]


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    settings = runtime_settings()
    return HealthOut(demo=not settings.llm_configured, llm_configured=settings.llm_configured,
                     quality_protocol_hash=_PROCESS_QUALITY_PROTOCOL_HASH)

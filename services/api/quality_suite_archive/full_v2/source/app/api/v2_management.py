from __future__ import annotations

from typing import Any, Literal
from fastapi import APIRouter, Depends
from pydantic import Field
from ..db import SessionLocal
from ..v2.contracts import Contract, DomainError
from ..v2.security import action_key, owner
from ..v2 import management

router = APIRouter(prefix="/v2", tags=["management.v2"], dependencies=[Depends(owner)])
ManagedOperation = Literal["archive", "restore", "label", "trash", "restore_trash", "purge"]


class ManagedTarget(Contract):
    kind: str
    id: str
    expected_hash: str
    impact_hash: str


class ManagedAction(Contract):
    items: list[ManagedTarget] = Field(min_length=1, max_length=100)
    action: ManagedOperation
    label: str | None = Field(default=None, max_length=200)
    acknowledge_references: bool = False
    confirmation_names: dict[str, str] = Field(default_factory=dict)


class ManagedOverlay(Contract):
    revision: int
    archived: bool
    trashed: bool
    label: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class ManagedResource(Contract):
    kind: str
    id: str
    label: str
    resource_hash: str
    metadata: dict[str, Any]
    management: ManagedOverlay
    source_edit: str
    hard_delete: str
    source_fingerprint: str | None = None


class ManagedPage(Contract):
    items: list[ManagedResource]
    total: int
    offset: int
    limit: int
    next_offset: int | None


class ManagedReference(Contract):
    kind: str
    id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    state: str | None = None
    label: str | None = None
    fields: list[str] = Field(default_factory=list)


class ManagedBlocker(Contract):
    code: str
    reason: str


class ManagedImpact(Contract):
    resource: ManagedResource
    impact_hash: str
    active: bool
    active_blockers: list[dict[str, Any]] = Field(default_factory=list)
    stale_execution_releasable: bool = False
    references: list[ManagedReference]
    purge_allowed: bool
    purge_blockers: list[ManagedBlocker]
    shared_blob_references: list[ManagedReference] = Field(default_factory=list)
    effects: dict[str, str]
    policy: str


class ManagedItemResult(Contract):
    kind: str
    id: str
    purged: bool | None = None
    management: ManagedOverlay | None = None


class ManagedReceipt(Contract):
    operation: ManagedOperation
    items: list[ManagedItemResult]
    receipt_hash: str
    cleanup_pending: list[str]
    source_bytes_deleted: bool
    batch_semantics: Literal["atomic_all_or_nothing"]
    blob_hashes: list[str] = Field(default_factory=list)
    restore: str | None = None


@router.get("/managed-resources")
def kinds():
    return {"items": [{"kind": kind, "label": management.LABELS[kind]} for kind in management.MODELS] + [{"kind": "model-profiles", "label": "模型配置"}],
        "additional_entities": {"learner_profile": "/api/v2/learning-profile", "model_configuration": "/api/v2/model-settings",
            "quality_rubrics": "/api/v2/quality-profiles", "knowledge_cards": "/api/v2/tasks/{task_id}/versions/{version_id}/cards"}}


@router.get("/managed-resources/{kind}", response_model=ManagedPage, response_model_exclude_unset=True)
def catalog(kind: str, search: str = "", offset: int = 0, limit: int = 50, archived: bool = False, trashed: bool = False):
    return management.catalog(kind, search, offset, limit, archived, trashed)


@router.get("/managed-resources/{kind}/{object_id}/impact", response_model=ManagedImpact, response_model_exclude_unset=True)
def impact(kind: str, object_id: str):
    with SessionLocal() as db:
        return management.impact(db, kind, object_id)


@router.post("/managed-resource-actions", response_model=ManagedReceipt, response_model_exclude_unset=True)
def change(payload: ManagedAction, key=Depends(action_key)):
    return management.change(payload, key)


@router.post("/managed-resource-cleanup", response_model=ManagedReceipt, response_model_exclude_unset=True)
def cleanup(key=Depends(action_key)):
    return management.cleanup(key)


class UnderstandingMaterialContext(Contract):
    source_id: str
    filename: str | None
    task_id: str | None
    source_hash: str
    current_hash: str | None
    status: Literal["current", "stale", "missing", "trashed"]


class UnderstandingContext(Contract):
    profile_id: str
    version: int
    hash: str
    status: Literal["current", "stale", "unknown"]
    updated_at: str | None
    updated_at_source: Literal["asset_wrapper", "research_result", "generation_job", "unknown"] = "unknown"
    materials: list[UnderstandingMaterialContext]
    inference_triggered: Literal[False] = False


@router.get("/managed-understanding-context/{profile_id}", response_model=UnderstandingContext)
def understanding_context(profile_id: str, version: int):
    """Add source labels/expiry to Agent2's immutable profile without rewriting it."""
    from ..models import GoalMaterial, GenerationJob
    from ..v2.insights import record
    from ..v2.runtime import load
    from ..v2.storage import BlobStore
    with SessionLocal() as db:
        value = record(db, f"import-profile:{profile_id}:{version}")
        if not value:
            raise DomainError("IMPORT_ASSET_NOT_FOUND", 404)
        head = record(db, "import-profile-head:"+profile_id)
        materials = []
        for ref in value["content"].get("material_refs", []):
            row = db.get(GoalMaterial, ref["source_id"])
            trashed = record(db, management.key_of("materials", ref["source_id"]), {}).get("trashed")
            status = "missing" if not row else "trashed" if trashed else "current" if row.extracted_hash == ref["source_hash"] else "stale"
            materials.append({"source_id": ref["source_id"], "filename": row.filename if row else None,
                "task_id": row.goal_id if row else None, "source_hash": ref["source_hash"],
                "current_hash": row.extracted_hash if row else None, "status": status})
        updated = []
        for run in db.query(GenerationJob).filter_by(job_type="task_v2"):
            state = load(run.state_json)
            result = state.get("research_result")
            if not result and state.get("research_artifacts", {}).get("result"):
                try:
                    result = load(BlobStore().get(state["research_artifacts"]["result"]["blob_hash"]).decode())
                except DomainError:
                    continue
            reference = (result.get("asset_refs") or {}).get("profile") if isinstance(result, dict) else None
            result_profile = result.get("profile") if isinstance(result, dict) else None
            result_profile_id = (
                result_profile.get("profile_id") or result_profile.get("id")
                if isinstance(result_profile, dict) else None
            )
            if reference == {k: value[k] for k in ("id", "version", "hash")} or result_profile_id == profile_id:
                if result.get("completed_at"):
                    updated.append((result["completed_at"], "research_result"))
                elif run.updated_at:
                    updated.append((run.updated_at, "generation_job"))
        wrapper_time = value.get("updated_at") or value.get("created_at")
        if wrapper_time:
            updated_at, updated_at_source = wrapper_time, "asset_wrapper"
        elif updated:
            updated_at, updated_at_source = max(updated, key=lambda item: item[0])
        else:
            updated_at, updated_at_source = None, "unknown"
        return {"profile_id": profile_id, "version": version, "hash": value["hash"],
            "status": "stale" if head != {k: value[k] for k in ("id", "version", "hash")} or any(m["status"] != "current" for m in materials) else "current" if materials else "unknown",
            "updated_at": updated_at, "updated_at_source": updated_at_source,
            "materials": materials, "inference_triggered": False}

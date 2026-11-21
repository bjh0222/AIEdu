"""Independent C->B optimization source contracts and routes."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import Field

from ..v2.contracts import Contract, DomainError, Revision
from ..v2.optimization import request_optimization, set_policy
from ..v2.security import action_key, owner
from ..v2.worker import execute_run

router = APIRouter(prefix="/v2/optimization", tags=["optimization.v2"], dependencies=[Depends(owner)])


class OptimizationPolicy(Contract):
    policy_version: Literal["aiedu.repair.v1", "aiedu.repair.v2"]
    enabled: Literal[True] = True
    max_attempts_per_finding: int = Field(ge=1)
    max_repair_rounds_per_run: int = Field(ge=1)
    root_call_budget: int | None = Field(default=None, ge=1)


class BudgetAmendment(Contract):
    action: Literal["revoke_call_budget"]
    acknowledged_existing_calls: bool


class OptimizationPolicyRequest(Revision):
    run_id: str = Field(min_length=1)
    version_id: str | None = Field(default=None, min_length=1)
    content_hash: str | None = Field(default=None, min_length=64, max_length=64)
    quality_hash: str | None = Field(default=None, min_length=64, max_length=64)
    checkpoint_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    policy: OptimizationPolicy
    budget_amendment: BudgetAmendment | None = None


class OptimizationRequest(Revision):
    run_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    content_hash: str = Field(min_length=64, max_length=64)
    quality_hash: str = Field(min_length=64, max_length=64)
    candidate_recheck: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


@router.post("/policy")
def update_policy(payload: OptimizationPolicyRequest, key=Depends(action_key)):
    return set_policy(payload.run_id, payload, key)


@router.post("/runs", status_code=202)
def start_optimization(payload: OptimizationRequest, background: BackgroundTasks, key=Depends(action_key)):
    result = request_optimization(payload, key)
    background.add_task(execute_run, result["run_id"])
    return result

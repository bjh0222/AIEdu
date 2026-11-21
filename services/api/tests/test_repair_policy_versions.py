# -*- coding: utf-8 -*-
"""Explicit versioned repair policy and cumulative hard-budget boundaries."""
from __future__ import annotations

import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post, task_run


def policy(version="aiedu.repair.v2", attempts=6, rounds=8, budget: int | None = 10):
    return {"policy_version": version, "enabled": True, "max_attempts_per_finding": attempts,
            "max_repair_rounds_per_run": rounds, "root_call_budget": budget}


def test_v1_keeps_legacy_caps_and_v2_can_raise_them():
    from app.v2.optimization import effective_repair_policy, validate_policy
    from app.v2.quality import POLICY

    assert effective_repair_policy({}) == POLICY
    assert validate_policy(policy("aiedu.repair.v1", 3, 5))["policy_version"] == "aiedu.repair.v1"
    raised = validate_policy(policy(attempts=7, rounds=9, budget=23))
    assert raised["max_attempts_per_finding"] == 7 and raised["max_repair_rounds_per_run"] == 9
    with pytest.raises(Exception, match="OPTIMIZATION_POLICY_REQUIRES_V2"):
        validate_policy(policy("aiedu.repair.v1", 4, 5))
    unlimited = validate_policy(policy(budget=None))
    assert unlimited["root_call_budget"] is None
    for bad in (0, -1, "10", True):
        with pytest.raises(Exception, match="OPTIMIZATION_POLICY_LIMIT_INVALID"):
            validate_policy(policy(budget=bad))


def test_ancestry_walks_up_and_counts_calls_not_descendants(client):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.optimization import ancestor_job_ids, cumulative_calls
    from app.v2.runtime import dump
    from app.v2.storage import CallDispatch

    root, child, grandchild = uuid4().hex, uuid4().hex, uuid4().hex
    rows = []
    for parent, job_id in ((None, root), (root, child), (child, grandchild)):
        rows.append(GenerationJob(id=job_id, run_id=job_id, parent_job_id=parent, root_job_id=job_id,
                                  job_type="task_v2", status="completed", state_json=dump({}),
                                  request_hash=job_id))
    calls = []
    for ordinal, root_id in enumerate((root, root, child)):
        calls.append(CallDispatch(call_id=uuid4().hex, root_job_id=root_id, run_id=grandchild, unit_id=f"u{ordinal}",
                                  epoch=0, attempt=1, idempotency_key=uuid4().hex, request_hash="r", input_hash="i",
                                  provider="fixture", model_family="fixture", model="fixture", status="settled"))
    with SessionLocal() as db:
        db.add_all(rows + calls)
        db.commit()
        assert set(ancestor_job_ids(db, grandchild)) == {grandchild, child, root}
        assert cumulative_calls(db, grandchild) == 3


def test_policy_endpoint_preserves_smaller_existing_hard_budget(client):
    headers, created, snap = task_run(client)
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.optimization import set_policy
    from app.v2.runtime import dump, job, load, write
    from app.api.v2_optimization import OptimizationPolicy, OptimizationPolicyRequest

    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["budget"] = {"calls": 5}
        obj.state_json, obj.state_revision = dump(state), obj.state_revision + 1
        revision = obj.state_revision
    with SessionLocal() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        quality_hash = version.quality_hash
    dto = OptimizationPolicyRequest(expected_revision=revision, run_id=snap["run_id"],
        version_id=snap["version_id"], content_hash=snap["content_hash"], quality_hash=quality_hash,
        policy=OptimizationPolicy(**policy(budget=6)))
    with pytest.raises(Exception, match="ROOT_BUDGET_INCREASE_FORBIDDEN"):
        set_policy(snap["run_id"], dto, "raise-budget")
    allowed = dto.model_copy(update={"policy": OptimizationPolicy(**policy(budget=5))})
    result = set_policy(snap["run_id"], allowed, "keep-budget")
    assert result["policy_version"] == "aiedu.repair.v2"
    with SessionLocal() as db:
        state = load(job(db, snap["run_id"]).state_json)
    assert state["budget"]["calls"] == 5
    assert state["optimization_policy"]["root_call_budget"] == 5


def test_explicit_root_amendment_revokes_limit_and_preserves_history(client):
    from app.db import SessionLocal
    from app.models import GenerationJob, TutorialVersion
    from app.v2.storage import CallDispatch
    from app.api.v2_optimization import BudgetAmendment, OptimizationPolicy, OptimizationPolicyRequest
    from app.v2.optimization import _hard_root_budget, cumulative_calls, set_policy
    from app.v2.runtime import dump, job, load, write

    headers, created, snap = task_run(client)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["budget"] = {"calls": 80}
        obj.state_json, obj.state_revision = dump(state), obj.state_revision + 1
        revision = obj.state_revision
        db.add_all([CallDispatch(root_job_id=obj.id, run_id=obj.run_id,
                                 call_id=uuid4().hex, unit_id=f"unit-{i:02d}", epoch=0, attempt=1, idempotency_key=uuid4().hex,
                                 request_hash="r", input_hash="i", provider="fixture", model="fixture",
                                 model_family="fixture", status="settled") for i in range(76)])
        db.commit()
    with SessionLocal() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        quality_hash = version.quality_hash
    base = {"expected_revision": revision, "run_id": snap["run_id"], "version_id": snap["version_id"],
            "content_hash": snap["content_hash"], "quality_hash": quality_hash,
            "policy": OptimizationPolicy(**policy(budget=None))}
    with pytest.raises(Exception, match="BUDGET_AMENDMENT_REQUIRED"):
        set_policy(snap["run_id"], OptimizationPolicyRequest(**base), "silent-revoke")
    result = set_policy(snap["run_id"], OptimizationPolicyRequest(**base, budget_amendment=BudgetAmendment(
        action="revoke_call_budget", acknowledged_existing_calls=True)), "explicit-revoke")
    assert result["root_call_budget"] is None and result["cumulative_calls"] == 77
    with SessionLocal() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        hard = _hard_root_budget(db, obj.id)
        assert cumulative_calls(db, obj.id) == 77
    assert state["budget"]["calls"] is None and state["budget"]["calls_amended"] is True
    assert state["optimization_policy"]["root_call_budget"] is None
    assert hard is None
    amendment = state["budget_amendments"][0]
    assert amendment["previous_calls"] == 80 and amendment["cumulative_calls"] == 77

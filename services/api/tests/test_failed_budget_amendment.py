"""A failed-before-publication task can revoke its cap without a fake version."""
from copy import deepcopy

import pytest

from app.api.v2_optimization import OptimizationPolicyRequest
from app.v2.contracts import DomainError, digest
from app.v2.optimization import set_policy
from app.v2.runtime import dump, job, load, write
from test_integration_quality_entry import queued, POLICY


@pytest.mark.parametrize("fault", [None, "checkpoint", "confirmation", "revision", "finite", "fake_version", "active"])
def test_failed_root_amendment_is_CAS_idempotent_and_keeps_original_accounting(client, monkeypatch, fault):
    run = queued(client, monkeypatch)
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state.pop("optimization_policy", None)
        state["budget"]["calls"] = 80
        obj.state_json, obj.status = dump(state), "failed"
        original_plan = deepcopy(state["plan"])
        payload = {"run_id": obj.run_id, "expected_revision": obj.state_revision,
            "checkpoint_hash": digest(state["plan"]), "policy": deepcopy(POLICY),
            "budget_amendment": {"action": "revoke_call_budget", "acknowledged_existing_calls": True}}
        if fault == "active": obj.status = "running"
    if fault == "checkpoint": payload["checkpoint_hash"] = "0" * 64
    if fault == "confirmation": payload["budget_amendment"]["acknowledged_existing_calls"] = False
    if fault == "revision": payload["expected_revision"] += 1
    if fault == "finite": payload["policy"]["root_call_budget"] = 100
    if fault == "fake_version": payload["version_id"] = "invented"
    request = OptimizationPolicyRequest(**payload)
    if fault:
        with pytest.raises(DomainError): set_policy(run["run_id"], request, "amend-failed")
    else:
        result = set_policy(run["run_id"], request, "amend-failed")
        assert set_policy(run["run_id"], request, "amend-failed") == result
        assert result["root_call_budget"] is None and result["cumulative_calls"] == 0
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        assert state["plan"] == original_plan and state["version_id"] is None
        assert state["budget"]["calls"] == (80 if fault else None)
        if not fault:
            amendment = state["budget_amendments"][0]
            assert amendment["previous_calls"] == 80 and amendment["version_id"] is None
            assert amendment["checkpoint_hash"] == payload["checkpoint_hash"]


def test_resumed_limit_revocation_requires_original_grant_and_keeps_ancestral_calls(client, monkeypatch):
    from uuid import uuid4
    from app.v2.contracts import Resume
    from app.v2.tasks import resume
    from app.v2.storage import CallDispatch
    from app.v2.optimization import cumulative_calls, _hard_root_budget
    run = queued(client, monkeypatch)
    with write() as db:
        root = job(db, run["run_id"])
        state = load(root.state_json)
        state["budget"]["calls"] = 80
        root.status, root.state_json = "failed", dump(state)
        root_request = {"run_id": root.run_id, "expected_revision": root.state_revision,
            "checkpoint_hash": digest(state["plan"]), "policy": deepcopy(POLICY),
            "budget_amendment": {"action": "revoke_call_budget", "acknowledged_existing_calls": True}}
        resume_request = Resume(expected_revision=root.state_revision, checkpoint_hash=digest(state["plan"]))
    child_run = resume(run["run_id"], resume_request, "child-before-amendment")
    with write() as db:
        root, child = job(db, run["run_id"]), job(db, child_run["run_id"])
        child.status = "failed"
        for obj in (root, child):
            db.add(CallDispatch(call_id=str(uuid4()), root_job_id=obj.id, run_id=obj.run_id,
                unit_id="settled", epoch=0, attempt=1, idempotency_key=str(uuid4()),
                request_hash="f", input_hash="f", provider="fixture", model="fixture", model_family="fixture",
                status="settled", outcome="failed"))
        root_request["expected_revision"] = root.state_revision
        child_request = {**root_request, "run_id": child.run_id, "expected_revision": child.state_revision}
    with pytest.raises(DomainError, match="BUDGET_AMENDMENT_ROOT_REQUIRED"):
        set_policy(child_run["run_id"], OptimizationPolicyRequest(**child_request), "revoke-child")
    set_policy(run["run_id"], OptimizationPolicyRequest(**root_request), "revoke-root")
    with write() as db:
        child = job(db, child_run["run_id"])
        assert _hard_root_budget(db, child.id) == 80  # root revoke alone cannot erase child limit
    result = set_policy(child_run["run_id"], OptimizationPolicyRequest(**child_request), "revoke-child")
    assert set_policy(child_run["run_id"], OptimizationPolicyRequest(**child_request), "revoke-child") == result
    assert result["cumulative_calls"] == 2
    with write() as db:
        child = job(db, child_run["run_id"])
        state = load(child.state_json)
        assert cumulative_calls(db, child.id) == 2 and _hard_root_budget(db, child.id) is None
        assert state["budget_amendments"][-1]["previous_calls"] == 80
        assert state["budget_amendments"][-1]["root_authorization"]["run_id"] == run["run_id"]

"""Fault, security, recovery and repair tests using synthetic offline evidence only."""
from __future__ import annotations

import copy
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post, task_run


def queued(client):
    from app.v2.contracts import StartRun
    from app.v2.tasks import start_run
    headers, created, snap = task_run(client, False)
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), str(uuid4()))
    return created, run


def test_concurrent_creation_same_key_one_resource(client):
    from app.v2.contracts import TaskInput
    from app.v2.tasks import create_task
    payload = TaskInput(title="parallel", raw_input="原文不变")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: create_task(payload, "same-concurrent-key"), range(4)))
    assert len({x["task_id"] for x in results}) == 1


def test_lease_expiry_fences_old_worker_and_unknown_not_retried(client):
    from app.v2.contracts import DomainError
    from app.v2.runtime import authorize_call, claim_root, job, mark_sent, publish, write
    from app.v2.storage import CallDispatch
    created, run = queued(client)
    first = claim_root(run["run_id"], "old")
    call = authorize_call(run["run_id"], "unit", "hash", "input", first["epoch"], "old", "attempt")
    mark_sent(call, "old", first["epoch"])
    with write() as db:
        job(db, run["run_id"]).lease_until = time.time()-1
    next_lease = claim_root(run["run_id"], "new")
    assert next_lease["epoch"] > first["epoch"]
    with pytest.raises(DomainError, match="FENCED"):
        authorize_call(run["run_id"], "second", "hash2", "input", first["epoch"], "old", "attempt2")
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        authorize_call(run["run_id"], "unit", "hash3", "input", next_lease["epoch"], "new", "attempt3")


def test_global_one_active_root(client):
    from app.v2.runtime import claim_root
    from app.v2.contracts import DomainError
    a, one = queued(client)
    b, two = queued(client)
    claim_root(one["run_id"], "one")
    with pytest.raises(DomainError, match="RESOURCE_BACKPRESSURE"):
        claim_root(two["run_id"], "two")


def test_unknown_resume_requires_separate_quarantine_then_new_call(client):
    from app.v2.contracts import DomainError, Pause, QuarantineUnknown, Resume
    from app.v2.contracts import sha
    from app.v2.runtime import authorize_call, claim_root, mark_sent, settle
    from app.v2.tasks import pause, quarantine_unknown, resume, snapshot
    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    request_hash = sha("unknown-request")
    call = authorize_call(run["run_id"], "unit", request_hash, "i", lease["epoch"], "worker", "c")
    mark_sent(call, "worker", lease["epoch"])
    settle(call, "unknown")
    snap = snapshot(created["task_id"])
    paused = pause(run["run_id"], Pause(expected_revision=snap["state_revision"]), "pause")
    dto = Resume(expected_revision=paused["state_revision"], checkpoint_hash=paused["checkpoint_hash"])
    with pytest.raises(DomainError, match="UNKNOWN_QUARANTINE_REQUIRED"):
        resume(run["run_id"], dto, "resume-no-risk")
    isolated = quarantine_unknown(run["run_id"], QuarantineUnknown(
        expected_revision=paused["state_revision"],
        checkpoint_hash=paused["checkpoint_hash"],
        calls=[{"call_id": call, "request_hash": request_hash}],
        acknowledge_possible_duplicate_cost=True,
    ), "isolate")
    data = {
        "expected_revision": isolated["state_revision"],
        "checkpoint_hash": isolated["checkpoint_hash"],
        "unknown_resolutions": [{
            "call_id": call,
            "action": "retry_as_new_attempt",
            "acknowledge_possible_duplicate_cost": True,
        }],
    }
    result = resume(run["run_id"], Resume(**data), "resume-risk")
    assert result["run_id"] != run["run_id"]


def test_paused_receiptless_sent_can_be_quarantined_then_resumed(client):
    """Pausing fences an in-flight call; the explicit UI path must not deadlock."""
    from app.v2.contracts import DomainError, Pause, QuarantineUnknown, Resume, sha
    from app.v2.runtime import authorize_call, claim_root, mark_sent
    from app.v2.tasks import pause, quarantine_unknown, resume, snapshot

    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    request_hash = sha("paused-sent-request")
    call = authorize_call(
        run["run_id"], "unit", request_hash, "input", lease["epoch"],
        "worker", "paused-sent-call",
    )
    mark_sent(call, "worker", lease["epoch"])
    current = snapshot(created["task_id"], run["run_id"])
    paused = pause(
        run["run_id"],
        Pause(expected_revision=current["state_revision"], reason="user_pause"),
        "pause-paused-sent",
    )
    paused_snap = snapshot(created["task_id"], run["run_id"])
    assert paused_snap["state"] == "paused"
    assert [c["status"] for c in paused_snap["calls"] if c["call_id"] == call] == ["sent"]

    dto = Resume(
        expected_revision=paused["state_revision"],
        checkpoint_hash=paused["checkpoint_hash"],
    )
    with pytest.raises(DomainError, match="UNKNOWN_QUARANTINE_REQUIRED"):
        resume(run["run_id"], dto, "paused-sent-resume-blocked")

    isolated = quarantine_unknown(
        run["run_id"],
        QuarantineUnknown(
            expected_revision=paused["state_revision"],
            checkpoint_hash=paused["checkpoint_hash"],
            calls=[{"call_id": call, "request_hash": request_hash}],
            acknowledge_possible_duplicate_cost=True,
        ),
        "paused-sent-isolate",
    )
    after = snapshot(created["task_id"], run["run_id"])
    row = next(item for item in after["calls"] if item["call_id"] == call)
    assert row["status"] == "quarantined"
    assert row["outcome"] == "unknown"
    assert row["failure_diagnostics"]["validation_reason"] == "PAUSED_CALL_FENCED_WITHOUT_RECEIPT"

    result = resume(
        run["run_id"],
        Resume(
            expected_revision=isolated["state_revision"],
            checkpoint_hash=isolated["checkpoint_hash"],
            unknown_resolutions=[{
                "call_id": call,
                "action": "retry_as_new_attempt",
                "acknowledge_possible_duplicate_cost": True,
            }],
        ),
        "paused-sent-resume",
    )
    assert result["run_id"] != run["run_id"]


def test_immutable_version_cas_rejects_stale_base(client):
    from app.v2.runtime import claim_root, publish
    from app.v2.contracts import DomainError, sha
    from app.v2.quality import reduce_quality
    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    unit = {"unit_id": str(uuid4()), "body": "unchanged", "content_hash": sha("unchanged"), "artifact_role": "tutorial"}
    version = publish(run["run_id"], "worker", lease["epoch"], [unit], {"quality_status": "needs_review"}, "publish1")
    with pytest.raises(DomainError, match="VERSION_MISMATCH"):
        publish(run["run_id"], "worker", lease["epoch"], [unit], {}, "publish2", base_version=None)


def test_repair_gates_and_non_target_byte_identity():
    from app.v2.quality import apply_patch, compile_plan, deterministic_checks, repair_contract
    from app.v2.contracts import DomainError, sha
    unit = {"unit_id": "unit", "body": "safe prefix\n```python\nx=1\n", "content_hash": sha("safe prefix\n```python\nx=1\n")}
    plan = compile_plan({"spec_bindings": []})
    check = deterministic_checks(plan, [unit], "v1")
    finding = check["findings"][0]
    contract = repair_contract(finding, unit, "v1", plan["check_plan_hash"], [])
    changed = apply_patch(unit, contract, "x=1\n", "x=1\n```\n")
    assert changed["body"].startswith("safe prefix")
    suspected = finding | {"status": "suspected"}
    with pytest.raises(DomainError, match="NOT_ELIGIBLE"):
        repair_contract(suspected, unit, "v1", plan["check_plan_hash"], [])
    with pytest.raises(DomainError, match="PROTECTED_SPAN"):
        repair_contract(finding, unit, "v1", plan["check_plan_hash"], [(0, 4)])
    with pytest.raises(DomainError, match="FINDING_STALE"):
        repair_contract(finding, unit, "wrong-version", plan["check_plan_hash"], [])


def test_progress_does_not_use_evidence_hash():
    from app.v2.quality import progress_fingerprint
    finding = {"normalized_claim_hash": "same", "status": "confirmed", "tool_raw_output_hash": "first"}
    one = progress_fingerprint([], [finding], [])
    assert one == progress_fingerprint([], [finding | {"tool_raw_output_hash": "different"}], [])


def test_conflicts_and_dependency_closure():
    from app.v2.quality import dependency_closure
    from app.v2.repairs import conflict_components
    contracts = [{"finding_id": "a", "unit_id": "u", "edit_scope": [0, 10]},
                 {"finding_id": "b", "unit_id": "u", "edit_scope": [5, 15]},
                 {"finding_id": "c", "unit_id": "v", "edit_scope": [0, 2]}]
    assert conflict_components(contracts) == [["a", "b"], ["c"]]
    result = dependency_closure([{"unit_id": "a"}, {"unit_id": "b", "depends_on": ["a"]}, {"unit_id": "c", "depends_on": ["b"]}], {"a"})
    assert result["unit_ids"] == ["a", "b", "c"]
    assert dependency_closure([{"unit_id": "x", "depends_on": ["missing"]}], {"x"})["full_semantic_rerun"]


def test_knowledge_scope_approval_call_version_hash_and_withdrawal():
    from app.v2.contracts import sha
    from app.v2.knowledge import evidence_bundle, invalidated_traces, verify_binding
    candidates = [{"id": "p", "domain": "math", "branch": "linear", "status": "confirmed", "version": 2,
                   "mechanism": "matrix", "evidence_hash": "e", "source": {"family": "same"}}]
    args = dict(domain="math", branch="linear", query="matrix", call_id="call", stage="generate", unit_id="unit")
    assert not evidence_bundle(candidates, **args)["selected"]
    bundle = evidence_bundle(candidates, **args, guidance_enabled=True)
    bound = dict(call_id="call", stage="generate", unit_id="unit", principle_id="p", version=2,
                 evidence_hash="e", body="matrix", start_byte=0, end_byte=6, span_hash=sha("matrix"))
    assert verify_binding(bundle, **bound)["binding_status"] == "confirmed"
    assert verify_binding(bundle, **(bound | {"version": 1}))["binding_status"] == "unconfirmed"
    assert verify_binding(bundle, **(bound | {"call_id": "other"}))["binding_status"] == "unconfirmed"
    assert invalidated_traces([{"trace_id": "t", "selected": bundle["selected"]}], "p", 2) == ["t"]


def test_tools_never_execute_free_shell_or_python():
    from app.v2.verifiers import polynomial_identity, ps51_verify
    from app.v2.contracts import DomainError
    assert polynomial_identity("(x+1)**2", "x*x+2*x+1", ["x"], "input")["result"] == "satisfied"
    assert polynomial_identity("x+1", "x+2", ["x"], "input")["result"] == "violated"
    with pytest.raises(DomainError, match="UNSUPPORTED_POLYNOMIAL"):
        polynomial_identity("__import__('os').system('whoami')", "0", [], "input")
    assert ps51_verify("Invoke-Expression malicious", None, None)["result"] == "not_run"


def test_sqlite_wal_full_busy_timeout_and_transactional_delta(client):
    from app.db import SessionLocal
    from app.v2.contracts import TaskInput
    from app.v2.storage import MigrationDelta
    from app.v2.tasks import create_task
    result = create_task(TaskInput(title="audit", raw_input="audit"), "audit-create")
    with SessionLocal() as db:
        from sqlalchemy import text
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA synchronous")).scalar() == 2
        assert db.execute(text("PRAGMA busy_timeout")).scalar() == 30000
        assert db.query(MigrationDelta).filter_by(entity_type="learning_goals", entity_id=result["task_id"]).count() == 1


def test_wal_backup_restore_and_idempotent_delta(tmp_path):
    from app.v2.migration import replay_deltas, restore_copy
    source, target = tmp_path / "test_source.db", tmp_path / "test_restored.db"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE things (id TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT INTO things VALUES ('a','old')")
        db.commit()
        assert restore_copy(source, target)["integrity_check"] == "ok"
    event = {"event_id": "e", "entity_type": "things", "entity_id": "a", "operation": "update", "after_json": '{"id":"a","value":"new"}'}
    assert replay_deltas(target, [event])["applied"] == ["e"]
    assert replay_deltas(target, [event])["skipped"] == ["e"]
    with sqlite3.connect(target) as db:
        assert db.execute("SELECT value FROM things").fetchone()[0] == "new"


def test_capacity_unknown_and_explicit_registry():
    from app.v2.capacity import get_capacity, request_capacity
    from app.v2.contracts import DomainError
    assert get_capacity("https://api.deepseek.com/v1", "deepseek-flash")["model_version"] == "DeepSeek-V4.1-Flash-1017"
    with pytest.raises(DomainError, match="CAPACITY_UNKNOWN"):
        request_capacity([], None)
    with pytest.raises(DomainError, match="CALL_CAPACITY_EXCEEDED"):
        request_capacity([{"content": "x"*20}], {"max_output_tokens": 10, "context_tokens": 20,
             "safety_margin_tokens": 2, "estimate_method": "test", "source_uri_or_config": "fixture"})


def test_quality_protocol_upgrade_requires_explicit_acknowledgment(client):
    """Resume refuses a stale frozen protocol unless the user explicitly adopts the new one."""
    from app.v2.contracts import DomainError, Pause, Resume
    from app.v2.review_contract import review_protocol_manifest
    from app.v2.runtime import dump, job, load, write
    from app.v2.tasks import pause, resume, snapshot
    created, run = queued(client)
    stale = copy.deepcopy(load(job_db_state(run["run_id"]))["quality_protocol"])
    stale["components"]["applicability"]["version"] = "zero-model-applicability.v0"
    previous_hash = stale["manifest_hash"]
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state["quality_protocol"] = stale
        obj.state_json = dump(state)
        obj.state_revision += 1
    snap = snapshot(created["task_id"])
    paused = pause(run["run_id"], Pause(expected_revision=snap["state_revision"]), "pause")
    dto = Resume(expected_revision=paused["state_revision"], checkpoint_hash=paused["checkpoint_hash"])
    with pytest.raises(DomainError, match="QUALITY_PROTOCOL_UPGRADE_REQUIRED") as exc:
        resume(run["run_id"], dto, "upgrade-no-ack")
    assert exc.value.details["frozen_manifest_hash"] == previous_hash
    assert exc.value.details["current_manifest_hash"] == review_protocol_manifest()["manifest_hash"]
    result = resume(run["run_id"], Resume(**{**dto.model_dump(), "quality_protocol_upgrade": True}), "upgrade-ack")
    child = job_db_state(result["run_id"])
    upgraded = load(child)["quality_protocol"]
    assert upgraded == review_protocol_manifest()
    record = load(child)["quality_protocol_upgrade"]
    assert record["previous_manifest_hash"] == previous_hash
    assert record["current_manifest_hash"] == upgraded["manifest_hash"]
    assert record["method_version"] == "explicit-quality-protocol-upgrade.v1"
    assert load(job_db_state(run["run_id"]))["quality_protocol"]["manifest_hash"] == previous_hash


def job_db_state(run_id: str) -> str:
    from app.db import SessionLocal
    from app.models import GenerationJob
    with SessionLocal() as db:
        return db.query(GenerationJob).filter_by(run_id=run_id).one().state_json

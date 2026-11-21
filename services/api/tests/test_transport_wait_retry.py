"""Read-only wait increases are not model changes or unlimited retry grants."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from test_v2_optimization import heading_task


def configured():
    return SimpleNamespace(llm_base_url="https://fixture.invalid/v1", llm_model="fixture", llm_api_key="fixture-secret",
        llm_timeout=120, llm_read_timeout=600, llm_temperature=0.3, llm_reasoning_effort="high",
        llm_thinking="enabled", llm_json_mode=True, llm_max_tokens=32768, llm_model_family="fixture")


def test_read_timeout_changes_only_http_read_wait_not_model_payload(monkeypatch):
    from app.v2.inference import AuditedProvider
    calls = []
    def response(*a, **kw):
        calls.append(kw)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})
    monkeypatch.setattr("app.v2.inference.httpx.post", response)
    default = configured()
    default.llm_read_timeout = None
    AuditedProvider(default).complete_json("system", "user")
    AuditedProvider(configured()).complete_json("system", "user")
    assert calls[0]["json"] == calls[1]["json"]
    assert calls[0]["headers"] == calls[1]["headers"]
    assert calls[0]["timeout"] == 120
    assert calls[1]["timeout"].as_dict() == {"connect": 120, "read": 600, "write": 120, "pool": 120}


def test_optional_read_wait_survives_older_client_save_without_changing_key(client):
    from app import model_settings as store
    values = {"id": "fixture", "name": "fixture", "base_url": "https://fixture.invalid/v1", "model": "fixture",
              "api_key": "fixture-secret", "timeout": 120, "read_timeout": 600}
    store.save_profile(store.ModelProfile(**values))
    assert store.profile_settings(store.read_config()["profiles"][0]).llm_read_timeout == 600
    old_client = {k: v for k, v in values.items() if k not in {"read_timeout", "api_key"}}
    store.save_profile(store.ModelProfile(**old_client))
    saved = store.read_config()["profiles"][0]
    assert saved["read_timeout"] == 600 and saved["api_key"] == "fixture-secret" and saved["timeout"] == 120
    store.save_profile(store.ModelProfile(**(old_client | {"read_timeout": None})))
    assert store.read_config()["profiles"][0]["read_timeout"] is None


def stopped_unknown(client, *, recorded_wait=True):
    from app.v2.contracts import digest
    from app.v2.runtime import job, load, write, dump
    from app.v2.storage import CallDispatch
    _, snap = heading_task(client)
    cid, req = str(uuid4()), digest("unknown-fixture")
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        obj.status = "failed"
        state["unknown_quarantines"] = {cid: {"request_hash": req, "acknowledge_possible_duplicate_cost": True}}
        obj.state_json = dump(state)
        request = {"messages": [{"role": "system", "content": "review"}, {"role": "user", "content": dump({"checks": [{"item_id": "C3.correctness"}]})}],
            "parameters": {"temperature": 0.3, "reasoning_effort": "high", "thinking": "enabled", "json_mode": True, "max_tokens": 32768}}
        if recorded_wait:
            request["transport"] = {"configured_timeout_seconds": 120}
        db.add(CallDispatch(call_id=cid, root_job_id=obj.id, run_id=obj.run_id, unit_id=snap["units"][0]["unit_id"],
            epoch=0, attempt=1, idempotency_key=cid, request_hash=req, input_hash=state["context"]["context_hash"],
            manifest_json=dump({"stage": "checking", "plan_hash": state["plan"]["plan_hash"]}), request_json=dump(request),
            provider="https://fixture.invalid/v1", model="fixture", model_family="fixture", status="quarantined", outcome="unknown",
            receipt_json=dump({"failure_code": "PROVIDER_TRANSPORT_UNKNOWN"})))
        revision = obj.state_revision
    return snap["run_id"], state, revision, cid, req


def permission(cid, request_hash):
    return {"call_id": cid, "request_hash": request_hash, "previous_read_timeout_seconds": 120,
            "read_timeout_seconds": 600, "acknowledge_possible_duplicate_cost": True}


@pytest.mark.parametrize("recorded_wait", [True, False])
def test_explicit_resume_grant_preserves_unknown_and_binds_exact_new_wait(client, monkeypatch, recorded_wait):
    from app.db import SessionLocal
    from app.v2 import model_roles, tasks
    from app.v2.contracts import Resume, digest
    from app.v2.runtime import job, load
    from app.v2.storage import CallDispatch
    run_id, state, revision, cid, req = stopped_unknown(client, recorded_wait=recorded_wait)
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: configured())
    with SessionLocal() as db:
        call_count = db.query(CallDispatch).count()
    payload = Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"]), transport_retries=[permission(cid, req)])
    response = tasks.resume(run_id, payload, "explicit-wait-increase")
    assert tasks.resume(run_id, payload, "explicit-wait-increase") == response
    with SessionLocal() as db:
        current = load(job(db, response["run_id"]).state_json)
        grant = current["transport_retry_permissions"][cid]
        assert grant["request_hash"] == req and grant["read_timeout_seconds"] == 600
        assert grant["previous_wait_source"] == ("recorded_request" if recorded_wait else "explicit_user_declared_legacy_limit")
        call = db.get(CallDispatch, cid)
        assert call.status == "quarantined" and call.outcome == "unknown" and call.usage_json is None
        assert db.query(CallDispatch).count() == call_count


@pytest.mark.parametrize("case", ["no_ack", "wrong_hash", "not_increased", "profile_wait_mismatch", "model_changed", "thinking_changed", "other_call"])
def test_invalid_transport_grant_cannot_create_new_run(client, monkeypatch, case):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import model_roles, tasks
    from app.v2.contracts import Resume, DomainError, digest
    from app.v2.storage import CallDispatch
    run_id, state, revision, cid, req = stopped_unknown(client)
    grant, profile = permission(cid, req), configured()
    if case == "no_ack": grant["acknowledge_possible_duplicate_cost"] = False
    if case == "wrong_hash": grant["request_hash"] = digest("other")
    if case == "not_increased": grant["read_timeout_seconds"] = 120
    if case == "profile_wait_mismatch": profile.llm_read_timeout = 300
    if case == "model_changed": profile.llm_model = "different"
    if case == "thinking_changed": profile.llm_thinking = "disabled"
    if case == "other_call": grant["call_id"] = str(uuid4())
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: profile)
    with SessionLocal() as db:
        before = db.query(GenerationJob).count(), db.query(CallDispatch).count()
    with pytest.raises(DomainError, match="TRANSPORT_RETRY"):
        tasks.resume(run_id, Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"]), transport_retries=[grant]), "bad-grant")
    with SessionLocal() as db:
        assert before == (db.query(GenerationJob).count(), db.query(CallDispatch).count())
        assert db.get(CallDispatch, cid).outcome == "unknown"


def test_new_six_hundred_second_unknown_is_not_covered_by_old_grant(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import model_roles, tasks, checker_batch
    from app.v2.contracts import Resume, DomainError, digest
    from app.v2.runtime import job, load, write, dump
    from app.v2.storage import CallDispatch
    run_id, state, revision, cid, req = stopped_unknown(client)
    profile = configured()
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: profile)
    response = tasks.resume(run_id, Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"]),
        transport_retries=[permission(cid, req)]), "explicit")
    with SessionLocal() as db:
        current = load(job(db, response["run_id"]).state_json)
        original = db.get(CallDispatch, cid)
        uid, payload = original.unit_id, load(load(original.request_json)["messages"][1]["content"])
    signatures = checker_batch.quarantined_checklists(response["run_id"], current, uid, "checking", "review", profile)
    assert checker_batch.split_quarantined_checklists(payload, signatures) == [payload]
    assert signatures.retryable_singletons == {digest(payload)}
    new_call, grandchild = str(uuid4()), str(uuid4())
    with write() as db:
        parent = job(db, response["run_id"])
        old = db.get(CallDispatch, cid)
        request = load(old.request_json)
        request["transport"] = {"configured_timeout_seconds": 120, "read_timeout_seconds": 600}
        current["unknown_quarantines"][new_call] = {"request_hash": req, "acknowledge_possible_duplicate_cost": True}
        parent.state_json = dump(current)
        db.add(CallDispatch(call_id=new_call, root_job_id=parent.id, run_id=parent.run_id, unit_id=uid, epoch=0, attempt=1,
            idempotency_key=new_call, request_hash=req, input_hash=old.input_hash, manifest_json=old.manifest_json,
            request_json=dump(request), provider=old.provider, model=old.model, model_family=old.model_family,
            status="quarantined", outcome="unknown", receipt_json=old.receipt_json))
        db.add(GenerationJob(id=grandchild, root_job_id=grandchild, parent_job_id=parent.id, run_id=grandchild,
            goal_id=parent.goal_id, job_type="task_v2", status="queued", state_json=dump(current)))
    signatures = checker_batch.quarantined_checklists(grandchild, current, uid, "checking", "review", profile)
    assert not signatures.retryable_singletons
    with pytest.raises(DomainError, match="CHECKLIST_TRANSPORT_SINGLETON_UNRESOLVED"):
        checker_batch.split_quarantined_checklists(payload, signatures)


def test_successful_request_is_reused_after_read_wait_increase(client, monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import inference
    from app.v2.runtime import job, load, dump, write, claim_root
    from app.v2.storage import CallDispatch
    from test_generation_source_tools import running
    rid, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {"max_output_tokens": 32768, "context_tokens": 100000,
        "safety_margin_tokens": 0, "estimate_method": "fixture", "source_uri_or_config": "fixture"})
    calls = []
    class Provider:
        def complete_json(self, *a):
            calls.append(a)
            return {"ok": True}, 1, {"total_tokens": 2}
    profile = configured()
    profile.llm_read_timeout = None
    args = dict(stage="checking", unit_id="u", data={"same": "content"}, system="same system", role="review",
                profile=profile, provider_factory=lambda _: Provider())
    first, audit = inference.invoke(rid, "source-fixture", epoch, state, **args)
    child = str(uuid4())
    with write() as db:
        parent = job(db, rid)
        parent.status = "failed"
        db.add(GenerationJob(id=child, root_job_id=child, parent_job_id=parent.id, run_id=child, goal_id=parent.goal_id,
            status="queued", job_type="task_v2", state_json=dump(load(parent.state_json))))
    lease = claim_root(child, "resume")
    profile.llm_read_timeout = 600
    second, reused = inference.invoke(child, "resume", lease["epoch"], state, **args)
    assert first == second and len(calls) == 1
    assert reused["call_id"] == audit["call_id"] and reused["cache"] == "identical_ancestral_request"
    with SessionLocal() as db:
        assert db.query(CallDispatch).count() == 1

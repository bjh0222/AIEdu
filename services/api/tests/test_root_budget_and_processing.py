"""Cumulative authorization is not refreshed by resume/optimization children."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_generation_source_tools import running


def test_calls_across_parent_ancestry_exhaust_same_hard_budget(client):
    from app.models import GenerationJob
    from app.v2.runtime import authorize_call, mark_sent, settle, write, job, load, dump, claim_root
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    cid = authorize_call(rid, "u", "a"*64, "b"*64, epoch, "source-fixture", uuid4().hex)
    mark_sent(cid, "source-fixture", epoch)
    settle(cid, "success", {"total_tokens": 2}, raw_result={"ok": True})
    child_id, child_run = uuid4().hex, uuid4().hex
    with write() as db:
        old = job(db, rid)
        old.status = "completed"
        current = load(old.state_json)
        current["budget"] = {"calls": 1}
        db.add(GenerationJob(id=child_id, run_id=child_run, root_job_id=child_id, parent_job_id=old.id,
            goal_id=old.goal_id, job_type="task_v2", status="queued", state_json=dump(current), request_hash=uuid4().hex))
    lease = claim_root(child_run, "next")
    with pytest.raises(DomainError, match="CALL_BUDGET_EXHAUSTED"):
        authorize_call(child_run, "u2", "c"*64, "b"*64, lease["epoch"], "next", uuid4().hex)


def test_child_null_calls_inherits_unrevoked_ancestor_limit(client):
    from app.models import GenerationJob
    from app.v2.runtime import authorize_call, claim_root, dump, job, load, mark_sent, settle, write
    from app.v2.contracts import DomainError
    rid, epoch, _ = running(client)
    with write() as db:
        old = job(db, rid)
        old.status = "completed"
        current = load(old.state_json)
        current["budget"] = {"calls": 1}
        old.state_json = dump(current)
        child_state = deepcopy(current) | {"budget": {"calls": None}}
        child_id, child_run = uuid4().hex, uuid4().hex
        db.add(GenerationJob(id=child_id, run_id=child_run, root_job_id=child_id, parent_job_id=old.id,
            goal_id=old.goal_id, job_type="task_v2", status="queued", state_json=dump(child_state),
            request_hash=uuid4().hex))
    lease = claim_root(child_run, "next")
    first = authorize_call(child_run, "u", "a"*64, "b"*64, lease["epoch"], "next", uuid4().hex)
    mark_sent(first, "next", lease["epoch"])
    settle(first, "success", {"total_tokens": 1}, raw_result={"ok": True})
    with pytest.raises(DomainError, match="CALL_BUDGET_EXHAUSTED"):
        authorize_call(child_run, "u2", "c"*64, "b"*64, lease["epoch"], "next", uuid4().hex)


def test_token_reservations_and_unknown_usage_do_not_count_as_zero(client):
    from app.v2.runtime import authorize_call, mark_sent, settle, write, job, load, dump
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    with write() as db:
        obj = job(db, rid)
        current = load(obj.state_json)
        current["budget"] = {"tokens": 10}
        obj.state_json = dump(current)
    first = authorize_call(rid, "u", "a"*64, "b"*64, epoch, "source-fixture", uuid4().hex,
        request={"capacity": {"reserved_total_tokens": 8}})
    with pytest.raises(DomainError, match="TOKEN_BUDGET_EXHAUSTED"):
        authorize_call(rid, "v", "c"*64, "b"*64, epoch, "source-fixture", uuid4().hex,
            request={"capacity": {"reserved_total_tokens": 3}})
    mark_sent(first, "source-fixture", epoch)
    settle(first, "success", {}, raw_result={"ok": True})
    with pytest.raises(DomainError, match="TOKEN_BUDGET_USAGE_UNKNOWN"):
        authorize_call(rid, "v", "c"*64, "b"*64, epoch, "source-fixture", uuid4().hex,
            request={"capacity": {"reserved_total_tokens": 1}})


def test_source_stage_settles_without_losing_provider_fact(client):
    from app.v2.runtime import authorize_call, mark_sent, settle, job, load
    from app.db import SessionLocal
    from app.v2.storage import CallDispatch
    rid, epoch, state = running(client)
    cid = authorize_call(rid, "u", "a"*64, "b"*64, epoch, "source-fixture", uuid4().hex,
        manifest={"stage": "research_screening"})
    mark_sent(cid, "source-fixture", epoch)
    settle(cid, "success", {"total_tokens": 3}, raw_result={"bad_local_schema": True})
    with SessionLocal() as db:
        assert load(job(db, rid).state_json)["stages"]["research_screening"] == "done"
        call = db.get(CallDispatch, cid)
        assert call.outcome == "success" and call.status == "settled"
        assert load(call.usage_json)["total_tokens"] == 3


def test_process_scoped_profile_does_not_rewrite_user_configuration(client, monkeypatch):
    from app.v2 import model_roles
    from app import model_settings
    original = {"profiles": [{"id": "generation", "model": "deepseek-flash"}, {"id": "other", "model": "glm"}],
        "model_roles": {"generation": "generation", "review": "other"}}
    monkeypatch.setattr(model_roles, "read_config", lambda: deepcopy(original))
    monkeypatch.setenv("AIEDU_SCOPED_ROLE_PROFILE", "generation")
    profiles, roles = model_roles.role_config()
    assert set(roles.values()) == {"generation"}
    assert original["model_roles"]["review"] == "other"

"""Provider failures retain distinguishable, non-sensitive processing evidence."""
from types import SimpleNamespace
import httpx
import pytest


def profile():
    return SimpleNamespace(llm_base_url="https://fixture.invalid/v1", llm_model="fixture", llm_api_key="secret-fixture",
        llm_timeout=10, llm_temperature=None, llm_reasoning_effort="high", llm_thinking="enabled", llm_json_mode=True, llm_max_tokens=64)


def test_complete_prefix_never_salvages_truncation_or_drops_another_value():
    from app.v2.inference import complete_object_prefix
    assert complete_object_prefix('{"a":{"b":1}}}') == {"a": {"b": 1}}
    for invalid in ('{"a":', '{"a":1', '{"a":1}{"b":2}', '{"a":1} prose', '```json\n{"a":1}\n```'):
        assert complete_object_prefix(invalid) is None


@pytest.mark.parametrize("reason,text,validation", [("length", '{"a":1}', "OUTPUT_LENGTH_LIMIT"),
    ("stop", '{"a":', "JSON_PARSE_FAILED")])
def test_distinguishable_failure_details_and_raw_hash_not_loss_of_usage(monkeypatch, reason, text, validation):
    from app.v2.inference import AuditedProvider, AttemptFailure
    monkeypatch.setattr("app.v2.inference.httpx.post", lambda *a, **k: httpx.Response(200,
        json={"id": "request-id", "choices": [{"finish_reason": reason, "message": {"content": text}}], "usage": {"total_tokens": 7}}))
    with pytest.raises(AttemptFailure) as caught:
        AuditedProvider(profile()).complete_json("json", "input")
    error = caught.value
    assert error.diagnostics["validation_reason"] == validation
    assert error.diagnostics["finish_reason"] == reason
    assert error.usage["total_tokens"] == 7
    assert error.raw_result["provider_content"] == text


def test_complete_json_plus_redundant_closer_is_exactly_normalized(monkeypatch):
    from app.v2.inference import AuditedProvider
    monkeypatch.setattr("app.v2.inference.httpx.post", lambda *a, **k: httpx.Response(200,
        json={"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}}'}}]}))
    provider = AuditedProvider(profile())
    result, _, _ = provider.complete_json("json", "input")
    assert result == {"ok": True}
    assert provider.response_normalization["method"] == "complete_object_redundant_closers.v1"


@pytest.mark.parametrize("exception,phase", [(httpx.ReadTimeout, "read"),
    (httpx.ConnectTimeout, "connect"), (httpx.WriteTimeout, "write"),
    (httpx.PoolTimeout, "pool"), (httpx.ConnectError, "unspecified_transport")])
def test_transport_diagnostics_preserve_unknown_without_leaking_exception(monkeypatch, exception, phase):
    from app.v2.inference import AuditedProvider, AttemptFailure
    calls = []
    def fail(*a, **kw):
        calls.append(kw)
        raise exception("secret-fixture https://private.invalid/?token=do-not-log")
    monkeypatch.setattr("app.v2.inference.httpx.post", fail)
    with pytest.raises(AttemptFailure) as caught:
        AuditedProvider(profile()).complete_json("json", "input")
    error = caught.value
    assert len(calls) == 1 and calls[0]["timeout"] == 10
    assert error.code == "PROVIDER_TRANSPORT_UNKNOWN" and error.outcome == "unknown"
    assert error.usage is None and error.provider_request_id is None
    assert error.diagnostics["timeout_phase"] == phase
    assert error.diagnostics["configured_timeout_seconds"] == 10
    assert error.diagnostics["transport_exception"] == exception.__name__
    assert error.diagnostics["remote_outcome"] == "unknown"
    assert "secret-fixture" not in str(error.diagnostics) and "private.invalid" not in str(error.diagnostics)


def test_explicit_resume_recovers_failed_parse_without_changing_ledger_or_redispatch(client, monkeypatch):
    from uuid import uuid4
    from app.config import settings
    from app.models import GenerationJob
    from app.db import SessionLocal
    from app.v2 import inference, model_roles
    from app.v2.runtime import write, job, load, dump, claim_root
    from app.v2.storage import CallDispatch
    from app.v2.contracts import DomainError
    from test_generation_source_tools import running
    rid, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    configured = profile()
    configured.llm_model_family = "fixture"
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: configured)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {"max_output_tokens": 64, "context_tokens": 100000,
        "safety_margin_tokens": 0, "estimate_method": "fixture", "source_uri_or_config": "fixture"})
    class Provider:
        def complete_json(self, *a):
            raise inference.AttemptFailure("PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failed", {"total_tokens": 8}, "paid-once",
                {"validation_reason": "JSON_PARSE_FAILED", "finish_reason": "stop", "content_characters": 12},
                {"provider_content": '{"ok":true}}'})
    args = dict(stage="understanding", unit_id="A", data={"input": "same"}, system="JSON", provider_factory=lambda _: Provider())
    with pytest.raises(DomainError, match="PROVIDER_CALL_FAILED") as caught:
        inference.invoke(rid, "source-fixture", epoch, state, **args)
    assert caught.value.details["failure_code"] == "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"
    assert caught.value.details["failure_diagnostics"]["validation_reason"] == "JSON_PARSE_FAILED"
    assert caught.value.details["call_id"]
    child_run, child_id = uuid4().hex, uuid4().hex
    with write() as db:
        old = job(db, rid)
        old.status = "failed"
        saved = load(old.state_json)
        db.add(GenerationJob(id=child_id, run_id=child_run, root_job_id=child_id, parent_job_id=old.id,
            goal_id=old.goal_id, status="queued", job_type="task_v2", state_json=dump(saved), request_hash=uuid4().hex))
    lease = claim_root(child_run, "resume")
    def forbidden(_):
        raise AssertionError("must not dispatch again")
    result, audit = inference.invoke(child_run, "resume", lease["epoch"], state, **(args | {"provider_factory": forbidden}))
    assert result == {"ok": True} and audit["new_provider_dispatch"] is False
    assert audit["original_dispatch_outcome"] == "failed"
    with SessionLocal() as db:
        calls = db.query(CallDispatch).all()
        assert len(calls) == 1 and calls[0].outcome == "failed"
        assert load(calls[0].usage_json)["total_tokens"] == 8


def test_dispatch_uses_model_ceiling_even_when_caller_passes_a_small_hint(client, monkeypatch):
    """AI Edu must not turn a per-call planning hint into an internal output cap."""
    from app.config import settings
    from app.db import SessionLocal
    from app.v2 import inference, model_roles
    from app.v2.contracts import DomainError
    from app.v2.runtime import load
    from app.v2.storage import CallDispatch
    from test_generation_source_tools import running
    rid, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    configured = profile()
    configured.llm_model_family = "fixture"
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: configured)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {"max_output_tokens": 128000, "context_tokens": 1000000,
        "safety_margin_tokens": 0, "estimate_method": "fixture", "source_uri_or_config": "fixture"})
    captured = {}

    class LengthLimited:
        def complete_json(self, *a):
            raise inference.AttemptFailure("PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failed",
                {"prompt_tokens": 10, "completion_tokens": 128000, "total_tokens": 128010}, "paid-once",
                {"validation_reason": "OUTPUT_LENGTH_LIMIT", "finish_reason": "length", "content_characters": 0},
                {"provider_content": "", "diagnostics": {"validation_reason": "OUTPUT_LENGTH_LIMIT",
                    "finish_reason": "length", "content_characters": 0}})

    def factory(p):
        captured["limit"] = p.llm_max_tokens
        return LengthLimited()

    args = dict(stage="research_scope_review_knowledge_0_part_0", unit_id="A", data={"input": "same"},
        system="JSON", output_tokens=8192, provider_factory=factory)
    with pytest.raises(DomainError, match="PROVIDER_CALL_FAILED") as caught:
        inference.invoke(rid, "source-fixture", epoch, state, **args)
    assert captured["limit"] == 128000
    assert caught.value.details["failure_code"] == "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"
    with SessionLocal() as db:
        original = db.query(CallDispatch).one()
        assert original.outcome == "failed"
        assert load(original.request_json)["parameters"]["max_tokens"] == 128000


def test_resume_stops_instead_of_rebilling_an_identical_ceiling_failure(client, monkeypatch):
    """A length failure at the active ceiling is exhausted, not retried."""
    from uuid import uuid4
    from app.config import settings
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import inference, model_roles
    from app.v2.contracts import DomainError
    from app.v2.runtime import dump, job, load, claim_root, write
    from app.v2.storage import CallDispatch
    from test_generation_source_tools import running
    rid, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    configured = profile()
    configured.llm_model_family = "fixture"
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: configured)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {"max_output_tokens": 128000, "context_tokens": 1000000,
        "safety_margin_tokens": 0, "estimate_method": "fixture", "source_uri_or_config": "fixture"})

    class LengthLimited:
        def complete_json(self, *a):
            raise inference.AttemptFailure("PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failed",
                {"prompt_tokens": 10, "completion_tokens": 128000, "total_tokens": 128010}, "paid-once",
                {"validation_reason": "OUTPUT_LENGTH_LIMIT", "finish_reason": "length", "content_characters": 0},
                {"provider_content": "", "diagnostics": {"validation_reason": "OUTPUT_LENGTH_LIMIT",
                    "finish_reason": "length", "content_characters": 0}})

    args = dict(stage="checking_perturbation", unit_id="A", data={"input": "same"}, system="JSON",
        output_tokens=32768, provider_factory=lambda _: LengthLimited())
    with pytest.raises(DomainError, match="PROVIDER_CALL_FAILED"):
        inference.invoke(rid, "source-fixture", epoch, state, **args)
    child_run, child_id = uuid4().hex, uuid4().hex
    with write() as db:
        old = job(db, rid)
        old.status = "failed"
        parent_state = load(old.state_json)
        db.add(GenerationJob(id=child_id, run_id=child_run, root_job_id=child_id, parent_job_id=old.id,
            goal_id=old.goal_id, status="queued", job_type="task_v2", state_json=dump(parent_state),
            request_hash=uuid4().hex))
    lease = claim_root(child_run, "resume")

    def forbidden(_):
        raise AssertionError("an exhausted ceiling must not be re-dispatched")

    with pytest.raises(DomainError, match="OUTPUT_CAPACITY_EXHAUSTED"):
        inference.invoke(child_run, "resume", lease["epoch"], state,
                         **(args | {"provider_factory": forbidden}))
    with SessionLocal() as db:
        assert db.query(CallDispatch).count() == 1


def test_resume_may_dispatch_at_ceiling_after_a_smaller_historical_limit(client, monkeypatch):
    """A legacy 8k failure is allowed to acquire the higher model identity."""
    from uuid import uuid4
    from app.config import settings
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2 import inference, model_roles
    from app.v2.contracts import DomainError
    from app.v2.runtime import dump, job, load, claim_root, write
    from app.v2.storage import CallDispatch
    from test_generation_source_tools import running
    rid, epoch, state = running(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    configured = profile()
    configured.llm_model_family = "fixture"
    monkeypatch.setattr(model_roles, "for_role", lambda *a, **k: configured)
    monkeypatch.setattr(inference, "get_capacity", lambda *a: {"max_output_tokens": 128000, "context_tokens": 1000000,
        "safety_margin_tokens": 0, "estimate_method": "fixture", "source_uri_or_config": "fixture"})

    class LengthLimited:
        def complete_json(self, *a):
            raise inference.AttemptFailure("PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failed",
                {"prompt_tokens": 10, "completion_tokens": 8192, "total_tokens": 8202}, "paid-once",
                {"validation_reason": "OUTPUT_LENGTH_LIMIT", "finish_reason": "length", "content_characters": 0},
                {"provider_content": "", "diagnostics": {"validation_reason": "OUTPUT_LENGTH_LIMIT",
                    "finish_reason": "length", "content_characters": 0}})

    args = dict(stage="research_scope_review_knowledge_0_part_0", unit_id="A", data={"input": "same"},
        system="JSON", provider_factory=lambda _: LengthLimited())
    with pytest.raises(DomainError, match="PROVIDER_CALL_FAILED"):
        inference.invoke(rid, "source-fixture", epoch, state, **args)
    # Simulate the pre-fix request identity: a settled failure dispatched at 8k.
    with write() as db:
        prior = db.query(CallDispatch).one()
        request = load(prior.request_json)
        request["parameters"]["max_tokens"] = 8192
        prior.request_json = dump(request)
        prior.request_hash = prior.request_hash  # ledger hash retained; identity is re-derived below
        prior_hash = inference.request_hash(load(prior.manifest_json), request["messages"],
            prior.provider, prior.model, request["parameters"], request.get("output_schema", {}))
        prior.request_hash = prior_hash
    child_run, child_id = uuid4().hex, uuid4().hex
    with write() as db:
        old = job(db, rid)
        old.status = "failed"
        parent_state = load(old.state_json)
        db.add(GenerationJob(id=child_id, run_id=child_run, root_job_id=child_id, parent_job_id=old.id,
            goal_id=old.goal_id, status="queued", job_type="task_v2", state_json=dump(parent_state),
            request_hash=uuid4().hex))
    lease = claim_root(child_run, "resume")
    captured = {}

    class Ok:
        def complete_json(self, sys, user):
            return {"ok": True}, 1, {"total_tokens": 5}

    def factory(p):
        captured["limit"] = p.llm_max_tokens
        return Ok()

    result, audit = inference.invoke(child_run, "resume", lease["epoch"], state,
                                     **(args | {"provider_factory": factory}))
    assert result == {"ok": True}
    assert captured["limit"] == 128000
    assert audit["request_hash"] != prior_hash
    with SessionLocal() as db:
        assert db.query(CallDispatch).count() == 2

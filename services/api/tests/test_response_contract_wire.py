"""Response contracts are in the real message bytes, not only request hashes."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.v2.contracts import DomainError
from app.v2.inference import request_messages


def test_complete_response_contract_is_in_wire_without_mutating_task_or_caller():
    data = {"raw_input": "保留原文😀", "fields": {"f": "literal evidence"}}
    before = deepcopy(data)
    shape = {"assessments": [{"request_id": "copy exact supplied ID", "clauses": []}]}
    messages = request_messages("original system", data, shape)
    actual = json.loads(messages[1]["content"])
    assert actual == before == data
    from app.v2.contracts import canonical
    assert canonical(shape).decode() in messages[0]["content"]
    assert "at the JSON root" in messages[0]["content"]
    assert messages[0]["content"].startswith("original system")


def test_no_contract_preserves_exact_previous_messages():
    assert request_messages("same system", {"a": "unchanged"}) == [
        {"role": "system", "content": "same system"}, {"role": "user", "content": '{"a":"unchanged"}'}]


def test_nonobject_caller_fails_and_source_field_cannot_shadow_system_contract():
    with pytest.raises(DomainError, match="RESPONSE_CONTRACT_ENVELOPE_CONFLICT"):
        request_messages("system", [], {"real": "contract"})
    data = {"__aiedu_output_contract": {"untrusted": "source content only"}}
    messages = request_messages("system", data, {"real": "contract"})
    assert json.loads(messages[1]["content"]) == data
    assert '{"real":"contract"}' in messages[0]["content"]


@pytest.mark.parametrize("shape", [
    {"assessments": [{"request_id": "copy ID", "clauses": []}]},
    {"type": "object", "properties": {"assessments": {"type": "array"}}, "required": ["assessments"]},
])
def test_contract_reaches_http_payload_and_legacy_metadata_only_cache_is_not_reused(client, monkeypatch, shape):
    import httpx
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.contracts import Confirmation, Resume, StartRun, TaskInput, request_hash
    from app.v2.inference import invoke
    from app.v2.runtime import claim_root, dump, job, load, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import create_task, mutate_input, resume, snapshot, start_run

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr("app.v2.inference.get_capacity", lambda *args: {
        "max_output_tokens": 8192, "context_tokens": 1048576, "safety_margin_tokens": 1024,
        "estimate_method": "fixture", "source_uri_or_config": "fixture", "output_limit_parameter": "max_tokens"})
    created = create_task(TaskInput(title="contract fixture", raw_input="synthetic fixture only"), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    run = start_run(created["task_id"], StartRun(expected_revision=1,
        confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    lease = claim_root(run["run_id"], "fixture")
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    profile = SimpleNamespace(llm_base_url="https://contract.invalid/v1", llm_model="fixture",
        llm_model_family="fixture", llm_api_key="fixture-not-a-secret", llm_timeout=1,
        llm_temperature=0.3, llm_thinking="enabled", llm_reasoning_effort="high", llm_json_mode=True)
    payloads = []
    def post(url, *, headers, json, timeout, follow_redirects):
        assert url == "https://contract.invalid/v1/chat/completions"
        assert follow_redirects is False
        payloads.append(deepcopy(json))
        return httpx.Response(200, json={"id": "fixture-receipt", "choices": [{"finish_reason": "stop",
            "message": {"content": '{"assessments":[]}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
    monkeypatch.setattr("app.v2.inference.httpx.post", post)
    data = {"raw_input": "完整需求😀", "fields": {"item:a:answer.reasoning": "literal content"}}
    kwargs = dict(stage="criterion_assessing", unit_id="fixture-unit", system="fixture system", data=data,
        profile=profile, output_schema=shape)
    _, audit = invoke(run["run_id"], "fixture", lease["epoch"], state, **kwargs)
    from app.v2.contracts import canonical
    assert canonical(shape).decode() in payloads[0]["messages"][0]["content"]
    assert json.loads(payloads[0]["messages"][1]["content"]) == data
    assert payloads[0]["response_format"] == {"type": "json_object"}
    with write() as db:
        call = db.get(CallDispatch, audit["call_id"])
        saved, manifest = load(call.request_json), load(call.manifest_json)
        assert saved["messages"] == payloads[0]["messages"]
        assert call.request_hash == request_hash(manifest, saved["messages"], call.provider,
            call.model, saved["parameters"], shape)
        assert load(call.usage_json)["total_tokens"] == 15
        # Emulate the former bug: identical schema metadata, absent on the wire.
        saved["messages"] = request_messages("fixture system", data)
        call.request_json = dump(saved)
        call.request_hash = request_hash(manifest, saved["messages"], call.provider,
            call.model, saved["parameters"], shape)
        job(db, run["run_id"]).status = "failed"
    fixed = snapshot(created["task_id"])
    child = resume(run["run_id"], Resume(expected_revision=fixed["state_revision"],
        checkpoint_hash=fixed["checkpoint_hash"]), str(uuid4()))
    child_lease = claim_root(child["run_id"], "fixture")
    with SessionLocal() as db:
        child_state = load(job(db, child["run_id"]).state_json)
    _, corrected = invoke(child["run_id"], "fixture", child_lease["epoch"], child_state, **kwargs)
    assert len(payloads) == 2
    assert corrected["call_id"] != audit["call_id"]
    assert "cache" not in corrected

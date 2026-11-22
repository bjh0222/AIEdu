"""Resume compatibility for legacy JSON ordering, with no real provider calls."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.mark.parametrize("change", [
    "none", "object_order", "stem", "array_order", "system", "parameters", "endpoint",
    "model", "context", "plan", "schema", "entries", "late", "failed", "unknown", "corrupt_hash", "duplicate_keys",
])
def test_legacy_json_order_reuse_is_bound_to_successful_ancestor(client, monkeypatch, change):
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.contracts import Confirmation, DomainError, Resume, StartRun, TaskInput, digest, request_hash
    from app.v2.inference import invoke
    from app.v2.runtime import claim_root, dump, job, load, write
    from app.v2.storage import CallDispatch
    from app.v2.tasks import create_task, mutate_input, resume, snapshot, start_run

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr("app.v2.inference.get_capacity", lambda *a: {
        "max_output_tokens": 128000, "context_tokens": 1048576, "safety_margin_tokens": 1024,
        "estimate_method": "fixture", "source_uri_or_config": "fixture", "output_limit_parameter": "max_tokens"})
    created = create_task(TaskInput(title="cache fixture", raw_input="synthetic cache fixture"), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    run = start_run(created["task_id"], StartRun(expected_revision=1,
        confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    lease = claim_root(run["run_id"], "fixture")
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    profile = SimpleNamespace(llm_base_url="https://api.deepseek.com/v1", llm_model="deepseek-flash",
        llm_model_family="deepseek", llm_temperature=0.3, llm_thinking="enabled",
        llm_reasoning_effort="high", llm_json_mode=True)
    dispatched = []

    class Provider:
        def complete_json(self, system, user):
            dispatched.append((system, user))
            return {"result": "fixture"}, 1, {"total_tokens": 2}

    data = {"task": "fixture", "items": [{"stem": "literal", "id": "one"}, {"id": "two"}]}
    kwargs = dict(stage="item_reviewing", unit_id="unit", data=data, system="fixture system",
        profile=profile, provider_factory=lambda _: Provider())
    result, audit = invoke(run["run_id"], "fixture", lease["epoch"], state, **kwargs)
    with write() as db:
        call = db.get(CallDispatch, audit["call_id"])
        saved, manifest = load(call.request_json), load(call.manifest_json)
        saved["messages"][1]["content"] = json.dumps(data)  # historical insertion order
        if change == "duplicate_keys":
            saved["messages"][1]["content"] = '{"task":"discarded",' + saved["messages"][1]["content"][1:]
        call.request_json = dump(saved)
        call.request_hash = request_hash(manifest, saved["messages"], call.provider,
            call.model, saved["parameters"], saved.get("output_schema", {}))
        legacy_hash = call.request_hash
        if change == "corrupt_hash":
            call.request_hash = digest("corrupt")
        if change in {"late", "failed"}:
            call.status = "late" if change == "late" else "settled"
            call.outcome = "success" if change == "late" else "failed"
        if change == "unknown":
            call.status, call.outcome = "unknown", "unknown"
        job(db, run["run_id"]).status = "failed"
    before = snapshot(created["task_id"])
    if change == "unknown":
        with pytest.raises(DomainError, match="UNKNOWN_QUARANTINE_REQUIRED"):
            resume(run["run_id"], Resume(expected_revision=before["state_revision"],
                checkpoint_hash=before["checkpoint_hash"]), str(uuid4()))
        assert len(dispatched) == 1
        return
    child = resume(run["run_id"], Resume(expected_revision=before["state_revision"],
        checkpoint_hash=before["checkpoint_hash"]), str(uuid4()))
    child_lease = claim_root(child["run_id"], "fixture")
    with SessionLocal() as db:
        child_state = load(job(db, child["run_id"]).state_json)
    if change == "object_order":
        kwargs["data"] = {"items": [{"id": "one", "stem": "literal"}, {"id": "two"}], "task": "fixture"}
    elif change == "stem":
        kwargs["data"] = {**data, "items": [{"stem": "changed", "id": "one"}, {"id": "two"}]}
    elif change == "array_order":
        kwargs["data"] = {**data, "items": list(reversed(data["items"]))}
    elif change == "system":
        kwargs["system"] = "different system"
    elif change == "parameters":
        profile.llm_reasoning_effort = "low"
    elif change == "endpoint":
        profile.llm_base_url = "https://other.invalid/v1"
    elif change == "model":
        profile.llm_model = "other"
    elif change == "context":
        child_state["context"]["context_hash"] = digest("other context")
    elif change == "plan":
        child_state["plan"]["plan_hash"] = digest("other plan")
    elif change == "schema":
        kwargs["output_schema"] = {"type": "object"}
    elif change == "entries":
        kwargs["entries"] = [{"source_id": "other"}]
    second, reused = invoke(child["run_id"], "fixture", child_lease["epoch"], child_state, **kwargs)
    if change in {"none", "object_order"}:
        assert len(dispatched) == 1 and second == result
        assert reused["call_id"] == audit["call_id"]
        assert reused["request_hash"] == legacy_hash
        assert reused["identity_normalization"] == "json_object_order_v1"
        assert reused["requested_request_hash"] != legacy_hash
    else:
        assert len(dispatched) == 2 and "cache" not in reused


def test_message_normalization_preserves_literal_strings_and_array_order():
    from app.v2.inference import canonical_messages
    first = [{"role": "system", "content": "literal system"},
             {"role": "user", "content": '{"b":[2,1], "a":" x  y "}'}]
    second = [{"role": "system", "content": "literal system"},
              {"role": "user", "content": '{"a":" x  y ","b":[2,1]}'}]
    assert canonical_messages(first) == canonical_messages(second)
    for text in ('{"a":1,"a":2}', '{"a":NaN}'):
        with pytest.raises(ValueError):
            canonical_messages([first[0], {"role": "user", "content": text}])

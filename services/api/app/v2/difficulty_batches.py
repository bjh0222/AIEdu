"""Bounded difficulty requests, preserving exact successful legacy batches.

No retries are dispatched here. An explicit resume can reuse a successful
three-item request, or submit smaller one-item requests with more output room.
A failed minimum-size request is a stopping condition, not an endless retry.
"""
from copy import deepcopy

from .contracts import DomainError, request_hash

METHOD = "difficulty-one-item-batch.v1"


def matching_attempts(calls, data, schema, system, profile, output_tokens):
    from .capacity import get_capacity, planned_output_tokens, request_capacity
    from .inference import request_messages

    messages = request_messages(system, data, schema)
    capacity = get_capacity(profile.llm_base_url, profile.llm_model)
    reserve = planned_output_tokens(profile, output_tokens, capacity)["reserved_output_tokens"]
    estimate = request_capacity(messages, capacity, reserve)
    limit = capacity.get("output_limit_parameter", "max_tokens")
    params = {limit: estimate["reserved_output_tokens"],
        "temperature": getattr(profile, "llm_temperature", None),
        "thinking": getattr(profile, "llm_thinking", None),
        "reasoning_effort": getattr(profile, "llm_reasoning_effort", None),
        "json_mode": getattr(profile, "llm_json_mode", True)}
    matched = []
    for call in calls:
        if (call["provider"] != profile.llm_base_url or call["model"] != profile.llm_model
                or call["model_family"] != getattr(profile, "llm_model_family", "unknown")):
            continue
        manifest = call["manifest"]
        expected = request_hash(manifest, messages, profile.llm_base_url, profile.llm_model, params, schema)
        if expected == call["request_hash"]:
            matched.append(call)
    return matched


def select_batches(data, legacy_matches, minimum_matches):
    """Pure decision gate: successful receipts win; missing work is not unknown scoring."""
    if any(c["status"] == "settled" and c["outcome"] == "success" for c in legacy_matches):
        return [(data, 16384)]
    parts = []
    for item in data["items"]:
        part = deepcopy(data)
        part["items"] = [deepcopy(item)]
        matches = minimum_matches.get(item["item_id"], [])
        success = any(c["status"] == "settled" and c["outcome"] == "success" for c in matches)
        if not success and any(c["status"] in {"settled", "late", "unknown", "quarantined"} for c in matches):
            raise DomainError("DIFFICULTY_REVIEW_MINIMUM_BATCH_EXHAUSTED", 422,
                item_id=item["item_id"], prior_call_ids=[c["call_id"] for c in matches])
        parts.append((part, 32768))
    return parts


def resume_batches(run_id, state, unit_id, data, schema, system):
    # Synthetic/pure consumers have no durable ancestry and need no DB access.
    if not state.get("artifact_id") or not state.get("context", {}).get("context_hash"):
        return select_batches(data, [], {})
    from sqlalchemy import select
    from ..db import SessionLocal
    from ..models import GenerationJob
    from .model_roles import for_role
    from .runtime import load
    from .storage import CallDispatch

    with SessionLocal() as db:
        root = db.query(GenerationJob).filter_by(run_id=run_id, job_type="task_v2").first()
        if not root or not root.parent_job_id:
            return select_batches(data, [], {})
        chain = select(GenerationJob.id, GenerationJob.parent_job_id).where(
            GenerationJob.id == root.parent_job_id).cte("difficulty_ancestors", recursive=True)
        chain = chain.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(
            chain, GenerationJob.id == chain.c.parent_job_id))
        rows = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(chain.c.id)),
            CallDispatch.unit_id == unit_id, CallDispatch.input_hash == state["context"]["context_hash"]).all()
        calls = []
        for row in rows:
            manifest = load(row.manifest_json)
            if (manifest.get("stage") != "difficulty_reviewing" or manifest.get("role") != "review"
                    or manifest.get("plan_hash") != state["plan"]["plan_hash"]):
                continue
            calls.append({k: getattr(row, k) for k in ("call_id", "request_hash", "provider", "model",
                "model_family", "status", "outcome")} | {"manifest": manifest})
    profile = for_role("review")
    legacy = matching_attempts(calls, data, schema, system, profile, 16384) if calls else []
    minimum = {}
    for item in data["items"]:
        part = deepcopy(data)
        part["items"] = [deepcopy(item)]
        minimum[item["item_id"]] = matching_attempts(calls, part, schema, system, profile, 32768) if calls else []
    return select_batches(data, legacy, minimum)

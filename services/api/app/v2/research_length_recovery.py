"""Lossless partition ONLY after an exact, settled ancestral length failure.

Not a transport retry, timeout recovery, rubric change or parameter fallback.
Only an explicit product resume can create the required child run. A failed
single-dimension request stops: there is no recursive/sub-indicator splitting.
"""
from copy import deepcopy

from .contracts import DomainError, request_hash
from .skills import entry as skill_entry, review_skill

METHOD = "research-rubric-batch.v3-output-length-recovery"
# Planning hint only. The dispatch allowance is the model capacity (or the
# explicit user override), never an AI Edu constant.
REVIEW_OUTPUT_TOKENS = None
LEGACY_REVIEW_OUTPUT_TOKENS = (65536, 32768, 16384, 8192, 6144)
C_REVIEW_ENTRY = skill_entry(review_skill(), "C")


def split_data(data, failed_call_id, failed_request_hash):
    rubric = data["research_rubric"]
    dimensions = rubric["dimensions"]
    targets = [d.get("dimension_id") or d.get("id") for d in dimensions]
    if (targets != data["target_dimension_ids"] or len(set(targets)) != len(targets)
            or not targets or len(targets) > 2):
        raise DomainError("RESEARCH_LENGTH_RECOVERY_TARGETS_INVALID", 422)
    if len(targets) == 1:
        raise DomainError("RESEARCH_REVIEW_MINIMUM_BATCH_EXHAUSTED", 422)
    parts = []
    available = {c["claim_id"] for c in rubric.get("source_claims", [])}
    for dimension in dimensions:
        references = set(dimension.get("source_refs", []))
        if not references <= available:
            raise DomainError("RESEARCH_DIMENSION_SOURCE_UNBOUND", 422)
        part = deepcopy(data)
        scoped = part["research_rubric"]
        scoped["dimensions"] = [deepcopy(dimension)]
        scoped["source_claims"] = [deepcopy(c) for c in rubric.get("source_claims", []) if c["claim_id"] in references]
        source_ids = {c.get("source_id") for c in scoped["source_claims"]}
        if "source_catalog" in scoped:
            scoped["source_catalog"] = [s for s in scoped["source_catalog"] if s.get("source_id") in source_ids]
        part["source_claims"] = deepcopy(scoped["source_claims"])
        part["target_dimension_ids"] = [dimension.get("dimension_id") or dimension["id"]]
        part["review_batch_version"] = METHOD
        part["partition_recovery"] = {"failed_call_id": failed_call_id,
            "failed_request_hash": failed_request_hash, "reason": "settled_empty_output_length_limit",
            "retained_full_rubric_hash": data["full_rubric_hash"],
            "parent_target_dimension_ids": targets}
        # The full unit, A targets, indicator definitions/anchors, source claims
        # and whole-rubric binding never become a lossy summary.
        parts.append(part)
    return parts


def _identity_matches(call, manifest, messages, profile, parameters):
    """Exact identity first, then the same request with its output ceiling raised."""
    provider, model = profile.llm_base_url, profile.llm_model
    current = request_hash(manifest, messages, provider, model, parameters, {})
    if call["request_hash"] == current:
        return True
    saved = call.get("parameters")
    if not isinstance(saved, dict) or not saved:
        return False
    # Verify the stored hash describes the stored request before normalizing:
    # a corrupt or mismatched row must never be treated as the same request.
    if request_hash(manifest, messages, provider, model, saved, {}) != call["request_hash"]:
        return False
    normalized = {k: v for k, v in saved.items() if k not in {"max_tokens", "max_completion_tokens"}}
    normalized.update({k: v for k, v in parameters.items() if k in {"max_tokens", "max_completion_tokens"}})
    if normalized == saved:
        return False
    return request_hash(manifest, messages, provider, model, normalized, {}) == current


def matching_length_failure(calls, manifest, messages, profile, parameters):
    """Pure identity gate; a successful exact request always wins over failure."""
    failures = []
    for call in calls:
        if (call["provider"] != profile.llm_base_url
                or call["model"] != profile.llm_model or call["status"] != "settled"):
            continue
        if not _identity_matches(call, manifest, messages, profile, parameters):
            continue
        if call["outcome"] == "success":
            return None
        receipt = call["receipt"]
        diagnostic = receipt.get("failure_diagnostics") or {}
        if (call["outcome"] == "failed" and receipt.get("failure_code") == "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"
                and diagnostic.get("finish_reason") == "length"
                and diagnostic.get("validation_reason") == "OUTPUT_LENGTH_LIMIT"
                and diagnostic.get("content_characters") == 0):
            failures.append(call)
    return failures[-1] if failures else None


def resume_partitions(run_id, state, data, system):
    """Read-only ancestry inspection; no dispatch and no implicit new run."""
    # Non-durable pure consumers do not have a resume ancestry to recover.
    if not state.get("artifact_id") or not state.get("context", {}).get("context_hash"):
        return [data]
    from sqlalchemy import select
    from ..db import SessionLocal
    from ..models import GenerationJob
    from .capacity import get_capacity, planned_output_tokens, request_capacity
    from .inference import request_messages
    from .model_roles import for_role
    from .runtime import load
    from .storage import CallDispatch

    with SessionLocal() as db:
        root = db.query(GenerationJob).filter_by(run_id=run_id, job_type="task_v2").first()
        if not root or not root.parent_job_id:
            return [data]
        chain = select(GenerationJob.id, GenerationJob.parent_job_id).where(
            GenerationJob.id == root.parent_job_id).cte("research_length_ancestors", recursive=True)
        chain = chain.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(
            chain, GenerationJob.id == chain.c.parent_job_id))
        rows = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(chain.c.id)),
            CallDispatch.unit_id == data["unit"]["unit_id"],
            CallDispatch.input_hash == state["context"]["context_hash"]).order_by(
                CallDispatch.started_at, CallDispatch.call_id).all()
        calls = [{k: getattr(row, k) for k in ("call_id", "request_hash", "provider", "model", "status", "outcome")}
            | {"receipt": load(row.receipt_json),
               "parameters": (load(row.request_json) or {}).get("parameters", {}) if row.request_json else {}}
            for row in rows]
    if not any(c["outcome"] == "failed" and c["receipt"].get("failure_code") == "PROVIDER_OUTPUT_INVALID_OR_TRUNCATED"
            for c in calls):
        return [data]
    profile = for_role("review")
    messages = request_messages(system, data, ensure_json_literal=True)
    capacity = get_capacity(profile.llm_base_url, profile.llm_model)
    current_plan = planned_output_tokens(profile, REVIEW_OUTPUT_TOKENS, capacity)
    limit_parameter = current_plan["output_limit_parameter"]
    if limit_parameter not in {"max_tokens", "max_completion_tokens"}:
        raise DomainError("CAPACITY_PARAMETER_INVALID", 422)
    manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": state["context"]["context_hash"],
        "plan_hash": state["plan"]["plan_hash"], "unit_id": data["unit"]["unit_id"],
        "stage": "rubric_reviewing", "role": "review", "entries": [C_REVIEW_ENTRY]}

    def failed_at_budget(budget, payload):
        planned = planned_output_tokens(profile, budget, capacity)
        estimate = request_capacity(
            request_messages(system, payload, ensure_json_literal=True), capacity,
            planned["reserved_output_tokens"])
        params = {planned["output_limit_parameter"]: estimate["reserved_output_tokens"],
            "temperature": getattr(profile, "llm_temperature", None), "thinking": getattr(profile, "llm_thinking", None),
            "reasoning_effort": getattr(profile, "llm_reasoning_effort", None),
            "json_mode": getattr(profile, "llm_json_mode", True)}
        return matching_length_failure(
            calls, manifest, request_messages(system, payload, ensure_json_literal=True), profile, params)

    # The current request already uses the model ceiling (or the user override).
    # Identity normalization lets a failure recorded under any smaller legacy
    # budget still be recognized, while never treating a different prompt as
    # the same failure.
    failed = failed_at_budget(REVIEW_OUTPUT_TOKENS, data)
    if not failed:
        return [data]
    parts = split_data(data, failed["call_id"], failed["request_hash"])
    for part in parts:
        child_failure = failed_at_budget(REVIEW_OUTPUT_TOKENS, part)
        if child_failure:
            # Every indicator of one dimension must stay together. Do not
            # replay the failed single request or fragment its scoring rule.
            raise DomainError("RESEARCH_REVIEW_MINIMUM_BATCH_EXHAUSTED", 422,
                failed_call_id=child_failure["call_id"], target_dimension_ids=part["target_dimension_ids"])
    return parts

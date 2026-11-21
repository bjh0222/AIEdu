"""Exact, owner-authorized read-wait increases; never a provider settlement."""
from .contracts import DomainError

VERSION = "explicit-read-wait-retry.v1"
MODEL_PARAMETERS = ("temperature", "thinking", "reasoning_effort", "json_mode")


def effective_read_wait(profile):
    value = getattr(profile, "llm_read_timeout", None)
    return value if value is not None else profile.llm_timeout


def model_parameters(profile):
    return {name: getattr(profile, "llm_" + name, True if name == "json_mode" else None)
            for name in MODEL_PARAMETERS}


def recorded_read_wait(request):
    transport = request.get("transport", {})
    value = transport.get("read_timeout_seconds", transport.get("configured_timeout_seconds"))
    return value if type(value) in {int, float} and value > 0 else None


def authorize(db, owner, state, entries, action_hash):
    """Validate grants in the resume transaction; no dispatch or ledger write."""
    if not entries:
        return {}
    from sqlalchemy import select
    from ..models import GenerationJob
    from .model_roles import for_role
    from .runtime import load
    from .storage import CallDispatch
    profile = for_role("review")
    chain = select(GenerationJob.id, GenerationJob.parent_job_id).where(GenerationJob.id == owner.id).cte("transport_grant_ancestors", recursive=True)
    chain = chain.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(chain, GenerationJob.id == chain.c.parent_job_id))
    ancestors = set(db.scalars(select(chain.c.id)))
    grants = {}
    for entry in entries:
        cid = str(entry.call_id)
        if cid in grants:
            raise DomainError("TRANSPORT_RETRY_DUPLICATE_CALL", 422)
        call = db.get(CallDispatch, cid)
        if not call or call.root_job_id not in ancestors:
            raise DomainError("TRANSPORT_RETRY_CALL_NOT_FOUND", 422)
        manifest, request, receipt = load(call.manifest_json), load(call.request_json), load(call.receipt_json)
        old = db.get(GenerationJob, call.root_job_id)
        isolation = load(old.state_json).get("unknown_quarantines", {}).get(cid, {})
        previous = recorded_read_wait(request)
        if (not entry.acknowledge_possible_duplicate_cost or call.status != "quarantined" or call.outcome != "unknown"
                or call.request_hash != entry.request_hash or call.input_hash != state["context"]["context_hash"]
                or manifest.get("plan_hash") != state["plan"]["plan_hash"]
                or manifest.get("stage") not in {"checking", "checking_perturbation"}
                or receipt.get("failure_code") != "PROVIDER_TRANSPORT_UNKNOWN"
                or isolation.get("request_hash") != call.request_hash
                or isolation.get("acknowledge_possible_duplicate_cost") is not True):
            raise DomainError("TRANSPORT_RETRY_EXACT_ISOLATION_REQUIRED", 422)
        parameters = {name: request.get("parameters", {}).get(name) for name in MODEL_PARAMETERS}
        if (call.provider != profile.llm_base_url or call.model != profile.llm_model
                or parameters != model_parameters(profile)
                or effective_read_wait(profile) != entry.read_timeout_seconds
                or entry.read_timeout_seconds <= entry.previous_read_timeout_seconds
                or (previous is not None and previous != entry.previous_read_timeout_seconds)):
            raise DomainError("TRANSPORT_RETRY_WAIT_OR_MODEL_MISMATCH", 422)
        grants[cid] = {"method_version": VERSION, **entry.model_dump(mode="json"), "request_action_hash": action_hash,
            "provider": call.provider, "model": call.model, "model_parameters": parameters,
            "configured_timeout_seconds": profile.llm_timeout,
            "previous_wait_source": "recorded_request" if previous is not None else "explicit_user_declared_legacy_limit",
            "provider_settlement": "unresolved"}
    return grants


def permitted(call, request, grant, profile):
    """A later unknown needs its own grant; an inherited earlier one cannot fit."""
    if not isinstance(grant, dict):
        return False
    previous = recorded_read_wait(request)
    declared = grant.get("previous_read_timeout_seconds")
    requested = grant.get("read_timeout_seconds")
    return (grant.get("method_version") == VERSION and grant.get("call_id") == call.call_id
        and grant.get("request_hash") == call.request_hash and grant.get("acknowledge_possible_duplicate_cost") is True
        and grant.get("provider") == call.provider == profile.llm_base_url
        and grant.get("model") == call.model == profile.llm_model
        and type(declared) is int and type(requested) is int and declared < requested <= 600
        and (previous is None or previous == declared) and effective_read_wait(profile) == requested
        and grant.get("configured_timeout_seconds") == profile.llm_timeout
        and grant.get("model_parameters") == model_parameters(profile)
        and grant.get("model_parameters") == {k: request.get("parameters", {}).get(k) for k in MODEL_PARAMETERS})

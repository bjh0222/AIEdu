"""Sourced model capacities. Unknown means no dispatch, not guessed from model names."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import DomainError, canonical

# The provider-facing output allowance is never an AI Edu policy cap. It is the
# sourced model maximum, optionally reduced by the user's explicit profile
# override. Per-call ``requested_tokens`` values are planning hints only: they
# let a caller say "about this much output is expected" for batching, capacity
# estimation and traceability, but they must never silently truncate a response.
MIN_USABLE_OUTPUT_TOKENS = 1


def get_capacity(base_url, model):
    data = json.loads(Path(__file__).with_name("model_capacities.json").read_text(encoding="utf-8"))
    return data["models"].get(base_url.rstrip("/")+"|"+model)


def request_capacity(messages, capacity, output_tokens=None):
    """Estimate input size and return the largest output the context can hold.

    ``reserved_output_tokens`` is the value that may actually be sent to the
    provider. When the model maximum cannot fit beside the estimated input, the
    allowance is reduced to the remaining context and flagged explicitly; if
    even a minimal response cannot fit, the dispatch fails instead of sending a
    request that would be rejected or silently truncated.
    """
    if not capacity:
        raise DomainError("CAPACITY_UNKNOWN", 422)
    requested_output = output_tokens or capacity["max_output_tokens"]
    estimated = len(canonical(messages))+capacity["safety_margin_tokens"]
    context = capacity["context_tokens"]
    if estimated >= context:
        raise DomainError("CALL_CAPACITY_EXCEEDED", 422, estimate_method=capacity["estimate_method"],
                          estimated_input_tokens=estimated, reserved_output_tokens=0,
                          context_tokens=context)
    available = context - estimated
    allowed = min(requested_output, available)
    if allowed < MIN_USABLE_OUTPUT_TOKENS:
        raise DomainError("CALL_CAPACITY_EXCEEDED", 422, estimate_method=capacity["estimate_method"],
                          estimated_input_tokens=estimated, reserved_output_tokens=allowed,
                          context_tokens=context, requested_output_tokens=requested_output)
    return {"estimated_input_tokens": estimated, "reserved_output_tokens": allowed,
            "requested_reserved_output_tokens": requested_output,
            "context_limited": allowed < requested_output,
            "estimate_method": capacity["estimate_method"], "capacity_source": capacity["source_uri_or_config"]}


def planned_output_tokens(profile, requested_tokens=None, capacity=None):
    """Resolve the provider-facing output allowance.

    Precedence is: the sourced model maximum, reduced only by an explicit user
    ``output_limit_tokens`` override. ``requested_tokens`` no longer caps the
    dispatch; it is recorded as the caller's expected-output hint. This keeps a
    stage such as research scope review from imposing an internal 8k ceiling
    that truncates a reasoning model's response.
    """
    capacity = capacity or get_capacity(profile.llm_base_url, profile.llm_model)
    if not capacity:
        raise DomainError("CAPACITY_UNKNOWN", 422)
    maximum = capacity.get("max_output_tokens")
    if type(maximum) is not int or maximum <= 0:
        raise DomainError("CAPACITY_UNKNOWN", 422)
    override = getattr(profile, "llm_output_limit_tokens", None)
    if override is not None:
        if type(override) is not int or override <= 0 or override > maximum:
            raise DomainError(
                "MODEL_OUTPUT_LIMIT_INVALID", 422,
                configured=override, max_output_tokens=maximum,
            )
    if requested_tokens is not None and (type(requested_tokens) is not int or requested_tokens <= 0):
        raise DomainError("MODEL_OUTPUT_REQUEST_INVALID", 422)
    ceiling = maximum if override is None else min(maximum, override)
    parameter = capacity.get("output_limit_parameter", "max_tokens")
    if parameter not in {"max_tokens", "max_completion_tokens"}:
        raise DomainError("CAPACITY_PARAMETER_INVALID", 422)
    return {
        "requested_output_tokens": requested_tokens,
        "user_output_limit_tokens": override,
        "reserved_output_tokens": ceiling,
        "capacity_max_output_tokens": maximum,
        "output_limit_parameter": parameter,
        "capacity_source": capacity.get("source_uri_or_config"),
    }

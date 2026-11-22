"""Sourced model capacities. Unknown means no dispatch, not guessed from model names."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import DomainError, canonical


def get_capacity(base_url, model):
    data = json.loads(Path(__file__).with_name("model_capacities.json").read_text(encoding="utf-8"))
    return data["models"].get(base_url.rstrip("/")+"|"+model)


def request_capacity(messages, capacity, output_tokens=None):
    if not capacity:
        raise DomainError("CAPACITY_UNKNOWN", 422)
    output = output_tokens or capacity["max_output_tokens"]
    estimated = len(canonical(messages))+capacity["safety_margin_tokens"]
    if estimated+output > capacity["context_tokens"]:
        raise DomainError("CALL_CAPACITY_EXCEEDED", 422, estimate_method=capacity["estimate_method"],
                          estimated_input_tokens=estimated, reserved_output_tokens=output)
    return {"estimated_input_tokens": estimated, "reserved_output_tokens": output,
            "estimate_method": capacity["estimate_method"], "capacity_source": capacity["source_uri_or_config"]}


def planned_output_tokens(profile, requested_tokens=None, capacity=None):
    """Resolve requested output against the profile override and sourced capacity."""
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
    requested = maximum if requested_tokens is None else requested_tokens
    if type(requested) is not int or requested <= 0:
        raise DomainError("MODEL_OUTPUT_REQUEST_INVALID", 422)
    effective = min(requested, maximum, override or maximum)
    parameter = capacity.get("output_limit_parameter", "max_tokens")
    if parameter not in {"max_tokens", "max_completion_tokens"}:
        raise DomainError("CAPACITY_PARAMETER_INVALID", 422)
    return {
        "requested_output_tokens": requested,
        "user_output_limit_tokens": override,
        "reserved_output_tokens": effective,
        "capacity_max_output_tokens": maximum,
        "output_limit_parameter": parameter,
        "capacity_source": capacity.get("source_uri_or_config"),
    }

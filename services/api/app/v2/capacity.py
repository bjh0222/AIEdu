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

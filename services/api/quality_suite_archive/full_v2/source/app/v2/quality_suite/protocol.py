from __future__ import annotations

from copy import deepcopy

from ..contracts import DomainError, digest

FULL_V1 = "full_v1"
CORRECTNESS_ONLY_V1 = "correctness_only.v1"
SUPPORTED_PROFILES = {FULL_V1, CORRECTNESS_ONLY_V1}
VERSION = "quality-suite-protocol.v1"


def normalize_quality_profile(value=None) -> dict:
    if value is None:
        value = FULL_V1
    if isinstance(value, dict):
        value = value.get("id")
    if value not in SUPPORTED_PROFILES:
        raise DomainError("QUALITY_PROFILE_UNSUPPORTED", 422, quality_profile=value)
    adapter = "full" if value == FULL_V1 else "correctness_only"
    result = {
        "id": value,
        "version": VERSION,
        "adapter": adapter,
    }
    result["protocol_hash"] = digest(result)
    return result


def frozen_quality_profile(state: dict) -> dict:
    frozen = state.get("quality_profile")
    if isinstance(frozen, dict) and frozen.get("id") in SUPPORTED_PROFILES:
        return deepcopy(frozen)
    execution = state.get("execution_policy") or {}
    value = execution.get("quality_profile")
    return normalize_quality_profile(value if value is not None else FULL_V1)


def quality_profile_is_correctness_only(state: dict) -> bool:
    return frozen_quality_profile(state)["id"] == CORRECTNESS_ONLY_V1

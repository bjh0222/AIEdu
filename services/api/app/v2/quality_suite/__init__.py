from .protocol import (
    CORRECTNESS_ONLY_V1,
    FULL_V1,
    frozen_quality_profile,
    normalize_quality_profile,
    quality_profile_is_correctness_only,
)

__all__ = [
    "CORRECTNESS_ONLY_V1",
    "FULL_V1",
    "frozen_quality_profile",
    "normalize_quality_profile",
    "quality_profile_is_correctness_only",
]


def evaluate(run_id, worker, epoch, state, units, version_id, provider_factory=None, **kwargs):
    if frozen_quality_profile(state)["id"] == CORRECTNESS_ONLY_V1:
        from .correctness_only_v1 import evaluate
        return evaluate(run_id, worker, epoch, state, units, version_id, provider_factory)
    from ..checker import evaluate
    return evaluate(run_id, worker, epoch, state, units, version_id, provider_factory, **kwargs)

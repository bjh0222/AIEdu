"""A durable correction must not change identity when its source is cached."""
from app.v2.contracts import digest
from app.v2.criterion_protocol import bind_observations, correction_payload


def payload(audit):
    request = {"request_id": "r", "check_id": "C3.correctness", "item_id": None,
        "criterion": {"clauses": {"requirement": "Actual content must support this conclusion"},
            "criterion_hash": "frozen-criterion"}}
    data = {"requests": [request], "fields": {"unit:body": "Fixed source body, never rewritten."}, "task": {}, "sources": []}
    output = {"assessments": []}
    bound = bind_observations(output, [request], data["fields"], audit, "u")
    return correction_payload(data, output, bound, attempt=1)


def test_identical_source_receipt_uses_same_correction_identity_after_cache_reuse():
    fresh = {"call_id": "source-call", "request_hash": "source-request", "latency_ms": 1500,
        "usage": {"total_tokens": 100}, "response_normalization": None, "model_family": "fixture"}
    cached = {"call_id": "source-call", "request_hash": "source-request", "latency_ms": 0,
        "usage": {"total_tokens": 100}, "cache": "identical_ancestral_request", "requested_request_hash": "source-request",
        "identity_normalization": None, "model_family": "fixture"}
    assert payload(fresh) == payload(cached)
    # A genuinely different receipt is not retroactively rebound to the first.
    assert digest(payload(fresh | {"call_id": "different-call"})) != digest(payload(cached))

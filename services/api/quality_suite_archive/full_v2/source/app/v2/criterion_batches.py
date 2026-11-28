"""Lossless execution partitions, not a new rubric or scoring threshold."""
from .contracts import DomainError, digest

VERSION = "criterion-target-batches.v1"
MAX_CLAUSES = 6


def batches(requests):
    """Keep each request/criterion intact; group by item before bounded packing.

    All original content fields and peer questions remain in the caller's
    payload. Six is an engineering output-size budget, not an academic cutoff.
    An indivisible large criterion stays whole and is never silently trimmed.
    """
    ids = [r["request_id"] for r in requests]
    if len(ids) != len(set(ids)):
        raise DomainError("CRITERION_REQUEST_DUPLICATE", 422)
    groups = {}
    for request in requests:
        groups.setdefault(request.get("item_id"), []).append(request)
    result = []
    for group in groups.values():
        pending, count = [], 0
        for request in group:
            size = len(request["criterion"]["clauses"])
            if pending and count + size > MAX_CLAUSES:
                result.append(pending)
                pending, count = [], 0
            pending.append(request)
            count += size
        if pending:
            result.append(pending)
    assert sorted(r["request_id"] for batch in result for r in batch) == sorted(ids)
    return result


def batch_payload(data, requests):
    """No truncation, source filtering, clause splitting or semantic rewrite."""
    return {**data, "requests": requests, "execution_batch": {
        "method_version": VERSION,
        "batch_hash": digest(requests),
        "full_request_set_hash": digest(data["requests"]),
        "full_request_count": len(data["requests"]),
        "clause_count": sum(len(r["criterion"]["clauses"]) for r in requests),
        "partial_execution_not_partial_scope": True,
    }}

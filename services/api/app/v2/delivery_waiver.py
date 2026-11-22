"""Versioned user confirmation for only the C3/G2 soft delivery blockers."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from .contracts import DomainError, digest

VERSION = "delivery-waiver.v1"
SOFT_ITEM_IDS = frozenset({"C3.correctness", "G2.knowledge_integration"})


def _criterion_hashes(plan):
    return {
        item["item_id"]: (item.get("criterion_contract") or {}).get("criterion_hash")
        for item in plan.get("check_items", [])
        if item.get("item_id") in SOFT_ITEM_IDS
    }


def build_delivery_waiver(
    plan,
    quality,
    item_results,
    confirmation_event,
    *,
    signer="local-owner",
    confirmed_at=None,
):
    item_ids = sorted(set(item_results or {}))
    if not item_ids or not set(item_ids) <= SOFT_ITEM_IDS:
        raise DomainError("DELIVERY_WAIVER_ITEM_INVALID", 422, item_ids=item_ids)
    hashes = _criterion_hashes(plan)
    if any(not hashes.get(item_id) for item_id in item_ids):
        raise DomainError("DELIVERY_WAIVER_CRITERION_MISSING", 422, item_ids=item_ids)
    if not isinstance(confirmation_event, dict) or not confirmation_event.get("event_id"):
        raise DomainError("DELIVERY_WAIVER_CONFIRMATION_REQUIRED", 422)
    payload = {
        "schema_version": VERSION,
        "status": "active",
        "signer": signer,
        "confirmed_at": confirmed_at or datetime.now(timezone.utc).isoformat(),
        "confirmation_event": deepcopy(confirmation_event),
        "check_plan_hash": plan.get("check_plan_hash"),
        "criterion_contract_version": plan.get("assessment_contract"),
        "soft_items": [
            {
                "item_id": item_id,
                "criterion_hash": hashes[item_id],
                "actual_result": item_results[item_id],
            }
            for item_id in item_ids
        ],
        "quality_hash": digest(quality),
        "scope": "only_listed_soft_items_not_professional_calibration",
    }
    payload["waiver_hash"] = digest(payload)
    return payload


def revoke_delivery_waiver(waiver, revocation_event, *, revoked_at=None):
    if not isinstance(waiver, dict) or waiver.get("schema_version") != VERSION:
        raise DomainError("DELIVERY_WAIVER_NOT_FOUND", 404)
    if not isinstance(revocation_event, dict) or not revocation_event.get("event_id"):
        raise DomainError("DELIVERY_WAIVER_CONFIRMATION_REQUIRED", 422)
    revoked = deepcopy(waiver)
    revoked.update(
        status="revoked",
        revoked_at=revoked_at or datetime.now(timezone.utc).isoformat(),
        revocation_event=deepcopy(revocation_event),
    )
    body = {key: value for key, value in revoked.items() if key != "waiver_hash"}
    revoked["waiver_hash"] = digest(body)
    return revoked


def waiver_state(waiver, plan, quality):
    if not isinstance(waiver, dict) or waiver.get("schema_version") != VERSION:
        return "not_present"
    if waiver.get("status") == "revoked":
        return "revoked"
    if waiver.get("status") != "active":
        return "stale"
    body = {key: value for key, value in waiver.items() if key != "waiver_hash"}
    if waiver.get("waiver_hash") != digest(body):
        return "stale"
    if waiver.get("check_plan_hash") != plan.get("check_plan_hash"):
        return "stale"
    if waiver.get("criterion_contract_version") != plan.get("assessment_contract"):
        return "stale"
    if waiver.get("quality_hash") != digest(quality):
        return "stale"
    hashes = _criterion_hashes(plan)
    results = {
        row.get("item_id"): row.get("result")
        for row in quality.get("checks", [])
        if row.get("item_id") in SOFT_ITEM_IDS
    }
    for item in waiver.get("soft_items", []):
        item_id = item.get("item_id")
        if item_id not in SOFT_ITEM_IDS:
            return "stale"
        if item.get("criterion_hash") != hashes.get(item_id):
            return "stale"
        if item.get("actual_result") != results.get(item_id):
            return "stale"
    return "active"

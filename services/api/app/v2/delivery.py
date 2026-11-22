"""Fail-closed unified final-delivery decision without mutating quality facts."""
from __future__ import annotations

from copy import deepcopy

from .calibration_certificate import certificate_covers, certificate_state
from .contracts import digest
from .delivery_waiver import SOFT_ITEM_IDS, waiver_state
from .quality_policy import task_domains
from .resolver import canonical_scene

VERSION = "delivery-decision.v1"


def delivery_scope(context, units):
    roles = set()
    for unit in units:
        if not isinstance(unit, dict):
            continue
        if unit.get("artifact_role"):
            roles.add(unit["artifact_role"])
        for role in unit.get("artifact_roles", []) if isinstance(unit.get("artifact_roles"), list) else []:
            if isinstance(role, str) and role:
                roles.add(role)
    return {
        "domains": sorted(task_domains(context, units)),
        "artifact_roles": sorted(roles),
        "scenes": sorted({
            canonical_scene(str(binding["scene"]))
            for binding in (context or {}).get("spec_bindings", [])
            if isinstance(binding, dict) and binding.get("scene")
        }),
    }


def decision_from_state(state, quality, units, *, profile_binding_current):
    from .. import model_settings
    policy = model_settings.delivery_policy()
    return delivery_decision(
        quality, state.get("check_plan", {}),
        profile_binding_current=profile_binding_current,
        certificate=state.get("calibration_certificate"),
        certificate_current_bindings=state.get("calibration_current_bindings"),
        certificate_current_anchor=state.get("calibration_current_anchor"),
        waiver=state.get("delivery_waiver"),
        requested_scope=delivery_scope(state.get("context", {}), units),
        auto_waiver_enabled=policy["auto_waiver_enabled"],
    )


def _claim_class(item):
    if item.get("decision_type") == "deterministic":
        return "computable"
    if item.get("item_id") in SOFT_ITEM_IDS:
        return "professional"
    return "anchored"


def _scope_for_items(plan, quality, requested_scope, waived_items):
    claims = set((requested_scope or {}).get("claim_classes") or [])
    if not claims:
        claims = {
            _claim_class(item)
            for item in plan.get("check_items", [])
            if item.get("required") and item.get("item_id") not in waived_items
        }
    return {
        "domains": deepcopy((requested_scope or {}).get("domains") or []),
        "claim_classes": sorted(claims),
        "artifact_roles": deepcopy((requested_scope or {}).get("artifact_roles") or []),
        "scenes": deepcopy((requested_scope or {}).get("scenes") or []),
    }


def delivery_decision(
    quality,
    plan,
    *,
    profile_binding_current,
    certificate=None,
    certificate_current_bindings=None,
    certificate_current_anchor=None,
    waiver=None,
    requested_scope=None,
    auto_waiver_enabled=True,
):
    """Return final eligibility, hard blockers, soft blockers and waiver requirement."""
    checks = {
        row.get("item_id"): row
        for row in quality.get("checks", [])
        if isinstance(row, dict) and row.get("item_id")
    }
    findings = {
        row.get("finding_id"): row
        for row in quality.get("findings", [])
        if isinstance(row, dict) and row.get("finding_id")
    }
    hard = []
    soft = []
    seen = set()
    if not profile_binding_current:
        hard.append({"code": "PROFILE_BINDING_STALE"})
    if quality.get("quality_status") in {"failed", "not_evaluated"}:
        hard.append({
            "code": "QUALITY_NOT_DELIVERABLE",
            "quality_status": quality.get("quality_status"),
        })
    for reason in quality.get("blocking_reasons") or []:
        item_id = reason.get("item_id")
        finding_id = reason.get("finding_id")
        if not item_id and finding_id:
            finding = findings.get(finding_id)
            if finding is None or not finding.get("item_id"):
                hard.append({"code": "BLOCKING_REASON_BINDING_AMBIGUOUS",
                             "finding_id": finding_id})
                continue
            item_id = finding["item_id"]
        if not item_id:
            hard.append({"code": "BLOCKING_REASON_UNBOUND",
                         "reason": deepcopy(reason)})
            continue
        key = (item_id, reason.get("result"), finding_id)
        if key in seen:
            continue
        seen.add(key)
        actual = checks.get(item_id, {}).get("result", "not_run")
        if actual in {"satisfied", "not_applicable"}:
            hard.append({
                "code": "BLOCKING_REASON_RESULT_CONFLICT",
                "item_id": item_id,
                "actual_result": actual,
                "reason": deepcopy(reason),
            })
        elif item_id in SOFT_ITEM_IDS:
            soft.append({
                "item_id": item_id,
                "result": actual,
                "criterion_hash": (checks.get(item_id, {}).get("criterion_hash")
                                   or (next((item for item in plan.get("check_items", [])
                                             if item.get("item_id") == item_id), {})
                                       .get("criterion_contract") or {}).get("criterion_hash")),
            })
        else:
            hard.append({
                "code": "HARD_CHECK_NOT_SATISFIED",
                "item_id": item_id,
                "result": actual,
            })
    for item in plan.get("check_items", []):
        if not item.get("required"):
            continue
        item_id = item.get("item_id")
        actual = checks.get(item_id, {}).get("result", "not_run")
        if actual in {"satisfied", "not_applicable"}:
            continue
        entry = {
            "item_id": item_id,
            "result": actual,
            "criterion_hash": (item.get("criterion_contract") or {}).get("criterion_hash"),
        }
        target = soft if item_id in SOFT_ITEM_IDS else hard
        if not any(row.get("item_id") == item_id for row in target):
            target.append(entry)

    soft_ids = {row["item_id"] for row in soft}
    waiver_effective = waiver_state(waiver, plan, quality)
    if soft and waiver_effective == "active":
        waived_items = {
            row.get("item_id") for row in waiver.get("soft_items", [])
        }
        if waived_items != soft_ids:
            waiver_effective = "stale"
            waived_items = set()
    else:
        waived_items = set()

    required_scope = _scope_for_items(plan, quality, requested_scope, soft_ids)
    certificate_status = "not_present"
    if certificate is None:
        hard.append({"code": "CALIBRATION_CERTIFICATE_REQUIRED"})
    elif certificate_current_bindings is None or certificate_current_anchor is None:
        certificate_status = certificate_state(certificate)
        hard.append({"code": "CALIBRATION_CURRENT_BINDINGS_UNAVAILABLE"})
    else:
        certificate_status = certificate_state(
            certificate,
            current_bindings=certificate_current_bindings,
            current_anchor=certificate_current_anchor,
        )
        if certificate_status != "calibrated":
            hard.append({
                "code": "CALIBRATION_CERTIFICATE_NOT_CURRENT",
                "certificate_status": certificate_status,
            })
        elif certificate_current_bindings.get("check_plan_hash") != plan.get("check_plan_hash"):
            hard.append({"code": "CALIBRATION_PLAN_BINDING_MISMATCH"})
        elif (certificate_current_bindings.get("criterion_contract_version")
              != plan.get("assessment_contract")):
            hard.append({"code": "CALIBRATION_CRITERION_CONTRACT_MISMATCH"})
        elif not set(
                (item.get("criterion_contract") or {}).get("criterion_hash")
                for item in plan.get("check_items", [])
                if (item.get("criterion_contract") or {}).get("criterion_hash")
        ) <= set(certificate_current_bindings.get("criterion_hashes") or []):
            hard.append({"code": "CALIBRATION_CRITERION_HASH_MISMATCH"})
        elif not certificate_covers(
                certificate, required_scope,
                current_bindings=certificate_current_bindings,
                current_anchor=certificate_current_anchor):
            hard.append({
                "code": "CALIBRATION_SCOPE_NOT_COVERED",
                "required_scope": required_scope,
            })

    if hard:
        state = "blocked"
    elif soft and waiver_effective != "active":
        state = "waiver_required"
    else:
        state = "eligible"
    decision = {
        "schema_version": VERSION,
        "state": state,
        "final_allowed": state == "eligible",
        "hard_blockers": hard,
        "soft_blockers": soft,
        "soft_item_ids": sorted(soft_ids),
        "waiver_auto_enabled": bool(auto_waiver_enabled),
        "waiver_status": waiver_effective,
        "certificate_status": certificate_status,
        "delivery_calibration_status": (
            "waiver_authorized" if state == "eligible" and soft
            else "calibrated" if state == "eligible"
            else "not_calibrated"
        ),
        "required_certificate_scope": required_scope,
        "quality_unchanged": True,
    }
    decision["decision_hash"] = digest(decision)
    return decision

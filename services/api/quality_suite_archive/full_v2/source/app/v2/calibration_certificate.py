"""Fail-closed calibration certificate v1 and deterministic stale detection."""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone

from ..learning.disciplines import canonical_domain_key
from .benchmark import wilson
from .contracts import DomainError, digest

VERSION = "calibration-certificate.v1"
CLAIM_CLASSES = frozenset({"computable", "anchored", "professional"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _normalized_scope(scope):
    value = deepcopy(scope) if isinstance(scope, dict) else {}
    domains = sorted({
        canonical_domain_key(str(domain))
        for domain in value.get("domains") or []
        if isinstance(domain, str) and domain.strip()
    })
    claims = sorted({
        str(claim) for claim in value.get("claim_classes") or []
        if isinstance(claim, str) and claim.strip()
    })
    roles = sorted({
        str(role) for role in value.get("artifact_roles") or []
        if isinstance(role, str) and role.strip()
    })
    scenes = sorted({
        str(scene) for scene in value.get("scenes") or []
        if isinstance(scene, str) and scene.strip()
    })
    return {
        "domains": domains,
        "claim_classes": claims,
        "artifact_roles": roles,
        "scenes": scenes,
    }


def _normalized_bindings(bindings):
    value = deepcopy(bindings) if isinstance(bindings, dict) else {}
    implementation = value.get("implementation_files")
    files = []
    if isinstance(implementation, list):
        for row in implementation:
            if isinstance(row, dict):
                files.append({
                    "path": row.get("path"),
                    "sha256": row.get("sha256"),
                })
    return {
        **value,
        "implementation_files": sorted(
            files, key=lambda row: str(row.get("path"))
        ),
        "criterion_hashes": sorted({
            str(item) for item in value.get("criterion_hashes") or []
            if isinstance(item, str) and item
        }),
        "prompt_hashes": sorted({
            str(item) for item in value.get("prompt_hashes") or []
            if isinstance(item, str) and item
        }),
        "model_families": sorted({
            str(item) for item in value.get("model_families") or []
            if isinstance(item, str) and item
        }),
        "tool_versions": sorted({
            str(item) for item in value.get("tool_versions") or []
            if isinstance(item, str) and item
        }),
    }


def _normalized_anchor(anchor):
    value = deepcopy(anchor) if isinstance(anchor, dict) else {}
    return {key: value.get(key) for key in (
        "commit",
        "archive_sha256",
        "protocol_hash",
        "license",
        "storage_identity",
    )}


def _binding_errors(bindings):
    errors = []
    files = bindings.get("implementation_files")
    if not files:
        errors.append("implementation_files_required")
    elif any(
        not isinstance(row.get("path"), str) or not row["path"].strip()
        or not isinstance(row.get("sha256"), str)
        or not _SHA256.fullmatch(row["sha256"])
        for row in files
    ):
        errors.append("implementation_file_hash_invalid")
    for key in ("review_protocol_hash", "checker_protocol_hash", "check_plan_hash"):
        if not isinstance(bindings.get(key), str) or not _SHA256.fullmatch(bindings[key]):
            errors.append(key + "_invalid")
    if not isinstance(bindings.get("criterion_contract_version"), str) or not bindings.get("criterion_contract_version"):
        errors.append("criterion_contract_version_missing")
    if not bindings.get("criterion_hashes") or not all(
            _SHA256.fullmatch(item) for item in bindings["criterion_hashes"]):
        errors.append("criterion_hashes_invalid")
    if not bindings.get("prompt_hashes") or not all(
            _SHA256.fullmatch(item) for item in bindings["prompt_hashes"]):
        errors.append("prompt_hashes_invalid")
    if not bindings.get("model_families"):
        errors.append("model_families_missing")
    if not bindings.get("tool_versions"):
        errors.append("tool_versions_missing")
    return errors


def _anchor_errors(anchor):
    errors = []
    for key in ("commit", "archive_sha256", "protocol_hash", "license", "storage_identity"):
        if not isinstance(anchor.get(key), str) or not anchor[key].strip():
            errors.append(key + "_missing")
    if isinstance(anchor.get("archive_sha256"), str) and not _SHA256.fullmatch(anchor["archive_sha256"]):
        errors.append("archive_sha256_invalid")
    if isinstance(anchor.get("protocol_hash"), str) and not _SHA256.fullmatch(anchor["protocol_hash"]):
        errors.append("protocol_hash_invalid")
    return errors


def _sample_errors(summary):
    errors = []
    detail = summary.get("metric_denominators_detail") or {}
    records = summary.get("records") or []
    expected = summary.get("expected")
    clusters = summary.get("clusters") or 0
    positives = sum(row.get("expected") == "satisfied" for row in records)
    negatives = sum(row.get("expected") == "violated" for row in records)
    case_hashes = [row.get("case_hash") for row in records if isinstance(row, dict)]
    source_groups = [
        row.get("source_group") for row in records if isinstance(row, dict)
    ]
    if clusters < 16:
        errors.append("clusters_below_16")
    if positives < 35:
        errors.append("expert_positives_below_35")
    if negatives < 9:
        errors.append("expert_negatives_below_9")
    if len(case_hashes) != len(set(case_hashes)):
        errors.append("duplicate_case")
    if any(not isinstance(group, str) or not group.strip() for group in source_groups):
        errors.append("source_group_missing")
    if len(records) != expected or detail.get("unrecorded_cases") != 0:
        errors.append("unrecorded_cases")
    if summary.get("status") != "completed" or detail.get("invalid_observations") != 0:
        errors.append("invalid_observations")
    if summary.get("decision_coverage") is None or summary["decision_coverage"] < 0.80:
        errors.append("decision_coverage_below_0_80")
    fp = detail.get("false_positive_cases")
    decided_positives = detail.get("positive_cases_decided")
    if type(fp) is not int or type(decided_positives) is not int or decided_positives <= 0:
        errors.append("positive_denominator_unexplained")
    elif wilson(fp, decided_positives)[1] > 0.10:
        errors.append("case_false_positive_wilson_upper_above_0_10")
    return errors


def _threshold_errors(summary):
    errors = []
    agreement = summary.get("agreement_decided_ci95_clustered")
    recall = summary.get("error_recall_ci95_clustered")
    false_positive = summary.get("false_positive_ci95_clustered")
    if not isinstance(agreement, list) or len(agreement) != 2 or agreement[0] is None or agreement[0] < 0.80:
        errors.append("clustered_agreement_lower_below_0_80")
    if not isinstance(recall, list) or len(recall) != 2 or recall[0] is None or recall[0] < 0.70:
        errors.append("clustered_error_recall_lower_below_0_70")
    if not isinstance(false_positive, list) or len(false_positive) != 2 or false_positive[1] is None or false_positive[1] > 0.10:
        errors.append("clustered_false_positive_upper_above_0_10")
    return errors


def issue_certificate(scope, summary, bindings, anchor, *, issued_at=None):
    """Return calibrated only when every scope, sample and metric precondition passes."""
    normalized_scope = _normalized_scope(scope)
    normalized_bindings = _normalized_bindings(bindings)
    normalized_anchor = _normalized_anchor(anchor)
    errors = []
    if not normalized_scope["domains"]:
        errors.append("scope_domains_missing")
    if not normalized_scope["claim_classes"] or not set(normalized_scope["claim_classes"]) <= CLAIM_CLASSES:
        errors.append("scope_claim_classes_invalid")
    if not normalized_scope["artifact_roles"]:
        errors.append("scope_artifact_roles_missing")
    if not normalized_scope["scenes"]:
        errors.append("scope_scenes_missing")
    errors.extend(_binding_errors(normalized_bindings))
    errors.extend(_anchor_errors(normalized_anchor))
    errors.extend(_sample_errors(summary))
    errors.extend(_threshold_errors(summary))
    timestamp = issued_at or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema_version": VERSION,
        "issued_at": timestamp,
        "scope": normalized_scope,
        "eligibility": {
            "eligible": not errors,
            "errors": sorted(set(errors)),
            "bootstrap_seed": summary.get("bootstrap_seed"),
            "bootstrap_iterations": summary.get("bootstrap_iterations"),
            "summary_hash": digest(summary),
        },
        "bindings": normalized_bindings,
        "anchor": normalized_anchor,
    }
    if errors:
        return {**payload, "status": "not_calibrated"}
    calibrated = {**payload, "status": "calibrated"}
    calibrated["certificate_hash"] = digest(calibrated)
    return calibrated


def certificate_state(certificate, current_bindings=None, current_anchor=None):
    if not isinstance(certificate, dict) or certificate.get("schema_version") != VERSION:
        return "not_calibrated"
    if certificate.get("status") != "calibrated":
        return "not_calibrated"
    body = {key: value for key, value in certificate.items() if key != "certificate_hash"}
    if certificate.get("certificate_hash") != digest(body):
        return "not_calibrated"
    if (current_bindings is not None
            and _normalized_bindings(current_bindings) != certificate.get("bindings")):
        return "stale"
    if (current_anchor is not None
            and _normalized_anchor(current_anchor) != certificate.get("anchor")):
        return "stale"
    return "calibrated"


def certificate_covers(certificate, requested_scope, *, current_bindings=None, current_anchor=None):
    if certificate_state(certificate, current_bindings, current_anchor) != "calibrated":
        return False
    requested = _normalized_scope(requested_scope)
    certified = certificate.get("scope") or {}
    for key in ("domains", "claim_classes", "artifact_roles", "scenes"):
        if not requested[key] or not set(requested[key]) <= set(certified.get(key) or []):
            return False
    return True

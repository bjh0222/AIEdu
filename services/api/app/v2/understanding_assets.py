"""Versioned cross-subject understanding and tutorial-planning guidance.

The content seed is a product-authored runtime asset. It is not a model card,
professional calibration, or permission to infer empirical learning outcomes.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from ..learning.disciplines import SUBJECTS, canonical_domain_key
from .contracts import DomainError, digest

SEED_PATH = Path(__file__).with_name("subject_understanding.v1.json")


def _load():
    try:
        value = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainError("SUBJECT_UNDERSTANDING_ASSET_UNAVAILABLE", 500) from exc
    if not isinstance(value, dict):
        raise DomainError("SUBJECT_UNDERSTANDING_ASSET_INVALID", 500)
    return value


def _validate(value):
    required_subjects = {key for key, _, _ in SUBJECTS}
    subjects = value.get("subjects")
    planning = value.get("tutorial_planning")
    if not isinstance(value.get("general"), dict):
        raise DomainError("SUBJECT_UNDERSTANDING_GENERAL_MISSING", 500)
    if not isinstance(subjects, dict) or set(subjects) != required_subjects:
        raise DomainError("SUBJECT_UNDERSTANDING_COVERAGE_INVALID", 500,
                          missing=sorted(required_subjects - set(subjects or {})),
                          extra=sorted(set(subjects or {}) - required_subjects))
    if not isinstance(planning, dict) or set(planning) != required_subjects:
        raise DomainError("TUTORIAL_PLANNING_COVERAGE_INVALID", 500,
                          missing=sorted(required_subjects - set(planning or {})),
                          extra=sorted(set(planning or {}) - required_subjects))
    return value


def seed():
    return _validate(_load())


def _binding(kind, identity, domain_key, content, value):
    content = deepcopy(content)
    difficulty = content.setdefault("difficulty_model", [])
    invariant = "实测难度：not_measured；无真实作答数据不得推断难度、区分度或学习收益。"
    if invariant not in difficulty:
        difficulty.append(invariant)
    body = {
        "kind": kind,
        "id": identity,
        "version": value["version"],
        "domain_key": domain_key,
        "status": "active",
        "calibration_status": "not_calibrated",
        "source_kind": content.get("source_kind", "AI 归纳与工程整理"),
        "source_refs": deepcopy(content.get("source_refs", [])),
        "updated_at": value.get("updated_at"),
        "content": content,
    }
    return {**body, "hash": digest(body)}


def catalog():
    value = seed()
    general = _binding(
        "general_understanding",
        value.get("general_id", "subject-understanding:general"),
        "general",
        value["general"],
        value,
    )
    subjects = []
    for key, display_name, _ in SUBJECTS:
        content = value["subjects"][key]
        subjects.append({
            "display_name": display_name,
            **_binding(
                "discipline_understanding",
                f"subject-understanding:{key}",
                key,
                content,
                value,
            ),
        })
    normalized_planning = {
        item["domain_key"]: {k: v for k, v in tutorial_planning(item["domain_key"]).items() if k != "hash"}
        for item in subjects
    }
    return {
        "schema_version": value.get("schema_version", "aiedu.subject-understanding.v1"),
        "version": value["version"],
        "updated_at": value.get("updated_at"),
        "calibration_status": "not_calibrated",
        "professional_certification": False,
        "general": general,
        "subjects": subjects,
        "tutorial_planning_version": value.get("tutorial_planning_version", value["version"]),
        "tutorial_planning_hash": digest(normalized_planning),
    }


def tutorial_planning(domain_key):
    value = seed()
    key = canonical_domain_key(domain_key)
    if key not in value["tutorial_planning"]:
        return None
    content = deepcopy(value["tutorial_planning"][key])
    policy = content.setdefault("difficulty_policy", [])
    invariant = "实测难度与区分度固定为 not_measured，除非有真实作答数据和适用校准证据。"
    if invariant not in policy:
        policy.append(invariant)
    content.update(
        rubric_id=f"tutorial-planning:{key}",
        version=value.get("tutorial_planning_version", value["version"]),
        status="authored_uncalibrated",
        calibration_status="not_calibrated",
        source_refs=deepcopy(content.get("source_refs", [])),
    )
    content["hash"] = digest({k: v for k, v in content.items() if k != "hash"})
    return content


def freeze_subject_understanding(context):
    """Freeze the general layer plus every task-bound subject and material overlay."""
    value = catalog()
    domains = {canonical_domain_key(context.get("domain_key"))}
    domains.update(canonical_domain_key(item["domain_key"])
                   for item in context.get("subject_bindings", []))
    by_domain = {item["domain_key"]: item for item in value["subjects"]}
    subjects = [deepcopy(by_domain[key]) for key in sorted(domains) if key in by_domain]
    material_refs = []
    for binding in context.get("import_understanding", []):
        material_refs.append({
            "profile": {k: binding["profile"][k] for k in ("id", "version", "hash")},
            "rubric": {k: binding["rubric"][k] for k in ("id", "version", "hash")},
            "binding_hash": binding.get("binding_hash"),
        })
    body = {
        "method_version": "understanding-composition.v1",
        "asset_version": value["version"],
        "general_ref": {k: value["general"][k] for k in ("id", "version", "hash")},
        "discipline_refs": [{k: item[k] for k in ("id", "version", "hash", "domain_key")}
                            for item in subjects],
        "material_refs": material_refs,
        "selection": {
            "domain_keys": sorted(domains),
            "general": "always",
            "subjects": "task_bound",
            "material_overlays": "only_frozen_import_profiles",
        },
        "calibration_status": "not_calibrated",
    }
    return {
        **context,
        "subject_understanding": {
            **body,
            "composition_hash": digest(body),
            "general": deepcopy(value["general"]),
            "subjects": subjects,
        },
    }


def manifest_entries(context, consumer, domains=None):
    composition = context.get("subject_understanding")
    if not composition:
        return []
    wanted = {canonical_domain_key(value) for value in (domains or [])}
    rows = [composition["general"]]
    rows.extend(item for item in composition.get("subjects", [])
                if not wanted or item["domain_key"] in wanted)
    return [{
        "source_id": item["id"],
        "source_kind": item["kind"],
        "version": item["version"],
        "sha256": item["hash"],
        "selected": True,
        "reason": "task_subject_understanding",
        "consumer": consumer,
        "message_position": "context.subject_understanding",
        "domain_key": item["domain_key"],
    } for item in rows]


def composition_refs(context):
    composition = context.get("subject_understanding")
    if not composition:
        return {"status": "legacy_unbound", "composition_hash": None, "refs": []}
    refs = [composition["general"], *composition.get("subjects", [])]
    return {
        "status": "bound",
        "composition_hash": composition["composition_hash"],
        "refs": [{k: item[k] for k in ("kind", "id", "version", "hash", "domain_key")}
                 for item in refs],
    }

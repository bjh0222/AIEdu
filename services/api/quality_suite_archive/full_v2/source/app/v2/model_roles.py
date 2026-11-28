"""Private, explicit role assignment. A differently named same-family model is not independent."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import settings
from ..model_settings import read_config, profile_settings, runtime_settings
from .contracts import DomainError


WORKFLOW_ROLE_GROUPS = {
    "understanding_planning": ("understanding", "planning"),
    "generation_repair": ("generation", "repair"),
    "quality": ("review", "blind_solver", "secondary_review"),
    "chat": ("chat",),
}
WORKFLOW_ROLE_FOR_MEMBER = {
    member: group
    for group, members in WORKFLOW_ROLE_GROUPS.items()
    for member in members
}


def qualified_family(value):
    """Reject missing/placeholder labels; never infer a family from an alias.

    A usable label is still operator-declared metadata, not proof of upstream
    identity or professional independence. Numeric profile IDs are not labels.
    """
    family = str(value or "").strip().lower()
    return family if family not in {"unknown", "demo", "none", "null", "n/a"} and any(c.isalpha() for c in family) else None


def role_config():
    config = read_config()
    # Tests always use their own MODEL_SETTINGS_FILE; never discover private credentials.
    path = Path(os.getenv("AIEDU_ROLE_SETTINGS_FILE", str(settings.private_root / "model-roles.private.json")))
    test_config = "test_model_settings" in os.getenv("MODEL_SETTINGS_FILE", "")
    extra = json.loads(path.read_text(encoding="utf-8")) if path.is_file() and not test_config else {}
    # Both files are private. Explicit choices saved through the settings UI
    # take precedence over deployment defaults, including duplicate profile
    # IDs. Keep each profile whole so credentials cannot cross endpoints.
    profiles = {p["id"]: p for p in extra.get("profiles", []) + config.get("profiles", [])}
    profiles = {key: value for key, value in profiles.items()
        if not config.get("v2_profile_management", {}).get(key, {}).get("trashed")}
    roles = {**extra.get("roles", {}), **config.get("model_roles", {})}
    # Explicit process-local acceptance/deployment policy. It never changes the
    # saved user file, crosses credentials between profiles or auto-selects an
    # independent model. Expose its identity through public_roles for audit.
    scoped_profile = os.getenv("AIEDU_SCOPED_ROLE_PROFILE")
    if scoped_profile:
        if scoped_profile not in profiles:
            raise DomainError("SCOPED_ROLE_PROFILE_NOT_FOUND", 422)
        roles = {role: scoped_profile for role in ("generation", "understanding", "planning", "repair", "knowledge",
            "review", "blind_solver", "secondary_review", "benchmark_panel", "benchmark_refiner")}
    # Independence is enforced by for_role, never by silently substituting
    # a different model for the user's saved selection.
    return profiles, roles


def workflow_role(role):
    """Return the public four-part workflow group for an internal role."""
    return WORKFLOW_ROLE_FOR_MEMBER.get(role)


def workflow_assignments(config=None):
    """Read explicit workflow choices without mutating the compatibility roles."""
    value = (config if config is not None else read_config()).get("workflow_roles")
    return dict(value) if isinstance(value, dict) else {}


def _resolve_saved_profile(role, profiles, roles):
    """Resolve one internal role while retaining its exact selection source."""
    config = read_config()
    groups = workflow_assignments(config)
    group = workflow_role(role)
    scoped = bool(os.getenv("AIEDU_SCOPED_ROLE_PROFILE"))
    if scoped and roles.get(role) in profiles:
        return roles[role], "scoped", group, profiles
    if group and group in groups:
        profile_id = groups[group]
        if profile_id not in profiles:
            raise DomainError("MODEL_ROLE_UNCONFIGURED", 422, role=role,
                              workflow_role=group, profile_id=profile_id)
        return profile_id, "workflow", group, profiles
    if roles.get(role) in profiles:
        return roles[role], "role", group, profiles
    if role in {"generation", "repair", "understanding", "planning", "knowledge", "chat"} \
            and roles.get("generation") in profiles:
        return roles["generation"], "generation_fallback", group, profiles
    return None, "unconfigured", group, profiles


def for_role(role, independent_of=()):
    profiles, roles = role_config()
    profile_id, source, group, _ = _resolve_saved_profile(role, profiles, roles)
    if profile_id in profiles:
        profile = profile_settings(profiles[profile_id])
    elif source == "generation_fallback" or role in {
            "generation", "repair", "understanding", "planning", "knowledge", "chat"}:
        profile = runtime_settings()
        profile_id = "environment" if profile.llm_configured else None
    else:
        profile = None
    if profile is None or not profile.llm_configured:
        raise DomainError("MODEL_ROLE_UNCONFIGURED", 422, role=role, workflow_role=group)
    family = qualified_family(getattr(profile, "llm_model_family", None))
    excluded = {qualified_family(x) for x in independent_of}
    if independent_of and (family is None or None in excluded or family in excluded):
        raise DomainError("MODEL_FAMILY_NOT_INDEPENDENT", 422, role=role)
    # Snapshot these on the immutable settings object so the request manifest
    # records the route used for that exact call, even if settings change later.
    profile.llm_internal_role = role
    profile.llm_workflow_role = group
    profile.llm_profile_id = profile_id
    profile.llm_role_source = source
    return profile


def public_roles():
    result = {}
    for role in ("generation", "understanding", "planning", "repair", "review", "blind_solver",
                 "secondary_review", "benchmark_panel", "benchmark_refiner", "chat"):
        try:
            profile = for_role(role)
            result[role] = {"configured": True, "model": profile.llm_model,
                            "model_family": getattr(profile, "llm_model_family", "unknown"),
                            "output_limit_tokens": getattr(profile, "llm_output_limit_tokens", None),
                            "thinking": profile.llm_thinking, "reasoning_effort": profile.llm_reasoning_effort,
                            "profile_id": getattr(profile, "llm_profile_id", None),
                            "workflow_role": getattr(profile, "llm_workflow_role", None),
                            "scoped_role_profile": os.getenv("AIEDU_SCOPED_ROLE_PROFILE")}
        except DomainError as exc:
            result[role] = {"configured": False, "reason": exc.code}
    return result


def public_workflow_roles():
    """Four user-facing partitions with their complete effective member audit."""
    profiles, roles = role_config()
    explicit = workflow_assignments()
    result = {}
    for group, members in WORKFLOW_ROLE_GROUPS.items():
        member_rows = {}
        for member in members:
            try:
                profile = for_role(member)
                member_rows[member] = {
                    "configured": True,
                    "profile_id": getattr(profile, "llm_profile_id", None),
                    "model": profile.llm_model,
                    "model_family": getattr(profile, "llm_model_family", "unknown"),
                }
            except DomainError as exc:
                member_rows[member] = {"configured": False, "reason": exc.code}
        configured = [row for row in member_rows.values() if row.get("configured")]
        identities = {(row.get("profile_id"), row.get("model"), row.get("model_family"))
                      for row in configured}
        common = next(iter(identities)) if len(identities) == 1 else (None, None, None)
        result[group] = {
            "configured": len(configured) == len(members),
            "profile_id": common[0],
            "model": common[1],
            "model_family": common[2],
            "mixed": len(identities) > 1,
            "source": "explicit" if group in explicit else ("single_effective" if identities
                                                               and len(identities) == 1 else "mixed_or_missing"),
            "members": member_rows,
        }
    return result

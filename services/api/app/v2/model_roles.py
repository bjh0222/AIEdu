"""Private, explicit role assignment. A differently named same-family model is not independent."""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..config import settings
from ..model_settings import read_config, profile_settings, runtime_settings
from .contracts import DomainError


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


def for_role(role, independent_of=()):
    profiles, roles = role_config()
    if role in {"repair","understanding","planning","knowledge"} and role not in roles and "generation" in roles:
        role = "generation"
    if roles.get(role) in profiles:
        profile = profile_settings(profiles[roles[role]])
    elif role in {"generation", "repair", "understanding", "planning", "knowledge"}:
        profile = runtime_settings()
    else:
        profile = None
    if profile is None or not profile.llm_configured:
        raise DomainError("MODEL_ROLE_UNCONFIGURED", 422, role=role)
    family = qualified_family(getattr(profile, "llm_model_family", None))
    excluded = {qualified_family(x) for x in independent_of}
    if independent_of and (family is None or None in excluded or family in excluded):
        raise DomainError("MODEL_FAMILY_NOT_INDEPENDENT", 422, role=role)
    return profile


def public_roles():
    result = {}
    for role in ("generation", "review", "blind_solver", "secondary_review", "benchmark_panel", "benchmark_refiner"):
        try:
            profile = for_role(role)
            result[role] = {"configured": True, "model": profile.llm_model,
                            "model_family": getattr(profile, "llm_model_family", "unknown"),
                            "thinking": profile.llm_thinking, "reasoning_effort": profile.llm_reasoning_effort,
                            "scoped_role_profile": os.getenv("AIEDU_SCOPED_ROLE_PROFILE")}
        except DomainError as exc:
            result[role] = {"configured": False, "reason": exc.code}
    return result

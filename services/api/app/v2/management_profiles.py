"""Model lifecycle in the existing private file, including safe action receipts."""
from __future__ import annotations

from .. import model_settings as store
from .contracts import DomainError, digest
from .management_lifecycle import ACTIVE
from .storage import CallDispatch


def describe(config, profile):
    overlay = {"revision": 0, "archived": False, "trashed": False,
        **config.get("v2_profile_management", {}).get(profile["id"], {})}
    value = {"kind": "model-profiles", "id": profile["id"], "label": overlay.get("label") or profile["name"],
        "metadata": {k: profile.get(k) for k in ("id", "name", "model")},
        "management": overlay, "source_edit": "private_model_settings", "hard_delete": "explicit_preflight_required"}
    value["metadata"]["has_key"] = bool(profile.get("api_key"))
    # Bind CAS to the complete private profile without disclosing any secret.
    return {**value, "resource_hash": digest([value, profile, config.get("v2_revision", 0)])}


def find(config, profile_id):
    row = next((p for p in config["profiles"] if p["id"] == profile_id), None)
    if row is None:
        raise DomainError("RESOURCE_NOT_FOUND", 404)
    return row


def catalog(search, offset, limit, archived, trashed):
    with store._lock:
        config = store.read_config()
        rows = [describe(config, p) for p in config["profiles"]]
        rows = [r for r in rows if r["management"]["trashed"] == trashed and
            (trashed or r["management"]["archived"] == archived) and
            (not search or search in r["label"] or search in r["metadata"]["model"] or search in r["id"])]
        rows.sort(key=lambda r: r["id"])
        return {"items": rows[offset:offset+limit], "total": len(rows), "offset": offset, "limit": limit,
            "next_offset": offset+limit if offset+limit < len(rows) else None}


def impact(db, profile_id, config=None):
    from .model_roles import role_config
    config = config or store.read_config()
    profile = find(config, profile_id)
    _, assignments = role_config()
    refs = []
    if config.get("active_id") == profile_id:
        refs.append({"kind": "active_profile", "label": "当前使用的模型", "id": profile_id})
    refs.extend({"kind": "model_role", "id": role, "label": "模型角色："+role} for role, identity in sorted(assignments.items()) if identity == profile_id)
    calls = db.query(CallDispatch).filter(CallDispatch.status.in_(ACTIVE), CallDispatch.model == profile.get("model")).order_by(CallDispatch.call_id).all()
    refs.extend({"kind": "provider_call", "id": c.call_id, "run_id": c.run_id, "state": c.status, "label": "模型调用尚未结算或隔离"} for c in calls)
    result = {"resource": describe(config, profile), "references": refs, "active": bool(calls),
        "purge_allowed": not refs, "purge_blockers": [{"code": "MODEL_PROFILE_IN_USE", "reason": "先解除当前模型/角色引用并结清或隔离活动调用。"}] if refs else [],
        "effects": {"trash": "凭据及完整配置仍保留在原私有文件；可恢复", "purge": "从私有文件删除该profile及其凭据；审计仅保留删除身份，不更换任何角色"},
        "policy": "private_file_only; active_and_assigned_profiles_protected"}
    return {**result, "impact_hash": digest(result)}


def change(payload, key):
    from .runtime import write
    body_hash = digest(payload.model_dump())
    # Lock order matches existing model writes. Source, credentials and receipt
    # are changed by one private-file replacement, never copied to the database.
    with store._lock, write() as db:
        config = store.read_config()
        receipts = config.setdefault("v2_management_receipts", {})
        prior = receipts.get(digest(key))
        if prior:
            if prior["request_hash"] != body_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return prior["response"]
        if len({i.id for i in payload.items}) != len(payload.items):
            raise DomainError("RESOURCE_DUPLICATE_TARGET", 422)
        checked = []
        for item in payload.items:
            current = impact(db, item.id, config)
            resource = current["resource"]
            if item.expected_hash != resource["resource_hash"] or item.impact_hash != current["impact_hash"]:
                raise DomainError("RESOURCE_STALE")
            if current["active"] or (payload.action in {"trash", "purge"} and current["references"]):
                raise DomainError("MODEL_PROFILE_IN_USE", references=current["references"])
            if payload.action == "purge":
                if not resource["management"]["trashed"]:
                    raise DomainError("RESOURCE_TRASH_REQUIRED")
                if payload.confirmation_names.get(item.id) != resource["label"]:
                    raise DomainError("RESOURCE_CONFIRMATION_REQUIRED", 422)
            if payload.action == "restore" and resource["management"]["trashed"]:
                raise DomainError("RESOURCE_USE_RESTORE_TRASH", 422)
            if payload.action == "label" and (not payload.label or not payload.label.strip()):
                raise DomainError("RESOURCE_LABEL_REQUIRED", 422)
            checked.append(resource)
        results = []
        for resource in checked:
            identity = resource["id"]
            overlay = dict(resource["management"])
            if payload.action == "purge":
                config["profiles"] = [p for p in config["profiles"] if p["id"] != identity]
                config.setdefault("v2_profile_management", {}).pop(identity, None)
                config.setdefault("v2_profile_deletions", []).append({"id": identity, "resource_hash": resource["resource_hash"], "action_key_hash": digest(key)})
                results.append({"kind": "model-profiles", "id": identity, "purged": True})
                continue
            overlay.setdefault("history", []).append({k: overlay.get(k) for k in ("revision", "archived", "trashed", "label")})
            overlay["revision"] += 1
            if payload.action == "label":
                overlay["label"] = payload.label.strip()
            elif payload.action in {"trash", "restore_trash"}:
                overlay["trashed"] = payload.action == "trash"
            else:
                overlay["archived"] = payload.action == "archive"
            config.setdefault("v2_profile_management", {})[identity] = overlay
            results.append({"kind": "model-profiles", "id": identity, "management": overlay})
        response = {"items": results, "operation": payload.action, "source_bytes_deleted": payload.action == "purge",
            "cleanup_pending": [], "batch_semantics": "atomic_all_or_nothing", "receipt_hash": digest([body_hash, results])}
        config["v2_revision"] = config.get("v2_revision", 0)+1
        receipts[digest(key)] = {"request_hash": body_hash, "response": response}
        store.write_config(config)
        return response

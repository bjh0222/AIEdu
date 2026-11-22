"""Conservative resource references and recoverable, content-addressed file removal.

No cascade: an inbound FK or source binding is a purge blocker. Audit/delta rows
are deliberately retained and are not live bindings. This is not secure erasure.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from sqlalchemy import String, Text, select, func

from ..db import Base
from ..models import PropositionConfig
from .contracts import DomainError, digest
from .storage import BlobStore

ALIASES = {"quality-reports": "versions"}
AUDIT_PREFIXES = ("managed:", "insight-action:", "action:", "knowledge-import:",
                  "principle-action:", "framework-install:", "resource-deletion:")
RUN_ACTIVE = {"queued", "running"}
CALL_ACTIVE = {"sent", "authorized", "unknown"}
ACTIVE = RUN_ACTIVE | CALL_ACTIVE


def canonical_kind(kind):
    return ALIASES.get(kind, kind)


def overlay_ids(kind, flag="trashed"):
    prefix = "managed:" + canonical_kind(kind) + ":"
    return select(func.substr(PropositionConfig.id, len(prefix) + 1)).where(
        PropositionConfig.id.startswith(prefix),
        func.json_extract(PropositionConfig.settings_json, "$." + flag) == 1)


def visible(query, kind, identity):
    return query.filter(identity.notin_(overlay_ids(kind)))


def contains(value, needles):
    if isinstance(value, dict):
        return any(contains(k, needles) or contains(v, needles) for k, v in value.items())
    if isinstance(value, list):
        return any(contains(v, needles) for v in value)
    return isinstance(value, str) and any(n and n in value for n in needles)


def references(db, row, needles):
    """Include non-FK JSON bindings, private import profiles and call manifests.

    References contain identities/navigation only, never source text or credentials.
    Checking source hashes also finds frozen copies no longer carrying source IDs.
    """
    found = []
    for table in sorted(Base.metadata.tables.values(), key=lambda t: t.name):
        if table.name == "migration_delta_log":
            continue
        primary = list(table.primary_key.columns)[0]
        columns = [c for c in table.columns if isinstance(c.type, (String, Text))]
        for record in db.execute(select(table)).mappings():
            identity = str(record[primary.name])
            if table is row.__table__ and identity == str(getattr(row, primary.name)):
                continue
            if table.name == "proposition_config" and identity.startswith(AUDIT_PREFIXES):
                continue
            hits = []
            for column in columns:
                if column.primary_key:
                    continue
                value = record[column.name]
                if contains(value, needles):
                    hits.append(column.name)
            if not hits:
                continue
            found.append({"kind": "run" if table.name == "generation_jobs" else table.name,
                "id": identity, "fields": hits, "state": record.get("status") or record.get("state"),
                "task_id": record.get("goal_id"), "run_id": record.get("run_id"),
                "label": record.get("title") or record.get("name") or record.get("filename") or table.name})
    return found


def blob_references(db, row, hashes):
    return references(db, row, hashes) if hashes else []


def stage_blobs(hashes, key):
    """Same-filesystem rename: rollback can put the bytes back without copying.

    The durable receipt owns the staging names after DB commit. A crash before
    commit leaves source metadata intact and BlobStore.get can recover the file.
    """
    store = BlobStore()
    staged = []
    try:
        for value in sorted(set(hashes)):
            source = store.path(value)
            pending = source.with_name(value + ".purge-" + digest(key))
            if pending.is_symlink():
                raise DomainError("UNSAFE_STORAGE_PATH", 422)
            if source.exists():
                if pending.exists():
                    raise DomainError("RESOURCE_FILE_CLEANUP_PENDING")
                os.replace(source, pending)
                staged.append((source, pending))
            elif pending.is_file():
                staged.append((source, pending))
        return staged
    except BaseException:
        restore_blobs(staged)
        raise


def restore_blobs(staged):
    for source, pending in reversed(staged):
        if pending.exists() and not source.exists():
            os.replace(pending, source)


def finish_blobs(hashes, key):
    store, pending = BlobStore(), []
    for value in hashes:
        path = store.path(value).with_name(value + ".purge-" + digest(key))
        if path.is_symlink():
            pending.append(value)
            continue
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pending.append(value)
    return pending

"""Scratch-only consistent database backup and idempotent v2 delta recovery."""
from __future__ import annotations

import json
import sqlite3
import time
import shutil
from pathlib import Path

from ..config import settings
from .contracts import DomainError, digest, sha
from .storage import backup_database


def validate_scratch(path):
    resolved = Path(path).resolve()
    if (resolved.name == "aiedu.db" or "data" in resolved.parts or resolved == settings.private_root / "aiedu.db"
            or not any(word in str(resolved).lower() for word in ("scratch", "test_", "pytest-", "fixture"))):
        raise DomainError("EXPLICIT_SCRATCH_REQUIRED", 422)
    return resolved


def restore_copy(source, target):
    source, target = validate_scratch(source), validate_scratch(target)
    started = time.monotonic()
    result = backup_database(source, target)
    result.update(elapsed_seconds=time.monotonic()-started, production_restore="not_run")
    return result


def replay_deltas(target, events):
    target = validate_scratch(target)
    if not target.is_file():
        raise DomainError("SCRATCH_DATABASE_MISSING", 422)
    applied, skipped = [], []
    with sqlite3.connect(target) as db:
        db.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        db.execute("CREATE TABLE IF NOT EXISTS aiedu_replay_receipts (event_id TEXT PRIMARY KEY, event_hash TEXT NOT NULL)")
        last_sequence = -1
        for event in events:
            if event.get("schema_version","aiedu.delta.v1") != "aiedu.delta.v1":
                raise DomainError("DELTA_SCHEMA_VERSION_UNSUPPORTED",422)
            sequence = event.get("txn_seq", last_sequence+1)
            if type(sequence) is not int or sequence <= last_sequence:
                raise DomainError("DELTA_ORDER_INVALID", 422)
            last_sequence = sequence
            event_hash = digest(event)
            old = db.execute("SELECT event_hash FROM aiedu_replay_receipts WHERE event_id=?", (event["event_id"],)).fetchone()
            if old:
                if old[0] != event_hash:
                    raise DomainError("DELTA_IDEMPOTENCY_CONFLICT")
                skipped.append(event["event_id"])
                continue
            table = event["entity_type"]
            if table not in tables or table in {"migration_delta_log", "sqlite_sequence", "aiedu_replay_receipts"}:
                raise DomainError("UNMAPPED_DELTA_ENTITY", 422)
            columns = {row[1]: row for row in db.execute(f'PRAGMA table_info("{table}")')}
            keys = [name for name, info in columns.items() if info[5]]
            if len(keys) != 1:
                raise DomainError("DELTA_PRIMARY_KEY_UNSUPPORTED", 422)
            primary = keys[0]
            if event["operation"] == "delete":
                db.execute(f'DELETE FROM "{table}" WHERE "{primary}"=?', (event["entity_id"],))
            elif event["operation"] in {"insert", "update"}:
                values = json.loads(event["after_json"])
                if not isinstance(values,dict) or not values.keys() <= columns.keys():
                    raise DomainError("DELTA_SCHEMA_MISMATCH", 422)
                if primary not in values or str(values[primary]) != str(event["entity_id"]):
                    raise DomainError("DELTA_ENTITY_MISMATCH",422)
                names = list(values)
                quoted = ",".join(f'"{name}"' for name in names)
                marks = ",".join("?" for _ in names)
                updates = ",".join(f'"{name}"=excluded."{name}"' for name in names if name != primary)
                conflict = "DO UPDATE SET "+updates if updates else "DO NOTHING"
                sql = f'INSERT INTO "{table}" ({quoted}) VALUES ({marks}) ON CONFLICT("{primary}") {conflict}'
                db.execute(sql, [values[name] for name in names])
            else:
                raise DomainError("DELTA_OPERATION_INVALID", 422)
            db.execute("INSERT INTO aiedu_replay_receipts VALUES (?,?)", (event["event_id"], event_hash))
            applied.append(event["event_id"])
    return {"applied": applied, "skipped": skipped, "production_restore": "not_run"}


def _safe_tree(root):
    root = validate_scratch(root)
    for path in [root, *root.rglob("*")]:
        if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
            raise DomainError("UNSAFE_RECOVERY_ASSET", 422)
    return root


def backup_asset_set(database, asset_roots, destination, *, quiescent=False):
    """Scratch rehearsal only. Caller must stop all fixture writers first.

    Files/config stay in the private package; public result exposes hashes/counts only.
    This is deliberately NOT a production/cutover endpoint.
    """
    if not quiescent:
        raise DomainError("QUIESCENT_SNAPSHOT_REQUIRED", 422)
    source, destination = validate_scratch(database), validate_scratch(destination)
    if destination.exists():
        raise DomainError("RESTORE_TARGET_EXISTS", 422)
    roots = {}
    import re
    for label, root in asset_roots.items():
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,40}", label):
            raise DomainError("ASSET_LABEL_INVALID", 422)
        roots[label] = _safe_tree(root)
        if destination.is_relative_to(roots[label]) or roots[label].is_relative_to(destination):
            raise DomainError("RECOVERY_PATH_OVERLAP", 422)
    started = time.monotonic()
    destination.mkdir(parents=True)
    database_result = backup_database(source, destination/"test_snapshot.db")
    files = []
    for label, root in roots.items():
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            data = path.read_bytes()
            relative = Path(label)/path.relative_to(root)
            output = destination/"assets"/relative
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            if sha(path.read_bytes()) != sha(data):
                raise DomainError("SOURCE_CHANGED_DURING_BACKUP", 422)
            files.append({"relative_path":relative.as_posix(), "sha256":sha(data), "size":len(data)})
    manifest = {"schema_version":"aiedu.full-asset-recovery.v1", "database":database_result,
                "files":files, "scope":"quiescent_scratch_fixture", "production_restore":"not_run"}
    manifest["manifest_hash"] = digest(manifest)
    (destination/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    return {"manifest_hash":manifest["manifest_hash"], "files":len(files), "elapsed_seconds":time.monotonic()-started,
            "production_restore":"not_run"}


def restore_asset_set(package, destination):
    package, destination = _safe_tree(package), validate_scratch(destination)
    if destination.exists() or destination.is_relative_to(package) or package.is_relative_to(destination):
        raise DomainError("RESTORE_TARGET_EXISTS_OR_OVERLAP", 422)
    manifest = json.loads((package/"manifest.json").read_text(encoding="utf-8"))
    claimed = manifest.pop("manifest_hash")
    if digest(manifest) != claimed:
        raise DomainError("RECOVERY_MANIFEST_CORRUPTED", 422)
    validated = []
    for file in manifest["files"]:
        relative = Path(file["relative_path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in str(relative) or "\\" in file["relative_path"]:
            raise DomainError("UNSAFE_RECOVERY_ASSET", 422)
        source = package/"assets"/relative
        if not source.is_file() or sha(source.read_bytes()) != file["sha256"] or source.stat().st_size != file["size"]:
            raise DomainError("RECOVERY_ASSET_CORRUPTED", 422)
        validated.append((source,relative))
    database = package/"test_snapshot.db"
    if sha(database.read_bytes()) != manifest["database"]["database_hash"]:
        raise DomainError("RECOVERY_DATABASE_CORRUPTED", 422)
    started = time.monotonic()
    destination.mkdir(parents=True)
    restored = restore_copy(database, destination/"test_restored.db")
    for source, relative in validated:
        target = destination/"assets"/relative
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    return {"manifest_hash":claimed, "restored_files":len(validated), "integrity_check":restored["integrity_check"],
            "elapsed_seconds":time.monotonic()-started, "production_restore":"not_run"}

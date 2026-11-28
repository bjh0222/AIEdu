"""Thin durable responsibilities; domain identity stays in existing models."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Column, Float, Integer, String, Text, event, inspect
from sqlalchemy.orm import Session

from ..config import settings
from ..db import Base
from .contracts import DomainError, canonical, digest, sha


class CallDispatch(Base):
    __tablename__ = "generation_call_dispatches"
    call_id = Column(String(36), primary_key=True)
    root_job_id = Column(String(64), nullable=False, index=True)
    run_id = Column(String(64), nullable=False, index=True)
    unit_id = Column(String(64), nullable=False)
    epoch = Column(Integer, nullable=False)
    attempt = Column(Integer, nullable=False)
    idempotency_key = Column(String(128), nullable=False, unique=True)
    request_hash = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    manifest_json = Column(Text, default="{}")
    request_json = Column(Text, default="{}")
    provider = Column(String(80), nullable=False)
    model_family = Column(String(80), nullable=False)
    model = Column(String(200), nullable=False)
    status = Column(String(24), default="authorized", index=True)
    outcome = Column(String(24), default="")
    supersedes_call_id = Column(String(36), nullable=True)
    receipt_json = Column(Text, default="{}")
    usage_json = Column(Text, nullable=True)
    price_json = Column(Text, nullable=True)
    provider_request_id = Column(String(200), nullable=True)
    result_blob_hash = Column(String(64), nullable=True)
    started_at = Column(Float, default=0)
    finished_at = Column(Float, default=0)


class ExportSet(Base):
    __tablename__ = "artifact_export_sets"
    bundle_id = Column(String(36), primary_key=True)
    artifact_id = Column(String(36), nullable=False, index=True)
    version_id = Column(String(36), nullable=False, index=True)
    kind = Column(String(24), nullable=False)
    state = Column(String(16), default="building")
    request_hash = Column(String(64), nullable=False)
    action_key = Column(String(128), nullable=False, unique=True)
    renderer_version = Column(String(40), default="markdown.v2")
    options_hash = Column(String(64), nullable=False)
    manifest_json = Column(Text, default="{}")
    manifest_hash = Column(String(64), default="")
    cache_relpath = Column(String(200), default="")


class MigrationDelta(Base):
    __tablename__ = "migration_delta_log"
    txn_seq = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(36), nullable=False, unique=True)
    entity_type = Column(String(100), nullable=False)
    entity_id = Column(String(128), nullable=False, index=True)
    operation = Column(String(16), nullable=False)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    schema_version = Column(String(40), default="aiedu.delta.v1")
    applied_key = Column(String(128), nullable=False, unique=True)


@event.listens_for(Session, "after_flush")
def audit_writes(session, context):
    """Capture all ORM writers in the SAME transaction, including legacy entities.

    SQL/bulk writers require explicit migration coverage; production cutover remains blocked.
    """
    for operation, objects in (("insert", session.new), ("update", session.dirty), ("delete", session.deleted)):
        for obj in list(objects):
            if isinstance(obj, MigrationDelta) or not isinstance(obj, Base):
                continue
            if session.get_bind().dialect.name == "sqlite":
                from sqlalchemy import text
                installed = session.connection().execute(text("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name=:name"),
                    {"name": "aiedu_delta_"+obj.__tablename__+"_insert"}).first()
                if installed:
                    continue  # SQLite triggers cover ORM, Core, bulk and raw SQL atomically.
            if operation == "update" and not session.is_modified(obj, include_collections=False):
                continue
            state = inspect(obj)
            after = {c.key: getattr(obj, c.key) for c in state.mapper.columns}
            before = dict(after)
            for attr in state.attrs:
                if attr.history.deleted:
                    before[attr.key] = attr.history.deleted[0]
            primary = state.mapper.primary_key[0].key
            event_id = str(uuid4())
            session.add(MigrationDelta(event_id=event_id, entity_type=obj.__tablename__,
                        entity_id=str(after[primary]), operation=operation,
                        before_json=None if operation == "insert" else canonical(before).decode(),
                        after_json=None if operation == "delete" else canonical(after).decode(), applied_key=event_id))


def install_delta_triggers(connection):
    """Journal every SQLite writer, not only SQLAlchemy's unit-of-work."""
    if connection.dialect.name != "sqlite":
        return
    quote = lambda value: '"'+value.replace('"', '""')+'"'
    names = {r[0] for r in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")}
    if "migration_delta_log" not in names:
        return
    for table in Base.metadata.sorted_tables:
        if table.name == "migration_delta_log" or table.name not in names:
            continue
        columns = list(table.columns)
        def row_json(prefix):
            groups = []
            for i in range(0,len(columns),40):
                args = ','.join("'"+c.name.replace("'", "''")+"',"+prefix+'.'+quote(c.name) for c in columns[i:i+40])
                groups.append("json_object("+args+")")
            value = groups[0]
            # Merge-patch deletes null-valued keys. Concatenate disjoint object
            # members instead so wide rows retain every column, including NULL.
            for group in groups[1:]:
                value = "json(substr("+value+",1,length("+value+")-1)||','||substr("+group+",2))"
            return value
        primary = list(table.primary_key.columns)[0].name
        for operation in ("insert", "update", "delete"):
            before = "NULL" if operation == "insert" else row_json("OLD")
            after = "NULL" if operation == "delete" else row_json("NEW")
            prefix = "OLD" if operation == "delete" else "NEW"
            trigger = quote("aiedu_delta_"+table.name+"_"+operation)
            entity = table.name.replace("'", "''")
            sql = (f"CREATE TRIGGER IF NOT EXISTS {trigger} AFTER {operation.upper()} ON {quote(table.name)} BEGIN "
                "INSERT INTO migration_delta_log(event_id,entity_type,entity_id,operation,before_json,after_json,schema_version,applied_key) "
                f"VALUES(lower(hex(randomblob(16))),'{entity}',CAST({prefix}.{quote(primary)} AS TEXT),'{operation}',"
                f"{before},{after},'aiedu.delta.v1',lower(hex(randomblob(16)))); END")
            # Replace only our own schema-owned trigger when its definition
            # changes; an existing private database must not keep stale logic.
            existing = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                ("aiedu_delta_"+table.name+"_"+operation,)).scalar()
            if existing and existing.replace("CREATE TRIGGER", "CREATE TRIGGER IF NOT EXISTS", 1) != sql:
                connection.exec_driver_sql("DROP TRIGGER "+trigger)
            connection.exec_driver_sql(sql)


class BlobStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or settings.v2_blob_dir).resolve()

    def path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise DomainError("INVALID_BLOB_HASH", 422)
        path = self.root / key[:2] / key
        # Reject every existing reparse/symlink component, not only the final file.
        for parent in [path, *path.parents]:
            if parent.is_symlink() or (parent.exists() and getattr(parent.lstat(), "st_file_attributes", 0) & 0x400):
                raise DomainError("UNSAFE_STORAGE_PATH", 422)
        return path

    def put(self, data: bytes) -> str:
        key = sha(data)
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if sha(path.read_bytes()) != key:
                raise DomainError("BLOB_CORRUPTED", 500)
            return key
        fd, temp = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)
        return key

    def get(self, key: str) -> bytes:
        try:
            data = self.path(key).read_bytes()
        except FileNotFoundError as exc:
            # An interrupted purge cannot make surviving source rows unreadable.
            # Only the durable, explicit purge receipt may finish file removal.
            candidates = list(self.path(key).parent.glob(key + ".purge-*"))
            candidates = [p for p in candidates if p.is_file() and not p.is_symlink()
                and re.fullmatch(key + r"\.purge-[0-9a-f]{64}", p.name)]
            if not candidates:
                raise DomainError("BLOB_MISSING", 410) from exc
            data = candidates[0].read_bytes()
        if sha(data) != key:
            raise DomainError("BLOB_CORRUPTED", 500)
        return data


def backup_database(source: Path, target: Path) -> dict:
    """Explicit scratch/copy only; online backup includes committed WAL frames."""
    source, target = source.resolve(), target.resolve()
    forbidden = [Path(settings.private_root / "aiedu.db").resolve(),
                 Path("D:/桌面/dsh/ai-edu/services/api/data/aiedu.db").resolve()]
    if source in forbidden or target in forbidden or source == target or target.exists() or not source.is_file():
        raise DomainError("UNSAFE_BACKUP_PATH", 422)
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
        result = dst.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise DomainError("RESTORE_INTEGRITY_FAILED", 500)
    return {"database_hash": sha(target.read_bytes()), "integrity_check": result,
            "external_assets": "not_run", "production_restore": "not_run"}

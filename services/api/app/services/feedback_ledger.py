"""反馈证据台账引擎（任务书 05 切片 A）：只追加事件日志 + 可重建投影。

取代旧「单 JSON 文件 read-modify-replace」形态（原 feedback_evidence.py ~126 行的
无锁写、PID 临时名互撞、~370 行的 2000 条截断）。目录布局
（settings.analysis_dir / feedback_evidence/）：

- ``events-0001.jsonl`` …          只追加事件日志；每行一个 JSON 对象（一条记录）。
                                   活动分片满 ``MAX_ACTIVE_EVENTS`` 条即轮换新分片；
                                   旧分片成为归档，**永不删除**（分片即归档）。
- ``projection.json``              投影缓存（可随时由日志重建；观测用途，读路径不依赖它）。
- ``feedback_evidence.lock``       跨进程互斥锁文件（O_EXCL 独占创建 + stale 打破）。
- ``.legacy-imported.json``        legacy 迁移标记（sha256 + 数量），保证迁移幂等。

记录（行）类型（``kind``）：
- ``feedback`` / ``revision``      一条 FeedbackEvidence 事件（generation.v1 线消息）。
- ``retraction``                   撤回事件（message.extensions.retracts 指向原事件）。
- ``occurrence``                   同指纹重复提交的计数操作（目标事件 +1，原文不重写）。
- ``revaluation`` / ``regression`` 重评请求 / 回归待办（随事件追加，投影折叠还原）。
- ``legacy_import``                旧单文件台账迁移导入的事件（未知字段如实保留）。

并发纪律：所有读写都在 ``ledger_lock()`` 内完成；同进程线程由模块级
``_THREAD_LOCK`` 串行，跨进程由锁文件互斥（持锁超 ``LOCK_STALE_SECONDS`` 视为
持有者已死，允许打破）。锁只覆盖本地文件操作——本模块零模型调用、零网络；
DB 写与文件写的先后由调用方（feedback_evidence._deliver_event）编排。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ..config import settings

logger = logging.getLogger("aiedu")

LEDGER_VERSION = 2
MAX_ACTIVE_EVENTS = 2000
LOCK_STALE_SECONDS = 30.0
LOCK_TIMEOUT_SECONDS = 15.0

_THREAD_LOCK = threading.Lock()

_EVENT_KINDS = ("feedback", "revision", "retraction", "legacy_import")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------


def ledger_dir() -> Path:
    path = Path(settings.analysis_dir) / "feedback_evidence"
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_ledger_path() -> Path:
    """旧单文件台账（v1 形态 analysis_dir/feedback_evidence.json）；迁移后原样保留，不删。"""
    path = Path(settings.analysis_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path / "feedback_evidence.json"


def _shard_path(index: int) -> Path:
    return ledger_dir() / f"events-{index:04d}.jsonl"


def _shard_indices() -> list[int]:
    indices = []
    for p in ledger_dir().glob("events-*.jsonl"):
        try:
            indices.append(int(p.stem.split("-")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(indices)


def migration_marker_path() -> Path:
    return ledger_dir() / ".legacy-imported.json"


# ---------------------------------------------------------------------------
# 跨进程文件锁
# ---------------------------------------------------------------------------


def _lock_is_stale(lock_path: Path) -> bool:
    try:
        age = time.time() - lock_path.stat().st_mtime
    except OSError:
        return True
    return age > LOCK_STALE_SECONDS


@contextlib.contextmanager
def ledger_lock(timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """进程内 + 跨进程互斥；只覆盖本地文件操作，不包裹任何模型/网络调用。

    同线程不可重入：不要在 ``with ledger_lock():`` 内再调用会取锁的公开函数。
    """
    with _THREAD_LOCK:  # 同进程线程串行
        lock_path = ledger_dir() / "feedback_evidence.lock"
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if _lock_is_stale(lock_path):
                    try:
                        os.remove(lock_path)
                    except OSError:
                        pass
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"反馈台账锁等待超时：{lock_path}")
                time.sleep(0.02)
        try:
            os.write(fd, json.dumps({"pid": os.getpid(), "acquired_at": _now()}).encode("utf-8"))
        except OSError:
            pass
        finally:
            os.close(fd)
        try:
            yield
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 追加写（日志 O_APPEND 单行写入；投影用唯一临时文件 + os.replace）
# ---------------------------------------------------------------------------


def _active_shard() -> tuple[int, Path]:
    indices = _shard_indices()
    if not indices:
        return 1, _shard_path(1)
    index = indices[-1]
    return index, _shard_path(index)


def _shard_event_count(path: Path) -> int:
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _append_lines_locked(lines: list[dict]) -> None:
    if not lines:
        return
    index, path = _active_shard()
    if path.exists() and _shard_event_count(path) >= MAX_ACTIVE_EVENTS:
        index += 1
        path = _shard_path(index)
    payload = "".join(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n"
                      for line in lines)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())


def _iter_event_lines_locked() -> Iterator[dict]:
    for index in _shard_indices():
        path = _shard_path(index)
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except ValueError:
                        logger.warning("反馈台账分片 %s 存在损坏行，跳过（原文件保留）", path.name)
        except OSError:
            continue


def iter_all_events() -> list[dict]:
    """全量事件条目（含归档分片；跳过 occurrence/revaluation/regression 操作行）。"""
    with ledger_lock():
        _ensure_migrated_locked()
        return [line for line in _iter_event_lines_locked() if line.get("kind") in _EVENT_KINDS]


# ---------------------------------------------------------------------------
# 投影（可重建；retraction/occurrence 折叠为最终状态）
# ---------------------------------------------------------------------------


def build_projection() -> dict:
    """从日志重建投影：events（折叠状态/计数）、revaluations、regressions、分片索引。"""
    with ledger_lock():
        _ensure_migrated_locked()
        return _build_projection_locked()


def _build_projection_locked() -> dict:
    events: dict[str, dict] = {}
    order: list[str] = []
    revaluations: list[dict] = []
    regressions: list[dict] = []
    for line in _iter_event_lines_locked():
        kind = line.get("kind")
        if kind in ("feedback", "revision", "legacy_import"):
            events[line["event_id"]] = dict(line)
            order.append(line["event_id"])
        elif kind == "retraction":
            events[line["event_id"]] = dict(line)
            order.append(line["event_id"])
            target_id = ((line.get("message") or {}).get("extensions") or {}).get("retracts")
            target = events.get(target_id)
            if target is not None:
                target["status"] = "retracted"
                target["updated_at"] = line.get("created_at") or target.get("updated_at")
        elif kind == "occurrence":
            target = events.get(line.get("target_event_id"))
            if target is not None:
                target["occurrences"] = int(target.get("occurrences", 1)) + 1
                target["updated_at"] = line.get("at") or target.get("updated_at")
        elif kind == "revaluation":
            payload = line.get("payload")
            if isinstance(payload, dict):
                revaluations.append(payload)
        elif kind == "revaluation_update":
            # 任务书 05 切片 C：revaluation 状态机 pending_review→dispatched→reviewed/
            # failed/cancelled 以追加行表达（原行不改写），投影折叠为最新状态。
            target = next((p for p in revaluations
                           if p.get("request_id") == line.get("request_id")), None)
            if target is not None:
                update = line.get("update") or {}
                target.setdefault("history", []).append({"at": line.get("at"), **update})
                target.update({k: v for k, v in update.items() if k not in ("history",)})
        elif kind == "regression":
            payload = line.get("payload")
            if isinstance(payload, dict):
                regressions.append(payload)
        elif kind == "regression_update":
            # 回归待办绑定 fixed_case_id/case_version/report_id 与 resolved 判定，
            # 同样以追加行表达，投影折叠为最新状态。
            target = next((p for p in regressions
                           if p.get("todo_id") == line.get("todo_id")), None)
            if target is not None:
                update = line.get("update") or {}
                target.setdefault("history", []).append({"at": line.get("at"), **update})
                target.update({k: v for k, v in update.items() if k not in ("history",)})
    ordered = [events[eid] for eid in order if eid in events]
    shards = {p.name: _shard_event_count(p) for p in (_shard_path(i) for i in _shard_indices())}
    return {
        "version": LEDGER_VERSION,
        "updated_at": _now(),
        "events": ordered,
        "revaluations": revaluations,
        "regressions": regressions,
        "total_events": len(ordered),
        "shards": shards,
    }


def _write_projection_locked(projection: dict) -> None:
    path = ledger_dir() / "projection.json"
    tmp = path.with_suffix(f".json.{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(projection, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def find_active_by_fingerprint(fingerprint: str) -> dict | None:
    """同指纹且未撤回的既有事件（跨分片折叠后判定）；提交前去重用。"""
    with ledger_lock():
        _ensure_migrated_locked()
        return _find_active_by_fingerprint_locked(fingerprint)


def _find_active_by_fingerprint_locked(fingerprint: str) -> dict | None:
    projection = _build_projection_locked()
    for entry in reversed(projection["events"]):
        if entry.get("fingerprint") == fingerprint and entry.get("status", "active") == "active":
            return entry
    return None


def append_batch_with_dedup(entry: dict, fingerprint: str,
                            extra_lines: list[dict] | None = None) -> tuple[dict | None, int]:
    """原子「去重判定 + 追加」：同指纹活动事件存在则只追加 occurrence 操作行。

    返回 ``(dedup_target, occurrences)``：``dedup_target`` 非 None 表示本次提交与
    该既有事件逐字重复（事件未追加，计数 +1）；否则事件与 extra_lines 同批落盘。
    """
    with ledger_lock():
        _ensure_migrated_locked()
        target = _find_active_by_fingerprint_locked(fingerprint)
        if target is not None:
            _append_lines_locked([{"kind": "occurrence", "target_event_id": target["event_id"],
                                   "fingerprint": fingerprint, "at": _now()}])
            new_count = int(target.get("occurrences", 1)) + 1
            _write_projection_locked(_build_projection_locked())
            return dict(target), new_count
        _append_lines_locked([entry] + list(extra_lines or []))
        _write_projection_locked(_build_projection_locked())
        return None, 1


def append_occurrence(target_event_id: str, fingerprint: str) -> int:
    """投递侧的重复提交计数：目标必须仍是活动事件，返回折叠后的新计数。"""
    with ledger_lock():
        _ensure_migrated_locked()
        projection = _build_projection_locked()
        target = next((e for e in reversed(projection["events"])
                       if e.get("event_id") == target_event_id
                       and e.get("status", "active") == "active"), None)
        if target is None:
            raise KeyError(target_event_id)
        _append_lines_locked([{"kind": "occurrence", "target_event_id": target_event_id,
                               "fingerprint": fingerprint, "at": _now()}])
        new_count = int(target.get("occurrences", 1)) + 1
        _write_projection_locked(_build_projection_locked())
        return new_count


def append_lines(lines: list[dict]) -> None:
    """批量追加（事件 + 重评 + 回归同批落盘，保证一次反馈一个原子单元）。"""
    with ledger_lock():
        _ensure_migrated_locked()
        _append_lines_locked(lines)
        _write_projection_locked(_build_projection_locked())


def event_ids_present() -> set[str]:
    """已落账的事件 id 集合（幂等投递判定用）。"""
    with ledger_lock():
        _ensure_migrated_locked()
        return {line["event_id"] for line in _iter_event_lines_locked()
                if line.get("kind") in _EVENT_KINDS and line.get("event_id")}


# ---------------------------------------------------------------------------
# legacy 迁移：先备份（字节一致 + sha256），数量校验，未知字段保留，不删旧文件
# ---------------------------------------------------------------------------


def migrate_legacy_ledger() -> dict:
    """旧 feedback_evidence.json → 新事件日志。幂等：已迁移且源未变时 imported=0。"""
    with ledger_lock():
        return _migrate_legacy_locked()


def _migrate_legacy_locked() -> dict:
    source = legacy_ledger_path()
    if not source.exists():
        return {"imported": 0, "source_sha256": None, "backup": None}
    raw = source.read_bytes()
    source_hash = hashlib.sha256(raw).hexdigest()
    marker = migration_marker_path()
    if marker.exists():
        try:
            marked = json.loads(marker.read_text(encoding="utf-8"))
            if marked.get("source_sha256") == source_hash:
                return {"imported": 0, "source_sha256": source_hash,
                        "backup": marked.get("backup")}
        except (OSError, ValueError):
            pass
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"旧反馈台账不是合法 JSON，拒绝迁移（原文件未动）：{exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("旧反馈台账格式不符合预期（顶层不是对象），拒绝迁移")
    legacy_events = data.get("events") or []
    if not isinstance(legacy_events, list):
        raise ValueError("旧反馈台账 events 不是列表，拒绝迁移")
    # 先备份：字节一致校验，失败即拒绝迁移
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = source.with_name(
        f"feedback_evidence.legacy-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:6]}.json")
    shutil.copyfile(source, backup)
    if backup.read_bytes() != raw:
        raise OSError("legacy 台账备份字节校验失败，拒绝迁移")
    imported = 0
    lines: list[dict] = []
    now = _now()
    for original in legacy_events:
        if not isinstance(original, dict) or not original.get("event_id"):
            continue
        entry = dict(original)  # 未知字段（custom_note 等）如实保留
        entry.setdefault("kind", "legacy_import")
        entry.setdefault("status", "active")
        entry.setdefault("occurrences", 1)
        entry.setdefault("created_at", now)
        entry.setdefault("updated_at", now)
        entry["legacy_imported_at"] = now
        lines.append(entry)
        imported += 1
    for key in ("revaluations", "regressions"):
        for payload in data.get(key) or []:
            if isinstance(payload, dict):
                lines.append({"kind": key[:-1] if key.endswith("s") else key,
                              "payload": payload, "created_at": now})
    _append_lines_locked(lines)
    marker.write_text(json.dumps({
        "source_sha256": source_hash, "imported": imported, "backup": backup.name,
        "event_count": len(legacy_events), "migrated_at": now,
    }, ensure_ascii=False), encoding="utf-8")
    _write_projection_locked(_build_projection_locked())
    return {"imported": imported, "source_sha256": source_hash, "backup": backup.name,
            "event_count": len(legacy_events)}


def _ensure_migrated_locked() -> None:
    """首次使用新引擎时自动迁移旧台账（幂等，由 marker 保证）。"""
    if legacy_ledger_path().exists() and not migration_marker_path().exists():
        try:
            _migrate_legacy_locked()
        except (ValueError, OSError) as exc:
            logger.warning("旧反馈台账自动迁移失败（保留原文件，按新日志继续）：%s", exc)

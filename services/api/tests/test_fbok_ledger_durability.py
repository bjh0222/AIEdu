# -*- coding: utf-8 -*-
"""C1（05 切片 A / 任务书 05）失败 fixture：反馈台账耐久性（只追加日志 + 投影 + 恢复）。

这些用例在 C1 实现前**预期失败（红）**，实现后转绿；对应任务书 05 验收门槛 2
「线程/两个进程并发写入、重复投递、DB rollback、写文件失败、2001 条事件、撤回与
修订均不丢原文、不留悬空引用；模拟只在 tmp_path/隔离库」：

- 同进程多线程并发 append 不丢事件（tmp 文件名唯一 + 进程内锁）；
- 跨进程互斥：ledger_lock() 独占期间其他进程 append 阻塞等待（文件锁）；
- 超过 2000 条：老事件进归档分片，可全量枚举，绝不删证据（现状直接裁掉）；
- 旧格式台账迁移 legacy_import：先备份（字节一致 + hash 校验）、数量核验、
  未知字段如实保留、不删旧文件；
- DB↔文件同步：完整事件先随 TrainingRecord.context_json 同事务落库，commit 后
  幂等投递；投递失败状态 pending/failed，显式恢复入口零模型补投；
- DB 回滚不产生文件侧悬空事件；
- 撤回先落独立事件记录（DB 可审计），再重建投影，原 message 不动；
- 投影可从事件日志重建（occurrences / 撤回状态一致）。

全部零模型调用。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import TrainingRecord
from app.services import feedback_evidence as fe


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    target = tmp_path / "analysis"
    monkeypatch.setattr(settings, "analysis_dir", str(target))
    return target


def _msg(record_id: str, comment: str, rating: int = 1, event_id: str | None = None) -> dict:
    """轻量事件：rating=1 纯文件路径，无回流/重评，无需 DB。"""
    return fe.build_feedback_evidence(
        record_id=record_id, goal_id="g-led", tutorial_id=None, section_id=None,
        version=None, rating=rating, tags=[], comment=comment, event_id=event_id,
    )


def _all_events() -> list[dict]:
    """实现后经 iter_all_events() 全量枚举（含归档分片）；红态退回当前窗口。"""
    iter_all = getattr(fe, "iter_all_events", None)
    if iter_all is not None:
        return list(iter_all())
    return list(fe.load_ledger()["events"])


# ---------------------------------------------------------------------------
# 并发：同进程线程
# ---------------------------------------------------------------------------


def test_threaded_appends_do_not_lose_events(ledger_dir):
    n = 8
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=10)
            fe._append_event(None, _msg(f"rec-{i}", f"线程 {i} 的反馈原文"))
        except BaseException as exc:  # noqa: BLE001  收集线程内异常
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, f"并发 append 抛异常：{errors[:3]}"
    events = _all_events()
    assert len(events) == n
    assert {e["message"]["payload"]["text"] for e in events} == {
        f"线程 {i} 的反馈原文" for i in range(n)
    }


# ---------------------------------------------------------------------------
# 并发：跨进程文件锁
# ---------------------------------------------------------------------------

_LOCK_CHILD = r'''
import os, sys, time
from pathlib import Path
db_url, analysis_dir, api_root, marker_locked, marker_released = sys.argv[1:6]
os.environ["DATABASE_URL"] = db_url          # 显式 scratch，绝不碰 data/aiedu.db
os.environ["AIEDU_ANALYSIS_DIR"] = analysis_dir
assert "aiedu.db" not in db_url.lower().replace("\\", "/")
sys.path.insert(0, api_root)
from app.services import feedback_evidence as fe
with fe.ledger_lock():
    Path(marker_locked).write_text("locked", encoding="utf-8")
    time.sleep(2.5)
Path(marker_released).write_text("released", encoding="utf-8")
'''


def test_cross_process_append_waits_for_ledger_lock(ledger_dir, tmp_path):
    import app as app_pkg

    api_root = str(Path(app_pkg.__file__).resolve().parents[1])  # services/api
    scratch_db = tmp_path / "scratch-child.db"
    script = tmp_path / "lock_child.py"
    script.write_text(_LOCK_CHILD, encoding="utf-8")
    marker_locked = tmp_path / "locked.marker"
    marker_released = tmp_path / "released.marker"
    proc = subprocess.Popen(
        [sys.executable, str(script),
         f"sqlite:///{scratch_db.as_posix()}", str(ledger_dir), api_root,
         str(marker_locked), str(marker_released)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not marker_locked.exists():
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        assert marker_locked.exists(), "子进程未能持锁（见 stderr）"
        started = time.monotonic()
        fe._append_event(None, _msg("rec-parent", "父进程等待锁期间的事件"))
        blocked = time.monotonic() - started
        assert blocked >= 1.0, "父进程未等待跨进程锁（未互斥）"
    finally:
        proc.wait(timeout=30)
    assert proc.returncode == 0, proc.stderr.read().decode("utf-8", "replace")
    assert marker_released.exists()
    texts = [e["message"]["payload"]["text"] for e in _all_events()]
    assert texts == ["父进程等待锁期间的事件"]          # 事件完好，无损坏


# ---------------------------------------------------------------------------
# 归档：2001 条不删证据
# ---------------------------------------------------------------------------


def test_2001_events_archived_not_truncated(ledger_dir):
    legacy = {"version": 1, "updated_at": fe._now(), "events": [],
              "revaluations": [], "regressions": []}
    for i in range(2000):
        msg = _msg(f"seed-{i}", f"历史事件 {i}")
        legacy["events"].append({
            "event_id": msg["payload"]["event_id"], "created_at": fe._now(),
            "updated_at": fe._now(), "fingerprint": f"fp-{i}",
            "occurrences": 1, "status": "active", "message": msg,
        })
    fe.save_ledger(legacy)
    fe._append_event(None, _msg("rec-new", "新事件触发归档"))
    events = _all_events()
    assert len(events) == 2001                          # 一条不少
    assert next(e for e in events if e["message"]["payload"]["text"] == "历史事件 0")
    assert next(e for e in events if e["message"]["payload"]["text"] == "新事件触发归档")


# ---------------------------------------------------------------------------
# legacy 迁移：备份 + 校验 + 未知字段保留
# ---------------------------------------------------------------------------


def test_legacy_ledger_migrated_with_backup_and_integrity(ledger_dir):
    path = fe.ledger_path()
    legacy_events = []
    for i in range(3):
        msg = _msg(f"old-{i}", f"旧事件 {i}")
        legacy_events.append({
            "event_id": msg["payload"]["event_id"], "created_at": fe._now(),
            "updated_at": fe._now(), "fingerprint": f"old-fp-{i}",
            "occurrences": i + 1, "status": "active", "message": msg,
            "custom_note": f"未知字段 {i}",             # 旧版本未知字段
        })
    legacy = {"version": 1, "updated_at": fe._now(), "events": legacy_events,
              "revaluations": [], "regressions": []}
    original_bytes = json.dumps(legacy, ensure_ascii=False, indent=1).encode("utf-8")
    path.write_bytes(original_bytes)  # 字节写入，避免 Windows 换行翻译破坏 hash 校验
    original_hash = hashlib.sha256(original_bytes).hexdigest()

    out = fe.migrate_legacy_ledger()
    assert out["imported"] == 3
    assert out["source_sha256"] == original_hash        # 备份经 hash 校验
    backups = list(path.parent.glob("feedback_evidence.legacy-*.json"))
    assert backups, "必须先备份旧台账"
    assert backups[0].read_bytes() == original_bytes    # 字节一致，旧文件未删
    events = _all_events()
    assert len(events) == 3
    by_text = {e["message"]["payload"]["text"]: e for e in events}
    assert set(by_text) == {f"旧事件 {i}" for i in range(3)}
    assert by_text["旧事件 2"]["occurrences"] == 3      # 计数如实迁移
    assert by_text["旧事件 1"]["custom_note"] == "未知字段 1"   # 未知字段保留
    # 迁移幂等：再跑一次不重复导入
    again = fe.migrate_legacy_ledger()
    assert again["imported"] == 0
    assert len(_all_events()) == 3


# ---------------------------------------------------------------------------
# DB ↔ 文件同步：先落 DB，commit 后幂等投递，失败可恢复
# ---------------------------------------------------------------------------


def test_failed_delivery_marks_record_and_recovery_is_idempotent(ledger_dir, client, monkeypatch):
    real_deliver = fe._deliver_event
    state = {"fail": True}

    def _flaky(*a, **k):
        if state["fail"]:
            raise OSError("ledger disk full")
        return real_deliver(*a, **k)

    monkeypatch.setattr(fe, "_deliver_event", _flaky)
    resp = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": 1, "comment": "待恢复的原文",
    })
    assert resp.status_code == 200, resp.text           # 投递失败不阻断反馈提交
    evidence = resp.json()["context"]["feedback_evidence"]
    assert evidence["delivery_status"] in ("pending", "failed")
    assert fe.load_ledger()["events"] == []             # 文件侧无悬空事件
    state["fail"] = False                               # 恢复投递能力（保留 analysis_dir）
    out = client.post("/api/training/feedback-evidence/recover")
    assert out.status_code == 200, out.text
    assert out.json()["recovered"] >= 1
    events = fe.load_ledger()["events"]
    assert [e["message"]["payload"]["text"] for e in events] == ["待恢复的原文"]
    again = client.post("/api/training/feedback-evidence/recover")
    assert again.json()["recovered"] == 0               # 幂等：已投递不再重复
    assert len(fe.load_ledger()["events"]) == 1


def test_db_rollback_leaves_no_file_event(ledger_dir, client):
    from app.schemas import TrainingFeedbackIn
    from app.services import training as training_service

    db = SessionLocal()
    try:
        original_commit = db.commit

        def _crashing_commit():
            raise RuntimeError("模拟 commit 前进程崩溃")

        db.commit = _crashing_commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            training_service.submit_feedback(
                db, TrainingFeedbackIn(rating=1, comment="回滚不留痕", target_type="general")
            )
        db.rollback()
        assert fe.load_ledger()["events"] == []          # DB 未提交 → 文件无事件
        assert db.query(TrainingRecord).count() == 0     # 记录也未落库
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 撤回：先独立事件，再重建投影；原文不动
# ---------------------------------------------------------------------------


def test_retraction_journaled_and_original_message_intact(ledger_dir, client):
    resp = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": -1, "comment": "要撤回的原文",
    })
    assert resp.status_code == 200, resp.text
    event_id = resp.json()["context"]["feedback_evidence"]["event_id"]
    out = client.post(f"/api/training/feedback-evidence/{event_id}/retract",
                      json={"reason": "误点"})
    assert out.status_code == 200, out.text
    db = SessionLocal()
    try:
        journals = db.query(TrainingRecord).filter(
            TrainingRecord.feature == "feedback_retraction").all()
        assert journals, "撤回必须先落独立事件记录（DB 可审计）再重建投影"
        assert any(event_id in (j.context_json or "") for j in journals)
    finally:
        db.close()
    ledger = fe.load_ledger()
    original = next(e for e in ledger["events"] if e["event_id"] == event_id)
    assert original["message"]["payload"]["text"] == "要撤回的原文"
    assert original["message"]["payload"]["outcome"] != "retracted"  # 原事件不覆写


def test_projection_rebuild_from_event_log(ledger_dir):
    fe._append_event(None, _msg("r1", "原文甲"))
    fe._append_event(None, _msg("r2", "原文甲"))          # 同版本逐字重复 → occurrences 2
    negative = fe._append_event(None, _msg("r3", "原文乙", rating=-1))
    fe.retract_event(negative["event_id"], reason="撤回")
    rebuilt = fe.rebuild_projection()
    assert rebuilt["total"] == fe.list_evidence(limit=200)["total"]
    by_text = {e["text"]: e for e in rebuilt["events"]}
    assert by_text["原文甲"]["occurrences"] == 2
    assert by_text["原文乙"]["status"] == "retracted"

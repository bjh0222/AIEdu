# -*- coding: utf-8 -*-
"""C2（任务书 04）调用边界记账测试：call_id、真实起止时间、短事务、失败计账。

对应任务书 04「必须通过的测试」：
- 单事务缓冲两次不同延迟调用仍保存不同的真实起止时间；
- 失败/超时/取消计账，失败不生成训练样本；
- 网络替身阻塞时另一个隔离 DB 会话仍可写，证明未持 SQLite 写锁；
- 记账（ModelCallLog/TrainingRecord）用独立短事务：调用一返回即可见，
  不等待调用方 commit；TrainingRecord.context_json 带 call_id/run_id。

全部零真实模型调用（provider 替身）；测试库由 conftest 隔离。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from app.db import SessionLocal
from app.llm.service import AIService, AIServiceError, _now_iso
from app.llm.provider import LLMError
from app.models import LearningGoal, ModelCallLog, TrainingRecord

USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
         "usage_source": "provider"}


class StubProvider:
    """provider 替身：可控行为（延迟 / 阻塞 / 失败），零网络。"""

    model = "stub-model"

    def __init__(self, *, delay_s: float = 0.0, text: str = "stub 输出",
                 fail: bool = False, gate: threading.Event | None = None):
        self.delay_s = delay_s
        self.text = text
        self.fail = fail
        self.gate = gate
        self.calls = 0

    def complete_text(self, system: str, user: str):
        self.calls += 1
        if self.gate is not None:
            assert self.gate.wait(timeout=30), "测试关卡超时"
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail:
            raise LLMError("stub failure")
        started = time.monotonic()
        return self.text, int((time.monotonic() - started) * 1000) + int(self.delay_s * 1000), dict(USAGE)

    def complete_json(self, system: str, user: str):
        self.calls += 1
        if self.gate is not None:
            assert self.gate.wait(timeout=30), "测试关卡超时"
        if self.delay_s:
            time.sleep(self.delay_s)
        if self.fail:
            raise LLMError("stub failure")
        return {"title": "t", "content": "c"}, int(self.delay_s * 1000), dict(USAGE)


@pytest.fixture()
def goal(client):
    db = SessionLocal()
    try:
        row = LearningGoal(id="g-orch-acc", title="记账测试", domain_key="cs_it")
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _service(goal_id: str, provider) -> AIService:
    service = AIService(SessionLocal(), goal_id=goal_id, tutorial_id="t-acc")
    service.provider = provider
    return service


def _close(service):
    if service.db is not None:
        service.db.close()


def test_two_delayed_calls_keep_distinct_real_start_finish(goal):
    service = _service(goal, StubProvider(delay_s=0.12))
    try:
        service._real_text("f1", "sys", "user-1")
        service._real_text("f1", "sys", "user-2")
    finally:
        _close(service)
    db = SessionLocal()
    try:
        rows = (db.query(ModelCallLog)
                .filter(ModelCallLog.goal_id == goal, ModelCallLog.feature == "f1")
                .order_by(ModelCallLog.created_at.asc()).all())
        assert len(rows) == 2
        first, second = rows
        for row in rows:
            assert row.call_id and row.started_at and row.finished_at
            assert row.started_at < row.finished_at
            assert row.duration_ms >= 100
            assert row.outcome == "ok" and row.attempt == 1
        assert first.call_id != second.call_id
        t1 = datetime.fromisoformat(first.finished_at)
        t2 = datetime.fromisoformat(second.finished_at)
        assert t2 > t1                                    # 两次调用的真实起止不同
    finally:
        db.close()


def test_failed_call_logged_without_training_sample(goal):
    service = _service(goal, StubProvider(fail=True))
    try:
        with pytest.raises(AIServiceError):
            service._real_text("f_fail", "sys", "user")
    finally:
        _close(service)
    db = SessionLocal()
    try:
        rows = db.query(ModelCallLog).filter(ModelCallLog.feature == "f_fail").all()
        assert len(rows) == 1
        assert rows[0].status == "error" and rows[0].outcome == "error"
        assert rows[0].call_id and rows[0].finished_at
        samples = db.query(TrainingRecord).filter(TrainingRecord.feature == "f_fail").all()
        assert samples == []                               # 失败不产生训练样本
    finally:
        db.close()


def test_accounting_committed_independently_of_caller_session(goal):
    """记账用独立短事务：调用一返回即从其他会话可见，不等调用方 commit。"""
    service = _service(goal, StubProvider())
    try:
        service._real_text("f_short", "sys", "user")
        # 此处**不** commit service.db
        other = SessionLocal()
        try:
            logs = other.query(ModelCallLog).filter(ModelCallLog.feature == "f_short").all()
            assert len(logs) == 1
            samples = other.query(TrainingRecord).filter(TrainingRecord.feature == "f_short").all()
            assert len(samples) == 1
            assert samples[0].context_json and logs[0].call_id in samples[0].context_json
        finally:
            other.close()
    finally:
        service.db.rollback()                              # 调用方回滚也不影响已落账
        _close(service)


def test_blocked_provider_holds_no_write_lock(goal):
    """网络替身阻塞期间，另一个隔离会话仍可写——调用边界不持 SQLite 写事务。"""
    gate = threading.Event()
    service = _service(goal, StubProvider(gate=gate))
    errors: list[BaseException] = []

    def blocked_call():
        try:
            service._real_text("f_block", "sys", "user")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    worker = threading.Thread(target=blocked_call, daemon=True)
    worker.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and service.provider.calls == 0:
            time.sleep(0.01)
        # 阻塞中：另一会话可以写入并提交（未持写锁）
        other = SessionLocal()
        try:
            other.add(LearningGoal(id="g-orch-while-blocked", title="并发写", domain_key="cs_it"))
            other.commit()
            assert other.get(LearningGoal, "g-orch-while-blocked") is not None
        finally:
            other.close()
    finally:
        gate.set()
        worker.join(timeout=30)
    assert not errors
    assert service.provider.calls == 1
    _close(service)


def test_run_id_and_call_id_flow_into_logs_and_samples(goal):
    service = _service(goal, StubProvider())
    service.run_id = "run-orch-acc"
    try:
        service._real_text("f_run", "sys", "user")
    finally:
        _close(service)
    db = SessionLocal()
    try:
        log = db.query(ModelCallLog).filter(ModelCallLog.feature == "f_run").one()
        assert log.run_id == "run-orch-acc" and log.call_id
        sample = db.query(TrainingRecord).filter(TrainingRecord.feature == "f_run").one()
        context = __import__("json").loads(sample.context_json)
        assert context["call_id"] == log.call_id
        assert context["run_id"] == "run-orch-acc"
    finally:
        db.close()


def test_budget_shared_across_calls_and_exhaustion_raises(goal):
    from app.services.generation_runtime import BudgetController

    service = _service(goal, StubProvider())
    try:
        service.budget = BudgetController(max_calls=2)
        service._real_text("f_b1", "sys", "u1")
        service._real_text("f_b2", "sys", "u2")
        with pytest.raises(AIServiceError, match="预算"):
            service._real_text("f_b3", "sys", "u3")
        snap = service.budget.snapshot()
        assert snap["calls_used"] == 2
        logs = SessionLocal()
        try:
            count = logs.query(ModelCallLog).filter(
                ModelCallLog.feature.in_(["f_b1", "f_b2", "f_b3"])).count()
            assert count == 2                              # 被拒的调用不落账（未发生）
        finally:
            logs.close()
    finally:
        _close(service)

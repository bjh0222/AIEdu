# -*- coding: utf-8 -*-
"""C2（任务书 04）BudgetController 测试：原子预留、对账退还、共享池、取消与软预算。

对应任务书 04「必须通过的测试」：
- Barrier 并发最后一份预算额度只能被一个 worker 预留；
- 退还、超额、估算、provider 无 usage、嵌套重试均覆盖；
- provider 重试也计调用；取消后不派新任务，在途响应安全落账。

纯内存逻辑，零模型调用，零数据库。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from app.services.generation_runtime import (
    BudgetController,
    BudgetExceeded,
    CancelToken,
    RunCancelled,
)


def test_last_call_slot_awarded_to_exactly_one_worker():
    budget = BudgetController(max_calls=1)
    n = 8
    barrier = threading.Barrier(n)
    ok: list[str] = []
    exceeded: list[int] = []
    guard = threading.Lock()

    def worker() -> None:
        try:
            barrier.wait(timeout=10)
            reservation = budget.reserve(calls=1, purpose="attempt")
            with guard:
                ok.append(reservation.purpose)
        except BudgetExceeded:
            with guard:
                exceeded.append(1)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert len(ok) == 1 and len(exceeded) == n - 1
    snap = budget.snapshot()
    assert snap["calls_reserved"] == 1 and snap["calls_used"] == 0


def test_settle_refunds_difference_between_estimate_and_actual():
    budget = BudgetController(max_calls=10, token_allowance=1000)
    reservation = budget.reserve(calls=1, est_input_tokens=400, max_output_tokens=400)
    assert budget.snapshot()["tokens_reserved"] == 800
    budget.settle(reservation, usage={
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
        "usage_source": "provider",
    }, outcome="ok")
    snap = budget.snapshot()
    assert snap["tokens_reserved"] == 0
    assert snap["tokens_used"] == 150
    assert snap["calls_used"] == 1
    assert snap["overrun_tokens"] == 0
    assert reservation.settled and reservation.actual["usage_source"] == "provider"


def test_overcommit_rejected_without_partial_reservation():
    budget = BudgetController(max_calls=2, token_allowance=100)
    budget.reserve(calls=2, est_input_tokens=40, max_output_tokens=40)
    with pytest.raises(BudgetExceeded):
        budget.reserve(calls=1)
    with pytest.raises(BudgetExceeded):
        budget.reserve(calls=1, est_input_tokens=10, max_output_tokens=20)  # token 也超
    snap = budget.snapshot()
    assert snap["calls_reserved"] == 2 and snap["calls_used"] == 0
    assert snap["tokens_reserved"] == 80 and snap["tokens_used"] == 0


def test_provider_without_usage_keeps_estimate_and_marks_estimated():
    budget = BudgetController(max_calls=5, token_allowance=1000)
    reservation = budget.reserve(calls=1, est_input_tokens=300, max_output_tokens=200,
                                 purpose="section")
    budget.settle(reservation, usage=None, outcome="ok")
    assert reservation.actual["usage_source"] == "estimated"
    snap = budget.snapshot()
    assert snap["tokens_used"] == 500 and snap["calls_used"] == 1


def test_provider_retry_counts_as_extra_call_and_failures_are_charged():
    budget = BudgetController(max_calls=2)
    first = budget.reserve(calls=1, purpose="attempt-1")
    budget.settle(first, usage=None, outcome="error")     # 失败也计账
    second = budget.reserve(calls=1, purpose="attempt-2-retry")
    assert second.purpose == "attempt-2-retry"            # 重试允许，但占用额度
    budget.settle(second, usage=None, outcome="ok")
    with pytest.raises(BudgetExceeded):
        budget.reserve(calls=1, purpose="attempt-3")
    snap = budget.snapshot()
    assert snap["calls_used"] == 2


def test_cancelled_run_refuses_new_but_settles_inflight():
    token = CancelToken()
    budget = BudgetController(max_calls=5, cancel_token=token)
    inflight = budget.reserve(calls=1, est_input_tokens=10, max_output_tokens=0)
    inflight.call_ids.append("abc")
    token.cancel("user requested")
    with pytest.raises(RunCancelled):
        budget.reserve(calls=1)
    budget.settle(inflight, usage={"prompt_tokens": 8, "completion_tokens": 2,
                                   "total_tokens": 10, "usage_source": "provider"},
                  outcome="cancelled")
    snap = budget.snapshot()
    assert snap["cancelled"] is True
    assert snap["calls_used"] == 1 and snap["tokens_used"] == 10   # 在途响应安全落账


def test_deadline_expiry_blocks_new_reservations():
    budget = BudgetController(max_calls=5,
                              deadline=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(BudgetExceeded):
        budget.reserve(calls=1)


def test_child_budget_shares_parent_pool():
    parent = BudgetController(max_calls=3, token_allowance=900)
    child = BudgetController(shared=parent, name="knowledge_extract")
    child.reserve(calls=2, est_input_tokens=400, max_output_tokens=100)
    parent_snap = parent.snapshot()
    assert parent_snap["calls_reserved"] == 2
    assert parent_snap["tokens_reserved"] == 500
    with pytest.raises(BudgetExceeded):
        child.reserve(calls=2)                            # 超出父池剩余额度
    tail = child.reserve(calls=1, est_input_tokens=0, max_output_tokens=0)
    child.settle(tail, usage={"prompt_tokens": 0, "completion_tokens": 50,
                              "total_tokens": 50, "usage_source": "provider"}, outcome="ok")
    parent_snap = parent.snapshot()
    assert parent_snap["calls_used"] == 1                 # 父子账本合计准确
    assert parent_snap["tokens_used"] == 50 and parent_snap["tokens_reserved"] == 500


def test_unit_repair_limit_is_per_unit():
    budget = BudgetController(unit_repair_limit=1)
    budget.reserve_unit_repair("u1")
    with pytest.raises(BudgetExceeded):
        budget.reserve_unit_repair("u1")
    budget.reserve_unit_repair("u2")                      # 其他单元不受影响


def test_soft_budget_records_overrun_honestly():
    budget = BudgetController(max_calls=10, token_allowance=100, soft=True)
    reservation = budget.reserve(calls=1, est_input_tokens=50, max_output_tokens=50)
    budget.settle(reservation, usage={"prompt_tokens": 80, "completion_tokens": 100,
                                      "total_tokens": 180, "usage_source": "provider"},
                  outcome="ok")
    snap = budget.snapshot()
    assert snap["soft"] is True
    assert snap["overrun_tokens"] == 80                   # 软预算超额如实记录
    assert snap["tokens_used"] == 180


def test_hard_budget_provider_overrun_is_recorded_not_hidden():
    budget = BudgetController(max_calls=10, token_allowance=1000, soft=False)
    reservation = budget.reserve(calls=1, est_input_tokens=100, max_output_tokens=100)
    budget.settle(reservation, usage={"prompt_tokens": 200, "completion_tokens": 300,
                                      "total_tokens": 500, "usage_source": "provider"},
                  outcome="ok")
    snap = budget.snapshot()
    assert snap["soft"] is False
    assert snap["overrun_tokens"] == 300                  # provider 无视上限时也不掩盖


def test_double_settle_is_idempotent():
    budget = BudgetController(max_calls=5)
    reservation = budget.reserve(calls=1)
    budget.settle(reservation, usage={"total_tokens": 10, "calls": 1}, outcome="ok")
    budget.settle(reservation, usage={"total_tokens": 10, "calls": 1}, outcome="ok")
    snap = budget.snapshot()
    assert snap["calls_used"] == 1 and snap["tokens_used"] == 10

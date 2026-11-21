# -*- coding: utf-8 -*-
"""C2（任务书 04）GenerationRunContext 测试：阶段回调契约、状态/checkpoint、执行策略。

对应任务书 04：
- 给 _apply_exam_quality 与各检查服务注入可选 on_stage callback，由 orchestration
  更新状态；质量模块不直接写 job 状态；callback 可选、异常不穿透；
- 阶段至少覆盖 planning…knowledge_extract；
- checkpoint 记录已完成单元 / 阶段状态 / 内容 hash / 调用账本。

纯内存逻辑，零模型调用，零数据库。
"""
from __future__ import annotations

from app.services.generation_runtime import (
    DEFAULT_EXECUTION_POLICY,
    STAGES,
    CancelToken,
    GenerationRunContext,
    new_call_id,
    new_run_id,
)


def test_stage_ids_cover_task_book_minimum():
    assert set(STAGES) >= {
        "planning", "generating", "companions", "reviewing", "repairing",
        "verifying", "accepting", "persisting", "knowledge_extract",
    }


def test_stage_callback_receives_stage_status_and_meta():
    ctx = GenerationRunContext(goal_id="g1", tutorial_id="t1", version=2)
    seen: list[tuple[str, str, dict]] = []
    ctx.register_stage_callback(lambda stage, status, meta: seen.append((stage, status, meta)))
    ctx.stage("reviewing", "running", detail="命题审查")
    ctx.stage("reviewing", "done")
    assert seen[0][0] == "reviewing" and seen[0][1] == "running"
    assert seen[0][2]["detail"] == "命题审查"
    assert seen[1] == ("reviewing", "done", {"unit_id": None, "completed_units": None,
                                             "total_units": None, "detail": ""})
    snap = ctx.state()
    assert snap["stage_id"] == "reviewing" and snap["stage_status"] == "done"


def test_callback_exception_never_breaks_generation():
    ctx = GenerationRunContext(goal_id="g1")

    def boom(stage, status, meta):  # noqa: ANN001  故意坏回调
        raise RuntimeError("callback bug")

    ctx.register_stage_callback(boom)
    ctx.stage("generating", "running")                    # 不抛
    ctx.stage("generating", "done")
    assert ctx.state()["stage_id"] == "generating"
    assert ctx.state()["stage_status"] == "done"


def test_progress_units_tracked_via_stage_meta():
    ctx = GenerationRunContext(goal_id="g1")
    ctx.stage("generating", "running", completed_units=0, total_units=3)
    ctx.stage("generating", "running", completed_units=1, total_units=3, unit_id="s2")
    snap = ctx.state()
    assert snap["completed_units"] == 1 and snap["total_units"] == 3
    assert snap["unit_id"] == "s2"


def test_checkpoint_holds_units_hash_and_call_ledger():
    ctx = GenerationRunContext(goal_id="g1")
    ctx.stage("generating", "running", completed_units=1, total_units=3)
    ctx.set_checkpoint(completed_unit_ids=["s1"], calls=5, tokens=1234)
    ctx.set_content_hash("sha256:abc")
    snap = ctx.state()
    assert snap["checkpoint"]["completed_unit_ids"] == ["s1"]
    assert snap["checkpoint"]["calls"] == 5
    assert snap["checkpoint"]["tokens"] == 1234
    assert snap["content_hash"] == "sha256:abc"
    assert snap["last_heartbeat"]


def test_execution_policy_defaults_and_override():
    ctx = GenerationRunContext(goal_id="g1")
    assert ctx.execution_policy_value("knowledge_extract_timing") == "outline_and_final"
    assert ctx.execution_policy_value("anything_else") is None
    assert ctx.execution_policy_value("anything_else", "x") == "x"
    custom = GenerationRunContext(goal_id="g1",
                                  execution_policy={"knowledge_extract_timing": "final_only"})
    assert custom.execution_policy_value("knowledge_extract_timing") == "final_only"
    assert custom.state()["execution_policy"]["knowledge_extract_timing"] == "final_only"
    assert DEFAULT_EXECUTION_POLICY["knowledge_extract_timing"] == "outline_and_final"


def test_child_run_links_parent_and_shares_policy():
    main = GenerationRunContext(goal_id="g1", tutorial_id="t1", version=3)
    sub = main.child(purpose="knowledge_extract")
    assert sub.parent_run_id == main.run_id
    assert sub.run_id != main.run_id
    assert sub.goal_id == "g1" and sub.tutorial_id == "t1" and sub.version == 3
    assert sub.execution_policy == main.execution_policy
    assert sub.cancel_token is main.cancel_token           # 取消联动


def test_ids_are_unique_and_well_formed():
    run_a, run_b = new_run_id(), new_run_id()
    assert run_a.startswith("run-") and len(run_a) == 24 and run_a != run_b
    call_a, call_b = new_call_id(), new_call_id()
    assert len(call_a) == 32 and call_a != call_b


def test_state_snapshot_is_json_safe():
    from datetime import datetime, timezone

    ctx = GenerationRunContext(goal_id="g1")
    ctx.set_checkpoint(deadline=datetime.now(timezone.utc), weird=object())
    snap = ctx.state()
    import json

    encoded = json.dumps(snap, ensure_ascii=False)         # 不抛即通过
    assert "deadline" in encoded


def test_cancel_token_check_raises():
    token = CancelToken()
    token.check()                                          # 未取消 → 正常
    token.cancel("测试")
    assert token.cancelled() and token.reason == "测试"
    try:
        token.check()
        raise AssertionError("check() 应抛 RunCancelled")
    except Exception as exc:  # noqa: BLE001
        assert "测试" in str(exc)

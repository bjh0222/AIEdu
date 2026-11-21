# -*- coding: utf-8 -*-
"""C2（任务书 04）编排状态测试：真实阶段、父子任务、执行策略、中断与取消。

对应任务书 04：
- 公开 job API 显示真实阶段与 n/m（demo 全流程跑完的状态与 checkpoint 可查）；
- 主任务 done 时后台子任务可见；后台失败公开可见，已生成稿件仍可读；父子账本合计；
- 知识提取时机 outline_and_final / final_only / disabled（C 只执行 B 的策略）；
- 进程中断遗留 running 标 interrupted/needs_resume，checkpoint 保留，不自动续跑；
- 取消：活动 run 立即生效；非活动 run 只记录取消意图；
- 旧 JobOut 字段不动（可读性）。

全部零真实模型调用（demo 模式）。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

from app.db import SessionLocal
from app.models import GenerationJob, Tutorial
from app.services import tutorials as tutorials_service
from app.services.generation_runtime import (
    BudgetController,
    CancelToken,
    GenerationRunContext,
    register_run,
)


def _create_goal(client, **overrides) -> dict:
    payload = {
        "title": "编排状态测试目标",
        "domain_key": "cs_it",
        "motivation": "验证编排可观测",
    }
    payload.update(overrides)
    resp = client.post("/api/goals", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _quickstart(client, goal_id: str) -> dict:
    resp = client.post(f"/api/goals/{goal_id}/tutorials/quickstart")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _poll_job(client, job_id: str, *, timeout_s: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/generation-jobs/{job_id}").json()
        if body["status"] in ("done", "failed", "interrupted"):
            return body
        time.sleep(0.05)
    raise AssertionError("生成任务未在预期时间内结束")


def _wait_children(client, parent_id: str, *, status: str | None = None,
                   timeout_s: float = 30.0) -> list[dict]:
    """等待主 job 的 knowledge_extract 子 job 出现（可选等终态）；直接查库。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            rows = (db.query(GenerationJob)
                    .filter(GenerationJob.parent_job_id == parent_id,
                            GenerationJob.job_type == "knowledge_extract")
                    .all())
            found = [{"id": r.id, "status": r.status, "error": r.error_message or ""}
                     for r in rows]
        finally:
            db.close()
        if found and (status is None or all(f["status"] == status for f in found)):
            return found
        time.sleep(0.05)
    raise AssertionError("未在预期时间内观察到知识提取子 job")


def _state_of(job_id: str) -> dict:
    db = SessionLocal()
    try:
        row = db.get(GenerationJob, job_id)
        return json.loads(row.state_json or "{}")
    finally:
        db.close()


# ---------------------------------------------------------------- JobOut 兼容


def test_job_api_keeps_old_fields_and_adds_run_state(client):
    goal = _create_goal(client)
    out = _quickstart(client, goal["id"])
    assert out.get("job_id")
    body = _poll_job(client, out["job_id"])
    # 旧字段全部保留且可读
    for key in ("id", "goal_id", "job_type", "status", "progress", "stage",
                "error_code", "error_message", "token_usage"):
        assert key in body
    # 新可选字段
    assert body["run_id"]
    assert body["current_stage"]
    assert body["current_stage"]["id"] in (
        "persisting", "knowledge_extract")          # done 后真实阶段
    assert body["delivery"]["status"] in ("drafted", "drafted_with_issues")
    assert body["postprocess"]["job_id"]           # 后台知识提取子任务可见


def test_worker_records_state_checkpoint_and_hash(client):
    goal = _create_goal(client)
    out = _quickstart(client, goal["id"])
    _poll_job(client, out["job_id"])
    state = _state_of(out["job_id"])
    # 任务书 04 样例：主交付 done 后，current_stage 可以停在 knowledge_extract（后台处理中）
    assert state["stage_id"] in ("persisting", "knowledge_extract")
    assert state["stage_status"] in ("done", "running")
    assert state["delivery_status"] in ("drafted", "drafted_with_issues")
    assert state["version"] >= 1
    assert state["content_hash"] and state["content_hash"].startswith("sha256:")
    checkpoint = state.get("checkpoint") or {}
    assert len(checkpoint.get("completed_unit_ids") or []) >= 1
    assert state.get("total_units", 0) >= 1
    assert state["last_heartbeat"]


def test_background_knowledge_child_job_visible_and_accounted(client):
    goal = _create_goal(client)
    out = _quickstart(client, goal["id"])
    main_id = out["job_id"]
    _poll_job(client, main_id)
    children = _wait_children(client, main_id, status="done")
    assert children, "主任务 done 后必须存在 knowledge_extract 子 job"
    body = client.get(f"/api/generation-jobs/{main_id}").json()
    assert body["postprocess"]["status"] == "done"
    assert body["postprocess"]["job_id"] == children[-1]["id"]
    child = client.get(f"/api/generation-jobs/{children[-1]['id']}").json()
    assert child["parent_job_id"] == main_id
    assert child["job_type"] == "knowledge_extract"
    # 父子账本合计：usage.total_tokens ≥ 主 job 自身 token
    assert body["usage"]["total_tokens"] >= body["token_usage"]["total_tokens"]


def test_knowledge_child_failure_visible_main_delivery_intact(client, monkeypatch):
    from app.services import knowledge as knowledge_service

    def _boom(*a, **k):
        raise RuntimeError("提取替身失败")

    monkeypatch.setattr(knowledge_service, "extract_from_tutorial", _boom)
    goal = _create_goal(client, title="后台失败可见目标")
    out = _quickstart(client, goal["id"])
    main_id = out["job_id"]
    main = _poll_job(client, main_id)
    assert main["status"] == "done"                    # 主交付不受后台失败影响
    children = _wait_children(client, main_id, status="failed")
    assert any("提取替身失败" in c["error"] for c in children)
    # 已生成稿件仍可读
    detail = client.get(f"/api/tutorials/{out['tutorial']['id']}").json()
    assert detail["id"] == out["tutorial"]["id"]


# ---------------------------------------------------------------- 执行策略


def _seed_outline_job(client, *, timing: str | None):
    """构造 goal/tutorial/job，直接调用 _save_outline_version 验证提取时机。"""
    goal = _create_goal(client, title=f"策略目标-{timing}")
    db = SessionLocal()
    try:
        tutorial = Tutorial(id=f"t-policy-{timing}", goal_id=goal["id"],
                            title="策略教程", status="outline")
        db.add(tutorial)
        job = GenerationJob(goal_id=goal["id"], job_type="tutorial_outline",
                            status="running", progress=10, stage="生成大纲")
        db.add(job)
        db.commit()
        content = {
            "format_spec": {"scene": "demo"},
            "teaching_plan": {},
            "outline": [{"id": "s1", "title": "第一节"}],
            "sections": [],
        }
        if timing is not None:
            content["task_context"] = {"extensions": {"execution_policy": {
                "knowledge_extract_timing": timing}}}
        from app.llm.service import AIService

        ai = AIService(db, goal_id=goal["id"], tutorial_id=tutorial.id, job_id=job.id)
        result = tutorials_service._save_outline_version(
            db, db.get(type(tutorial), tutorial.id), tutorial, content, ai, job_id=job.id)
        return goal["id"], job.id, result
    finally:
        db.close()


@pytest.mark.parametrize("timing,expect_children", [
    (None, 1),                # 缺省/旧客户端：outline_and_final（现状不变）
    ("outline_and_final", 1),
    ("final_only", 0),
    ("disabled", 0),
])
def test_execution_policy_gates_outline_extraction(client, timing, expect_children):
    goal_id, job_id, _ = _seed_outline_job(client, timing=timing)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            count = (db.query(GenerationJob)
                     .filter(GenerationJob.parent_job_id == job_id,
                             GenerationJob.job_type == "knowledge_extract")
                     .count())
        finally:
            db.close()
        if count >= expect_children:
            break
        time.sleep(0.05)
    db = SessionLocal()
    try:
        count = (db.query(GenerationJob)
                 .filter(GenerationJob.parent_job_id == job_id,
                         GenerationJob.job_type == "knowledge_extract")
                 .count())
    finally:
        db.close()
    assert count == expect_children


# ---------------------------------------------------------------- 中断与取消


def test_orphan_sweep_marks_interrupted_and_keeps_checkpoint(client):
    db = SessionLocal()
    try:
        stale = GenerationJob(goal_id="g-any", job_type="tutorial_sections",
                              status="running", progress=50, stage="并发生成")
        db.add(stale)
        fresh = GenerationJob(goal_id="g-any", job_type="tutorial_sections",
                              status="running", progress=10, stage="并发生成")
        db.add(fresh)
        db.commit()
        old = (datetime.now().astimezone() - timedelta(hours=2)).isoformat()
        stale.updated_at = old
        stale.state_json = json.dumps({
            "stage_id": "generating", "stage_status": "running",
            "last_heartbeat": old,
            "checkpoint": {"completed_unit_ids": ["s1", "s2"], "calls": 7, "tokens": 12345},
        })
        db.commit()
        # 活动 run：注册 + 新心跳 → 不清点
        ctx = GenerationRunContext(goal_id="g-any", budget=BudgetController(),
                                   cancel_token=CancelToken())
        register_run(ctx)
        fresh.run_id = ctx.run_id
        fresh.state_json = json.dumps({"last_heartbeat": datetime.now().astimezone().isoformat()})
        db.commit()
        marked = tutorials_service.mark_orphaned_jobs_interrupted(db)
        assert marked >= 1
        db.refresh(stale)
        db.refresh(fresh)
        assert stale.status == "interrupted"
        state = json.loads(stale.state_json)
        assert state["needs_resume"] is True
        assert state["checkpoint"]["completed_unit_ids"] == ["s1", "s2"]  # checkpoint 保留
        assert state["checkpoint"]["calls"] == 7
        assert fresh.status == "running"               # 活动 run 不被误伤
        from app.services.generation_runtime import unregister_run
        unregister_run(ctx.run_id)
    finally:
        db.close()


def test_cancel_run_active_and_inactive(client):
    db = SessionLocal()
    try:
        job = GenerationJob(goal_id="g-cancel", job_type="tutorial_sections",
                            status="running", progress=10, stage="并发生成")
        db.add(job)
        db.commit()
        ctx = tutorials_service._begin_run(db, job, tutorial_id="t-cancel")
        db.commit()
        out = tutorials_service.cancel_run(db, job)
        assert out["cancelled"] is True and out["active"] is True
        assert ctx.cancel_token.cancelled()
        state = json.loads(db.get(GenerationJob, job.id).state_json)
        assert state["cancel_requested"] is True
        # 非活动 run：只记录取消意图
        job2 = GenerationJob(goal_id="g-cancel", job_type="tutorial_sections",
                             status="queued", progress=0, stage="排队")
        db.add(job2)
        db.commit()
        out2 = tutorials_service.cancel_run(db, job2)
        assert out2["cancelled"] is False and out2["active"] is False
        assert out2["reason"] == "run_not_active_in_this_process"
        from app.services.generation_runtime import unregister_run
        unregister_run(ctx.run_id)
    finally:
        db.close()


# ---------------------------------------------------------------- 续跑复用


def _seed_resume_fixture(client):
    """构造「中断遗留 + 用户显式续跑」场景：返回 (tutorial_id, prev_draft_id)。"""
    from app.models import TutorialVersion as TV

    goal = _create_goal(client, title="续跑复用目标")
    db = SessionLocal()
    try:
        tutorial = Tutorial(id="t-resume", goal_id=goal["id"], title="续跑教程", status="outline")
        db.add(tutorial)
        outline_version = TV(
            id="tv-resume-1", tutorial_id=tutorial.id, version=1,
            content_json=json.dumps({
                "format_spec": {"scene": "demo"},
                "outline": [{"id": "s1", "title": "第一节"}, {"id": "s2", "title": "第二节"}],
                "sections": [],
            }, ensure_ascii=False),
        )
        db.add(outline_version)
        tutorial.current_version_id = outline_version.id
        # 中断那次运行的 draft：已完成 s1（已计费），s2 未完成
        prev_draft = TV(
            id="tv-resume-draft", tutorial_id=tutorial.id, version=2,
            content_json=json.dumps({
                "format_spec": {"scene": "demo"},
                "outline": [{"id": "s1", "title": "第一节"}, {"id": "s2", "title": "第二节"}],
                "sections": [{"id": "s1", "title": "第一节", "content": ["已完成的第一节"]}],
            }, ensure_ascii=False),
        )
        db.add(prev_draft)
        interrupted = GenerationJob(goal_id=goal["id"], job_type="tutorial_sections",
                                    status="interrupted", progress=40, stage="中断")
        db.add(interrupted)
        db.commit()
        interrupted.state_json = json.dumps({
            "stage_id": "generating", "stage_status": "running",
            "needs_resume": True,
            "checkpoint": {"completed_unit_ids": ["s1"], "draft_version_id": "tv-resume-draft",
                           "calls": 3, "tokens": 900},
        })
        db.commit()
        return tutorial.id, prev_draft.id
    finally:
        db.close()


def test_resume_reuses_completed_units_without_regenerating(client, monkeypatch):
    from app.models import TutorialVersion

    tutorial_id, prev_draft_id = _seed_resume_fixture(client)
    calls: list[str] = []
    original = tutorials_service._generate_section_task

    def counting(**kwargs):
        calls.append(str(kwargs.get("item", {}).get("id") or kwargs.get("index")))
        return original(**kwargs)

    monkeypatch.setattr(tutorials_service, "_generate_section_task", counting)
    db = SessionLocal()
    try:
        job = GenerationJob(goal_id=db.get(Tutorial, tutorial_id).goal_id,
                            job_type="tutorial_sections", status="queued",
                            progress=0, stage="排队（续跑）")
        db.add(job)
        ctx = tutorials_service._begin_run(db, job, tutorial_id=tutorial_id)
        db.commit()
        job_id = job.id
        tutorials_service._sections_worker(tutorial_id, job_id, ctx)  # 同步执行（demo）
        db.refresh(job)
        assert job.status == "done"
        assert calls == ["s2"], f"只应生成未完成的单元，实际生成：{calls}"
        # 新版本包含复用的 s1 与新生成的 s2
        tutorial = db.get(Tutorial, tutorial_id)
        version = db.get(TutorialVersion, tutorial.current_version_id)
        sections = json.loads(version.content_json).get("sections") or []
        by_id = {s.get("id"): s for s in sections}
        assert by_id["s1"]["content"] == ["已完成的第一节"]      # 复用，未重新生成
        assert by_id["s2"]                                       # 新生成
        state = json.loads(db.get(GenerationJob, job_id).state_json)
        assert set(state["checkpoint"]["completed_unit_ids"]) == {"s1", "s2"}
    finally:
        db.close()

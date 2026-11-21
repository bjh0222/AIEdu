# -*- coding: utf-8 -*-
"""C（任务书 05 切片 C）显式重评消费与状态机测试。

- POST /training/feedback-evidence/{event_id}/review-request：
  无可定位题目 → needs_triage（不造题）；题干逐字匹配命题库 → dispatched（返回
  task_id + ADR-020 预算摘要，适配既有指定题目重评任务）；人工指认 question_id
  亦可；多义/无匹配 → needs_triage 列候选；
- revaluation 状态机 pending_review→dispatched→reviewed/failed + cancel；
  撤回产生 tombstone（未派发消费取消，历史保留）；
- 回归待办绑定 fixed_case_id/case_version/report_id，满足判据才 resolved。

全部零模型调用（start_analysis 后台线程替身规避真实执行）。
"""
from __future__ import annotations

import json

import pytest

from app.db import SessionLocal
from app.config import settings
from app.models import PropositionAnalysisJob, PropositionBatch, PropositionQuestion, Tutorial, TutorialVersion
from app.services import feedback_evidence as fe
from app.services.generation_runtime import active_run_ids, unregister_run


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    target = tmp_path / "analysis"
    monkeypatch.setattr(settings, "analysis_dir", str(target))
    return target


@pytest.fixture(autouse=True)
def _no_background_analysis(monkeypatch):
    """重评任务的模型执行不在本测试范围：_run_job 替身为空操作，只验证桥接与状态机。"""
    monkeypatch.setattr("app.services.proposition_knowledge._run_job", lambda job_id: None)


STEM = "第 2 题 已知矩阵 A = [[1,2],[3,4]]，求其特征值与特征向量，并说明几何意义。"


def _seed_world(client, *, question: bool = True) -> dict:
    """goal/tutorial/version（quality_report 带第 2 题 E017）+ 命题库同题干题。"""
    goal = client.post("/api/goals", json={
        "title": "重评消费目标", "domain_key": "math_logic", "motivation": "测试"}).json()
    db = SessionLocal()
    try:
        tutorial = Tutorial(id="t-review", goal_id=goal["id"], title="重评教程", status="drafted")
        db.add(tutorial)
        content = {
            "format_spec": {"scene": "exam"},
            "outline": [{"id": "s1", "title": "试卷一"}],
            "sections": [{"id": "s1", "title": "试卷一",
                          "content": [{"type": "text", "title": "第 2 题",
                                       "body": "已知矩阵 A = [[1,2],[3,4]]，求其特征值与特征向量，并说明几何意义。"}]}],
        }
        version = TutorialVersion(
            id="tv-review-1", tutorial_id=tutorial.id, version=1,
            content_json=json.dumps(content, ensure_ascii=False),
            quality_report=json.dumps({"issues": ["[E017] 第 2 题缺少答案与解析节"]}, ensure_ascii=False),
        )
        db.add(version)
        tutorial.current_version_id = version.id
        question_id = None
        if question:
            from app.services.proposition_knowledge import ensure_framework

            ensure_framework(db)
            batch = PropositionBatch(id="batch-review", filename="bank.md",
                                     domain_key="math_logic", status="parsed")
            db.add(batch)
            row = PropositionQuestion(id="pq-review", batch_id=batch.id,
                                      domain_key="math_logic", raw_text=STEM,
                                      raw_stem=STEM, parse_status="ready",
                                      question_number="2")
            db.add(row)
            question_id = row.id
        db.commit()
        return {"goal_id": goal["id"], "tutorial_id": tutorial.id,
                "version_id": version.id, "question_id": question_id}
    finally:
        db.close()


def _submit_feedback(client, world) -> str:
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "第 2 题缺少答案",
        "goal_id": world["goal_id"], "tutorial_id": world["tutorial_id"],
        "tutorial_version_id": world["version_id"],
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["context"]["feedback_evidence"]["event_id"]


def _revaluations():
    return fe.list_evidence(limit=200)["revaluations"]


def test_review_request_without_question_number_needs_triage(client, ledger_dir):
    """负反馈但缺陷无题号：needs_triage，不造题（ledger_dir 隔离台账）。"""
    goal = client.post("/api/goals", json={
        "title": "无题号反馈", "domain_key": "cs_it"}).json()
    resp = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": -1, "comment": "整体排版不舒服"})
    assert resp.status_code == 200
    event_id = resp.json()["context"]["feedback_evidence"]["event_id"]
    out = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "needs_triage" and body["task_id"] is None
    rows = [r for r in _revaluations() if r["event_id"] == event_id]
    assert rows and rows[-1]["status"] == "needs_triage"


def test_review_request_with_locatable_question_dispatches(client, ledger_dir):
    world = _seed_world(client, question=True)
    event_id = _submit_feedback(client, world)
    out = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "dispatched"
    assert body["task_id"]
    assert body["budget"]["max_model_calls"] == 200 and body["budget"]["retries"] == 1
    # 既有指定题目重评任务确实建了单题任务
    db = SessionLocal()
    try:
        job = db.get(PropositionAnalysisJob, body["task_id"])
        assert job is not None
        assert json.loads(job.question_ids) == [world["question_id"]]
    finally:
        db.close()
    rows = [r for r in _revaluations() if r["event_id"] == event_id]
    assert rows[-1]["status"] == "dispatched"
    assert rows[-1]["task_id"] == body["task_id"]
    assert "history" in rows[-1]


def test_review_request_no_bank_match_needs_triage_without_fabrication(client, ledger_dir):
    world = _seed_world(client, question=False)  # 命题库无同题干
    event_id = _submit_feedback(client, world)
    out = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    assert out.status_code == 200, out.text
    body = out.json()
    assert body["status"] == "needs_triage"
    db = SessionLocal()
    try:
        assert db.query(PropositionAnalysisJob).count() == 0   # 不造任务
    finally:
        db.close()


def test_review_request_ambiguous_or_explicit_question_id(client, ledger_dir):
    world = _seed_world(client, question=True)
    db = SessionLocal()
    try:
        # 同题干第二题 → 多义
        db.add(PropositionQuestion(id="pq-review-2", batch_id="batch-review",
                                   domain_key="math_logic", raw_text=STEM,
                                   raw_stem=STEM, parse_status="ready"))
        db.commit()
    finally:
        db.close()
    event_id = _submit_feedback(client, world)
    ambiguous = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    assert ambiguous.status_code == 200
    assert ambiguous.json()["status"] == "needs_triage"
    assert len(ambiguous.json().get("candidates") or []) == 2   # 候选列出，不自动挑
    # 人工甄别：显式指认 question_id → dispatched
    explicit = client.post(f"/api/training/feedback-evidence/{event_id}/review-request",
                           json={"question_id": "pq-review"})
    assert explicit.status_code == 200, explicit.text
    assert explicit.json()["status"] == "dispatched"


def test_revaluation_cancel_transitions(client, ledger_dir):
    world = _seed_world(client, question=False)
    event_id = _submit_feedback(client, world)
    first = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    request_id = first.json()["request_id"]
    out = client.post(f"/api/training/feedback-evidence/revaluations/{request_id}/cancel",
                      json={"reason": "误报"})
    assert out.status_code == 200 and out.json()["status"] == "cancelled"
    rows = [r for r in _revaluations() if r["request_id"] == request_id]
    assert rows[-1]["status"] == "cancelled"
    # 终态再取消 → 409
    again = client.post(f"/api/training/feedback-evidence/revaluations/{request_id}/cancel", json={})
    assert again.status_code == 409


def test_sync_revaluation_states_reviewed_and_failed(client, ledger_dir):
    world = _seed_world(client, question=True)
    event_id = _submit_feedback(client, world)
    dispatched = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={}).json()
    db = SessionLocal()
    try:
        job = db.get(PropositionAnalysisJob, dispatched["task_id"])
        job.status = "done"                                   # 替身任务直接落终态
        db.commit()
    finally:
        db.close()
    listing = client.get("/api/training/feedback-evidence").json()   # 读取侧同步
    row = next(r for r in listing["revaluations"] if r["event_id"] == event_id)
    assert row["status"] == "reviewed"
    assert row["internal_evaluation"]["task_id"] == dispatched["task_id"]
    # failed 分支
    event2 = _submit_feedback(client, world)                  # 同文重复 → 去重！
    assert event2 == event_id
    # 新建一条不同文本的事件验证 failed 分支
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "第 2 题解析跳步且符号错误",
        "goal_id": world["goal_id"], "tutorial_id": world["tutorial_id"],
        "tutorial_version_id": world["version_id"]})
    event3 = resp.json()["context"]["feedback_evidence"]["event_id"]
    d2 = client.post(f"/api/training/feedback-evidence/{event3}/review-request", json={}).json()
    db = SessionLocal()
    try:
        job2 = db.get(PropositionAnalysisJob, d2["task_id"])
        job2.status = "failed"
        db.commit()
    finally:
        db.close()
    listing2 = client.get("/api/training/feedback-evidence").json()
    row2 = next(r for r in listing2["revaluations"] if r["event_id"] == event3)
    assert row2["status"] == "failed"


def test_retraction_cancels_undispatched_consumption(client, ledger_dir):
    world = _seed_world(client, question=False)
    event_id = _submit_feedback(client, world)
    client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    retract = client.post(f"/api/training/feedback-evidence/{event_id}/retract",
                          json={"reason": "误点撤回"})
    assert retract.status_code == 200
    rows = [r for r in _revaluations() if r["event_id"] == event_id]
    assert rows[-1]["status"] == "cancelled"
    assert any("tombstone" in (h.get("note") or "") for h in rows[-1].get("history", []))
    # 撤回后的事件不能再发起重评
    again = client.post(f"/api/training/feedback-evidence/{event_id}/review-request", json={})
    assert again.status_code == 409


def test_regression_todo_binding_and_resolution_gate(client, ledger_dir):
    world = _seed_world(client, question=False)
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "第 2 题缺答案",
        "goal_id": world["goal_id"], "tutorial_id": world["tutorial_id"],
        "tutorial_version_id": world["version_id"]})
    event_id = resp.json()["context"]["feedback_evidence"]["event_id"]
    listing = client.get("/api/training/feedback-evidence").json()
    todo = next(r for r in listing["regressions"] if r["event_id"] == event_id)
    # 未绑定不能 resolved
    with pytest.raises(ValueError, match="unbound"):
        fe.resolve_regression_todo(todo["todo_id"], report_passed=True)
    # 绑定 + 报告未通过 → 保持 open
    fe.bind_regression_todo(todo["todo_id"], fixed_case_id="exam-no-answers-two-papers",
                            case_version=1, report_id="report-1")
    kept = fe.resolve_regression_todo(todo["todo_id"], report_passed=False, report_id="report-2")
    assert kept["status"] == "open"
    # 报告通过 → resolved
    resolved = fe.resolve_regression_todo(todo["todo_id"], report_passed=True, report_id="report-3")
    assert resolved["status"] == "resolved"
    listing2 = client.get("/api/training/feedback-evidence").json()
    row = next(r for r in listing2["regressions"] if r["todo_id"] == todo["todo_id"])
    assert row["status"] == "resolved" and row["fixed_case_id"] == "exam-no-answers-two-papers"


class _NullThreadGuard:
    """占位：确保测试结束不遗留活动 run 注册表。"""


def teardown_module(module):
    for run_id in list(active_run_ids()):
        unregister_run(run_id)

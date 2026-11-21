"""02 切片 5：criteria 角色分类 + 可回退 TaskExecutionPolicy。

钉死任务书 02-5：
- criteria 分 learner_outcome 与 delivery_requirement：提示词要求模型显式标注；
  既有数据无字段时确定性分类器兜底（不要求改表结构）；
- TaskExecutionPolicy 默认 off：行为与从前逐字一致；
  AIEDU_TASK_EXECUTION_POLICY=on 且 artifact（exam/exercise）时跳过无消费者的
  学习诊断/计划，criteria 照常生成，端到端消费者（展示端/大纲/章节/验收）不破坏；
- 成本对照：跳过后不产生 diagnose/plan 模型调用（ModelCallLog 无该 feature）。
零真实模型调用（demo 全链路）。
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from app.db import SessionLocal
from app.llm import prompts
from app.models import GoalAssessment, LearningGoal, LearningPlan, ModelCallLog
from app.services.task_understanding import (
    classify_criterion_role,
    classify_criteria_roles,
    decide_execution_policy,
)


# ---------------------------------------------------------------- criteria 角色分类

def test_criteria_prompt_requests_role_split():
    goal = SimpleNamespace(
        id="g", title="出两套试卷", domain_key="math_logic", branch="", motivation="",
        application_context="", current_level="", time_budget="",
        hard_constraints="", user_notes="", format_spec_snapshot="", format_spec_source="")
    _system, user = prompts.criteria(goal, SimpleNamespace(
        display_name="线性代数", assessment_rubrics=[]))
    assert "criterion_type" in user
    assert "delivery_requirement" in user and "learner_outcome" in user
    assert "交付规格与用户明确要求" in user


def test_classify_criterion_role_is_deterministic():
    # 显式标注优先
    assert classify_criterion_role(
        {"id": "c1", "statement": "能够解释秩",
         "criterion_type": "delivery_requirement"}) == "delivery_requirement"
    # 文本推断：交付规格词优先于「能够」开头
    assert classify_criterion_role(
        {"id": "c2", "statement": "能够生成题号连续的试卷，答案集中在卷末"}) == "delivery_requirement"
    assert classify_criterion_role(
        {"id": "c3", "statement": "能够解释行列式的几何含义",
         "pass_rule": "解释包含几何直觉"}) == "learner_outcome"
    # 都不命中时按任务类型默认
    assert classify_criterion_role({"id": "c4", "statement": "结果可复核"}, "exam") == \
        "delivery_requirement"
    assert classify_criterion_role({"id": "c5", "statement": "结果可复核"}, "knowledge_tutorial") == \
        "learner_outcome"
    roles = classify_criteria_roles([
        {"id": "a", "statement": "试卷题号连续"}, {"id": "b", "statement": "能够独立完成变式"}], "exam")
    assert roles == {"a": "delivery_requirement", "b": "learner_outcome"}


# ---------------------------------------------------------------- TaskExecutionPolicy

def _create_goal(client, **overrides) -> dict:
    payload = {
        "title": "掌握命令行管道与重定向",
        "domain_key": "cs_it",
        "motivation": "能独立处理日志与数据流",
        "application_context": "分析服务器日志",
        "current_level": "会用 ls/cd 等基础命令",
        "time_budget": "共 10 小时",
        "hard_constraints": "只输出教程正文",
    }
    payload.update(overrides)
    resp = client.post("/api/goals", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _run_quickstart(client, goal_id: str) -> dict:
    resp = client.post(f"/api/goals/{goal_id}/tutorials/quickstart", timeout=None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for _ in range(600):
        job = client.get(f"/api/generation-jobs/{body['job_id']}").json()
        if job["status"] in ("done", "failed"):
            assert job["status"] == "done", job
            break
        time.sleep(0.05)
    else:
        raise AssertionError("quickstart job 未在时限内完成")
    return body


def _exam_goal_payload() -> dict:
    return {
        "title": "出两套线性代数期中模拟卷",
        "domain_key": "math_logic",
        "motivation": "期中自测",
        "application_context": "按参考风格出卷",
        "current_level": "本科生",
        "time_budget": "",
        "hard_constraints": "只输出两套试卷\n不要安排任何日程",
    }


def test_quickstart_default_keeps_diagnosis_and_plan(client):
    """默认 off：exam 目标仍生成诊断/计划，行为与从前一致。"""
    goal = _create_goal(client, **_exam_goal_payload())
    body = _run_quickstart(client, goal["id"])
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["diagnose"]["status"] == "created"
    assert steps["plan"]["status"] == "created"
    # 执行策略经 task-understanding 摘要可见（quickstart 响应模型不含协议新键）
    summary = client.get(f"/api/goals/{goal['id']}/task-understanding").json()["summary"]
    assert summary["execution_policy"]["delivery_mode"] == "artifact"
    assert summary["execution_policy"]["skip_learning_stages"] is False
    with SessionLocal() as db:
        assert db.query(GoalAssessment).filter(GoalAssessment.goal_id == goal["id"]).count() >= 1
        assert db.query(LearningPlan).filter(LearningPlan.goal_id == goal["id"]).count() >= 1


def test_quickstart_artifact_policy_skips_learning_stages_end_to_end(client, monkeypatch):
    """激活 + artifact：跳过诊断/计划，criteria 照常，端到端消费者不破坏。"""
    monkeypatch.setenv("AIEDU_TASK_EXECUTION_POLICY", "on")
    goal = _create_goal(client, **_exam_goal_payload())
    body = _run_quickstart(client, goal["id"])
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["diagnose"]["status"] == "skipped"
    assert steps["plan"]["status"] == "skipped"
    assert steps["criteria"]["status"] in {"created", "reused"}
    with SessionLocal() as db:
        assert db.query(GoalAssessment).filter(GoalAssessment.goal_id == goal["id"]).count() == 0
        assert db.query(LearningPlan).filter(LearningPlan.goal_id == goal["id"]).count() == 0
        features = {row.feature for row in db.query(ModelCallLog)
                    .filter(ModelCallLog.goal_id == goal["id"]).all()}
        assert "diagnose" not in features and "plan" not in features, \
            f"跳过后不得产生诊断/计划调用：{sorted(features)}"
    # 展示端消费者容忍缺失
    assert client.get(f"/api/goals/{goal['id']}/diagnose").status_code == 200
    assert client.get(f"/api/goals/{goal['id']}/plan").status_code == 200
    assert client.get(f"/api/goals/{goal['id']}").status_code == 200
    # 大纲与 TaskContext 照常：交付物完整、策略可构建
    tutorial = client.get(f"/api/tutorials/{body['tutorial']['id']}").json()
    content = tutorial["current_version"]["content"]
    assert content.get("outline")
    assert content["task_context"]["extensions"]["execution_policy"]["delivery_mode"] == "artifact"
    summary = client.get(f"/api/goals/{goal['id']}/task-understanding").json()["summary"]
    assert summary["execution_policy"]["delivery_mode"] == "artifact"


def test_quickstart_policy_on_learning_goal_not_skipped(client, monkeypatch):
    """激活但 learning 任务：诊断/计划照常（有真实消费者）。"""
    monkeypatch.setenv("AIEDU_TASK_EXECUTION_POLICY", "on")
    goal = _create_goal(client)
    body = _run_quickstart(client, goal["id"])
    steps = {s["key"]: s for s in body["steps"]}
    assert steps["diagnose"]["status"] == "created"
    assert steps["plan"]["status"] == "created"
    summary = client.get(f"/api/goals/{goal['id']}/task-understanding").json()["summary"]
    assert summary["execution_policy"]["delivery_mode"] == "learning"


def test_policy_decision_is_pure_and_documented():
    policy = decide_execution_policy("exam")
    assert policy["delivery_mode"] == "artifact"
    assert policy["knowledge_extract_timing"] == "outline_and_final"
    assert policy["skip_learning_stages"] is False
    assert "ADR" in policy["reason"]
    assert json.dumps(policy, ensure_ascii=False)

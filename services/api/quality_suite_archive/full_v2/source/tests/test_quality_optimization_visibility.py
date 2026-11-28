import json
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.models import GenerationJob, LearningGoal, Tutorial, TutorialVersion
from app.services import proposition_generated as pg, proposition_knowledge as pk, tutorials
from app.services.generation_runtime import active_run, unregister_run
from app.services.task_understanding import resolve_difficulty
from app.services.utils import jdump, jload
from app.llm.service import AIService
from app.services import exam_verify


def test_explicit_harder_constraint_beats_foundation_topic():
    goal = SimpleNamespace(title="矩阵基础与线性代数入门", difficulty_mode="", difficulty_note="",
                           hard_constraints="难度高于例题来源，总共输出2套试卷")
    profile = {"source_refs": [{"kind": "material", "id": "m1"}]}
    assert resolve_difficulty(goal, profile)["mode"] == "above_reference"


def test_tool_transcription_retries_only_invalid_items():
    class AI:
        is_demo = False
        calls = []
        def _real_json(self, feature, system, user, keys, goal):
            self.calls.append(user)
            if len(self.calls) == 1:
                return {"items": [{"number": 1, "sympy_code": "RESULT = 5"},
                                  {"number": 2, "sympy_code": "计算结果为3"}]}
            return {"items": [{"number": 2, "sympy_code": "RESULT = 3"}]}
    ai = AI()
    values = AIService.transcribe_for_tools(ai, None, None, "试卷", [
        {"number": 1, "question": "2+3", "answer": "5"},
        {"number": 2, "question": "1+2", "answer": "3"}])
    assert values == {1: "RESULT = 5", 2: "RESULT = 3"}
    assert "共 1 道题" in ai.calls[1] and "第 1 题" not in ai.calls[1]


def test_blind_solution_closes_tool_unknown_without_changing_answer(monkeypatch):
    monkeypatch.setattr(exam_verify, "_tool_verify_paper", lambda *args: ([
        {"number": 1, "verdict": "undecidable", "confidence": "low", "reasoning": "工具不能表达"}], set()))
    class AI:
        is_demo = False
        def verify_questions(self, *args):
            return [{"number": 1, "solved": True, "confidence": "high", "my_answer": "5", "reasoning": "2+3=5"}]
    body = "> 本卷满分100分，共1题。\n\n### 填空题\n\n**1.**（100分）2+3=___。\n\n### 参考答案\n\n**1.** 5\n"
    report = exam_verify.verify_paper_with_ai(AI(), None, None, "试卷", body, "exam")
    assert not report["skipped"] and not report["changed"]
    assert report["tool_unresolved"]  # 方法局限留痕，但不再冒充最终未决结论。


@pytest.mark.parametrize("unresolved", [False, True])
def test_unresolved_review_retries_without_authorizing_edits(client, monkeypatch, unresolved):
    calls = []
    class AI:
        is_demo = False
        def __init__(self, **kwargs): pass
        def _real_json(self, feature, system, user, *args):
            calls.append(feature)
            return {"defects": [], "uncertainties": ["无法确认第1题设问是否唯一。"]
                    if unresolved or len(calls) == 1 else []}
        def _real_text(self, *args):
            pytest.fail("未决项不授权改写正文")
    monkeypatch.setattr(pg, "AIService", AI)
    with SessionLocal() as db:
        goal = LearningGoal(title="试卷", domain_key="math_logic")
        db.add(goal); db.commit()
        pk.save_config(db, {"auto_evaluate_generated": True})
        content = {"format_spec": {"scene": "exam_paper"}, "sections": [
            {"id": "s1", "title": "试卷", "content": [{"type": "text", "body":
                "> 本卷满分100分，共1题。\n\n### 解答题\n\n**1.**（100分）求结果。\n\n### 参考答案\n\n**1.** 由已知条件逐步推导得到正确结果，推理完整。"}]}]}
        quality = {"checks": [{"name": "结构", "passed": True}], "issues": []}
        tutorials._apply_exam_quality(db, content, quality, goal, "t1", "", verify_enabled=False)
    assert len(calls) == (tutorials.settings.quality_review_retries + 1 if unresolved else 2)
    assert quality["repair_loop"]["stop_reason"] == ("review_limit" if unresolved else "passed")
    assert quality["passed"] is not unresolved


@pytest.mark.parametrize("successful_attempt", [2, 4])
def test_tutorial_text_defect_gets_repeated_protected_repair(client, monkeypatch, successful_attempt):
    repairs = []
    class AI:
        is_demo = False
        def __init__(self, **kwargs): pass
        def _real_json(self, feature, *args):
            if feature == "proposition_generated_review":
                return {"defects": [{"severity": "blocking", "reason": "原理推导错误", "evidence": "错误结论"}]}
            return {"defects": [], "uncertainties": []}
        def _real_text(self, feature, system, payload, goal):
            repairs.append(feature)
            original = json.loads(payload)["original"]
            return "" if len(repairs) < successful_attempt else original.replace("错误结论", "正确结论")
    monkeypatch.setattr(pg, "AIService", AI)
    with SessionLocal() as db:
        goal = LearningGoal(title="原理教程", domain_key="math_logic")
        db.add(goal); db.commit()
        pk.save_config(db, {"auto_evaluate_generated": True})
        content = {"format_spec": {"scene": "default_tutorial"}, "sections": [
            {"id": "s1", "title": "原理", "content": [{"type": "text", "title": "推导", "body": "由条件得到错误结论，下面说明理由。"}]}]}
        quality = {"checks": [{"name": "结构", "passed": True}], "issues": []}
        tutorials._apply_exam_quality(db, content, quality, goal, "t1", "", verify_enabled=False)
    assert len(repairs) == successful_attempt
    assert "正确结论" in content["sections"][0]["content"][0]["body"]
    assert quality["passed"]
    assert quality["repair_loop"]["rounds"][0]["status"] == "failed"


def test_job_discovery_and_quality_resume_preserve_source(client, monkeypatch):
    observed = []
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): pass
    monkeypatch.setattr(tutorials.threading, "Thread", Thread)
    monkeypatch.setattr(tutorials.settings, "exam_verify_enabled", False)
    class AI:
        is_demo = False
        def __init__(self, **kwargs): pass
        def _real_json(self, *args):
            state = client.get(f"/api/generation-jobs/{job['id']}").json()
            observed.append(state["optimization"]["current_round"])
            assert state["status"] == "running"
            return {"defects": [], "uncertainties": []}
    monkeypatch.setattr(pg, "AIService", AI)
    original = {"format_spec": {"scene": "default_tutorial"},
                "outline": [{"id": "s1", "title": "概念", "criteria_ids": []}],
                "sections": [{"id": "s1", "title": "概念", "content": [{"type": "text", "body": "定义与适用条件。"}]}]}
    with SessionLocal() as db:
        goal = LearningGoal(title="教程", domain_key="math_logic")
        db.add(goal); db.flush()
        tutorial = Tutorial(goal_id=goal.id, title="教程")
        db.add(tutorial); db.flush()
        source = TutorialVersion(tutorial_id=tutorial.id, version=1, content_json=jdump(original))
        db.add(source); db.flush()
        tutorial.current_version_id = source.id
        db.add(TutorialVersion(tutorial_id=tutorial.id, version=4, content_json=jdump(original)))
        db.commit()
        tid, source_id = tutorial.id, source.id
        pk.save_config(db, {"auto_evaluate_generated": True})
    first = client.post(f"/api/tutorials/{tid}/quality/optimize").json()
    second = client.post(f"/api/tutorials/{tid}/quality/optimize").json()
    assert first["job_id"] == second["job_id"]
    job = client.get(f"/api/tutorials/{tid}/generation-jobs").json()[0]
    assert job["id"] == first["job_id"] and job["tutorial_id"] == tid
    ctx = active_run(job["run_id"])
    assert ctx.version == 5  # 遗留草稿不导致下一次优化复用同一版本号。
    try:
        tutorials._quality_worker(tid, job["id"], ctx)
        final = client.get(f"/api/generation-jobs/{job['id']}").json()
        assert final["status"] == "done"
        assert final["optimization"]["rounds"]
        assert final["optimization"]["stop_reason"]
        assert observed and observed[0] == 1
        with SessionLocal() as db:
            assert jload(db.get(TutorialVersion, source_id).content_json, {}) == original
            assert db.get(Tutorial, tid).current_version_id != source_id
    finally:
        unregister_run(job["run_id"])

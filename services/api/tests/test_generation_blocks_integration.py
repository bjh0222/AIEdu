"""Exercise the three real consumers together using isolated DBs and model doubles."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import httpx
import pytest

from app import models
from app.db import SessionLocal
from app.learning.disciplines import get_profile
from app.llm.provider import OpenAICompatibleProvider
from app.llm.service import AIService
from app.schemas import PropositionBatchImport
from app.services import generation_acceptance as A
from app.services import proposition_evidence as E
from app.services import proposition_knowledge as K
from app.services import synthesis
from app.services.generation_contract import validate_generation_exchange, validate_generation_message
from app.services.utils import jdump, jload


FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/generation.v1.examples.json").read_text(encoding="utf-8-sig"))


def fixture(kind):
    return copy.deepcopy(next(message for message in FIXTURES if message["kind"] == kind))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("Integration tests cannot call real models or embeddings")
    monkeypatch.setattr(httpx, "post", prohibited)
    monkeypatch.setattr(OpenAICompatibleProvider, "_post", prohibited)


class ItemModelDouble:
    is_demo = False

    def __init__(self, **kwargs):
        pass

    def _real_json(self, feature, system, user, required, *args, **kwargs):
        if feature == "proposition_blind_solve":
            assert "IMMUTABLE_EXTERNAL" not in user
            return {"answer": "由导数定义构造差商并取极限。", "solvable": True,
                    "confidence": .95, "verification": "可复核静态推导"}
        if feature == "proposition_evaluate":
            return {"correctness": {"status": "supported", "reason": "题面与独立推导相容"},
                    "difficulty": {"level": 3, "learner": "已学导数定义的本科生", "sources": ["构造差商"]},
                    "quality": {"strengths": ["考查定义迁移"], "limitations": []},
                    "teaching_value": "对照定义与条件", "collection_value": "候选证据",
                    "structure": {"mechanism": "从函数关系构造差商", "invariants": ["导数定义"],
                                  "variable_factors": ["条件关系"], "boundaries": ["须满足可导条件"]},
                    "recommendation": "consider", "confidence": .95,
                    "knowledge_points": ["导数定义"], "question_type": "证明题", "uncertainties": []}
        if feature == "proposition_experience":
            samples = json.loads(user.split("\nJSON：", 1)[0])["samples"]
            return {"principles": [{"title": "构造差商", "statement": "导数定义题可通过条件关系考查差商构造",
                "mechanism": "保留导数定义，改变条件关系与构造要求", "applicability": "学习者已学导数定义",
                "guidance": "给出能构造差商的函数关系并检查条件边界",
                "scope": {"knowledge_points": ["导数定义"], "question_types": ["证明题"]},
                "positive_refs": [s["question_id"] for s in samples], "negative_refs": [],
                "counterexamples": [], "confidence": .95, "rationale": "两独立结构家族的静态证据"}]}
        raise AssertionError(feature)


class GenerationModelDouble:
    model = "offline-integration-double"

    def __init__(self):
        self.calls = []

    def complete_json(self, system, user):
        self.calls.append((system, user))
        index = len(self.calls)
        if index == 1:
            return {"sections": [{"section_id": "unit-1", "global_position": "导数定义迁移"}],
                    "global_structure": []}, 1, None
        if index == 2:
            return {"sections": [{"section_id": "unit-1", "knowledge_points": ["导数定义"],
                                  "question_types": ["证明题"], "practice_focus": "构造差商"}]}, 1, None
        return {"title": "模拟试卷", "content": [{"type": "text", "title": "模拟试卷",
            "body": "1. 由导数定义证明可导函数连续。\n答案：将增量写成差商乘增量，再取极限。"}]}, 1, None


def test_assessment_principle_planning_prompt_unit_and_acceptance_share_evidence(client):
    source = ("## 第1题\n由导数定义证明可导函数连续。\n外部评价：IMMUTABLE_EXTERNAL_A\n"
              "## 第2题\n设函数满足加法关系，证明其在零点导数决定所有点导数。\n外部评价：IMMUTABLE_EXTERNAL_B\n")
    with SessionLocal() as db:
        imported = K.import_batch(db, PropositionBatchImport(filename="integration.md", markdown=source,
            domain_key="math_logic", branch="calculus", learner="已学导数定义的本科生", auto_analyse=False))
        question_ids = [q["id"] for q in imported["questions"]]
        external_before = db.get(models.PropositionQuestion, question_ids[0]).external_evaluation
        framework = K.active_framework(db, "math_logic")
        framework_id = framework.id
    for identifier in question_ids:
        assert K.analyse_question(identifier, framework_id, ai_factory=ItemModelDouble)["status"] == "analysed"
    with SessionLocal() as db:
        assessment = K.assessment_for_question(db, question_ids[0])
        validate_generation_message(assessment, "ItemAssessment")
        assert assessment["payload"]["difficulty"]["evidence_basis"] == "prior"
        original = db.get(models.PropositionQuestion, question_ids[0])
        assert original.external_evaluation == external_before
        principles = K.distill_evaluations(db, question_ids, framework_id, ItemModelDouble())
        assert len(principles) == 1
        principle = db.get(models.DesignPrinciple, principles[0])
        assert principle.status == "auto_accepted" and principle.evidence_family_count == 2
        K.save_config(db, {"budget_chars": 6000, "complex_budget_chars": 6000, "include_examples": False})
        goal = models.LearningGoal(title="导数定义证明题模拟试卷", domain_key="math_logic",
            application_context="导数定义迁移", current_level="已学导数定义的本科生",
            format_spec_snapshot=jdump({"scene": "exam_paper"}))
        db.add(goal)
        db.flush()
        tutorial = models.Tutorial(goal_id=goal.id, title="模拟试卷")
        db.add(tutorial)
        db.commit()
        task = fixture("TaskContext")
        task["identity"].update(task_id=goal.id, goal_id=goal.id, tutorial_id=tutorial.id, version=1)
        task["payload"]["scope"]["include"] = ["导数定义"]
        task["extensions"]["question_types"] = ["证明题"]
        strategy = fixture("GenerationStrategy")
        strategy["identity"] = copy.deepcopy(task["identity"])
        preview = E.planning_evidence_bundle(db, task, config={"include_examples": False})
        assert principles[0] in {b["id"] for b in preview["payload"]["principles"]}
        unit = E.unit_evidence_bundle(db, task, strategy, unit_id="unit-1", config={"include_examples": False})
        validate_generation_exchange(task, strategy, evidence_bundles=[preview, unit])
        ai = AIService(db, goal_id=goal.id, tutorial_id=tutorial.id)
        provider = GenerationModelDouble()
        ai.provider = provider
        outline = [{"id": "unit-1", "title": "导数定义证明题模拟试卷", "knowledge_points": ["导数定义"]}]
        synthesis_data, blueprint = synthesis.prepare(goal, outline, {}, None, ai)
        for _, user in provider.calls:
            assert principle.statement in user
            assert user.count(principle.statement) == 1
        attached = synthesis.attach_to_outline(outline, synthesis_data, blueprint)
        attached[0].update(_task_context=task, _generation_strategy=strategy)
        ai.section_id = "unit-1"
        output = ai._section_payload(goal, get_profile(goal.domain_key), attached[0], [], [], "", None)
        _, actual_user = provider.calls[-1]
        assert principle.statement in actual_user and actual_user.count(principle.statement) == 1
        assert '"_generation_strategy"' not in actual_user and '"_task_context"' not in actual_user
        db.commit()
        traces = db.query(models.PropositionGuidanceTrace).all()
        consumed = [call for trace in traces for call in jload(trace.result, {}).get("calls", [])]
        assert {c["feature"] for c in consumed} >= {"synthesis", "teaching_blueprint", "section"}
        assert all(c["block_present"] and c["status"] == "ok" for c in consumed)
        plan = A.plan_checks(task, strategy=strategy, rubric=[{"id": "correctness", "required": True}])
        content = {"outline": outline, "sections": [{"id": "unit-1", **output}], "format_spec": {"scene": "exam_paper"}}
        outcomes = {
            "correctness": {"status": "undecidable", "evidence_method": "model_static", "detail": "离线替身生成，不证明专业正确性。"},
            "constraint-c1": {"status": "satisfied", "evidence_method": "deterministic", "detail": "最终成果只含unit-1一套卷面。"},
        }
        report = A.quality_acceptance(goal, content, {"checks": [{"name": "结构", "passed": True}], "issues": []},
            task_context=task, strategy=strategy, planned_checks=plan, outcomes=outcomes, tutorial_id=tutorial.id, version=1)
        validate_generation_exchange(task, strategy, evidence_bundles=[preview, unit], acceptance_report=report)
        assert report["payload"]["status"] == "needs_review" and not report["payload"]["passed"]


def test_legacy_adapters_preserve_same_task_and_unknown_reference(client):
    with SessionLocal() as db:
        goal = models.LearningGoal(title="Python列表推导教程", domain_key="computer_cli", application_context="理解列表推导")
        db.add(goal)
        db.commit()
        outline = [{"id": "s1", "title": "列表推导"}, {"id": "s2", "title": "迭代器"}]
        evidence_task = E.adapt_legacy_task_context(goal, outline_items=outline, task_kind="planning", tutorial_id="t1", version=2)
        acceptance_task = A.adapt_task_context(goal, {"outline": outline}, tutorial_id="t1", version=2)
        assert evidence_task["identity"] == acceptance_task["identity"]
        assert evidence_task["payload"]["deliverables"] == acceptance_task["payload"]["deliverables"]
        assert evidence_task["payload"]["reference_profile"] is None
        assert acceptance_task["payload"]["reference_profile"] is None
        assert evidence_task["payload"]["learner"] is acceptance_task["payload"]["learner"] is None

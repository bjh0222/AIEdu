"""Delivery semantics with real contexts and fake models; no live quality claims."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.llm.service import AIService, AIServiceError
from app.models import ModelCallLog, TrainingRecord
from app.schemas import QualityReport
from app.services import exam_check, generation_acceptance as acceptance
from app.services import proposition_generated, proposition_knowledge, spec_check
from app.services.generation_contract import validate_generation_exchange, validate_generation_message

FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/generation.v1.examples.json").read_text(encoding="utf-8"))


def context(task_type="exam"):
    value = deepcopy(FIXTURES[0])
    value["payload"]["task_type"] = task_type
    return value


def goal(**kwargs):
    data = {"id": "g", "domain_key": "math_logic", "branch": "calculus", "title": "导数教程",
            "motivation": "解释导数", "application_context": "本科生课程", "current_level": "学过极限",
            "time_budget": "", "hard_constraints": ""}
    return SimpleNamespace(**{**data, **kwargs})


def content(text="### 证明题\n**1.**（5 分）证明 $x^2\\geq0$。\n### 参考答案\n**1.** 平方非负。"):
    return {"format_spec": {"scene": "exam_paper"}, "outline": [{"id": "unit-1", "title": "模拟试卷"}],
            "sections": [{"id": "unit-1", "title": "模拟试卷", "mapping_criteria": ["c"],
                          "content": [{"type": "text", "body": text}]}]}


def outcome(status="satisfied", method="deterministic"):
    return {"status": status, "evidence_method": method, "detail": "完整证据下判定",
            "evidence_refs": [acceptance.source_ref("generated", "t", version=1, location="sections[0]")]}


@pytest.mark.parametrize("kind, expected", [
    ("knowledge_tutorial", "connections"), ("practical_tutorial", "execution"),
    ("reading_guide", "source_grounding"), ("exercise", "assessment_value"), ("exam", "scope_style")])
def test_same_text_selects_task_specific_value_targets(kind, expected):
    ids = {target["id"] for target in acceptance.plan_checks(context(kind))}
    assert expected in ids
    assert "structure" in ids


def test_required_is_fixed_before_switch_and_cannot_be_downgraded_by_result():
    task = context()
    task["payload"]["constraints"] = []
    targets = acceptance.plan_checks(task, rubric=[{"id": "correctness", "required": True}])
    result = acceptance.acceptance_report(task, targets, {"structure": outcome(),
        "correctness": {"status": "not_run", "required": False, "applicable": False}})
    check = next(c for c in result["payload"]["checks"] if c["id"] == "correctness")
    assert check["required"] and check["applicable"]
    assert result["payload"]["status"] == "needs_review"
    assert result["payload"]["passed"] is False
    validate_generation_exchange(task, acceptance_report=result)


def test_disabled_optional_check_does_not_fail_satisfied_required_targets():
    """选配项（scope_style/difficulty）未运行不拖垮已满足的必需项——选配规则保持。"""
    task = context()
    task["payload"]["constraints"] = []
    targets = acceptance.plan_checks(task)
    correctness = dict(outcome(method="model_static"), id="correctness")
    result = acceptance.acceptance_report(task, targets, {"structure": outcome(), "correctness": correctness})
    assert result["payload"]["passed"] is True
    assert next(c for c in result["payload"]["checks"] if c["id"] == "scope_style")["status"] == "not_run"
    assert "真实作答" in result["extensions"]["limitation"] or "实证" in result["extensions"]["limitation"]


def test_exam_correctness_required_by_delivery_rubric_and_pending_when_review_off():
    """版本化量规 v1：试卷 correctness 必需；审查未运行 → 待复核，不暗开模型。"""
    task = context()
    task["payload"]["constraints"] = []
    targets = acceptance.plan_checks(task)
    correctness = next(p for p in targets if p["id"] == "correctness")
    assert correctness["required"] is True and correctness["strength"] == "hard"
    assert correctness["rubric_version"] == acceptance.DELIVERY_RUBRIC_VERSION
    result = acceptance.acceptance_report(task, targets, {"structure": outcome()})
    check = next(c for c in result["payload"]["checks"] if c["id"] == "correctness")
    assert check["status"] == "not_run" and check["required"] is True
    assert result["payload"]["status"] == "needs_review" and result["payload"]["passed"] is False


def test_non_exam_tutorial_keeps_value_checks_optional():
    """普通教程选配项规则保持：量规 v1 只把试卷 correctness 列为必需。"""
    targets = acceptance.plan_checks(context("knowledge_tutorial"))
    assert next(p for p in targets if p["id"] == "connections")["required"] is False
    assert next(p for p in targets if p["id"] == "connections")["rubric_version"] == acceptance.DELIVERY_RUBRIC_VERSION


@pytest.mark.parametrize("status, expected", [("violated", "failed"), ("undecidable", "needs_review"),
                                               ("error", "needs_review"), ("not_run", "not_evaluated")])
def test_required_negative_and_unknown_states_are_not_passes(status, expected):
    task = context()
    task["payload"]["constraints"] = []
    result = acceptance.acceptance_report(task, acceptance.plan_checks(task), {"structure": outcome(status)})
    assert result["payload"]["status"] == expected
    assert result["payload"]["passed"] is False


def test_absent_empirical_evidence_cannot_be_satisfied():
    task = context()
    task["payload"]["constraints"] = []
    targets = acceptance.plan_checks(task, rubric=[{"id": "learning_gain", "method": "empirical", "required": True}])
    result = acceptance.acceptance_report(task, targets, {"structure": outcome(),
        "learning_gain": {"status": "satisfied", "evidence_method": "empirical", "detail": "模型说有效"}})
    assert result["payload"]["status"] == "needs_review"


@pytest.mark.parametrize("method", ["empirical", "tool", "human"])
def test_model_score_cannot_replace_required_external_method(method):
    task = context()
    task["payload"]["constraints"] = []
    targets = acceptance.plan_checks(task, rubric=[{"id": "difficulty", "required": True, "method": method}])
    result = acceptance.acceptance_report(task, targets, {"structure": outcome(), "difficulty": outcome(method="model_static")})
    check = next(c for c in result["payload"]["checks"] if c["id"] == "difficulty")
    assert check["status"] == "undecidable" and "不能替代" in check["detail"]
    assert result["payload"]["passed"] is False


@pytest.mark.parametrize("override", [{"required": False}, {"applicable": False}, {"label": "忽略原始约束"}])
def test_rubric_cannot_disable_or_rewrite_original_hard_constraint(override):
    with pytest.raises(ValueError, match="硬约束冲突"):
        acceptance.plan_checks(context(), rubric=[{"id": "constraint-c1", **override}])


def test_strategy_check_ids_and_constraint_references_cross_validate():
    task = context()
    strategy = deepcopy(FIXTURES[1])
    targets = acceptance.plan_checks(task, strategy=strategy)
    result = acceptance.acceptance_report(task, targets, {"structure": outcome(), "constraint-c1": outcome()})
    assert result["payload"]["status"] == "needs_review"
    validate_generation_exchange(task, strategy, acceptance_report=result)


def test_user_learning_outcomes_do_not_become_mandatory_delivery_review():
    targets = acceptance.plan_checks(context(), criteria=[
        {"id": "learn", "statement": "能够解释导数定义", "pass_rule": "学习者给出解释"},
        {"id": "correct", "statement": "所有题目答案正确完整", "pass_rule": "可核查推导"},
    ])
    assert next(p for p in targets if p["id"] == "criterion-learn")["required"] is False
    assert next(p for p in targets if p["id"] == "criterion-correct")["required"] is True


def test_old_snapshot_adapter_keeps_unknowns_and_real_goal_identity():
    adapted = acceptance.adapt_task_context(goal(), content(), tutorial_id="t", version=3)
    validate_generation_message(adapted, expected_kind="TaskContext")
    assert adapted["identity"] == {"task_id": "g", "goal_id": "g", "tutorial_id": "t", "version": 3, "unit_id": None}
    assert adapted["payload"]["branch"] == "calculus"
    assert adapted["payload"]["learner"] == "学过极限"
    assert adapted["payload"]["reference_profile"] is None
    assert adapted["payload"]["difficulty_target"]["evidence_basis"] == "unknown"


def test_one_paper_guard_excludes_analysis_sections():
    task = context()
    document = content()
    document["sections"].append({"id": "analysis", "title": "参考风格分析", "content": [{"type": "text", "body": "参考卷重视推理。"}]})
    quality = {"checks": [{"name": "结构", "passed": True}], "issues": []}
    result = acceptance.quality_acceptance(goal(), document, quality, task_context=task)
    check = next(c for c in result["payload"]["checks"] if c["id"] == "constraint-c1")
    assert check["status"] == "satisfied" and "单元数为 1" in check["detail"]
    document["sections"].append(deepcopy(document["sections"][0]))
    result = acceptance.quality_acceptance(goal(), document, quality, task_context=task)
    assert result["payload"]["status"] == "failed"


def test_multiline_answers_and_all_subitems_and_python_are_retained():
    text = """### 综合题
**1.**（10 分）(1)证明平方非负。(2)写Python。
(1) 给出完整推导。
(2) 输出零。
### 参考答案
**1.** (1) 首先取实数x。
第二行：由平方定义得到结论。

#### 第二小问说明
(2) 使用下列完整程序。
```python
def square(x):
    return x * x
```
完成验证。
"""
    paper = exam_check.parse_paper(text)
    answer = paper.answers[1]
    assert "第二行" in answer and "#### 第二小问说明" in answer
    assert "(2) 使用" in answer and "    return x * x" in answer
    assert "完成验证" in answer and "```python" in answer
    payload = exam_check.build_verification_payload(paper)
    assert "完成验证" not in payload[0]["question"]
    assert "(2) 输出零" in payload[0]["question"]


def test_integral_without_equation_exact_duplicate_and_conflict():
    stem = r"计算 $\int_0^1 x^2\,dx$，写出结果。"
    def paper(answer):
        return exam_check.parse_paper(f"### 填空题\n**1.**（5 分）{stem}\n### 参考答案\n**1.** ${answer}$")
    results = exam_check.check_cross_paper([("卷1", paper("1/3")), ("卷2", paper("1/2"))])
    assert {v.code for v in results} == {"E017", "E018"}
    assert next(v for v in results if v.code == "E017").source == "l0_exact"


def test_integral_shared_object_different_requested_quantity_is_lawful_variant():
    a = r"证明 $\int_0^1 x^2\,dx$ 有界。"
    b = r"计算 $\int_0^1 x^2\,dx$ 的值。"
    papers = [(name, exam_check.parse_paper(f"### 证明题\n**1.**（5 分）{stem}\n### 参考答案\n**1.** {answer}"))
              for name, stem, answer in [("A", a, "是"), ("B", b, "1/3")]]
    assert exam_check.check_cross_paper(papers) == []


def test_repair_preserves_untargeted_long_proof_and_all_answer_subitems():
    proof = "(1) 由导数定义推出非负。\n" + "这一完整推导必须保留，包括必要条件。" * 8 + "\n(2) 同理成立。"
    original = f"### 证明题\n**1.**（5 分）证明。\n**2.**（5 分）计算。\n### 参考答案\n**1.** {proof}\n**2.** 2"
    defects = [{"number": 2, "evidence": "第2题结果应为3"}]
    replacement = original.replace(proof, "结论成立。")
    assert any("长答案" in reason for reason in proposition_generated.repair_guard(original, replacement, defects))
    assert any("小问" in reason for reason in proposition_generated.repair_guard(original, replacement, defects))
    assert proposition_generated.repair_guard(original, original.replace("**2.** 2", "**2.** 3"), defects) == []


def test_spec_exception_is_incomplete_required_check(client, monkeypatch):
    monkeypatch.setattr(spec_check.practice_check_service, "check_practice_section",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("checker unavailable")))
    document = content("1. 执行操作。")
    document["format_spec"]["scene"] = "practical_tutorial"
    summary = spec_check.check_sections(document)
    assert summary["passed"] is False
    quality = {"checks": [{"name": "实践检查", "passed": False}],
               "issues": spec_check.quality_issues(summary), "spec_check": summary}
    result = acceptance.quality_acceptance(goal(), document, quality)
    assert result["payload"]["status"] == "needs_review"


def test_quality_new_fields_survive_response_model():
    task = context()
    task["payload"]["constraints"] = []
    result = acceptance.acceptance_report(task, acceptance.plan_checks(task), {"structure": outcome()})
    dumped = QualityReport(passed=True, status="passed", acceptance=result,
                           proposition_review={"enabled": False}).model_dump(exclude_none=True)
    assert dumped["acceptance"]["payload"]["checks"][0]["evidence_method"] == "deterministic"
    assert dumped["status"] == "passed" and dumped["proposition_review"] == {"enabled": False}


def test_real_model_boundary_gets_context_and_recheck_once(client, monkeypatch):
    captured = []
    class FakeAI:
        is_demo = False
        def __init__(self, **kwargs):
            pass
        def _real_json(self, feature, system, user, required, goal):
            captured.append((feature, json.loads(user.split("\nJSON：")[0])))
            if feature.endswith("recheck"):
                return {"defects": [], "uncertainties": []}
            return {"defects": [{"severity": "blocking", "number": 1, "evidence": "第1题结果应为3"}]}
        def _real_text(self, feature, system, user, goal):
            captured.append((feature, json.loads(user)))
            return json.loads(user)["original"].replace("**1.** 2", "**1.** 3")
    monkeypatch.setattr(proposition_generated, "AIService", FakeAI)
    task = context()
    strategy = deepcopy(FIXTURES[1])
    with SessionLocal() as db:
        proposition_knowledge.save_config(db, {"auto_evaluate_generated": True})
        document = content("### 计算题\n**1.**（5 分）求1+2。\n### 参考答案\n**1.** 2")
        result = proposition_generated.review_generated(db, document, goal(), "t", "j",
            task_context=task, strategy=strategy)
        assert result["reports"][0]["recheck_status"] == "satisfied"
    assert [feature for feature, _ in captured] == ["proposition_generated_review", "proposition_generated_repair", "proposition_generated_recheck"]
    for _, payload in captured:
        assert payload["task_context"]["payload"]["learner"] == task["payload"]["learner"]
        assert payload["generation_strategy"] == strategy


def test_applied_edit_with_unresolved_recheck_cannot_pass():
    document = content()
    report = {"enabled": True, "reports": [{"repair": "applied_once", "recheck_status": "violated",
        "defects": [{"severity": "blocking", "evidence": "证明仍有缺口"}]}]}
    result = acceptance.quality_acceptance(goal(), document,
        {"checks": [{"name": "结构", "passed": True}], "issues": [], "proposition_review": report},
        rubric=[{"id": "correctness", "required": True}])
    assert result["payload"]["status"] == "failed"
    assert result["payload"]["passed"] is False


def test_initial_model_uncertainties_are_not_a_correctness_pass():
    result = acceptance.quality_acceptance(goal(), content(), {
        "checks": [{"name": "结构", "passed": True}], "issues": [],
        "proposition_review": {"enabled": True, "reports": [{"repair": "not_needed", "defects": [],
                                                               "uncertainties": ["证明缺少边界情况证据"]}]}},
        rubric=[{"id": "correctness", "required": True}])
    assert result["payload"]["status"] == "needs_review"
    assert next(c for c in result["payload"]["checks"] if c["id"] == "correctness")["status"] == "undecidable"


@pytest.mark.parametrize("bad", [
    {"defects": "string"}, {"defects": [], "uncertainties": "string"},
    {"defects": [{"severity": "blocking", "reason": "发现问题", "evidence": ""}]},
])
def test_real_recheck_boundary_rejects_bad_contract_without_training_sample(client, bad):
    with SessionLocal() as db:
        ai = AIService(db=db)
        ai.provider = SimpleNamespace(model="fake-no-network", complete_json=lambda system, user: (bad, 0, None))
        with pytest.raises(AIServiceError):
            ai._real_json("proposition_generated_recheck", "检查", "完整成果", ("defects",), goal())
        db.commit()
        assert db.query(TrainingRecord).filter_by(feature="proposition_generated_recheck").count() == 0
        assert db.query(ModelCallLog).filter_by(feature="proposition_generated_recheck", status="error").count() == 1

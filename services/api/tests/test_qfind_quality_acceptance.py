# -*- coding: utf-8 -*-
"""S1 fixture：验收报告的复核状态与方法标签（脱敏自真实运行样本）。

「缺陷已消除 + 未做学生实测」必须得到 correctness 的 model_static satisfied，
同时 empirical 保持 not_run（不能声称已实测）；未解决缺陷不得被掩盖；
旧版报告（无分类字段）保留原判定。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.generation_acceptance import acceptance_report, plan_checks, quality_acceptance
from app.services.generation_acceptance import adapt_task_context

CONTENT = {"format_spec": {"scene": "exam_paper"},
           "sections": [{"id": "s1", "title": "线性代数期中试卷", "content": [
               {"type": "text", "body": "**1.**（4 分）题面。"}]}]}


def _goal():
    return SimpleNamespace(id="goal-1", domain_key="math_logic", title="线代试卷",
                           motivation="", application_context="", current_level="",
                           time_budget="", hard_constraints="")


def _strategy():
    """生产形态：GenerationStrategy 把 correctness 点名为必需（镜像真实试卷链路）。"""
    return {"contract_version": "generation.v1", "kind": "GenerationStrategy",
            "identity": {"task_id": "goal-1", "goal_id": "goal-1", "tutorial_id": "tut-1",
                         "version": 2, "unit_id": None},
            "payload": {"units": [{"unit_id": "s1", "knowledge_points": ["矩阵"],
                                   "question_types": ["解答题"], "boundaries": [],
                                   "difficulty_mechanisms": [], "knowledge_requests": [],
                                   "check_ids": ["correctness"]}],
                        "shared_constraints": [],
                        "source_refs": [{"kind": "user_task", "id": "goal-1", "version": "1",
                                         "hash": None, "location": "request"}]},
            "extensions": {}}


def _quality(reports: list[dict]) -> dict:
    # 镜像真实 quality_report：结构检查至少有一条确定性结论（空清单时既有语义
    # 判 structure violated——没有结构证据就不能声称结构通过，本 fixture 不改它）。
    return {"checks": [{"name": "试卷卷面账目校验", "passed": True, "detail": "校验通过"}],
            "issues": [], "proposition_review": {"enabled": True, "reports": reports}}


CLEAN_REPAIR_REPORT = {
    "id": "u:0",
    "repair": "applied_once",
    "recheck_status": "satisfied",
    "uncertainties": ["本复核为静态数学一致性检查，未进行实际考试、作答或运行验证。",
                      "未实测排版渲染、学习者适配性与考试过程，相关判断仅为静态先验。"],
    "open_uncertainties": [],
    "limitations": ["本复核为静态数学一致性检查，未进行实际考试、作答或运行验证。",
                    "未实测排版渲染、学习者适配性与考试过程，相关判断仅为静态先验。"],
}


def test_defect_cleared_with_limitations_gives_model_static_satisfied():
    """缺陷已消除 + 未做学生实测：correctness=model_static satisfied，不降级为 undecidable。"""
    result = quality_acceptance(_goal(), CONTENT, _quality([CLEAN_REPAIR_REPORT]),
                                tutorial_id="tut-1", version=2, strategy=_strategy())
    payload = result["payload"]
    by_id = {c["id"]: c for c in payload["checks"]}
    correctness = by_id["correctness"]
    assert correctness["status"] == "satisfied"
    assert correctness["evidence_method"] == "model_static"
    # 局限如实呈现：不声称已实测
    assert "未实测" in correctness["detail"] or "静态" in correctness["detail"]
    assert payload["status"] == "passed"


def test_empirical_check_stays_not_run_and_never_claims_tested():
    """量规里显式要求的 empirical 检查保持 not_run，模型静态满足不冒充实测。"""
    result = quality_acceptance(_goal(), CONTENT, _quality([CLEAN_REPAIR_REPORT]),
                                tutorial_id="tut-1", version=2, strategy=_strategy(),
                                rubric=[{"id": "student_outcome", "label": "学生真实作答实测",
                                         "method": "empirical"}])
    payload = result["payload"]
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["correctness"]["status"] == "satisfied"
    assert by_id["correctness"]["evidence_method"] == "model_static"
    assert by_id["student_outcome"]["status"] == "not_run"
    assert by_id["student_outcome"]["evidence_method"] is None
    # 必需的 empirical 未运行 → 诚实显示待复核，而不是 passed
    assert payload["status"] == "needs_review"
    assert any("学生真实作答实测" in u for u in payload["uncertainties"])


def test_unresolved_defects_are_not_masked():
    """保护器拒绝修复 + 缺陷仍在：correctness violated，报告不转绿。"""
    report = {
        "id": "u:1",
        "repair": "rejected_by_guard",
        "defects": [
            {"severity": "blocking",
             "reason": "第5题参考答案第二空错误：AP^-1 对应的列变换描述与矩阵乘法不符。",
             "evidence": "P^-1=[[1,0,0],[0,1,0],[-2,0,1]]。对任意 A，AP^-1 的第一列为 C1-2C3。"},
            {"severity": "blocking",
             "reason": "第9题(2)中 AQ 的列变换描述错误，答案与设问不匹配。",
             "evidence": "Q 的第二列为 (0,1,2)^T，故 AQ 的第二列为 C2+2C3。"},
        ],
        "uncertainties": ["未进行学生实测或统计，无法验证难度判断是否成立；该判断仅为先验。"],
        "open_uncertainties": [],
        "limitations": ["未进行学生实测或统计，无法验证难度判断是否成立；该判断仅为先验。"],
    }
    result = quality_acceptance(_goal(), CONTENT, _quality([report]),
                                tutorial_id="tut-1", version=2, strategy=_strategy())
    payload = result["payload"]
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["correctness"]["status"] == "violated"
    assert payload["status"] == "failed"


def test_legacy_report_without_classification_keeps_original_verdict():
    """旧报告没有 open_uncertainties 字段：uncertainties 非空仍判 undecidable（保留原判定）。"""
    legacy = {"id": "u:0", "repair": "not_needed", "defects": [],
              "uncertainties": ["未执行实际作答、运行或统计核验，仅作静态题面与答案审查。"]}
    result = quality_acceptance(_goal(), CONTENT, _quality([legacy]),
                                tutorial_id="tut-1", version=2, strategy=_strategy())
    payload = result["payload"]
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["correctness"]["status"] == "undecidable"
    assert payload["status"] == "needs_review"


def test_acceptance_report_rejects_empirical_label_without_refs():
    """satisfied 的 empirical 检查缺可定位证据时必须降为 undecidable（不能凭空声称实测）。"""
    context = adapt_task_context(_goal(), CONTENT, tutorial_id="tut-1", version=2)
    plan = plan_checks(context, rubric=[{"id": "student_outcome", "label": "学生真实作答实测",
                                         "method": "empirical"}])
    report = acceptance_report(context, plan, {"student_outcome": {
        "status": "satisfied", "evidence_method": "empirical", "evidence_refs": [],
        "detail": "模型自评认为学生实测通过。"}})
    by_id = {c["id"]: c for c in report["payload"]["checks"]}
    assert by_id["student_outcome"]["status"] == "undecidable"

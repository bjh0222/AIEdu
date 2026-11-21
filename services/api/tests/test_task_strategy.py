"""板块一：GenerationStrategy 调度定向测试。

覆盖任务书测试计划第三组条目：策略不能越明确范围或改交付/题量、单元只获自身
片段、学习风格而非逐题复制；并与 plan_checks 的必需语义对齐（回归防护）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.generation_contract import validate_generation_exchange
from app.services.task_strategy import (
    attach_unit_fragments,
    build_generation_strategy,
    unit_fragment,
)
from app.services.task_understanding import build_task_context


class _EmptyDB:
    def query(self, *args, **kwargs):
        class _Q:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return []

        return _Q()


def _goal(**overrides) -> SimpleNamespace:
    base = dict(
        id="g-strat", title="出两套线性代数模拟卷", domain_key="math_logic", branch="calculus",
        motivation="", application_context="按参考风格出卷", current_level="本科生", time_budget="",
        hard_constraints="只输出两套试卷", user_notes="",
        format_spec_snapshot=json.dumps({"scene": "exam_paper", "name": "试卷生成规范"}),
        format_spec_source="manual", format_spec_match_note="",
        difficulty_mode="", difficulty_note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_OUTLINE = [
    {"id": "unit-1", "title": "线性代数期中模拟试卷（第一套）"},
    {"id": "unit-2", "title": "线性代数期中模拟试卷（第二套）"},
]


def _context(**goal_overrides):
    return build_task_context(_EmptyDB(), _goal(**goal_overrides), outline=_OUTLINE)


# ---------------------------------------------------------------- 对齐与校验

def test_strategy_units_match_deliverables_and_exchange_validates():
    context = _context()
    strategy = build_generation_strategy(context, _OUTLINE, None)
    validate_generation_exchange(context, strategy)
    assert [u["unit_id"] for u in strategy["payload"]["units"]] == ["unit-1", "unit-2"]


def test_strategy_never_changes_deliverables_or_scope():
    context = _context(hard_constraints="只输出两套试卷\n不要考特征值")
    strategy = build_generation_strategy(context, _OUTLINE, None)
    # 共同约束逐条引用用户硬约束，不增不删
    assert strategy["payload"]["shared_constraints"] == ["hard-1", "hard-2"]
    # 单元边界携带排除项，不越明确范围
    for unit in strategy["payload"]["units"]:
        assert any("特征值" in b for b in unit["boundaries"])
        assert any("不写其它单元内容" in b for b in unit["boundaries"])
    # 交付物保持两套卷：策略不新增/合并单元
    assert len(strategy["payload"]["units"]) == 2


def test_strategy_check_ids_reference_only_constraint_checks():
    context = _context()
    strategy = build_generation_strategy(context, _OUTLINE, None)
    for unit in strategy["payload"]["units"]:
        assert unit["check_ids"] == ["constraint-hard-1"]
    # 与验收计划对齐：硬约束检查保持必需
    from app.services.generation_acceptance import DELIVERY_RUBRIC_VERSION, plan_checks

    plan = plan_checks(context, strategy=strategy)
    by_id = {p["id"]: p for p in plan}
    assert by_id["constraint-hard-1"]["required"] is True
    # correctness.required=True 来自**量规 v1 默认**（01 任务书 S2 政策：试卷基本交付
    # correctness 必需），不是策略升格——策略 check_ids 仍只含 constraint-*，
    # 「量规判级不被策略升为必需」的原意图不变（升级来源见 rubric_version 标注）。
    assert by_id["correctness"]["required"] is True
    assert by_id["correctness"]["rubric_version"] == DELIVERY_RUBRIC_VERSION
    assert by_id["correctness"]["source_refs"][0]["location"].endswith(":static_task_rubric")


def test_exam_correctness_pending_when_review_off():
    """关模型/旧快照兼容：correctness 必需但未运行 → needs_review（草稿/正确性待复核），
    不暗开模型，也不因选配化而静默通过。"""
    from app.services.generation_acceptance import acceptance_report, plan_checks

    task = _context()
    task["payload"]["constraints"] = []
    plan = plan_checks(task)
    result = acceptance_report(task, plan, {"structure": {
        "status": "satisfied", "evidence_method": "deterministic", "detail": "结构完整",
        "evidence_refs": []}})
    check = next(c for c in result["payload"]["checks"] if c["id"] == "correctness")
    assert check["status"] == "not_run" and check["required"] is True
    assert result["payload"]["status"] == "needs_review"
    assert result["payload"]["passed"] is False


def test_blueprint_fields_flow_into_units_without_being_invented():
    context = _context()
    blueprint = {"sections": [
        {"section_id": "unit-1", "knowledge_points": ["行列式的展开"], "question_types": ["选择题"],
         "difficulty": "中高"},
        {"section_id": "unit-2", "knowledge_points": ["秩与解的结构"], "question_types": ["证明题"]},
    ]}
    strategy = build_generation_strategy(context, _OUTLINE, blueprint)
    units = {u["unit_id"]: u for u in strategy["payload"]["units"]}
    assert units["unit-1"]["knowledge_points"] == ["行列式的展开"]
    assert units["unit-2"]["question_types"] == ["证明题"]
    assert not any(u["knowledge_points"] == [] and u["question_types"] == []
                   for u in units.values())


def test_difficulty_mechanisms_follow_mode_and_vary_per_unit():
    context = _context()  # 有 manual 规范、无参考材料 → explicit? 无参考 + 无偏好 → unspecified
    strategy = build_generation_strategy(context, _OUTLINE, None)
    mode = context["payload"]["difficulty_target"]["mode"]
    for unit in strategy["payload"]["units"]:
        assert unit["difficulty_mechanisms"], unit
        assert all(m["evidence_basis"] == "prior" for m in unit["difficulty_mechanisms"])
    if mode == "above_reference":
        assert any("综合度" in m["mechanism"] for u in strategy["payload"]["units"]
                   for m in u["difficulty_mechanisms"])
    # 基础模式 → 基础达标机制
    context_basic = _context(difficulty_mode="basic")
    strategy_basic = build_generation_strategy(context_basic, _OUTLINE, None)
    assert any("基础达标" in m["expected_effect"] or "铺垫" in m["mechanism"]
               for u in strategy_basic["payload"]["units"] for m in u["difficulty_mechanisms"])


# ---------------------------------------------------------------- 片段隔离

def test_unit_fragments_are_isolated_per_section():
    context = _context()
    blueprint = {"sections": [
        {"section_id": "unit-1", "knowledge_points": ["甲知识点"], "question_types": ["选择题"]},
        {"section_id": "unit-2", "knowledge_points": ["乙知识点"], "question_types": ["证明题"]},
    ]}
    strategy = build_generation_strategy(context, _OUTLINE, blueprint)
    attached = attach_unit_fragments(_OUTLINE, strategy)
    frag1 = attached[0]["_strategy_unit"]
    frag2 = attached[1]["_strategy_unit"]
    assert frag1["knowledge_points"] == ["甲知识点"]
    assert frag2["knowledge_points"] == ["乙知识点"]
    # 自身片段不含其它单元的细粒度知识点
    assert "乙知识点" not in json.dumps(frag1, ensure_ascii=False)
    assert "甲知识点" not in json.dumps(frag2, ensure_ascii=False)
    # 完整策略不随片段下发
    assert "_generation_strategy" not in attached[0]
    assert unit_fragment(strategy, "unit-1")["unit_id"] == "unit-1"
    assert unit_fragment(strategy, "missing") is None


def test_style_policy_forbids_per_question_copying():
    context = _context()
    strategy = build_generation_strategy(context, _OUTLINE, None)
    assert "不逐题一比一模仿" in strategy["extensions"]["style_policy"]
    for unit in strategy["payload"]["units"]:
        assert any("不逐题复述参考原题" in b for b in unit["boundaries"])
        assert any("同卷同次生成" in b for b in unit["boundaries"])

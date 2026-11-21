"""02 切片 4：独立交付物规划校验（validate_strategy / sanitize_artifact_plan）。

钉死契约 §4 与任务书 02-4：
- exam 默认单位关系 independent；「针对第一套暴露的错因」类无来源反馈依赖 = error，
  且 sanitize 自动移除并逐条记录（不能只写「不越界」一句话）；
- 套数：用户显式 N 套 vs 试卷类交付物数不符 = error（答案/解析单元不计套数）；
- 范围：知识点命中 scope.exclude = error；basis=direct 时范围外知识点 = 降级移除；
- build_generation_strategy 内部调用校验并把结果写 extensions.validation；
- demo 合成产物在 artifact 模式同样不虚构先修/反馈依赖。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.generation_contract import validate_generation_exchange
from app.services.synthesis import _demo_synthesis
from app.services.task_strategy import (
    build_generation_strategy,
    sanitize_artifact_plan,
    validate_strategy,
)
from app.services.task_understanding import build_task_context
from tests.test_task_strategy import _OUTLINE, _context, _goal  # noqa: F401  复用同型替身


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


def _context(**goal_overrides):
    return build_task_context(_EmptyDB(), _goal(**goal_overrides), outline=_OUTLINE)


_FABRICATED_SYNTHESIS = {
    "sections": [
        {"section_id": "unit-1", "dependencies": [
            {"dependency_id": "unit-2", "relation_type": "prerequisite_of",
             "rationale": "先完成第二套再回收第一套，按难度递进安排。"},
        ], "global_position": "第一套覆盖行列式与秩。"},
        {"section_id": "unit-2", "dependencies": [
            {"dependency_id": "unit-1", "relation_type": "prerequisite_of",
             "rationale": "第二套针对第一套暴露的错因安排变式迁移。"},
        ], "global_position": "第二套针对第一套暴露的错因进行拔高。"},
    ],
    "global_structure": [],
}

_FABRICATED_BLUEPRINT = {
    "sections": [
        {"section_id": "unit-1", "bridges": [], "practice_focus": "基础自测"},
        {"section_id": "unit-2", "bridges": ["承接第一套暴露的薄弱点"],
         "practice_focus": "针对第一套作答情况安排变式训练"},
    ],
}


# ---------------------------------------------------------------- 反馈依赖

def test_validate_strategy_flags_fabricated_feedback_dependencies():
    context = _context()
    report = validate_strategy(
        context, None, synthesis_data=_FABRICATED_SYNTHESIS,
        blueprint_data=_FABRICATED_BLUEPRINT)
    assert report["ok"] is False
    assert any("尚不存在的作答反馈" in e for e in report["errors"])
    assert any("prerequisite_of" in d for d in report["degradations"])
    evidence = json.dumps(report["checks"], ensure_ascii=False)
    assert "feedback-dependency" in evidence
    # 错误逐条带证据（可定位到依赖出处与原文）
    assert any("unit-1" in e and "错因" in e for e in report["errors"])


def test_sanitize_removes_feedback_dependencies_and_records_changes():
    context = _context()
    synthesis, blueprint, changes = sanitize_artifact_plan(
        context, _FABRICATED_SYNTHESIS, _FABRICATED_BLUEPRINT)
    row2 = next(r for r in synthesis["sections"] if r["section_id"] == "unit-2")
    assert row2["dependencies"] == []
    assert "错因" not in row2["global_position"]
    bp2 = next(r for r in blueprint["sections"] if r["section_id"] == "unit-2")
    assert bp2["bridges"] == []
    assert "作答情况" not in bp2["practice_focus"]
    assert any("移除无来源反馈依赖" in c for c in changes)
    assert any("先修依赖" in c for c in changes)
    assert synthesis["extensions"]["dependency_sanitizer"] == changes
    # 输入对象不被修改（deepcopy）
    assert _FABRICATED_SYNTHESIS["sections"][1]["dependencies"]


def test_sanitize_keeps_structural_answer_dependency_and_learning_mode():
    context = _context()
    synthesis = {"sections": [
        {"section_id": "unit-3", "dependencies": [
            {"dependency_id": "unit-1", "relation_type": "prerequisite_of",
             "rationale": "参考答案跟随第一套试卷的题号与题面。"},
        ], "global_position": "集中给答案。"},
    ]}
    _syn, _bp, changes = sanitize_artifact_plan(context, synthesis, None)
    # 试卷→答案的结构性先修不是反馈依赖，保留
    assert changes == []
    row = _syn["sections"][0]
    assert row["dependencies"] and row["dependencies"][0]["dependency_id"] == "unit-1"
    # learning 任务原样返回
    learning_context = build_task_context(
        _EmptyDB(), _goal(title="理解线性代数的秩", hard_constraints="",
                          application_context="", motivation="",
                          format_spec_snapshot="", format_spec_source=""),
        outline=[{"id": "u1", "title": "秩的概念"}])
    data = {"sections": [{"section_id": "u1", "dependencies": [
        {"dependency_id": "x", "relation_type": "prerequisite_of", "rationale": "错因"}]}]}
    syn2, bp2, changes2 = sanitize_artifact_plan(learning_context, data, None)
    assert changes2 == [] and syn2["sections"][0]["dependencies"]


# ---------------------------------------------------------------- 套数

def test_paper_count_mismatch_is_an_error_not_a_silent_fix():
    context = _context(hard_constraints="只输出两套试卷")  # 交付物两套 → 通过
    report = validate_strategy(context, None)
    assert report["ok"] is True
    assert any(c["id"] == "paper-count" and c["status"] == "passed" for c in report["checks"])
    one_paper_context = build_task_context(
        _EmptyDB(),
        _goal(hard_constraints="只输出两套试卷"),
        outline=[{"id": "unit-1", "title": "线性代数期中模拟试卷（第一套）"}])
    report2 = validate_strategy(one_paper_context, None)
    assert report2["ok"] is False
    assert any("2 套" in e and "1 份" in e for e in report2["errors"])
    # 答案单元不计套数：两卷 + 一份答案 = 2 套，通过
    with_answer = build_task_context(
        _EmptyDB(),
        _goal(hard_constraints="只输出两套试卷"),
        outline=[{"id": "unit-1", "title": "第一套试卷"},
                 {"id": "unit-2", "title": "第二套试卷"},
                 {"id": "unit-3", "title": "参考答案"}])
    report3 = validate_strategy(with_answer, None)
    assert report3["ok"] is True


# ---------------------------------------------------------------- 范围

def test_out_of_scope_points_degrade_and_excluded_points_error():
    context = _context(hard_constraints="只输出两套试卷\n不要考特征值")
    blueprint = {"sections": [
        {"section_id": "unit-1", "knowledge_points": ["行列式的展开", "特征值分解"]},
        {"section_id": "unit-2", "knowledge_points": ["秩与解的结构"]},
    ]}
    strategy = build_generation_strategy(context, _OUTLINE, blueprint)
    validation = strategy["extensions"]["validation"]
    assert validation["ok"] is False
    assert any("特征值分解" in e and "排除" in e for e in validation["errors"])
    units = {u["unit_id"]: u for u in strategy["payload"]["units"]}
    assert units["unit-1"]["knowledge_points"] == ["行列式的展开"]
    assert any(c["id"] == "points-scope-alignment" for c in validation["checks"])
    validate_generation_exchange(context, strategy)


# ---------------------------------------------------------------- 集成与 demo

def test_build_strategy_attaches_validation_and_stays_wire_valid():
    context = _context()
    strategy = build_generation_strategy(context, _OUTLINE, None)
    validation = strategy["extensions"]["validation"]
    assert validation["ok"] is True
    assert {c["id"] for c in validation["checks"]} >= {"paper-count", "feedback-dependency"}
    validate_generation_exchange(context, strategy)


def _demo_ai():
    class _DemoAI:
        is_demo = True

        def _demo(self, feature, data, goal):
            return data

    return _DemoAI()


def test_demo_synthesis_for_artifact_has_no_fabricated_dependencies():
    sections = [{"id": "unit-1", "title": "第一套试卷"},
                {"id": "unit-2", "title": "第二套试卷"}]
    context = _context()
    demo = _demo_synthesis(sections, context)
    assert all(not row["dependencies"] for row in demo["sections"])
    assert all("独立交付物" in row["global_position"] for row in demo["sections"])
    # learning 模式 demo 保持原有顺序先修派生
    learning = build_task_context(
        _EmptyDB(), _goal(title="理解线性代数的秩", hard_constraints="",
                          application_context="", motivation="",
                          format_spec_snapshot="", format_spec_source=""),
        outline=[{"id": "u1", "title": "秩"}])
    demo_learning = _demo_synthesis(sections, learning)
    assert demo_learning["sections"][1]["dependencies"]

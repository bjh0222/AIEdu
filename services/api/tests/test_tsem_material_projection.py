"""02 切片 7：材料按用途投影（project_for_stage）+ 规划统计摘要。

钉死任务书 02-7：
- 规划拿完整可信统计 + 必要片段（长材料只取统计+首题，不冒充全文）；
- 章节拿自身知识点相关完整片段 + 跨卷共同分布摘要 + 来源边界；无关片段不借入；
- 小材料全量、长材料按预算选完整题目片段；超预算显式报告 dropped；
- 疑点/来源边界等硬信息不参与裁剪；
- reading 原有流程不经过新切片器（本函数只被非 reading 路径的装配补丁调用）。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.llm import prompts
from app.services.reference_documents import parse_reference_documents, project_for_stage
from tests.test_tsem_reference_documents import _three_volume_fixture
from tests.test_tsem_task_context_extensions import _MaterialDB, _material
from app.services.task_understanding import build_task_context

_SOURCE_REF = {"kind": "material", "id": "m1", "version": None,
               "hash": "0" * 64, "location": "filename:three-volume.md"}


def _parsed_and_bodies():
    text = _three_volume_fixture()
    parsed = parse_reference_documents(text, dict(_SOURCE_REF))
    # 与 reference_source_texts 同口径：id 与 filename 都可作为定位键
    return parsed, {
        str(_SOURCE_REF["id"]): text,
        str(_SOURCE_REF["location"]).removeprefix("filename:"): text,
    }


def test_planning_projection_has_full_stats_and_honest_fragments():
    parsed, bodies = _parsed_and_bodies()
    projected = project_for_stage(parsed, bodies, stage="planning", budget_chars=600)
    assert "参考材料统计" in projected["text"]
    assert "8 题" in projected["distribution_summary"]
    assert "9 题" in projected["distribution_summary"]
    assert "未提供题型划分" in projected["distribution_summary"]  # 第二册保持 unknown
    assert "来源边界" in projected["text"]
    assert "考后回忆版" in projected["text"]  # 疑点不参与裁剪
    # 长材料超预算：显式报告，不冒充全文
    assert projected["basis"] == "fragments"
    assert projected["dropped"] and "超出规划预算" in projected["dropped"][0]


def test_planning_projection_small_material_is_full():
    body = "### 选择题\n\n**1.** 题\n\n**2.** 题\n"
    parsed = parse_reference_documents(body, dict(_SOURCE_REF))
    projected = project_for_stage(parsed, {"m1": body}, stage="planning", budget_chars=5000)
    assert projected["basis"] == "full"
    assert "题" in projected["text"]
    assert projected["dropped"] == []


def test_unit_projection_selects_relevant_complete_questions():
    parsed, bodies = _parsed_and_bodies()
    projected = project_for_stage(
        parsed, bodies, stage="unit", unit_points=["协方差矩阵"], budget_chars=12000)
    text = projected["text"]
    assert "参考分布摘要" in text and "8 题" in text
    assert "来源边界" in text
    # 相关片段是完整题目（题号行起），不是任意截断
    assert "**6.**" in text
    # 无关题目不借入：第三册证明题的「非零常数 c」题不含协方差，不应整段出现
    assert "存在非零常数" not in text


def test_unit_projection_reports_budget_drops():
    parsed, bodies = _parsed_and_bodies()
    projected = project_for_stage(
        parsed, bodies, stage="unit", unit_points=["矩阵"], budget_chars=400)
    # 预算极小（固定统计后所剩无几）：相关片段装不下 → 显式 dropped，不静默截断半个题
    assert projected["dropped"], "超预算必须显式报告"
    assert any("超预算" in d or "未注入" in d for d in projected["dropped"])


def test_unit_projection_without_match_keeps_boundary_only():
    parsed, bodies = _parsed_and_bodies()
    projected = project_for_stage(
        parsed, bodies, stage="unit", unit_points=["傅里叶变换"], budget_chars=12000)
    assert "参考分布摘要" in projected["text"]
    assert "无与本单元知识点相关的片段" in projected["dropped"][0]
    # 不借入任何题目正文
    assert "—— " not in projected["text"].split("来源边界")[1]


def test_reading_and_spec_bodies_untouched():
    """投影不改用户已保存规范正文与材料原文：纯读函数。"""
    parsed, bodies = _parsed_and_bodies()
    original = dict(bodies)
    project_for_stage(parsed, bodies, stage="unit", unit_points=["秩"], budget_chars=12000)
    assert bodies == original


def test_task_context_block_carries_collection_stats():
    db = _MaterialDB([_material(1, _three_volume_fixture())])
    goal = SimpleNamespace(
        id="g-test", title="出三套试卷", domain_key="math_logic", branch="线性代数",
        motivation="期中", application_context="期中模拟", current_level="基础扎实",
        time_budget="", hard_constraints="不要安排任何日程", user_notes="",
        format_spec_snapshot="", format_spec_source="", format_spec_match_note="",
        difficulty_mode="", difficulty_note="")
    context = build_task_context(db, goal, outline=[{"id": "u1", "title": "第一套试卷"}])
    block = prompts._task_context_block(context)
    assert "参考合辑：3 份逻辑卷" in block
    assert "8/8/9" in block
    assert "合辑总量口径，非单卷题量" in block

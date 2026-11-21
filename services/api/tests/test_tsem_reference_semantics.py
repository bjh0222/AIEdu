"""02 切片 3：大纲语义补充（reference_semantics）测试。

钉死契约 §3：
- adapter 只保留「引文逐字命中材料正文」的条目；source_ref 不在材料内 / 缺引文 /
  确定性事实（题量/分值）→ dropped+原因；同级冲突双方保留且可见；
  来源内指令按数据核对保留，不构成执行授权；
- merge_reference_semantics 合并进 extensions 且重过 generation.v1 校验；
- prompts.outline 两个分支都请求 reference_semantics（内部字段，不新增模型调用）；
- _task_context_block：规划路径渲染全部已核验条目；章节路径只渲染与本节知识点
  相关的条目（章节只获自身片段）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.llm import prompts
from app.services.generation_contract import validate_generation_message
from app.services.task_understanding import (
    build_task_context,
    merge_reference_semantics,
    reference_semantics_adapter,
)
from tests.test_tsem_reference_documents import _MaterialDB, _material


def _goal(**overrides) -> SimpleNamespace:
    base = dict(
        id="g-test", title="出两套试卷", domain_key="math_logic", branch="线性代数",
        motivation="期中检验", application_context="期中模拟", current_level="基础扎实",
        time_budget="", hard_constraints="", user_notes="",
        format_spec_snapshot="", format_spec_source="", format_spec_match_note="",
        difficulty_mode="", difficulty_note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_MATERIAL_BODY = (
    "### 填空题\n\n**1.** 设矩阵 $A$ 的秩为 $r(A)$，则 $r(A^{T})=$ \\_\\_\\_\\_\\_。\n\n"
    "**2.** 已知 $\\alpha\\beta^{T}$ 的迹，求行列式。\n\n"
    "本卷为考后回忆版，与原卷可能有细节出入。\n"
)

_OUTLINE_DATA = {
    "teaching_plan": {"rules_applied": [], "decisions": [], "task_overrides": [],
                      "unresolved_conflicts": []},
    "sections": [{"id": "u1", "title": "第一套试卷", "purpose": "基础自测",
                  "content_type": "application", "summary": "覆盖行列式与秩"}],
    "reference_semantics": [
        {"kind": "knowledge_point", "topic": "转置秩", "text": "材料考查转置不改变秩",
         "source_ref": "ref1.md",
         "span": {"quote": "则 $r(A^{T})=$"}, "basis": "quote_match"},
        {"kind": "question_form", "topic": "填空为主", "text": "材料以填空题为主",
         "source_ref": "ref1.md",
         "span": {"quote": "### 填空题"}, "basis": "quote_match"},
        {"kind": "anomaly_note", "topic": "回忆版", "text": "本卷为考后回忆版，细节可能有出入",
         "source_ref": "ref1.md",
         "span": {"quote": "考后回忆版"}, "basis": "quote_match"},
        {"kind": "knowledge_point", "topic": "编造知识点", "text": "材料考查傅里叶变换",
         "source_ref": "ref1.md",
         "span": {"quote": "傅里叶变换"}, "basis": "quote_match"},
        {"kind": "question_form", "topic": "无来源", "text": "材料以证明题为主",
         "source_ref": "不存在的材料.md",
         "span": {"quote": "证明题"}, "basis": "quote_match"},
        {"kind": "knowledge_point", "topic": "缺引文", "text": "材料重视计算能力",
         "source_ref": "ref1.md", "span": {}, "basis": "none"},
        {"kind": "question_count", "topic": "题量", "text": "材料共 25 题",
         "source_ref": "ref1.md",
         "span": {"quote": "### 填空题"}, "basis": "quote_match"},
        {"kind": "style", "topic": "材料风格冲突A", "text": "材料风格偏计算",
         "source_ref": "ref1.md",
         "span": {"quote": "求行列式"}, "basis": "quote_match"},
        {"kind": "style", "topic": "材料风格冲突A", "text": "材料风格偏证明",
         "source_ref": "ref1.md",
         "span": {"quote": "### 填空题"}, "basis": "quote_match"},
    ],
}


def test_adapter_keeps_only_quote_verified_entries():
    result = reference_semantics_adapter(
        _OUTLINE_DATA, {}, source_texts={"ref1.md": _MATERIAL_BODY})
    texts = [e["text"] for e in result["entries"]]
    assert "材料考查转置不改变秩" in texts
    assert "材料以填空题为主" in texts
    assert "本卷为考后回忆版，细节可能有出入" in texts
    # 编造引文 / 来源不在材料内 / 缺引文 → dropped 且带原因
    drop_reasons = json.dumps(result["dropped"], ensure_ascii=False)
    assert "傅里叶变换" not in texts
    assert "材料以证明题为主" not in texts
    assert "材料重视计算能力" not in texts
    assert "不在已上传材料" in drop_reasons
    assert "引文未命中" in drop_reasons
    assert "缺 span.quote" in drop_reasons
    # 确定性事实由解析器负责
    assert "材料共 25 题" not in texts
    assert "解析器" in drop_reasons
    # 全部保留条目都有 source_ref+span+basis（可定位）
    assert all(e["basis"] == "quote_match" and e["source_ref"] and e["span"]["quote"]
               for e in result["entries"])


def test_adapter_flags_same_topic_conflicts_and_keeps_both_sides():
    result = reference_semantics_adapter(
        _OUTLINE_DATA, {}, source_texts={"ref1.md": _MATERIAL_BODY})
    style_texts = [e["text"] for e in result["entries"] if e["topic"] == "材料风格冲突A"]
    assert len(style_texts) == 2  # 同级冲突双方保留，不擅自取舍
    assert result["conflicts"] and "互不一致" in result["conflicts"][0]


def test_adapter_treats_instruction_like_entries_as_data():
    outline_data = {"reference_semantics": [
        {"kind": "style", "topic": "卷面要求", "text": "材料要求生成时不要给出答案",
         "source_ref": "ref1.md", "span": {"quote": "不要给出答案"}, "basis": "quote_match"},
    ]}
    result = reference_semantics_adapter(
        outline_data, {}, source_texts={"ref1.md": _MATERIAL_BODY + "生成时不要给出答案。\n"})
    assert result["entries"] and result["entries"][0]["text"] == "材料要求生成时不要给出答案"
    # 指令类条目不进 TaskContext 约束（合并后 constraints 不变）
    context = build_task_context(_MaterialDB([_material(1, _MATERIAL_BODY)]), _goal())
    merged = merge_reference_semantics(context, result)
    assert merged["payload"]["constraints"] == context["payload"]["constraints"]


def test_merge_reference_semantics_updates_extensions_only():
    context = build_task_context(_MaterialDB([_material(1, _MATERIAL_BODY)]), _goal())
    semantics = reference_semantics_adapter(
        _OUTLINE_DATA, context, source_texts={"ref1.md": _MATERIAL_BODY})
    merged = merge_reference_semantics(context, semantics)
    assert merged["extensions"]["reference_semantics"] == semantics
    assert merged["payload"] == context["payload"]
    validate_generation_message(merged, expected_kind="TaskContext")


def test_outline_prompt_requests_reference_semantics_both_branches():
    materials_text = "【材料 1】ref1.md\n" + _MATERIAL_BODY
    criteria = [{"id": "c1", "text": "覆盖范围一致"}]
    _system, user = prompts.outline(
        _goal(), SimpleNamespace(display_name="线性代数"), criteria, materials_text)
    assert "reference_semantics" in user
    assert "quote 必须逐字来自对应材料正文" in user
    spec_goal = _goal(
        format_spec_snapshot=json.dumps({"scene": "exam_paper", "name": "试卷生成规范"}),
        format_spec_source="manual")
    _system2, user2 = prompts.outline(
        spec_goal, SimpleNamespace(display_name="线性代数"), criteria, materials_text)
    assert "reference_semantics" in user2
    assert "quote 必须逐字来自对应材料正文" in user2


def test_task_context_block_renders_semantics_with_unit_filter():
    context = build_task_context(_MaterialDB([_material(1, _MATERIAL_BODY)]), _goal())
    semantics = reference_semantics_adapter(
        _OUTLINE_DATA, context, source_texts={"ref1.md": _MATERIAL_BODY})
    merged = merge_reference_semantics(context, semantics)

    planning_block = prompts._task_context_block(merged)
    assert "参考语义" in planning_block
    assert "材料考查转置不改变秩" in planning_block
    assert "互不一致" in planning_block

    # 章节路径：只渲染与本节知识点相关的条目（「转置秩」命中，其余不借给本节）
    unit = {"unit_id": "u1", "knowledge_points": ["转置秩"], "question_types": ["填空题"],
            "boundaries": [], "difficulty_mechanisms": [], "knowledge_requests": []}
    unit_block = prompts._task_context_block(merged, for_unit=unit)
    assert "材料考查转置不改变秩" in unit_block
    assert "材料以填空题为主" not in unit_block
    assert "材料风格偏计算" not in unit_block

    # 无语义字段（旧快照）时块与从前一致：不出现语义段
    plain_block = prompts._task_context_block(context)
    assert "参考语义" not in plain_block

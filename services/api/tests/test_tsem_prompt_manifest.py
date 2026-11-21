"""02 切片 6：PromptBuild / PromptManifest（正向装配清单）测试。

钉死契约 §5：
- 四类块（task / content_evidence / format / check）经 build.add() 注册；
  build=None 时与旧路径**逐字节一致**（不做等价重写）；
- PromptBlock 携带 block_id/authority/owner/source_hash/source_version/unit_id/
  char_count/token_estimate；PromptManifest.hash 可复算、块顺序=拼接顺序；
- 章节块全部带本节 unit_id（章节只含自身策略的可核对依据）；
- 同一约束一个拥有者渲染：用户硬约束全文在 user 里恰好一次；
- manifest 覆盖整份 user prompt（Σchar_count == len(user)），prompt_explain 可直读；
- token_estimate 与 ModelCallLog 估算同口径。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.llm import context_layers, prompts
from app.llm.context_layers import PromptBuild, token_estimate


def _goal(**overrides) -> SimpleNamespace:
    base = dict(
        id="g-manifest", title="出两套线性代数模拟卷", domain_key="math_logic",
        branch="线性代数", motivation="期中自测", application_context="按参考风格出卷",
        current_level="本科生", time_budget="", hard_constraints="只输出两套试卷\n不要安排任何日程",
        user_notes="", format_spec_snapshot="", format_spec_source="",
        format_spec_match_note="", difficulty_mode="", difficulty_note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


_DISCIPLINE = SimpleNamespace(display_name="线性代数", assessment_rubrics=[])
_CRITERIA = [{"id": "c1", "statement": "能够独立完成秩的计算"}]
_MATERIALS = "【材料 1】ref.md\n矩阵的秩不因转置改变。\n"


def test_build_none_is_byte_identical_to_legacy():
    """build=None 与带 build 的拼接结果逐字节一致（对调优系统的红线）。"""
    goal = _goal()
    outline_items = [{"id": "u1", "title": "第一套试卷", "purpose": "", "content_type": "application",
                      "summary": "", "criteria_ids": []}]
    legacy_system, legacy_user = prompts.outline(goal, _DISCIPLINE, _CRITERIA, _MATERIALS)
    build = PromptBuild(stage="outline", call_site="prompts.outline")
    built_system, built_user = prompts.outline(goal, _DISCIPLINE, _CRITERIA, _MATERIALS, build=build)
    assert legacy_system == built_system
    assert legacy_user == built_user
    assert "".join(b.text for b in build.blocks) == built_user

    item = {"id": "u1", "title": "第一套试卷"}
    legacy_section = prompts.section(goal, _DISCIPLINE, item, _CRITERIA, "[]", _MATERIALS)[1]
    build2 = PromptBuild(stage="section", call_site="prompts.section")
    built_section = prompts.section(goal, _DISCIPLINE, item, _CRITERIA, "[]", _MATERIALS,
                                    build=build2)[1]
    assert legacy_section == built_section


def test_manifest_blocks_carry_contract_fields_and_verifiable_hash():
    goal = _goal()
    build = PromptBuild(stage="outline", call_site="prompts.outline")
    _system, _user = prompts.outline(goal, _DISCIPLINE, _CRITERIA, _MATERIALS, build=build)
    manifest = build.finish()
    assert manifest.stage == "outline" and manifest.call_site == "prompts.outline"
    ids = [b.block_id for b in manifest.blocks]
    # 四类拥有者齐备：任务 / 内容证据 / 格式 / 检查在 outline 至少出现前三类
    owners = {b.owner for b in manifest.blocks}
    assert {"task", "content_evidence", "format"} <= owners
    # 关键块在场且字段完整
    materials = next(b for b in manifest.blocks if b.block_id == "materials")
    assert materials.layer == "materials" and materials.authority == "source"
    assert materials.source_hash == context_layers.content_hash(_MATERIALS)
    assert materials.owner == "content_evidence"
    assert materials.char_count == len(materials.text) > 0
    assert materials.token_estimate >= 1
    spec = next(b for b in manifest.blocks if b.block_id == "format-spec")
    assert spec.layer == "format_spec" and spec.owner == "format"
    hard = next(b for b in manifest.blocks if b.block_id == "hard-constraints")
    assert hard.layer == "hard_constraints" and hard.authority == "source"
    assert hard.source_hash == context_layers.content_hash(goal.hard_constraints)
    # hash 可复算；块顺序 = 拼接顺序
    assert manifest.manifest_hash == manifest.compute_hash()
    assert manifest.manifest_hash == build.finish().manifest_hash
    payload = manifest.to_dict()
    assert payload["manifest_hash"] and payload["block_count"] == len(manifest.blocks)
    assert payload["blocks"][0]["block_id"] == ids[0]


def test_section_blocks_carry_unit_id_and_check_owner():
    goal = _goal()
    item = {
        "id": "u1", "title": "第一套试卷",
        "_task_context": {"payload": {"task_type": "exam"}, "extensions": {}},
        "_strategy_unit": {"unit_id": "u1", "knowledge_points": ["秩"], "question_types": ["填空题"],
                           "boundaries": [], "difficulty_mechanisms": [], "knowledge_requests": []},
    }
    build = PromptBuild(stage="section", call_site="prompts.section")
    _system, user = prompts.section(goal, _DISCIPLINE, item, _CRITERIA, "[]", _MATERIALS,
                                    retry_violations=["缺少题号"], build=build)
    manifest = build.finish()
    assert manifest.blocks, "章节路径应注册块"
    # 章节块全部带本节 unit_id（章节只含自身策略的可核对依据）
    assert all(b.unit_id == "u1" for b in manifest.blocks)
    # 检查类块在场：重试修正（owner=check）
    assert any(b.owner == "check" and b.block_id == "retry-violations" for b in manifest.blocks)
    assert "缺少题号" in user
    # 本节策略片段在场，其它单元不可见
    assert "本节策略分工" in user
    assert "unit-2" not in json.dumps([b.text for b in manifest.blocks], ensure_ascii=False)


def test_manifest_covers_full_prompt_and_explain_can_read_it():
    """Σblock.char_count == len(user)：清单完整覆盖，prompt_explain 可直读清单。"""
    goal = _goal()
    item = {"id": "u1", "title": "第一套试卷"}
    build = PromptBuild(stage="section", call_site="prompts.section")
    _system, user = prompts.section(goal, _DISCIPLINE, item, _CRITERIA, "[]", _MATERIALS,
                                    proposition_hints="经验：先验证再组合。", build=build)
    manifest = build.finish()
    assert sum(b.char_count for b in manifest.blocks) == len(user)
    assert manifest.total_token_estimate == sum(b.token_estimate for b in manifest.blocks)
    # 直读清单可回答「注入了什么」：反向解析只作旧路径兜底
    owners_char = {}
    for block in manifest.blocks:
        owners_char[block.owner] = owners_char.get(block.owner, 0) + block.char_count
    assert owners_char.get("content_evidence", 0) > 0
    explain = context_layers.analyze_prompt(user, stage="section")
    assert explain.total_chars == len(user)


def test_same_constraint_rendered_by_single_owner():
    """同一约束一个拥有者渲染：用户硬约束全文恰好出现一次（task-context 块让位）。"""
    goal = _goal(hard_constraints="不要安排任何日程\n不要考特征值")
    item = {"id": "u1", "title": "第一套试卷",
            "_task_context": {"payload": {"task_type": "exam"}, "extensions": {}}}
    build = PromptBuild(stage="section", call_site="prompts.section")
    _system, user = prompts.section(goal, _DISCIPLINE, item, _CRITERIA, "[]", "", build=build)
    manifest = build.finish()
    constraint_owners = [b.owner for b in manifest.blocks
                         if "不要安排任何日程" in b.text]
    assert constraint_owners == ["task"], constraint_owners
    assert user.count("不要安排任何日程") == 1


def test_prompt_build_rejects_unknown_layer_and_authority():
    build = PromptBuild(stage="section", call_site="prompts.section")
    try:
        build.add("x", "no-such-layer", "t", "text")
    except ValueError as exc:
        assert "未知层级" in str(exc)
    else:
        raise AssertionError("未知层级应被拒绝")
    try:
        build.add("x", "system", "t", "text", authority="magic")
    except ValueError as exc:
        assert "未知权威来源" in str(exc)
    else:
        raise AssertionError("未知权威来源应被拒绝")


def test_token_estimate_matches_call_log_scale():
    text = "线性代数秩abc" * 10
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    assert token_estimate(text) == max(1, cjk + (other + 3) // 4)
    assert token_estimate("") >= 1

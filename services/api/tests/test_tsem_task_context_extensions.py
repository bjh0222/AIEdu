"""02 切片 2：TaskContext.extensions（reference_documents / execution_policy）测试。

钉死契约 §1.1/§2：
- extensions.reference_documents：per-document count / spans / count_basis / parse_coverage /
  anomalies / legacy_question_count（单卷口径）；generation.v1 payload 不加未声明字段；
- execution_policy：delivery_mode 判定与依据、knowledge_extract_timing=outline_and_final
  （现状不变）、skip_learning_stages=false（激活走 ADR）；
- 旧快照兼容：没有新键的 TaskContext 仍然合法（extensions 可选语义）。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.generation_contract import validate_generation_message
from app.services.task_understanding import (
    build_task_context,
    decide_execution_policy,
    load_persisted_task_context,
)
from tests.test_tsem_reference_documents import (
    _MaterialDB,
    _material,
    _three_volume_fixture,
)


def _goal(**overrides) -> SimpleNamespace:
    base = dict(
        id="g-test", title="出三套试卷检验学习成果", domain_key="math_logic", branch="线性代数",
        motivation="检验期中复习效果", application_context="期中模拟",
        current_level="基础扎实", time_budget="", hard_constraints="不要安排任何日程",
        user_notes="", format_spec_snapshot=json.dumps({"scene": "exam_paper"}),
        format_spec_source="", format_spec_match_note="",
        difficulty_mode="", difficulty_note="",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_task_context_extensions_carry_reference_documents():
    db = _MaterialDB([_material(1, _three_volume_fixture())])
    context = build_task_context(db, _goal(), outline=[{"id": "u1", "title": "第一套试卷"}])
    # generation.v1 payload 不加未声明字段：合辑明细只存在于 extensions（anomaly 文本提及不算）
    assert "reference_documents" not in context["payload"]
    assert "legacy_question_count" not in context["payload"]
    ext = context["extensions"]["reference_documents"]
    assert ext["parser_version"].startswith("reference-documents.v")
    assert ext["source_count"] == 1
    assert ext["logical_document_count"] == 3
    assert ext["totals"]["question_count"] == 25
    assert ext["totals"]["per_document_counts"] == [8, 8, 9]
    docs = ext["documents"]
    assert docs[0]["material_id"] == "m1" and docs[0]["filename"] == "ref1.md"
    assert [d["question_count"] for d in docs] == [8, 8, 9]
    assert all(d["question_spans"] for d in docs)
    kinds = {a["kind"] for d in docs for a in d["anomalies"]}
    assert {"missing_question_number", "recalled_version", "answer_absent"} <= kinds
    legacy = ext["legacy_question_count"]
    assert legacy["question_count"] is None
    assert legacy["semantic"] == "typical_single_document"
    assert legacy["total_count"] == 25
    assert legacy["range_summary"]
    validate_generation_message(context, expected_kind="TaskContext")


def test_task_context_single_volume_question_count_is_typical():
    body = "### 选择题\n\n**1.** 题\n\n**2.** 题\n\n### 解答题\n\n**3.** 题\n"
    context = build_task_context(_MaterialDB([_material(1, body)]), _goal())
    legacy = context["extensions"]["reference_documents"]["legacy_question_count"]
    assert legacy["question_count"] == 3
    assert legacy["total_count"] == 3
    # 画像层 legacy 字段与 extensions 口径一致
    assert context["payload"]["reference_profile"]["question_count"] == 3


def test_execution_policy_for_artifact_and_learning_tasks():
    policy = decide_execution_policy("exam")
    assert policy["delivery_mode"] == "artifact"
    assert "artifact" in policy["mode_basis"]
    assert policy["knowledge_extract_timing"] == "outline_and_final"  # v1 现状不变
    assert policy["skip_learning_stages"] is False
    policy_learning = decide_execution_policy("knowledge_tutorial")
    assert policy_learning["delivery_mode"] == "learning"
    assert policy_learning["knowledge_extract_timing"] == "outline_and_final"
    # extensions 恒带 execution_policy（无材料也有）
    context = build_task_context(_MaterialDB([]), _goal())
    assert context["extensions"]["execution_policy"]["policy_version"] == "execution-policy.v1"
    assert "reference_documents" not in context["extensions"]  # 无材料不假装有文档


def test_execution_policy_present_without_materials_and_snapshot_compatible():
    # 旧快照：TaskContext 无新 extensions 键仍合法（extensions 可选语义）
    old_context = {
        "contract_version": "generation.v1", "kind": "TaskContext",
        "identity": {"task_id": "t", "goal_id": "g", "tutorial_id": None,
                     "version": None, "unit_id": None},
        "payload": {
            "task_type": "exam", "domain_key": "math_logic", "branch": None,
            "learner": None, "objective": "出卷", "constraints": [],
            "scope": {"include": [], "exclude": [], "basis": "unknown", "source_refs": []},
            "reference_profile": None,
            "difficulty_target": {"mode": "unspecified", "instruction": None,
                                  "evidence_basis": "unknown", "source_refs": []},
            "deliverables": [], "assumptions": [], "conflicts": [],
        },
        "extensions": {"adapter": "task-understanding.v1"},
    }
    validate_generation_message(old_context, expected_kind="TaskContext")
    assert load_persisted_task_context({"task_context": old_context}) is not None


def test_summary_exposes_range_not_fake_count():
    context = build_task_context(
        _MaterialDB([_material(1, _three_volume_fixture())]), _goal())
    summary = json.dumps(context, ensure_ascii=False)
    # 范围摘要在 extensions 可见；payload 的 legacy 字段不得出现合辑总量 25 冒充单卷
    assert "typical_single_document" in summary
    assert context["payload"]["reference_profile"]["question_count"] is None

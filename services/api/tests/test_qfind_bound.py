# -*- coding: utf-8 -*-
"""R2-3：下界类生成任务明确性检查需求。

来源：运行记录-两套线代试卷-20261108-修复后/监控与比较报告.md §6「本轮仍有质量问题」
（第二卷第 4 题仅写 r(C)≥空白、未要求最大下界/最小可能秩，评分唯一性未解决）与 §7 第 2 行。
本测试钉死：①边界性设问缺紧界限定 → 可定位 advisory finding；②已有「最大下界/最小可能秩」
不误报；③检查需求进入验收计划（required=False，不阻断交付）；④改生产扫描逻辑必须真实
改变验收结果（mutation gate），只改 fixture expectation 不算数。
零模型。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from app.services import generation_acceptance as acceptance
from app.services.proposition_generated import bound_tightness_findings

FIXTURES = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/contracts/generation.v1.examples.json")
    .read_text(encoding="utf-8"))


def context(task_type="exam"):
    value = deepcopy(FIXTURES[0])
    value["payload"]["task_type"] = task_type
    return value


def goal(**kwargs):
    data = {"id": "g", "domain_key": "math_logic", "branch": "linear_algebra",
            "title": "两套线代试卷", "motivation": "按原卷风格出卷",
            "application_context": "本科生", "current_level": "学过线性代数",
            "time_budget": "", "hard_constraints": ""}
    return SimpleNamespace(**{**data, **kwargs})


def content(text: str) -> dict:
    return {"format_spec": {"scene": "exam_paper"},
            "outline": [{"id": "unit-1", "title": "线代试卷"}],
            "sections": [{"id": "unit-1", "title": "线代试卷", "mapping_criteria": ["c"],
                          "content": [{"type": "text", "body": text}]}]}


def test_bound_question_without_tightness_is_advisory_finding():
    text = "### 证明题\n**4.** 设 $C$ 为 $m\\times n$ 矩阵，证明 $r(C)\\ge\\;\\underline{\\qquad}$。"
    findings = bound_tightness_findings(content(text))
    assert len(findings) == 1
    finding = findings[0]
    assert finding["check_id"] == "bound_tightness"
    assert finding["severity"] == "advisory"
    assert finding["status"] == "violated"
    assert finding["evidence_method"] == "deterministic"
    assert finding["location"] == "questions/4"
    assert finding["question_numbers"] == [4]
    assert "评分唯一性" in finding["reason"]


def test_tight_bound_question_does_not_false_positive():
    for text in (
        "**4.** 求 $r(C)$ 的最小可能秩，并说明等号成立条件。",
        "**4.** 求 $r(C)$ 的最大下界。",
        "**4.** 求该秩的紧界。",
    ):
        assert bound_tightness_findings(content(text)) == [], text


def test_non_bound_question_is_skipped():
    assert bound_tightness_findings(content("**3.** 计算 $2+2$。")) == []


def test_quality_acceptance_adds_bound_check_requirement():
    task = context()
    task["payload"]["constraints"] = []
    document = content("**4.** 设 $C$ 为 $m\\times n$ 矩阵，证明 $r(C)\\ge\\;\\underline{\\qquad}$。")
    result = acceptance.quality_acceptance(
        goal(), document, {"checks": [], "issues": []}, task_context=task)
    plan = result["extensions"]["check_plan"]
    bound = next(p for p in plan if p["id"] == "bound_tightness")
    assert bound["required"] is False           # advisory：不阻断交付
    assert bound["method"] == "deterministic"
    assert "最大下界" in bound["evidence_requirement"]
    check = next(c for c in result["payload"]["checks"] if c["id"] == "bound_tightness")
    assert check["status"] == "violated" and check["evidence_method"] == "deterministic"
    assert result["extensions"]["bound_tightness"]


def test_bound_check_disappears_when_scanner_returns_none(monkeypatch):
    """mutation gate：生产扫描逻辑改变 → 验收结果必须真实改变（不是 fixture 断言）。"""
    import app.services.generation_acceptance as ga

    monkeypatch.setattr("app.services.proposition_generated.bound_tightness_findings",
                        lambda content: [])
    task = context()
    task["payload"]["constraints"] = []
    document = content("**4.** 证明 $r(C)\\ge\\;\\underline{\\qquad}$。")
    result = ga.quality_acceptance(goal(), document, {"checks": [], "issues": []},
                                   task_context=task)
    assert all(p["id"] != "bound_tightness" for p in result["extensions"]["check_plan"])
    assert "bound_tightness" not in result["extensions"]

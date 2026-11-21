# -*- coding: utf-8 -*-
"""S1 fixture：复核状态逐 finding 分类（脱敏自真实运行样本）。

真实失败（单元 3c10ce46:0）：修复后复核 defects=[]，且明确说「原第4题参考答案已
复核为 0…未发现该缺陷残留」，但 uncertainties 里同时有「未进行实际考试、作答或
运行验证」「未实测排版渲染」这类通用方法局限；旧代码只要 uncertainties 非空就判
undecidable，把已确认修复拖成未完成。

预期：通用方法局限归 limitations（empirical 保持 not_run，不冒充已实测）；
无法分类的未知项保守保留 undecidable；复核出现新 blocking 缺陷判 violated。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.db import SessionLocal
from app.services import proposition_generated as generated
from app.services import proposition_knowledge as knowledge

# 脱敏自真实复核输出（单元 3c10ce46:0 的 recheck.uncertainties）。
RECHECK_CLEAN = {
    "defects": [],
    "uncertainties": [
        "本复核为静态数学一致性检查，未进行实际考试、作答或运行验证。",
        "原第4题参考答案已复核为 $0$，与 $r(A)\\le n-2$ 时 $r(A^*)=0$ 及第11题定理一致；未发现该缺陷残留。",
        "未实测排版渲染、学习者适配性与考试过程，相关判断仅为静态先验。",
    ],
}

# 脱敏自真实初审输出：未实测/静态类通用局限。
REVIEW_LIMITATIONS = [
    "未执行实际作答、运行或统计核验，仅作静态题面与答案审查。",
    "未独立核对参考来源原文件；仅依据给出的题面、参考答案与任务元数据审查。",
]


def test_generic_method_limitations_do_not_make_recheck_undecidable():
    verdict = generated.classify_recheck(RECHECK_CLEAN, [
        {"severity": "blocking", "reason": "第4题参考答案错误：应有 r(A*)=0，而参考答案给 1。",
         "evidence": "题面第4题；参考答案第4题给出 1。"},
    ])
    assert verdict["status"] == "satisfied"
    assert verdict["open_uncertainties"] == []
    # 未实测/静态局限单独记录，不得伪装成已实测
    assert any("未实测" in x or "未进行实际考试" in x for x in verdict["limitations"])
    assert any("静态" in x for x in verdict["limitations"])
    # 原 finding 判 resolved，并保留解决证据
    assert [f["status"] for f in verdict["findings"]] == ["resolved"]


def test_unclassifiable_uncertainty_stays_undecidable():
    """无法分类的未知项保守保留未知，不得当成已解决。"""
    verdict = generated.classify_recheck(
        {"defects": [], "uncertainties": ["静态检查无法确认第7题答案的唯一性。"]},
        [{"severity": "blocking", "reason": "第7题答案可能不唯一。", "evidence": "题面第7题。"}],
    )
    assert verdict["status"] == "undecidable"
    assert verdict["open_uncertainties"]
    assert verdict["findings"][0]["status"] == "undecidable"


def test_new_blocking_defect_makes_recheck_violated():
    verdict = generated.classify_recheck(
        {"defects": [{"severity": "blocking", "reason": "第4题参考答案仍是 1。",
                      "evidence": "参考答案第4题给出 1。"}],
         "uncertainties": ["未实测排版渲染。"]},
        [{"severity": "blocking", "reason": "第4题参考答案错误：应有 r(A*)=0。",
          "evidence": "题面第4题。"}],
    )
    assert verdict["status"] == "violated"
    assert verdict["findings"][0]["status"] == "unresolved"


def test_new_unrelated_defect_does_not_clear_original_finding():
    """复核只报另一题新缺陷时，原缺陷不得被自动清空（保守记 undecidable）。"""
    verdict = generated.classify_recheck(
        {"defects": [{"severity": "blocking", "reason": "第10题新发现的证明缺口。",
                      "evidence": "题面第10题。"}],
         "uncertainties": []},
        [{"severity": "blocking", "reason": "第4题参考答案错误。", "evidence": "题面第4题。"}],
    )
    assert verdict["status"] == "violated"
    assert verdict["findings"][0]["status"] == "undecidable"


def test_review_uncertainties_are_split_into_limitations_and_unknowns():
    verdict = generated.classify_uncertainties(REVIEW_LIMITATIONS + [
        "无法确认第7题答案的唯一性。"])
    assert len(verdict["limitations"]) == 2 and len(verdict["unknown"]) == 1


class ReviewAI:
    """端到端假 AI：审查 → 定向修复 → 复核（零真实模型调用）。"""

    is_demo = False
    calls = []
    recheck_payload = RECHECK_CLEAN

    def __init__(self, **kwargs):
        pass

    def _real_json(self, *args):
        if args[0] == "proposition_generated_recheck":
            self.calls.append("recheck")
            return dict(self.recheck_payload)
        self.calls.append("review")
        return {"defects": [{"severity": "blocking",
                             "reason": "第 9 题参考答案第二空错误：AQ 的列变换描述与矩阵乘法不符。",
                             "evidence": "Q 的第二列为 (0,1,2)^T，故 AQ 的第二列为 C2+2C3。"}],
                "uncertainties": list(REVIEW_LIMITATIONS)}

    def _real_text(self, *args):
        self.calls.append("repair")
        return REPAIRED


PAPER = r"""### 解答题

**9.**（11 分）设 $A$、$P$、$Q$ 为给定矩阵：(1) 用初等行变换求 $A^{-1}$；(2) 分别说明 $PA$ 与 $AQ$ 对 $A$ 施行的初等变换并求 $|PAQ|$。

### 参考答案

**9.** (1) 对 $(A\mid E)$ 作初等行变换把 $A$ 化为 $E$，得 $A^{-1}$ 为题设矩阵的逆；(2) $PA$：把第 2 行加上第 1 行；$AQ$：把第 2 列的 2 倍加到第 3 列；$|P|=|Q|=1$，$|A|=4$，故 $|PAQ|=4$。
"""

REPAIRED = PAPER.replace(
    "$AQ$：把第 2 列的 2 倍加到第 3 列",
    "$AQ$：把第 3 列的 2 倍加到第 2 列",
)


def _goal():
    return SimpleNamespace(id="test_goal", domain_key="math_logic", title="线代",
                           motivation="", application_context="", current_level="",
                           time_budget="", hard_constraints="")


def _content(text):
    return {"format_spec": {"scene": "exam_paper"},
            "sections": [{"id": "s1", "content": [{"type": "text", "body": text}]}]}


def test_end_to_end_repair_with_clean_recheck_is_satisfied(client, monkeypatch):
    ReviewAI.calls = []
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        content = _content(PAPER)
        report = generated.review_generated(db, content, _goal(), "", "")
    entry = report["reports"][0]
    assert entry["repair"] == "applied_once"
    assert entry["recheck_status"] == "satisfied"
    assert entry["recheck_open_uncertainties"] == []
    assert any("未实测" in x or "未进行实际考试" in x for x in entry["recheck_limitations"])
    # 初审的通用局限也单独记录，不进入 open_uncertainties
    assert entry["open_uncertainties"] == []
    assert len(entry["limitations"]) == 2
    assert ReviewAI.calls == ["review", "repair", "recheck"]

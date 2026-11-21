# -*- coding: utf-8 -*-
"""S1 fixture：repair_guard 的题号定位协议（脱敏自真实运行样本）。

真实失败（tutorial 81fa245e03fb4a4397a0193af8fa08c1，单元 410ef7e8:0）：
审查报告把题号写在 reason（「第 9 题参考答案第二空错误…」），evidence 只有矩阵
推导，没有「第 N 题」表述，也没有 number/question_number 字段。repair_guard 只从
number/question_number 与 evidence 抽题号，affected 集为空，把正确定位的第 9 题
定向修复当成「未列为修复对象的完整长答案被改写」而拒绝。

本文件只做脱敏合成：题面与答案是自编的线代小题，不含任何真实用户数据。
"""
from __future__ import annotations

from app.services.proposition_generated import repair_guard

# 题面：Q9 带两个小问；Q10 是另一道长答案题（非缺陷目标）。
PAPER = r"""### 解答题

**9.**（11 分）设 $A=\begin{pmatrix}2&1&1\\1&2&1\\1&1&2\end{pmatrix}$，$P$、$Q$ 为初等矩阵：(1) 用初等行变换求 $A^{-1}$；(2) 分别说明 $PA$ 与 $AQ$ 对 $A$ 施行的初等变换并求 $|PAQ|$。

**10.**（11 分）设 $A$、$B$ 均为 $m\times n$ 实矩阵，证明 $r(A+B)\le r(A)+r(B)$。

### 参考答案

**9.** (1) 对 $(A\mid E)$ 作初等行变换把 $A$ 化为 $E$，得 $A^{-1}=\frac{1}{4}\begin{pmatrix}3&-1&-1\\-1&3&-1\\-1&-1&3\end{pmatrix}$；(2) $PA$：把第 2 行加上第 1 行；$AQ$：把第 2 列的 2 倍加到第 3 列；$|P|=|Q|=1$，$|A|=4$，故 $|PAQ|=4$。

**10.** 证明：记 $V_A$、$V_B$ 分别为 $A$、$B$ 的列空间，则 $A+B$ 的每一列 $\alpha_j+\beta_j$ 都属于 $V_A+V_B$，故列空间包含于 $V_A+V_B$，于是 $r(A+B)\le\dim(V_A+V_B)\le\dim V_A+\dim V_B=r(A)+r(B)$，证毕。
"""

# 脱敏自真实样本：题号只在 reason，evidence 里的「第2列/第3列」不是题号。
DEFECTS_REASON_ONLY = [
    {
        "severity": "blocking",
        "reason": "第 9 题参考答案第二空错误：AQ 对应的列变换描述与矩阵乘法不符。",
        "evidence": "Q=[[1,0,0],[0,1,0],[0,2,1]] 的第二列为 (0,1,2)^T，故 AQ 的第二列为 C2+2C3；"
                    "应描述为「第2列加上第3列的2倍」，答案写成「第3列加上第2列的2倍」。"
                    "虽然 |PAQ|=4 的结果正确，但操作说明错误。",
    },
    {
        "severity": "blocking",
        "reason": "第9题(2)中 AQ 的列变换描述错误，答案与设问不匹配。",
        "evidence": "Q 的第二列为 (0,1,2)^T，故 AQ 的第二列为 C2+2C3，"
                    "应描述为「第2列加上第3列的2倍」。",
    },
]


def _repair_q9() -> str:
    """只修复第 9 题答案：把写反的 AQ 列变换方向改正，其余逐字不动。"""
    return PAPER.replace(
        "$AQ$：把第 2 列的 2 倍加到第 3 列",
        "$AQ$：把第 3 列的 2 倍加到第 2 列",
    )


def test_reason_number_targeted_full_repair_passes():
    """reason 含「第 9 题」、evidence 不含题号：第 9 题定向完整修复必须通过。"""
    assert repair_guard(PAPER, _repair_q9(), DEFECTS_REASON_ONLY) == []


def test_editing_unrelated_long_answer_still_rejected():
    """改第 10 题（非缺陷目标）的长答案仍拒绝。"""
    repaired = PAPER.replace(
        r"于是 $r(A+B)\le\dim(V_A+V_B)\le\dim V_A+\dim V_B=r(A)+r(B)$，证毕。",
        r"故列空间包含于 $V_A+V_B$，维数不超过两者之和，证毕。",
    )
    reasons = repair_guard(PAPER, repaired, DEFECTS_REASON_ONLY)
    assert any("第10题" in r and "长答案" in r for r in reasons)


def test_losing_subitems_still_rejected():
    """删除第 9 题答案小问 (2) 仍拒绝——即使第 9 题是缺陷目标。"""
    repaired = _repair_q9().replace(
        "；(2) $PA$：把第 2 行加上第 1 行；$AQ$：把第 3 列的 2 倍加到第 2 列；$|P|=|Q|=1$，$|A|=4$，故 $|PAQ|=4$。",
        "。",
    )
    reasons = repair_guard(PAPER, repaired, DEFECTS_REASON_ONLY)
    assert any("第9题" in r and "小问丢失" in r for r in reasons)


def test_rewording_unrelated_stem_still_rejected():
    """改非目标题面仍拒绝。"""
    repaired = PAPER.replace(r"设 $A$、$B$ 均为 $m\times n$ 实矩阵，证明", r"设 $A$、$B$ 是任意实矩阵，证明")
    reasons = repair_guard(PAPER, repaired, DEFECTS_REASON_ONLY)
    assert any("第10题" in r and "题面" in r for r in reasons)


def test_score_change_still_rejected():
    """分值括注变化仍拒绝（契约签名保护）。"""
    repaired = PAPER.replace("**10.**（11 分）", "**10.**（12 分）")
    reasons = repair_guard(PAPER, repaired, DEFECTS_REASON_ONLY)
    assert any("题号、分值、题量、栏目或代码围栏发生变化" in r for r in reasons)


def test_number_field_still_supported():
    """回归：number 字段定位的定向修复不受影响。"""
    defects = [{"severity": "blocking", "number": 9,
                "reason": "AQ 的列变换描述与矩阵乘法不符。",
                "evidence": "Q 的第二列为 (0,1,2)^T。"}]
    assert repair_guard(PAPER, _repair_q9(), defects) == []


def test_number_in_evidence_still_supported():
    """回归：题号只在 evidence（「题面第9题」式定位）时仍可用。"""
    defects = [{"severity": "blocking",
                "reason": "AQ 的列变换描述与矩阵乘法不符。",
                "evidence": "题面第9题(2)：$AQ$ 的列变换方向写反，应为「第3列的2倍加到第2列」。"}]
    assert repair_guard(PAPER, _repair_q9(), defects) == []

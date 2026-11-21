# -*- coding: utf-8 -*-
"""S1 fixture：分值表「每题分值」逐题列表解析（脱敏自真实运行样本）。

真实失败（第二套卷）：`| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 68 |`。
旧解析器把 each 不可解析当成整行不可解析（continue），把这一行的题量 6 与小计 68
也丢掉，只剩填空行的 8 题/32 分，级联出 E008「小计之和 32 ≠ 满分 100」「合计行
100 ≠ 32」「合计行题量 14 ≠ 8」和 E016「100 ≠ 32」。而生产规范明确允许逐题列表：
format_specs 卷面分值分布表规则第 3 条「同题型分值不同时，每题分值列逐项列出并用
顿号分隔，小计仍写总和」。

要求：题量/每题分值/小计独立解析，未知为 null 不当 0；列表非法只报对应行的解析/
契约错误，禁止级联误报；不得自动改题面分值凑满分。
"""
from __future__ import annotations

from app.services import exam_check as ec

# 脱敏合成：结构镜像真实第二套卷，题面为自编占位内容，不含真实用户数据。
EXAM = """> 本卷满分 100 分，考试时间 120 分钟，共 14 题。

| 题型 | 题号 | 题量 | 每题分值 | 小计 |
|---|---|---|---|---|
| 填空题 | 1–8 | 8 | 4 | 32 |
| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 68 |
| 合计 | 1–14 | 14 | — | 100 |

### 填空题

**1.**（4 分）题面一 ______。

**2.**（4 分）题面二 ______。

**3.**（4 分）题面三 ______。

**4.**（4 分）题面四 ______。

**5.**（4 分）题面五 ______。

**6.**（4 分）题面六 ______。

**7.**（4 分）题面七 ______。

**8.**（4 分）题面八 ______。

### 证明题

**9.**（10 分）证明题面一。

**10.**（10 分）证明题面二。

**11.**（11 分）证明题面三。

**12.**（11 分）证明题面四。

**13.**（11 分）证明题面五。

**14.**（15 分）证明题面六。



### 参考答案

**1.** 甲

**2.** 乙

**3.** 丙

**4.** 丁

**5.** 戊

**6.** 己

**7.** 庚

**8.** 辛

**9.** 证明一。

**10.** 证明二。

**11.** 证明三。

**12.** 证明四。

**13.** 证明五。

**14.** 证明六。
"""


def test_parse_score_row_supports_per_question_list():
    row = ec.parse_score_row({"type": "证明题", "range": "9–14", "count": "6",
                              "each": "10、10、11、11、11、15", "sub": "68"})
    assert row.count == 6
    assert row.each is None
    assert row.each_scores == [10, 10, 11, 11, 11, 15]
    assert row.subtotal == 68
    assert row.parse_errors == []
    assert row.product == 68


def test_parse_score_row_supports_uniform_single_value():
    row = ec.parse_score_row({"type": "填空题", "range": "1–8", "count": "8",
                              "each": "4", "sub": "32"})
    assert row.count == 8 and row.each == 4 and row.each_scores is None
    assert row.subtotal == 32 and row.parse_errors == []
    assert row.product == 32


def test_mixed_table_sums_to_14_questions_100_points_without_cascade():
    """6/[10,10,11,11,11,15]/68 + 8/4/32 → 总量 14、总分 100，不产生 E008/E016/E007。"""
    paper = ec.parse_paper(EXAM)
    codes = [v.code for v in ec.check_paper(paper)]
    assert "E008" not in codes
    assert "E016" not in codes
    assert "E007" not in codes


def test_illegal_list_only_reports_its_own_row_error():
    """逐题分值 3 项 ≠ 题量 6：只报本行契约错误；题量/小计仍参与合计，禁止级联。"""
    bad = EXAM.replace("10、10、11、11、11、15", "10、10、11")
    violations = ec.check_paper(ec.parse_paper(bad))
    e008 = [v for v in violations if v.code == "E008"]
    assert e008, "列表与题量不符必须报 E008"
    detail = e008[0].detail
    assert "逐题分值 3 项与题量 6 不符" in detail
    # 部分乘积未知时不做全局乘积比较，也不拿部分和冒充全体
    assert "各行小计之和" not in detail
    assert "≠ 卷面声明满分" not in detail
    assert "合计行" not in detail
    # 小计 68 与题量 6 仍被解析：32 分级联误报消失
    assert not any(v.code == "E016" for v in violations)
    assert not any(v.code == "E007" for v in violations)


def test_unparseable_each_is_null_not_zero():
    """含不可解析 token：each_scores 为 None（不当 0），题量与小计独立解析。"""
    row = ec.parse_score_row({"type": "证明题", "range": "9–14", "count": "6",
                              "each": "10、未知、11、11、11、15", "sub": "68"})
    assert row.each is None and row.each_scores is None
    assert row.count == 6 and row.subtotal == 68
    assert row.product is None
    assert row.parse_errors and "未知" in row.parse_errors[0]

    bad = EXAM.replace("10、10、11、11、11、15", "10、未知、11、11、11、15")
    detail = next(v for v in ec.check_paper(ec.parse_paper(bad)) if v.code == "E008").detail
    assert "证明题" in detail and "未知" in detail
    # 不得把未知当 0 参与：无 ×0 乘积、无部分和级联
    assert "×0" not in detail and "各行小计之和" not in detail
    assert not any(v.code == "E016" for v in ec.check_paper(ec.parse_paper(bad)))


def test_list_row_with_wrong_subtotal_reports_row_mismatch():
    """列表可解析但 sum ≠ 小计：报本行账目错误（这是真实不一致，不是解析问题）。"""
    bad = EXAM.replace("| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 68 |",
                       "| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 60 |")
    detail = next(v for v in ec.check_paper(ec.parse_paper(bad)) if v.code == "E008").detail
    assert "逐题分值之和 68 ≠ 小计 60" in detail


def test_no_auto_rewrite_of_face_scores_or_list_cells():
    """账目修复不得改题面分值凑满分，也不得改写逐题列表单元格。"""
    bad = EXAM.replace("| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 68 |",
                       "| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 60 |")
    fixed, violations = ec.fix_markdown(bad)
    e008 = next(v for v in violations if v.code == "E008")
    assert e008.auto_fixable is False
    assert "（10 分）" in fixed and "（15 分）" in fixed, "题面分值不得被改写"
    assert "| 证明题 | 9–14 | 6 | 10、10、11、11、11、15 | 60 |" in fixed, "列表单元格不得被改写"
    assert "> 本卷满分 100 分" in fixed


def test_wellformed_list_paper_fix_is_noop():
    """账目本来就自洽的逐题列表卷：修复器逐字不动。"""
    fixed, violations = ec.fix_markdown(EXAM)
    assert not [v for v in violations if v.code in {"E008", "E016", "E007"}]
    assert fixed == EXAM

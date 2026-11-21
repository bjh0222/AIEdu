"""02 切片 1 失败夹具：三册合辑解析（parse_reference_documents）。

夹具为**脱敏合成**（概率统计科目、自拟题面、机构用「某某大学」占位），
只复刻真实三册合辑的**结构特征**，禁止真实用户数据进 tests：

- 一份物理文件、三个逻辑试卷（``## 第N册`` 划界），题量 8/8/9 合计 25；
- 第二册无题型标题 → 题型分布保持 unknown；题内 （1）（2）（3） 小问不多算；
- 第三册填空题区用中文序号题号（**一.**…），证明题区的 (1)/(2) 是该区一级题号；
- 「原卷缺第 5 题」「考后回忆版」「识别说明」成为可定位疑点（带 span 与来源引用）。

红→绿路径：本文件先于实现提交；``parse_reference_documents`` 落地前整体为红，
其中 legacy profile 一条（跨册状态串染 → 16 题/解答题 10）在 02-2 接线后转绿。
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.reference_documents import (
    REFERENCE_PARSER_VERSION,
    parse_reference_documents,
)


# ---------------------------------------------------------------- 合成夹具

def _three_volume_fixture() -> str:
    """三册合辑合成夹具：结构与 .audit 源文一致，内容全部自拟。"""
    return "\n".join([
        "# 概率统计（乙）期中考试卷 · 题目版合辑",
        "",
        "> 合成测试夹具：仅用于解析器行为验证，题目内容自拟。",
        "> 两册均为**题目版，不含答案**；每题下方保留原卷的作答区提示「解：」。",
        ">",
        "> - 第一册：某某大学 2024–2025 学年 秋季学期（考后回忆版，历年卷第 12 套）",
        "> - 第二册：某某大学 2022–2023 学年 冬季学期（历年卷第 11 套）",
        "> - 第三册：26-27学年期中（由试卷照片识别整理，原图未印套号与考试时长）",
        "",
        "---",
        "",
        "## 第一册　某某大学 2024–2025 学年 秋季学期《概率统计（乙）》期中考试试卷",
        "",
        "**考试形式**：闭卷　　**考试时间**：120 分钟　　**满分**：100 分"
        "（一、填空题 80 分，二、解答题 20 分）　　**共 8 项**（填空题 6 题 + 解答题 2 题）",
        "",
        "> 编号说明：本册按顺序重编为 1–8。原卷填空题题号由 4 直接跳到 6（缺第 5 题），"
        "解答题另起编号 1、2；下文仅在有出入时括注原卷题号。",
        "",
        "### 一、填空题（80 分）",
        "",
        "**1.** 已知随机变量 $X$ 服从二项分布 $B(10,p)$，且 $E(X)=4$，则 $p=$ \\_\\_\\_\\_\\_。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**2.** 设 $A,B$ 为互斥事件，$P(A)=0.3$，$P(B)=0.4$，则 $P(A\\cup B)=$ \\_\\_\\_\\_\\_。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**3.** 已知二维离散型随机变量的联合分布含待定常数 $c$，则 $c=$ \\_\\_\\_\\_\\_。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**4.** 若 $D(X)=2$，则 $D(2X+3)=$ \\_\\_\\_\\_\\_。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**5.**（原卷第 6 题，原卷缺第 5 题）设总体 $X\\sim N(\\mu,\\sigma^{2})$ 且 $\\sigma^{2}$ 未知，"
        "则 $\\mu$ 的置信区间用 \\_\\_\\_\\_\\_ 分布构造。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**6.**（原卷第 7 题）从一批产品中不放回抽取 3 件，恰有 1 件次品的概率为 \\_\\_\\_\\_\\_。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "### 二、解答题（20 分）",
        "",
        "**7.**（原卷解答题第 1 题）设 $(X,Y)$ 的联合密度已知，求 $f_{X}(x)$ 与 $\\mathrm{Cov}(X,Y)$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**8.**（原卷解答题第 2 题）设总体 $X$ 的密度已知，证明：样本均值 $\\bar X$ 是 $\\mu$ 的无偏估计。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "> **来源**：某某大学《概率统计（乙）》期中历年卷（2016–2025，第 12 套）。"
        "本套为考后回忆版，题面据回忆整理，与原卷可能有细节出入：原卷填空题题号由 4 直接跳到 6"
        "（缺第 5 题），解答题另起编号（1、2），本册按顺序重新编号为 1–8 并在题首注明原题号。",
        "",
        "---",
        "",
        "## 第二册　某某大学 2022–2023 学年 冬季学期《概率统计（乙）》期中考试试卷",
        "",
        "**考试形式**：闭卷　　**考试时间**：120 分钟　　**分值**：原卷未印各题分值　　**共 8 题**",
        "",
        "**1.** 计算二维离散型随机变量联合分布中的常数 $c$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**2.** 已知 $X\\sim P(\\lambda)$，求 $E\\left(\\dfrac{1}{X+1}\\right)$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**3.** 设 $(X,Y)$ 服从区域 $G$ 上的均匀分布，求 $P\\left(Y\\le X^{2}\\right)$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**4.** 分别用矩估计法与最大似然估计法求参数 $\\theta$ 的估计量。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**5.** 设 $r$ 为自然数。",
        "",
        "（1）叙述大数定律的结论；",
        "",
        "（2）证明：本题条件下依概率收敛蕴含期望收敛；",
        "",
        "（3）设 $X$ 经一次线性变换得到 $Y$，求证：$\\rho_{XY}=\\pm 1$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**6.** 设 $\\Sigma$ 为 $n$ 维随机向量的协方差矩阵，证明：",
        "",
        "（1）$\\Sigma$ 半正定当且仅当其各阶主子式非负；",
        "",
        "（2）$|\\Sigma|\\ge 0$；",
        "",
        "（3）若 $\\Sigma$ 正定，则 $|\\Sigma|>0$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**7.** 设 $X,Y$ 为连续型随机变量，证明：$D(X+Y)\\le D(X)+D(Y)+2\\sqrt{D(X)D(Y)}$。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "**8.** 设 $X_{1},\\dots,X_{n}$ 独立同分布，用数学归纳法证明样本均值方差不超过均值方差。",
        "",
        "**解：**",
        "",
        "---",
        "",
        "> **来源**：某某大学《概率统计（乙）》期中历年卷（2016–2025，第 11 套），题目照原卷录入；"
        "原卷未附标准答案且未印分值，本册解答为自行编写。本册为题目版，大题下方留有作答区。",
        "",
        "---",
        "",
        "## 第三册　26-27学年期中《概率统计（乙）》期中考试试卷",
        "",
        "**题型分值**：填空题 85 分（共 7 题）＋ 证明题 15 分（共 2 题）　　**满分**：100 分"
        "（由卷面 85 + 15 相加得出，原图未单独印总分）",
        "",
        "> 本册由两张试卷照片识别整理，原图未印考试时长、未设作答区，故本册不加「解：」占位；"
        "照片中的答案部分已按要求剔除，仅保留题目。",
        "",
        "### 一、填空题（85 分）",
        "",
        "**一.** 设事件 $A$ 与 $B$ 独立，$P(A)=0.4$，$P(B)=0.5$，则 $P(A\\cup B)=$ \\_\\_\\_\\_\\_。",
        "",
        "**二.** 设分段函数 $F$ 含待定系数 $a$，则当 $a=$ \\_\\_\\_\\_\\_ 时 $F$ 是分布函数，"
        "且 $P\\left(X\\le \\tfrac{1}{2}\\right)=$ \\_\\_\\_\\_\\_。",
        "",
        "**三.** 若 $P(A)=P(B)=P(C)=\\tfrac14$ 且事件两两独立，则 $P(A\\cup B\\cup C)=$ \\_\\_\\_\\_\\_。",
        "",
        "**四.** 设 $X$ 的密度已知，则 $E(X)=$ \\_\\_\\_\\_\\_，$D(X)=$ \\_\\_\\_\\_\\_。",
        "",
        "**五.** 设 $\\hat\\theta_1$ 与 $\\hat\\theta_2$ 均为 $\\theta$ 的无偏估计，"
        "则当 \\_\\_\\_\\_\\_ 时 $\\hat\\theta_1$ 比 $\\hat\\theta_2$ 有效。",
        "",
        "**六.** 设总体 $X\\sim N(\\mu,\\sigma^2)$，$X_{1},\\dots,X_{n}$ 为样本，"
        "则 $\\bar X$ 服从 \\_\\_\\_\\_\\_ 分布，$\\dfrac{(n-1)S^{2}}{\\sigma^{2}}$ 服从 \\_\\_\\_\\_\\_ 分布。",
        "",
        "**七.** 假设矩阵 $R$ 是相关矩阵，则 $R$ 的特征值之和 $=$ \\_\\_\\_\\_\\_。",
        "",
        "### 二、证明题（15 分）",
        "",
        "**(1)** 设随机变量序列满足题给条件，证明 $X_{n}$ 依概率收敛于 $X$。",
        "",
        "**(2)** 设 $X,Y$ 满足 $P(X>Y)>0$ 且 $E(XY)=0$，证明存在非零常数 $c$ 使 $E(X+cY)=0$。",
        "",
        "> **来源**：试卷照片，图内标注「公众号 · 合成示例」，为考后回忆版。"
        "**识别说明**：第三题分布函数的参数、第五题估计量的各元素，经两次图像识别结果一致；"
        "如与原卷有出入，以原卷为准。",
    ])


_SOURCE_REF = {
    "kind": "material", "id": "m-fix", "version": None,
    "hash": "0" * 64, "location": "filename:three-volume.md",
}


def _parse() -> dict:
    return parse_reference_documents(_three_volume_fixture(), dict(_SOURCE_REF))


def _doc(parsed: dict, index: int) -> dict:
    return parsed["documents"][index - 1]


# ---------------------------------------------------------------- 逻辑文档与总量

def test_three_volume_collection_parses_logical_documents():
    parsed = _parse()
    assert parsed["physical_source_count"] == 1
    assert parsed["logical_document_count"] == 3
    counts = [d["question_count"] for d in parsed["documents"]]
    assert counts == [8, 8, 9], counts
    assert parsed["totals"]["question_count"] == 25
    assert parsed["totals"]["per_document_counts"] == [8, 8, 9]
    # 合辑总量是「合计」语义，legacy 单卷语义由上层适配（本模块不冒充共同题量）
    assert parsed["totals"]["count_basis"] == "numbered_questions"
    assert parsed["parser_version"] == REFERENCE_PARSER_VERSION


def test_volume_without_type_titles_stays_unknown():
    doc2 = _doc(_parse(), 2)
    assert doc2["type_sections"] == []
    assert doc2["type_distribution"] == []
    assert doc2["type_distribution_basis"] == "unknown"
    # 没有题型标题不得推断出任何题型（含沿用上一册的「解答题」状态）
    assert not any("解答题" in json.dumps(t, ensure_ascii=False) for t in doc2["type_distribution"])


def test_third_volume_uses_chinese_and_paren_numbering():
    doc3 = _doc(_parse(), 3)
    dist = {t["name"]: t["count"] for t in doc3["type_distribution"]}
    assert dist == {"填空题": 7, "证明题": 2}
    numbers = [q["number"] for q in doc3["question_spans"]]
    assert numbers == ["一", "二", "三", "四", "五", "六", "七", "(1)", "(2)"]
    numberings = {q["numbering"] for q in doc3["question_spans"]}
    assert numberings == {"chinese", "parenthesized"}
    # 证明题区的 (1)(2) 是该区一级题号，不是上一题的小问
    proof = [q for q in doc3["question_spans"] if q["type_section"] == "证明题"]
    assert [q["number"] for q in proof] == ["(1)", "(2)"]
    assert all(q["subquestion_count"] == 0 for q in proof)


def test_subquestions_inside_questions_not_double_counted():
    doc2 = _doc(_parse(), 2)
    assert doc2["question_count"] == 8
    by_number = {q["number"]: q for q in doc2["question_spans"]}
    assert by_number["5"]["subquestion_count"] == 3
    assert by_number["6"]["subquestion_count"] == 3
    assert by_number["1"]["subquestion_count"] == 0
    # 小问跨度可定位（「题内 (1)/(2) 小问」留有证据）
    assert doc2["type_distribution_basis"] == "unknown"


def test_first_volume_type_distribution_direct():
    doc1 = _doc(_parse(), 1)
    dist = {t["name"]: t["count"] for t in doc1["type_distribution"]}
    assert dist == {"填空题": 6, "解答题": 2}
    assert doc1["type_distribution_basis"] == "direct"
    assert {q["number"] for q in doc1["question_spans"]} == {str(i) for i in range(1, 9)}


# ---------------------------------------------------------------- 疑点可定位

def test_anomalies_are_locatable_with_span_and_source():
    parsed = _parse()
    ref = parsed["source_ref"]
    assert ref["id"] == _SOURCE_REF["id"] and ref["hash"] == _SOURCE_REF["hash"]
    doc1_anomaly_kinds = {a["kind"] for a in _doc(parsed, 1)["anomalies"]}
    assert "missing_question_number" in doc1_anomaly_kinds
    assert "recalled_version" in doc1_anomaly_kinds
    missing = next(a for a in _doc(parsed, 1)["anomalies"] if a["kind"] == "missing_question_number")
    assert "缺第 5 题" in missing["message"]
    assert missing["span"]["start_line"] > 0
    doc1_span = _doc(parsed, 1)["span"]
    assert doc1_span["start_line"] <= missing["span"]["start_line"] <= doc1_span["end_line"]
    doc3_kinds = {a["kind"] for a in _doc(parsed, 3)["anomalies"]}
    assert {"recalled_version", "recognition_note"} <= doc3_kinds
    doc2_kinds = {a["kind"] for a in _doc(parsed, 2)["anomalies"]}
    assert "recalled_version" not in doc2_kinds
    # 明确自述「不含答案 / 未附标准答案 / 已剔除」是可追溯疑点（03 候选桥要消费）
    all_kinds = set().union(*( {a["kind"] for a in d["anomalies"]} for d in parsed["documents"] ))
    assert "answer_absent" in all_kinds


# ---------------------------------------------------------------- 跨度与自述交叉核对

def test_spans_ordered_and_questions_inside_documents():
    parsed = _parse()
    docs = parsed["documents"]
    for prev, nxt in zip(docs, docs[1:]):
        assert prev["span"]["end_line"] < nxt["span"]["start_line"]
    for doc in docs:
        for question in doc["question_spans"]:
            assert doc["span"]["start_line"] <= question["span"]["start_line"]
            assert question["span"]["end_line"] <= doc["span"]["end_line"]
    # 第一册的题型标题跨度落在文档跨度内，且能定位到「填空题」标题行
    fill = _doc(parsed, 1)["type_sections"][0]
    assert fill["name"] == "填空题"
    assert _doc(parsed, 1)["span"]["start_line"] <= fill["span"]["start_line"]


def test_declared_totals_cross_check():
    parsed = _parse()
    declared1 = _doc(parsed, 1)["declared_totals"]
    assert declared1["total_count"] == 8
    assert [(t["name"], t["count"]) for t in declared1["by_type"]] == [
        ("填空题", 6), ("解答题", 2)]
    assert declared1["by_type"][0].get("score") == 80
    assert _doc(parsed, 2)["declared_totals"]["total_count"] == 8
    by_type3 = {t["name"]: t for t in _doc(parsed, 3)["declared_totals"]["by_type"]}
    assert by_type3["填空题"]["count"] == 7 and by_type3["证明题"]["count"] == 2
    assert by_type3["证明题"].get("score") == 15
    coverage = parsed["totals"]["parse_coverage"]
    assert coverage["questions_identified"] == 25
    assert coverage["declared_alignment"] == "match"


# ---------------------------------------------------------------- 回归与确定性

def test_single_exam_file_is_one_logical_document():
    body = "\n".join([
        "# 某某课程 模拟卷",
        "### 选择题",
        "**1.** 题",
        "**2.** 题",
        "### 解答题",
        "**3.** 题",
    ])
    parsed = parse_reference_documents(body, dict(_SOURCE_REF))
    assert parsed["logical_document_count"] == 1
    assert parsed["documents"][0]["question_count"] == 3
    dist = {t["name"]: t["count"] for t in parsed["documents"][0]["type_distribution"]}
    assert dist == {"选择题": 2, "解答题": 1}


def test_plain_numbered_list_without_question_context_stays_unknown():
    # 非试卷的普通编号清单：无题型标题、无自述题量 → 保持 unknown，不冒充题量
    body = "\n".join(["备忘：", "1. 安装环境", "2. 运行脚本", "3. 核对输出"])
    parsed = parse_reference_documents(body, dict(_SOURCE_REF))
    doc = parsed["documents"][0]
    assert doc["question_count"] is None
    assert doc["count_basis"] == "unknown"
    assert doc["type_distribution_basis"] == "unknown"


def test_parse_is_deterministic_and_json_safe():
    first, second = _parse(), _parse()
    assert first == second
    json.dumps(first, ensure_ascii=False)


# ---------------------------------------------------------------- legacy profile 适配（02-2 接线后转绿）

def _material(idx: int, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=f"m{idx}", goal_id="g-test", kind="example_source", filename=f"ref{idx}.md",
        stored_name="", ext=".md", content_type="", size=len(text), status="ready",
        error_message="", extract_status="ok", text_excerpt=text,
        char_count=len(text), source_char_count=len(text), note="")


class _MaterialDB:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *args, **kwargs):
        class _Q:
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

            def all(self):
                return self._rows

        q = _Q()
        q._rows = self._rows  # noqa: SLF001 测试替身
        return q


def test_legacy_profile_no_cross_document_state_bleed():
    """合辑总量 25 不得冒充单卷题量；跨册题型状态不得串染（解答题 10 的旧错误不得复现）。"""
    from app.services.task_understanding import build_reference_profile

    profile = build_reference_profile(
        _MaterialDB([_material(1, _three_volume_fixture())]),
        SimpleNamespace(id="g-test"))
    assert profile is not None
    # 单卷题量不同（8/8/9）→ legacy question_count 用 None + 摘要，不取合计也不取均值
    assert profile["question_count"] is None
    # 跨册状态重置：解答题只来自第一册（2），不得把第二册 8 题串染成解答题 10
    names = {form["name"]: form["count"] for form in profile["question_types"]}
    assert names.get("解答题") == 2
    assert names.get("填空题") == 13
    assert names.get("证明题") == 2
    # 文档级疑点进入画像（缺题 / 回忆版可从画像层定位）
    joined = "\n".join(profile["anomalies"])
    assert "缺第 5 题" in joined
    assert "考后回忆版" in joined


def test_legacy_profile_single_volume_question_count_unchanged():
    from app.services.task_understanding import build_reference_profile

    body = "### 选择题\n\n**1.** 题\n\n**2.** 题\n\n### 解答题\n\n**3.** 题\n"
    profile = build_reference_profile(_MaterialDB([_material(1, body)]),
                                      SimpleNamespace(id="g-test"))
    assert profile["question_count"] == 3


def test_reference_parser_version_is_reported():
    assert REFERENCE_PARSER_VERSION.startswith("reference-documents.v")

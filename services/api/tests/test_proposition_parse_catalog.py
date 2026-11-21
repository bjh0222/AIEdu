"""题库条目清单（题干＋外评）的解析契约：标识符题号、题型元数据、引用式题面。"""
from app.db import SessionLocal
from app.schemas import PropositionBatchImport
from app.services import proposition_knowledge as knowledge

CATALOG = """# 线性代数 · 推荐入库题目清单（题干 ＋ 评价）

### C3-X-040 ｜ 第 3 章 习题 3 第 40 题 ｜ 文件 A `#02`

**题型（评价者归档）**：多小问命题判断

**题干**（逐字复现）：

> 判断下列命题是否正确:
> (1) 若矩阵 $A$ 有一列元素全为 $0$，则 $A$ 必不可逆;
> (2) 若矩阵 $A$ 的所有元素都不为 $0$，则 $A$ 必可逆;

**评价**：

- **D 难度**（先验，非实测）：3/5，置信高。
- **H 结论**：**优先推荐入库** ｜ 推荐类型：优秀典型题。

### C5-B-003 ｜ 第 5 章 补充题 5 第 3 题 ｜ 文件 A `#10`

**题型（评价者归档，非原题文字）**：证明题（充要条件）

**题干**（逐字复现，含源题面中的编辑标记）：

> 设 $A,B$ 是数域 $\\mathbb{P}$ 上的两个 $n$ 阶方阵，证明：$A$ 与 $B$ 可交换当且仅当存在不可逆矩阵 $C$ 使 $AC=BC$。

**评价**：

- **H 结论**：**可考虑入库** ｜ 推荐类型：基础关键题。
"""


def payload(text=CATALOG, **kwargs):
    return PropositionBatchImport(filename="catalog.md", markdown=text, auto_analyse=False, **kwargs)


def test_identifier_headings_split_into_separate_entries(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        assert result["counts"]["total"] == 2
        numbers = [q["question_number"] for q in result["questions"]]
        assert numbers == ["C3-X-040", "C5-B-003"]
        assert all(q["parse_status"] == "ready" for q in result["questions"])
        # 文档名保留为卷名；条目标题作为出处留在 source_map，不冒充卷名。
        assert {q["paper_title"] for q in result["questions"]} == {"线性代数 · 推荐入库题目清单（题干 ＋ 评价）"}
        assert "文件 A `#02`" in result["questions"][0]["source_map"]["entry_title"]


def test_evaluator_labels_and_quote_markers_stay_out_of_blind_stem(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        first, second = result["questions"]
        for question in (first, second):
            stem = question["raw_stem"]
            assert "题型" not in stem and "题干" not in stem
            assert not any(line.lstrip().startswith(">") for line in stem.splitlines())
            assert "D 难度" not in stem and "H 结论" not in stem and "推荐入库" not in stem
        assert first["raw_stem"].lstrip().startswith("判断下列命题是否正确")
        # 外评原文仍完整保留在 external_evaluation，未因清洗题面而丢失。
        assert "**优先推荐入库**" in first["external_evaluation"]
        assert "D 难度" in first["external_evaluation"]
        # 原文逐字保留在 raw_text（含 `>` 引用与标签行），证据不被改写。
        assert "> 判断下列命题是否正确" in first["raw_text"]
        assert "**题干**（逐字复现）：" in first["raw_text"]


def test_evaluator_question_type_is_recorded_as_metadata_not_original_text(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        for question in result["questions"]:
            # 题型是外评归档，不写入「模型分析后」的 question_type 列。
            assert question["question_type"] == ""
        meta = result["questions"][0]["source_map"]["parse_meta"]
        assert meta["question_type"] == "多小问命题判断"
        assert meta["stem_label"] == "题干（逐字复现）："
        assert meta["stem_blockquote_unwrapped"] is True


def test_bare_identifier_headings_are_entries_and_noise_quote_is_kept(client):
    """只有标识符、没有文档标题时，标题本身仍是卷名；非整段引用不清洗。"""
    source = """### A1-2 ｜ 第 1 章 第 2 题

**题干**：

> 求 $1+2$。
普通一行不在引用里。
"""
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload(source))
        question = result["questions"][0]
        assert question["paper_title"] == "A1-2 ｜ 第 1 章 第 2 题"
        assert not question["source_map"].get("entry_title")
        # 引用不完整时不猜清洗：`>` 保留，避免把正文误当题面或反之。
        assert "> 求 $1+2$。" in question["raw_stem"]


def test_report_without_recoverable_stem_still_fails_after_label_support(client):
    source = "# 候选题评价 #1\n\n**题型（评价者归档）**：证明题\n\n- A 独立解答：6\n- H 结论：可考虑入库\n"
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload(source))
        assert result["counts"]["parse_failed"] == 1
        assert result["questions"][0]["source_map"].get("question")

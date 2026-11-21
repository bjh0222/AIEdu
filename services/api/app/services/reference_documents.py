"""参考材料的逻辑文档解析（02 切片 1）：一份物理文件 → N 份逻辑试卷。

为什么需要它：2026-11-07 线代三册合辑（一份上传文件、三个逻辑试卷、明确编号
8+8+9=25 题）被旧统计器数成 16 题、解答题 10+填空题 6——第二册没有题型标题，
代码沿用了第一册末尾的「解答题」状态；第三册用 **一.** 与 **(1)** 题号，旧正则
只认阿拉伯题号；「原卷缺第 5 题」「考后回忆版」也没有成为疑点。

本模块是**纯函数**：文本进、结构出，零模型调用、不碰数据库；跨文档状态显式重置。
统计口径（红线）：
- 合辑总量（totals.question_count）是「所有逻辑文档识别题数之和」，不再冒充单卷题量；
- 单卷口径的 legacy 适配见 legacy_question_count_summary；
- 只在「明确题目区」（题型标题区 / 卷头自述题量 / 存在作答区提示）识别题号，
  普通编号清单保持 unknown——宁可未知，也不把待办列表当题量；
- 题内 （1）（2） 小问计入所在题的 subquestion_*，不重复计题；但当一个题型区
  的**第一个**题号就是括号形（如第三册证明题区）时，括号号是该区的一级题号。
- 「原卷缺第 N 题」「考后回忆版」「识别说明」「不含答案」等来源自述按疑点记录
  （kind+span+source_ref），是数据，不是执行授权。
"""
from __future__ import annotations

import re
from typing import Any

REFERENCE_PARSER_VERSION = "reference-documents.v1"

# ---------------------------------------------------------------- 行标记正则

# 逻辑文档边界：一/二级标题且标题像「册/卷/套/试卷」；`#` 文件标题不算。
_DOC_HEAD_RE = re.compile(r"^##\s+(.+?)\s*$")
_DOC_TITLE_HINT_RE = re.compile(
    r"第\s*[一二三四五六七八九十\d]+\s*[册套]|历年卷|[上中下]册|试卷|考卷|题目版|合辑|"
    r"Part\s*\d|Paper\s*\d|Exam\s*\d",
    re.I,
)
# 像说明/答案区的标题不是文档边界（例如「## 试卷说明」）
_DOC_TITLE_EXCLUDE_RE = re.compile(r"说明|答案|评分|参考|解析|须知")

# 题型小节标题（沿用旧口径：整行只有题型标题 + 可选括注）
_QUESTION_TYPE_HEAD_RE = re.compile(
    r"^#{1,4}\s*(?:[一二三四五六七八九十\d]+\s*[、.．]\s*)?"
    r"(单项选择|多项选择|多选题|单选题|选择题|判断题|填空题|解答题|证明题|综合题|计算题|"
    r"简答题|论述题|名词解释|应用题|作图题|实验题|翻译题|完形填空|阅读理解)\s*(?:[（(].{0,12}[）)])?\s*$"
)

# 一级题号：阿拉伯 **1.** / 1.（沿用旧口径）
_QUESTION_NUMBER_RE = re.compile(r"^\s*(?:\*\*)?(\d{1,3})\s*[.、．](?:\*\*)?\s*\S")
# 一级题号：中文序号，仅认加粗形态（**一.**），裸「一、」是标题/枚举惯例不认
_CHINESE_QUESTION_RE = re.compile(r"^\s*\*\*\s*([一二三四五六七八九十]+)\s*[.．]\s*\*\*\s*\S")
# 括号题号：（1）/(1)/**(1)**；是一级题还是小问由区上下文决定
_PAREN_QUESTION_RE = re.compile(r"^\s*(?:\*\*)?\s*[（(]\s*(\d{1,2})\s*[)）]\s*(?:\*\*)?\s*\S")
# 「第N题」标题（无歧义，任何位置都认）
_NTH_QUESTION_RE = re.compile(r"^\s*#{0,4}\s*第\s*([0-9一二三四五六七八九十百]+)\s*题")

# 明确题目区的信号：卷头自述题量 / 作答区提示
_DECLARED_TOTAL_RE = re.compile(r"(?:共|合计)\s*([0-9一二两三四五六七八九十]{1,3})\s*(项|道?题)")
_ANSWER_AREA_RE = re.compile(r"^\s*\*{0,2}\s*解\s*[：:]\s*\*{0,2}\s*$|作答区|「解：」")

# 卷头自述的题型计数 / 分值（best-effort，只用于交叉核对，不作为识别题号）
_DECLARED_TYPE_COUNT_RE = re.compile(
    r"((?:单项选择|多项选择|单选|多选|选择|填空|解答|证明|判断|计算|综合|简答|论述|应用)题)"
    r"[^，,；;）()]{0,6}?"
    r"(?:([0-9]{1,3})\s*分)?"
    r"[^，,；;]{0,4}?"
    r"[（(]?\s*共?\s*([0-9]{1,3})\s*题"
)
_DECLARED_TYPE_SCORE_RE = re.compile(
    r"((?:单项选择|多项选择|单选|多选|选择|填空|解答|证明|判断|计算|综合|简答|论述|应用)题)"
    r"\s*([0-9]{1,3})\s*分"
)

# 来源疑点（自述性文本 = 数据；kind 供上层与 03 候选桥消费）
_MISSING_NO_RE = re.compile(r"缺第\s*([0-9一二三四五六七八九十]+)\s*题|题号[^。；\n]{0,10}跳[到至]")
_RECALLED_RE = re.compile(r"考后回忆版|回忆版|据回忆整理")
_RECOGNITION_RE = re.compile(r"识别说明|图像识别|照片识别|两次识别")
_NO_ANSWER_RE = re.compile(r"不含答案|未附标准答案|未附参考答案|答案部分已[^。]{0,10}剔除|未印.*答案")

_CN_NUM = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(token: str) -> int | None:
    token = str(token).strip()
    if token.isdigit():
        return int(token)
    if len(token) == 1:
        return _CN_NUM.get(token)
    if token == "十二":
        return 12
    if token.startswith("十") and len(token) == 2:
        unit = _CN_NUM.get(token[1])
        return 10 + unit if unit else None
    if token.endswith("十") and len(token) == 2:
        tens = _CN_NUM.get(token[0])
        return tens * 10 if tens else None
    return None


def _span(start_line: int, end_line: int, offsets: list[int], lines: list[str]) -> dict:
    """1-based 闭区间行跨度 + 字符跨度（start_line/end_line 均含）。"""
    start_idx = max(0, start_line - 1)
    end_idx = min(end_line, len(lines)) - 1
    start_char = offsets[start_idx]
    end_char = offsets[end_idx] + len(lines[end_idx]) if end_idx >= 0 else start_char
    return {"start_line": start_line, "end_line": end_line,
            "start_char": start_char, "end_char": end_char}


def _excerpt(line: str, limit: int = 60) -> str:
    return re.sub(r"\s+", " ", line).strip()[:limit]


# ---------------------------------------------------------------- 主解析

def parse_reference_documents(text: str, source_ref: dict | None = None) -> dict:
    """解析一份物理材料的逻辑试卷结构。

    参数
    ----
    text: 材料正文（已入库文本）。
    source_ref: 来源引用（material_ref 形态：kind/id/version/hash/location），
                原样嵌入结果，疑点与文档都可定位回来源。

    返回结构（字段含义见模块 docstring；测试 test_tsem_reference_documents.py 钉死）：
    documents[] 每项含 index/title/span/question_count/count_basis/question_spans/
    type_sections/type_distribution/type_distribution_basis/declared_totals/anomalies。
    """
    text = str(text or "")
    lines = text.splitlines()
    offsets: list[int] = []
    acc = 0
    for line in lines:
        offsets.append(acc)
        acc += len(line) + 1
    total_lines = len(lines)
    ref = dict(source_ref or {})

    # ---- 定位文档边界（## + 册/卷/套 样标题）；没有则整份材料是一个逻辑文档
    doc_starts: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, 1):
        head = _DOC_HEAD_RE.match(line.strip())
        if not head:
            continue
        title = head.group(1).strip()
        if _DOC_TITLE_HINT_RE.search(title) and not _DOC_TITLE_EXCLUDE_RE.search(title):
            doc_starts.append((idx, title))
    if doc_starts:
        preamble_lines = doc_starts[0][0] - 1
    else:
        preamble_lines = 0

    # ---- 单遍扫描：文档 / 题型区 / 题目 / 小问 / 疑点
    docs: list[dict] = []
    cur_doc: dict | None = None
    cur_section: str = ""
    section_numbering: str | None = None   # None | "primary" | "paren"
    cur_question: dict | None = None
    question_area = False                  # 当前文档是否处于「明确题目区」

    # 没有 ## 文档边界时：整份材料视为一个逻辑文档（先建好再扫描），
    # 题目区信号只决定是否在文档内识别题号——不把序言/信号行误当新文档起点。
    if not doc_starts:
        cur_doc = {
            "index": 1,
            "title": _file_title(lines) or "整份材料",
            "span": {"start_line": 1, "end_line": total_lines,
                     "start_char": 0, "end_char": acc},
            "_questions": [],
            "_sections": [],
            "_header_lines": [],
            "_anomaly_hits": [],
        }
        header_text = "\n".join(lines[:12])
        if _ANSWER_AREA_RE.search(header_text) or _DECLARED_TOTAL_RE.search(header_text):
            question_area = True

    def _close_question(end_line: int) -> None:
        nonlocal cur_question
        if cur_question is not None and cur_doc is not None:
            cur_question["span"]["end_line"] = max(cur_question["span"]["start_line"], end_line - 1)
            cur_doc["_questions"].append(cur_question)
        cur_question = None

    def _close_section() -> None:
        nonlocal cur_section, section_numbering
        cur_section = ""
        section_numbering = None

    def _new_doc(start_line: int, title: str) -> None:
        nonlocal cur_doc, question_area
        question_area = False
        cur_doc = {
            "index": len(docs) + 1,
            "title": title,
            "span": {"start_line": start_line, "end_line": total_lines,
                     "start_char": offsets[start_line - 1], "end_char": acc},
            "_questions": [],
            "_sections": [],
            "_header_lines": [],
            "_anomaly_hits": [],
        }

    anomaly_defs = (
        ("missing_question_number", _MISSING_NO_RE),
        ("recalled_version", _RECALLED_RE),
        ("recognition_note", _RECOGNITION_RE),
        ("answer_absent", _NO_ANSWER_RE),
    )

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # 1) 逻辑文档边界
        boundary = None
        if stripped.startswith("##"):
            head = _DOC_HEAD_RE.match(stripped)
            if head:
                title = head.group(1).strip()
                if (_DOC_TITLE_HINT_RE.search(title)
                        and not _DOC_TITLE_EXCLUDE_RE.search(title)):
                    boundary = title
        if boundary is not None:
            _close_question(lineno - 1)
            if cur_doc is not None:
                cur_doc["span"]["end_line"] = lineno - 1
                cur_doc["span"]["end_char"] = offsets[lineno - 2] + len(lines[lineno - 2]) if lineno >= 2 else 0
                docs.append(cur_doc)
            _close_section()
            _new_doc(lineno, boundary)
            continue

        # 2) 题型小节标题
        type_match = _QUESTION_TYPE_HEAD_RE.match(stripped)
        if type_match:
            _close_question(lineno - 1)
            _close_section()
            if cur_doc is None:
                _new_doc(1, _file_title(lines) or "整份材料")
            cur_section = type_match.group(1)
            section_numbering = None
            question_area = True
            cur_doc["_sections"].append({
                "name": cur_section,
                "span": {"start_line": lineno, "end_line": total_lines,
                         "start_char": offsets[lineno - 1], "end_char": acc},
                "_question_count": 0,
            })
            continue

        # 3) 题号识别（先判一级，再判小问）
        primary = None      # (number, numbering)
        arabic = _QUESTION_NUMBER_RE.match(line)
        chinese = _CHINESE_QUESTION_RE.match(line)
        nth = _NTH_QUESTION_RE.match(stripped)
        if arabic and question_area:
            primary = (arabic.group(1), "arabic")
        elif chinese and question_area:
            primary = (chinese.group(1), "chinese")
        elif nth:
            primary = (nth.group(1), "nth")
        paren = _PAREN_QUESTION_RE.match(line) if not primary else None

        if primary or paren:
            if cur_doc is None:
                # 序言里的题号样式不属于任何逻辑文档：不产题、不留悬空跨度
                continue
            if primary is None and paren is not None:
                # 括号号：区上下文决定是一级题还是小问
                if section_numbering == "primary":
                    if cur_question is not None:
                        cur_question["subquestion_count"] += 1
                        cur_question["subquestion_spans"].append({
                            "number": paren.group(1),
                            "span": {"start_line": lineno, "end_line": lineno,
                                     "start_char": offsets[lineno - 1],
                                     "end_char": offsets[lineno - 1] + len(line)},
                        })
                    continue
                # 区内还没有任何一级题号 → 括号号是该区的一级题号（保留括号形态）
                primary = (f"({paren.group(1)})", "parenthesized")
                section_numbering = "paren"
            elif primary is not None:
                if section_numbering is None:
                    section_numbering = "primary"
            _close_question(lineno - 1)
            cur_question = {
                "number": primary[0],
                "numbering": primary[1],
                "type_section": cur_section,
                "span": {"start_line": lineno, "end_line": lineno,
                         "start_char": offsets[lineno - 1],
                         "end_char": offsets[lineno - 1] + len(line)},
                "subquestion_count": 0,
                "subquestion_spans": [],
            }
            if cur_section and cur_doc["_sections"]:
                cur_doc["_sections"][-1]["_question_count"] += 1
            continue

        # 4) 题目区信号与卷头行：作答区提示 / 卷头自述题量都是「明确题目区」的信号；
        #    自述行本身要先记入卷头（它同时是题量声明，不能因切换状态而漏记）
        if cur_doc is not None:
            if not question_area and (_ANSWER_AREA_RE.search(line)
                                      or _DECLARED_TOTAL_RE.search(line)):
                question_area = True
            if cur_question is None and not cur_doc["_questions"] \
                    and not cur_doc["_sections"]:
                cur_doc["_header_lines"].append(lineno)

        # 5) 疑点行（文件级与文档级都记录，最后按 kind+文本去重）
        for kind, pattern in anomaly_defs:
            hit = pattern.search(line)
            if hit:
                owner = cur_doc
                if owner is not None:
                    owner["_anomaly_hits"].append({
                        "kind": kind,
                        "text": hit.group(0),
                        "line": lineno,
                        "excerpt": _excerpt(line),
                    })
                else:
                    docs.append({  # 临时占位：preamble 疑点挂在 file 层
                        "_file_anomaly": True, "kind": kind, "text": hit.group(0),
                        "line": lineno, "excerpt": _excerpt(line),
                    })

    # 收尾最后一个文档
    _close_question(total_lines)
    if cur_doc is not None:
        docs.append(cur_doc)

    # 分离文件级疑点占位与真文档
    file_anomalies = [d for d in docs if d.get("_file_anomaly")]
    real_docs = [d for d in docs if not d.get("_file_anomaly")]

    # ---- 文档定稿：统计、题型分布、自述交叉核对、疑点去重
    documents: list[dict] = []
    for doc in real_docs:
        documents.append(_finalize_document(doc, ref, lines, offsets))

    documents.sort(key=lambda d: d["span"]["start_line"])
    for i, doc in enumerate(documents, 1):
        doc["index"] = i

    totals = _finalize_totals(documents, file_anomalies, ref, total_lines, preamble_lines)
    return {
        "parser_version": REFERENCE_PARSER_VERSION,
        "source_ref": ref,
        "physical_source_count": 1,
        "logical_document_count": len(documents),
        "documents": documents,
        "totals": totals,
    }


def _file_title(lines: list[str]) -> str:
    for line in lines[:5]:
        head = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if head:
            return head.group(1)
    return ""


# ---------------------------------------------------------------- 文档定稿

def _finalize_document(doc: dict, ref: dict, lines: list[str], offsets: list[int]) -> dict:
    questions = doc["_questions"]
    for question in questions:
        end = question["span"]["end_line"]
        question["span"]["end_char"] = offsets[end - 1] + len(lines[end - 1]) if end >= 1 else 0
    # 题型区跨度：从本区标题行到下一个区/文档边界前
    sections = doc["_sections"]
    for i, section in enumerate(sections):
        next_start = sections[i + 1]["span"]["start_line"] if i + 1 < len(sections) \
            else doc["span"]["end_line"] + 1
        section["span"]["end_line"] = min(next_start - 1, doc["span"]["end_line"])
        section["span"]["end_char"] = offsets[section["span"]["end_line"] - 1] + \
            len(lines[section["span"]["end_line"] - 1])
        section["question_count"] = section.pop("_question_count")

    declared = _parse_declared(doc["_header_lines"], lines)
    doc_span = doc["span"]
    anomalies = _dedupe_anomalies(doc.pop("_anomaly_hits"), ref, doc_span)

    question_count = len(questions) if questions else None
    count_basis = "numbered_questions" if questions else (
        "declared_only" if declared.get("total_count") else "unknown")
    if count_basis == "declared_only":
        question_count = declared.get("total_count")

    type_distribution: list[dict] = []
    for section in sections:
        if section["question_count"]:
            type_distribution.append({
                "name": section["name"], "count": section["question_count"], "basis": "direct"})
        elif (declared.get("by_type") or {}).get(section["name"], {}).get("count"):
            type_distribution.append({
                "name": section["name"],
                "count": declared["by_type"][section["name"]]["count"], "basis": "declared"})
    distribution_basis = "direct" if any(t["basis"] == "direct" for t in type_distribution) else (
        "declared" if type_distribution else "unknown")

    # 自述与识别交叉核对：题数不一致是疑点
    identified = len(questions)
    declared_total = declared.get("total_count")
    if declared_total is not None and identified and declared_total != identified:
        anomalies.append({
            "kind": "declared_mismatch",
            "message": f"识别 {identified} 题，卷头自述共 {declared_total} 题；以逐题识别为准并保留差异。",
            "source_ref": dict(ref),
            "span": dict(doc_span),
        })

    return {
        "index": doc["index"],
        "title": doc["title"],
        "span": {
            "start_line": doc_span["start_line"], "end_line": doc_span["end_line"],
            "start_char": doc_span["start_char"], "end_char": doc_span["end_char"],
        },
        "question_count": question_count,
        "count_basis": count_basis,
        "question_spans": questions,
        "type_sections": [
            {k: v for k, v in section.items() if k != "_question_count"} for section in sections
        ],
        "type_distribution": type_distribution,
        "type_distribution_basis": distribution_basis,
        "declared_totals": {
            "total_count": declared_total,
            "by_type": [
                {"name": name, **({"count": v["count"]} if v.get("count") else {}),
                 **({"score": v["score"]} if v.get("score") else {})}
                for name, v in (declared.get("by_type") or {}).items()
            ],
        },
        "anomalies": anomalies,
    }


def _parse_declared(header_lines: list[int], lines: list[str]) -> dict:
    """卷头自述（共 N 项/题、题型计数/分值）——best-effort，只作交叉核对。"""
    header_text = "\n".join(lines[i - 1] for i in header_lines[:12])
    by_type: dict[str, dict] = {}
    for match in _DECLARED_TYPE_COUNT_RE.finditer(header_text):
        entry = by_type.setdefault(match.group(1), {})
        entry["count"] = int(match.group(3))
        if match.group(2):
            entry["score"] = int(match.group(2))
    for match in _DECLARED_TYPE_SCORE_RE.finditer(header_text):
        entry = by_type.setdefault(match.group(1), {})
        if "score" not in entry:
            entry["score"] = int(match.group(2))
    total = None
    for match in _DECLARED_TOTAL_RE.finditer(header_text):
        prefix = header_text[max(0, match.start() - 1):match.start()]
        value = _cn_to_int(match.group(1))
        if value is None:
            continue
        if match.group(2) == "项" or prefix not in "（(":
            total = value
            break
    return {"total_count": total, "by_type": by_type}


def _dedupe_anomalies(hits: list[dict], ref: dict, doc_span: dict) -> list[dict]:
    """同一疑点（kind+命中文本相同）只保留首处，但保持可定位（span+source_ref）。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for hit in hits:
        key = (hit["kind"], hit["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "kind": hit["kind"],
            "message": hit["excerpt"],
            "matched_text": hit["text"],
            "source_ref": dict(ref),
            "span": {"start_line": hit["line"], "end_line": hit["line"],
                     "start_char": 0, "end_char": 0},
            "document_span": dict(doc_span),
        })
    return out


# ---------------------------------------------------------------- 汇总定稿

def _finalize_totals(documents: list[dict], file_anomalies: list[dict], ref: dict,
                     total_lines: int, preamble_lines: int) -> dict:
    per_doc = [d["question_count"] for d in documents]
    identified_total = sum(c for c in per_doc if isinstance(c, int))
    # 每份文档的自述口径：卷头总数优先，缺省时用题型自述合计回退
    per_doc_declared = _per_doc_declared(documents)
    declared_total = (sum(int(v) for v in per_doc_declared)
                      if per_doc_declared and all(v is not None for v in per_doc_declared)
                      else None)
    alignment = _declared_alignment(documents, per_doc, per_doc_declared)

    bases = {d["count_basis"] for d in documents}
    if bases == {"numbered_questions"}:
        count_basis = "numbered_questions"
    elif "numbered_questions" in bases or "declared_only" in bases:
        count_basis = "mixed"
    elif bases == {"declared_only"}:
        count_basis = "declared_only"
    else:
        count_basis = "unknown"

    subquestions_seen = sum(
        q["subquestion_count"] for d in documents for q in d["question_spans"])

    doc_anomalies = [a for d in documents for a in d["anomalies"]]
    file_level = [
        {"kind": hit["kind"], "message": hit["excerpt"], "matched_text": hit["text"],
         "source_ref": dict(ref),
         "span": {"start_line": hit["line"], "end_line": hit["line"],
                  "start_char": 0, "end_char": 0}}
        for hit in file_anomalies
    ]
    unparsed = preamble_lines
    return {
        "question_count": identified_total if documents else None,
        "count_basis": count_basis,
        "per_document_counts": per_doc,
        "per_document_uniform": bool(documents) and len(set(
            c for c in per_doc if isinstance(c, int))) <= 1,
        "range_summary": _range_summary(per_doc),
        "parse_coverage": {
            "documents_identified": len(documents),
            "questions_identified": identified_total if documents else 0,
            "declared_question_total": declared_total,
            "declared_alignment": alignment,
            "subquestions_seen": subquestions_seen,
            "preamble_lines": preamble_lines,
            "total_lines": total_lines,
            "unparsed_line_ratio": round(unparsed / total_lines, 4) if total_lines else 0.0,
        },
        "anomalies": doc_anomalies + file_level,
    }


def _per_doc_declared(documents: list[dict]) -> list[int | None]:
    """每份文档的自述口径：卷头总数优先，缺省时用题型自述合计回退。"""
    per_doc_declared: list[int | None] = []
    for d in documents:
        declared = d.get("declared_totals") or {}
        total = declared.get("total_count")
        if total is None and declared.get("by_type"):
            counts = [t["count"] for t in declared["by_type"] if t.get("count")]
            total = sum(counts) if len(counts) == len(declared["by_type"]) else None
        per_doc_declared.append(total)
    return per_doc_declared


def _declared_alignment(documents: list[dict], per_doc: list[int | None],
                        per_doc_declared: list[int | None]) -> str:
    if not any(v is not None for v in per_doc_declared):
        return "no_declaration"
    if all(v is not None for v in per_doc_declared):
        return "match" if all(c == v for c, v in zip(per_doc, per_doc_declared)) else "mismatch"
    comparable = [(c, v) for c, v in zip(per_doc, per_doc_declared) if v is not None]
    if all(c == v for c, v in comparable):
        return "partial"
    return "mismatch"


def aggregate_reference_documents(entries: list[dict]) -> dict:
    """把多份材料的解析结果聚合为一个合辑视图（TaskContext.extensions 用）。

    entries: [{"material_id": str, "filename": str, "parsed": parse_reference_documents 输出}]
    聚合只做跨材料求和与口径合并，不做任何二次推断；空材料不产生文档。
    """
    documents: list[dict] = []
    source_count = 0
    total_lines = 0
    preamble_lines = 0
    subquestions_seen = 0
    anomalies: list[dict] = []
    for entry in entries:
        parsed = entry.get("parsed")
        if not parsed:
            continue
        source_count += 1
        totals = parsed.get("totals", {})
        coverage = totals.get("parse_coverage", {})
        total_lines += int(coverage.get("total_lines") or 0)
        preamble_lines += int(coverage.get("preamble_lines") or 0)
        subquestions_seen += int(coverage.get("subquestions_seen") or 0)
        anomalies.extend(totals.get("anomalies", []))
        for doc in parsed.get("documents", []):
            documents.append({
                "material_id": str(entry.get("material_id") or ""),
                "filename": str(entry.get("filename") or ""),
                **doc,
            })
    per_doc = [d["question_count"] for d in documents]
    identified_total = sum(c for c in per_doc if isinstance(c, int))
    per_doc_declared = _per_doc_declared(documents)
    declared_total = (sum(int(v) for v in per_doc_declared)
                      if per_doc_declared and all(v is not None for v in per_doc_declared)
                      else None)
    bases = {d["count_basis"] for d in documents}
    if not documents:
        count_basis = "unknown"
    elif bases == {"numbered_questions"}:
        count_basis = "numbered_questions"
    elif "numbered_questions" in bases or "declared_only" in bases:
        count_basis = "mixed"
    elif bases == {"declared_only"}:
        count_basis = "declared_only"
    else:
        count_basis = "unknown"
    return {
        "parser_version": REFERENCE_PARSER_VERSION,
        "source_count": source_count,
        "logical_document_count": len(documents),
        "documents": documents,
        "totals": {
            "question_count": identified_total if documents else None,
            "count_basis": count_basis,
            "per_document_counts": per_doc,
            "per_document_uniform": bool(documents) and len(set(
                c for c in per_doc if isinstance(c, int))) <= 1,
            "range_summary": _range_summary(per_doc),
            "parse_coverage": {
                "documents_identified": len(documents),
                "questions_identified": identified_total if documents else 0,
                "declared_question_total": declared_total,
                "declared_alignment": _declared_alignment(documents, per_doc, per_doc_declared),
                "subquestions_seen": subquestions_seen,
                "preamble_lines": preamble_lines,
                "total_lines": total_lines,
                "unparsed_line_ratio": round(preamble_lines / total_lines, 4) if total_lines else 0.0,
            },
            "anomalies": anomalies,
        },
    }


# ---------------------------------------------------------------- 按用途投影（02-7）

def project_for_stage(
    parsed: dict | None, bodies: dict[str, str], *, stage: str,
    unit_points: list[str] | None = None, budget_chars: int = 12000,
) -> dict:
    """把参考材料按消费阶段投影（纯函数；reading 原有流程不经过本函数）。

    - stage="planning"：完整可信统计（每卷题量/题型分布/疑点）+ 每卷首题完整片段；
    - stage="unit"：与本单元知识点相关的**完整题目片段** + 跨卷共同分布摘要 + 来源边界；
    - 小材料（单文档正文 ≤ budget）全量；长材料按预算选完整题目片段，
      硬约束性内容（疑点/来源边界）不参与裁剪；超预算的部分显式报告 dropped。
    返回 {"text", "basis": full|fragments|none, "dropped": [str], "distribution_summary": str}。
    """
    documents = list((parsed or {}).get("documents") or [])
    if not documents:
        return {"text": "", "basis": "none", "dropped": [],
                "distribution_summary": ""}
    # 直接解析输出（无 material_id）回退到 source_ref 的 id / filename 定位
    ref = (parsed or {}).get("source_ref") or {}
    fallback_keys = [str(ref.get("id") or ""),
                     str(ref.get("location") or "").removeprefix("filename:")]
    if stage == "planning":
        return _project_planning(documents, bodies, budget_chars, fallback_keys)
    return _project_unit(documents, bodies, unit_points or [], budget_chars, fallback_keys)


def question_span_text(body: str, span: dict) -> str:
    """按解析器输出的绝对行号跨度取题面原文（03 候选桥复用；不截断、不改写）。"""
    return _question_text(str(body or ""), {"span": span or {}})


def _raw_body(document: dict, bodies: dict[str, str], fallback_keys: list[str]) -> str:
    material_id = str(document.get("material_id") or "")
    filename = str(document.get("filename") or "")
    for key in (material_id, filename, *fallback_keys):
        if key and key in bodies:
            return bodies[key]
    return ""


def _document_text(document: dict, bodies: dict[str, str], fallback_keys: list[str]) -> str:
    body = _raw_body(document, bodies, fallback_keys)
    if not body:
        return ""
    span = document.get("span") or {}
    lines = body.splitlines()
    start = max(0, int(span.get("start_line", 1)) - 1)
    end = min(len(lines), int(span.get("end_line", len(lines))))
    return "\n".join(lines[start:end])


def _question_text(body: str, question: dict) -> str:
    """题跨度基于**全材料**绝对行号（与解析器输出一致），不得二次切片。"""
    lines = body.splitlines()
    start = max(0, int((question.get("span") or {}).get("start_line", 1)) - 1)
    end = min(len(lines), int((question.get("span") or {}).get("end_line", start)))
    return "\n".join(lines[start:end])


def _distribution_summary(documents: list[dict]) -> str:
    parts: list[str] = []
    for document in documents:
        count = document.get("question_count")
        if count is None:
            parts.append(f"「{document.get('title', '')[:24]}」题量未知")
            continue
        dist = "、".join(
            f"{t['name']} {t['count']}" for t in document.get("type_distribution", []))
        parts.append(f"「{document.get('title', '')[:24]}」{count} 题"
                     + (f"（{dist}）" if dist else "（未提供题型划分）"))
    return "；".join(parts)


def _source_boundary(documents: list[dict]) -> str:
    kinds = {a["kind"] for d in documents for a in d.get("anomalies", [])}
    notes = ["来源边界：参考材料仅作分布、风格与范围证据，不逐题复述。"]
    if "missing_question_number" in kinds:
        notes.append("来源存在缺题/编号跳空：题量与分布以解析统计为准。")
    if "recalled_version" in kinds:
        notes.append("来源为考后回忆版：题面细节可能与原卷有出入，不作为权威答案依据。")
    if "answer_absent" in kinds:
        notes.append("来源不含答案：不得虚构来源答案或声称已对标来源答案。")
    return " ".join(notes)


def _project_planning(documents: list[dict], bodies: dict[str, str], budget: int,
                      fallback_keys: list[str]) -> dict:
    parts: list[str] = [f"参考材料统计（解析器证据）：{_distribution_summary(documents)}。",
                        _source_boundary(documents)]
    dropped: list[str] = []
    basis = "fragments"
    for document in documents:
        body = _document_text(document, bodies, fallback_keys)
        if not body:
            continue
        if len(body) <= budget // max(1, len(documents)):
            parts.append(f"—— {document.get('title', '')[:24]} 全文 ——\n{body}")
            continue
        questions = document.get("question_spans") or []
        head = questions[0] if questions else None
        if head:
            sample = _question_text(body, head)
            parts.append(f"—— {document.get('title', '')[:24]} 首题片段 ——\n{sample}")
        else:
            parts.append(f"—— {document.get('title', '')[:24]} 开头片段 ——\n{body[:budget // max(1, len(documents))]}")
        dropped.append(f"「{document.get('title', '')[:24]}」超出规划预算：仅取统计与首题片段")
    if sum(len(p) for p in parts) <= budget:
        basis = "full" if not dropped else "fragments"
    text = "\n\n".join(parts)
    return {"text": text, "basis": basis, "dropped": dropped,
            "distribution_summary": _distribution_summary(documents)}


def _project_unit(documents: list[dict], bodies: dict[str, str],
                  unit_points: list[str], budget: int, fallback_keys: list[str]) -> dict:
    points = [str(p) for p in unit_points if str(p).strip()]
    fixed: list[str] = [f"参考分布摘要（跨卷共同特征）：{_distribution_summary(documents)}。",
                        _source_boundary(documents)]
    parts: list[str] = list(fixed)
    dropped: list[str] = []
    # 摘要与来源边界是硬信息，不参与裁剪：预算先扣除固定头部
    available = max(0, budget - len("\n\n".join(fixed)))
    selected = 0
    full_threshold = min(600, max(available // max(1, len(documents)), 200))
    for document in documents:
        body = _document_text(document, bodies, fallback_keys)
        if not body:
            continue
        # 题跨度是全材料绝对行号：小问提取必须用 raw 全文，不得用文档切片二次切
        raw = _raw_body(document, bodies, fallback_keys)
        title = document.get("title", "")[:24]
        if len(body) <= full_threshold and selected + len(body) <= available:
            parts.append(f"—— {title} 全文 ——\n{body}")
            selected += len(body)
            continue
        relevant: list[str] = []
        for question in document.get("question_spans") or []:
            text = _question_text(raw, question)
            if not points or any(p in text for p in points):
                relevant.append(text)
        if not relevant:
            dropped.append(
                f"「{title}」无与本单元知识点相关的片段：不注入，避免借入无关参考")
            continue
        injected = 0
        for text in relevant:
            if selected + len(text) > available:
                remaining = len(relevant) - injected
                dropped.append(
                    f"「{title}」相关片段超预算：剩余 {remaining} 个完整片段未注入"
                    "（不截半个题；完整片段优先）")
                break
            parts.append(f"—— {title} 相关片段 ——\n{text}")
            selected += len(text)
            injected += 1
    if not dropped and selected == 0 and available == 0:
        dropped.append("预算被固定统计占满：题目片段未注入（统计与来源边界已保留）")
    return {
        "text": "\n\n".join(parts),
        "basis": "full" if not dropped and any("全文" in p for p in parts) else
                 ("none" if selected == 0 else "fragments"),
        "dropped": dropped,
        "distribution_summary": _distribution_summary(documents),
    }


def _range_summary(per_doc: list[int | None]) -> str:
    known = [c for c in per_doc if isinstance(c, int)]
    if not known:
        return "各逻辑卷题量均未能识别，不编造题数"
    if len(known) < len(per_doc):
        return "部分逻辑卷题量未能识别：" + "、".join(
            f"第{i + 1}份 {c if c is not None else '未知'}" for i, c in enumerate(per_doc))
    if len(set(known)) == 1:
        return f"共 {len(known)} 份逻辑卷，每份 {known[0]} 题"
    return "共 {} 份逻辑卷，题量分别为 {}".format(
        len(known), "/".join(str(c) for c in known))


# ---------------------------------------------------------------- legacy 适配语义

def legacy_question_count_summary(parsed: dict) -> dict:
    """legacy ``reference_profile.question_count`` 的适配语义（文档化，供既有消费者过渡）。

    该字段历史上同时表示「合辑总量」与「典型单卷题量」——三册合辑把 25 当成共同
    题量就是它的直接后果。适配规则：
    - 语义固定为「典型单份逻辑卷的题量」（typical_single_document）；
    - 各逻辑卷题量一致 → 该值；题量不一致或未识别 → None + range_summary；
    - 合辑总量永远在 totals.question_count，不进本字段。
    """
    per_doc = [d.get("question_count") for d in parsed.get("documents", [])]
    known = [c for c in per_doc if isinstance(c, int)]
    if known and len(known) == len(per_doc) and len(set(known)) == 1:
        value: int | None = known[0]
    else:
        value = None
    return {
        "question_count": value,
        "semantic": "typical_single_document",
        "range_summary": _range_summary(per_doc),
        "total_count": parsed.get("totals", {}).get("question_count"),
        "per_document_counts": per_doc,
        "logical_document_count": parsed.get("logical_document_count", 0),
        "parser_version": parsed.get("parser_version", REFERENCE_PARSER_VERSION),
    }


__all__ = [
    "REFERENCE_PARSER_VERSION",
    "aggregate_reference_documents",
    "legacy_question_count_summary",
    "parse_reference_documents",
    "project_for_stage",
    "question_span_text",
]

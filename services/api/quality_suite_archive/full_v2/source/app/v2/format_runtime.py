"""Deterministic adapters for inherited format contracts; source text stays immutable."""
from __future__ import annotations

import re

from .contracts import DomainError, sha

EXERCISE_LAYOUT = "exercise.chapter.v1"
READING_LAYOUT = "reading.chapter.v1"
QUESTION_TYPES = ("判断题", "选择题", "基础概念题", "变式题", "拔高题", "综合题")
_CHAPTER = re.compile(r"^(#{1,6})[ \t]+(第\s*([零〇一二两三四五六七八九十百\d]+)\s*[章篇]\s*[^\n]*)$", re.M)


def format_layout(snapshot):
    structure = snapshot.get("content_structure", "")
    if snapshot.get("scene") == "exercise" and all(x in structure for x in
            ("### 一、易错辨析", "### 二、训练", "### 参考答案")):
        return EXERCISE_LAYOUT
    if snapshot.get("scene") == "reading_guide" and all(x in structure for x in
            ("本章概要", "深度导读", "全书认知地图", "阅读路线")):
        return READING_LAYOUT
    return None


def chapter_ranges(text):
    """Only real outermost Markdown chapter headings, never a bold TOC or fenced sample.

    Offsets are UTF-8 bytes. Unassigned preface bytes are retained as shared context.
    A heading-only TOC is not a chapter body; ambiguous repeated chapters fail closed.
    """
    fences, offset, excluded = None, 0, []
    for line in text.splitlines(keepends=True):
        fence = re.match(r"\s*(`{3,}|~{3,})", line)
        if fences or fence:
            excluded.append((offset, offset+len(line)))
        if fence:
            token = fence[1]
            if fences is None:
                fences = token
            elif token[0] == fences[0] and len(token) >= len(fences):
                fences = None
        offset += len(line)
    headings = [m for m in _CHAPTER.finditer(text) if not any(a <= m.start() < b for a,b in excluded)]
    if not headings:
        return []
    level = min(len(m[1]) for m in headings)
    headings = [m for m in headings if len(m[1]) == level]
    result, seen = [], set()
    for index, match in enumerate(headings):
        end = headings[index+1].start() if index+1 < len(headings) else len(text)
        body = text[match.end():end]
        meaningful = [line for line in body.splitlines() if line.strip() and
            not re.match(r"^\s*(?:#{1,6}\s|[-*]\s+(?:第|习题\s*\d)|---+\s*$|\*\*第)", line)]
        if not meaningful:
            continue
        title = re.sub(r"\s*(?:[（(](?:习题|练习|原文|节选)[）)]|\.{2,}\s*\d+|\s+\d+)\s*$", "", match[2].strip())
        marker = re.sub(r"\s+", "", title.split("章")[0].split("篇")[0])
        if marker in seen:
            raise DomainError("MATERIAL_CHAPTER_AMBIGUOUS", 422)
        seen.add(marker)
        sections = re.findall(r"^#{%d,6}\s+([^\n]+)" % (level+1), body, flags=re.M) if level < 6 else []
        result.append({"start_byte": len(text[:match.start()].encode()), "end_byte": len(text[:end].encode()),
                       "chapter_title": title, "section_titles": list(dict.fromkeys(s.strip() for s in sections))})
    return result


def chapter_groups(chunks):
    groups = {}
    shared = [c["chunk_id"] for c in chunks if not c.get("chapter_title")]
    for chunk in chunks:
        title = chunk.get("chapter_title")
        if not title:
            continue
        key = (chunk["source_id"], title)
        group = groups.setdefault(key, {"title": title, "chunk_ids": list(shared), "source_sections": []})
        group["chunk_ids"].append(chunk["chunk_id"])
        group["source_sections"] = list(dict.fromkeys(group["source_sections"]+chunk.get("section_titles", [])))
    return list(groups.values())


def reading_digests(upstream, total_chars=60000):
    """Keep every upstream chapter, with explicit bounded excerpts (not full book text)."""
    allowance = max(1, total_chars // max(1, len(upstream)))
    result = []
    for unit in upstream:
        body = unit["body"]
        summary_heading = unit.get("summary_heading") or "本章概要"
        summary = re.search(r"(?ms)^### " + re.escape(summary_heading) + r"\s*\n(.*?)(?=^### |\Z)", body)
        argument = re.search(r"(?ms)^#### 论证结构分析\s*\n(.*?)(?=^#{1,4} |\Z)", body)
        selected = (summary[1].strip() if summary else body)
        if argument:
            selected += "\n\n论证结构分析：\n"+argument[1].strip()
        result.append({"unit_id": unit["unit_id"], "title": unit["title"], "source_hash": sha(body),
            "digest": selected[:allowance], "truncated": len(selected) > allowance,
            "summary_missing": summary is None})
    return result


def section_body(body, title, index=1):
    """Own the H2 title, preserve nested H3+ headings and fenced examples."""
    from ..services.tutorials import _normalize_deliverable_body
    from ..services.format_specs import cn_numeral
    normalized = _normalize_deliverable_body(body, 3)
    return f"## {cn_numeral(index)}、{title}\n\n{normalized.strip()}\n"


def check_layout(unit):
    """Structural evidence only: never asserts semantic source coverage or correctness."""
    body = unit.get("body", "")
    from .adaptive_format import LAYOUT, render, validate_quantity
    if unit.get("format_contract") == LAYOUT:
        if unit.get("items"):
            try:
                validate_quantity(unit["items"], unit)
            except DomainError:
                return False
            return body == render(unit["items"], unit)
        required = unit.get("required_sections", [])
        return (body.startswith("## ") and bool(unit.get("output_design")) and
                all("### " + heading in body for heading in required) and
                (not unit.get("summary_heading") or "### " + unit["summary_heading"] in body))
    if unit.get("format_contract") == EXERCISE_LAYOUT:
        from .items import render_unit_items
        # Frozen versions predate the blank-checkbox and duplicate-label display
        # fixes. Accept only an exact known projection, never fuzzy normalization.
        declared = unit.get("layout_projection_version")
        versions = (1, 2, 3) if declared is None else (declared,)
        return bool(unit.get("items")) and any(type(v) is int and v in (1, 2, 3) and
            body == render_unit_items(unit["items"], unit, projection_version=v) for v in versions)
    if unit.get("format_contract") == READING_LAYOUT:
        if unit["artifact_role"] == "book_level":
            return body.startswith("## ") and "### " in body
        required = ("### 本章概要", "### 深度导读", "**核心问题**", "**核心思想**", "**主要论证**",
                    "**核心概念**", "**本章结论**", "**与其他章的联系**")
        return all(token in body for token in required) and body.index(required[0]) < body.index(required[1])
    return False


def presentation_contract(unit, bindings):
    """Wrapper-owned render policy; never changes the original spec or source."""
    from .adaptive_format import LAYOUT
    if unit.get("format_contract") == LAYOUT:
        if unit.get("content_hash") != sha(unit.get("body", "")) or not check_layout(unit):
            return None
        return {"method_version": LAYOUT, "format_contract": LAYOUT,
            "source_hash": unit["content_hash"], "output_design": unit["output_design"],
            "original_spec_and_body_unchanged": True, "professional_certification": False,
            "rules": {"authority": "complete user request > frozen task-derived design > applicable template defaults",
                "grouping": "use A's actual method/topic groups, not legacy teaching-level headings",
                "form": "enforce the task question forms, never inject compulsory judgement/choice items"}}
    if (unit.get("format_contract") != EXERCISE_LAYOUT or not unit.get("items")
            or unit.get("content_hash") != sha(unit.get("body", "")) or not check_layout(unit)):
        return None
    from .items import render_unit_items
    return {"method_version": "inherited-layout-review.v1", "format_contract": EXERCISE_LAYOUT,
        "source_hash": unit["content_hash"],
        "source_spec_snapshot_hashes": [b.get("snapshot_hash") for b in bindings if b.get("artifact_role") == unit["artifact_role"]],
        "matching_projection_versions": [v for v in (1, 2, 3) if
            unit["body"] == render_unit_items(unit["items"], unit, projection_version=v)],
        "learner_projection_hash": sha(render_unit_items(unit["items"], unit)),
        "rules": {
            "chapter_heading": "System owns the H2 chapter heading; template body headings begin at H3.",
            "checkbox": "Safe editable GFM replaces legacy active HTML and right alignment; historical empty markers are displayed as checkboxes.",
            "answer_label": "Historical duplicate answer/analysis prefixes are normalized in the learner display, including source-highlighted spans.",
            "semantic_scope": "Only wrapper-owned presentation is adapted; task scope, question wording, premises, answers and reasoning remain reviewable.",
        }, "original_spec_and_body_unchanged": True, "professional_certification": False}

"""Coupled question/answer candidates. Rendering is a projection, never a second call."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from uuid import uuid4

from .contracts import DomainError, digest
from .format_runtime import EXERCISE_LAYOUT, QUESTION_TYPES

ITEM_SCHEMA = {"items": [{"stem_markdown": "string", "answer": {"final": "string", "reasoning": "string"},
                          "score": "positive number for paper, absent for exercise"}]}
EXERCISE_SCHEMA = {"items": [{"question_type": "|".join(QUESTION_TYPES), "stem_markdown": "unnumbered Chinese Markdown; no headings",
    "source_sections": ["exact source section title covered by this question"],
    "answer": {"final": "answer only", "reasoning": "full internal checking reasoning",
               "display_reasoning": "1-3 concise lines for 拔高题/综合题; empty string for other types"}}]}


def normalize_items(output, role, unit_id, format_contract=None, source_sections=None, task_contract=None):
    candidates = output.get("items")
    if not isinstance(candidates, list) or not candidates:
        raise DomainError("ITEMS_REQUIRED", 422)
    items = []
    for ordinal, candidate in enumerate(candidates, 1):
        if not isinstance(candidate, dict):
            raise DomainError("INVALID_ITEM", 422)
        stem, answer = candidate.get("stem_markdown"), candidate.get("answer")
        if (not isinstance(stem, str) or not stem.strip() or not isinstance(answer, dict)
                or any(not isinstance(answer.get(k), str) or not answer[k].strip() for k in ("final", "reasoning"))):
            raise DomainError("ITEM_ANSWER_INCOMPLETE", 422)
        score = candidate.get("score")
        if role == "exercise" and score is not None:
            raise DomainError("EXERCISE_SCORE_FORBIDDEN", 422)
        if role == "paper":
            try:
                number = Decimal(str(score))
                if not number.is_finite() or number <= 0 or type(score) not in {int, float}:
                    raise ValueError()
            except (InvalidOperation, ValueError):
                raise DomainError("PAPER_SCORE_REQUIRED", 422) from None
        value = {"item_id": str(uuid4()), "section_id": unit_id, "ordinal": ordinal,
                 "stem_markdown": stem, "answer": {k: answer[k] for k in ("final", "reasoning")}}
        from .adaptive_format import LAYOUT, validate_item
        if format_contract == LAYOUT:
            value.update(validate_item(candidate, task_contract or {}))
            display = answer.get("display_reasoning", "")
            if not isinstance(display, str):
                raise DomainError("TASK_DISPLAY_REASONING_INVALID", 422)
            value["answer"]["display_reasoning"] = display
        if format_contract == EXERCISE_LAYOUT:
            kind = candidate.get("question_type")
            display = answer.get("display_reasoning", "")
            refs = candidate.get("source_sections", [])
            if kind not in QUESTION_TYPES or not isinstance(display, str):
                raise DomainError("EXERCISE_FORMAT_INVALID", 422)
            if kind in {"拔高题", "综合题"}:
                if not 1 <= len([l for l in display.splitlines() if l.strip()]) <= 3:
                    raise DomainError("EXERCISE_DISPLAY_REASONING_INVALID", 422)
            elif display.strip():
                raise DomainError("EXERCISE_DISPLAY_REASONING_FORBIDDEN", 422)
            if (not isinstance(refs, list) or any(not isinstance(r, str) or r not in (source_sections or []) for r in refs)
                    or re.search(r"(?m)^\s*(?:#{1,6}\s|\*\*\d+\.)", stem)):
                raise DomainError("EXERCISE_FORMAT_INVALID", 422)
            value.update(question_type=kind, source_sections=refs)
            value["answer"]["display_reasoning"] = display.strip()
        if role == "paper":
            value["score"] = score
        for field in ("formal_assertions", "transfer_assertions"):
            if field in candidate:
                assertions = candidate[field]
                if (not isinstance(assertions, list) or len(assertions) > 24
                        or any(not isinstance(a, dict) for a in assertions)
                        or len(__import__("json").dumps(assertions, ensure_ascii=False).encode()) > 32000):
                    raise DomainError("ITEM_ASSERTIONS_INVALID", 422)
                # Preserve proposed metadata for the independent verifier. The
                # source binder must still reject unrelated or translated data.
                value[field] = assertions
        value["content_hash"] = digest(value)
        items.append(value)
    if format_contract == EXERCISE_LAYOUT:
        if not any(i["question_type"] == "判断题" for i in items) or not any(i["question_type"] != "判断题" for i in items):
            raise DomainError("EXERCISE_COLUMNS_INCOMPLETE", 422)
        if set(source_sections or []) - {s for i in items for s in i["source_sections"]}:
            raise DomainError("EXERCISE_SECTION_COVERAGE_INCOMPLETE", 422)
        items.sort(key=lambda i: QUESTION_TYPES.index(i["question_type"]))
        for ordinal, item in enumerate(items, 1):
            item["ordinal"] = ordinal
            item["content_hash"] = digest({k:v for k,v in item.items() if k != "content_hash"})
    from .adaptive_format import LAYOUT, validate_quantity
    if format_contract == LAYOUT:
        validate_quantity(items, task_contract or {})
    return items


def render_items(items, role, answers=False):
    lines = ["# 参考答案" if answers else ("# 试卷" if role == "paper" else "# 练习")]
    if role == "paper" and not answers:
        total = sum(Decimal(str(i["score"])) for i in items)
        lines.extend([f"满分：{total}；共 {len(items)} 题。", ""])
    for item in items:
        label = f"## {item['ordinal']}."
        if not answers and role == "paper":
            label += f"（{item['score']} 分）"
        lines.extend([label, "", item["answer"]["final"] if answers else item["stem_markdown"], ""])
        if answers:
            lines.extend([item["answer"]["reasoning"], ""])
    return "\n".join(lines)


def render_unit_items(items, unit, answers_only=False, *, projection_version=3):
    from .adaptive_format import LAYOUT, render
    if unit.get("format_contract") == LAYOUT:
        return render(items, unit, answers_only)
    if projection_version not in (1, 2, 3):
        raise DomainError("EXERCISE_PROJECTION_VERSION_UNKNOWN", 422)
    if unit.get("format_contract") != EXERCISE_LAYOUT:
        if answers_only:
            return render_items(items, unit["artifact_role"], True)
        return render_items(items, unit["artifact_role"])+"\n\n"+render_items(items, unit["artifact_role"], True)
    from ..services.format_specs import cn_numeral
    title = unit["title"]+(" · 参考答案" if answers_only else "")
    lines = [f"## {cn_numeral(unit.get('section_number', 1))}、{title}", ""]
    if not answers_only:
        for column, kinds in (("一、易错辨析", ("判断题",)), ("二、训练", QUESTION_TYPES[1:])):
            lines.extend([f"### {column}", ""])
            if column == "一、易错辨析":
                lines.extend([f"> 考察知识点：{unit['title']}。", ""])
            for kind in kinds:
                group = [i for i in items if i["question_type"] == kind]
                if not group:
                    continue
                lines.extend([f"#### {kind}", ""])
                for item in group:
                    lines.extend([f"**{item['ordinal']}.** {item['stem_markdown'].strip()}", ""])
                    if kind == "判断题":
                        lines.extend(["- [ ]" if projection_version == 1 else "- [ ] 待判断", ""])
                    else:
                        lines.extend([""]*(2 if kind == "选择题" else 3))
    lines.extend(["### 参考答案", ""])
    for item in items:
        final = item['answer']['final'].strip()
        label = "" if projection_version == 3 and re.match(r"^(?:答案|答)\s*[:：]", final) else "答案："
        lines.extend([f"**{item['ordinal']}.** {label}{final}"])
        if item["question_type"] in {"拔高题", "综合题"}:
            explanation = item["answer"]["display_reasoning"]
            lines.append(("" if projection_version == 3 and re.match(r"^解析\s*[:：]", explanation) else "解析：") + explanation)
        lines.append("")
    return "\n".join(lines)


def project_units(units):
    for unit in sorted(units, key=lambda u: u.get("display_order", units.index(u))):
        if unit.get("items"):
            for role in unit.get("artifact_roles", [unit["artifact_role"]]):
                from .adaptive_format import LAYOUT
                body = (render_unit_items(unit["items"], unit, role == "answer") if unit.get("format_contract") in {EXERCISE_LAYOUT, LAYOUT}
                        else render_items(unit["items"], unit["artifact_role"], role == "answer"))
                yield unit | {"artifact_role": role, "file_id": f"{unit['unit_id']}-{role}", "body": body}
        else:
            yield unit | {"file_id": unit["unit_id"]}

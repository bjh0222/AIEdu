"""Source-bound AI item reviews, explicitly distinct from independent tool proof."""
from __future__ import annotations
from .contracts import digest, sha

DIMENSIONS = ("premises", "reasoning", "transfer", "difficulty")
REVIEW_SCHEMA = {"item_reviews": [{"item_id": "exact supplied ID", "dimensions": {
    d: {"result": "supported|concern|undecidable", "reason": "concise specific explanation",
        "quote": "exact unique substring of stem/final/reasoning, or empty if undecidable"} for d in DIMENSIONS}}]}

def bind_item_reviews(output, unit, audit):
    candidates = output.get("item_reviews", []) if isinstance(output, dict) else []
    candidates = candidates if isinstance(candidates, list) else []
    by_id = {}
    for value in candidates:
        if isinstance(value, dict):
            by_id.setdefault(value.get("item_id"), []).append(value)
    result = []
    for item in unit.get("items", []):
        values = by_id.get(item["item_id"], [])
        dimensions = {}
        for name in DIMENSIONS:
            declared = values[0].get("dimensions", {}) if len(values) == 1 else {}
            candidate = declared.get(name, {}) if isinstance(declared, dict) else {}
            candidate = candidate if isinstance(candidate, dict) else {}
            judgement, reason, quote = candidate.get("result"), candidate.get("reason"), candidate.get("quote")
            anchor = None
            if isinstance(quote, str) and quote:
                for field, text in (("stem_markdown", item["stem_markdown"]),
                    ("answer.final", item["answer"]["final"]), ("answer.reasoning", item["answer"]["reasoning"])):
                    raw, needle = text.encode(), quote.encode()
                    if raw.count(needle) == 1:
                        start = raw.index(needle)
                        anchor = {"field": field, "field_hash": sha(raw), "quote_hash": sha(needle),
                            "start_byte": start, "end_byte": start + len(needle)}
                        break
            valid = judgement in {"supported", "concern", "undecidable"} and isinstance(reason, str) and reason.strip()
            if judgement != "undecidable" and anchor is None:
                valid = False
            dimensions[name] = {"observation": judgement if valid else "undecidable",
                "reason": reason if valid else "missing_or_unbound_independent_observation", "source": anchor,
                "result": "undecidable", "score": None,
                "authority": "independent_ai_reference_not_tool_verification"}
        result.append({"item_id": item["item_id"], "unit_id": unit["unit_id"],
            "item_hash": item.get("content_hash", digest(item)), "dimensions": dimensions,
            "audit": audit, "calibration_status": "not_calibrated", "difficulty_status": "not_measured"})
    return result

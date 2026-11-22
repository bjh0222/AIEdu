"""Pure binding of research-C observations; execution is not certification.

This module never calls a model, reads configuration or writes storage. Raw
provider outputs stay in their immutable call records; returned diagnostics
retain valid sub-observations without converting malformed output to abstention.
"""
from __future__ import annotations

import math

from .contracts import sha
from .review_contract import prepare_review_output

VERSION = "research-observation-binding.v2"
COMPLETE = "complete"
INVALID = "invalid_or_missing_observation"


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _number(value):
    # bool is not an observed count; arbitrary-size Python ints need no float
    # conversion (math.isfinite on those can overflow).
    return type(value) is int or type(value) is float and math.isfinite(value)


def _evidence(proposals, unit):
    if not isinstance(proposals, list):
        return [], ["EVIDENCE_LIST_REQUIRED"]
    bound, errors = [], []
    body = unit.get("body")
    if not isinstance(body, str) or sha(body) != unit.get("content_hash"):
        return [], ["UNIT_CONTENT_HASH_MISMATCH"]
    for proposal in proposals:
        if not isinstance(proposal, dict) or proposal.get("field") != "body":
            errors.append("EVIDENCE_FIELD_INVALID")
            continue
        quote = proposal.get("quote")
        if not _text(quote) or body.count(quote) != 1:
            errors.append("EVIDENCE_UNIQUE_LITERAL_REQUIRED")
            continue
        start = body.encode("utf-8").find(quote.encode("utf-8"))
        bound.append({"unit_id": unit["unit_id"], "field": "body", "quote": quote,
            "unit_hash": unit["content_hash"], "quote_hash": sha(quote),
            "start_byte": start, "end_byte": start + len(quote.encode("utf-8"))})
    return bound, list(dict.fromkeys(errors))


def _indicator(raw, expected, unit):
    errors = []
    if not isinstance(raw, dict):
        raw = {}
        errors.append("INDICATOR_EXACTLY_ONCE_REQUIRED")
    evidence, evidence_errors = _evidence(raw.get("evidence", []), unit)
    errors.extend(evidence_errors)
    applicable, num, den = (raw.get(k) for k in ("applicable", "numerator", "denominator"))
    enum = raw.get("enum_value")
    counts = _number(num) and _number(den) and num >= 0 and den > 0
    count_supplied = num is not None or den is not None
    enum_supplied = enum is not None
    # Some frozen rubrics explicitly define a zero-denominator/null exit and
    # even permit a dimension anchor through a stated vacuous alternative.
    # Bind that observation; never invent a universal all-indicators-decided
    # passing rule. C still has to apply this dimension's actual anchors.
    zero_denominator = (num is None and _number(den) and den == 0
        and _text(expected.get("missing_denominator_rule")) and _text(raw.get("unknown_reason")))
    missing_rule_value = expected.get("missing_denominator_rule")
    missing_rule = missing_rule_value.strip() if isinstance(missing_rule_value, str) else ""
    lower_rule = missing_rule.casefold()
    # A frozen rubric may explicitly define a zero-occurrence best case as a
    # scored zero rather than an abstention (for example, "zero absolute
    # statements -> B3=0; perfect compliance, not null"). Accept that narrow
    # shape only when the rubric says so and literal evidence was supplied.
    scored_zero_denominator = (
        _number(num) and num == 0 and _number(den) and den == 0 and evidence
        and missing_rule and (
            ("perfect compliance" in lower_rule and "zero" in lower_rule)
            or ("zero-occurrence" in lower_rule and "0" in missing_rule)
            or ("零次" in missing_rule and "0" in missing_rule)
            or ("零个" in missing_rule and "0" in missing_rule)
        )
    )
    if count_supplied and not counts and not zero_denominator and not scored_zero_denominator:
        errors.append("INDICATOR_COUNTS_INVALID")
    if enum_supplied and not _text(enum):
        errors.append("INDICATOR_ENUM_INVALID")
    if applicable is False:
        if not _text(raw.get("unknown_reason")) or count_supplied or enum_supplied:
            errors.append("INDICATOR_NONAPPLICABILITY_INVALID")
        result = "not_applicable"
    elif applicable is True:
        if not evidence:
            errors.append("INDICATOR_LITERAL_EVIDENCE_REQUIRED")
        if counts or _text(enum) or scored_zero_denominator:
            result = "observed"
        else:
            result = "undecidable"
            if not _text(raw.get("unknown_reason")):
                errors.append("INDICATOR_UNKNOWN_REASON_REQUIRED")
    else:
        result = "undecidable"
        errors.append("INDICATOR_APPLICABILITY_REQUIRED")
    complete = not errors
    return {"indicator_id": expected["indicator_id"], "applicable": applicable,
        "numerator": num if complete and (counts or scored_zero_denominator) else None,
        "denominator": den if complete and (counts or zero_denominator or scored_zero_denominator) else None,
        "unit": raw.get("unit"), "enum_value": enum if complete and _text(enum) else None,
        "evidence": evidence, "unknown_reason": raw.get("unknown_reason"),
        "result": result if complete else "undecidable",
        "processing_status": COMPLETE if complete else INVALID,
        "validation_errors": list(dict.fromkeys(errors))}


def _dimension(raw, expected, unit):
    errors = []
    if not isinstance(raw, dict):
        raw = {}
        errors.append("DIMENSION_EXACTLY_ONCE_REQUIRED")
    evidence, evidence_errors = _evidence(raw.get("evidence", []), unit)
    errors.extend(evidence_errors)
    proposals = raw.get("indicators")
    if not isinstance(proposals, list):
        proposals = []
        if expected.get("indicators"):
            errors.append("INDICATOR_LIST_REQUIRED")
    indicators = []
    expected_ids = {i["indicator_id"] for i in expected.get("indicators", [])}
    if any(not isinstance(i, dict) or not isinstance(i.get("indicator_id"), str)
            or i["indicator_id"] not in expected_ids for i in proposals):
        errors.append("UNEXPECTED_INDICATOR")
    for indicator in expected.get("indicators", []):
        matches = [i for i in proposals if isinstance(i, dict) and i.get("indicator_id") == indicator["indicator_id"]]
        row = _indicator(matches[0] if len(matches) == 1 else None, indicator, unit)
        indicators.append(row)
        if row["processing_status"] != COMPLETE:
            errors.append("INDICATOR_EXECUTION_INCOMPLETE")
    result, score = raw.get("result"), raw.get("score")
    if not _text(raw.get("reason")):
        errors.append("DIMENSION_REASON_REQUIRED")
    if isinstance(result, str) and result in {"satisfied", "violated"}:
        if type(score) is not int or not ((result == "satisfied" and score == 2) or
                (result == "violated" and score in {0, 1})):
            errors.append("DIMENSION_SCORE_RESULT_CONFLICT")
        if not evidence:
            errors.append("DIMENSION_LITERAL_EVIDENCE_REQUIRED")
    elif result == "undecidable":
        # A reasonless/missing response is an execution defect. A grounded
        # null observation is a real abstention, not a negative/zero score.
        if "score" not in raw or score is not None:
            errors.append("UNKNOWN_SCORE_MUST_BE_EXPLICIT_NULL")
        if not evidence:
            errors.append("UNKNOWN_LITERAL_EVIDENCE_REQUIRED")
    elif result == "not_applicable":
        if "score" not in raw or score is not None:
            errors.append("NONAPPLICABLE_SCORE_MUST_BE_EXPLICIT_NULL")
        if any(i["result"] != "not_applicable" for i in indicators):
            errors.append("DIMENSION_INDICATOR_APPLICABILITY_CONFLICT")
    else:
        errors.append("DIMENSION_RESULT_REQUIRED")
    complete = not errors
    return {"dimension_id": expected.get("dimension_id") or expected["id"],
        "result": result if complete else "undecidable", "score": score if complete else None,
        "reason": raw.get("reason"), "evidence": evidence, "indicators": indicators,
        "repair_condition": raw.get("repair_condition"), "limitations": raw.get("limitations", []),
        "processing_status": COMPLETE if complete else INVALID,
        "validation_errors": list(dict.fromkeys(errors)), "method_version": VERSION}


def bind_batch(output, dimensions, unit, evidence_catalog=None):
    """Bind expected targets, preserving valid siblings and flagging extras.

    The caller must retain batch_contract_errors and require them to be fixed;
    unexpected output is never silently accepted by dropping it from a list.
    No score, source quote or observation is inferred from another row.
    """
    prepared, normalization = prepare_review_output(output, evidence_catalog)
    raw = prepared.get("dimensions") if isinstance(prepared, dict) else None
    errors = []
    if not isinstance(raw, list):
        raw = []
        errors.append("DIMENSION_LIST_REQUIRED")
    expected_ids = {d.get("dimension_id") or d["id"] for d in dimensions}
    if any(not isinstance(r, dict) or not isinstance(r.get("dimension_id"), str)
            or r["dimension_id"] not in expected_ids for r in raw):
        errors.append("UNEXPECTED_DIMENSION")
    rows = []
    for dimension in dimensions:
        identity = dimension.get("dimension_id") or dimension["id"]
        matches = [r for r in raw if isinstance(r, dict) and r.get("dimension_id") == identity]
        rows.append(_dimension(matches[0] if len(matches) == 1 else None, dimension, unit))
    complete = sum(r["processing_status"] == COMPLETE for r in rows)
    return {"method_version": VERSION, "dimensions": rows, "batch_contract_errors": errors,
        "normalization": normalization,
        "status": "completed" if complete == len(rows) and not errors else "incomplete",
        "coverage": {"expected": len(rows), "complete_observations": complete,
            "decided": sum(r["processing_status"] == COMPLETE and r["result"] in {"satisfied", "violated"} for r in rows),
            "grounded_abstentions": sum(r["processing_status"] == COMPLETE and r["result"] == "undecidable" for r in rows),
            "not_applicable": sum(r["processing_status"] == COMPLETE and r["result"] == "not_applicable" for r in rows),
            "invalid_or_missing": len(rows) - complete}}

"""Pure source/observation contracts for the pending whole-document B/C step.

No database, network, runtime registration, rubric thresholds or certification.
Literal binding proves where an observation points, not that its claim is true.
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import DomainError, digest, sha

METHOD = "whole-document-source-bound-synthesis.v1"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Block(_Record):
    block_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    body: str = Field(min_length=1)
    source_span_ids: list[str] = Field(min_length=1)


class Relation(_Record):
    source_unit_id: str = Field(min_length=1)
    target_unit_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    source_span_ids: list[str] = Field(min_length=2)


class UnintegratedUnit(_Record):
    unit_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class Proposal(_Record):
    source_bundle_hash: str
    title: str = Field(min_length=1)
    blocks: list[Block] = Field(min_length=1)
    relations: list[Relation]
    unintegrated_units: list[UnintegratedUnit]
    limitations: list[str]


class Observation(_Record):
    check_id: str
    result: Literal["satisfied", "violated", "undecidable", "not_applicable"]
    reason: str = Field(min_length=1)
    block_ids: list[str] = Field(min_length=1)
    source_span_ids: list[str]
    evidence_gap: str | None
    applicability_reason: str | None


class Review(_Record):
    source_bundle_hash: str
    proposal_hash: str
    checks: list[Observation] = Field(min_length=1)
    optimization_prompt: str | None
    repair_block_ids: list[str]
    preserve_block_ids: list[str]
    recheck_conditions: list[str]


def _require(condition, code):
    if not condition:
        raise DomainError(code, 422)


def _nonblank(text):
    return isinstance(text, str) and bool(text.strip())


def _unique(values, code):
    _require(all(_nonblank(v) for v in values) and len(values) == len(set(values)), code)


def _parse(model, value, code):
    try:
        return model.model_validate(value).model_dump()
    except ValidationError:
        raise DomainError(code, 422) from None


def _spans(unit):
    body = unit["body"]
    spans = []
    for match in re.finditer(r"\S[\s\S]*?(?=\n[ \t]*\n|\Z)", body):
        quote = match.group().rstrip()
        if not quote:
            continue
        start = len(body[:match.start()].encode("utf-8"))
        end = start + len(quote.encode("utf-8"))
        spans.append({"source_span_id": digest([unit["unit_id"], unit["content_hash"], start, end]),
            "unit_id": unit["unit_id"], "unit_hash": unit["content_hash"], "start_byte": start,
            "end_byte": end, "quote_hash": sha(quote), "text": quote})
    return spans


def source_bundle(state, version_id, content_hash, units):
    """Caller supplies a verified immutable version; include EVERY planned body.

    No title-only substitution, retrieval selection, digest or truncation. The
    existing inference layer must reject over-capacity input before dispatch.
    """
    _require(_nonblank(version_id) and isinstance(content_hash, str)
        and re.fullmatch(r"[0-9a-f]{64}", content_hash), "SYNTHESIS_SOURCE_IDENTITY_INVALID")
    plan = state.get("plan", {})
    planned = [u["unit_id"] for u in plan.get("units", [])]
    actual = [u["unit_id"] for u in units]
    _unique(planned, "SYNTHESIS_SOURCE_PLAN_INVALID")
    _unique(actual, "SYNTHESIS_SOURCE_DUPLICATE_UNIT")
    _require(planned and set(planned) == set(actual), "SYNTHESIS_SOURCE_INCOMPLETE")
    _require(_nonblank(plan.get("plan_hash")), "SYNTHESIS_SOURCE_PLAN_INVALID")
    by_id = {u["unit_id"]: u for u in units}
    # Whole bodies/items, but not generation traces, provider identity or prior
    # quality judgements. C must inspect content rather than inherit B's claims
    # about its own process or a previous C result.
    source_fields = {"unit_id", "file_id", "title", "artifact_role", "display_order", "body", "content_hash",
        "items", "method_overview", "teaching_arrangement", "source_sections", "format_contract", "subject_bindings"}
    ordered = [{k: deepcopy(v) for k, v in by_id[uid].items() if k in source_fields} for uid in planned]
    for unit in ordered:
        _require(_nonblank(unit.get("body")) and unit.get("content_hash") == sha(unit["body"]),
            "SYNTHESIS_SOURCE_HASH_MISMATCH")
    context = state.get("context", {})
    result = {"method_version": METHOD, "version_id": version_id, "content_hash": content_hash,
        "plan_hash": plan["plan_hash"], "frozen_A_arrangement": deepcopy(plan.get("teaching_arrangement")),
        "frozen_task": {k: deepcopy(context[k]) for k in (
            "raw_input", "hard_constraints", "domain_key", "branch", "generation_scope", "output_design",
            "spec_bindings", "subject_bindings", "subject_understanding",
            "evaluation_frameworks", "research_policy") if k in context},
        "research_rubrics": deepcopy(context.get("research_rubrics", [])),
        "network_policy": deepcopy(plan.get("network_policy")),
        "source_units": ordered, "source_spans": [s for u in ordered for s in _spans(u)],
        "coverage": {"expected_unit_ids": planned, "provided_unit_ids": list(planned),
                     "input_projection": "complete_bodies_not_summaries"}}
    return result | {"bundle_hash": digest(result)}


def _verify_bundle(bundle):
    _require(bundle.get("bundle_hash") == digest({k: v for k, v in bundle.items() if k != "bundle_hash"}),
        "SYNTHESIS_SOURCE_BUNDLE_STALE")


def proposal_schema(bundle):
    _verify_bundle(bundle)
    value = Proposal.model_json_schema()
    value["properties"]["source_bundle_hash"]["const"] = bundle["bundle_hash"]
    return value


def bind_proposal(output, bundle):
    _verify_bundle(bundle)
    value = _parse(Proposal, output, "SYNTHESIS_PROPOSAL_INVALID")
    _require(value["source_bundle_hash"] == bundle["bundle_hash"], "SYNTHESIS_SOURCE_BUNDLE_STALE")
    _require(_nonblank(value["title"]), "SYNTHESIS_PROPOSAL_INVALID")
    blocks = [b["block_id"] for b in value["blocks"]]
    _unique(blocks, "SYNTHESIS_BLOCK_ID_INVALID")
    known = {s["source_span_id"]: s for s in bundle["source_spans"]}
    used = set()

    def resolve(ids):
        _unique(ids, "SYNTHESIS_SOURCE_SPAN_INVALID")
        _require(set(ids) <= set(known), "SYNTHESIS_SOURCE_SPAN_INVALID")
        return {known[ref]["unit_id"] for ref in ids}

    for block in value["blocks"]:
        _require(_nonblank(block["heading"]) and _nonblank(block["body"]), "SYNTHESIS_PROPOSAL_INVALID")
        used |= resolve(block["source_span_ids"])
    expected = set(bundle["coverage"]["expected_unit_ids"])
    for relation in value["relations"]:
        endpoints = {relation["source_unit_id"], relation["target_unit_id"]}
        _require(len(endpoints) == 2 and endpoints <= expected, "SYNTHESIS_RELATION_INVALID")
        _require(_nonblank(relation["kind"]) and _nonblank(relation["explanation"]), "SYNTHESIS_RELATION_INVALID")
        _require(endpoints <= resolve(relation["source_span_ids"]), "SYNTHESIS_RELATION_SOURCE_UNBOUND")
    gaps = [g["unit_id"] for g in value["unintegrated_units"]]
    _unique(gaps, "SYNTHESIS_COVERAGE_INVALID")
    _require(all(_nonblank(g["reason"]) for g in value["unintegrated_units"]), "SYNTHESIS_COVERAGE_INVALID")
    # A source mentioned only by an edge is not counted as integrated prose.
    _require(set(gaps) == expected - used, "SYNTHESIS_COVERAGE_INVALID")
    _require(all(_nonblank(s) for s in value["limitations"]), "SYNTHESIS_PROPOSAL_INVALID")
    return {"method_version": METHOD, "proposal": value, "proposal_hash": digest(value),
        "source_bundle_hash": bundle["bundle_hash"], "source_version_id": bundle["version_id"],
        "source_content_hash": bundle["content_hash"], "source_plan_hash": bundle["plan_hash"],
        "coverage": {"integrated_unit_ids": sorted(used), "unintegrated_unit_ids": gaps},
        "status": "candidate", "calibration_status": "not_calibrated",
        "binding_scope": "literal_source_identity_not_semantic_truth"}


def review_schema(bound):
    value = Review.model_json_schema()
    for key in ("source_bundle_hash", "proposal_hash"):
        value["properties"][key]["const"] = bound[key]
    return value


def bind_review(output, bound, bundle, expected_check_ids, *, external_unresolved=False):
    """Bind externally selected checks; never invent a rubric or passing score."""
    _verify_bundle(bundle)
    rebound = bind_proposal(bound["proposal"], bundle)
    _require(rebound == bound, "SYNTHESIS_PROPOSAL_STALE")
    value = _parse(Review, output, "SYNTHESIS_REVIEW_INVALID")
    _require(value["source_bundle_hash"] == bundle["bundle_hash"]
        and value["proposal_hash"] == bound["proposal_hash"], "SYNTHESIS_REVIEW_STALE")
    _unique(expected_check_ids, "SYNTHESIS_CHECK_PLAN_INVALID")
    _require(bool(expected_check_ids), "SYNTHESIS_CHECK_PLAN_INVALID")
    actual = [r["check_id"] for r in value["checks"]]
    _unique(actual, "SYNTHESIS_REVIEW_COVERAGE_INVALID")
    _require(set(actual) == set(expected_check_ids), "SYNTHESIS_REVIEW_COVERAGE_INVALID")
    blocks = {b["block_id"] for b in bound["proposal"]["blocks"]}
    spans = {s["source_span_id"] for s in bundle["source_spans"]}
    for row in value["checks"]:
        _unique(row["block_ids"], "SYNTHESIS_REVIEW_EVIDENCE_INVALID")
        _unique(row["source_span_ids"], "SYNTHESIS_REVIEW_EVIDENCE_INVALID")
        _require(set(row["block_ids"]) <= blocks and set(row["source_span_ids"]) <= spans
            and _nonblank(row["reason"]), "SYNTHESIS_REVIEW_EVIDENCE_INVALID")
        if row["result"] == "undecidable":
            _require(_nonblank(row["evidence_gap"]), "SYNTHESIS_REVIEW_UNKNOWN_UNGROUNDED")
        elif row["result"] == "not_applicable":
            _require(_nonblank(row["applicability_reason"]), "SYNTHESIS_REVIEW_APPLICABILITY_MISSING")
        else:
            _require(bool(row["source_span_ids"]), "SYNTHESIS_REVIEW_EVIDENCE_INVALID")
    repair, preserve = value["repair_block_ids"], value["preserve_block_ids"]
    _unique(repair, "SYNTHESIS_REPAIR_SCOPE_INVALID")
    _unique(preserve, "SYNTHESIS_REPAIR_SCOPE_INVALID")
    _require(not set(repair) & set(preserve) and set(repair) | set(preserve) == blocks,
        "SYNTHESIS_REPAIR_SCOPE_INVALID")
    results = {r["result"] for r in value["checks"]}
    _require(type(external_unresolved) is bool, "SYNTHESIS_EXTERNAL_DIAGNOSTIC_INVALID")
    # The caller may bind separately executed full research indicators. Their
    # unresolved result tightens this gate; it can never turn a failure to pass.
    unresolved = (bool(results & {"violated", "undecidable"})
        or bool(bound["coverage"]["unintegrated_unit_ids"]) or external_unresolved)
    if unresolved:
        _require(_nonblank(value["optimization_prompt"]) and bool(value["recheck_conditions"])
            and all(_nonblank(c) for c in value["recheck_conditions"]), "SYNTHESIS_REPAIR_PROMPT_REQUIRED")
    else:
        _require(not repair, "SYNTHESIS_REPAIR_SCOPE_INVALID")
    return {"method_version": METHOD, "review": value, "review_hash": digest(value),
        "source_bundle_hash": bundle["bundle_hash"], "proposal_hash": bound["proposal_hash"],
        "execution_coverage": {"expected": len(expected_check_ids), "received": len(actual)},
        "automatic_status": "needs_repair" if "violated" in results else "needs_review" if unresolved else "satisfied",
        "calibration_status": "not_calibrated", "professional_certification": "not_established",
        "binding_scope": "literal_source_identity_not_semantic_truth"}

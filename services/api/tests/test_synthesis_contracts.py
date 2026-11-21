"""Pure contracts only: not runtime wiring, model quality or calibration tests."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest, sha
from app.v2.synthesis_contracts import (bind_proposal, bind_review, proposal_schema,
    review_schema, source_bundle)


@pytest.fixture
def case():
    bodies = ["# 概念甲\n\n相同段落。\n\n相同段落。", "# Method B\n\nThe condition is explicit."]
    units = [{"unit_id": f"u{i}", "title": f"Section {i}", "artifact_role": "tutorial",
        "body": body, "content_hash": sha(body)} for i, body in enumerate(bodies)]
    state = {"context": {"raw_input": "按真实方法组织，不用固定难度分组。", "hard_constraints": ["完整覆盖"],
        "research_rubrics": [{"id": "frozen-research", "version": 2}]},
        "plan": {"plan_hash": "frozen-plan", "units": [{"unit_id": u["unit_id"]} for u in units],
            "teaching_arrangement": {"content": {"generation_prompt": "保留方法适用条件"}}}}
    bundle = source_bundle(state, "v1", sha("immutable stored content"), units)
    refs = [next(s["source_span_id"] for s in bundle["source_spans"] if s["unit_id"] == u["unit_id"]) for u in units]
    proposal = {"source_bundle_hash": bundle["bundle_hash"], "title": "实际关系", "blocks": [
        {"block_id": "b1", "heading": "条件与联系", "body": "由B生成的整合候选。", "source_span_ids": refs}],
        "relations": [{"source_unit_id": "u0", "target_unit_id": "u1", "kind": "comparison",
            "explanation": "待C验证的联系。", "source_span_ids": refs}], "unintegrated_units": [], "limitations": []}
    bound = bind_proposal(proposal, bundle)
    review = {"source_bundle_hash": bundle["bundle_hash"], "proposal_hash": bound["proposal_hash"],
        "checks": [{"check_id": "frozen-check", "result": "satisfied", "reason": "检查的具体理由。",
            "block_ids": ["b1"], "source_span_ids": refs, "evidence_gap": None, "applicability_reason": None}],
        "optimization_prompt": None, "repair_block_ids": [], "preserve_block_ids": ["b1"], "recheck_conditions": []}
    return state, units, bundle, proposal, bound, review


def test_all_bodies_and_A_requirements_are_present_without_mutation(case):
    state, units, bundle, *_ = case
    before = deepcopy((state, units))
    reordered = source_bundle(state, "v1", bundle["content_hash"], list(reversed(units)))
    assert reordered == bundle
    assert (state, units) == before
    assert bundle["source_units"] == units
    assert bundle["frozen_task"]["raw_input"] == state["context"]["raw_input"]
    assert bundle["frozen_A_arrangement"] == state["plan"]["teaching_arrangement"]
    assert bundle["research_rubrics"] == state["context"]["research_rubrics"]
    assert bundle["coverage"]["input_projection"] == "complete_bodies_not_summaries"


def test_repeated_quotes_have_unambiguous_UTF8_byte_locations(case):
    body = case[1][0]["body"].encode()
    spans = [s for s in case[2]["source_spans"] if s["unit_id"] == "u0"]
    repeated = [s for s in spans if s["text"] == "相同段落。"]
    assert len(repeated) == 2 and repeated[0]["source_span_id"] != repeated[1]["source_span_id"]
    for span in spans:
        assert body[span["start_byte"]:span["end_byte"]].decode() == span["text"]
        assert sha(span["text"]) == span["quote_hash"]


def test_generation_traces_and_prior_judgements_do_not_enter_content_bundle(case):
    state, units, *_ = deepcopy(case)
    units[0].update(network_research={"provider": "not content"}, generation_audit={"model": "not content"},
        quality={"result": "old judgement"}, call_id="old call")
    bundle = source_bundle(state, "v1", sha("source"), units)
    for key in ("network_research", "generation_audit", "quality", "call_id"):
        assert key not in bundle["source_units"][0]


@pytest.mark.parametrize("domain,role", [("math_logic", "exercise"), ("cs_it", "tutorial"),
    ("humanities", "reading"), ("life_science", "paper")])
def test_no_discipline_or_question_type_defaults(case, domain, role):
    state, units, *_ = deepcopy(case)
    state["context"]["domain_key"] = domain
    for unit in units:
        unit["artifact_role"] = role
    bundle = source_bundle(state, "v1", sha("source"), units)
    assert all(u["artifact_role"] == role for u in bundle["source_units"])
    assert "question_type" not in bundle and bundle["frozen_task"]["domain_key"] == domain


def test_single_unit_is_valid_not_a_forced_cross_chapter_edge(case):
    state, units, *_ = deepcopy(case)
    state["plan"]["units"] = state["plan"]["units"][:1]
    bundle = source_bundle(state, "v1", sha("source"), units[:1])
    assert bundle["coverage"]["provided_unit_ids"] == ["u0"]


@pytest.mark.parametrize("mutate,code", [
    (lambda s, u: u.pop(), "SYNTHESIS_SOURCE_INCOMPLETE"),
    (lambda s, u: u.append(deepcopy(u[0])), "SYNTHESIS_SOURCE_DUPLICATE_UNIT"),
    (lambda s, u: u[0].update(body="changed"), "SYNTHESIS_SOURCE_HASH_MISMATCH"),
    (lambda s, u: s["plan"].update(units=[]), "SYNTHESIS_SOURCE_INCOMPLETE"),
])
def test_missing_duplicate_and_changed_source_rejected(case, mutate, code):
    state, units, *_ = deepcopy(case)
    mutate(state, units)
    with pytest.raises(DomainError, match=code):
        source_bundle(state, "v1", sha("source"), units)


def test_large_input_is_not_silently_summarized(case):
    state, units, *_ = deepcopy(case)
    units[0]["body"] = "完整正文。" * 10000
    units[0]["content_hash"] = sha(units[0]["body"])
    bundle = source_bundle(state, "v1", sha("source"), units)
    assert bundle["source_units"][0]["body"] == units[0]["body"]
    assert bundle["source_spans"][0]["text"] == units[0]["body"]


def test_proposal_is_only_a_candidate_and_schema_binds_identity(case):
    bundle, proposal, bound = case[2:5]
    assert bound["status"] == "candidate" and bound["calibration_status"] == "not_calibrated"
    assert bound["proposal_hash"] == digest(proposal)
    assert proposal_schema(bundle)["properties"]["source_bundle_hash"]["const"] == bundle["bundle_hash"]
    assert review_schema(bound)["properties"]["proposal_hash"]["const"] == bound["proposal_hash"]


@pytest.mark.parametrize("mutate,code", [
    (lambda p: p.update(source_bundle_hash="old"), "SYNTHESIS_SOURCE_BUNDLE_STALE"),
    (lambda p: p["blocks"].append(deepcopy(p["blocks"][0])), "SYNTHESIS_BLOCK_ID_INVALID"),
    (lambda p: p["blocks"][0].update(source_span_ids=["invented"]), "SYNTHESIS_SOURCE_SPAN_INVALID"),
    (lambda p: p["blocks"][0].update(body="  "), "SYNTHESIS_PROPOSAL_INVALID"),
    (lambda p: p["relations"][0].update(target_unit_id="u0"), "SYNTHESIS_RELATION_INVALID"),
    (lambda p: p.update(calibration_status="calibrated"), "SYNTHESIS_PROPOSAL_INVALID"),
])
def test_proposal_rejects_forged_source_or_authority(case, mutate, code):
    proposal = deepcopy(case[3])
    mutate(proposal)
    with pytest.raises(DomainError, match=code):
        bind_proposal(proposal, case[2])


def test_relations_must_have_source_spans_from_both_endpoints(case):
    proposal = deepcopy(case[3])
    proposal["relations"][0]["source_span_ids"] = [s["source_span_id"] for s in case[2]["source_spans"] if s["unit_id"] == "u0"]
    with pytest.raises(DomainError, match="SYNTHESIS_RELATION_SOURCE_UNBOUND"):
        bind_proposal(proposal, case[2])


def test_omitted_prose_cannot_hide_behind_a_relation(case):
    proposal = deepcopy(case[3])
    proposal["blocks"][0]["source_span_ids"] = proposal["blocks"][0]["source_span_ids"][:1]
    with pytest.raises(DomainError, match="SYNTHESIS_COVERAGE_INVALID"):
        bind_proposal(proposal, case[2])
    proposal["unintegrated_units"] = [{"unit_id": "u1", "reason": "尚缺可支持的联系"}]
    bound = bind_proposal(proposal, case[2])
    review = deepcopy(case[5])
    review["proposal_hash"] = bound["proposal_hash"]
    with pytest.raises(DomainError, match="SYNTHESIS_REPAIR_PROMPT_REQUIRED"):
        bind_review(review, bound, case[2], ["frozen-check"])


def test_literal_positive_execution_is_not_professional_calibration(case):
    result = bind_review(case[5], case[4], case[2], ["frozen-check"])
    assert result["automatic_status"] == "satisfied"
    assert result["calibration_status"] == "not_calibrated"
    assert result["professional_certification"] == "not_established"


@pytest.mark.parametrize("result", ["violated", "undecidable"])
def test_nonpassing_review_requires_complete_B_prompt_and_recheck(case, result):
    review = deepcopy(case[5])
    review["checks"][0].update(result=result, evidence_gap="适用条件的依据不足" if result == "undecidable" else None)
    with pytest.raises(DomainError, match="SYNTHESIS_REPAIR_PROMPT_REQUIRED"):
        bind_review(review, case[4], case[2], ["frozen-check"])
    review.update(optimization_prompt="仅整改b1的条件说明，保留其它结构与原题，重新核该条件。",
        repair_block_ids=["b1"], preserve_block_ids=[], recheck_conditions=["C复验条件与源文一致"])
    result = bind_review(review, case[4], case[2], ["frozen-check"])
    assert result["automatic_status"] in {"needs_repair", "needs_review"}


@pytest.mark.parametrize("mutate,code", [
    (lambda r: r.update(proposal_hash="old"), "SYNTHESIS_REVIEW_STALE"),
    (lambda r: r.update(checks=[]), "SYNTHESIS_REVIEW_INVALID"),
    (lambda r: r["checks"].append(deepcopy(r["checks"][0])), "SYNTHESIS_REVIEW_COVERAGE_INVALID"),
    (lambda r: r["checks"][0].update(check_id="different"), "SYNTHESIS_REVIEW_COVERAGE_INVALID"),
    (lambda r: r["checks"][0].update(block_ids=["absent"]), "SYNTHESIS_REVIEW_EVIDENCE_INVALID"),
    (lambda r: r["checks"][0].update(source_span_ids=[]), "SYNTHESIS_REVIEW_EVIDENCE_INVALID"),
    (lambda r: r["checks"][0].update(result="undecidable"), "SYNTHESIS_REVIEW_UNKNOWN_UNGROUNDED"),
    (lambda r: r["checks"][0].update(result="not_applicable"), "SYNTHESIS_REVIEW_APPLICABILITY_MISSING"),
    (lambda r: r.update(repair_block_ids=["b1"]), "SYNTHESIS_REPAIR_SCOPE_INVALID"),
    (lambda r: r.update(preserve_block_ids=[]), "SYNTHESIS_REPAIR_SCOPE_INVALID"),
])
def test_review_missing_execution_cannot_become_unknown_or_pass(case, mutate, code):
    review = deepcopy(case[5])
    mutate(review)
    with pytest.raises(DomainError, match=code):
        bind_review(review, case[4], case[2], ["frozen-check"])


def test_mutated_bundle_and_bound_proposal_are_stale(case):
    bundle = deepcopy(case[2])
    bundle["source_units"][0]["body"] = "mutated"
    with pytest.raises(DomainError, match="SYNTHESIS_SOURCE_BUNDLE_STALE"):
        bind_proposal(case[3], bundle)
    bound = deepcopy(case[4])
    bound["coverage"]["unintegrated_unit_ids"] = ["u1"]
    with pytest.raises(DomainError, match="SYNTHESIS_PROPOSAL_STALE"):
        bind_review(case[5], bound, case[2], ["frozen-check"])

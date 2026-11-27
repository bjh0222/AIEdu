from __future__ import annotations

from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest, sha


def _strategy():
    return {
        "method_version": "A0-research-strategy.v1",
        "necessity": "required",
        "decision_basis": "The task needs source-backed assessment guidance.",
        "user_requirements": ["Prefer official education sources."],
        "autonomous_additions": [{
            "direction_id": "d2",
            "question": "How should cognitively demanding items be represented?",
            "reason": "This supports the requested difficulty strategy.",
            "priority": "useful",
        }],
        "research_directions": [
            {"direction_id": "d1", "question": "What does the official framework require?",
             "why_needed": "It bounds the assessment design.", "needed_for": ["structure"]},
            {"direction_id": "d2", "question": "How should cognitively demanding items be represented?",
             "why_needed": "It informs difficulty implementation.", "needed_for": ["design"]},
        ],
        "search_strategy": {
            "queries": ["assessment item design"],
            "source_types": ["official framework", "peer-reviewed article"],
            "preferred_indexes": ["openalex", "crossref"],
            "language_preferences": ["en"],
            "recency_rule": "Prefer current authoritative sources.",
        },
        "source_selection": {
            "include_criteria": ["Relevant and locatable."],
            "exclude_criteria": ["No readable evidence."],
            "authority_rule": "Prefer official and peer-reviewed sources.",
            "conflict_rule": "Keep material conflicts explicit.",
        },
        "evidence_extraction": [
            {"direction_id": "d1", "record_fields": [
                "evidence_id", "source", "claim", "location", "scope",
                "confidence", "limitation", "used_for"],
             "minimum_support": "Exact locatable support."},
            {"direction_id": "d2", "record_fields": [
                "evidence_id", "source", "claim", "location", "scope",
                "confidence", "limitation", "used_for"],
             "minimum_support": "Exact locatable support."},
        ],
        "stop_conditions": ["All critical decisions are supported."],
        "unresolved_questions": [],
    }


def _blueprint():
    return {
        "method_version": "A1-blueprint.v1",
        "task_understanding": {
            "task_type": "assessment tutorial",
            "audience": "adult learner",
            "goals": ["Understand item design"],
            "constraints": ["Keep one chapter"],
        },
        "research_summary": {
            "key_findings": ["Targets must be observable."],
            "evidence_ids": ["e1"],
            "unresolved_questions": [],
        },
        "structure": {
            "units": [{
                "blueprint_unit_id": "bu1",
                "title": "Unit 1",
                "purpose": "Explain and practice.",
                "required_content": ["Observable targets"],
                "depends_on_titles": [],
                "knowledge_relations": ["target -> evidence"],
            }],
        },
        "design": {
            "difficulty_strategy": ["Use nonroutine transfer"],
            "teaching_or_assessment_strategy": ["Explain then practice"],
            "required_methods": ["construct items"],
            "avoid": ["numeric clones"],
        },
        "acceptance": {
            "hard_requirements": ["One observable target"],
            "quality_requirements": ["No direct numerical clone"],
            "verification_methods": ["Criterion review"],
        },
        "flexibility": {
            "b_may_decide": ["Examples and local transitions"],
            "must_return_to_a_if_changed": ["Unit count and target"],
        },
        "decisions": [{
            "decision_id": "d1",
            "statement": "Each item has one observable target.",
            "requirement": "must_follow",
            "blueprint_unit_ids": ["bu1"],
            "evidence_ids": ["e1"],
            "acceptance_method": "Check each actual item target.",
        }],
    }


def _evidence(source_id="m1"):
    return {
        "evidence_id": "e1",
        "source": {
            "source_id": source_id,
            "url": None,
            "title": "Material",
            "source_hash": sha("source"),
        },
        "claim": "Targets should be observable.",
        "location": {
            "locator": "section 1",
            "quote_hash": sha("Targets should be observable."),
            "start_byte": 0,
            "end_byte": 5,
        },
        "scope": "assessment design",
        "confidence": "high",
        "limitation": "Single source.",
        "used_for": ["d1"],
    }


def _context():
    return {
        "context_hash": "c" * 64,
        "material_ids": ["m1"],
        "import_understanding": [],
    }


def _teaching():
    return {
        "units": [{
            "title": "Unit 1",
            "artifact_role": "tutorial",
        }],
    }


def test_a0_a1_prompts_are_separate_and_strategy_preserves_user_and_autonomous_inputs():
    from app.v2.planning import A0_RESEARCH_STRATEGY_PROMPT, A1_BLUEPRINT_INSTRUCTION, research_strategy

    assert "teaching_arrangement" not in A0_RESEARCH_STRATEGY_PROMPT
    assert "blueprint" in A1_BLUEPRINT_INSTRUCTION
    value = research_strategy(_strategy())
    assert value["content"]["user_requirements"] == ["Prefer official education sources."]
    assert value["content"]["autonomous_additions"][0]["direction_id"] == "d2"
    assert value["hash"] == digest(value["content"])


def test_research_strategy_disabled_path_is_deterministic_and_unknown_evidence_is_not_needed():
    from app.v2.planning import disabled_research_strategy, research_strategy

    value = disabled_research_strategy({"requirements": ["Use local files only."]})
    assert value["status"] == "schema_validated"
    assert value["content"]["necessity"] == "disabled"
    assert value["content"]["user_requirements"] == ["Use local files only."]
    assert research_strategy(value["content"])["hash"] == value["hash"]
    not_needed = _strategy()
    not_needed["necessity"] = "not_needed"
    not_needed["research_directions"] = []
    not_needed["autonomous_additions"] = []
    not_needed["search_strategy"]["queries"] = []
    not_needed["evidence_extraction"] = [{
        "direction_id": "none",
        "record_fields": ["evidence_id", "source", "claim", "location", "scope",
                          "confidence", "limitation", "used_for"],
        "minimum_support": "No external evidence is required.",
    }]
    normalized = research_strategy(not_needed)
    assert normalized["content"]["evidence_extraction"] == []


def test_blueprint_binds_evidence_decisions_layout_and_hashes():
    from app.v2.planning import bind_a1_artifact

    state = {
        "research_result": {
            "sources": [{"source_id": "m1", "source_hash": sha("source")}],
            "source_claims": [],
        },
        "plan": {},
    }
    artifact = bind_a1_artifact(
        {"blueprint": _blueprint(), "evidence_records": [_evidence()]},
        teaching_content=_teaching(),
        context=_context(),
        state=state,
    )
    assert artifact["hash"] == digest(artifact["content"])
    assert artifact["evidence_hash"] == digest({"evidence_records": artifact["evidence_records"]})
    assert artifact["teaching_arrangement_hash"] == digest(_teaching())
    assert artifact["context_hash"] == "c" * 64


def test_blueprint_rejects_unbound_evidence_and_layout_mismatch():
    from app.v2.planning import bind_a1_artifact

    state = {"research_result": {"sources": [{"source_id": "m1", "source_hash": sha("source")}]}}
    unbound = _evidence()
    unbound["used_for"] = ["d2"]
    with pytest.raises(DomainError) as exc:
        bind_a1_artifact(
            {"blueprint": _blueprint(), "evidence_records": [unbound]},
            teaching_content=_teaching(), context=_context(), state=state,
        )
    assert exc.value.code == "BLUEPRINT_EVIDENCE_UNBOUND"
    mismatch = _teaching()
    mismatch["units"][0]["title"] = "Different"
    with pytest.raises(DomainError) as exc:
        bind_a1_artifact(
            {"blueprint": _blueprint(), "evidence_records": [_evidence()]},
            teaching_content=mismatch, context=_context(), state=state,
        )
    assert exc.value.code == "BLUEPRINT_LAYOUT_UNBOUND"


def test_b_blueprint_execution_allows_only_low_risk_noncore_deviation():
    from app.v2.planning import bind_a1_artifact, validate_blueprint_execution

    state = {"research_result": {"sources": [{"source_id": "m1", "source_hash": sha("source")}]}}
    artifact = bind_a1_artifact(
        {"blueprint": _blueprint(), "evidence_records": [_evidence()]},
        teaching_content=_teaching(), context=_context(), state=state,
    )
    output = {
        "blueprint_execution": {
            "status": "compliant",
            "deviations": [],
        },
    }
    assert validate_blueprint_execution(output, artifact)["status"] == "compliant"
    core = {
        "blueprint_execution": {
            "status": "local_deviation",
            "deviations": [{
                "decision_id": "d1",
                "change": "Use an unobservable target.",
                "reason": "Local wording preference.",
                "risk": "low",
                "evidence_ids": ["e1"],
            }],
        },
    }
    with pytest.raises(DomainError) as exc:
        validate_blueprint_execution(core, artifact)
    assert exc.value.code == "BLUEPRINT_REQUIRES_HUMAN_DECISION"
    with pytest.raises(DomainError) as exc:
        validate_blueprint_execution({"blueprint_execution": {
            "status": "requires_human_decision", "deviations": []}}, artifact)
    assert exc.value.code == "BLUEPRINT_REQUIRES_HUMAN_DECISION"


def test_c_blueprint_review_targets_follow_frozen_unit_titles():
    from app.v2.planning import blueprint_review_targets

    blueprint = {"content": _blueprint()}
    assert [row["decision_id"] for row in blueprint_review_targets(blueprint, "Unit 1")] == ["d1"]
    assert blueprint_review_targets(blueprint, "Other") == []


def test_criterion_requests_include_one_existing_contract_row_per_blueprint_decision():
    from app.v2.criterion_review import requests_for_unit
    from app.v2.planning import bind_a1_artifact

    state_for_binding = {
        "research_result": {"sources": [{"source_id": "m1", "source_hash": sha("source")}]},
        "plan": {},
    }
    artifact = bind_a1_artifact(
        {"blueprint": _blueprint(), "evidence_records": [_evidence()]},
        teaching_content=_teaching(), context=_context(), state=state_for_binding,
    )
    content = {
        "units": [{
            "title": "Unit 1",
            "concepts": [],
            "question_types": [],
            "difficulty_basis": {},
            "quantity": None,
            "material_mapping": [],
            "acceptance_criteria": [],
            "depends_on_titles": [],
            "knowledge_chain": [],
            "misconceptions": [],
            "question_blueprint": [],
        }],
        "goals": [],
        "learner_prerequisites": [],
        "scope": "focused",
        "quality_targets": [],
        "quality_target_refs": [],
        "unresolved_conflicts": [],
        "generation_prompt": "Create the unit.",
    }
    planned = {
        "unit_id": "u1",
        "title": "Unit 1",
        "artifact_role": "tutorial",
        "teaching_arrangement": content["units"][0],
    }
    state = {
        "context": _context(),
        "plan": {
            "plan_hash": "p" * 64,
            "units": [planned],
            "teaching_arrangement": {"content": content, "hash": digest(content)},
            "integration_plan": None,
            "quality_layers": {
                "method_version": "quality-layers.v1",
                "correctness": True,
                "completeness": True,
                "quality_specific": False,
            },
            "blueprint": artifact,
        },
        "check_plan": {
            "check_items": [],
            "check_plan_hash": "k" * 64,
            "quality_layers": {
                "method_version": "quality-layers.v1",
                "correctness": True,
                "completeness": True,
                "quality_specific": False,
            },
        },
        "execution_policy": {"criterion_assessment": True},
    }
    unit = {
        "unit_id": "u1",
        "title": "Unit 1",
        "body": "Observable target content.",
        "items": [],
        "blueprint_execution": {"status": "compliant", "deviations": []},
    }
    requests, semantic = requests_for_unit(state, unit)
    blueprint_rows = [row for row in requests if row["check_id"].startswith("A.blueprint_design.")]
    assert semantic == []
    assert len(blueprint_rows) == 1
    assert blueprint_rows[0]["criterion"]["target"] == "unit"
    assert blueprint_rows[0]["a_target"]["decision_id"] == "d1"

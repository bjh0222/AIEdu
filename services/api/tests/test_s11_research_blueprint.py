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
        "spec_bindings": [],
        "quality_profiles": [],
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


def test_a0_allows_autonomous_strategy_to_name_more_than_three_queries():
    from app.v2.planning import research_strategy

    value = _strategy()
    value["search_strategy"]["queries"] = [
        "query one", "query two", "query three", "query four", "query five",
    ]
    normalized = research_strategy(value)
    assert len(normalized["content"]["search_strategy"]["queries"]) == 5


def test_a1_research_projection_is_bounded_and_preserves_exact_source_excerpts():
    from app.v2.planning import planning_research_projection, research_strategy

    strategy = research_strategy(_strategy())
    text = "unrelated prefix " + ("assessment item design " * 2000) + "tail"
    result = {
        "sources": [{
            "source_id": "public-1",
            "source_hash": sha(text),
            "url": "https://example.test/source",
            "text": text,
        }],
        "source_claims": [{"claim_id": "c1", "source_id": "public-1", "quote": "assessment item design"}],
    }
    projected = planning_research_projection(
        result, strategy, per_source_chars=1200, total_chars=1200)
    source = projected["sources"][0]
    assert projected["projected_source_chars"] <= 1200
    assert projected["full_source_chars"] == len(text)
    assert source["full_text_chars"] == len(text)
    assert source["source_hash"] == sha(text)
    assert source["excerpts"]
    for excerpt in source["excerpts"]:
        assert text[excerpt["start_char"]:excerpt["end_char"]] == excerpt["text"]
    assert projected["source_claims"][0]["source_id"] == "public-1"


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
    aliased = bind_a1_artifact(
        {"blueprint": _blueprint(), "evidence": [_evidence()]},
        teaching_content=_teaching(), context=_context(), state=state,
    )
    assert aliased["hash"] == artifact["hash"]
    assert aliased["evidence_hash"] == artifact["evidence_hash"]


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


@pytest.mark.parametrize("field", ["evidence_records", "evidence"])
def test_nested_blueprint_evidence_is_moved_losslessly_to_the_top_level(field):
    from app.v2.planning import bind_a1_artifact

    state = {
        "research_result": {
            "sources": [{"source_id": "m1", "source_hash": sha("source")}],
            "source_claims": [],
        },
        "plan": {},
    }
    blueprint = _blueprint()
    blueprint[field] = [_evidence()]
    artifact = bind_a1_artifact(
        {"blueprint": blueprint},
        teaching_content=_teaching(),
        context=_context(),
        state=state,
    )
    assert artifact["evidence_records"] == [_evidence()]
    assert artifact["normalizations"] == [{
        "from": ["blueprint", field],
        "to": ["evidence_records"],
        "reason": "blueprint_evidence_moved_to_top_level",
    }]


def test_blueprint_completes_reciprocal_evidence_decision_links_without_changing_evidence_text():
    from app.v2.planning import bind_a1_artifact

    state = {"research_result": {"sources": [{"source_id": "m1", "source_hash": sha("source")}]}}
    blueprint = _blueprint()
    blueprint["decisions"].append({
        "decision_id": "d2",
        "statement": "Use a second decision.",
        "requirement": "should_implement",
        "blueprint_unit_ids": ["bu1"],
        "evidence_ids": ["e1"],
        "acceptance_method": "Locate the implementation.",
    })
    evidence = _evidence()
    evidence["used_for"] = ["d1"]
    artifact = bind_a1_artifact(
        {"blueprint": blueprint, "evidence": [evidence]},
        teaching_content=_teaching(), context=_context(), state=state,
    )
    normalized = artifact["evidence_records"][0]
    assert normalized["claim"] == evidence["claim"]
    assert normalized["location"] == evidence["location"]
    assert set(normalized["used_for"]) == {"d1", "d2"}


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


def test_b_generation_payload_scopes_blueprint_and_teaching_arrangement_to_current_unit():
    from app.v2.planning import bind_a1_artifact, generation_blueprint, generation_teaching_arrangement
    from app.v2.worker import generation_user_payload

    blueprint_content = _blueprint()
    blueprint_content["structure"]["units"].append({
        "blueprint_unit_id": "bu2",
        "title": "Unit 2",
        "purpose": "Second unit.",
        "required_content": ["Second target"],
        "depends_on_titles": [],
        "knowledge_relations": [],
    })
    blueprint_content["decisions"].append({
        "decision_id": "d2",
        "statement": "The second unit has one item.",
        "requirement": "must_follow",
        "blueprint_unit_ids": ["bu2"],
        "evidence_ids": ["e1"],
        "acceptance_method": "Count the second unit.",
    })
    artifact = bind_a1_artifact(
        {"blueprint": blueprint_content, "evidence_records": [_evidence()]},
        teaching_content={"units": [{"title": "Unit 1", "artifact_role": "tutorial"},
                                    {"title": "Unit 2", "artifact_role": "tutorial"}]},
        context=_context(),
        state={"research_result": {
            "sources": [{"source_id": "m1", "source_hash": sha("source")}],
            "source_claims": [],
        }, "plan": {}},
    )
    arrangement = {
        "hash": "a" * 64,
        "content": {
            "goals": ["global"],
            "units": [{"title": "Unit 1", "quantity": 2},
                      {"title": "Unit 2", "quantity": 4}],
        },
    }
    projected_blueprint = generation_blueprint(artifact, "Unit 1")
    projected_arrangement = generation_teaching_arrangement(arrangement, "Unit 1")
    assert [row["title"] for row in projected_blueprint["content"]["structure"]["units"]] == ["Unit 1"]
    assert [row["decision_id"] for row in projected_blueprint["content"]["decisions"]] == ["d1"]
    assert projected_blueprint["source_hash"] == artifact["hash"]
    assert [row["title"] for row in projected_arrangement["content"]["units"]] == ["Unit 1"]
    assert projected_arrangement["source_hash"] == arrangement["hash"]

    payload = generation_user_payload(
        {"context": _context(), "plan": {
            "blueprint": artifact,
            "teaching_arrangement": arrangement,
        }},
        {
            "title": "Unit 1",
            "artifact_role": "tutorial",
            "teaching_arrangement": {"quantity": 2},
            "subject_bindings": [],
        },
        upstream=[],
        material_chunks=[],
        knowledge_text="",
    )
    assert payload["blueprint"] == projected_blueprint
    assert payload["teaching_arrangement"] == projected_arrangement
    assert payload["teaching_arrangement"]["content"]["units"] == [{"title": "Unit 1", "quantity": 2}]


def test_b_generation_payload_rejects_unbound_unit_scope():
    from app.v2.planning import generation_blueprint, generation_teaching_arrangement

    blueprint = {"content": _blueprint()}
    with pytest.raises(DomainError, match="BLUEPRINT_GENERATION_SCOPE_UNBOUND"):
        generation_blueprint(blueprint, "Unknown")
    with pytest.raises(DomainError, match="TEACHING_ARRANGEMENT_GENERATION_SCOPE_UNBOUND"):
        generation_teaching_arrangement(
            {"content": {"units": [{"title": "Unit 1"}]}}, "Unknown")


def test_repair_generation_plan_exposes_only_target_unit_blueprint():
    from app.v2.planning import generation_plan_for_units

    content = _blueprint()
    content["structure"]["units"].append({
        "blueprint_unit_id": "bu2",
        "title": "Unit 2",
        "purpose": "Second unit.",
        "required_content": ["Second target"],
        "depends_on_titles": [],
        "knowledge_relations": [],
    })
    content["decisions"].append({
        "decision_id": "d2",
        "statement": "The second unit has one item.",
        "requirement": "must_follow",
        "blueprint_unit_ids": ["bu2"],
        "evidence_ids": ["e1"],
        "acceptance_method": "Count the second unit.",
    })
    plan = {
        "plan_hash": "p" * 64,
        "units": [{"unit_id": "u1", "title": "Unit 1"},
                  {"unit_id": "u2", "title": "Unit 2"}],
        "blueprint": {"hash": "b" * 64, "evidence_hash": "e" * 64,
                      "content": content, "evidence_records": [_evidence()]},
        "teaching_arrangement": {
            "hash": "a" * 64,
            "content": {"units": [{"title": "Unit 1"},
                                  {"title": "Unit 2"}]},
        },
    }
    projected = generation_plan_for_units(plan, ["u2"])
    assert [row["unit_id"] for row in projected["units"]] == ["u2"]
    assert [row["title"] for row in projected["blueprint"]["content"]["structure"]["units"]] == ["Unit 2"]
    assert [row["decision_id"] for row in projected["blueprint"]["content"]["decisions"]] == ["d2"]
    assert [row["title"] for row in projected["teaching_arrangement"]["content"]["units"]] == ["Unit 2"]
    assert projected["source_plan_hash"] == plan["plan_hash"]


def test_b_generation_payload_preserves_legacy_request_shape_without_blueprint():
    from app.v2.worker import generation_user_payload

    arrangement = {"content": {"units": [{"title": "Unit 1", "quantity": 2}]}}
    payload = generation_user_payload(
        {"context": _context(), "plan": {"teaching_arrangement": arrangement}},
        {
            "title": "Unit 1",
            "artifact_role": "tutorial",
            "teaching_arrangement": {"quantity": 2},
            "subject_bindings": [],
        },
        upstream=[],
        material_chunks=[],
        knowledge_text="",
    )
    assert payload["teaching_arrangement"] == arrangement
    assert "blueprint" not in payload


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


def test_knowledge_chain_inventory_lists_every_unbound_endpoint_across_units():
    from app.v2.worker import knowledge_chain_endpoint_inventory

    content = {
        "learner_prerequisites": ["P"],
        "units": [
            {"title": "U1", "concepts": ["A"], "knowledge_chain": [
                {"source_concept": "A", "target_concept": "bad-1"},
            ]},
            {"title": "U2", "concepts": ["B"], "knowledge_chain": [
                {"source_concept": "bad-2", "target_concept": "B"},
            ]},
        ],
    }
    rows = knowledge_chain_endpoint_inventory(content)
    assert [(row["unit_title"], row["field"], row["unbound_endpoint"]) for row in rows] == [
        ("U1", "target_concept", "bad-1"),
        ("U2", "source_concept", "bad-2"),
    ]


def test_research_source_catalog_includes_frozen_evaluation_framework_evidence_refs():
    from app.v2.planning import research_source_catalog

    context = {
        "evaluation_frameworks": {
            "frameworks": [{
                "id": "math-framework",
                "source_hash": "f" * 64,
                "evidence_refs": [{"id": "E15"}, {"id": "E4"}],
            }],
        },
    }
    catalog = research_source_catalog({"plan": {}}, context)
    assert catalog["math-framework"] == "f" * 64
    assert "math-framework:E15" in catalog
    assert "math-framework:E4" in catalog

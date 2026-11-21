"""Frozen A targets are bound before B and executed as explicit C rows."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest, sha


def profile(domain="math_logic"):
    from app.v2.profiles import domain_profile
    return domain_profile(domain, "exercise")


def quality_refs(value):
    return [{
        "profile_id": item["profile_id"],
        "profile_version": item["version"],
        "profile_hash": item["profile_hash"],
        "rule_id": rule["rule_id"],
        "rule_version": rule["version"],
        "criterion_hash": rule["criterion_hash"],
    } for item in value for rule in item["rules"]]


def design():
    return {
        "mode": "chapter_practice",
        "grouping_basis": "actual solution method",
        "rationale": "Use the requested method organization and one executable item.",
        "request_quotes": ["一道解答题"],
        "question_form": "constructed_response_only",
        "explanation_policy": "full",
        "answer_placement": "unit_end",
        "template_adaptations": [],
        "difficulty_target": None,
        "quantity_per_group": 1,
    }


def context(domain="math_logic"):
    item_profile = profile(domain)
    return {
        "raw_input": "围绕实际方法生成一道解答题，并给出同题答案。",
        "hard_constraints": [],
        "domain_key": domain,
        "canonical_domain_key": domain,
        "branch": "",
        "runtime_target": "",
        "requested_artifacts": ["exercise", "answer"],
        "material_ids": ["m1"],
        "spec_bindings": [{"scene": "exercise", "artifact_role": "exercise", "snapshot": {}}],
        "subject_bindings": [{"domain_key": domain, "branch": "", "unit_keys": []}],
        "quality_profiles": [item_profile],
        "output_design": design(),
    }


def arrangement(ctx, *, blueprint=None, knowledge=None, misconception=None):
    refs = quality_refs(ctx["quality_profiles"])
    return {
        "goals": ["Verify the actual method"],
        "learner_prerequisites": ["prerequisite concept"],
        "scope": "focused",
        "quality_targets": ["Executable method and evidence"],
        "quality_target_refs": refs,
        "unresolved_conflicts": [],
        "generation_prompt": "Generate one self-contained constructed-response item.",
        "output_design": design(),
        "units": [{
            "title": "Method A",
            "concepts": ["concept A", "concept B"],
            "question_types": ["constructed response"],
            "difficulty_basis": {"operations": ["analysis"]},
            "quantity": 1,
            "material_mapping": ["m1"],
            "acceptance_criteria": ["one coupled item"],
            "depends_on_titles": [],
            "artifact_role": "exercise",
            "knowledge_chain": knowledge or [],
            "misconceptions": misconception or [],
            "question_blueprint": blueprint or [{
                "group": "Method A",
                "target": "Apply the method to a new but solvable premise.",
                "question_form": "constructed_response",
                "quantity": 1,
                "answer_requirement": "Final answer and checkable reasoning belong to the item.",
                "difficulty_basis": {"dependency_depth": 2},
            }],
        }],
    }


def plan():
    return {
        "schema_version": "aiedu.plan.v1",
        "units": [{
            "unit_id": "u",
            "title": "Method A",
            "artifact_role": "exercise",
            "depends_on": [],
            "subject_bindings": [{"domain_key": "math_logic", "branch": "", "unit_keys": []}],
        }],
        "output_design": design(),
    }


def frozen(ctx, value):
    from app.v2.planning import apply_arrangement, arrangement as validate
    return apply_arrangement(plan(), validate({"teaching_arrangement": value}),
                             context=ctx, enforce_contract=True, require_complete=True)


def test_quality_refs_require_exact_profile_rule_hashes_and_complete_coverage():
    ctx = context()
    valid = arrangement(ctx)
    frozen(ctx, deepcopy(valid))

    bad = deepcopy(valid)
    bad["quality_target_refs"][0]["criterion_hash"] = "0" * 64
    with pytest.raises(DomainError, match="QUALITY_TARGET_REFERENCE_UNBOUND"):
        frozen(ctx, bad)

    missing = deepcopy(valid)
    missing["quality_target_refs"] = []
    with pytest.raises(DomainError, match="QUALITY_TARGET_REFS_REQUIRED"):
        frozen(ctx, missing)

    duplicate = deepcopy(valid)
    duplicate["quality_target_refs"].append(deepcopy(duplicate["quality_target_refs"][0]))
    with pytest.raises(DomainError, match="QUALITY_TARGET_REFERENCE_DUPLICATE"):
        frozen(ctx, duplicate)

    missing_binding = context()
    missing_binding["quality_profiles"][0].pop("profile_hash")
    with pytest.raises(DomainError, match="QUALITY_PROFILE_BINDING_REQUIRED"):
        frozen(missing_binding, arrangement(ctx))

    no_display = deepcopy(valid)
    no_display["quality_targets"] = []
    with pytest.raises(DomainError, match="QUALITY_TARGETS_REQUIRED"):
        frozen(ctx, no_display)


@pytest.mark.parametrize("fault,code", [
    ("missing", "QUESTION_BLUEPRINT_REQUIRED"),
    ("sum", "QUESTION_BLUEPRINT_QUANTITY_MISMATCH"),
    ("form", "QUESTION_BLUEPRINT_FORM_MISMATCH"),
])
def test_item_blueprint_must_close_quantity_and_form_before_b(fault, code):
    ctx = context()
    value = arrangement(ctx)
    blueprint = value["units"][0]["question_blueprint"]
    if fault == "missing":
        blueprint.clear()
    elif fault == "sum":
        blueprint[0]["quantity"] = 2
    else:
        blueprint[0]["question_form"] = "judgement"
    with pytest.raises(DomainError, match=code):
        frozen(ctx, value)


def test_A_cannot_reclassify_a_frozen_item_unit_as_a_nonitem_role():
    ctx = context()
    value = arrangement(ctx)
    value["units"][0]["artifact_role"] = "tutorial"
    with pytest.raises(DomainError, match="TEACHING_ARRANGEMENT_ARTIFACT_ROLE_MISMATCH"):
        frozen(ctx, value)


def test_knowledge_links_and_misconceptions_bind_to_frozen_nodes_and_groups():
    ctx = context()
    valid_link = {
        "source_concept": "concept A",
        "relation": "prerequisite",
        "target_concept": "concept B",
        "condition": "Only under the stated premise.",
        "evidence_ref": "m1",
    }
    valid_misconception = {
        "plausible_idea": "Any familiar method works without checking its premise.",
        "failure_boundary": "The premise fails in the boundary case.",
        "contrast_or_counterexample": "Use a counterexample with the failed premise.",
        "blueprint_group": "Method A",
    }
    frozen(ctx, arrangement(ctx, knowledge=[valid_link],
                            misconception=[valid_misconception]))

    bad = arrangement(ctx, knowledge=[deepcopy(valid_link)])
    bad["units"][0]["knowledge_chain"][0]["target_concept"] = "unbound concept"
    with pytest.raises(DomainError, match="KNOWLEDGE_CHAIN_ENDPOINT_UNBOUND"):
        frozen(ctx, bad)

    bad = arrangement(ctx, knowledge=[deepcopy(valid_link)])
    bad["units"][0]["knowledge_chain"][0]["evidence_ref"] = "invented source"
    with pytest.raises(DomainError, match="KNOWLEDGE_CHAIN_EVIDENCE_UNBOUND"):
        frozen(ctx, bad)

    bad = arrangement(ctx, misconception=[deepcopy(valid_misconception)])
    bad["units"][0]["misconceptions"][0]["blueprint_group"] = "Other group"
    with pytest.raises(DomainError, match="MISCONCEPTION_BLUEPRINT_UNBOUND"):
        frozen(ctx, bad)

    bad = arrangement(ctx, misconception=[deepcopy(valid_misconception)])
    bad["units"][0]["misconceptions"][0]["blueprint_group"] = ""
    with pytest.raises(DomainError, match="MISCONCEPTION_BLUEPRINT_REQUIRED"):
        frozen(ctx, bad)

    bad = arrangement(ctx)
    bad["units"][0]["acceptance_criteria"] *= 2
    with pytest.raises(DomainError, match="UNIT_ACCEPTANCE_CRITERIA_INVALID"):
        frozen(ctx, bad)

    bad = arrangement(ctx, knowledge=[valid_link, deepcopy(valid_link)])
    with pytest.raises(DomainError, match="KNOWLEDGE_CHAIN_DUPLICATE"):
        frozen(ctx, bad)


def review_state(domain="math_logic"):
    from app.v2.quality import compile_plan
    ctx = context(domain)
    ctx["subject_bindings"] = [{"domain_key": domain, "branch": "", "unit_keys": []}]
    profile_rows = ctx["quality_profiles"]
    link = {
        "source_concept": "concept A",
        "relation": "prerequisite",
        "target_concept": "concept B",
        "condition": "Only under the stated premise.",
        "evidence_ref": "m1",
    }
    misconception = {
        "plausible_idea": "Surface pattern is sufficient.",
        "failure_boundary": "The pattern fails at the boundary.",
        "contrast_or_counterexample": "A boundary counterexample distinguishes both.",
        "blueprint_group": "Method A",
    }
    value = arrangement(ctx, knowledge=[link], misconception=[misconception])
    value["units"][0]["quantity"] = 2
    value["units"][0]["question_blueprint"][0]["quantity"] = 2
    frozen_plan = frozen(ctx, value)
    frozen_plan["units"][0]["subject_bindings"] = ctx["subject_bindings"]
    items = [{
        "item_id": item_id,
        "ordinal": ordinal,
        "stem_markdown": f"Apply the frozen method to a new premise {ordinal}.",
        "answer": {"final": f"A checked result {ordinal}.",
                   "reasoning": f"Use the premise, relation and boundary {ordinal}."},
    } for ordinal, item_id in enumerate(("i", "j"), 1)]
    body = "# Method A\n\n" + "\n\n".join(
        item["stem_markdown"] + "\n\n" + item["answer"]["final"] for item in items)
    unit = frozen_plan["units"][0] | {
        "body": body,
        "content_hash": sha(body),
        "items": items,
        "output_design": design(),
    }
    state = {
        "context": ctx,
        "plan": frozen_plan,
        "check_plan": compile_plan(ctx),
        "execution_policy": {"check_concurrency": 1, "network_tool_rounds": 0},
    }
    return state, unit, profile_rows


def test_C_gets_one_bound_request_per_quality_target_blueprint_link_and_misconception():
    from app.v2.criterion_review import requests_for_unit
    state, unit, _ = review_state()
    requests, _ = requests_for_unit(state, unit)
    rule_id = state["context"]["quality_profiles"][0]["rules"][0]["rule_id"]
    quality = [r for r in requests if r.get("a_quality_refs")]
    assert quality and all(r["criterion"]["check_id"] == rule_id for r in quality)
    assert any(r["check_id"].startswith("A.blueprint.") for r in requests)
    assert any(r["check_id"].startswith("A.knowledge.") for r in requests)
    assert any(r["check_id"].startswith("A.misconception.") for r in requests)
    assert any(r["check_id"].startswith("A.acceptance.") for r in requests)
    assert all(r["criterion"]["criterion_hash"] == digest({
        k: v for k, v in r["criterion"].items() if k != "criterion_hash"})
        for r in requests)


def test_all_21_domain_packs_compile_and_execute_against_the_same_fake_C(monkeypatch):
    from app.v2.assessment_assets import DIMENSIONS
    from app.v2.criterion_review import evaluate_criteria

    def generate(*args, **kwargs):
        data = kwargs["data"]
        assessments = []
        for request in data["requests"]:
            own = "item:" + request["item_id"] + ":stem_markdown" \
                if request.get("item_id") else "unit:body"
            clauses = [{
                "clause_id": clause_id,
                "result": "satisfied",
                "reason": "Synthetic contract execution with a literal content span.",
                "evidence": [{"field": own, "quote": data["fields"][own]}] + ([{
                    "field": next(field for field in data["fields"]
                                  if field.startswith("item:") and not field.startswith(
                                      "item:" + request["item_id"] + ":")
                                  and field.endswith(":stem_markdown")),
                    "quote": next(value for field, value in data["fields"].items()
                                  if field.startswith("item:") and not field.startswith(
                                      "item:" + request["item_id"] + ":")
                                  and field.endswith(":stem_markdown")),
                }] if request["check_id"].startswith("Q4.") and clause_id == "transfer" else []),
            } for clause_id in request["criterion"]["clauses"]]
            rating = 2 if request["criterion"].get("rating_scale") else None
            assessments.append({
                "request_id": request["request_id"],
                "clauses": clauses,
                "rating": rating,
            })
        return {"assessments": assessments}, {"call_id": "synthetic-C"}, {}

    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    assert len(DIMENSIONS) == 21
    for domain in DIMENSIONS:
        state, unit, _ = review_state(domain)
        report = evaluate_criteria("run", "worker", 1, state, [unit], "version")
        assert report["status"] == "completed", domain
        assert report["coverage"]["complete_observations"] == report["coverage"]["expected"], domain
        assert any(row["check_id"].startswith(f"I.{domain}.")
                   for row in report["criteria"]), domain

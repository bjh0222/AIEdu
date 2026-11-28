"""The generic-plus-domain quality contract is executable, not documentation."""

from app.v2.assessment_assets import rubric
from app.v2.check_criteria import GENERAL, criterion
from app.v2.profiles import domain_profile
from app.v2.quality import compile_plan


def test_every_catalog_domain_has_common_and_domain_item_dimensions():
    # Keep the website extensible: a domain with no formal oracle still gets
    # a domain pack and honest unknowns rather than inheriting mathematics.
    from app.learning.disciplines import SUBJECTS

    for domain, _, _ in SUBJECTS:
        value = rubric(domain)
        assert value is not None, domain
        assert {d["id"] for d in value["common_dimensions"]} == set(GENERAL_ID_DIMENSIONS)
        assert value["dimensions"]
        assert value["calibration_status"] == "not_calibrated"
        assert value["aggregate_score"] is None


GENERAL_ID_DIMENSIONS = {
    "semantic_alignment", "knowledge_integration", "assessment_design", "evidence_boundaries",
}


def test_plan_binds_generic_and_subject_specific_criteria_without_math_leakage():
    context = {
        "domain_key": "history_culture",
        "subject_bindings": [{"domain_key": "history_culture", "branch": ""}],
        "spec_bindings": [{"scene": "exercise", "artifact_role": "exercise"}],
        "quality_profiles": [domain_profile("history_culture", "exercise")],
        "task_intent": {"conflicts": []},
    }
    plan = compile_plan(context)
    items = {item["item_id"]: item for item in plan["check_items"]}
    assert set(GENERAL) <= items.keys()
    assert all(items[key]["verifier_binding"] == "criterion_review.v1" for key in GENERAL)
    subject = [item for item in items.values() if item["item_id"].startswith("I.history_culture.")]
    assert subject
    assert all(item["verifier_binding"] == "criterion_review.v1" for item in subject)
    assert not any(item["verifier_binding"] == "independent_math.v1" for item in subject)
    assert all(item.get("criterion_contract") for item in [items[key] for key in GENERAL])
    assert all(item.get("criterion_contract", {}).get("rating_scale") for item in subject)


def test_criterion_contracts_expose_clause_level_evidence_and_scale():
    generic = criterion({"item_id": "G2.knowledge_integration", "pass_condition": "fallback"})
    assert set(generic["clauses"]) == {"relations", "synthesis"}
    assert generic["target"] == "unit"

    domain_item = {
        "item_id": "I.history_culture.provenance.exercise",
        "pass_condition": "fallback",
        "quality_dimension": {"id": "provenance", "criterion": "来源可定位", "source_ref": "test"},
    }
    contract = criterion(domain_item)
    assert contract["target"] == "each_item"
    assert contract["rating_scale"] == {"0": "有可定位反证或明确缺陷", "1": "部分证据、限制或边界未闭合", "2": "适用要求均有可定位支持"}
    assert contract["clauses"] == {"dimension": "来源可定位"}

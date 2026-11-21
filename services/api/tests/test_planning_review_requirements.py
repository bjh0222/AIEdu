"""A targets reach C without B instructions, answers, or earlier judgements."""
from copy import deepcopy

from app.v2.planning import review_requirements


def test_targets_are_local_hash_bound_and_do_not_contain_generation_or_prior_judgement():
    state = {"context": {"raw_input": "three difficult items for each actual method", "context_hash": "ctx"},
        "plan": {"plan_hash": "plan", "teaching_arrangement": {"hash": "arr", "content": {
            "goals": ["actual method classification"], "quality_targets": ["cognitive demand"],
            "generation_prompt": "SECRET_B_INSTRUCTION"}}, "units": [
                {"unit_id": "u", "title": "local method", "teaching_arrangement": {
                    "quantity": 3, "concepts": ["local concept"], "difficulty_basis": {"nonroutine": 2}}},
                {"unit_id": "v", "title": "other method", "teaching_arrangement": {"quantity": 7}}]},
        "prior_judgement": "SECRET_PRIOR", "model": "SECRET_MODEL"}
    before = deepcopy(state)
    contract = review_requirements(state, {"unit_id": "u", "answer": "SECRET_ANSWER"})
    assert contract["unit_targets"]["quantity"] == 3
    assert contract["global_targets"]["quality_targets"] == ["cognitive demand"]
    assert contract["arrangement_hash"] == "arr" and contract["plan_hash"] == "plan"
    assert contract["scope"] == "single_planned_unit"
    assert "SECRET" not in str(contract)
    contract["unit_targets"]["quantity"] = 9
    assert state == before


def test_research_C_consumes_same_frozen_local_targets(monkeypatch):
    from app.v2 import inference
    from app.v2.import_profiles import review_research_rubrics
    from app.v2.contracts import sha
    state = {"context": {"raw_input": "raw goal", "research_rubrics": [{"id": "r", "version": 1, "hash": "rh",
        "content": {"dimensions": [{"dimension_id": "coverage", "scope": "knowledge",
            "source_refs": ["claim:coverage"], "indicators": [{"indicator_id": "coverage-count"}]}],
            "source_claims": [{"claim_id": "claim:coverage", "source_id": "fixture",
                "quote": "A complete frozen claim."}]}}]},
        "plan": {"plan_hash": "ph", "units": [{"unit_id": "u", "title": "method",
            "teaching_arrangement": {"quantity": 3, "concepts": ["determinant"]}}]}}
    seen = []
    def invoke(*args, **kwargs):
        from app.v2.skills import entry as skill_entry, review_skill
        skill = review_skill()
        assert kwargs["entries"].count(skill_entry(skill, "C")) == 1
        assert f"# {skill['skill_id']}.v{skill['version']}" in kwargs["system"]
        seen.append(kwargs["data"])
        return {"dimensions": [{"dimension_id": "coverage", "result": "satisfied", "score": 2,
            "reason": "The local candidate supports this synthetic observation.",
            "indicators": [{"indicator_id": "coverage-count", "applicable": True,
                "numerator": 1, "denominator": 1, "unit": "items",
                "enum_value": None,
                "evidence": [{"field": "body", "quote": "candidate"}],
                "unknown_reason": None}],
            "evidence": [{"field": "body", "quote": "candidate"}]}]}, {"call_id": "c"}
    monkeypatch.setattr(inference, "invoke", invoke)
    review_research_rubrics("run", "w", 0, state, [{"unit_id": "u", "body": "candidate", "content_hash": sha("candidate")}], "v")
    assert len(seen) == 1
    assert seen[0]["teaching_requirements"] == review_requirements(state, {"unit_id": "u"})

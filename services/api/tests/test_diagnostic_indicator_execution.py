"""A dimension score is withheld unless its actual indicators are observed."""
from copy import deepcopy

import pytest


def test_counts_evidence_unknown_and_nonapplicability(monkeypatch):
    from app.v2 import inference
    from app.v2.import_profiles import review_bound_profiles
    from app.v2.contracts import sha
    dimension = {"dimension_id": "steps", "indicators": [{"indicator_id": "steps-count"}]}
    binding = {"profile": {"id": "p", "version": 1, "hash": "p", "content": {}},
        "rubric": {"id": "r", "version": 1, "hash": "r", "content": {"dimensions": [dimension]}}, "binding_hash": "b"}
    unit = {"unit_id": "u", "body": "two observable steps", "content_hash": sha("two observable steps")}
    observation = {"dimension_id": "steps", "result": "satisfied", "score": 2, "reason": "countable",
        "evidence": [{"field": "body", "quote": unit["body"]}]}
    monkeypatch.setattr(inference, "invoke", lambda *a, **k: ({"dimensions": [deepcopy(observation)]}, {"call_id": "C"}))
    def review():
        return review_bound_profiles("r", "w", 1, {"context": {"import_understanding": [binding]}}, [unit], "v")["dimensions"][0]
    assert review()["score"] is None
    observation["indicators"] = [{"indicator_id": "steps-count", "applicable": True, "numerator": 2, "denominator": 2,
        "unit": "steps", "evidence": observation["evidence"]}]
    assert review()["score"] == 2
    observation["indicators"][0]["denominator"] = None
    assert review()["score"] is None
    observation.update(result="not_applicable", score=None, reason="no practice requested")
    assert review()["result"] == "not_applicable" and review()["score"] is None
    observation.update(result="undecidable", score=0)
    assert review()["score"] is None


def test_all_dimensions_batched_with_same_full_binding(monkeypatch):
    from app.v2 import inference
    from app.v2.import_profiles import review_bound_profiles
    dimensions = [{"dimension_id": f"d{i}", "indicators": []} for i in range(7)]
    binding = {"profile": {"id": "p", "version": 1, "hash": "ph", "content": {}},
        "rubric": {"id": "r", "version": 1, "hash": "rh", "content": {"dimensions": dimensions}}, "binding_hash": "bh"}
    calls = []
    def invoke(*args, **kwargs):
        data = kwargs["data"]
        calls.append(data)
        return {"dimensions": [{"dimension_id": d, "result": "not_applicable", "score": None,
            "reason": "no such material"} for d in data["target_dimension_ids"]]}, {"call_id": str(len(calls))}
    monkeypatch.setattr(inference, "invoke", invoke)
    result = review_bound_profiles("r", "w", 1, {"context": {"import_understanding": [binding]}},
        [{"unit_id": "u", "body": "fixed", "content_hash": "hash"}], "v")
    assert [len(c["target_dimension_ids"]) for c in calls] == [3, 3, 1]
    assert all(c["import_understanding"] == binding for c in calls)
    assert [r["dimension_id"] for r in result["dimensions"]] == [f"d{i}" for i in range(7)]
    assert len(result["audits"]) == 3


def test_research_rubric_executes_without_import_profile(monkeypatch):
    from app.v2 import inference
    from app.v2.import_profiles import review_research_rubrics
    from app.v2.contracts import sha

    dimension = {"dimension_id": "structure", "name": "结构", "scope": "knowledge",
        "source_refs": ["claim:1"], "indicators": [{"indicator_id": "concept-count"}]}
    binding = {"id": "research-rubric", "version": 1, "hash": "r" * 64,
        "content": {"dimensions": [dimension], "source_claims": [{"claim_id": "claim:1"}]},
        "binding_hash": "b"}
    unit = {"unit_id": "u", "body": "概念结构清晰", "content_hash": sha("概念结构清晰")}
    def invoke(*args, **kwargs):
        return {"dimensions": [{"dimension_id": "structure", "result": "satisfied", "score": 2,
            "reason": "证据明确", "evidence": [{"field": "body", "quote": "概念结构清晰"}],
            "indicators": [{"indicator_id": "concept-count", "applicable": True, "numerator": 1,
                "denominator": 1, "unit": "observation", "evidence": [{"field": "body", "quote": "概念结构清晰"}]}]}]}, {"call_id": "C", "model_family": "glm"}
    monkeypatch.setattr(inference, "invoke", invoke)
    result = review_research_rubrics("r", "w", 1, {"context": {"research_rubrics": [binding]}}, [unit], "v")
    assert result["dimensions"][0]["score"] == 2
    assert result["dimensions"][0]["profile_ref"]["reason"] == "NO_IMPORTED_MATERIAL"
    assert result["method_version"] == "research-rubric-model-review.v1"


def test_research_review_is_a_required_quality_check_and_failure_is_repairable():
    from app.v2.checker import _apply_research_review
    from app.v2.contracts import digest, sha
    from app.v2.quality import compile_plan

    binding = {"id": "r", "version": 1, "hash": "r" * 64, "binding_hash": "b",
        "content": {"dimensions": [{"dimension_id": "correctness", "scope": "knowledge"}]}}
    plan = compile_plan({"domain_key": "general", "task_intent": {"conflicts": []},
        "spec_bindings": [], "quality_profiles": [], "research_rubrics": [binding]})
    unit = {"unit_id": "u", "body": "错误结论", "content_hash": sha("错误结论")}
    result = {"checks": [{"item_id": item["item_id"], "result": "satisfied"}
        for item in plan["check_items"] if item.get("verifier_binding") != "research_rubric.v1"],
        "findings": [], "diagnostic_review": {"calibration_status": "not_calibrated",
        "version_id": "v", "processing_status": "complete",
        "dimensions": [{"dimension_id": "correctness", "unit_id": "u", "result": "violated", "score": 0,
            "processing_status": "complete", "rubric_ref": {k: binding[k] for k in ("id", "version", "hash")},
            "reason": "正文与研究量规不符", "evidence": [{"quote": "错误结论"}],
            "repair_condition": "修复后该维度需满足", "binding_hash": "b"}]}}
    state = {"check_plan": plan}
    _apply_research_review(result, state, [unit], "v")
    check = next(c for c in result["checks"] if c["item_id"] == "R.correctness")
    assert check["result"] == "violated"
    assert result["tqi_verdict"] == "needs_repair"
    assert not any(f.get("category") == "research_rubric" for f in result["findings"])


def test_research_content_batches_keep_complete_targets_sources_and_original_identity(monkeypatch):
    from app.v2 import inference
    from app.v2.adaptive_format import POLICY_PROMPT
    from app.v2.import_profiles import review_research_rubrics
    from app.v2.contracts import digest, sha
    from app.v2.research_length_recovery import REVIEW_OUTPUT_TOKENS
    dims = [{"dimension_id": f"d{i}", "scope": "knowledge", "source_refs": [f"claim:{i}"],
        "construct_definition": f"complete dimension {i}", "anchors": {"0": "negative", "1": "partial", "2": "positive"},
        "indicators": [{"indicator_id": "metric"}]} for i in range(5)]
    claims = [{"claim_id": f"claim:{i}", "source_id": f"s{i}", "quote": f"full literal source {i}"} for i in range(5)]
    rubric = {"rubric_id": "r", "dimensions": dims, "source_claims": claims,
        "source_catalog": [{"source_id": f"s{i}", "url": f"https://example.org/{i}"} for i in range(5)],
        "limitations": ["not professionally calibrated"], "scope_reviews": [{"history": "not an extra scoring instruction"}]}
    before = deepcopy(rubric)
    binding = {"id": "r", "version": 1, "hash": "whole-binding-hash", "binding_hash": "bh", "content": rubric}
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return {"dimensions": [{"dimension_id": d, "result": "not_applicable", "score": None,
            "reason": "Explicitly no such applicable content in this synthetic candidate.",
            "indicators": [{"indicator_id": "metric", "applicable": False,
                "numerator": None, "denominator": None, "unit": "items",
                "enum_value": None, "evidence": [],
                "unknown_reason": "No applicable synthetic content."}]}
            for d in kwargs["data"]["target_dimension_ids"]]}, {"call_id": str(len(calls))}
    monkeypatch.setattr(inference, "invoke", invoke)
    unit = {"unit_id": "u", "body": "fixed content", "content_hash": sha("fixed content")}
    result = review_research_rubrics("r", "w", 1, {"context": {"research_rubrics": [binding]}}, [unit], "v")
    assert [len(c["data"]["target_dimension_ids"]) for c in calls] == [2, 2, 1]
    assert [d for c in calls for d in c["data"]["research_rubric"]["dimensions"]] == dims
    assert [r["dimension_id"] for r in result["dimensions"]] == [f"d{i}" for i in range(5)]
    for call in calls:
        data = call["data"]
        targets = data["target_dimension_ids"]
        refs = {r for d in data["research_rubric"]["dimensions"] for r in d["source_refs"]}
        assert data["source_claims"] == [c for c in claims if c["claim_id"] in refs]
        assert data["research_rubric"]["source_claims"] == data["source_claims"]
        assert data["full_rubric_hash"] == digest(rubric) and data["binding_hash"] == "bh"
        assert data["rubric_ref"] == {"id": "r", "version": 1, "hash": "whole-binding-hash"}
        assert "scope_reviews" not in data["research_rubric"]
        # The real 8k/32k reasoning budgets exhausted before visible JSON. Keep
        # the complete two-dimension payload and reserve the bounded review
        # allowance; never truncate criteria to fit the output.
        assert call["output_tokens"] == REVIEW_OUTPUT_TOKENS and POLICY_PROMPT in call["system"]
    assert rubric == before and len(result["audits"]) == 3


def test_content_batching_cannot_hide_a_missing_referenced_source():
    from app.v2.contracts import DomainError
    from app.v2.import_profiles import _research_review_batches
    with pytest.raises(DomainError, match="RESEARCH_DIMENSION_SOURCE_UNBOUND"):
        list(_research_review_batches({"dimensions": [{"source_refs": ["missing-claim"]}], "source_claims": []}))

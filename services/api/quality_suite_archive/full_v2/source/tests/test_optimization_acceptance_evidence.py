"""A model's satisfied label cannot override a failed mechanical criterion."""
from app.v2.contracts import sha


def test_mechanical_limit_overrides_model_label(monkeypatch):
    from app.v2 import inference
    from app.v2.optimization_acceptance import verify
    monkeypatch.setattr(inference, "invoke", lambda *a, **k: ({"criteria": [{
        "item_id": "length", "result": "satisfied", "evidence": [{"unit_id": "u", "quote": "abcdef"}]}]}, {"call_id": "C"}))
    package = {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "length", "pass_condition": "limit 5",
        "verification_method": "count", "deterministic_test": {"method": "maximum_codepoints", "unit_ids": ["u"], "limit": 5}}]}
    unit = {"unit_id": "u", "body": "abcdef", "content_hash": sha("abcdef")}
    report = verify("r", "w", 1, {}, package, [unit])
    assert report["status"] == "unresolved"
    assert report["criteria"][0]["result"] == "violated"
    assert report["criteria"][0]["deterministic_evidence"]["observed"] == 6


def test_finite_batches_keep_the_entire_fixed_candidate(monkeypatch):
    from app.v2 import inference
    from app.v2.optimization_acceptance import verify
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs)
        return {"criteria": [{"item_id": c["item_id"], "result": "satisfied",
            "evidence": [{"unit_id": "u", "quote": "complete fixed candidate"}]} for c in kwargs["data"]["criteria"]]}, {"call_id": str(len(calls))}
    monkeypatch.setattr(inference, "invoke", invoke)
    package = {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": str(i), "pass_condition": "fixed"} for i in range(7)]}
    unit = {"unit_id": "u", "body": "complete fixed candidate", "content_hash": sha("complete fixed candidate")}
    report = verify("r", "w", 1, {}, package, [unit])
    assert [len(c["data"]["criteria"]) for c in calls] == [3, 3, 1]
    assert all(c["data"]["units"] == [unit] for c in calls)
    assert report["status"] == "satisfied" and len(report["criteria"]) == 7


def test_acceptance_sees_complete_candidate_items_and_frozen_arrangement(monkeypatch):
    from app.v2 import inference
    from app.v2.optimization_acceptance import verify
    unit = {"unit_id": "u", "body": "visible candidate", "content_hash": sha("visible candidate"),
            "items": [{"item_id": "i", "stem_markdown": "visible candidate",
                       "source_sections": [], "answer": {"final": "a", "reasoning": "fixed internal checking",
                                                          "display_reasoning": ""}}]}
    state = {"plan": {"units": [{"unit_id": "u", "teaching_arrangement": {
        "difficulty_basis": {"operations": ["proof"]}, "quantity": 3, "generation_prompt": "must not leak"}}]},
        "check_plan": {"check_plan_hash": "bound", "check_items": [{"item_id": "check", "required": True}]}}
    def invoke(*args, **kwargs):
        data = kwargs["data"]
        assert data["units"][0]["items"] == unit["items"]
        assert data["frozen_arrangement"][0]["arrangement"]["difficulty_basis"] == {"operations": ["proof"]}
        assert data["frozen_check_contract"]["check_plan_hash"] == "bound"
        assert "must not leak" not in str(data)
        return {"criteria": [{"item_id": "c", "result": "satisfied",
            "evidence": [{"unit_id": "u", "quote": "visible candidate"}]}]}, {"call_id": "c"}
    monkeypatch.setattr(inference, "invoke", invoke)
    assert verify("r", "w", 1, state, {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "c"}]}, [unit])["status"] == "satisfied"


def test_acceptance_has_metadata_provenance_and_execution_not_prior_judgment(monkeypatch):
    from app.v2 import inference
    from app.v2.optimization_acceptance import verify
    captured = []
    def invoke(*args, **kwargs):
        captured.append(kwargs["data"])
        return {"criteria": [{"item_id": "a", "result": "satisfied", "evidence": [
            {"unit_id": "u", "quote": "candidate"}]}]}, {"call_id": "acceptance"}
    monkeypatch.setattr(inference, "invoke", invoke)
    review = {"diagnostic_review": {"method_version": "bound.v2", "dimensions": [{
        "dimension_id": "d", "unit_id": "u", "result": "undecidable", "score": None,
        "reason": "must not leak prior reasoning", "binding_hash": "b", "audit": {"call_id": "C"}}]},
        "findings": [{"status": "unsupported", "claim": "must not leak"}], "tqi_verdict": "blocked"}
    unit = {"unit_id": "u", "body": "candidate", "content_hash": sha("candidate"), "title": "frozen", "artifact_role": "tutorial"}
    verify("r", "w", 1, {}, {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "a"}]}, [unit],
        recheck=review, network_trace={"receipts": [{"request_hash": "r"}], "citation_bindings": [{"status": "literal_bound"}]},
        candidate_citations=[{"source_id": "s", "source_quote": "source", "body_quote": "candidate"}])
    data = captured[0]
    assert data["units"] == [unit] and data["observed_codepoints"] == {"u": 9}
    assert data["citation_bindings"] == [{"status": "literal_bound"}]
    assert data["network_citations"][0]["body_quote"] == "candidate"
    assert "spec_bindings" in data["frozen_task_contract"] and data["candidate_structure"] == [{"unit_id": "u", "headings": []}]
    facts = data["candidate_execution_facts"]
    assert facts["dimensions"][0]["call_id"] == "C" and facts["dimensions"][0]["unknown_score_is_null"]
    assert facts["wrapper_confirmed_finding_count"] == 0
    assert "must not leak" not in str(data) and "tqi_verdict" not in str(data)


def test_exact_nested_candidate_metadata_is_only_locally_expanded():
    from types import SimpleNamespace
    import pytest
    from app.v2.contracts import DomainError, digest
    from app.v2.optimization import normalize_candidate_envelope, CANDIDATE_SCHEMA_VERSION
    package, version = {"a": "b"}, SimpleNamespace(id="v", content_hash="hash")
    metadata = {"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v",
        "base_content_hash": "hash", "package_hash": digest(package)}
    original = {"immutable_candidate_metadata": metadata, "units": [{"body": "unchanged"}]}
    fixed, audit = normalize_candidate_envelope(original, package, version)
    assert "schema_version" not in original and fixed["schema_version"] == CANDIDATE_SCHEMA_VERSION
    assert fixed["units"] == original["units"] and not audit["body_changed"] and not audit["new_provider_dispatch"]
    with pytest.raises(DomainError, match="STALE"):
        normalize_candidate_envelope(original | {"base_content_hash": "conflict"}, package, version)
    with pytest.raises(DomainError, match="STALE"):
        normalize_candidate_envelope(original | {"immutable_candidate_metadata": metadata | {"package_hash": "bad"}}, package, version)


def test_literals_cannot_be_created_by_unit_join_or_outside_scope(monkeypatch):
    from app.v2 import inference
    from app.v2.optimization_acceptance import verify
    units = [{"unit_id": key, "body": body, "content_hash": sha(body)} for key, body in (("a", "foo"), ("b", "bar"))]
    def invoke(*args, **kwargs):
        return {"criteria": [{"item_id": "literal", "result": "satisfied", "evidence": [{"unit_id": "a", "quote": "foo"}]}]}, {"call_id": "C"}
    monkeypatch.setattr(inference, "invoke", invoke)
    criterion = {"item_id": "literal", "deterministic_test": {"method": "required_literals", "unit_ids": ["a", "b"], "literals": ["foo\nbar"]}}
    package = {"frozen_bindings": {}, "scope": {"unit_ids": ["a", "b"]}, "acceptance_criteria": [criterion]}
    result = verify("r", "w", 1, {}, package, units)
    assert result["criteria"][0]["result"] == "violated"
    package["scope"]["unit_ids"] = ["b"]
    criterion["deterministic_test"] = {"method": "required_literals", "unit_ids": ["a"], "literals": ["foo"]}
    result = verify("r", "w", 1, {}, package, units)
    assert result["criteria"][0]["result"] == "undecidable" and not result["criteria"][0]["evidence"]


def test_final_candidate_rebind_rejects_duplicate_unchanged_unit_quotes():
    from app.v2.generation_tools import bind_citations
    receipt = {"source_id": "s", "text": "source", "source_hash": "hash"}
    cite = [{"source_id": "s", "source_quote": "source", "body_quote": "duplicate"}]
    candidate = {"network_citations": cite, "units": [{"body": "duplicate"}]}
    assert bind_citations(candidate, [receipt])[0]["status"] == "literal_bound"
    candidate["units"].append({"body": "unchanged duplicate"})
    assert bind_citations(candidate, [receipt])[0]["status"] == "unsupported"
    candidate["units"] = [{"body": "foo"}, {"body": "bar"}]
    candidate["network_citations"][0]["body_quote"] = "foo\nbar"
    assert bind_citations(candidate, [receipt])[0]["status"] == "unsupported"

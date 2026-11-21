"""Synthetic positive/negative/unknown clause bindings and Q4 aggregation."""
from copy import deepcopy

import pytest

from app.v2.criterion_review import bind_rows, bound_quote, summarize


def test_unicode_binding_rejects_duplicate_or_missing_quote():
    fields = {"f": "前缀中文😀唯一片段"}
    value = bound_quote({"field": "f", "quote": "中文😀"}, fields)
    assert fields["f"].encode()[value["start_byte"]:value["end_byte"]].decode() == "中文😀"
    assert bound_quote({"field": "f", "quote": "不存在"}, fields) is None
    assert bound_quote({"field": "f", "quote": "重复"}, {"f": "重复重复"}) is None


def request():
    return {"request_id": "r", "check_id": "Q4.exercise", "item_id": "i",
            "criterion": {"criterion_hash": "h", "clauses": {"core_method": "核心方法", "transfer": "迁移"}}}


def response(rating=2):
    return {"assessments": [{"request_id": "r", "rating": rating, "clauses": [{"clause_id": k, "result": "satisfied",
        "reason": "逐项分析实际内容并明确适用理由", "evidence": [{"field": "item:i:stem", "quote": "唯一证据"}]} for k in ("core_method", "transfer")]}]}


def test_positive_model_observation_is_not_professional_certification():
    rows = bind_rows(response(), [request()], {"item:i:stem": "唯一证据"}, {"call_id": "c"}, "u")
    report = summarize(rows, 1, [])
    assert report["result"] == "satisfied" and report["Q4"]["observed_median"] == 2
    assert report["calibration_status"] == "not_calibrated" and report["professional_certification"] is False


def test_reason_count_never_substitutes_for_Q4_rubric_score():
    output = response(1)
    rows = bind_rows(output, [request()], {"item:i:stem": "唯一证据"}, {}, "u")
    report = summarize(rows, 1, [])
    assert report["Q4"]["observed_median"] == 1 and report["result"] == "violated"


def test_missing_clause_duplicate_row_or_cross_item_anchor_stays_unknown():
    for fault in ("clause", "duplicate", "cross_item", "score"):
        output = response()
        if fault == "clause": output["assessments"][0]["clauses"].pop()
        if fault == "duplicate": output["assessments"] *= 2
        if fault == "cross_item":
            for c in output["assessments"][0]["clauses"]: c["evidence"][0]["field"] = "item:other:stem"
        if fault == "score": output["assessments"][0].pop("rating")
        rows = bind_rows(output, [request()], {"item:i:stem": "唯一证据", "item:other:stem": "唯一证据"}, {}, "u")
        assert summarize(rows, 1, [])["result"] == "undecidable", fault
        assert summarize(rows, 1, [])["status"] == "incomplete", fault
        assert summarize(rows, 1, [])["coverage"]["complete_observations"] == 0, fault


def test_executed_abstention_is_not_confused_with_missing_execution_or_invalid_result():
    output = response()
    output["assessments"][0]["clauses"][0]["result"] = "undecidable"
    rows = bind_rows(output, [request()], {"item:i:stem": "唯一证据"}, {}, "u")
    assert summarize(rows, 1, [])["status"] == "completed"
    assert summarize(rows, 1, [])["result"] == "undecidable"
    output["assessments"][0]["clauses"][0]["result"] = "pass"
    rows = bind_rows(output, [request()], {"item:i:stem": "唯一证据"}, {}, "u")
    assert summarize(rows, 1, [])["status"] == "incomplete"


def test_malformed_model_arrays_are_unknown_not_parser_crashes():
    for output in ({"assessments": None}, {"assessments": [{"request_id": "r", "clauses": None}]},
                   {"assessments": [{"request_id": "r", "clauses": [{"clause_id": "core_method", "evidence": None}]}]}):
        rows = bind_rows(output, [request()], {"item:i:stem": "唯一证据"}, {}, "u")
        assert summarize(rows, 1, [])["result"] == "undecidable"
    assert bound_quote({"field": ["bad"], "quote": "唯一证据"}, {}) is None


@pytest.mark.parametrize("negative", [False, True])
def test_actual_request_executes_all_clauses_and_excludes_generator_context(monkeypatch, negative):
    from app.v2.criterion_review import evaluate_criteria
    item = {"item_id": "i", "ordinal": 1, "stem_markdown": "唯一题面", "answer": {"final": "唯一答案", "reasoning": "唯一推导"},
            "difficulty_basis": {"secret": "generator_marker"}}
    peer = deepcopy(item) | {"item_id": "j", "ordinal": 2, "stem_markdown": "另一道迁移题面"}
    unit = {"unit_id": "u", "title": "方法", "body": "方法与题答", "items": [item, peer],
            "teaching_arrangement": {"generation_prompt": "secret_generation_chain", "acceptance_criteria": ["test"]}}
    check = {"item_id": "Q4.exercise", "decision_type": "semantic", "verifier_binding": "fixture"}
    monkeypatch.setattr("app.v2.checker.applies", lambda *a: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": "原始任务", "sources": [], "review_scope": {"current_unit_id": "u"}})
    calls = []
    def generate(*args, **kw):
        data = kw["data"]
        calls.append(data)
        from app.v2.criterion_review import C_REVIEW_SKILL
        assert kw["entries"] == [{"source_id": "assessment-review",
            "version": C_REVIEW_SKILL["version"],
            "sha256": C_REVIEW_SKILL["hash"], "consumer": "C", "message_position": "system.skill"}]
        assert f"# assessment-review.v{C_REVIEW_SKILL['version']}" in kw["system"]
        assert "inspect each request's exact frozen A row separately" in kw["system"]
        assert "generator_marker" not in str(data) and "secret_generation_chain" not in str(data)
        assert data["requests"][0]["criterion"]["rating_scale"]["2"]
        assessments = []
        for req in data["requests"]:
            own = "item:" + req["item_id"] + ":stem_markdown"
            other = "item:" + ("j" if req["item_id"] == "i" else "i") + ":stem_markdown"
            clauses = []
            for clause_id in req["criterion"]["clauses"]:
                evidence = [{"field": own, "quote": data["fields"][own]}]
                if clause_id == "transfer":
                    evidence.append({"field": other, "quote": data["fields"][other]})
                clauses.append({"clause_id": clause_id, "result": "violated" if negative else "satisfied",
                    "reason": "基于题面与实际推理的具体分析结果", "evidence": evidence})
            assessments.append({"request_id": req["request_id"], "rating": 0 if negative else 2, "clauses": clauses})
        return {"assessments": assessments}, {"call_id": "synthetic"}, {}
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    report = evaluate_criteria("r", "w", 0, {"context": {}, "check_plan": {"check_items": [check]}}, [unit], "v")
    assert len(calls) == 2 and report["coverage"]["decided"] == report["coverage"]["expected"] == 2
    assert report["result"] == ("violated" if negative else "satisfied")


@pytest.mark.parametrize("with_ordinary_check", [False, True])
def test_research_dimension_is_never_reexecuted_as_a_generic_fallback(monkeypatch, with_ordinary_check):
    from app.v2.criterion_review import evaluate_criteria
    unit = {"unit_id": "u", "title": "A task-derived unit", "body": "Exact unit evidence.", "items": []}
    research = {"item_id": "R.task-specific", "decision_type": "semantic_rubric",
        "verifier_binding": "research_rubric.v1", "pass_condition": "Full source-bound rubric, not this generic text"}
    ordinary = {"item_id": "C3.correctness", "decision_type": "semantic_rubric",
        "verifier_binding": "ordinary.v1", "pass_condition": "Actual ordinary check"}
    checks = [research, ordinary] if with_ordinary_check else [research]
    monkeypatch.setattr("app.v2.checker.applies", lambda *a: True)
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": {}, "sources": []})
    seen = []
    def generate(*args, **kw):
        requests = kw["data"]["requests"]
        seen.extend(requests)
        assert [r["check_id"] for r in requests] == ["C3.correctness"]
        assert "R.task-specific" not in str(kw["data"])
        return {"assessments": [{"request_id": r["request_id"], "rating": None,
            "clauses": [{"clause_id": cid, "result": "undecidable", "reason": "A grounded unresolved observation",
                "evidence": [{"field": "unit:body", "quote": "Exact unit evidence."}]}
                for cid in r["criterion"]["clauses"]]} for r in requests]}, {"call_id": "fixture"}, {}
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    result = evaluate_criteria("r", "w", 1, {"context": {}, "check_plan": {"check_items": checks}}, [unit], "v")
    assert len(seen) == int(with_ordinary_check)
    assert result["coverage"]["expected"] == int(with_ordinary_check)
    assert all(r["check_id"] != "R.task-specific" for r in result["criteria"])

"""Deterministic domain applicability and not-applicable consumer contracts."""
from copy import deepcopy

from app.v2.assessment_binding import (
    consume,
    criterion_check_results,
    input_binding,
)
from app.v2.contracts import sha
from app.v2.difficulty_review import evaluate_difficulty
from app.v2.quality import compile_plan
from app.v2.review_contract import applicability_decision


def math_unit(*, items=True):
    body = "math fixture"
    return {
        "unit_id": "math-unit",
        "title": "determinant",
        "artifact_role": "exercise",
        "body": body,
        "content_hash": sha(body),
        "subject_bindings": [{"domain_key": "math_data"}],
        "items": [
            {
                "item_id": "i1",
                "ordinal": 1,
                "stem_markdown": "compute",
                "answer": {"final": "0", "reasoning": "expand"},
            }
        ] if items else [],
    }


def tutorial_task(hard_constraints=None):
    return {
        "raw_input": "生成一份跨学科教程",
        "domain_key": "history_culture",
        "branch": "",
        "runtime_target": "",
        "material_ids": [],
        "subject_bindings": [{"domain_key": "history_culture"}],
        "requested_artifacts": ["tutorial"],
        "spec_bindings": [
            {
                "scene": "knowledge_tutorial",
                "artifact_role": "tutorial",
                "snapshot": {},
            }
        ],
        "hard_constraints": list(hard_constraints or []),
        "task_intent": {"conflicts": []},
    }


def tutorial_unit():
    body = "正文以可核验的史料说明制度变化。"
    return {
        "unit_id": "tutorial-unit",
        "title": "制度变化",
        "artifact_role": "tutorial",
        "body": body,
        "content_hash": sha(body),
        "subject_bindings": [{"domain_key": "history_culture"}],
        "items": [],
    }


def test_legacy_math_domain_key_still_executes_math_specific_checks():
    task = {
        "domain_key": "math_data",
        "subject_bindings": [{"domain_key": "math_data"}],
        "requested_artifacts": ["exercise"],
        "spec_bindings": [
            {"scene": "exercise", "artifact_role": "exercise", "snapshot": {}}
        ],
    }
    plan = compile_plan(task)
    by_id = {item["item_id"]: item for item in plan["check_items"]}
    assert {f"Q{number}.exercise" for number in range(1, 7)} <= set(by_id)
    decision = applicability_decision(by_id["Q1.exercise"], math_unit(), task)
    assert decision["decision"] == "execute"
    assert decision["matched_domains"] == ["math_logic"]
    assert decision["scope"]["domains"] == ["math_logic"]
    assert decision["canonical_domain_keys"] == ["math_logic"]
    assert decision["applicability_reason"] == "applicable"


def test_c4_applicability_is_frozen_from_actual_hard_constraints():
    empty_task = tutorial_task()
    empty_plan = compile_plan(empty_task)
    c4 = next(
        item for item in empty_plan["check_items"]
        if item["item_id"] == "C4.constraints"
    )
    assert c4["scope"]["requires_nonempty_hard_constraints"] is True
    skipped = applicability_decision(c4, tutorial_unit(), empty_task)
    assert skipped["decision"] == "skip"
    assert skipped["reason"] == "no_frozen_hard_constraints"

    constrained_task = tutorial_task(["必须引用原始材料"])
    executed = applicability_decision(c4, tutorial_unit(), constrained_task)
    assert executed["decision"] == "execute"
    assert executed["reason"] == "applicable"

    # Historical frozen plans had no such scope. Re-evaluating them must not
    # retrofit the new gate after the plan hash was already accepted.
    legacy = deepcopy(c4)
    legacy["scope"].pop("requires_nonempty_hard_constraints")
    assert applicability_decision(legacy, tutorial_unit(), empty_task)["decision"] == "execute"


def test_empty_hard_constraints_skip_c4_before_any_model_request(monkeypatch):
    from app.v2.criterion_review import evaluate_criteria, requests_for_unit

    task = tutorial_task()
    plan = compile_plan(task)
    c4 = next(
        item for item in plan["check_items"]
        if item["item_id"] == "C4.constraints"
    )
    plan["check_items"] = [c4]
    unit = tutorial_unit()
    state = {
        "context": task,
        "plan": {"units": [unit]},
        "check_plan": plan,
        "execution_policy": {"criterion_assessment": True},
    }

    assert not any(
        request["check_id"] == "C4.constraints"
        for request in requests_for_unit(state, unit)[0]
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("C4 must not be dispatched without hard constraints")

    monkeypatch.setattr(
        "app.v2.generation_tools.generation_with_sources", forbidden
    )
    report = evaluate_criteria("run", "worker", 0, state, [unit], "version")
    assert report["status"] == "completed"
    assert report["result"] == "not_applicable"
    assert report["coverage"]["expected"] == 0
    row = next(
        item for item in report["quality_preflight"]["rows"]
        if item["check_id"] == "C4.constraints"
    )
    assert row["decision"] == "skip"
    assert row["reason"] == "no_frozen_hard_constraints"


def test_nonempty_hard_constraints_execute_c4_with_isolated_review(monkeypatch):
    from app.v2.criterion_review import evaluate_criteria

    task = tutorial_task(["必须引用原始材料"])
    plan = compile_plan(task)
    c4 = next(
        item for item in plan["check_items"]
        if item["item_id"] == "C4.constraints"
    )
    plan["check_items"] = [c4]
    unit = tutorial_unit()
    state = {
        "context": task,
        "plan": {"units": [unit]},
        "check_plan": plan,
        "execution_policy": {"criterion_assessment": True},
    }
    calls = []

    def generate(*args, **kwargs):
        calls.append(kwargs)
        request = kwargs["data"]["requests"][0]
        assert request["check_id"] == "C4.constraints"
        assert kwargs["data"]["task"]["hard_constraints"] == ["必须引用原始材料"]
        return {
            "assessments": [{
                "request_id": request["request_id"],
                "rating": None,
                "clauses": [{
                    "clause_id": "requirement",
                    "result": "satisfied",
                    "reason": "正文说明了来源与制度变化的对应关系。",
                    "evidence": [{
                        "field": "unit:body",
                        "quote": unit["body"],
                    }],
                }],
            }]
        }, {"call_id": "isolated-review"}, {}

    monkeypatch.setattr(
        "app.v2.generation_tools.generation_with_sources", generate
    )
    report = evaluate_criteria("run", "worker", 0, state, [unit], "version")
    assert len(calls) == 1
    assert calls[0]["stage"] == "criterion_assessing"
    assert calls[0]["role"] == "review"
    assert report["status"] == "completed"
    assert report["result"] == "satisfied"
    assert report["criteria"][0]["authority"] == (
        "isolated_ai_observation_not_professional_certification"
    )


def test_unit_domain_scope_wins_over_other_cross_disciplinary_context_domains():
    task = {
        "domain_key": "math_data",
        "subject_bindings": [
            {"domain_key": "math_logic"},
            {"domain_key": "history_culture"},
        ],
        "requested_artifacts": ["exercise"],
        "spec_bindings": [
            {"scene": "exercise", "artifact_role": "exercise", "snapshot": {}}
        ],
    }
    math = math_unit()
    history = math_unit()
    history["unit_id"] = "history-unit"
    history["subject_bindings"] = [{"domain_key": "history_culture"}]
    plan = compile_plan(task)
    q1 = next(item for item in plan["check_items"] if item["item_id"] == "Q1.exercise")
    math_decision = applicability_decision(q1, math, task)
    history_decision = applicability_decision(q1, history, task)
    assert math_decision["decision"] == "execute"
    assert history_decision["decision"] == "skip"
    assert history_decision["reason"] == "domain_not_in_scope"


def test_non_math_item_task_records_complete_not_applicable_difficulty(monkeypatch):
    unit = math_unit()
    unit["subject_bindings"] = [{"domain_key": "biology_life"}]
    state = {"context": {"domain_key": "biology_life"}}

    def forbidden(*args, **kwargs):
        raise AssertionError("difficulty model must not run outside its domain")

    monkeypatch.setattr("app.v2.inference.invoke", forbidden)
    report = evaluate_difficulty("run", "worker", 0, state, [unit], "version")
    assert report["status"] == "completed"
    assert report["result"] == "not_applicable"
    assert report["reason"] == "domain_not_in_scope"
    assert report["processing_status"] == "complete"
    assert report["items"] == []
    assert report["coverage"] == {
        "expected": 0,
        "recorded": 0,
        "complete_observations": 0,
        "decided": 0,
        "invalid_response_entries": 0,
    }
    assert report["applicability"]["matched_domains"] == []
    assert consume(report, "difficulty_review", state, [unit], "version") == (
        "not_applicable",
        [],
    )


def test_math_task_without_items_skips_the_item_difficulty_axis():
    unit = math_unit(items=False)
    state = {"context": {"domain_key": "math_logic"}}
    report = evaluate_difficulty("run", "worker", 0, state, [unit], "version")
    assert report["result"] == "not_applicable"
    assert report["reason"] == "item_content_not_present"
    assert report["applicability"]["matched_domains"] == ["math_logic"]


def test_mixed_domain_difficulty_only_reviews_math_items(monkeypatch):
    from test_difficulty_review import fixture

    row, _, math = fixture()
    math["unit_id"] = "math-unit"
    history = {
        **math,
        "unit_id": "history-unit",
        "subject_bindings": [{"domain_key": "history_culture"}],
    }
    seen = []

    def invoke(*args, **kwargs):
        seen.append(kwargs["unit_id"])
        return {"items": [dict(row, item_id=kwargs["data"]["items"][0]["item_id"])]}, {}

    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    report = evaluate_difficulty(
        "run", "worker", 0, {"context": {"domain_key": "math_logic"}},
        [math, history], "version",
    )
    assert seen == ["math-unit"]
    assert report["coverage"]["expected"] == 1
    assert [item["unit_id"] for item in report["items"]] == ["math-unit"]


def test_forged_not_applicable_report_is_rejected_for_an_applicable_math_task():
    unit = math_unit()
    nonmath_unit = math_unit()
    nonmath_unit["subject_bindings"] = [{"domain_key": "biology_life"}]
    nonmath = {"context": {"domain_key": "biology_life"}}
    report = evaluate_difficulty("run", "worker", 0, nonmath, [nonmath_unit], "version")
    math_context = {"context": {"domain_key": "math_logic"}}
    outcome, errors = consume(report, "difficulty_review", math_context, [unit], "version")
    assert outcome == "undecidable"
    assert errors


def test_criterion_consumer_marks_check_not_applicable_when_no_unit_matches_scope():
    from app.v2.criterion_review import summarize

    unit = math_unit()
    unit["subject_bindings"] = [{"domain_key": "history_culture"}]
    check = {
        "item_id": "I.math_logic.reasoning.exercise",
        "verifier_binding": "criterion_review.v1",
        "decision_type": "semantic_rubric",
        "scope": {
            "domains": ["math_logic"],
            "scenes": ["exercise"],
            "requires_items": True,
        },
        "required": True,
        "severity_class": "blocker",
    }
    state = {
        "context": {"domain_key": "history_culture"},
        "check_plan": {"check_items": [check]},
    }
    report = summarize([], 0, []) | {
        "version_id": "version",
        "input_binding": input_binding(state, [unit], "version"),
    }
    assert report["result"] == "not_applicable"
    assert consume(report, "automated_assessment", state, [unit], "version") == (
        "not_applicable",
        [],
    )
    result = criterion_check_results(report, state, [unit], "version")
    assert result[check["item_id"]]["result"] == "not_applicable"
    assert result[check["item_id"]]["matched_unit_ids"] == []


def test_research_rubric_scope_is_frozen_or_explicitly_rejected():
    base = {
        "requested_artifacts": ["tutorial"],
        "spec_bindings": [
            {
                "scene": "knowledge_tutorial",
                "artifact_role": "tutorial",
                "snapshot": {},
            }
        ],
        "research_rubrics": [
            {
                "id": "legacy-rubric",
                "version": 1,
                "hash": "fixture",
                "content": {
                    "rubric_id": "legacy-rubric",
                    "dimensions": [{"dimension_id": "D1"}],
                },
            }
        ],
    }
    unique = compile_plan(
        {
            **base,
            "domain_key": "history_culture",
            "subject_bindings": [{"domain_key": "history_culture"}],
        }
    )
    row = next(item for item in unique["check_items"] if item["item_id"] == "R.D1")
    assert row["scope"]["domains"] == ["history_culture"]
    assert row["research_rubric_ref"]["legacy_scope_derived"] is True
    assert any(
        item["verifier_binding"] == "research_scope_completion.v1"
        for item in unique["check_items"]
    )

    ambiguous = compile_plan(
        {
            **base,
            "domain_key": "interdisciplinary",
            "subject_bindings": [
                {"domain_key": "history_culture"},
                {"domain_key": "math_logic"},
            ],
        }
    )
    assert any(
        conflict["code"] == "RESEARCH_RUBRIC_SCOPE_AMBIGUOUS"
        for conflict in ambiguous["conflicts"]
    )
    assert not any(
        item["item_id"] == "R.D1" for item in ambiguous["check_items"]
    )


def test_scope_completion_consumes_complete_not_applicable_and_malformed_states():
    from app.v2.quality import deterministic_checks

    unit = {
        "unit_id": "unit",
        "body": "candidate",
        "content_hash": sha("candidate"),
    }
    base = {
        "domain_key": "history_culture",
        "subject_bindings": [{"domain_key": "history_culture"}],
        "requested_artifacts": ["tutorial"],
        "spec_bindings": [],
    }

    def report(status, gaps):
        rubric = {
            "rubric_id": "scope-fixture",
            "version": 1,
            "hash": "fixture",
            "dimensions": [],
            "scope_completion_status": status,
            "scope_gaps": gaps,
        }
        plan = compile_plan({**base, "research_rubrics": [{
            "id": "scope-fixture",
            "version": 1,
            "hash": "fixture",
            "content": rubric,
        }]})
        item = next(
            row for row in plan["check_items"]
            if row["verifier_binding"] == "research_scope_completion.v1"
        )
        return next(
            row for row in deterministic_checks(plan, [unit], "version")["checks"]
            if row["item_id"] == item["item_id"]
        )

    assert report("complete", [])["result"] == "satisfied"
    assert report("partial_unresolved", [{
        "dimension_id": "D6",
        "decision": {"result": "not_applicable"},
    }])["result"] == "not_applicable"
    assert report("broken_contract", [])["result"] == "violated"

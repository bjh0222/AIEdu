"""Deterministic domain applicability and not-applicable consumer contracts."""
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
    report = {
        "version_id": "version",
        "input_binding": input_binding(state, [unit], "version"),
        "status": "completed",
        "result": "satisfied",
        "criteria": [],
        "coverage": {
            "expected": 0,
            "recorded": 0,
            "complete_observations": 0,
            "decided": 0,
        },
    }
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

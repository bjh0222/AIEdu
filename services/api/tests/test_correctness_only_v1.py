from copy import deepcopy
from types import SimpleNamespace

from app.v2.contracts import DomainError, sha
from app.v2.quality import compile_plan, normalize_quality_layers
from app.v2.quality_suite import CORRECTNESS_ONLY_V1, normalize_quality_profile


TASK = {
    "title": "item correctness",
    "raw_input": "generate exercises",
    "task_intent": {},
    "spec_bindings": [{"artifact_role": "exercise", "scene": "exercise"}],
    "subject_bindings": [{"domain_key": "math_logic"}],
    "requested_artifacts": ["exercise"],
}


def fixture_state(items):
    body = "\n\n".join(
        f"{item['stem_markdown']}\n\n{item['answer']['final']}\n\n{item['answer']['reasoning']}"
        for item in items
    )
    unit = {
        "unit_id": "unit-1",
        "artifact_role": "exercise",
        "body": body,
        "content_hash": sha(body),
        "items": items,
    }
    profile = normalize_quality_profile(CORRECTNESS_ONLY_V1)
    plan = compile_plan(
        deepcopy(TASK),
        quality_layers=normalize_quality_layers({
            "correctness": True,
            "completeness": False,
            "quality_specific": False,
        }),
        quality_profile=profile,
    )
    state = {
        "quality_profile": profile,
        "execution_policy": {
            "criterion_assessment": False,
            "quality_profile": profile,
        },
        "context": {
            "domain_key": "math_logic",
            "raw_input": TASK["raw_input"],
            "hard_constraints": [],
            "spec_bindings": [],
            "material_ids": [],
        },
        "plan": {
            "units": [{"unit_id": "unit-1", "required": True, "material_chunk_ids": []}],
        },
        "check_plan": plan,
    }
    return state, [unit]


def patch_contract(monkeypatch):
    from app.v2.quality_suite import correctness_only_v1

    monkeypatch.setattr(correctness_only_v1, "SYSTEM_PROMPT", "fixture prompt")
    monkeypatch.setattr(correctness_only_v1, "OUTPUT_SCHEMA", {"items": "fixture"})
    monkeypatch.setattr(correctness_only_v1, "for_role", lambda *args, **kwargs: object())
    return correctness_only_v1


def test_plan_contains_only_generated_item_correctness():
    state, _ = fixture_state([])
    assert [row["item_id"] for row in state["check_plan"]["check_items"]] == ["Q.item_correctness"]
    assert state["check_plan"]["derivation_contract"]["enabled"] is False


def test_all_items_reviewed_without_errors_signs_correctness_only_pass(monkeypatch):
    module = patch_contract(monkeypatch)
    items = [{
        "item_id": "i1",
        "stem_markdown": "1+1=?",
        "answer": {"final": "2", "reasoning": "1+1=2"},
    }]
    state, units = fixture_state(items)
    monkeypatch.setattr(module, "invoke", lambda *args, **kwargs: ({
        "items": [{
            "item_id": "i1",
            "result": "correct",
            "field": "",
            "quote": "",
            "claim": "",
            "required_change": "",
        }]
    }, {"call_id": "c1", "request_hash": "h1"}))
    report = module.evaluate("r", "w", 0, state, units, "v1")
    assert report["tqi_verdict"] == "pass"
    assert report["quality_status"] == "passed"
    assert report["calibration_status"] == "not_calibrated"
    assert report["checks"][0]["result"] == "satisfied"
    assert report["item_correctness"]["professional_certification"] is False
    from app.v2.delivery import delivery_decision
    decision = delivery_decision(report, state["check_plan"], profile_binding_current=True)
    assert decision["final_allowed"] is True
    assert decision["hard_blockers"] == []


def test_concrete_item_error_is_bound_for_directive_repair(monkeypatch):
    module = patch_contract(monkeypatch)
    items = [{
        "item_id": "i1",
        "stem_markdown": "2+2=?",
        "answer": {"final": "5", "reasoning": "2+2=5"},
    }]
    state, units = fixture_state(items)
    monkeypatch.setattr(module, "invoke", lambda *args, **kwargs: ({
        "items": [{
            "item_id": "i1",
            "result": "incorrect",
            "field": "answer.final",
            "quote": "5",
            "claim": "final answer is incorrect",
            "required_change": "replace the final answer with 4",
        }]
    }, {"call_id": "c1", "request_hash": "h1"}))
    report = module.evaluate("r", "w", 0, state, units, "v1")
    assert report["tqi_verdict"] == "needs_repair"
    assert report["quality_status"] == "failed"
    assert report["checks"][0]["result"] == "violated"
    finding = report["findings"][0]
    assert finding["category"] == "item_correctness"
    assert finding["suggested_fix"] == "replace the final answer with 4"
    assert finding["acceptance_assertion"]["item_id"] == "i1"
    assert finding["acceptance_assertion"]["method"] == report["checks"][0]["method_version"]
    directive = report["item_correctness"]["repair_directives"][0]
    assert directive["finding_id"] == finding["finding_id"]
    assert directive["target"]["item_ordinal"] == 1
    assert directive["change_mode"] == "whole_item"
    assert "answer.final" not in directive["forbidden_changes"]
    assert "stem_markdown" in directive["forbidden_changes"]
    assert {"item_count", "unit_title", "unrelated_items"} <= set(
        directive["forbidden_changes"])
    from app.v2.optimization import correctness_package
    from app.v2.repair_directives import normalize_directives
    version = SimpleNamespace(id="v1", content_hash=units[0]["content_hash"])
    package = correctness_package(state, version, units, report["findings"])
    _, normalized = normalize_directives(package)
    assert normalized[0] == directive


def test_missing_or_duplicate_item_result_cannot_sign_final(monkeypatch):
    module = patch_contract(monkeypatch)
    items = [{
        "item_id": "i1",
        "stem_markdown": "3+3=?",
        "answer": {"final": "6", "reasoning": "3+3=6"},
    }]
    state, units = fixture_state(items)
    monkeypatch.setattr(module, "invoke", lambda *args, **kwargs: ({
        "items": []
    }, {"call_id": "c1", "request_hash": "h1"}))
    report = module.evaluate("r", "w", 0, state, units, "v1")
    assert report["tqi_verdict"] == "blocked"
    assert report["quality_status"] == "needs_review"
    assert report["checks"][0]["result"] == "undecidable"


def test_structured_answer_error_binds_when_answer_is_not_rendered_in_unit_body(monkeypatch):
    module = patch_contract(monkeypatch)
    items = [{
        "item_id": "i1",
        "stem_markdown": "2+2=?",
        "answer": {"final": "5", "reasoning": "2+2=5"},
    }]
    state, units = fixture_state(items)
    units[0]["body"] = "2+2=?"
    units[0]["content_hash"] = sha(units[0]["body"])
    monkeypatch.setattr(module, "invoke", lambda *args, **kwargs: ({
        "items": [{
            "item_id": "i1",
            "result": "incorrect",
            "field": "answer.reasoning",
            "quote": "2+2=5",
            "claim": "the reasoning has the wrong sum",
            "required_change": "replace the reasoning with 2+2=4",
        }]
    }, {"call_id": "c1", "request_hash": "h1"}))
    report = module.evaluate("r", "w", 0, state, units, "v1")
    finding = report["findings"][0]
    assert report["tqi_verdict"] == "needs_repair"
    assert finding["location"]["location_basis"] == "structured_item_field"
    assert finding["location"]["field"] == "answer.reasoning"
    assert finding["location"]["unit_hash"] == units[0]["content_hash"]
    assert report["item_correctness"]["repair_directives"][0]["finding_id"] == finding["finding_id"]
    from app.v2.optimization import optimization_trigger
    trigger = optimization_trigger(
        state["check_plan"], report, units=units, version_id="v1")
    assert trigger["status"] == "repair"
    assert trigger["findings"][0]["finding_id"] == finding["finding_id"]


def test_non_item_content_is_not_applicable_without_a_model_call(monkeypatch):
    module = patch_contract(monkeypatch)
    state, units = fixture_state([])
    monkeypatch.setattr(module, "invoke", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("non-item content must not call the item checker")
    ))
    report = module.evaluate("r", "w", 0, state, units, "v1")
    assert report["tqi_verdict"] == "pass"
    assert report["quality_status"] == "passed"
    assert report["checks"][0]["result"] == "not_applicable"


def test_profile_without_approved_prompt_fails_closed(monkeypatch):
    module = patch_contract(monkeypatch)
    monkeypatch.setattr(module, "SYSTEM_PROMPT", None)
    state, units = fixture_state([])
    try:
        module.evaluate("r", "w", 0, state, units, "v1")
    except DomainError as exc:
        assert exc.code == "QUALITY_PROFILE_PROMPT_NOT_APPROVED"
    else:
        raise AssertionError("missing prompt must not produce a quality result")

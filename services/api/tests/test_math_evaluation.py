"""Independent math item-quality tests. Synthetic data, no DB writes and no paid calls."""
from copy import deepcopy

import pytest

from app.v2.assessment_assets import quantity_summary
from app.v2.math_framework import (
    ASSERTION_METHODS,
    DIMENSION_IDS,
    bind_framework,
    evaluate_assertion,
    evaluate_item_quality,
    framework_for_domain,
)
from app.v2.profiles import domain_profile
from app.v2.spec_packages import build_package


def math_state():
    return {"context": {"quality_profiles": [domain_profile("math_logic", "exercise")],
        "evaluation_framework": framework_for_domain("math_logic")}}


def item(assertions=None, transfer=None):
    value = {
        "item_id": "item-1",
        "stem_markdown": "synthetic stem",
        "answer": {"final": "synthetic final", "reasoning": "synthetic reasoning"},
    }
    if assertions is not None:
        value["formal_assertions"] = assertions
    if transfer is not None:
        value["transfer_assertions"] = transfer
    from app.v2.contracts import canonical
    value["answer"]["reasoning"] = "\n".join(canonical({k: v for k, v in a.items() if k != "dimension_ids"}).decode()
        for a in (assertions or []) + (transfer or [])) or "synthetic reasoning"
    return value


def polynomial(assertion="polynomial_identity", left="x^2", right="x*x", dimensions=None):
    value = {"assertion": assertion, "left": left, "right": right, "variables": ["x"]}
    if dimensions is not None:
        value["dimension_ids"] = dimensions
    return value


def test_supported_math_assertions_return_exact_results():
    satisfied = evaluate_assertion(polynomial())
    violated = evaluate_assertion(polynomial(right="x^3"))
    assert satisfied["result"] == "satisfied"
    assert satisfied["professional_correctness"] == "not_run"
    assert satisfied["scope"] == "literal structured mathematical assertion only"
    assert violated["result"] == "violated"
    assert violated["tool_raw_output_hash"]

    inverse = evaluate_assertion({
        "assertion": "matrix_inverse", "matrix": [[2, 0], [0, 2]],
        "inverse": [["1/2", 0], [0, "1/2"]],
    })
    assert inverse["result"] == "satisfied"

    system = evaluate_assertion({
        "assertion": "linear_system_solution", "matrix": [[2, 3], [1, -1]],
        "constants": ["5/2", "0"], "solution": ["1/2", "1/2"],
    })
    assert system["result"] == "satisfied"

    equality = evaluate_assertion({
        "assertion": "matrix_equality", "left": [[1, 2], [3, 4]], "right": [[1, 2], [3, 4]],
    })
    assert equality["result"] == "satisfied"


def test_resource_and_type_limits_stay_undecidable():
    unsupported = evaluate_assertion({"assertion": "natural_language_quality", "text": "looks good"})
    oversize = evaluate_assertion(polynomial(left="x + 1" * 500, right="x + 1" * 500))
    assert unsupported["result"] == "undecidable"
    assert unsupported["unsupported_reason"] == "unsupported_assertion"
    assert oversize["result"] == "undecidable"


def test_item_quality_maps_verified_and_failing_evidence_without_full_dimension_certification():
    state = math_state()
    units = [{"unit_id": "unit-1", "artifact_role": "exercise", "items": [
        item([polynomial(dimensions=["premises", "reasoning"])]),
        item([polynomial(right="x^3", dimensions=["reasoning"])]),
        item(transfer=[polynomial(dimensions=["reasoning"])]),
        item(),
    ]}]
    result = evaluate_item_quality(state, units, framework=framework_for_domain("math_logic"))
    assert result["framework_binding"]["status"] == "bound"
    assert result["calibration_status"] == "not_calibrated"
    assert result["semantic_score"] is None
    assert len(result["item_results"]) == 4

    first = result["item_results"][0]["dimensions"]
    assert first["premises"]["result"] == "satisfied"
    assert first["premises"]["coverage"] == "assertion_only_not_whole_dimension"
    assert first["reasoning"]["result"] == "satisfied"
    assert first["transfer"]["result"] == "undecidable"

    second = result["item_results"][1]["dimensions"]
    assert second["reasoning"]["result"] == "violated"

    third = result["item_results"][2]["dimensions"]
    assert third["transfer"]["formal_assertion_result"] == "satisfied"
    assert third["transfer"]["result"] == "undecidable"
    assert third["reasoning"]["result"] == "undecidable"

    fourth = result["item_results"][3]
    assert all(d["result"] == "undecidable" for d in fourth["dimensions"].values())
    assert fourth["professional_correctness"] == "not_run"
    assert fourth["difficulty_status"] == "not_measured"


def test_transfer_field_overrides_to_transfer_dimension():
    state = math_state()
    units = [{"unit_id": "unit-1", "artifact_role": "exercise", "items": [
        item(transfer=[polynomial(dimensions=["reasoning"])])
    ]}]
    result = evaluate_item_quality(state, units)
    evidence = result["item_results"][0]["evidence"][0]
    assert evidence["dimension_ids"] == ["transfer"]


def test_unrelated_or_tampered_assertion_never_certifies_item():
    value = item([polynomial(dimensions=["reasoning", "difficulty"])])
    value["answer"]["reasoning"] = "Unrelated proof, no literal assertion."
    units = [{"unit_id": "u", "items": [value]}]
    result = evaluate_item_quality(math_state(), units)["item_results"][0]
    assert result["evidence"][0]["source_binding"]["status"] == "unbound"
    assert all(d["result"] == "undecidable" for d in result["dimensions"].values())


def test_quantity_summary_includes_math_evidence_but_keeps_score_unknown():
    state = math_state()
    units = [{"unit_id": "unit-1", "artifact_role": "exercise", "items": [
        item([polynomial()]), item(),
    ]}]
    result = quantity_summary(state, units)
    assert result["applicable"] is True
    assert result["method_version"] == "item-structure-and-formal-evidence.v2"
    assert result["semantic_score"] is None
    assert result["calibration_status"] == "not_calibrated"
    assert result["item_evaluation"]["item_results"][0]["dimensions"]["reasoning"]["result"] == "satisfied"
    assert result["item_evaluation"]["item_results"][1]["dimensions"]["reasoning"]["result"] == "undecidable"


def test_profile_and_spec_package_expose_independent_framework_contract():
    profile = domain_profile("math_logic", "exercise")
    framework = profile["item_quality_framework"]
    assert framework["framework_id"] == "independent-math.v2"
    assert framework["scope"]["model_prose_translation"] == "unsupported"
    assert set(framework["assertion_methods"]) <= set(ASSERTION_METHODS)
    package = build_package("math_logic", "线性代数", "exercise")
    assert package["item_quality_framework"]["status"] == "engineering_formal_only"
    assert package["quality_profile"]["item_quality_framework"] == framework


def test_imported_prose_framework_is_not_auto_bound():
    imported = {"id": "db-framework", "domain_key": "math_logic", "status": "active",
                "standards": [{"name": "prose"}]}
    assert bind_framework(imported)["status"] == "not_bound"
    assert bind_framework(None)["status"] == "unbound"
    assert bind_framework(framework_for_domain("math_logic"))["status"] == "bound"


def test_supported_non_math_domain_has_no_math_framework():
    profile = domain_profile("cs_it", "exercise")
    assert "item_quality_framework" not in profile
    assert framework_for_domain("cs_it") is None


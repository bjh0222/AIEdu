import pytest


def _arrangement(units):
    return {"content": {
        "output_design": {
            "mode": "method_practice",
            "grouping_basis": "actual task methods",
            "rationale": "The user asked for method groups.",
            "request_quotes": ["每类给3道较难题"],
            "question_form": "constructed_response_only",
            "explanation_policy": "concise",
            "answer_placement": "document_end",
            "template_adaptations": [],
            "difficulty_target": {
                "level": "较难",
                "minimum_nonroutine_operations": 1,
                "minimum_concept_interactions": 2,
                "minimum_dependency_depth": 2,
                "rationale": "Each problem requires nonroutine reasoning.",
                "empirical_status": "not_measured",
            },
            "quantity_per_group": 3,
        },
        "units": units,
    }}


def _unit(title, *, role="exercise", depends=None):
    return {
        "title": title,
        "concepts": ["concept"],
        "question_types": ["constructed response"],
        "difficulty_basis": {"prerequisites": ["algebra"]},
        "quantity": 3,
        "material_mapping": [],
        "acceptance_criteria": ["observable criterion"],
        "depends_on_titles": depends or [],
        "artifact_role": role,
    }


def _context():
    return {
        "raw_input": "每类给3道较难题",
        "hard_constraints": [],
        "requested_artifacts": ["exercise", "answer"],
        "material_ids": [],
        "spec_bindings": [],
        "subject_bindings": [],
    }


def test_composite_answer_role_is_canonicalized_and_dependencies_are_bound():
    from app.v2.adaptive_format import apply_design

    first = _unit("方法一", role="exercise_and_answer")
    second = _unit("方法二", role="exercise_and_answer", depends=["方法一"])
    plan = apply_design(_context(), {"units": []}, _arrangement([first, second]), [])

    assert [u["artifact_role"] for u in plan["units"]] == ["exercise", "exercise"]
    assert all(u["artifact_roles"] == ["exercise", "answer"] for u in plan["units"])
    assert plan["units"][0]["source_artifact_role"] == "exercise_and_answer"
    assert plan["units"][1]["depends_on"] == [plan["units"][0]["unit_id"]]


def test_unknown_adaptive_role_is_not_guessed():
    from app.v2.adaptive_format import apply_design
    from app.v2.contracts import DomainError

    with pytest.raises(DomainError, match="ADAPTIVE_ARTIFACT_ROLE_UNBOUND"):
        apply_design(_context(), {"units": []}, _arrangement([_unit("方法一", role="made_up_role")]), [])


def test_adaptive_dependency_cycle_is_rejected():
    from app.v2.adaptive_format import apply_design
    from app.v2.contracts import DomainError

    with pytest.raises(DomainError, match="OUTPUT_DESIGN_DEPENDENCY_CYCLE"):
        apply_design(_context(), {"units": []}, _arrangement([
            _unit("方法一", depends=["方法二"]),
            _unit("方法二", depends=["方法一"]),
        ]), [])


def test_constructed_response_description_is_normalized_but_other_forms_are_not():
    from app.v2.adaptive_format import validate_item
    from app.v2.contracts import DomainError

    candidate = {"question_form": "constructed_response (calculation/proof/multi-part)",
                 "difficulty_basis": {"prerequisites": ["algebra"],
                                       "cognitive_operations": ["choose"],
                                       "nonroutine_operations": ["transfer"],
                                       "concept_interactions": ["two concepts"],
                                       "dependency_depth": 2,
                                       "rationale": "requires a nonroutine choice"}}
    value = validate_item(candidate, {"output_design": {
        "question_form": "constructed_response_only",
        "difficulty_target": {"minimum_nonroutine_operations": 1,
                               "minimum_concept_interactions": 1,
                               "minimum_dependency_depth": 1},
    }})
    assert value["question_form"] == "constructed_response"
    with pytest.raises(DomainError, match="TASK_QUESTION_FORM_REQUIRED"):
        validate_item(candidate | {"question_form": "constructed_response_or_choice"}, {})

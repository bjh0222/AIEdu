"""Optimizer wire representation must agree with fail-closed validation."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.v2.contracts import DomainError, digest, sha
from app.v2.format_runtime import EXERCISE_LAYOUT
from app.v2.optimization import candidate_schema_for_units, validate_candidate, CANDIDATE_SCHEMA_VERSION


def test_mixed_unit_schema_is_exact_coupled_and_does_not_mutate():
    from app.v2.optimization import candidate_output_reserve
    from app.v2.items import EXERCISE_SCHEMA, ITEM_SCHEMA
    units = [{"unit_id": "exercise", "items": [{}], "format_contract": EXERCISE_LAYOUT},
             {"unit_id": "paper", "items": [{}]}, {"unit_id": "prose"}]
    old = deepcopy(units)
    schema = candidate_schema_for_units(units)
    assert schema["units"] == [
        {"unit_id": "exercise", "items": EXERCISE_SCHEMA["items"]},
        {"unit_id": "paper", "items": ITEM_SCHEMA["items"]},
        {"unit_id": "prose", "body": "complete replacement Markdown"}]
    schema["units"][0]["items"][0]["answer"]["final"] = "changed"
    assert EXERCISE_SCHEMA["items"][0]["answer"]["final"] == "answer only"
    assert units == old
    assert candidate_output_reserve(units) == 65536
    assert candidate_output_reserve([units[2]]) == 32768
    assert candidate_output_reserve([]) == 32768


def test_whole_exercise_unit_still_requires_items_and_projects_answers():
    from app.v2.items import render_unit_items
    unit = {"unit_id": "u", "title": "synthetic", "artifact_role": "exercise",
            "format_contract": EXERCISE_LAYOUT, "source_sections": [], "items": [{}],
            "body": "old", "content_hash": sha("old"), "depends_on": []}
    package = {"scope": {"unit_ids": ["u"], "allowed_change_modes": ["whole_unit"]}}
    candidate = {"schema_version": CANDIDATE_SCHEMA_VERSION, "base_version": "v",
                 "base_content_hash": "h", "package_hash": digest(package), "strategy": "whole_unit"}
    with pytest.raises(DomainError, match="ITEM_BODY_DRIFT"):
        validate_candidate(candidate | {"units": [{"unit_id": "u", "body": "unstructured"}]},
                           package, SimpleNamespace(id="v", content_hash="h"), [unit])
    items = [{"question_type": kind, "stem_markdown": "synthetic stem", "source_sections": [],
              "answer": {"final": "synthetic answer", "reasoning": "internal", "display_reasoning": ""}}
             for kind in ("判断题", "基础概念题")]
    result = validate_candidate(candidate | {"units": [{"unit_id": "u", "items": items}]},
                                package, SimpleNamespace(id="v", content_hash="h"), [unit])[0]
    assert len(result["items"]) == 2
    assert result["body"] == render_unit_items(result["items"], result)
    assert result["body"].count("### 参考答案") == 1
    assert result["body"].count("synthetic answer") == 2


def test_real_optimizer_wiring_supplies_typed_schema_to_C_and_B(client, monkeypatch):
    from test_task_run_v2 import task_run
    from test_optimization_prompt_loop import _run_loop
    from app.v2 import generation_tools
    _, _, snap = task_run(client)
    original = monkeypatch.setattr
    observed = []

    def instrument(target, name, value):
        if target is generation_tools and name == "generation_with_sources":
            wrapped = value

            def checked(*args, **kwargs):
                assert kwargs["output_schema"] == kwargs["data"]["schema"]
                assert "NEVER body" in kwargs["system"]
                assert kwargs["output_tokens"] == (65536 if any(u.get("items") for u in kwargs["data"]["base_units"]) else 32768)
                observed.append(kwargs["output_schema"])
                return wrapped(*args, **kwargs)
            value = checked
        original(target, name, value)

    monkeypatch.setattr = instrument
    seen = _run_loop(client, monkeypatch, snap)
    assert observed == [candidate_schema_for_units(snap["units"])]
    assert seen["data"][0]["candidate_schema"] == observed[0]


def test_source_bound_projection_contract_is_passed_to_acceptance(monkeypatch):
    from app.v2.items import normalize_items, render_unit_items
    from app.v2.optimization import presentation_contracts_for_units, OPTIMIZATION_SKILL
    from app.v2.optimization_acceptance import verify
    from app.v2 import inference
    unit = {"unit_id": "u", "title": "frozen", "artifact_role": "exercise",
            "format_contract": EXERCISE_LAYOUT, "source_sections": []}
    items = [{"question_type":kind,"stem_markdown":"one" if kind=="判断题" else "two",
              "answer":{"final":"a","reasoning":"r","display_reasoning":""}}
             for kind in ("判断题","基础概念题")]
    unit["items"] = normalize_items({"items":items}, "exercise", "u", EXERCISE_LAYOUT, [])
    unit["body"] = render_unit_items(unit["items"], unit)
    unit["content_hash"] = sha(unit["body"])
    context = {"spec_bindings":[{"artifact_role":"exercise","snapshot_hash":"frozen"}]}
    contracts = presentation_contracts_for_units([unit], context)
    assert contracts[0]["presentation_contract"]["source_hash"] == unit["content_hash"]
    assert presentation_contracts_for_units([unit | {"body":"drift"}], context) == []
    def invoke(*args, **kwargs):
        assert kwargs["data"]["presentation_contracts"] == contracts
        assert "system H2 chapter headings" in kwargs["system"]
        return {"criteria":[{"item_id":"layout","result":"satisfied",
                "evidence":[{"unit_id":"u","quote":"**2.** two"}]}]}, {"call_id":"c"}
    monkeypatch.setattr(inference,"invoke",invoke)
    report = verify("r","w",1,{"context":context},
        {"frozen_bindings":{},"acceptance_criteria":[{"item_id":"layout"}]},[unit])
    assert report["status"] == "satisfied"
    assert OPTIMIZATION_SKILL["version"] == "1.6.0"
    assert "不得把题型名称当作难度证据" in OPTIMIZATION_SKILL["text"]


def test_original_task_requirement_cannot_be_omitted_or_weakened(monkeypatch):
    from app.v2.optimization_acceptance import verify
    from app.v2.optimization import original_task_acceptance
    from app.v2 import inference
    state={"context":{"raw_input":"Give three difficult examples per category", "hard_constraints":"preserve all categories"}}
    observed=[]
    def invoke(*args,**kwargs):
        observed.extend(kwargs["data"]["criteria"])
        return {"criteria":[{"item_id":c["item_id"],"result":"violated" if c["item_id"]=="task.original_requirements" else "satisfied",
            "evidence":[{"unit_id":"u","quote":"only easy examples"}]} for c in kwargs["data"]["criteria"]]}, {"call_id":"C"}
    monkeypatch.setattr(inference,"invoke",invoke)
    package={"frozen_bindings":{},"acceptance_criteria":[{"item_id":"format"},
        {"item_id":"task.original_requirements","pass_condition":"ignore difficulty"}]}
    unit={"unit_id":"u","body":"only easy examples","content_hash":sha("only easy examples")}
    report=verify("r","w",1,state,package,[unit])
    assert report["status"] == "unresolved"
    assert next(c for c in observed if c["item_id"]=="task.original_requirements") == original_task_acceptance(state["context"])[0]
    assert len(observed)==2

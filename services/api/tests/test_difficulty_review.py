"""Blind difficulty execution and adversarial observation binding (synthetic)."""
from copy import deepcopy

import pytest

from app.v2.difficulty_review import bind_item, evaluate_difficulty


def fixture():
    item = {"item_id": "i1", "ordinal": 1, "stem_markdown": "综合条件甲，选择方法乙，并推广结论丙。",
            "answer": {"final": "精确结论丁", "reasoning": "先分析条件甲；再比较方法乙；最后推广结论丙。"},
            "difficulty_basis": {"private_generator_marker": "must_not_leak"}}
    unit = {"unit_id": "u1", "title": "合成方法", "items": [item],
            "subject_bindings": [{"domain_key": "math_logic"}],
            "output_design": {"difficulty_target": {
        "minimum_nonroutine_operations": 1, "minimum_concept_interactions": 1, "minimum_dependency_depth": 3}}}
    def entry(identity, quote, operation="justify", dependencies=None):
        value = {"id": identity, "operation": operation, "reason": "针对实际内容的独立且可定位的推理依据",
                 "evidence": [{"field": "answer.reasoning", "quote": quote}]}
        if dependencies is not None: value["depends_on"] = dependencies
        return value
    row = {"prerequisites": [entry("p1", "条件甲")],
           "cognitive_operations": [entry("c1", "方法乙", "select_method")],
           "nonroutine_operations": [entry("n1", "推广结论丙", "generalize")],
           "concept_interactions": [entry("x1", "比较方法乙")],
           "steps": [entry("s1", "分析条件甲", dependencies=[]), entry("s2", "比较方法乙", dependencies=["s1"]),
                     entry("s3", "推广结论丙", "generalize", ["s2"])],
           "computation_load": {"level": "light", "evidence": [{"field": "answer.final", "quote": "精确结论丁"}]}}
    return row, item, unit


def test_bound_positive_counts_DAG_and_UTF8_spans_do_not_certify_difficulty():
    row, item, unit = fixture()
    result = bind_item(row, item, unit, {"call_id": "synthetic"})
    assert result["result"] == "satisfied" and result["counts"]["dependency_depth"] == 3
    assert result["empirical_status"] == "not_measured"
    for dimension in result["dimensions"].values():
        for entry in dimension:
            for span in entry["evidence"]:
                source = item["answer"][span["field"].split(".")[1]].encode()
                assert source[span["start_byte"]:span["end_byte"]].decode() == span["quote"]


@pytest.mark.parametrize("negative", ["routine", "calculation_only", "one_step", "no_integration"])
def test_complete_easy_or_long_calculation_is_negative_not_unknown(negative):
    row, item, unit = fixture()
    if negative in {"routine", "calculation_only"}: row["nonroutine_operations"] = []
    if negative == "calculation_only": row["computation_load"]["level"] = "heavy"
    if negative == "one_step": row["steps"] = row["steps"][:1]
    if negative == "no_integration": row["concept_interactions"] = []
    result = bind_item(row, item, unit, {})
    assert result["input_binding_complete"] and result["result"] == "violated"


@pytest.mark.parametrize("fault", ["missing", "bad_quote", "repeated_anchor", "duplicate_id", "cycle", "dangling", "bad_operation", "malformed"])
def test_incomplete_or_inflated_observations_never_pass(fault):
    row, item, unit = fixture()
    if fault == "missing": del row["prerequisites"]
    if fault == "bad_quote": row["steps"][0]["evidence"][0]["quote"] = "并不存在"
    if fault == "repeated_anchor": row["steps"][1]["evidence"] = deepcopy(row["steps"][0]["evidence"])
    if fault == "duplicate_id": row["steps"][1]["id"] = "s1"
    if fault == "cycle": row["steps"][0]["depends_on"] = ["s3"]
    if fault == "dangling": row["steps"][1]["depends_on"] = ["not_a_step"]
    if fault == "bad_operation": row["nonroutine_operations"][0]["operation"] = "execute_procedure"
    if fault == "malformed": row["computation_load"]["evidence"] = None
    assert bind_item(row, item, unit, {})["result"] == "undecidable"


def test_actual_blind_request_hides_B_proposal_and_A_threshold_and_rejects_duplicate_rows(monkeypatch):
    row, item, unit = fixture()
    observed = []
    def invoke(*args, **kwargs):
        from app.v2.skills import entry as skill_entry, review_skill
        data = kwargs["data"]
        skill = review_skill()
        assert kwargs["entries"].count(skill_entry(skill, "C")) == 1
        assert f"# {skill['skill_id']}.v{skill['version']}" in kwargs["system"]
        observed.append(data)
        assert "difficulty_target" not in str(data) and "difficulty_basis" not in str(data)
        assert "must_not_leak" not in str(data) and kwargs["role"] == "review"
        return {"items": [dict(row, item_id=item["item_id"])] * 2}, {"call_id": "synthetic"}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    result = evaluate_difficulty("r", "w", 0, {"execution_policy": {"check_concurrency": 2}}, [unit], "v")
    assert observed and result["result"] == "undecidable"
    assert not result["professional_certification"] and result["version_id"] == "v"


def test_each_question_must_pass_no_averaging_away_easy_item(monkeypatch):
    row, item, unit = fixture()
    second = deepcopy(item) | {"item_id": "i2", "ordinal": 2}
    unit["items"].append(second)
    def invoke(*args, **kwargs):
        low = deepcopy(row)
        low["nonroutine_operations"] = []
        return {"items": [dict(row, item_id="i1"), dict(low, item_id="i2")]}, {}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    report = evaluate_difficulty("r", "w", 0, {}, [unit], "v")
    assert [r["result"] for r in report["items"]] == ["satisfied", "violated"]
    assert report["result"] == "violated"


def test_new_batches_preserve_every_item_and_use_more_output_room(monkeypatch):
    row, item, unit = fixture()
    unit["items"] = [deepcopy(item) | {"item_id": f"i{i}", "ordinal": i} for i in range(1, 4)]
    seen = []

    def invoke(*args, **kwargs):
        ids = [i["item_id"] for i in kwargs["data"]["items"]]
        seen.append(ids)
        assert len(ids) == 1 and kwargs["output_tokens"] == 32768
        assert set(kwargs["output_schema"]["items"][0]) >= {"steps", "nonroutine_operations", "computation_load"}
        return {"items": [dict(row, item_id=ids[0])]}, {"call_id": "synthetic-" + ids[0]}

    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    report = evaluate_difficulty("r", "w", 0, {}, [unit], "v")
    assert seen == [["i1"], ["i2"], ["i3"]]
    assert len(report["items"]) == len(report["audits"]) == 3
    assert all(r["input_binding_complete"] for r in report["items"])


def test_failure_stops_later_batches_and_later_unit_waves(monkeypatch):
    from app.v2.contracts import DomainError
    from threading import Barrier

    _, _, unit = fixture()
    units = [deepcopy(unit) | {"unit_id": f"u{i}"} for i in range(4)]
    rendezvous = Barrier(2)
    seen = []

    def invoke(*args, **kwargs):
        seen.append(kwargs["unit_id"])
        rendezvous.wait(timeout=5)
        if kwargs["unit_id"] == "u0":
            raise DomainError("UNKNOWN_CALL")
        return {}, {}

    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        evaluate_difficulty("r", "w", 0, {"execution_policy": {"check_concurrency": 2}}, units, "v")
    assert sorted(seen) == ["u0", "u1"]


def test_batch_planner_reuses_success_and_stops_failed_minimum_requests():
    from app.v2.contracts import DomainError
    from app.v2.difficulty_batches import select_batches

    data = {"items": [{"item_id": "a", "fields": {"stem_markdown": "unchanged"}}, {"item_id": "b"}],
        "measure_definition": {"v": "unchanged"}}
    failed = {"call_id": "original", "status": "settled", "outcome": "failed"}
    success = failed | {"call_id": "cached", "outcome": "success"}
    assert select_batches(data, [failed, success], {}) == [(data, 16384)]
    parts = select_batches(data, [failed], {})
    assert [i for part, _ in parts for i in part["items"]] == data["items"]
    assert all(reserve == 32768 and part["measure_definition"] == data["measure_definition"] for part, reserve in parts)
    for status, outcome in [("settled", "failed"), ("quarantined", "unknown"), ("unknown", "unknown")]:
        with pytest.raises(DomainError, match="DIFFICULTY_REVIEW_MINIMUM_BATCH_EXHAUSTED"):
            select_batches(data, [], {"a": [failed | {"status": status, "outcome": outcome}]})
    # Reuse wins only for the exact successful request, never for its failed peer.
    assert len(select_batches(data, [], {"a": [failed, success]})) == 2


def test_legacy_cache_requires_exact_content_schema_model_and_parameters(monkeypatch):
    from types import SimpleNamespace
    from app.v2.contracts import request_hash
    from app.v2.difficulty_batches import matching_attempts
    from app.v2.inference import request_messages

    capacity = {"max_output_tokens": 32768, "context_tokens": 1000000,
        "safety_margin_tokens": 100, "estimate_method": "synthetic", "source_uri_or_config": "synthetic"}
    monkeypatch.setattr("app.v2.capacity.get_capacity", lambda *args: capacity)
    profile = SimpleNamespace(llm_base_url="https://fixture.invalid/v1", llm_model="fixture",
        llm_model_family="fixture", llm_temperature=0.3, llm_thinking="enabled", llm_reasoning_effort="high")
    data, schema, system = {"items": [{"item_id": "a", "fields": {"stem_markdown": "actual"}}]}, {"items": []}, "review"
    manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": "c", "unit_id": "u",
        "entries": [], "stage": "difficulty_reviewing", "role": "review", "plan_hash": "p"}
    params = {"max_tokens": 32768, "temperature": 0.3, "thinking": "enabled", "reasoning_effort": "high", "json_mode": True}
    row = {"manifest": manifest, "provider": profile.llm_base_url, "model": profile.llm_model,
        "model_family": profile.llm_model_family,
        "request_hash": request_hash(manifest, request_messages(system, data, schema, ensure_json_literal=True), profile.llm_base_url, profile.llm_model, params, schema)}
    assert matching_attempts([row], data, schema, system, profile, 16384) == [row]
    assert not matching_attempts([row], {"items": []}, schema, system, profile, 16384)
    assert not matching_attempts([row], data, {"different": []}, system, profile, 16384)
    # The per-call hint no longer changes the provider allowance; an explicit
    # user override does, and must therefore invalidate the cached identity.
    assert matching_attempts([row], data, schema, system, profile, 32768) == [row]
    assert not matching_attempts([row | {"model": "other"}], data, schema, system, profile, 16384)
    profile.llm_output_limit_tokens = 16384
    assert not matching_attempts([row], data, schema, system, profile, 16384)
    profile.llm_output_limit_tokens = None
    profile.llm_reasoning_effort = "max"
    assert not matching_attempts([row], data, schema, system, profile, 16384)


@pytest.mark.parametrize("axis", ["prerequisites", "steps", "computation_load"])
def test_one_good_quote_cannot_hide_an_invalid_sibling_quote(axis):
    row, item, unit = fixture()
    entry = row[axis] if axis == "computation_load" else row[axis][0]
    entry["evidence"].append({"field": "answer.reasoning", "quote": "fabricated"})
    report = bind_item(row, item, unit, {})
    assert report["result"] == "undecidable"
    assert report["processing_status"] == "invalid_or_missing_observation"
    assert not report["input_binding_complete"]


@pytest.mark.parametrize("fault", ["operation_list", "operation_dict", "level_list", "level_dict", "duplicate_dependency", "duplicate_quote"])
def test_malformed_difficulty_fields_never_crash_or_count_as_complete(fault):
    row, item, unit = fixture()
    if fault.startswith("operation_"):
        row["steps"][0]["operation"] = [] if fault.endswith("list") else {}
    elif fault.startswith("level_"):
        row["computation_load"]["level"] = [] if fault.endswith("list") else {}
    elif fault == "duplicate_dependency":
        row["steps"][1]["depends_on"] *= 2
    else:
        row["steps"][0]["evidence"] *= 2
    report = bind_item(row, item, unit, {})
    assert report["result"] == "undecidable" and not report["input_binding_complete"]


@pytest.mark.parametrize("fault", ["duplicate", "missing", "extra", "bad_quote", "malformed_array", "malformed_row"])
def test_execution_coverage_is_not_completed_merely_because_provider_returned(fault, monkeypatch):
    row, item, unit = fixture()
    raw = dict(row, item_id=item["item_id"])
    output = {"items": [raw]}
    if fault == "duplicate": output["items"] *= 2
    if fault == "missing": output["items"] = []
    if fault == "extra": output["items"].append(dict(row, item_id="unrequested"))
    if fault == "bad_quote": raw["prerequisites"][0]["evidence"][0]["quote"] = "invented"
    if fault == "malformed_array": output["items"] = None
    if fault == "malformed_row": output["items"].append(None)
    monkeypatch.setattr("app.v2.inference.invoke", lambda *a, **k: (output, {"call_id": "synthetic"}))
    report = evaluate_difficulty("r", "w", 0, {}, [unit], "v")
    assert report["status"] == "incomplete" and report["result"] == "undecidable"
    assert report["processing_status"] == "invalid_or_missing_observation"
    assert report["coverage"]["expected"] == report["coverage"]["recorded"] == 1


def test_bound_report_retains_version_content_target_and_successful_execution(monkeypatch):
    from app.v2.assessment_binding import input_binding
    row, item, unit = fixture()
    state = {"context": {"raw_input": "original requirement"}}
    monkeypatch.setattr("app.v2.inference.invoke", lambda *a, **k: ({"items": [dict(row, item_id="i1")]}, {}))
    report = evaluate_difficulty("r", "w", 0, state, [unit], "v")
    assert report["input_binding"] == input_binding(state, [unit], "v")
    assert report["status"] == "completed" and report["processing_status"] == "complete"
    assert report["coverage"] == {"expected": 1, "recorded": 1, "complete_observations": 1,
        "decided": 1, "invalid_response_entries": 0}


def test_legacy_success_at_smaller_output_cap_is_reused_but_tamper_is_not(monkeypatch):
    from types import SimpleNamespace
    from app.v2.contracts import request_hash
    from app.v2.difficulty_batches import matching_attempts
    from app.v2.inference import request_messages

    capacity = {"max_output_tokens": 32768, "context_tokens": 1000000,
        "safety_margin_tokens": 100, "estimate_method": "synthetic", "source_uri_or_config": "synthetic"}
    monkeypatch.setattr("app.v2.capacity.get_capacity", lambda *args: capacity)
    profile = SimpleNamespace(llm_base_url="https://fixture.invalid/v1", llm_model="fixture",
        llm_model_family="fixture", llm_temperature=0.3, llm_thinking="enabled", llm_reasoning_effort="high")
    data, schema, system = {"items": [{"item_id": "a", "fields": {"stem_markdown": "actual"}}]}, {"items": []}, "review"
    manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": "c", "unit_id": "u",
        "entries": [], "stage": "difficulty_reviewing", "role": "review", "plan_hash": "p"}
    saved = {"max_tokens": 16384, "temperature": 0.3, "thinking": "enabled",
             "reasoning_effort": "high", "json_mode": True}
    row = {"manifest": manifest, "provider": profile.llm_base_url, "model": profile.llm_model,
        "model_family": profile.llm_model_family, "parameters": saved,
        "request_hash": request_hash(manifest, request_messages(system, data, schema, ensure_json_literal=True),
            profile.llm_base_url, profile.llm_model, saved, schema)}
    assert matching_attempts([row], data, schema, system, profile, 16384) == [row]
    assert matching_attempts([row | {"parameters": {**saved, "max_tokens": 32768}}], data, schema, system,
                             profile, 16384) == []
    assert matching_attempts([row | {"request_hash": "0" * 64}], data, schema, system, profile, 16384) == []

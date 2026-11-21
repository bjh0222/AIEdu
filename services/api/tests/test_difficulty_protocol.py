"""Unpaid execution-contract regressions; no semantic calibration claim."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest
from test_difficulty_review import fixture


def sample():
    raw, item, unit = fixture()
    data = {"unit_title": unit["title"], "items": [{"item_id": item["item_id"], "fields": {
        "stem_markdown": item["stem_markdown"], "answer.final": item["answer"]["final"],
        "answer.reasoning": item["answer"]["reasoning"]}}], "measure_definition": {"fixture": True}}
    return {"items": [dict(raw, item_id=item["item_id"])]}, data, item, unit


@pytest.mark.parametrize("negative", [False, True])
def test_complete_positive_and_negative_never_trigger_execution_correction(negative):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    if negative: output["items"][0]["nonroutine_operations"] = []
    before = deepcopy([output, data, item, unit])
    result = correct_observations(data, output, [item], unit, {"call_id": "original"},
        lambda **kw: pytest.fail("Complete negative is not a schema fault"))
    assert result["complete"] and result["rows"][0]["result"] == ("violated" if negative else "satisfied")
    assert before == [output, data, item, unit]


def test_only_bad_quote_can_be_corrected_while_easy_observation_and_valid_axes_stay_locked():
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    output["items"][0]["nonroutine_operations"] = []
    output["items"][0]["prerequisites"][0]["evidence"][0]["quote"] = "not present"
    seen = []
    def callback(**kw):
        seen.append(kw)
        assert kw["data"]["items"] == data["items"]
        assert "difficulty_target" not in str(kw) and "difficulty_basis" not in str(kw)
        assert "minimum_" not in str(kw) and "must_not_leak" not in str(kw)
        revised = deepcopy(output)
        revised["items"][0]["prerequisites"][0]["evidence"][0]["quote"] = "条件甲"
        return revised, {"call_id": "new", "request_hash": "new-hash"}
    bound = correct_observations(data, output, [item], unit, {"call_id": "old"}, callback)
    assert len(seen) == 1 and bound["complete"]
    assert bound["rows"][0]["result"] == "violated"
    assert bound["rows"][0]["counts"]["nonroutine_operations"] == 0
    assert bound["execution_corrections"][0]["not_a_content_repair"]


@pytest.mark.parametrize("fault", ["change_complete_axis", "change_valid_entry", "remove_declared_id", "add_declared_id", "change_reason", "change_operation"])
def test_quote_correction_cannot_rewrite_or_inflate_existing_observations(fault):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    output["items"][0]["prerequisites"].append(deepcopy(output["items"][0]["prerequisites"][0]) | {
        "id": "p2", "evidence": [{"field": "answer.reasoning", "quote": "missing"}]})
    def callback(**kw):
        revised = deepcopy(output)
        row = revised["items"][0]
        row["prerequisites"][1]["evidence"][0]["quote"] = "比较方法乙"
        if fault == "change_complete_axis": row["nonroutine_operations"] = []
        if fault == "change_valid_entry": row["prerequisites"][0]["reason"] += "different claim"
        if fault == "remove_declared_id": row["prerequisites"].pop()
        if fault == "add_declared_id": row["prerequisites"].append(deepcopy(row["prerequisites"][0]) | {"id": "p3"})
        if fault == "change_reason": row["prerequisites"][1]["reason"] += "changed semantic interpretation"
        if fault == "change_operation": row["prerequisites"][1]["operation"] = "transfer"
        return revised, {"call_id": "new"}
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_CHANGED_OBSERVATION"):
        correct_observations(data, output, [item], unit, {}, callback)


def test_one_missing_axis_executes_once_and_preserves_original_valid_sibling():
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    second = deepcopy(item) | {"item_id": "i2", "ordinal": 2}
    data["items"].append(deepcopy(data["items"][0]) | {"item_id": "i2"})
    valid = deepcopy(output["items"][0]) | {"item_id": "i2"}
    output["items"].append(valid)
    corrected = deepcopy(output["items"][0])
    del output["items"][0]["prerequisites"]
    seen = []
    def callback(**kw):
        seen.append(kw)
        assert [r["item_id"] for r in kw["data"]["items"]] == ["i1"]
        return {"items": [corrected]}, {"call_id": "new"}
    result = correct_observations(data, output, [item, second], unit, {"call_id": "original"}, callback)
    assert len(seen) == 1 and result["complete"]
    assert result["rows"][1]["audit"] == {"call_id": "original"}
    assert result["rows"][0]["audit"]["call_id"] == "new"


@pytest.mark.parametrize("fault", ["extra", "duplicate", "cycle"])
def test_ambiguous_rows_or_semantic_dependency_failure_do_not_dispatch_correction(fault):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    if fault == "extra": output["items"].append(deepcopy(output["items"][0]) | {"item_id": "outside"})
    if fault == "duplicate": output["items"] *= 2
    if fault == "cycle": output["items"][0]["steps"][0]["depends_on"] = ["s3"]
    with pytest.raises(DomainError, match="DIFFICULTY_.*CONTRACT_INVALID"):
        correct_observations(data, output, [item], unit, {}, lambda **kw: pytest.fail("No unsafe correction"))


def test_second_invalid_response_stops_and_unknown_propagates_without_retry():
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    del output["items"][0]["prerequisites"]
    seen = []
    def invalid(**kw):
        seen.append(kw)
        return output, {"call_id": "new"}
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_UNRESOLVED"):
        correct_observations(data, output, [item], unit, {}, invalid)
    assert len(seen) == 1
    def unknown(**kw): raise DomainError("UNKNOWN_CALL")
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        correct_observations(data, output, [item], unit, {}, unknown)


def test_correction_identity_is_stable_under_audit_timing_but_changes_with_content():
    from app.v2.difficulty_protocol import bind_observations, correction_payload
    output, data, item, unit = sample()
    del output["items"][0]["prerequisites"]
    one = bind_observations(data, output, [item], unit, {"call_id": "original", "request_hash": "h", "elapsed_seconds": 10})
    two = bind_observations(data, output, [item], unit, {"call_id": "original", "request_hash": "h", "elapsed_seconds": 11, "cache": True})
    first = correction_payload(data, output, one, attempt=1)
    assert first == correction_payload(data, output, two, attempt=1)
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_EXHAUSTED"):
        correction_payload(data, output, one, attempt=2)
    changed = deepcopy(data)
    changed["items"][0]["fields"]["answer.final"] += "changed"
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_INPUT_MISMATCH"):
        correction_payload(changed, output, one, attempt=1)
    assert digest(first["execution_correction"])


def test_configured_consumer_dispatches_only_one_new_blind_stage_and_keeps_negative(monkeypatch):
    from app.v2.difficulty_review import evaluate_difficulty
    output, _, item, unit = sample()
    output["items"][0]["nonroutine_operations"] = []
    output["items"][0]["prerequisites"][0]["evidence"][0]["quote"] = "bad"
    seen = []
    def invoke(*a, **kw):
        seen.append(kw)
        assert kw["role"] == "review" and kw["provider_factory"] == "same-provider"
        assert "minimum_" not in str(kw["data"]) and "must_not_leak" not in str(kw["data"])
        if len(seen) == 1:
            assert kw["stage"] == "difficulty_reviewing"
            return output, {"call_id": "primary", "elapsed_seconds": 12}
        assert kw["stage"] == "difficulty_contract_correcting" and kw["output_tokens"] == seen[0]["output_tokens"]
        assert kw["data"]["items"] == seen[0]["data"]["items"]
        revised = deepcopy(output)
        revised["items"][0]["prerequisites"][0]["evidence"][0]["quote"] = "条件甲"
        return revised, {"call_id": "correction"}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    result = evaluate_difficulty("r", "w", 0, {"mode": "configured"}, [unit], "v", "same-provider")
    assert len(seen) == len(result["audits"]) == 2
    assert result["result"] == "violated" and result["status"] == "completed"
    assert result["items"][0]["audit"]["call_id"] == "correction"
    assert result["audits"][0]["execution_contract"]["original_output_hash"] == digest(output)
    assert result["audits"][1]["execution_correction"]["maximum"] == 1


@pytest.mark.parametrize("failure", ["UNKNOWN_CALL", "PROVIDER_CALL_FAILED", "DIFFICULTY_REVIEW_MINIMUM_BATCH_EXHAUSTED"])
def test_configured_consumer_does_not_correct_failed_transport_or_exhausted_batch(failure, monkeypatch):
    from app.v2.difficulty_review import evaluate_difficulty
    _, _, _, unit = sample()
    seen = []
    def invoke(*a, **kw):
        seen.append(kw["stage"])
        raise DomainError(failure)
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    with pytest.raises(DomainError, match=failure):
        evaluate_difficulty("r", "w", 0, {"mode": "configured"}, [unit], "v")
    assert seen == ["difficulty_reviewing"]


def test_configured_consumer_stops_after_one_failed_correction(monkeypatch):
    from app.v2.difficulty_review import evaluate_difficulty
    output, _, _, unit = sample()
    del output["items"][0]["prerequisites"]
    seen = []
    def invoke(*a, **kw):
        seen.append(kw["stage"])
        return output, {"call_id": str(len(seen))}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_UNRESOLVED"):
        evaluate_difficulty("r", "w", 0, {"mode": "configured"}, [unit], "v")
    assert seen == ["difficulty_reviewing", "difficulty_contract_correcting"]


@pytest.mark.parametrize("fault", ["duplicate_axis_id", "cycle_with_bad_quote", "dangling_with_bad_quote", "oversized_axis"])
def test_irreparable_declared_identity_or_dependency_stops_before_correction(fault):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    row = output["items"][0]
    if fault == "duplicate_axis_id":
        row["prerequisites"].append(deepcopy(row["prerequisites"][0]) | {
            "evidence": [{"field": "answer.final", "quote": "精确结论丁"}]})
    elif fault == "oversized_axis":
        row["prerequisites"] = [deepcopy(row["prerequisites"][0]) | {"id": f"p{i}"} for i in range(41)]
    else:
        row["steps"][0]["depends_on"] = ["s3" if fault.startswith("cycle") else "outside"]
        row["steps"][0]["evidence"][0]["quote"] = "missing"
    with pytest.raises(DomainError, match="DIFFICULTY_.*CONTRACT_INVALID"):
        correct_observations(data, output, [item], unit, {}, lambda **kw: pytest.fail("No doomed request"))


@pytest.mark.parametrize("axis", ["cognitive_operations", "steps"])
def test_incomplete_empty_required_axis_can_execute_missing_observation_once(axis):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    revised = deepcopy(output)
    output["items"][0][axis] = []
    seen = []
    def correct(**kw):
        seen.append(kw)
        return revised, {"call_id": "correction"}
    result = correct_observations(data, output, [item], unit, {}, correct)
    assert result["complete"] and len(seen) == 1
    assert axis not in seen[0]["data"]["execution_correction"]["locks"][0]["complete_axes"]


def test_correction_schema_excludes_routine_nonroutine_code():
    from app.v2.difficulty_protocol import response_contract
    from app.v2.difficulty_review import NONROUTINE
    _, data, _, _ = sample()
    row_schema = response_contract(data["items"])["properties"]["items"]["items"]["anyOf"][0]
    codes = row_schema["properties"]["nonroutine_operations"]["items"]["properties"]["operation"]["enum"]
    assert set(codes) == NONROUTINE


@pytest.mark.parametrize("fault", ["level", "quote", "dependencies"])
def test_valid_load_and_dependency_declarations_cannot_change_during_quote_fix(fault):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    output["items"][0]["steps"][0]["evidence"].append({"field": "answer.final", "quote": "bad"})
    def correct(**kw):
        revised = deepcopy(output)
        row = revised["items"][0]
        row["steps"][0]["evidence"][1]["quote"] = "精确结论丁"
        if fault == "level": row["computation_load"]["level"] = "heavy"
        if fault == "quote": row["steps"][0]["evidence"][0]["quote"] = "条件甲"
        if fault == "dependencies": row["steps"][0]["depends_on"] = ["s2"]
        return revised, {}
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_CHANGED_OBSERVATION"):
        correct_observations(data, output, [item], unit, {}, correct)


@pytest.mark.parametrize("raw", [None, [], {"items": None}, {"items": []}, {"items": [None]}])
def test_missing_or_malformed_rows_are_either_rejected_or_explicitly_executed(raw):
    from app.v2.difficulty_protocol import correct_observations
    valid, data, item, unit = sample()
    if raw == {"items": [None]}:
        with pytest.raises(DomainError, match="DIFFICULTY_BATCH_CONTRACT_INVALID"):
            correct_observations(data, raw, [item], unit, {}, lambda **kw: pytest.fail("Unidentified row"))
        return
    seen = []
    def correct(**kw):
        seen.append(kw)
        assert kw["data"]["items"] == data["items"]
        return valid, {"call_id": "correction"}
    result = correct_observations(data, raw, [item], unit, {}, correct)
    assert result["complete"] and len(seen) == 1


@pytest.mark.parametrize("field,value", [("id", []), ("operation", {}), ("reason", None), ("evidence", None)])
def test_malformed_entry_fields_do_not_crash_correction_preparation(field, value):
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    revised = deepcopy(output)
    output["items"][0]["prerequisites"][0][field] = value
    # operation is optional on prerequisites, so malformed optional content
    # alone is not used to fabricate a missing execution obligation.
    if field == "operation":
        result = correct_observations(data, output, [item], unit, {}, lambda **kw: pytest.fail("Already complete"))
    else:
        result = correct_observations(data, output, [item], unit, {}, lambda **kw: (revised, {}))
    assert result["complete"]


def test_failed_correction_stops_all_remaining_items_and_unit_waves(monkeypatch):
    from app.v2.difficulty_review import evaluate_difficulty
    output, _, item, unit = sample()
    unit["items"] = [deepcopy(item) | {"item_id": f"i{i}", "ordinal": i} for i in range(1, 4)]
    units = [unit, deepcopy(unit) | {"unit_id": "u2"}]
    del output["items"][0]["prerequisites"]
    seen = []
    def invoke(*a, **kw):
        seen.append((kw["stage"], kw["unit_id"], [i["item_id"] for i in kw["data"]["items"]]))
        return output, {}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    with pytest.raises(DomainError, match="DIFFICULTY_CORRECTION_UNRESOLVED"):
        evaluate_difficulty("r", "w", 0, {"mode": "configured"}, units, "v")
    assert seen == [("difficulty_reviewing", "u1", ["i1"]), ("difficulty_contract_correcting", "u1", ["i1"])]


def test_configured_correction_dispatch_identity_ignores_cache_audit_fields(monkeypatch):
    from app.v2.difficulty_review import evaluate_difficulty
    output, _, _, unit = sample()
    revised = deepcopy(output)
    del output["items"][0]["prerequisites"]
    dispatches = []
    invocation = [0]
    def invoke(*a, **kw):
        invocation[0] += 1
        if kw["stage"] == "difficulty_reviewing":
            return output, {"call_id": "primary", "request_hash": "stable",
                "elapsed_seconds": invocation[0], "cache": invocation[0] > 1}
        dispatches.append((kw["key"], deepcopy(kw["data"]), deepcopy(kw["output_schema"]), kw["system"]))
        return revised, {"call_id": "correction"}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    for _ in range(2):
        assert evaluate_difficulty("r", "w", 0, {"mode": "configured"}, [unit], "v")["status"] == "completed"
    assert len(dispatches) == 2 and dispatches[0] == dispatches[1]


def test_correction_schema_preserves_complete_and_partial_legacy_entries_without_contradiction():
    from app.v2.difficulty_protocol import correct_observations
    output, data, item, unit = sample()
    output["items"][0]["cognitive_operations"][0]["legacy_note"] = "preserved data, not instructions"
    output["items"][0]["prerequisites"][0]["legacy_note"] = "do not rewrite accepted entry"
    output["items"][0]["prerequisites"].append(deepcopy(output["items"][0]["prerequisites"][0]) | {
        "id": "p2", "evidence": [{"field": "answer.final", "quote": "bad"}]})
    def correct(**kw):
        properties = kw["output_schema"]["properties"]["items"]["items"]["anyOf"][0]["properties"]
        assert properties["cognitive_operations"] == {"const": output["items"][0]["cognitive_operations"]}
        assert properties["computation_load"] == {"const": output["items"][0]["computation_load"]}
        assert {"const": output["items"][0]["prerequisites"][0]} in properties["prerequisites"]["items"]["anyOf"]
        revised = deepcopy(output)
        revised["items"][0]["prerequisites"][1].pop("legacy_note")
        revised["items"][0]["prerequisites"][1]["evidence"][0]["quote"] = "精确结论丁"
        return revised, {}
    assert correct_observations(data, output, [item], unit, {}, correct)["complete"]

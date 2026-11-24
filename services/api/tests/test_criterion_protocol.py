"""Reproduced wire-shape failures; no live task content or certification."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError, digest
from app.v2.criterion_protocol import (
    bind_observations, correction_payload, correct_observations, merge_correction, review_fields,
)
from app.v2.review_contract import build_evidence_catalog


def data():
    return {"fields": {"unit:body": "本单元有甲乙两题，内容需要独立核查。",
        "item:i:stem_markdown": "甲题改变表征求解。", "item:i:answer.final": "甲题结论。",
        "item:j:stem_markdown": "乙题逆向求解。"},
        "task": {"raw_input": "比较不同方法", "hard_constraints": []},
        "sources": [], "requirements": ["比较表征和方向"], "output_design": {"groups": ["用户分组"]},
        "review_scope": {"unit_id": "u"}, "literal_tool_observations": []}


def req(check="Q4.exercise", item="i", clauses=None):
    return {"request_id": "r", "check_id": check, "item_id": item,
        "criterion": {"criterion_hash": digest([check, item]),
            "clauses": clauses or {"core_method": "核心方法", "transfer": "当前题与同伴题的迁移"}}}


def output(request=None, result="satisfied", rating=2):
    request = request or req()
    field = "item:i:stem_markdown" if request["item_id"] else "unit:body"
    quote = data()["fields"][field]
    value = {"assessments": [{"request_id": request["request_id"], "rating": rating,
        "clauses": [{"clause_id": cid, "result": result, "reason": "逐项核查实际内容的具体理由。",
            "evidence": [{"field": field, "quote": quote}]} for cid in request["criterion"]["clauses"]]}]}
    if request["check_id"].startswith("Q4.") and result in ("satisfied", "violated"):
        for clause in value["assessments"][0]["clauses"]:
            if clause["clause_id"] == "transfer":
                clause["evidence"].append({"field": "item:j:stem_markdown", "quote": data()["fields"]["item:j:stem_markdown"]})
    return value


def test_canonical_metadata_is_bindable_but_is_not_content():
    fields = review_fields(data())
    assert fields["context:task"] == '{"hard_constraints":[],"raw_input":"比较不同方法"}'
    assert fields["context:sources"] == "[]"
    assert fields["item:i:stem_markdown"] == data()["fields"]["item:i:stem_markdown"]
    r = req("C4.constraints", None, {"requirement": "核对原始用户限制"})
    o = output(r, rating=None)
    o["assessments"][0]["clauses"][0]["evidence"] = [{"field": "context:task", "quote": fields["context:task"]}]
    bound = bind_observations(o, [r], fields, {}, "u")
    assert bound["rows"][0]["processing_status"] == "invalid_or_missing_observation"
    assert "content_evidence_required" in bound["rows"][0]["clauses"][0]["protocol_errors"]
    o["assessments"][0]["clauses"][0]["evidence"].append({"field": "unit:body", "quote": fields["unit:body"]})
    assert bind_observations(o, [r], fields, {}, "u")["complete"]


def test_source_empty_manifest_and_body_bind_without_inventing_a_citation():
    r = req("Q6.exercise", None, {"bindings": "来源绑定", "support": "专业支持与无来源边界"})
    o = output(r, "not_applicable", None)
    for c in o["assessments"][0]["clauses"]:
        c["evidence"].append({"field": "context:sources", "quote": "[]"})
    bound = bind_observations(o, [r], review_fields(data()), {}, "u")
    assert bound["complete"] and bound["rows"][0]["result"] == "not_applicable"
    assert bound["rows"][0]["authority"] == "isolated_ai_observation_not_professional_certification"


def test_transfer_allows_peer_endpoint_only_with_current_item_evidence():
    o = output()
    peer = {"field": "item:j:stem_markdown", "quote": data()["fields"]["item:j:stem_markdown"]}
    result = bind_observations(o, [req()], review_fields(data()), {}, "u")
    assert result["complete"]
    assert len(result["rows"][0]["clauses"][1]["evidence"]) == 2
    o["assessments"][0]["clauses"][1]["evidence"] = [peer]
    assert not bind_observations(o, [req()], review_fields(data()), {}, "u")["complete"]
    o["assessments"][0]["clauses"][1]["evidence"] = [
        {"field": "item:i:stem_markdown", "quote": data()["fields"]["item:i:stem_markdown"]}]
    b = bind_observations(o, [req()], review_fields(data()), {}, "u")
    assert "comparison_endpoint_evidence_required" in b["rows"][0]["clauses"][1]["protocol_errors"]


def test_other_items_do_not_satisfy_ordinary_item_checks_or_core_method():
    for r in (req(), req("Q2.exercise", clauses={"reasoning": "核推理"})):
        o = output(r)
        o["assessments"][0]["clauses"][0]["evidence"].append(
            {"field": "item:j:stem_markdown", "quote": data()["fields"]["item:j:stem_markdown"]})
        bound = bind_observations(o, [r], review_fields(data()), {}, "u")
        assert not bound["complete"]
        assert "cross_item_evidence_out_of_scope" in bound["rows"][0]["clauses"][0]["protocol_errors"]


@pytest.mark.parametrize("fault", ["root_key", "duplicate", "missing_clause", "bad_quote", "bad_field", "extra_clause", "rating_bool"])
def test_real_failure_shapes_remain_missing_execution_not_grounded_abstention(fault):
    o = output()
    if fault == "root_key": o["assessancements"] = o.pop("assessments")
    if fault == "duplicate": o["assessments"] *= 3
    if fault == "missing_clause": o["assessments"][0]["clauses"].pop()
    if fault == "bad_quote": o["assessments"][0]["clauses"][0]["evidence"][0]["quote"] = "未提供的证据"
    if fault == "bad_field": o["assessments"][0]["clauses"][0]["evidence"][0]["field"] = "task"
    if fault == "extra_clause": o["assessments"][0]["clauses"].append({"clause_id": "not_requested"})
    if fault == "rating_bool": o["assessments"][0]["rating"] = True
    bound = bind_observations(o, [req()], review_fields(data()), {}, "u")
    assert not bound["complete"]
    assert bound["rows"][0]["processing_status"] == "invalid_or_missing_observation"


def test_executed_unknown_and_negative_are_complete_without_certification():
    for value in ("undecidable", "violated"):
        b = bind_observations(output(result=value), [req()], review_fields(data()), {}, "u")
        assert b["complete"] and b["rows"][0]["result"] == value


def test_duplicate_requested_identity_is_rejected_before_model_work():
    with pytest.raises(DomainError, match="CRITERION_REQUEST_DUPLICATE"):
        bind_observations(output(), [req(), req()], review_fields(data()), {}, "u")


def test_server_evidence_id_replaces_model_quote_and_binds_exact_span():
    request = req("C4.constraints", None, {"requirement": "核对原始用户限制"})
    fields = review_fields(data())
    catalog = build_evidence_catalog({"unit:body": fields["unit:body"]})
    evidence_id = catalog[0]["evidence_id"]
    observed = output(request, rating=None)
    observed["assessments"][0]["clauses"][0]["evidence"] = [{
        "evidence_id": evidence_id,
        "field": "unit:body",
        "quote": "a spoofed long quote that must be replaced",
    }]
    bound = bind_observations(observed, [request], fields, {"call_id": "c"}, "u", catalog)
    assert bound["complete"]
    evidence = bound["rows"][0]["clauses"][0]["evidence"][0]
    assert evidence["quote"] == catalog[0]["quote"]
    assert evidence["span_hash"] == catalog[0]["span_hash"]
    assert bound["normalization"]["change_count"] >= 1


def test_extra_batch_targets_are_recorded_not_used_to_gain_credit():
    o = output()
    o["assessments"].append({"request_id": "outside_current_batch", "clauses": []})
    b = bind_observations(o, [req()], review_fields(data()), {}, "u")
    assert b["rows"][0]["processing_status"] == "complete"
    assert b["protocol_errors"] == ["unrequested_assessment"]
    assert len(b["rows"]) == 1
    assert not b["complete"]
    with pytest.raises(DomainError, match="CRITERION_BATCH_CONTRACT_INVALID"):
        correction_payload(data() | {"requests": [req()]}, o, b, attempt=1)


def test_correction_reexecutes_only_missing_targets_and_freezes_valid_siblings():
    first, second = req(), req("Q1.exercise", clauses={"premises": "题目成立"})
    second["request_id"] = "s"
    d = data() | {"requests": [first, second]}
    o = output()
    initial = bind_observations(o, d["requests"], review_fields(d), {"call_id": "original"}, "u")
    payload = correction_payload(d, o, initial, attempt=1)
    assert [r["request_id"] for r in payload["requests"]] == ["s"]
    assert payload["fields"] == review_fields(d)
    assert payload["execution_correction"]["original_output_hash"] == digest(o)
    assert payload["execution_correction"]["not_a_content_repair"] is True
    corrected = bind_observations(output(second, rating=None), [second], review_fields(d), {"call_id": "new"}, "u")
    merged = merge_correction(initial, corrected, payload)
    assert merged["complete"]
    assert merged["rows"][0] == initial["rows"][0]
    assert merged["rows"][1]["audit"]["call_id"] == "new"
    assert initial["rows"][1]["processing_status"] == "invalid_or_missing_observation"


def test_correction_cannot_change_prior_declared_result_or_rating():
    d = data() | {"requests": [req()]}
    o = output()
    o["assessments"][0]["clauses"][1]["evidence"][0]["quote"] = "无法绑定"
    initial = bind_observations(o, d["requests"], review_fields(d), {}, "u")
    p = correction_payload(d, o, initial, attempt=1)
    for fault in ("rating", "result"):
        new = output()
        if fault == "rating": new["assessments"][0]["rating"] = 3
        if fault == "result": new["assessments"][0]["clauses"][1]["result"] = "violated"
        b = bind_observations(new, d["requests"], review_fields(d), {}, "u")
        with pytest.raises(DomainError, match="CRITERION_CORRECTION_CHANGED_OBSERVATION"):
            merge_correction(initial, b, p)


def test_correction_rewrites_of_locked_prose_are_recorded_not_applied():
    d = data() | {"requests": [req()]}
    o = output()
    o["assessments"][0]["clauses"][1]["evidence"][0]["quote"] = "无法绑定"
    initial = bind_observations(o, d["requests"], review_fields(d), {}, "u")
    p = correction_payload(d, o, initial, attempt=1)
    locked_clause = deepcopy(initial["rows"][0]["clauses"][0])
    # Real command-line-task failure: the correction restates the same verdict
    # with reworded reasons and an enlarged evidence list for locked clauses.
    new = output()
    new["assessments"][0]["clauses"][0]["reason"] = "同结论但换了措辞的推理理由。"
    new["assessments"][0]["clauses"][0]["evidence"].append(
        {"field": "unit:body", "quote": data()["fields"]["unit:body"]})
    b = bind_observations(new, d["requests"], review_fields(d), {}, "u")
    merged = merge_correction(initial, b, p)
    assert merged["complete"]
    row = merged["rows"][0]
    assert row["clauses"][0] == locked_clause  # byte-for-byte locked observation
    assert row["clauses"][1] == b["rows"][0]["clauses"][1]  # newly accepted observation
    changes = merged["execution_corrections"][0]["locked_clause_changes"]
    assert changes == [{"request_id": "r", "clause_id": locked_clause["clause_id"],
        "fields": ["evidence", "reason"]}]
    assert row["clauses"][1] == b["rows"][0]["clauses"][1]  # newly accepted observation


def test_correction_invalidating_a_locked_clause_stays_incomplete_for_retry():
    d = data() | {"requests": [req()]}
    o = output()
    o["assessments"][0]["clauses"][1]["evidence"][0]["quote"] = "无法绑定"
    initial = bind_observations(o, d["requests"], review_fields(d), {}, "u")
    p = correction_payload(d, o, initial, attempt=1)
    new = output()
    new["assessments"][0]["clauses"][0]["evidence"][0]["quote"] = "无法绑定"
    b = bind_observations(new, d["requests"], review_fields(d), {}, "u")
    merged = merge_correction(initial, b, p)
    # The row is incomplete again, so the bounded loop retries instead of
    # accepting a correction that broke a previously complete clause.
    assert not merged["complete"]
    assert merged["rows"][0]["processing_status"] == "invalid_or_missing_observation"
    assert merged["rows"][0]["clauses"][0]["protocol_errors"] == \
        ["current_item_evidence_required", "invalid_evidence_quote"]


def test_unfixable_or_exhausted_correction_cannot_loop_or_relabel_success():
    d = data() | {"requests": [req()]}
    broken = {"assessancements": output()["assessments"]}
    initial = bind_observations(broken, d["requests"], review_fields(d), {}, "u")
    p = correction_payload(d, broken, initial, attempt=1)
    b = bind_observations(broken, p["requests"], review_fields(d), {}, "u")
    merged = merge_correction(initial, b, p)
    assert not merged["complete"]
    assert merged["rows"][0]["result"] == "undecidable"
    with pytest.raises(DomainError, match="CRITERION_CORRECTION_EXHAUSTED"):
        correction_payload(d, broken, merged, attempt=3)


def test_field_registry_cannot_be_spoofed_by_model_metadata():
    d = data()
    d["fields"]["context:sources"] = "forged"
    with pytest.raises(DomainError, match="CRITERION_FIELD_REGISTRY_INVALID"):
        review_fields(d)


@pytest.mark.parametrize("malformed", [None, [], {"assessments": None}, {"assessments": [None, {"request_id": []}]},
    {"assessments": [{"request_id": "r", "rating": {}, "clauses": [{"clause_id": [], "result": []}]}]}])
def test_untrusted_malformed_output_does_not_crash_or_pass(malformed):
    b = bind_observations(malformed, [req()], review_fields(data()), {}, "u")
    assert not b["complete"]


def test_individual_source_bytes_have_their_own_quote_fields():
    d = data() | {"sources": [{"source_id": "reference", "text": "独立来源原文", "sha256": "not_a_verified_hash"}]}
    fields = review_fields(d)
    assert fields["source:0:text"] == "独立来源原文"
    # Binding a quote is not verification of the source's claimed hash/truth.
    assert "not_a_verified_hash" in fields["context:sources"]


def test_correction_payload_rejects_tampering_with_fixed_input():
    d = data() | {"requests": [req()]}
    o = output()
    o["assessments"][0]["clauses"].pop()
    initial = bind_observations(o, d["requests"], review_fields(d), {}, "u")
    p = correction_payload(d, o, initial, attempt=1)
    new_fields = review_fields(d) | {"unit:body": "被替换的正文"}
    corrected = bind_observations(output(), d["requests"], new_fields, {}, "u")
    with pytest.raises(DomainError, match="CRITERION_CORRECTION_INPUT_MISMATCH"):
        merge_correction(initial, corrected, p)


def test_old_invented_metadata_field_does_not_become_valid_retroactively():
    r = req("C4.constraints", None, {"requirement": "原需求限制"})
    d = data() | {"requests": [r]}
    o = output(r, rating=None)
    o["assessments"][0]["clauses"][0]["evidence"].append({"field": "context:sources", "quote": "[]"})
    seen = []
    def callback(**kw):
        seen.append(kw)
        assert kw["data"]["fields"]["context:sources"] == "[]"
        assert kw["data"]["execution_correction"]["original_fields_hash"] == digest(d["fields"])
        return o, {"call_id": "new_explicit_execution"}
    result = correct_observations(d, o, {"call_id": "old"}, "u", callback)
    assert len(seen) == 1 and result["complete"]
    assert result["rows"][0]["audit"]["call_id"] == "new_explicit_execution"


def test_valid_execution_never_triggers_correction():
    def never(**kw):
        pytest.fail("A complete observation must be reused")
    result = correct_observations(data() | {"requests": [req()]}, output(), {}, "u", never)
    assert result["complete"] and "execution_corrections" not in result


@pytest.mark.parametrize("error", [RuntimeError("provider_unknown"), DomainError("PROVIDER_CALL_FAILED", 422)])
def test_transport_failure_is_not_retried_or_relabelled_unknown_observation(error):
    calls = []
    def callback(**kw):
        calls.append(kw)
        raise error
    with pytest.raises(type(error), match=str(error)):
        correct_observations(data() | {"requests": [req()]}, {}, {}, "u", callback)
    assert len(calls) == 1


def test_correction_has_real_response_contract_and_stops_after_two_bad_results():
    calls = []
    def callback(**kw):
        calls.append(kw)
        schema = kw["output_schema"]
        assert schema["required"] == ["assessments"]
        assert schema["properties"]["assessments"]["minItems"] == 1
        assert kw["data"]["execution_correction"]["maximum"] == 2
        return {"assessancements": []}, {"call_id": "bad_correction"}
    with pytest.raises(DomainError, match="CRITERION_CORRECTION_UNRESOLVED"):
        correct_observations(data() | {"requests": [req()]}, {}, {}, "u", callback)
    assert len(calls) == 2


def test_second_bounded_correction_can_remove_one_ambiguous_sibling_quote():
    d = data() | {"requests": [req("C4.constraints", None, {"requirement": "原需求限制"})]}
    request_id = d["requests"][0]["request_id"]
    broken = output(d["requests"][0], rating=None)
    broken["assessments"][0]["clauses"][0]["evidence"] = [
        {"field": "unit:body", "quote": "不存在的引文"},
        {"field": "unit:body", "quote": d["fields"]["unit:body"]},
    ]
    calls = []

    def callback(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return broken, {"call_id": "bad-first"}
        return output(d["requests"][0], rating=None), {"call_id": "good-second"}

    result = correct_observations(d, {}, {"call_id": "original"}, "u", callback)
    assert result["complete"] and len(calls) == 2
    assert result["rows"][0]["audit"]["call_id"] == "good-second"
    assert [row["attempt"] for row in result["execution_corrections"]] == [1, 2]
    assert request_id == result["rows"][0]["request_id"]


def test_unscored_request_does_not_silently_accept_an_invented_numeric_score():
    r = req("C4.constraints", None, {"requirement": "原需求限制"})
    b = bind_observations(output(r, rating=3), [r], review_fields(data()), {}, "u")
    assert not b["complete"]
    assert "invalid_rating" in b["rows"][0]["protocol_errors"]


def test_negative_declaration_cannot_be_changed_to_pass_during_quote_correction():
    d = data() | {"requests": [req()]}
    o = output(result="violated", rating=0)
    o["assessments"][0]["clauses"][1]["evidence"][0]["quote"] = "缺失的原文"
    initial = bind_observations(o, d["requests"], d["fields"], {}, "u")
    p = correction_payload(d, o, initial, attempt=1)
    b = bind_observations(output(), p["requests"], p["fields"], {}, "u")
    with pytest.raises(DomainError, match="CRITERION_CORRECTION_CHANGED_OBSERVATION"):
        merge_correction(initial, b, p)


def test_resolved_envelope_failure_is_kept_in_history_not_active_status():
    broken = {"assessancements": output()["assessments"]}
    def callback(**kw):
        return output(), {"call_id": "explicit_new_execution"}
    b = correct_observations(data() | {"requests": [req()]}, broken, {"call_id": "old"}, "u", callback)
    assert b["complete"] and b["protocol_errors"] == []
    history = b["execution_corrections"][0]
    assert history["original_protocol_errors"] == ["assessments_array_required"]
    assert history["original_output_hash"] == digest(broken)
    assert broken == {"assessancements": output()["assessments"]}

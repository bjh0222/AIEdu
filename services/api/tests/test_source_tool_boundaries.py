"""Synthetic tool decisions only: no external requests or paid model calls."""
from copy import deepcopy
from urllib.parse import quote

import pytest

from test_generation_source_tools import running


@pytest.mark.parametrize("field", ["selected_materials", "base_units", "source_units"])
def test_private_long_passage_from_all_role_inputs_is_not_a_public_query(field):
    from app.v2.generation_tools import _public_query
    from app.v2.contracts import DomainError
    private = "confidential synthetic passage only for this frozen educational request"
    with pytest.raises(DomainError, match="PRIVATE_DATA"):
        _public_query(private[:40], {field: [{"text": private, "body": private}]})


@pytest.mark.parametrize("url", [
    "https://example.org/read?q=" + quote("private synthetic source paragraph not intended for publication"),
    "https://example.org/" + quote("private synthetic source paragraph not intended for publication"),
    "https://example.org/?access_token=synthetic-secret",
])
def test_model_read_url_cannot_export_private_material_or_credentials(client, monkeypatch, url):
    from app.v2 import generation_tools as g, public_sources
    from app.v2.contracts import DomainError
    rid, epoch, _ = running(client)
    called = []
    monkeypatch.setattr(public_sources, "read_source", lambda *a, **k: called.append(a) or {"status": "ok"})
    with pytest.raises(DomainError, match="PRIVATE_DATA"):
        g._tool_result(rid, "source-fixture", epoch, {"name": "read_source", "url": url},
            {"selected_materials": [{"text": "private synthetic source paragraph not intended for publication"}]}, None)
    assert called == []


@pytest.mark.parametrize("extra", [{"teaching_arrangement": {}}, {"assessments": []}, {"units": []}, {"body": "candidate"}])
def test_tool_envelope_is_exclusive_before_any_network(client, monkeypatch, extra):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    calls = []
    decision = {"tool_requests": [{"name": "search_sources", "query": "public topic"}], **extra}
    monkeypatch.setattr(g, "invoke", lambda *a, **k: (decision, {"call_id": "synthetic"}))
    monkeypatch.setattr(g, "_source_tool", lambda *a: calls.append(a) or {"status": "ok"})
    with pytest.raises(DomainError, match="TOOL_LIMIT_OR_OUTPUT_INVALID"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="planning", unit_id="unit", data={}, system="A")
    assert calls == []


@pytest.mark.parametrize("requests", [[], None, {}, "search"])
def test_empty_or_malformed_tool_decision_is_not_a_candidate(client, monkeypatch, requests):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    monkeypatch.setattr(g, "invoke", lambda *a, **k: ({"tool_requests": requests}, {"call_id": "synthetic"}))
    with pytest.raises(DomainError, match="TOOL_LIMIT_OR_OUTPUT_INVALID"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="planning", unit_id="unit", data={}, system="A")


@pytest.mark.parametrize("field", ["network_tool_rounds", "network_requests_per_round"])
def test_boolean_is_not_a_network_budget(client, monkeypatch, field):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    state["execution_policy"][field] = True
    calls = []
    monkeypatch.setattr(g, "invoke", lambda *a, **k: calls.append(k) or ({"body": "x"}, {}))
    with pytest.raises(DomainError, match="NETWORK_TOOL_POLICY_INVALID"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="generating", unit_id="unit", data={}, system="B")
    assert not calls


def test_repeated_failed_target_gets_no_new_http_and_final_turn_keeps_evidence_boundary(client, monkeypatch):
    from app.v2 import generation_tools as g
    rid, epoch, state = running(client)
    turns, reads = [], []
    def invoke(*args, **kw):
        turns.append(deepcopy(kw))
        if len(turns) <= 2:
            return {"tool_requests": [{"name": "read_source", "url": "https://example.org/source",
                "reason": "first gap" if len(turns) == 1 else "same target but a new reason"}]}, {"call_id": str(len(turns))}
        return {"body": "An explicit source limitation, not a fact."}, {"call_id": "last"}
    monkeypatch.setattr(g, "invoke", invoke)
    monkeypatch.setattr(g, "_source_tool", lambda *a: reads.append(a) or {"status": "failed", "reason": "PUBLIC_SOURCE_HTTP_UNAVAILABLE"})
    g.generation_with_sources(rid, "source-fixture", epoch, state, stage="generating", unit_id="unit", data={}, system="B")
    assert len(reads) == 1 and len(turns) == 3
    assert turns[-1]["data"]["remaining_tool_rounds"] == 0
    assert "untrusted evidence" in turns[-1]["system"]
    assert "network_citations" in turns[-1]["system"]


def test_reason_change_does_not_bypass_successful_tool_cache(client, monkeypatch):
    from app.v2 import generation_tools as g
    rid, epoch, _ = running(client)
    reads = []
    monkeypatch.setattr(g, "_source_tool", lambda *a: reads.append(a) or {"status": "ok", "text": "public evidence"})
    first = {"name": "read_source", "url": "https://example.org/source", "reason": "gap A"}
    g._tool_result(rid, "source-fixture", epoch, first, {}, None)
    cached = g._tool_result(rid, "source-fixture", epoch, first | {"reason": "gap B"}, {}, None)
    assert len(reads) == 1 and cached["cache"] == "exact_successful_tool_request"


def test_duplicate_source_receipts_and_multiline_answer_are_bound_as_actual_text():
    from app.v2.generation_tools import bind_citations
    from app.v2.contracts import digest
    source = {"source_id": "source", "source_hash": "a" * 64, "text": "source support"}
    receipt = {"result": source, "result_hash": digest(source)}
    output = {"items": [{"stem_markdown": "Question", "answer": {"final": "Answer", "reasoning": "first line\nsecond line"}}],
        "network_citations": [{"source_id": "source", "source_quote": "source support", "body_quote": "first line\nsecond line"}]}
    assert bind_citations(output, [receipt, receipt | {"cache": "exact_successful_tool_request"}])[0]["status"] == "literal_bound"


def test_A_citations_bind_to_the_actual_arrangement_not_arbitrary_tool_metadata():
    from app.v2.generation_tools import bind_citations
    output = {"teaching_arrangement": {"generation_prompt": "Contrast explanations before transfer."},
        "network_citations": [{"source_id": "source", "source_quote": "read source text", "body_quote": "Contrast explanations before transfer."}]}
    receipts = [{"result": {"source_id": "source", "source_hash": "a" * 64, "text": "read source text"}}]
    assert bind_citations(output, receipts)[0]["status"] == "literal_bound"


def test_same_round_reason_changes_cannot_duplicate_a_target(client, monkeypatch):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    request = {"name": "read_source", "url": "https://example.org/source"}
    monkeypatch.setattr(g, "invoke", lambda *a, **kw: ({"tool_requests": [request, request | {"reason": "another reason"}]}, {}))
    monkeypatch.setattr(g, "_source_tool", lambda *a: pytest.fail("Duplicate rejected before HTTP"))
    with pytest.raises(DomainError, match="GENERATION_TOOL_REQUEST_DUPLICATE"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="planning", unit_id="unit", data={}, system="A")


@pytest.mark.parametrize("fault", ["result", "request", "failed_cache"])
def test_cache_tampering_cannot_become_evidence(client, monkeypatch, fault):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError, digest
    from app.v2.runtime import job, write, load, dump
    rid, epoch, _ = running(client)
    request = {"name": "read_source", "url": "https://example.org/source"}
    monkeypatch.setattr(g, "_source_tool", lambda *a: {"status": "ok", "text": "public evidence"})
    g._tool_result(rid, "source-fixture", epoch, request, {}, None)
    with write() as db:
        obj = job(db, rid)
        state = load(obj.state_json)
        cached = next(iter(state["network_tool_cache"].values()))
        if fault == "result": cached["result"]["text"] = "different text"
        if fault == "request": cached["request"]["url"] += "/different"
        if fault == "failed_cache":
            cached["result"]["status"] = "failed"
            cached["result_hash"] = digest(cached["result"])
        obj.state_json = dump(state)
    monkeypatch.setattr(g, "_source_tool", lambda *a: pytest.fail("Invalid cache is not silently reread"))
    with pytest.raises(DomainError, match="NETWORK_TOOL_CACHE_INVALID"):
        g._tool_result(rid, "source-fixture", epoch, request, {}, None)


@pytest.mark.parametrize("rounds", [0, 1])
def test_tool_request_after_frozen_budget_stops_instead_of_an_extra_read(client, monkeypatch, rounds):
    from app.v2 import generation_tools as g
    from app.v2.contracts import DomainError
    rid, epoch, state = running(client)
    state["execution_policy"]["network_tool_rounds"] = rounds
    calls, reads = [], []
    def invoke(*args, **kw):
        calls.append(kw)
        return {"tool_requests": [{"name": "read_source", "url": "https://example.org/source"}]}, {}
    monkeypatch.setattr(g, "invoke", invoke)
    monkeypatch.setattr(g, "_source_tool", lambda *a: reads.append(a) or {"status": "ok", "text": "public"})
    with pytest.raises(DomainError, match="GENERATION_TOOL_LIMIT_OR_OUTPUT_INVALID"):
        g.generation_with_sources(rid, "source-fixture", epoch, state, stage="planning", unit_id="unit", data={}, system="A")
    assert len(reads) == rounds and len(calls) == rounds + 1


@pytest.mark.parametrize("fault", ["hash", "text"])
def test_same_source_id_conflicting_versions_cannot_literal_bind(fault):
    from app.v2.generation_tools import bind_citations
    first = {"source_id": "s", "source_hash": "a"*64, "text": "public evidence"}
    other = first | ({"source_hash": "b"*64} if fault == "hash" else {"text": "different text"})
    output = {"body": "actual candidate", "network_citations": [{"source_id": "s",
        "source_quote": "public evidence", "body_quote": "actual candidate"}]}
    assert bind_citations(output, [{"result": first}, {"result": other}])[0]["status"] == "unsupported"


def test_encoded_credential_key_rejected_but_https_not_a_local_drive():
    from app.v2.generation_tools import _validated_request
    from app.v2.contracts import DomainError
    assert _validated_request({"name": "read_source", "url": "https://example.org/source"}, {})["url"].startswith("https://")
    with pytest.raises(DomainError, match="PRIVATE_DATA"):
        _validated_request({"name": "read_source", "url": "https://example.org/?%2561ccess_token=secret"}, {})

"""No real model/network/database: malformed citations get explicit correction."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError
from app.v2 import rubric_research as research


def fixture_source():
    return {"source_id": "public-short-id", "source_hash": "a"*64,
            "text": "A unique educational evidence sentence.", "access_level": "abstract"}


def candidate(source_id="public-short-id", quote="A unique educational evidence sentence."):
    return {"claims": [{"claim": "An observable construct, not a calibrated threshold.",
                        "source_id": source_id, "quote": quote}]}


def bind(value, source=None):
    source = source or fixture_source()
    return research.bind_segment_evidence("run", "worker", 0, {}, "evidence-0", value, source, source)


def test_valid_and_irrelevant_segments_do_not_make_extra_calls(monkeypatch):
    monkeypatch.setattr(research, "_call", lambda *a, **kw: pytest.fail("unnecessary paid correction"))
    bound, audits = bind(candidate())
    assert len(bound) == 1 and audits == []
    assert bind({"claims": []}) == ([], [])


@pytest.mark.parametrize("fault", ["identity", "quote", "partial", "schema"])
def test_new_request_corrects_identity_or_literal_evidence_without_mutating_original(monkeypatch, fault):
    original = candidate("public-"+"a"*64) if fault == "identity" else candidate(quote="unseen text")
    if fault == "partial":
        original["claims"] += candidate()["claims"]
    if fault == "schema":
        original = {"claims": "not a list"}
    before = deepcopy(original)
    requests = []
    def call(*args, **kw):
        requests.append(args)
        return candidate(), {"call_id": "correction", "request_hash": "new-request"}
    monkeypatch.setattr(research, "_call", call)
    bound, audits = bind(original)
    assert original == before
    assert len(requests) == 1 and requests[0][4] == "evidence-0_binding_retry_1"
    assert requests[0][5]["previous_output"] == original
    assert requests[0][5]["validation_errors"]
    assert bound[0]["source_id"] == fixture_source()["source_id"]
    assert audits[0]["call_id"] == "correction"


@pytest.mark.parametrize("correction", [candidate("still-wrong"), {"claims": []}, candidate(quote="unseen text")])
def test_two_failed_corrections_stop_without_aliasing_or_false_success(monkeypatch, correction):
    calls = []
    def call(*args, **kw):
        calls.append(args)
        return deepcopy(correction), {"call_id": str(len(calls))}
    monkeypatch.setattr(research, "_call", call)
    with pytest.raises(DomainError, match="RESEARCH_CLAIMS_WITHOUT_LITERAL_EVIDENCE"):
        bind(candidate("wrong"))
    assert len(calls) == 2
    assert calls[0][4] != calls[1][4]


def test_non_unique_quote_is_not_accepted_or_normalized(monkeypatch):
    source = fixture_source()
    source["text"] *= 2
    monkeypatch.setattr(research, "_call", lambda *a, **kw: (candidate(), {}))
    with pytest.raises(DomainError, match="RESEARCH_CLAIMS_WITHOUT_LITERAL_EVIDENCE"):
        bind(candidate(), source)

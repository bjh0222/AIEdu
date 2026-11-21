"""Knowledge extraction must select displayed source, never hidden reasoning."""
import pytest
from app.v2.contracts import DomainError, sha
from app.v2.insights import card_source_spans, resolve_card_spans


def test_displayed_unicode_spans_roundtrip_and_exclude_hidden_reasoning():
    body = "## 来源\n\n解析：矩阵的行变换。\n含 $\\alpha$。\n\n末段。"
    unit = {"unit_id": "one", "body": body, "content_hash": sha(body),
            "items": [{"answer": {"reasoning": "Hidden unrendered argument"}}]}
    spans = card_source_spans(unit)
    assert len(spans) == 3
    assert all(body.encode()[s["start_byte"]:s["end_byte"]].decode() == s["text"] for s in spans)
    assert all("Hidden" not in s["text"] for s in spans)
    candidate = {"unit_id": "one", "source_span_id": spans[1]["source_span_id"], "kind": "method"}
    assert resolve_card_spans(unit, [candidate], spans)[0]["quote"] == spans[1]["text"]
    for invalid in ({**candidate, "source_span_id": "invented"},
                    {**candidate, "unit_id": "foreign"},
                    {**candidate, "quote": "Model paraphrase"}):
        with pytest.raises(DomainError, match="CARD_SOURCE_BINDING_INVALID"):
            resolve_card_spans(unit, [invalid], spans)


def test_source_span_ids_expire_when_body_changes():
    one = {"unit_id": "same", "body": "一段来源。", "content_hash": sha("一段来源。")}
    two = {**one, "body": "来源已修改。", "content_hash": sha("来源已修改。")}
    old = card_source_spans(one)[0]
    with pytest.raises(DomainError, match="CARD_SOURCE_BINDING_INVALID"):
        resolve_card_spans(two, [{"unit_id": "same", "source_span_id": old["source_span_id"]}], card_source_spans(two))

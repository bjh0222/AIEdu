"""Public network boundary: mocks are safety tests, not research evidence."""
import json

import httpx
import pytest


@pytest.mark.parametrize("url", ["http://example.org", "https://localhost/a", "https://127.0.0.1/a",
    "https://169.254.169.254/a", "https://[::1]/a", "https://10.0.0.1/a", "https://a:b@example.org",
    "https://example.org:8000", "https://example.org/#x", "https://example.org\\@evil.org"])
def test_private_or_ambiguous_url_is_rejected(url):
    from app.v2.public_sources import validate_url
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError):
        validate_url(url, resolve=False)


def test_every_dns_address_and_redirect_are_checked(client, monkeypatch):
    from app.v2 import public_sources as p
    from app.v2.contracts import DomainError
    monkeypatch.setattr(p.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443)), (2, 1, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(DomainError, match="PRIVATE_ADDRESS"):
        p.validate_url("https://example.org")
    seen = []
    def transport(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://127.0.0.1/private"})
    with pytest.raises(DomainError, match="PRIVATE_ADDRESS"):
        p.read_source("https://example.org/public", resolve=False, transport=httpx.MockTransport(transport))
    assert seen == ["https://example.org/public"]


def test_read_is_hash_bound_sanitized_and_raw_preserved(client):
    from app.v2 import public_sources as p
    from app.v2.storage import BlobStore
    from app.v2.contracts import sha
    raw = '<h1>研究</h1><script>secret()</script><iframe>bad</iframe><p>observed evidence</p>'.encode()
    result = p.read_source("https://example.org/a", resolve=False, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=raw, headers={"Content-Type": "text/html; charset=utf-8"})))
    assert result["status"] == "ok" and "secret" not in result["text"] and "bad" not in result["text"]
    assert result["source_hash"] == sha(raw)
    assert BlobStore().get(result["source_blob_hash"]) == raw
    for span in result["spans"]:
        assert result["text"].encode()[span["start_byte"]:span["end_byte"]].decode() == span["text"]


@pytest.mark.parametrize("headers,body,code", [
    ({"Content-Type": "text/plain", "Content-Length": "2000001"}, b"x", "SIZE_LIMIT"),
    ({"Content-Type": "text/plain; charset=utf-8"}, b"\xff", "ENCODING_INVALID"),
])
def test_resource_and_encoding_limits(client, headers, body, code):
    from app.v2 import public_sources as p
    from app.v2.contracts import DomainError
    with pytest.raises(DomainError, match=code):
        p.read_source("https://example.org", resolve=False, transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=body, headers=headers)))


def test_search_metadata_not_fulltext_and_rate_limit(client):
    from app.v2 import public_sources as p
    document = {"message": {"items": [{"DOI": "10.1/x", "title": ["A title"], "type": "journal-article"}]}}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=document))
    result = p.search_sources("assessment validity", resolve=False, transport=transport)
    assert result["candidates"][0]["doi"] == "10.1/x"
    assert result["api_source"]["access_level"] == "metadata_api"
    assert result["api_source"]["source_blob_hash"]
    failed = p.search_sources("assessment validity", resolve=False, transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    assert failed["status"] == "failed" and failed["candidates"] == []


def test_citation_literal_support_is_not_semantic_proof():
    from app.v2.generation_tools import bind_citations
    receipts = [{"result": {"source_id": "s", "text": "source literal", "source_hash": "a"*64}}]
    valid = {"body": "candidate claim", "network_citations": [{"source_id": "s", "source_quote": "source literal", "body_quote": "candidate claim"}]}
    assert bind_citations(valid, receipts)[0]["status"] == "literal_bound"
    valid["network_citations"][0]["source_quote"] = "invented"
    assert bind_citations(valid, receipts)[0]["status"] == "unsupported"

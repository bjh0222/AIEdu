"""Bounded opt-in source retrieval. Retrieval is never professional fact checking."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

import httpx

from .contracts import DomainError, canonical, digest, sha
from .storage import BlobStore

HOSTS = frozenset({"www.nextgenscience.org", "apcentral.collegeboard.org", "apstudents.collegeboard.org",
    "www.aera.net", "aclanthology.org", "csed.acm.org", "pubs.rsc.org", "reactflow.dev", "www.w3.org"})
SEARCH_INDEXES = {
    "crossref": "https://api.crossref.org/works",
    "openalex": "https://api.openalex.org/works",
}
MAX_SOURCE_BYTES = 2_000_000
MAX_REDIRECTS = 5


def discovered_proxy():
    # Respect environment proxies, then the current enabled Windows system proxy.
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        if os.getenv(key):
            value = os.environ[key]
            value = value if "://" in value else "http://" + value
            if urlsplit(value).scheme in {"http", "https", "socks5", "socks5h"}:
                return value
    if os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as key:
                if winreg.QueryValueEx(key, "ProxyEnable")[0]:
                    value = winreg.QueryValueEx(key, "ProxyServer")[0]
                    if "=" in value:
                        values = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
                        value = values.get("https") or values.get("http")
                    if value:
                        return value if "://" in value else "http://" + value
        except OSError:
            pass
    return None


def capabilities():
    proxy = discovered_proxy()
    parsed = urlsplit(proxy) if proxy else None
    return {"method": "retrieved-text-quote.v1", "status": "available" if proxy else "not_run",
        "reason": "proxy_discovered_not_connectivity_verified" if proxy else "SYSTEM_PROXY_UNAVAILABLE",
        "hosts": sorted(HOSTS), "proxy": {"detected": bool(proxy), "scheme": parsed.scheme if parsed else None,
            "host": parsed.hostname if parsed else None, "port": parsed.port if parsed else None},
        "scope": "literal attributed text only; does not establish truth, absence, learning gain or professional quality"}


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(); self.skip = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}: self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style"}: self.skip = max(0, self.skip - 1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def verify_quote(url, quote, expected_source_hash=None, *, transport=None):
    target = urlsplit(url)
    if (target.scheme != "https" or target.hostname not in HOSTS or target.username or target.password
            or target.port not in {None, 443} or target.query or target.fragment or len(url) > 2048):
        raise DomainError("VERIFIER_URL_NOT_ALLOWED", 422)
    if not isinstance(quote, str) or not 1 <= len(quote) <= 4000:
        raise DomainError("VERIFIER_QUOTE_INVALID", 422)
    result = {"method_version": "retrieved-text-quote.v1", "url": url, "quote_hash": sha(quote),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "professional_correctness": "not_run",
        "scope": "literal attributed text only, not truth or absence", "result": "not_run", "http_status": None}
    proxy = discovered_proxy()
    if not proxy:
        return {**result, "reason": "SYSTEM_PROXY_UNAVAILABLE"}
    # Use the same DNS-pinned, per-hop validated, bounded reader as B/research.
    # Import here avoids a module cycle through discovered_proxy.
    from .public_sources import read_source
    try:
        source = read_source(url, transport=transport, resolve=transport is None)
    except DomainError as exc:
        return {**result, "result": "undecidable", "reason": exc.code}
    if source["status"] != "ok":
        return {**result, "reason": source.get("reason"), "http_status": source.get("http_status")}
    source_hash = source["source_hash"]
    if expected_source_hash and source_hash != expected_source_hash:
        return {**result, "result": "undecidable", "source_hash": source_hash, "reason": "SOURCE_HASH_CHANGED"}
    normal = lambda value: re.sub(r"\s+", " ", value).strip()
    found = normal(quote) in normal(source["text"])
    answer = {**result, "http_status": 200, "source_hash": source_hash, "source_blob_hash": source["source_blob_hash"],
        "access_level": source["access_level"], "result": "satisfied" if found else "undecidable",
        "reason": "QUOTE_PRESENT" if found else "QUOTE_NOT_LOCATED_NOT_PROOF_OF_ABSENCE",
        "retrieval_method": "public-source-pinned.v1", "hops": source["hops"]}
    return {**answer, "evidence_hash": digest(answer)}

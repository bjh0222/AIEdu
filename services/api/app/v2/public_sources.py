"""Bounded public HTTPS discovery/read adapters; no credentials or private text.

DNS is validated before every redirect hop; private, loopback, link-local and
non-global endpoints are rejected. Only declarative public GET requests exist.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import ipaddress
import json
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlencode

import httpx

from .contracts import DomainError, digest, sha
from .network_evidence import discovered_proxy

MAX_BYTES = 2_000_000


class PinnedTLS(ssl.SSLContext):
    """Retain certificate hostname verification when CONNECT uses a pinned IP.

    httpcore's HTTP proxy does not honor its sni_hostname extension. Override
    only the hostname passed into the standard verified TLS handshake; chain
    and hostname validation both remain enabled. No connection is kept alive.
    """
    def __new__(cls):
        return super().__new__(cls, ssl.PROTOCOL_TLS_CLIENT)

    def __init__(self):
        self.load_default_certs()
        self.pins = {}

    def wrap_socket(self, *args, server_hostname=None, **kwargs):
        return super().wrap_socket(*args, server_hostname=self.pins.get(server_hostname, server_hostname), **kwargs)

    def wrap_bio(self, *args, server_hostname=None, **kwargs):
        return super().wrap_bio(*args, server_hostname=self.pins.get(server_hostname, server_hostname), **kwargs)


def public_addresses(host):
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = list(dict.fromkeys(ipaddress.ip_address(row[4][0])
                for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
        except OSError:
            raise DomainError("PUBLIC_SOURCE_DNS_UNAVAILABLE", 422) from None
    if not addresses or any(not address.is_global for address in addresses):
        raise DomainError("PUBLIC_SOURCE_PRIVATE_ADDRESS", 422)
    return addresses


def validate_url(url, *, resolve=True):
    if not isinstance(url, str) or len(url) > 2048 or "\\" in url or any(ord(c) <= 32 for c in url):
        raise DomainError("PUBLIC_SOURCE_URL_INVALID", 422)
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        raise DomainError("PUBLIC_SOURCE_URL_INVALID", 422) from None
    if (parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in {None, 443}
            or parsed.fragment or host.lower() in {"localhost", "metadata.google.internal"}
            or host.lower().endswith((".localhost", ".local", ".internal"))):
        raise DomainError("PUBLIC_SOURCE_URL_NOT_ALLOWED", 422)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not literal.is_global:
        raise DomainError("PUBLIC_SOURCE_PRIVATE_ADDRESS", 422)
    if resolve:
        public_addresses(host)
    return parsed


class PublicText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.skip, self.parts = 0, []
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self.skip += 1
    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object", "svg"}:
            self.skip = max(0, self.skip-1)
    def handle_data(self, value):
        if not self.skip and value.strip():
            self.parts.append(value.strip())


def read_source(url, *, transport=None, resolve=True, json_allowed=False):
    original, hops = url, []
    proxy = discovered_proxy()
    tls = PinnedTLS()
    deadline = time.monotonic()+45
    try:
        with httpx.Client(proxy=proxy if transport is None else None, transport=transport,
                          verify=tls, limits=httpx.Limits(max_keepalive_connections=0),
                          trust_env=False, follow_redirects=False, timeout=httpx.Timeout(25)) as client:
            for _ in range(5):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise DomainError("PUBLIC_SOURCE_TOTAL_TIMEOUT", 422)
                client.timeout = httpx.Timeout(min(25, remaining))
                parsed = validate_url(url, resolve=False)
                headers = {"User-Agent": "AI-Edu-Public-Research/2.0",
                    "Accept": "text/html,text/plain,application/json,application/xml;q=0.9"}
                target, extensions = url, {}
                if resolve:
                    # Pin the checked address for the actual connection (and
                    # proxy CONNECT), closing DNS-rebinding/TOCTOU. TLS still
                    # verifies the original hostname through SNI.
                    addresses = public_addresses(parsed.hostname)
                    if transport is None:
                        target = httpx.URL(url).copy_with(host=str(addresses[0]))
                        headers["Host"] = parsed.hostname
                        extensions["sni_hostname"] = parsed.hostname
                        tls.pins[str(addresses[0])] = parsed.hostname
                client.cookies.clear()
                with client.stream("GET", target, headers=headers, extensions=extensions) as response:
                    hops.append({"url": url, "status": response.status_code})
                    if response.status_code in {301, 302, 303, 307, 308}:
                        target = response.headers.get("location")
                        if not target:
                            raise DomainError("PUBLIC_SOURCE_REDIRECT_INVALID", 422)
                        url = urljoin(url, target)
                        continue
                    if response.status_code != 200:
                        return {"status": "failed", "reason": "PUBLIC_SOURCE_HTTP_UNAVAILABLE", "url": original,
                            "http_status": response.status_code, "hops": hops}
                    media = response.headers.get("content-type", "").split(";")[0].lower().strip()
                    allowed = {"text/html", "text/plain", "application/xml", "text/xml", "application/xhtml+xml"}
                    if json_allowed:
                        allowed.add("application/json")
                    if media not in allowed:
                        return {"status": "failed", "reason": "PUBLIC_SOURCE_MEDIA_UNSUPPORTED", "url": original, "media_type": media}
                    if response.headers.get("content-length", "0").isdigit() and int(response.headers.get("content-length", "0")) > MAX_BYTES:
                        raise DomainError("PUBLIC_SOURCE_SIZE_LIMIT", 422)
                    raw, size = [], 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise DomainError("PUBLIC_SOURCE_TOTAL_TIMEOUT", 422)
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise DomainError("PUBLIC_SOURCE_SIZE_LIMIT", 422)
                        raw.append(chunk)
                    raw = b"".join(raw)
                    try:
                        text = raw.decode(response.encoding or "utf-8", errors="strict")
                    except (UnicodeError, LookupError):
                        raise DomainError("PUBLIC_SOURCE_ENCODING_INVALID", 422) from None
                    if media in {"text/html", "application/xhtml+xml"}:
                        parser = PublicText()
                        parser.feed(text)
                        text = "\n".join(parser.parts)
                    source_id = "public-"+sha(raw)[:24]
                    spans, offset, char_offset = [], 0, 0
                    for line in text.splitlines(keepends=True):
                        raw_line = line.encode()
                        if line.strip():
                            spans.append({"span_id": f"{source_id}:{offset}", "start_byte": offset,
                                "end_byte": offset+len(raw_line), "sha256": sha(raw_line),
                                "start": char_offset, "end": char_offset+len(line), "text": line})
                        offset += len(raw_line)
                        char_offset += len(line)
                    from .storage import BlobStore
                    result = {"status": "ok", "source_id": source_id, "url": original, "final_url": url,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(), "media_type": media,
                        "source_hash": sha(raw), "source_blob_hash": BlobStore().put(raw),
                        "text_hash": sha(text), "text": text, "spans": spans,
                        "access_level": "selected_html_or_text_page" if media != "application/json" else "metadata_api",
                        "hops": hops, "byte_count": size, "professional_truth": "not_verified"}
                    result["span_coordinate_system"] = "utf8_normalized_extracted_text_not_original_html"
                    result["evidence_hash"] = digest(result)
                    return result
            raise DomainError("PUBLIC_SOURCE_REDIRECT_LIMIT", 422)
    except httpx.HTTPError as exc:
        return {"status": "failed", "reason": "PUBLIC_SOURCE_TRANSPORT_UNAVAILABLE", "failure_class": type(exc).__name__,
            "url": original, "hops": hops}


def search_sources(query, index="crossref", *, transport=None, resolve=True, limit=3):
    if not isinstance(query, str) or not 2 <= len(query) <= 180 or any(ord(c) < 32 for c in query):
        raise DomainError("PUBLIC_SOURCE_QUERY_INVALID", 422)
    if re.search(r"https?://|[\w.+-]+@[\w.-]+\.[a-z]{2,}|\bsk-[\w-]+|Bearer\s", query, re.I):
        raise DomainError("PUBLIC_SOURCE_QUERY_PRIVATE_DATA", 422)
    if not isinstance(limit, int) or not 1 <= limit <= 10:
        raise DomainError("PUBLIC_SOURCE_LIMIT_INVALID", 422)
    if index == "crossref":
        url = "https://api.crossref.org/works?"+urlencode({"query": query, "rows": limit})
    elif index == "openalex":
        url = "https://api.openalex.org/works?"+urlencode({"search": query, "per-page": limit})
    elif index == "europepmc":
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"+urlencode({"query": query,
            "format": "json", "pageSize": limit, "resultType": "core"})
    else:
        raise DomainError("PUBLIC_SOURCE_INDEX_UNSUPPORTED", 422)
    response = read_source(url, transport=transport, resolve=resolve, json_allowed=True)
    if response["status"] != "ok":
        return {**response, "index": index, "query": query, "candidates": []}
    try:
        document = json.loads(response["text"])
        rows = (document["message"]["items"] if index == "crossref" else
            document["resultList"]["result"] if index == "europepmc" else document["results"])
        candidates = []
        for row in rows:
            if index == "crossref":
                title = (row.get("title") or [""])[0]
                locations = [r.get("URL") for r in row.get("link", []) if r.get("URL")]
                candidate = {"candidate_id": row.get("DOI") or row.get("URL"), "doi": row.get("DOI"), "title": title,
                    "authors": row.get("author", []), "year": row.get("published", {}).get("date-parts", [[None]])[0][0],
                    "publisher": row.get("publisher"), "publication_type": row.get("type"),
                    "url": row.get("URL"), "open_urls": locations, "abstract": row.get("abstract"), "index": index}
            elif index == "openalex":
                locations = row.get("locations") or []
                primary = row.get("primary_location") or {}
                candidate = {"candidate_id": row.get("id"), "doi": row.get("doi"), "title": row.get("display_name"),
                    "authors": [r.get("author", {}).get("display_name") for r in row.get("authorships", [])],
                    "year": row.get("publication_year"), "publisher": (primary.get("source") or {}).get("display_name"),
                    "publication_type": row.get("type"), "url": primary.get("landing_page_url") or row.get("doi"),
                    "open_urls": [u for loc in locations for u in (loc.get("landing_page_url"), loc.get("pdf_url")) if u],
                    "abstract_inverted_index": row.get("abstract_inverted_index"), "index": index}
            else:
                pmcid = row.get("pmcid")
                candidate = {"candidate_id": "europepmc:"+row.get("id", ""), "doi": row.get("doi"),
                    "title": row.get("title"), "authors": row.get("authorList", {}), "year": row.get("pubYear"),
                    "publisher": row.get("journalInfo", {}).get("journal", {}).get("title"),
                    "publication_type": row.get("pubTypeList", {}), "index": index,
                    "url": "https://europepmc.org/article/"+row.get("source", "MED")+"/"+row.get("id", ""),
                    "open_urls": (["https://www.ebi.ac.uk/europepmc/webservices/rest/"+pmcid+"/fullTextXML"] if pmcid else []),
                    "abstract": row.get("abstractText"), "fulltext_access": row.get("isOpenAccess")}
            candidate["metadata_hash"] = digest(candidate)
            candidate["candidate_id"] = candidate.get("candidate_id") or "metadata:"+candidate["metadata_hash"][:24]
            if candidate.get("doi"):
                candidate["doi"] = candidate["doi"].strip().lower().removeprefix("https://doi.org/")
            candidates.append(candidate)
        return {"status": "ok", "index": index, "query": query, "candidates": candidates,
            "api_source": {k: response[k] for k in ("url", "retrieved_at", "source_hash", "source_blob_hash", "access_level", "evidence_hash")}}
    except (ValueError, KeyError, TypeError, IndexError):
        return {"status": "failed", "reason": "PUBLIC_SOURCE_METADATA_INVALID", "index": index, "query": query, "candidates": []}

"""Bounded streaming ingestion and UTF-8 byte-exact coverage, never host paths."""
from __future__ import annotations

import codecs
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .contracts import DomainError, digest, sha
from .storage import BlobStore

_parser_slot = threading.BoundedSemaphore(1)


def validate_policy(policy):
    required = ("disk_reserve_bytes", "temp_quota_bytes", "extractor_memory_bytes", "extractor_timeout_seconds",
                "max_extract_workers", "max_expanded_bytes", "max_archive_entries", "ingress_body_policy",
                "cancel_grace_seconds", "policy_version", "chunk_bytes")
    missing = [name for name in required if not policy or policy.get(name) is None]
    if missing:
        raise DomainError("RESOURCE_POLICY_UNKNOWN", 422, missing=missing)
    numeric = set(required) - {"ingress_body_policy", "policy_version"}
    invalid = [k for k in numeric if type(policy[k]) not in {int, float} or not math.isfinite(policy[k]) or policy[k] < 0
               or (k != "disk_reserve_bytes" and policy[k] == 0)]
    if (invalid or policy["max_extract_workers"] != 1 or policy["chunk_bytes"] < 4
            or policy["ingress_body_policy"] != "stream"):
        raise DomainError("RESOURCE_POLICY_INVALID", 422, invalid=invalid)


@contextmanager
def parser_slot(policy):
    validate_policy(policy)
    if not _parser_slot.acquire(blocking=False):
        raise DomainError("PARSER_BACKPRESSURE", 429)
    try:
        yield
    finally:
        _parser_slot.release()


def resource_policy():
    import json
    from ..config import settings
    path = settings.private_root / "resource-policy.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def chunk_text(text: str, source_id: str, revision: int = 1, max_bytes: int = 4096) -> list[dict]:
    if max_bytes < 4:
        raise DomainError("CHUNK_CAPACITY_INVALID", 422)
    raw = text.encode("utf-8")
    from .format_runtime import chapter_ranges
    chapters = chapter_ranges(text)
    boundaries = sorted({len(raw), *(c["start_byte"] for c in chapters), *(c["end_byte"] for c in chapters)})
    chunks, start = [], 0
    while start < len(raw):
        end = min(start + max_bytes, next(b for b in boundaries if b > start))
        while end < len(raw) and raw[end] & 0xC0 == 0x80:
            end -= 1
        fragment = raw[start:end]
        chunks.append({"chunk_id": digest([source_id, revision, start, end, sha(fragment)]),
                       "source_id": source_id, "revision": revision, "sha256": sha(fragment),
                       "start_byte": start, "end_byte": end, "source_locator": f"utf8:{start}:{end}",
                       "split_reason": "call_capacity", "required": True, "assigned_unit_ids": [],
                       "status": "unassigned"})
        chapter = next((c for c in chapters if c["start_byte"] <= start < c["end_byte"]), None)
        if chapter:
            chunks[-1].update(chapter_title=chapter["chapter_title"], section_titles=chapter["section_titles"])
        start = end
    return chunks


def verify_coverage(text: str, chunks: list[dict]) -> dict:
    raw = text.encode("utf-8")
    expected_start, errors, seen = 0, [], set()
    for chunk in sorted(chunks, key=lambda c: c["start_byte"]):
        a, b = chunk["start_byte"], chunk["end_byte"]
        if chunk["chunk_id"] in seen or a != expected_start or not 0 <= a < b <= len(raw):
            errors.append("duplicate_overlap_or_gap")
        seen.add(chunk["chunk_id"])
        try:
            fragment = raw[a:b]
            fragment.decode("utf-8", errors="strict")
            if sha(fragment) != chunk["sha256"]:
                errors.append("hash_mismatch")
        except UnicodeDecodeError:
            errors.append("utf8_boundary")
        expected_start = b
    if expected_start != len(raw) or (raw and not chunks):
        errors.append("incomplete")
    return {"complete": not errors, "errors": sorted(set(errors)), "selected_bytes": len(raw),
            "chunks": len(chunks), "coverage_hash": digest(chunks)}


def ingest(stream, policy: dict | None, cancel=lambda: False, blob_store=None, filename="material.txt") -> dict:
    with parser_slot(policy):
        return _ingest(stream, policy, cancel, blob_store, filename)


def _ingest(stream, policy, cancel=lambda: False, blob_store=None, filename="material.txt"):
    """Spool raw bytes; parse document containers in a limited child process.

    Policy is operator-owned, not accepted from a public request. No 20 MB product cap.
    """
    validate_policy(policy)
    store = blob_store or BlobStore()
    store.root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(store.root).free < policy["disk_reserve_bytes"] + policy["temp_quota_bytes"]:
        raise DomainError("DISK_LOW", 422)
    started, count, raw_hash = time.monotonic(), 0, hashlib.sha256()
    with tempfile.TemporaryFile(dir=store.root) as temporary:
        while True:
            if cancel():
                raise DomainError("CANCELLED", 409)
            if time.monotonic() - started > policy["extractor_timeout_seconds"]:
                raise DomainError("EXTRACTOR_TIMEOUT", 422)
            piece = stream.read(64 * 1024)
            if not piece:
                break
            count += len(piece)
            if count > min(policy["temp_quota_bytes"], policy["max_expanded_bytes"], policy["extractor_memory_bytes"] // 8):
                raise DomainError("RESOURCE_EXHAUSTED", 422)
            raw_hash.update(piece)
            temporary.write(piece)
        temporary.seek(0)
        raw = temporary.read()
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md", ".csv", ".markdown"}:
        try:
            text = raw.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            raise DomainError("ENCODING_UNKNOWN", 422) from exc
        parsed = {"segments": [{"locator": "text:full", "text": text}], "extractor_version": "utf8-text.v2",
                  "memory_limiter": "bounded_plain_text_decode"}
    elif suffix in {".pdf", ".docx", ".epub"}:
        parsed = extract_document(raw, suffix, policy, store.root, cancel)
        text = parsed["text"]
    else:
        raise DomainError("UNSUPPORTED_MATERIAL_TYPE", 422)
    if not text.strip():
        raise DomainError("EMPTY_MATERIAL", 422)
    source_id = str(uuid4())
    chunks = chunk_text(text, source_id, max_bytes=policy["chunk_bytes"])
    segments, position = [], 0
    for segment in parsed["segments"]:
        length = len(segment["text"].encode())
        segments.append({"source_locator": segment["locator"], "start_byte": position, "end_byte": position+length,
                         "sha256": sha(segment["text"])})
        position += length + 2
    for chunk in chunks:
        chunk["original_locators"] = [s["source_locator"] for s in segments if s["start_byte"] < chunk["end_byte"] and s["end_byte"] > chunk["start_byte"]]
    return {"source_id": source_id, "revision": 1, "raw_hash": store.put(raw),
            "extracted_hash": store.put(text.encode()), "extractor_version": parsed["extractor_version"],
            "encoding": "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8", "permissions": "owner",
            "extraction_status": "complete", "size": count, "chunks": chunks,
            "source_segments": segments, "memory_limiter": parsed["memory_limiter"],
            "coverage": verify_coverage(text, chunks), "policy_version": policy["policy_version"]}


def extract_document(raw, suffix, policy, root, cancel=lambda: False):
    # TemporaryDirectory is owned by this request; no user-supplied filesystem path.
    with tempfile.TemporaryDirectory(prefix="extract-", dir=root) as owned:
        path = Path(owned) / ("input"+suffix)
        path.write_bytes(raw)
        runner = Path(__file__).with_name("extractor_runner.py")
        env = {k:v for k,v in os.environ.items() if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}}
        started = time.monotonic()
        with tempfile.TemporaryFile(dir=owned) as stdout:
            process = subprocess.Popen([sys.executable, "-I", str(runner), str(path), suffix, json.dumps(policy)],
                stdout=stdout, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                while process.poll() is None:
                    if cancel():
                        raise DomainError("CANCELLED", 409)
                    if time.monotonic()-started > policy["extractor_timeout_seconds"]:
                        raise DomainError("EXTRACTOR_TIMEOUT", 422)
                    if stdout.seek(0, 2) > min(policy["temp_quota_bytes"], policy["extractor_memory_bytes"]//2):
                        raise DomainError("EXTRACTION_EXPANSION_LIMIT", 422)
                    time.sleep(0.02)
                if process.returncode:
                    raise DomainError("EXTRACTOR_PROCESS_FAILED", 422)
                stdout.seek(0)
                data = stdout.read(min(policy["temp_quota_bytes"], policy["extractor_memory_bytes"]//2)+1)
                if len(data) > min(policy["temp_quota_bytes"], policy["extractor_memory_bytes"]//2):
                    raise DomainError("EXTRACTION_EXPANSION_LIMIT", 422)
                try:
                    result = json.loads(data)
                except ValueError:
                    raise DomainError("EXTRACTOR_RESULT_INVALID", 422) from None
                if not result.get("ok"):
                    raise DomainError(result.get("code", "EXTRACTION_FAILED"), 422)
                return result
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=max(1, policy["cancel_grace_seconds"]))


async def ingest_request(request, policy, blob_store=None, filename="material.txt"):
    with parser_slot(policy):
        return await _ingest_request(request, policy, blob_store, filename)


async def _ingest_request(request, policy, blob_store=None, filename="material.txt"):
    if not policy:
        raise DomainError("RESOURCE_POLICY_UNKNOWN", 422)
    store = blob_store or BlobStore()
    store.root.mkdir(parents=True, exist_ok=True)
    # Operator-configured reserve includes simultaneous staging and immutable raw/extracted blobs.
    validate_policy(policy)
    started, size = time.monotonic(), 0
    limit = min(policy["temp_quota_bytes"] // 3, policy["max_expanded_bytes"], policy["extractor_memory_bytes"] // 8)
    if shutil.disk_usage(store.root).free < policy["disk_reserve_bytes"] + policy["temp_quota_bytes"]:
        raise DomainError("DISK_LOW", 422)
    with tempfile.TemporaryFile(dir=store.root) as temporary:
        import anyio
        try:
            # The deadline covers waiting for the next body chunk as well as
            # receiving it. A stalled sender cannot hold the sole parser slot.
            with anyio.fail_after(policy["extractor_timeout_seconds"]):
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > limit:
                        raise DomainError("RESOURCE_EXHAUSTED", 422)
                    temporary.write(chunk)
        except TimeoutError:
            raise DomainError("EXTRACTOR_TIMEOUT", 422) from None
        temporary.seek(0)
        # Parsing is blocking CPU work, never block the ASGI event loop or pause endpoint.
        return await anyio.to_thread.run_sync(lambda: _ingest(temporary, policy, blob_store=store, filename=filename))

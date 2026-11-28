"""Validated full-text material evidence for C->B->C repair loops.

The helper never trusts a path, filename, excerpt or count supplied by a model.
It binds the complete extracted UTF-8 source, its original container hash, and
the frozen plan's exact chunk identities. Missing/corrupt/unmapped evidence is
rejected before any model dispatch. The package is evidence for generation and
review; it is not registered as a candidate document that can independently
prove a learner-facing claim.
"""
from __future__ import annotations

import json

from .contracts import DomainError, digest, sha

SCHEMA_VERSION = "aiedu.material-evidence.v1"


def _goal_id_for_state(db, state, supplied=None):
    if supplied:
        return supplied
    if state.get("goal_id"):
        return state["goal_id"]
    artifact_id = state.get("artifact_id")
    if not artifact_id:
        raise DomainError("MATERIAL_EVIDENCE_TASK_UNBOUND", 422)
    from ..models import Tutorial
    tutorial = db.get(Tutorial, artifact_id)
    if tutorial is None:
        raise DomainError("MATERIAL_EVIDENCE_TASK_UNBOUND", 422)
    return tutorial.goal_id


def _manifest(material):
    from .runtime import load
    try:
        value = load(material.manifest_json)
    except (TypeError, ValueError) as exc:
        raise DomainError("MATERIAL_EVIDENCE_MANIFEST_INVALID", 422, source_id=material.id) from exc
    if not isinstance(value, dict):
        raise DomainError("MATERIAL_EVIDENCE_MANIFEST_INVALID", 422, source_id=material.id)
    return value


def _chunk_identity(source_id, chunk, text):
    raw = text.encode("utf-8")
    required = {"chunk_id", "source_id", "revision", "sha256", "start_byte", "end_byte", "source_locator"}
    if not isinstance(chunk, dict) or not required <= set(chunk):
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id)
    if chunk.get("source_id") != source_id:
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="source_id")
    if type(chunk.get("revision")) is not int or type(chunk.get("start_byte")) is not int or type(chunk.get("end_byte")) is not int:
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="offsets")
    start, end = chunk["start_byte"], chunk["end_byte"]
    if not 0 <= start < end <= len(raw):
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="bounds")
    fragment = raw[start:end]
    try:
        fragment.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="utf8") from exc
    if chunk.get("sha256") != sha(fragment):
        raise DomainError("MATERIAL_EVIDENCE_HASH_MISMATCH", 422, source_id=source_id, field="chunk_sha256")
    expected_id = digest([source_id, chunk["revision"], start, end, sha(fragment)])
    if chunk.get("chunk_id") != expected_id:
        raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="chunk_id")
    return expected_id


def selected_material_evidence(state, *, db=None, goal_id=None, store=None):
    """Return a complete, hash-bound package for every selected source.

    ``None`` is returned for a source-free task so historical request identities
    and exact data/system/entries are unchanged. For a sourced task every error
    is fail-closed before the caller invokes a model.
    """
    context = state.get("context", {}) if isinstance(state, dict) else {}
    ids = list(context.get("material_ids", []) or [])
    if not ids:
        return None
    if any(not isinstance(source_id, str) or not source_id for source_id in ids):
        raise DomainError("MATERIAL_EVIDENCE_UNBOUND", 422)

    from ..db import SessionLocal
    from ..models import GoalMaterial
    from .materials import verify_coverage
    from .runtime import load
    from .storage import BlobStore

    owns_db = db is None
    if owns_db:
        db = SessionLocal()
    try:
        expected_goal = _goal_id_for_state(db, state, goal_id)
        blob = store or BlobStore()
        sources = []
        all_chunks = {}
        for source_id in ids:
            material = db.get(GoalMaterial, source_id)
            if material is None or material.goal_id != expected_goal:
                raise DomainError("MATERIAL_EVIDENCE_UNBOUND", 422, source_id=source_id)
            if material.status != "ready" or material.extract_status != "ok":
                raise DomainError("MATERIAL_INCOMPLETE", 422, source_id=source_id)
            extracted_hash = material.extracted_hash
            if not isinstance(extracted_hash, str) or len(extracted_hash) != 64:
                raise DomainError("MATERIAL_EVIDENCE_HASH_MISMATCH", 422, source_id=source_id, field="extracted_hash")
            manifest = _manifest(material)
            if manifest.get("source_id") != source_id or manifest.get("extracted_hash") != extracted_hash:
                raise DomainError("MATERIAL_EVIDENCE_UNBOUND", 422, source_id=source_id, field="manifest")
            raw_hash = manifest.get("raw_hash")
            if not isinstance(raw_hash, str) or len(raw_hash) != 64:
                raise DomainError("MATERIAL_EVIDENCE_HASH_MISMATCH", 422, source_id=source_id, field="raw_hash")
            extracted = blob.get(extracted_hash)
            if sha(extracted) != extracted_hash:
                raise DomainError("MATERIAL_EVIDENCE_HASH_MISMATCH", 422, source_id=source_id, field="extracted_blob")
            try:
                text = extracted.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="utf8") from exc
            raw = blob.get(raw_hash)
            if sha(raw) != raw_hash:
                raise DomainError("MATERIAL_EVIDENCE_HASH_MISMATCH", 422, source_id=source_id, field="raw_blob")
            chunks = manifest.get("chunks")
            if not isinstance(chunks, list) or (text and not chunks):
                raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="chunks")
            revision = manifest.get("revision")
            if type(revision) is not int or revision < 1:
                raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="revision")
            seen = set()
            for chunk in chunks:
                if not isinstance(chunk, dict) or chunk.get("revision") != revision:
                    raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="revision")
                chunk_id = _chunk_identity(source_id, chunk, text)
                if chunk_id in seen or chunk_id in all_chunks:
                    raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id, field="duplicate_chunk")
                seen.add(chunk_id)
                all_chunks[chunk_id] = source_id
            coverage = verify_coverage(text, chunks)
            if not coverage.get("complete"):
                raise DomainError("MATERIAL_EVIDENCE_CHUNK_INVALID", 422, source_id=source_id,
                                  field="coverage", errors=coverage.get("errors", []))
            source = {
                "source_id": source_id,
                "source_hash": extracted_hash,
                "raw_hash": raw_hash,
                "revision": revision,
                "manifest_hash": digest(manifest),
                "text": text,
                "text_bytes": len(extracted),
                "chunks": chunks,
                "coverage": coverage,
            }
            # Preserve the exact manifest metadata needed to identify the
            # source format without exposing host paths or raw container bytes.
            for key in ("extractor_version", "encoding", "policy_version", "source_segments"):
                if key in manifest:
                    source[key] = manifest[key]
            sources.append(source)

        plan_units = state.get("plan", {}).get("units", []) if isinstance(state, dict) else []
        if not isinstance(plan_units, list) or not plan_units:
            raise DomainError("MATERIAL_EVIDENCE_PLAN_UNBOUND", 422)
        mappings = []
        mapped = set()
        for unit in plan_units:
            if not isinstance(unit, dict) or not unit.get("unit_id"):
                raise DomainError("MATERIAL_EVIDENCE_PLAN_UNBOUND", 422)
            chunk_ids = unit.get("material_chunk_ids", [])
            if chunk_ids is None:
                chunk_ids = []
            if not isinstance(chunk_ids, list) or any(not isinstance(c, str) for c in chunk_ids):
                raise DomainError("MATERIAL_EVIDENCE_PLAN_COVERAGE", 422, unit_id=unit.get("unit_id"))
            if any(c not in all_chunks for c in chunk_ids):
                raise DomainError("MATERIAL_EVIDENCE_PLAN_COVERAGE", 422, unit_id=unit.get("unit_id"))
            if not chunk_ids and unit.get("artifact_role") != "book_level":
                raise DomainError("MATERIAL_EVIDENCE_PLAN_COVERAGE", 422, unit_id=unit.get("unit_id"),
                                  field="missing_unit_mapping")
            mapped.update(chunk_ids)
            mappings.append({"unit_id": unit["unit_id"], "material_chunk_ids": list(chunk_ids),
                             "source_ids": sorted({all_chunks[c] for c in chunk_ids})})
        out_of_scope = sorted(set(all_chunks) - mapped)
        package = {
            "schema_version": SCHEMA_VERSION,
            "plan_hash": state.get("plan", {}).get("plan_hash"),
            "context_hash": context.get("context_hash"),
            "sources": sources,
            "plan_bindings": mappings,
            "coverage": {
                "complete": True,
                "source_count": len(sources),
                "chunk_count": len(all_chunks),
                "plan_mapped_chunk_count": len(mapped),
                "out_of_scope_chunk_count": len(out_of_scope),
                "out_of_scope_chunk_ids": out_of_scope,
            },
        }
        package["package_hash"] = digest(package)
        return package
    finally:
        if owns_db:
            db.close()

from uuid import uuid4

import pytest

from app.db import SessionLocal
from app.models import GoalMaterial, LearningGoal
from app.v2.contracts import DomainError, sha
from app.v2.materials import chunk_text, verify_coverage
from app.v2.runtime import dump
from app.v2.storage import BlobStore


def _material(db, goal_id, text, *, max_bytes=24):
    from app.v2.contracts import digest
    source_id = str(uuid4())
    store = BlobStore()
    raw = (b"raw:" + text.encode("utf-8"))
    raw_hash = store.put(raw)
    extracted_hash = store.put(text.encode("utf-8"))
    chunks = chunk_text(text, source_id, max_bytes=max_bytes)
    manifest = {"source_id": source_id, "revision": 1, "raw_hash": raw_hash,
                "extracted_hash": extracted_hash, "chunks": chunks,
                "coverage": verify_coverage(text, chunks)}
    db.add(GoalMaterial(id=source_id, goal_id=goal_id, filename="source.md", ext=".md",
                        status="ready", extract_status="ok", raw_hash=raw_hash,
                        extracted_hash=extracted_hash, manifest_json=dump(manifest)))
    return source_id, chunks


def test_complete_multi_source_text_and_frozen_chunk_mapping(client):
    from app.v2.material_evidence import selected_material_evidence

    goal_id, source_a, source_b = str(uuid4()), str(uuid4()), str(uuid4())
    text_a = "Determinant definitions and conditions. " * 4
    text_b = "Thermodynamic state functions and path dependence. " * 4
    with SessionLocal() as db:
        db.add(LearningGoal(id=goal_id, title="multi-source"))
        db.commit()
        a_id, a_chunks = _material(db, goal_id, text_a)
        b_id, b_chunks = _material(db, goal_id, text_b)
        db.commit()
    state = {"context": {"material_ids": [a_id, b_id], "context_hash": "context"},
             "plan": {"plan_hash": "plan", "units": [
                 {"unit_id": "a", "artifact_role": "chapter", "material_chunk_ids": [c["chunk_id"] for c in a_chunks]},
                 {"unit_id": "b", "artifact_role": "chapter", "material_chunk_ids": [c["chunk_id"] for c in b_chunks]},
             ]}}
    package = selected_material_evidence(state, goal_id=goal_id)
    assert [s["source_id"] for s in package["sources"]] == [a_id, b_id]
    assert [s["text"] for s in package["sources"]] == [text_a, text_b]
    assert package["sources"][0]["raw_hash"] == sha(b"raw:" + text_a.encode("utf-8"))
    assert package["coverage"]["complete"] is True
    assert package["coverage"]["plan_mapped_chunk_count"] == len(a_chunks) + len(b_chunks)
    assert package["package_hash"] == __import__("app.v2.contracts", fromlist=["digest"]).digest(
        {k: v for k, v in package.items() if k != "package_hash"})


def test_source_free_task_keeps_legacy_payload_unchanged(client):
    from app.v2.material_evidence import selected_material_evidence
    assert selected_material_evidence({"context": {"material_ids": []}, "plan": {"units": []}}) is None


def test_plan_must_bind_every_frozen_chunk(client):
    from app.v2.material_evidence import selected_material_evidence

    goal_id = str(uuid4())
    with SessionLocal() as db:
        db.add(LearningGoal(id=goal_id, title="coverage"))
        db.commit()
        source_id, chunks = _material(db, goal_id, "A" * 80)
        db.commit()
    state = {"context": {"material_ids": [source_id], "context_hash": "context"},
             "plan": {"plan_hash": "plan", "units": [
                 {"unit_id": "only", "artifact_role": "chapter", "material_chunk_ids": [chunks[0]["chunk_id"]]}
             ]}}
    with pytest.raises(DomainError, match="MATERIAL_EVIDENCE_PLAN_COVERAGE"):
        selected_material_evidence(state, goal_id=goal_id)


def test_missing_blob_is_rejected_before_dispatch(client):
    from app.v2.material_evidence import selected_material_evidence

    goal_id = str(uuid4())
    with SessionLocal() as db:
        db.add(LearningGoal(id=goal_id, title="missing blob"))
        db.commit()
        source_id, chunks = _material(db, goal_id, "B" * 40)
        db.commit()
    from app.models import GoalMaterial as GM
    with SessionLocal() as db:
        material = db.get(GM, source_id)
        material.extracted_hash = "0" * 64
        material.manifest_json = dump({"source_id": source_id, "revision": 1, "raw_hash": "0" * 64,
                                       "extracted_hash": "0" * 64, "chunks": chunks,
                                       "coverage": {"complete": True}})
        db.commit()
    state = {"context": {"material_ids": [source_id], "context_hash": "context"},
             "plan": {"plan_hash": "plan", "units": [
                 {"unit_id": "only", "artifact_role": "chapter", "material_chunk_ids": [chunks[0]["chunk_id"]]}
             ]}}
    with pytest.raises(DomainError, match="BLOB_MISSING"):
        selected_material_evidence(state, goal_id=goal_id)


def test_a_and_c_skills_are_versioned_and_executable():
    from app.v2.planning import skill
    from app.v2.skills import review_skill
    a = skill()
    c = review_skill()
    assert a["version"] == 6 and "quality_target_refs" in a["instructions"]
    assert c["skill_id"] == "assessment-review" and c["version"] == 3
    assert "material_evidence" in c["instructions"]
    assert "专业" in c["instructions"]
    assert "非语义呈现顺序" in c["instructions"]

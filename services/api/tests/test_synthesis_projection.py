"""Projection only: isolated immutable records, zero paid calls and no wiring."""
from copy import deepcopy

import pytest

from app.v2 import synthesis, synthesis_projection
from app.v2.contracts import DomainError, digest
from test_synthesis_execution import diagnostics, c_output, durable_fixture


@pytest.fixture
def source(client):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.insights import save
    snap, run_id, state, bundle, _ = durable_fixture(client)
    def invoke(*args, **kw):
        if kw["role"] == "generation":
            return {"source_bundle_hash": bundle["bundle_hash"], "title": "跨章联系",
                "blocks": [{"block_id": "b1", "heading": "方法条件", "body": "模型生成的有界整合内容。",
                    "source_span_ids": [s["source_span_id"] for s in bundle["source_spans"]]}],
                "relations": [], "unintegrated_units": [], "limitations": ["尚无专业校准。"]}, {"call_id": "B"}
        return c_output(kw["data"]), {"call_id": "C"}
    result = synthesis.run_loop(run_id, "w", 1, state, bundle, max_repairs=0, progress=None,
        checkpoint=lambda _: None, invoke_call=invoke, research_check=lambda *a: diagnostics())
    with SessionLocal() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        result.update(task_id=snap["task_id"], run_id=run_id, revision=1, source_quality_hash=version.quality_hash,
            source_demo=True, demo=True, provenance="model_synthesis_of_demo_source")
        result_hash = digest(result)
        save(db, "projection-fixture", result)
        save(db, "synthesis-head:" + version.id, {"revision": 1, "result_hash": result_hash, "record_key": "projection-fixture"})
        db.commit()
    return snap, result, {"revision": 1, "result_hash": result_hash}


def test_fixed_draft_projects_all_blocks_and_resolvable_sources_without_promotion(source):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, result, selected = source
    before = deepcopy(result)
    with SessionLocal() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        projected = synthesis_projection.attachment(db, version, selected, delivery_kind="available_draft")
    assert result == before
    assert "模型生成的有界整合内容。" in projected["body"] and "未专业校准" in projected["body"]
    assert projected["binding"]["proposal_hash"] == result["latest_proposal_hash"]
    index = projected["binding"]["source_index"]
    assert index["spans"] and all("text" not in row for row in index["spans"])
    assert projected["binding"]["demo"] is True


@pytest.mark.parametrize("selection", [None, {}, {"revision": True, "result_hash": "x"},
    {"revision": 0, "result_hash": "x"}, {"revision": 1, "result_hash": "x", "latest": True}])
def test_export_never_implicitly_selects_mutable_latest(source, selection):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, _, _ = source
    with SessionLocal() as db, pytest.raises(DomainError, match="SYNTHESIS_EXPORT_SELECTION_INVALID"):
        synthesis_projection.attachment(db, db.get(TutorialVersion, snap["version_id"]), selection, delivery_kind="available_draft")


@pytest.mark.parametrize("mutation", [{"revision": 2}, {"result_hash": "0" * 64}])
def test_old_export_selection_requires_explicit_refresh(source, mutation):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, _, selected = source
    with SessionLocal() as db, pytest.raises(DomainError, match="SYNTHESIS_EXPORT_SELECTION_STALE"):
        synthesis_projection.attachment(db, db.get(TutorialVersion, snap["version_id"]), selected | mutation,
            delivery_kind="available_draft")


def test_automatic_success_does_not_unlock_final_synthesis_export(source):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, result, selected = source
    assert result["automatic_status"] == "satisfied"
    with SessionLocal() as db, pytest.raises(DomainError, match="SYNTHESIS_FINAL_NOT_CALIBRATED"):
        synthesis_projection.attachment(db, db.get(TutorialVersion, snap["version_id"]), selected, delivery_kind="complete")


def test_source_quality_hash_drift_is_not_ignored(source):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, _, selected = source
    with SessionLocal() as db, pytest.raises(DomainError, match="SYNTHESIS_RECORD_STALE"):
        version = db.get(TutorialVersion, snap["version_id"])
        version.quality_hash = "0" * 64
        synthesis_projection.attachment(db, version, selected, delivery_kind="available_draft")

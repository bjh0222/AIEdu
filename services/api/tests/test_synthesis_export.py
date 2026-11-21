"""Actual merged export boundary; isolated records, no paid inference."""
import io
import json
import zipfile

from test_synthesis_projection import source
from test_task_run_v2 import owner, post


def export(client, snap, selected, key="fixed-synthesis-export", **extra):
    return post(client, f"/artifacts/{snap['artifact_id']}/export-sets", {
        "expected_revision": snap["state_revision"], "version_id": snap["version_id"],
        "kind": "available_draft", "options": {"synthesis": selected}, **extra,
    }, owner(client), key)


def test_fixed_synthesis_is_inside_single_main_document_with_hashes_and_sources(client, source):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.contracts import digest, sha
    from app.v2.runtime import materialize
    from app.v2.exports import safe_markdown
    snap, result, selected = source
    response = export(client, snap, selected)
    assert response.status_code == 201, response.text
    manifest = response.json()
    assert manifest["renderer_version"] == "markdown.safe.merged.v9"
    assert manifest["synthesis_binding"]["result_hash"] == selected["result_hash"]
    assert manifest["synthesis_binding"]["source_content_hash"] == snap["content_hash"]
    assert manifest["synthesis_binding"]["proposal_hash"] == result["latest_proposal_hash"]
    assert manifest["synthesis_binding"]["calibration_status"] == "not_calibrated"
    body = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").content
    assert "模型生成的有界整合内容。" in body.decode()
    assert "未专业校准" in body.decode() and "交付状态：草稿" in body.decode()
    with SessionLocal() as db:
        units = materialize(db.get(TutorialVersion, snap["version_id"]))["units"]
    assert all(safe_markdown(u["body"], {}, "text")[0] in body.decode() for u in units)
    main = next(f for f in manifest["files"] if f["file_id"] == "main")
    assert main["sha256"] == sha(body) and main["size"] == len(body)
    bundle = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/bundle").content
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert archive.namelist() == ["draft-main.md"]
        assert archive.read("draft-main.md") == body
    assert manifest["bundle_hash"] == digest({k: v for k, v in manifest.items() if k != "bundle_hash"})
    assert manifest["quality_gate"]["passed"] is False


def test_export_selection_never_falls_back_to_latest_and_no_manifest_is_created(client, source):
    from app.db import SessionLocal
    from app.v2.storage import ExportSet
    snap, _, selected = source
    response = export(client, snap, selected | {"revision": 2})
    assert response.status_code == 409 and response.json()["code"] == "SYNTHESIS_EXPORT_SELECTION_STALE"
    with SessionLocal() as db:
        assert db.query(ExportSet).count() == 0


def test_candidate_synthesis_cannot_be_included_in_final_even_if_source_passes(client, source):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    snap, _, selected = source
    with SessionLocal() as db:
        version = db.get(TutorialVersion, snap["version_id"])
        quality = json.loads(version.quality_report)
        quality.update(tqi_verdict="pass", quality_status="passed", calibration_status="calibrated", blocking_reasons=[])
        version.quality_report = json.dumps(quality)
        db.commit()
    response = export(client, snap, selected, kind="complete")
    assert response.status_code == 422 and response.json()["code"] == "SYNTHESIS_FINAL_NOT_CALIBRATED"


def test_head_change_during_render_is_rejected_atomically(client, source, monkeypatch):
    from app.db import SessionLocal
    from app.v2.insights import save
    from app.v2.storage import BlobStore, ExportSet
    snap, _, selected = source
    original = BlobStore.put
    changed = False
    def put(store, body):
        nonlocal changed
        if not changed:
            changed = True
            with SessionLocal() as db:
                save(db, "synthesis-head:" + snap["version_id"], {"revision": 2, "result_hash": "0" * 64})
                db.commit()
        return original(store, body)
    monkeypatch.setattr(BlobStore, "put", put)
    response = export(client, snap, selected)
    assert response.status_code == 409 and response.json()["code"] == "SYNTHESIS_EXPORT_SELECTION_STALE"
    with SessionLocal() as db:
        assert db.query(ExportSet).count() == 0


def test_replay_keeps_previously_bound_bytes_after_head_changes(client, source):
    from app.db import SessionLocal
    from app.v2.insights import save
    snap, _, selected = source
    first = export(client, snap, selected)
    assert first.status_code == 201, first.text
    manifest = first.json()
    url = f"/api/v2/export-sets/{manifest['bundle_id']}/files/main"
    original = client.get(url).content
    with SessionLocal() as db:
        save(db, "synthesis-head:" + snap["version_id"], {"revision": 2, "result_hash": "0" * 64})
        db.commit()
    assert export(client, snap, selected).json() == manifest
    assert client.get(url).content == original
    stale = export(client, snap, selected, key="new-export-with-stale-selection")
    assert stale.status_code == 409

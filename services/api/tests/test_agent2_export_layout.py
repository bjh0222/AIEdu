"""Merged export layout and role-projection compatibility, offline only."""
from __future__ import annotations

import io
import json
import zipfile
from uuid import uuid4

from test_task_run_v2 import owner, post
from test_task_run_v2_guards import queued
from delivery_fixtures import install_delivery_certificate


def pass_quality(state):
    return {
        "tqi_verdict": "pass", "quality_status": "passed", "calibration_status": "calibrated",
        "blocking_reasons": [], "findings": [],
        "checks": [
            {"item_id": item["item_id"], "result": "satisfied"}
            for item in state["check_plan"]["check_items"]
        ],
    }


def prepare_pass_state(run_id, units):
    from app.v2.runtime import dump, job, load, write
    with write() as db:
        obj = job(db, run_id)
        state = install_delivery_certificate(load(obj.state_json), units)
        obj.state_json = dump(state)
        obj.state_revision += 1
    return pass_quality(state)


def publish_pass(client, units):
    from app.v2.runtime import claim_root, publish
    from app.v2.tasks import snapshot
    created, run = queued(client)
    lease = claim_root(run["run_id"], "worker")
    quality = prepare_pass_state(run["run_id"], units)
    version_id = publish(run["run_id"], "worker", lease["epoch"], units, quality | {"_version_id": None}, "agent2-export")
    snap = snapshot(created["task_id"])
    return created, snap


def export_complete(client, snap, key="agent2-complete"):
    headers = owner(client)
    result = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                  {"expected_revision": snap["state_revision"], "version_id": snap["version_id"], "kind": "complete"},
                  headers, f"{key}-{uuid4()}")
    assert result.status_code == 201, result.text
    return result.json()


def test_final_export_merges_units_without_chapter_files(client):
    from app.v2.contracts import digest, sha
    from app.v2.storage import BlobStore
    first = "## 一、第一章\n\n第1章正文。"
    second = "## 二、第二章\n\n第2章正文。"
    units = [
        {"unit_id": "u1", "body": first, "content_hash": sha(first), "artifact_role": "tutorial", "display_order": 1},
        {"unit_id": "u2", "body": second, "content_hash": sha(second), "artifact_role": "tutorial", "display_order": 2},
    ]
    created, snap = publish_pass(client, units)
    manifest = export_complete(client, snap)
    assert manifest["delivery_status"] == "final"
    assert manifest["quality_gate"]["passed"] is True
    assert manifest["quality_gate"]["certificate_required_for_unwaived_claims"] is True
    assert manifest["quality_gate"]["method"] == "delivery-decision.v1"
    assert [(f["file_id"], f["name"]) for f in manifest["files"]] == [("main", "main.md"), ("bundle", "bundle.zip")]
    body_bytes = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").content
    body = body_bytes.decode("utf-8")
    assert first in body and second in body
    assert "交付状态：最终交付" in body and "质量：pass / passed" in body
    main_file = manifest["files"][0]
    assert main_file["size"] == len(body_bytes)
    assert main_file["sha256"] == sha(body_bytes)
    assert len(manifest["legacy_role_projections"]) == 2
    assert all(not p["downloadable"] for p in manifest["legacy_role_projections"])
    bundle = zipfile.ZipFile(io.BytesIO(client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/bundle").content))
    assert bundle.namelist() == ["main.md"]
    without_hash = {key: value for key, value in manifest.items() if key != "bundle_hash"}
    assert manifest["bundle_hash"] == digest(without_hash)


def test_exercise_answers_keep_role_projection_but_not_chapter_files(client):
    from app.v2.contracts import sha
    from app.v2.format_runtime import EXERCISE_LAYOUT
    from app.v2.items import normalize_items, render_unit_items
    from app.v2.storage import BlobStore

    headers = owner(client)
    payload = {"title": "合并导出", "raw_input": "仅根据全部所选材料", "domain_key": "math_logic",
               "requested_artifacts": ["exercise", "answer"],
               "spec_bindings": [{"scene": "exercise", "artifact_role": "exercise",
                                  "snapshot": {"scene": "exercise", "content_structure": "### 一、易错辨析\n### 二、训练\n### 参考答案"}}]}
    created = post(client, "/tasks", payload, headers).json()
    assert post(client, f"/tasks/{created['task_id']}/confirm",
                {"expected_revision": 1, "context_hash": created["context_hash"]}, headers).status_code == 200
    from app.v2.contracts import StartRun
    from app.v2.tasks import start_run
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"]), "agent2-role-worker")
    from app.v2.runtime import claim_root, publish
    from app.v2.tasks import snapshot
    lease = claim_root(run["run_id"], "worker")
    items = normalize_items({"items": [
        {"question_type": "判断题", "stem_markdown": "判断题。", "source_sections": ["习题1"],
         "answer": {"final": "正确", "reasoning": "依据完整。"}},
        {"question_type": "综合题", "stem_markdown": "综合题。", "source_sections": ["习题1"],
         "answer": {"final": "结论", "reasoning": "完整推导。", "display_reasoning": "关键论证。"}},
    ]}, "exercise", "u", EXERCISE_LAYOUT, ["习题1"])
    unit = {"unit_id": "u", "title": "第1章", "section_number": 1, "artifact_role": "exercise",
            "artifact_roles": ["exercise", "answer"], "format_contract": EXERCISE_LAYOUT, "items": items, "display_order": 1}
    body = render_unit_items(items, unit)
    unit["body"] = body
    unit["content_hash"] = sha(body)
    quality = prepare_pass_state(run["run_id"], [unit])
    version_id = publish(run["run_id"], "worker", lease["epoch"], [unit], quality | {"_version_id": None}, "agent2-role")
    snap = snapshot(created["task_id"])
    manifest = export_complete(client, snap, "agent2-role-complete")
    assert [f["file_id"] for f in manifest["files"]] == ["main", "answers", "bundle"]
    roles = {p["artifact_role"]: p for p in manifest["legacy_role_projections"]}
    assert set(roles) == {"exercise", "answer"}
    assert all(not role["downloadable"] for role in roles.values())
    main_body = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").text
    assert main_body.count("### 参考答案") == 1
    answer_bytes = BlobStore().get(roles["answer"]["sha256"])
    answer_text = answer_bytes.decode("utf-8")
    assert "答案：正确" in answer_text and "解析：关键论证。" in answer_text
    assert answer_text != main_body


def test_draft_export_uses_draft_filenames_with_compatible_file_ids(client):
    created, snap = publish_pass(client, [
        {"unit_id": "u1", "body": "## 一、第一章\n\n第1章正文。 [[u2|答案]]", "content_hash": "0", "artifact_role": "tutorial", "display_order": 1},
        {"unit_id": "u2", "body": "参考答案。", "content_hash": "1", "artifact_role": "answer", "display_order": 2},
    ])
    headers = owner(client)
    result = post(client, f"/artifacts/{snap['artifact_id']}/export-sets",
                  {"expected_revision": snap["state_revision"], "version_id": snap["version_id"],
                   "kind": "available_draft"}, headers, f"agent2-draft-{uuid4()}")
    assert result.status_code == 201, result.text
    manifest = result.json()
    assert manifest["delivery_status"] == "draft"
    assert manifest["quality_gate"]["passed"] is False
    assert [(f["file_id"], f["name"]) for f in manifest["files"]] == [
        ("main", "draft-main.md"), ("answers", "draft-answers.md"), ("bundle", "draft-bundle.zip"),
    ]
    body = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").text
    assert "交付状态：草稿" in body and "第1章正文。" in body
    assert "[答案](draft-answers.md)" in body
    bundle = zipfile.ZipFile(io.BytesIO(client.get(
        f"/api/v2/export-sets/{manifest['bundle_id']}/files/bundle").content))
    assert bundle.namelist() == ["draft-main.md", "draft-answers.md"]


def test_chapter_artifact_projects_as_requested_tutorial_instead_of_missing(client):
    from app.v2.contracts import sha

    body = "## 第一章 二阶行列式\n\n教程正文。"
    _, snap = publish_pass(client, [{
        "unit_id": "chapter-1",
        "title": "第一章 二阶行列式",
        "body": body,
        "content_hash": sha(body),
        "artifact_role": "chapter",
        "artifact_roles": ["tutorial"],
        "display_order": 1,
    }])
    manifest = export_complete(client, snap, "chapter-tutorial-projection")
    assert manifest["missing_required"] == []
    assert manifest["delivery_status"] == "final"
    assert {
        row["artifact_role"] for row in manifest["legacy_role_projections"]
    } == {"tutorial"}
    main = client.get(f"/api/v2/export-sets/{manifest['bundle_id']}/files/main").text
    assert "教程正文。" in main

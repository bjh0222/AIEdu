"""Authored subject understanding remains versioned, bound and uncalibrated."""
from uuid import uuid4


def test_catalog_covers_general_and_every_subject_with_distinct_hashes():
    from app.learning.disciplines import SUBJECTS
    from app.v2.understanding_assets import catalog, tutorial_planning

    value = catalog()
    assert value["general"]["kind"] == "general_understanding"
    assert value["general"]["calibration_status"] == "not_calibrated"
    assert len(value["subjects"]) == 21
    assert {item["domain_key"] for item in value["subjects"]} == {key for key, _, _ in SUBJECTS}
    assert len({item["hash"] for item in value["subjects"]}) == 21
    for item in value["subjects"]:
        assert item["content"]["knowledge_model"]
        assert item["content"]["difficulty_model"]
        assert "not_measured" in " ".join(item["content"]["difficulty_model"])
        planning = tutorial_planning(item["domain_key"])
        assert planning["status"] == "authored_uncalibrated"
        assert planning["quantity_policy"]
        assert planning["organization"]


def test_task_freezes_only_its_subjects_plus_general_and_manifest_hashes():
    from app.v2.contracts import TaskInput
    from app.v2.tasks import create_task
    from app.v2.understanding_assets import manifest_entries
    from app.v2.runtime import job, load
    from app.db import SessionLocal

    created = create_task(TaskInput(
        title="跨学科理解绑定",
        raw_input="比较物理模型和历史解释。",
        domain_key="physics_astronomy",
        subject_bindings=[
            {"domain_key": "physics_astronomy", "branch": ""},
            {"domain_key": "history_culture", "branch": ""},
        ],
    ), str(uuid4()))
    with SessionLocal() as db:
        state = load(db.query(__import__("app.models", fromlist=["LearningGoal"]).LearningGoal)
                     .filter_by(id=created["task_id"]).one().task_state_json)
    composition = state["context"]["subject_understanding"]
    assert {item["domain_key"] for item in composition["subjects"]} == {
        "physics_astronomy", "history_culture"}
    assert composition["composition_hash"]
    entries = manifest_entries(state["context"], "A")
    assert len(entries) == 3
    assert {entry["consumer"] for entry in entries} == {"A"}
    assert all(entry["sha256"] and entry["message_position"] == "context.subject_understanding"
               for entry in entries)


def test_tutorial_profile_has_executable_planning_guidance_and_hash():
    from app.v2.profiles import domain_profile

    profile = domain_profile("math_logic")
    guidance = profile["planning_guidance"]
    assert guidance["status"] == "authored_uncalibrated"
    assert guidance["organization"] and guidance["knowledge_chain"]
    assert guidance["question_design"] and guidance["difficulty_policy"]
    assert guidance["hash"]
    assert "规划必须落实" in profile["rules"][0]["criterion"]
    assert profile["rules"][0]["criterion_hash"]


def test_understanding_context_tolerates_null_asset_refs_and_uses_job_time(client):
    from test_task_run_v2 import task_run
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.contracts import digest
    from app.v2.insights import save
    from app.v2.runtime import dump, load

    _, _, snap = task_run(client)
    content = {"material_refs": [], "knowledge": {"structure": "fixture"}}
    value = {"id": "null-assets", "version": 1, "hash": digest(content), "content": content}
    ref = {k: value[k] for k in ("id", "version", "hash")}
    with SessionLocal() as db:
        save(db, "import-profile:null-assets:1", value)
        save(db, "import-profile-head:null-assets", ref)
        job_row = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one()
        state = load(job_row.state_json)
        state["research_result"] = {"asset_refs": None, "profile": {"profile_id": "null-assets"}}
        job_row.state_json = dump(state)
        job_row.updated_at = "2026-11-22T09:30:00+08:00"
        db.commit()
        expected_time = job_row.updated_at
    response = client.get("/api/v2/managed-understanding-context/null-assets?version=1")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated_at"] == expected_time
    assert result["updated_at_source"] == "generation_job"

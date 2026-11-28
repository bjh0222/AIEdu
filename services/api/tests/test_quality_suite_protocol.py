from uuid import uuid4
import hashlib
import json
from pathlib import Path

import pytest

from app.v2.contracts import Confirmation, DomainError, Resume, StartRun, TaskInput, digest
from app.v2.quality_suite import (
    CORRECTNESS_ONLY_V1,
    FULL_V1,
    frozen_quality_profile,
    normalize_quality_profile,
)
from app.v2.tasks import create_task, mutate_input, resume, start_run


def test_profile_default_is_fail_closed_for_legacy_state_and_explicit_for_new_runs(client, monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.runtime import job, load

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    assert frozen_quality_profile({})["id"] == FULL_V1
    assert normalize_quality_profile(CORRECTNESS_ONLY_V1)["id"] == CORRECTNESS_ONLY_V1

    created = create_task(
        TaskInput(
            title="profile",
            raw_input="synthetic",
            execution_policy={
                "quality_layers": {
                    "correctness": True,
                    "completeness": False,
                    "quality_specific": False,
                },
            },
        ),
        str(uuid4()),
    )
    mutate_input(
        created["task_id"],
        Confirmation(expected_revision=1, context_hash=created["context_hash"]),
        str(uuid4()),
        True,
    )
    run = start_run(
        created["task_id"],
        StartRun(
            expected_revision=1,
            confirmed_context_hash=created["context_hash"],
            mode="configured",
        ),
        str(uuid4()),
    )
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    assert state["quality_profile"]["id"] == CORRECTNESS_ONLY_V1
    assert state["execution_policy"]["quality_profile"]["id"] == CORRECTNESS_ONLY_V1
    assert state["check_plan"]["quality_profile"]["id"] == CORRECTNESS_ONLY_V1


def test_unknown_profile_fails_before_a_run_is_created(client, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(
        TaskInput(
            title="profile",
            raw_input="synthetic",
            execution_policy={"quality_profile": "unknown"},
        ),
        str(uuid4()),
    )
    mutate_input(
        created["task_id"],
        Confirmation(expected_revision=1, context_hash=created["context_hash"]),
        str(uuid4()),
        True,
    )
    with pytest.raises(DomainError, match="QUALITY_PROFILE_UNSUPPORTED"):
        start_run(
            created["task_id"],
            StartRun(
                expected_revision=1,
                confirmed_context_hash=created["context_hash"],
                mode="configured",
            ),
            str(uuid4()),
        )


def test_correctness_only_resume_keeps_profile_manifest(client, monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.review_contract import review_protocol_manifest
    from app.v2.runtime import job, load, write

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(
        TaskInput(
            title="resume profile",
            raw_input="synthetic",
            execution_policy={
                "quality_profile": CORRECTNESS_ONLY_V1,
                "quality_layers": {
                    "correctness": True,
                    "completeness": False,
                    "quality_specific": False,
                },
            },
        ),
        str(uuid4()),
    )
    mutate_input(
        created["task_id"],
        Confirmation(expected_revision=1, context_hash=created["context_hash"]),
        str(uuid4()),
        True,
    )
    run = start_run(
        created["task_id"],
        StartRun(
            expected_revision=1,
            confirmed_context_hash=created["context_hash"],
            mode="configured",
        ),
        str(uuid4()),
    )
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        assert state["quality_protocol"] == review_protocol_manifest(
            state["quality_profile"]
        )
        obj.status = "paused"
        obj.state_json = __import__("app.v2.runtime", fromlist=["dump"]).dump(state)
        revision = obj.state_revision
    child = resume(
        run["run_id"],
        Resume(expected_revision=revision, checkpoint_hash=digest(state["plan"])),
        str(uuid4()),
    )
    with SessionLocal() as db:
        child_state = load(job(db, child["run_id"]).state_json)
    assert child_state["quality_profile"]["id"] == CORRECTNESS_ONLY_V1
    assert child_state["quality_protocol"] == state["quality_protocol"]
    assert child_state["execution_policy"]["criterion_assessment"] is False


def test_explicit_full_profile_keeps_legacy_criterion_default(client, monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.v2.runtime import job, load

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(
        TaskInput(
            title="full profile",
            raw_input="synthetic",
            execution_policy={"quality_profile": FULL_V1},
        ),
        str(uuid4()),
    )
    mutate_input(
        created["task_id"],
        Confirmation(expected_revision=1, context_hash=created["context_hash"]),
        str(uuid4()),
        True,
    )
    run = start_run(
        created["task_id"],
        StartRun(
            expected_revision=1,
            confirmed_context_hash=created["context_hash"],
            mode="configured",
        ),
        str(uuid4()),
    )
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    assert state["quality_profile"]["id"] == FULL_V1
    assert state["execution_policy"]["criterion_assessment"] is True


def test_full_v1_archive_matches_frozen_manifest():
    root = Path(__file__).resolve().parents[1] / "quality_suite_archive" / "full_v1"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["archive_id"] == "full_v1"
    assert manifest["source_commit"] == "b22a2bd11da220b7269eff6bec994f939b76e442"
    assert manifest["files"]
    for row in manifest["files"]:
        payload = (root / row["name"]).read_bytes()
        assert len(payload) == row["bytes"]
        assert hashlib.sha256(payload).hexdigest() == row["sha256"]

"""Agent1 lifecycle acceptance. All bytes/configs belong to conftest fixtures."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post, task_run


def make_task(client, headers, title="待整理目标"):
    value = post(client, "/tasks", {"title": title, "raw_input": "仅测试管理，不生成"}, headers)
    assert value.status_code == 201, value.text
    return value.json()["task_id"]


def material(client, headers, raw=b"original \xe5\x8e\x9f\xe6\x96\x87\r\n", goal_id=None):
    from app.db import SessionLocal
    from app.models import GoalMaterial
    from app.v2.storage import BlobStore
    goal_id = goal_id or make_task(client, headers)
    store = BlobStore()
    key = store.put(raw)
    with SessionLocal() as db:
        row = GoalMaterial(goal_id=goal_id, filename="原始资料.md", raw_hash=key, extracted_hash=key, status="ready")
        db.add(row)
        db.commit()
        return row.id, key


def preflight(client, kind, identity):
    response = client.get(f"/api/v2/managed-resources/{kind}/{identity}/impact")
    assert response.status_code == 200, response.text
    impact = response.json()
    return impact, {"kind": kind, "id": identity, "expected_hash": impact["resource"]["resource_hash"], "impact_hash": impact["impact_hash"]}


def change(client, headers, kind, identity, action, **extra):
    impact, target = preflight(client, kind, identity)
    body = {"action": action, "items": [target], "acknowledge_references": True, **extra}
    if action == "purge":
        body.setdefault("confirmation_names", {identity: impact["resource"]["label"]})
    return post(client, "/managed-resource-actions", body, headers), body


def test_material_trash_restore_bytes_and_purge_receipt(client):
    from app.db import SessionLocal
    from app.models import GoalMaterial, PropositionConfig
    from app.v2.contracts import digest
    from app.v2.storage import BlobStore
    headers = owner(client)
    identity, key = material(client, headers)
    before = BlobStore().get(key)
    assert change(client, headers, "materials", identity, "trash")[0].status_code == 200
    assert client.get("/api/v2/managed-resources/materials").json()["total"] == 0
    assert client.get("/api/v2/managed-resources/materials?trashed=true&search=原始").json()["total"] == 1
    assert BlobStore().get(key) == before
    assert change(client, headers, "materials", identity, "restore_trash")[0].status_code == 200
    assert BlobStore().get(key) == before
    assert change(client, headers, "materials", identity, "trash")[0].status_code == 200
    impact, target = preflight(client, "materials", identity)
    body = {"action": "purge", "items": [target], "confirmation_names": {identity: impact["resource"]["label"]}}
    result = post(client, "/managed-resource-actions", body, headers, "purge-material")
    assert result.status_code == 200, result.text
    assert result.json()["source_bytes_deleted"] and not result.json()["cleanup_pending"]
    assert not BlobStore().path(key).exists()
    assert not list(BlobStore().path(key).parent.glob(key+".purge-*"))
    with SessionLocal() as db:
        assert db.get(GoalMaterial, identity) is None
        assert db.get(PropositionConfig, "resource-deletion:"+digest(["materials", identity]))
    assert post(client, "/managed-resource-actions", body, headers, "purge-material").json() == result.json()
    receipt = client.get("/api/v2/actions/receipt", headers={"Idempotency-Key": "purge-material"}).json()
    assert receipt["status"] == "completed"


def test_shared_blob_preserved_and_other_material_untouched(client):
    from app.v2.storage import BlobStore
    headers = owner(client)
    first, key = material(client, headers)
    second, shared = material(client, headers)
    assert shared == key
    assert change(client, headers, "materials", first, "trash")[0].status_code == 200
    assert change(client, headers, "materials", first, "purge")[0].status_code == 200
    assert BlobStore().get(key)
    assert preflight(client, "materials", second)[0]["resource"]["management"]["trashed"] is False


@pytest.mark.parametrize("kind", ["questions", "principles"])
def test_trash_search_uses_visible_source_content_and_preserves_pagination(client, kind):
    from app.db import SessionLocal
    from app.models import PropositionQuestion, DesignPrinciple
    headers = owner(client)
    with SessionLocal() as db:
        row = (PropositionQuestion(raw_stem="解方程 2x+3=7", question_number="第3题") if kind == "questions"
               else DesignPrinciple(statement="核对等式两边的逆运算", domain_key="general"))
        db.add(row); db.commit(); identity = row.id
    assert change(client, headers, kind, identity, "trash")[0].status_code == 200
    query = "第3题" if kind == "questions" else "逆运算"
    result = client.get(f"/api/v2/managed-resources/{kind}", params={"trashed": "true", "search": query, "limit": 1}).json()
    assert result["total"] == 1 and result["items"][0]["id"] == identity
    assert query in result["items"][0]["label"]
    assert client.get(f"/api/v2/managed-resources/{kind}", params={"trashed": "true", "search": query, "offset": 1}).json()["items"] == []


def test_archive_and_trash_are_distinct_and_restore_keeps_original_archive(client):
    headers = owner(client)
    identity = make_task(client, headers)
    assert change(client, headers, "tasks", identity, "archive")[0].status_code == 200
    assert change(client, headers, "tasks", identity, "trash")[0].status_code == 200
    assert client.get("/api/v2/managed-resources/tasks?archived=true").json()["total"] == 0
    assert change(client, headers, "tasks", identity, "restore")[0].status_code == 422
    assert change(client, headers, "tasks", identity, "restore_trash")[0].status_code == 200
    assert client.get("/api/v2/managed-resources/tasks?archived=true").json()["total"] == 1


def test_task_hidden_from_regular_list_but_history_readable(client):
    headers, created, snap = task_run(client)
    identity = created["task_id"]
    assert change(client, headers, "tasks", identity, "trash")[0].status_code == 200
    assert client.get("/api/v2/tasks").json()["items"] == []
    assert client.get(f"/api/v2/tasks/{identity}/snapshot").json() == snap
    assert change(client, headers, "tasks", identity, "purge")[0].status_code == 409


def test_atomic_batch_stale_hash_and_confirmation(client):
    headers = owner(client)
    one, two = make_task(client, headers, "第一"), make_task(client, headers, "第二")
    _, first = preflight(client, "tasks", one)
    _, second = preflight(client, "tasks", two)
    second["expected_hash"] = "outdated"
    bad = {"action": "trash", "items": [first, second]}
    assert post(client, "/managed-resource-actions", bad, headers).status_code == 409
    assert not preflight(client, "tasks", one)[0]["resource"]["management"]["trashed"]
    assert change(client, headers, "tasks", one, "purge")[0].status_code == 409
    assert change(client, headers, "tasks", one, "trash")[0].status_code == 200
    assert change(client, headers, "tasks", one, "purge", confirmation_names={one: "错误名称"})[0].status_code == 422
    assert change(client, headers, "tasks", one, "purge")[0].status_code == 200


@pytest.mark.parametrize("status", ["queued", "running", "sent", "authorized", "unknown"])
def test_active_run_or_unresolved_call_cannot_trash(client, status):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.storage import CallDispatch
    headers, created, snap = task_run(client)
    with SessionLocal() as db:
        job = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one()
        job.status = status if status in {"queued", "running"} else "paused"
        if status not in {"queued", "running"}:
            db.add(CallDispatch(call_id=str(uuid4()), root_job_id=job.root_job_id, run_id=job.run_id,
                unit_id="fixture", epoch=0, attempt=1, idempotency_key=str(uuid4()), request_hash="a"*64,
                input_hash="a"*64, provider="fixture", model_family="fixture", model="fixture", status=status))
        db.commit()
    response, _ = change(client, headers, "tasks", created["task_id"], "trash")
    assert response.status_code == 409, response.text
    assert not preflight(client, "tasks", created["task_id"])[0]["resource"]["management"]["trashed"]


def test_expired_running_lease_is_precise_and_released_on_resource_action(client):
    import time
    from app.db import SessionLocal
    from app.models import GenerationJob
    headers, created, snap = task_run(client)
    with SessionLocal() as db:
        job = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one()
        job.status, job.lease_until, job.worker_instance_id = "running", time.time() - 1, "stale-worker"
        db.commit()
    impact, target = preflight(client, "tasks", created["task_id"])
    assert impact["active"] is False and impact["stale_execution_releasable"] is True
    blocker = impact["active_blockers"][0]
    assert blocker["run_id"] == snap["run_id"] and blocker["stale"] is True
    assert blocker["required_action"] == "automatic_lease_recovery"
    result = post(client, "/managed-resource-actions",
                  {"action": "trash", "items": [target], "acknowledge_references": True}, headers)
    assert result.status_code == 200, result.text
    with SessionLocal() as db:
        job = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one()
        assert job.status == "interrupted"


def test_unreceipted_sent_timeout_reports_exact_call_and_quarantine_unlocks(client):
    import time
    from uuid import uuid4
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.storage import CallDispatch
    headers, created, snap = task_run(client)
    call_id, request_hash = str(uuid4()), "b" * 64
    with SessionLocal() as db:
        job = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one()
        job.status = "failed"
        db.add(CallDispatch(call_id=call_id, root_job_id=job.root_job_id, run_id=job.run_id,
            unit_id="stale-sent", epoch=job.cancel_epoch, attempt=1,
            idempotency_key=str(uuid4()), request_hash=request_hash, input_hash="c" * 64,
            provider="fixture", model_family="fixture", model="fixture",
            status="sent", started_at=time.time() - 700))
        db.commit()
        revision = job.state_revision
        checkpoint = __import__("app.v2.contracts", fromlist=["digest"]).digest(
            __import__("app.v2.runtime", fromlist=["load"]).load(job.state_json)["plan"])
    impact, target = preflight(client, "tasks", created["task_id"])
    assert impact["active"] is True
    blocker = next(item for item in impact["active_blockers"] if item["id"] == call_id)
    assert blocker["state"] == "sent" and blocker["stale"] is True
    assert blocker["required_action"] == "explicit_quarantine"
    blocked = post(client, "/managed-resource-actions",
                   {"action": "trash", "items": [target]}, headers)
    assert blocked.status_code == 409 and blocked.json()["code"] == "RESOURCE_IN_ACTIVE_RUN"
    assert blocked.json()["details"]["blockers"][0]["id"] == call_id
    isolated = post(client, f"/runs/{snap['run_id']}/unknown-quarantines", {
        "expected_revision": revision,
        "checkpoint_hash": checkpoint,
        "calls": [{"call_id": call_id, "request_hash": request_hash}],
        "acknowledge_possible_duplicate_cost": True,
    }, headers, "isolate-stale-resource-call")
    assert isolated.status_code == 200, isolated.text
    unlocked, _ = change(client, headers, "tasks", created["task_id"], "trash")
    assert unlocked.status_code == 200, unlocked.text
    with SessionLocal() as db:
        call = db.get(CallDispatch, call_id)
        assert call.status == "quarantined" and call.outcome == "unknown"
        assert call.receipt_json


def test_concurrent_cas_only_one_writer(client):
    from app.api.v2_management import ManagedAction
    from app.v2.management import change as apply
    from app.v2.contracts import DomainError
    headers = owner(client)
    identity = make_task(client, headers)
    _, target = preflight(client, "tasks", identity)
    payload = ManagedAction(action="trash", items=[target])
    def writer(key):
        try:
            apply(payload, key)
            return "success"
        except DomainError as error:
            return error.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(writer, ["race-1", "race-2"]))
    assert sorted(results) == ["RESOURCE_STALE", "success"]


def test_owning_foreign_keys_and_batch_partial_failure(client):
    from app.db import SessionLocal
    from app.models import PropositionBatch, PropositionQuestion
    headers = owner(client)
    with SessionLocal() as db:
        batch = PropositionBatch(filename="命题.md")
        db.add(batch)
        db.flush()
        question = PropositionQuestion(batch_id=batch.id, raw_stem="第1题", raw_text="第1题")
        db.add(question)
        db.commit()
        identity = batch.id
    assert change(client, headers, "batches", identity, "trash")[0].status_code == 200
    response, _ = change(client, headers, "batches", identity, "purge")
    assert response.status_code == 409
    assert "proposition_questions" in response.text
    assert client.get("/api/v2/knowledge/assets/batches").json()["total"] == 0


@pytest.mark.parametrize("binding", ["active", "role", "none"])
def test_profile_private_lifecycle_and_references(client, caplog, binding):
    from app import model_settings as store
    headers = owner(client)
    secret = "fixture-credential-never-public-9627"
    store.write_config({"active_id": "fixture" if binding == "active" else None,
        "profiles": [{"id": "fixture", "name": "测试模型", "model": "offline", "api_key": secret, "base_url": "https://example.invalid/v1", "timeout": 120}],
        "model_roles": {"review": "fixture"} if binding == "role" else {}})
    before = store.read_config()["profiles"][0]
    impact, _ = preflight(client, "model-profiles", "fixture")
    response, body = change(client, headers, "model-profiles", "fixture", "trash")
    if binding != "none":
        assert response.status_code == 409
        assert store.read_config()["profiles"][0] == before
    else:
        assert response.status_code == 200, response.text
        assert client.get("/api/v2/model-settings").json()["config"]["profiles"] == []
        assert store.read_config()["profiles"][0] == before
        assert change(client, headers, "model-profiles", "fixture", "restore_trash")[0].status_code == 200
        assert store.read_config()["profiles"][0] == before
        assert change(client, headers, "model-profiles", "fixture", "trash")[0].status_code == 200
        assert change(client, headers, "model-profiles", "fixture", "purge")[0].status_code == 200
        assert store.read_config()["profiles"] == []
    assert secret not in str(impact)+response.text+str(body)+caplog.text


def test_auth_csrf_idempotency_and_duplicate_aliases(client):
    assert client.get("/api/v2/managed-resources/tasks").status_code == 401
    headers = owner(client)
    identity = make_task(client, headers)
    _, target = preflight(client, "tasks", identity)
    payload = {"action": "trash", "items": [target]}
    assert post(client, "/managed-resource-actions", payload, {"Origin": headers["Origin"]}).status_code == 403
    result = post(client, "/managed-resource-actions", payload, headers, "same-key")
    assert result.status_code == 200
    assert post(client, "/managed-resource-actions", payload, headers, "same-key").json() == result.json()
    assert post(client, "/managed-resource-actions", payload | {"action": "restore_trash"}, headers, "same-key").status_code == 409
    assert post(client, "/managed-resource-actions", payload | {"items": [target, target]}, headers).status_code == 422


@pytest.mark.parametrize("kind", ["specs", "batches", "questions", "evaluations", "principles", "results"])
def test_other_resource_kinds_delete_only_the_unreferenced_target(client, kind):
    from app.db import SessionLocal
    from app.v2.management import MODELS
    headers = owner(client)
    values = {"name": "自定义规范", "scene": "exercise", "is_builtin": False, "is_default": False} if kind == "specs" else {"goal_id": make_task(client, headers)} if kind == "results" else {}
    with SessionLocal() as db:
        row, survivor = MODELS[kind](**values), MODELS[kind](**values)
        db.add_all([row, survivor]); db.commit(); identity, survivor_id = row.id, survivor.id
    assert change(client, headers, kind, identity, "trash")[0].status_code == 200
    assert change(client, headers, kind, identity, "restore_trash")[0].status_code == 200
    assert change(client, headers, kind, identity, "trash")[0].status_code == 200
    response, _ = change(client, headers, kind, identity, "purge")
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        assert db.get(MODELS[kind], identity) is None
        assert db.get(MODELS[kind], survivor_id) is not None


def test_fixed_version_quality_alias_and_exports_cannot_purge(client):
    headers, created, snap = task_run(client)
    identity = snap["version_id"]
    assert change(client, headers, "versions", identity, "trash")[0].status_code == 200
    assert preflight(client, "quality-reports", identity)[0]["resource"]["management"]["trashed"]
    assert change(client, headers, "quality-reports", identity, "purge")[0].status_code == 409
    assert client.get(f"/api/v2/tasks/{created['task_id']}/snapshot").json() == snap
    assert change(client, headers, "versions", identity, "restore_trash")[0].status_code == 200
    from app.db import SessionLocal
    from app.v2.storage import ExportSet
    with SessionLocal() as db:
        db.add(ExportSet(bundle_id="fixture-download", artifact_id=snap["artifact_id"], version_id=identity,
            kind="draft", state="ready", request_hash="a"*64, action_key="fixture-export",
            options_hash="b"*64, cache_relpath="fixture-download"))
        db.commit()
    assert change(client, headers, "exports", "fixture-download", "trash")[0].status_code == 200
    blocked, _ = change(client, headers, "exports", "fixture-download", "purge")
    assert blocked.status_code == 409 and "IMMUTABLE_EVIDENCE_RETAINED" in blocked.text
    assert change(client, headers, "exports", "fixture-download", "restore_trash")[0].status_code == 200


def test_private_profile_full_content_cas_and_hidden_selection(client):
    from app import model_settings as store
    headers = owner(client)
    config = {"profiles": [{"id": "unused", "name": "完整配置", "model": "offline", "api_key": "fixture-secret", "base_url": "https://example.invalid"}], "active_id": None, "model_roles": {}}
    store.write_config(config)
    _, target = preflight(client, "model-profiles", "unused")
    config = store.read_config(); config["profiles"][0]["api_key"] = "changed-fixture-secret"; store.write_config(config)
    response = post(client, "/managed-resource-actions", {"items": [target], "action": "trash"}, headers)
    assert response.status_code == 409 and response.json()["code"] == "RESOURCE_STALE"
    assert change(client, headers, "model-profiles", "unused", "trash")[0].status_code == 200
    current = client.get("/api/v2/model-settings").json()
    response = client.put("/api/v2/model-settings", json={"expected_config_hash": current["config_hash"], "action": "select", "value": {"profile_id": "unused"}}, headers={**headers, "Idempotency-Key": "hidden-select"})
    assert response.status_code == 409 and response.json()["code"] == "RESOURCE_TRASHED"


def test_file_staging_rollback_and_cleanup_can_finish_with_original_receipt(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import GoalMaterial
    from app.v2 import management_lifecycle as lifecycle
    from app.v2.storage import BlobStore
    headers = owner(client)
    identity, blob = material(client, headers)
    before = BlobStore().get(blob)
    assert change(client, headers, "materials", identity, "trash")[0].status_code == 200
    _, target = preflight(client, "materials", identity)
    original = lifecycle.os.replace
    def fail(source, destination):
        raise OSError("fixture: staging denied")
    monkeypatch.setattr(lifecycle.os, "replace", fail)
    from app.api.v2_management import ManagedAction
    from app.v2.management import change as apply
    body = {"action": "purge", "items": [target], "confirmation_names": {identity: "原始资料.md"}, "acknowledge_references": True}
    with pytest.raises(OSError):
        apply(ManagedAction(**body), "stage-failure")
    with SessionLocal() as db:
        assert db.get(GoalMaterial, identity)
    assert BlobStore().get(blob) == before
    monkeypatch.setattr(lifecycle.os, "replace", original)
    original_unlink = lifecycle.Path.unlink
    def fail_unlink(path, *args, **kwargs):
        if ".purge-" in path.name:
            raise PermissionError("fixture: file locked")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(lifecycle.Path, "unlink", fail_unlink)
    first = apply(ManagedAction(**body), "cleanup-receipt")
    assert first["cleanup_pending"] == [blob] and not first["source_bytes_deleted"]
    with SessionLocal() as db:
        assert db.get(GoalMaterial, identity) is None
    monkeypatch.setattr(lifecycle.Path, "unlink", original_unlink)
    response = post(client, "/managed-resource-cleanup", {}, headers, "cleanup-receipt")
    assert response.status_code == 200, response.text
    second = response.json()
    assert not second["cleanup_pending"] and second["source_bytes_deleted"]
    assert not BlobStore().path(blob).exists()
    assert post(client, "/managed-resource-cleanup", {}, headers, "cleanup-receipt").json() == second
    assert post(client, "/managed-resource-cleanup", {}, headers, "not-a-purge").status_code == 404


def test_understanding_context_real_binding_timestamp_and_staleness(client):
    from app.db import SessionLocal
    from app.models import GenerationJob
    from app.v2.insights import save
    from app.v2.runtime import load, dump
    from app.v2.contracts import digest
    headers, created, snap = task_run(client)
    identity, blob = material(client, headers, goal_id=created["task_id"])
    content = {"material_refs": [{"source_id": identity, "source_hash": blob}], "knowledge": {"structure": "fixture"}}
    value = {"id": "fixture-context", "version": 1, "hash": digest(content), "content": content}
    ref = {k: value[k] for k in ("id", "version", "hash")}
    with SessionLocal() as db:
        save(db, "import-profile:fixture-context:1", value); save(db, "import-profile-head:fixture-context", ref)
        row = db.query(GenerationJob).filter_by(run_id=snap["run_id"]).one(); state = load(row.state_json)
        state["research_result"] = {"asset_refs": {"profile": ref}, "completed_at": "2026-11-16T11:12:13Z"}
        row.state_json = dump(state); db.commit()
    path = "/api/v2/managed-understanding-context/fixture-context?version=1"
    current = client.get(path).json()
    assert current["status"] == "current" and current["updated_at"] == "2026-11-16T11:12:13Z"
    assert current["materials"][0]["filename"] == "原始资料.md" and not current["inference_triggered"]
    assert change(client, headers, "materials", identity, "trash")[0].status_code == 200
    stale = client.get(path).json()
    assert stale["status"] == "stale" and stale["materials"][0]["status"] == "trashed"
    assert change(client, headers, "materials", identity, "purge")[0].status_code == 409


def test_new_bindings_exclude_trashed_specs_and_principles(client):
    from app.db import SessionLocal
    from app.models import FormatSpec, DesignPrinciple, LearningGoal
    from app.v2.runtime import load
    headers = owner(client)
    with SessionLocal() as db:
        spec = FormatSpec(name="回收站规范", scene="reading_guide", is_builtin=False, is_default=False)
        principle = DesignPrinciple(statement="回收站原理", domain_key="general", status="confirmed")
        db.add_all([spec, principle]); db.commit(); spec_id, principle_id = spec.id, principle.id
    assert change(client, headers, "specs", spec_id, "trash")[0].status_code == 200
    assert change(client, headers, "principles", principle_id, "trash")[0].status_code == 200
    response = post(client, "/tasks", {"title": "新任务", "raw_input": "阅读", "spec_bindings": [{"scene": "reading_guide", "artifact_role": "tutorial", "spec_id": spec_id}]}, headers)
    assert response.status_code == 409 and response.json()["code"] == "RESOURCE_TRASHED"
    task_id = make_task(client, headers)
    with SessionLocal() as db:
        sources = load(db.get(LearningGoal, task_id).task_state_json)["context"]["knowledge_sources"]
        assert all(s["id"] != principle_id for s in sources)


def test_unknown_understanding_time_and_missing_material_remain_unknown(client):
    from app.db import SessionLocal
    from app.v2.insights import save
    headers = owner(client)
    with SessionLocal() as db:
        value = {"id": "empty-understanding", "version": 1, "hash": "f"*64, "content": {}}
        save(db, "import-profile:empty-understanding:1", value)
        save(db, "import-profile-head:empty-understanding", {k: value[k] for k in ("id", "version", "hash")})
        db.commit()
    result = client.get("/api/v2/managed-understanding-context/empty-understanding?version=1").json()
    assert result["status"] == "unknown" and result["updated_at"] is None and result["materials"] == []
    assert client.get("/api/v2/managed-understanding-context/absent?version=1").status_code == 404


def test_staged_blob_crash_recovery_does_not_lose_surviving_bytes(client):
    from app.v2.management_lifecycle import stage_blobs, restore_blobs
    from app.v2.storage import BlobStore
    headers = owner(client)
    identity, blob = material(client, headers)
    original = BlobStore().get(blob)
    staged = stage_blobs([blob], "interrupted-before-commit")
    try:
        assert not BlobStore().path(blob).exists()
        assert BlobStore().get(blob) == original
        assert preflight(client, "materials", identity)[0]["resource"]
    finally:
        restore_blobs(staged)
    assert BlobStore().get(blob) == original

"""Public v2 API. One backend reducer; no arbitrary shell/path execution endpoint."""
from __future__ import annotations

import json
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, Response, UploadFile

from ..config import settings
from ..db import SessionLocal
from ..model_settings import configured_concurrency
from ..models import GenerationJob, GoalMaterial, LearningGoal, TutorialVersion
from ..schemas import ChatRequest
from ..v2 import exports, tasks
from ..v2.external_imports import ImportedFile, asset_response, import_markdown
from ..v2.contracts import (Confirmation, DeliveryWaiverRequest, DomainError, ExportRequest, InputPatch, Pause,
                            QuarantineUnknown, RepairRequest, Resume, StartRun, TaskInput, TaskStart,
                            UserReview, digest, sha)
from ..v2.quality import POLICY, SCENES, compile_plan
from ..v2.runtime import cas, dump, job, load, materialize, write
from ..v2.storage import BlobStore
from ..v2.security import action_key, create_session, owner
from ..v2.storage import CallDispatch
from ..v2.responses import DraftSnapshot, RunSnapshot, TaskPage, VersionPage
from ..v2.worker import execute_run

router = APIRouter(prefix="/v2", tags=["task-run.v2"])
secured = APIRouter(dependencies=[Depends(owner)])


@router.post("/session")
def session(request: Request, response: Response):
    return create_session(request, response)


@secured.get("/capabilities")
def capabilities(domain_key: str = "general", scene: str = "knowledge_tutorial", runtime_target: str = ""):
    from ..v2.materials import resource_policy, validate_policy
    try:
        validate_policy(resource_policy())
        material_policy = {"resource_policy_status": "ready", "resource_policy_reason": None}
    except DomainError as exc:
        material_policy = {"resource_policy_status": "unavailable", "resource_policy_reason": exc.code}
    except (OSError, ValueError, TypeError):
        material_policy = {"resource_policy_status": "unavailable", "resource_policy_reason": "RESOURCE_POLICY_INVALID"}
    from ..v2.profiles import coverage_catalog, domain_profile
    from ..v2.model_roles import public_roles, public_workflow_roles
    preview = compile_plan({"domain_key": domain_key, "runtime_target": runtime_target,
                            "spec_bindings": [{"scene": scene, "artifact_role": "tutorial"}]})
    return {"schema_version": "aiedu.task-run.v2", "scenes": sorted(SCENES), "obsidian_delivery": False,
              "paid_calls_enabled": settings.v2_allow_paid, "active_root_limit": 1,
              "provider_concurrency": configured_concurrency(),
            "repair_policy": POLICY, "calibration_status": "not_calibrated", "ps51_sandbox": "not_run",
            "model_roles": ["generation", "blind_solver", "review", "secondary_review", "benchmark_panel", "benchmark_refiner", "chat"],
            "verifiers": {"markdown.v1": "available", "coverage.v1": "available", "ps51_sandbox.v1": "unsupported",
                          "independent_oracle.v1": "not_calibrated", "polynomial-identity.v2":"available_bounded_literal_equations",
                          "verbatim-source.v1":"available_literal_attribution_only",
                          "criterion_review.v1":"available_with_configured_review_role"},
            "production_restore": "not_run", "taxonomy": coverage_catalog(),
            "scope": {"domain_key": domain_key, "scene": scene, "runtime_target": runtime_target},
            "generation": {"available": True, "requires_paid_permit": True, "demo_requires_explicit_choice": True},
            "quality_profile": domain_profile(domain_key, scene), "scoped_check_plan": preview,
            "role_configuration": public_roles(), "workflow_roles": public_workflow_roles(),
            "benchmark_build_status": "scope_specific_see_report",
            "benchmark_capabilities":{"controlled":"three_deterministic_families","semantic":"requires_independent_role_preflight",
                "sealed":"new_build_only_single_consume","calibration":"not_calibrated"},
            "materials":{"supported":["txt","md","csv","pdf_text","docx_text","epub_text"],
                "unsupported":["scanned_pdf_without_ocr","doc","ppt","nontext_office_objects"],"resource_policy_required":True,
                **material_policy}}


@secured.post("/task-starts", status_code=202)
def task_start(payload: TaskStart, background: BackgroundTasks, key=Depends(action_key)):
    result = tasks.task_start(payload, key)
    if result.get("run_id"):
        background.add_task(execute_run, result["run_id"])
    return result


@secured.get("/quality-profiles")
def quality_profiles():
    from ..v2.profiles import coverage_catalog
    return {"items": coverage_catalog(), "calibration_status": "not_calibrated"}


@secured.post("/spec-previews")
def spec_preview(payload: TaskInput, key=Depends(action_key)):
    from ..v2.resolver import resolve_task, spec_entries, bind_specs
    with SessionLocal() as db:
        context = bind_specs(db, resolve_task(payload.model_dump(), payload.model_fields_set))
    return compile_plan(context) | {"resolved_task": context, "field_consumers": spec_entries(context, "generating")}


@secured.post("/tasks", status_code=201)
def create_task(payload: TaskInput, key=Depends(action_key)):
    return tasks.create_task(payload, key)


@secured.get("/tasks", response_model=TaskPage)
def list_tasks(cursor: str | None = None, limit: int = 50, search: str = ""):
    return tasks.list_tasks(cursor, limit, search)


@secured.get("/tasks/{task_id}/versions", response_model=VersionPage)
def list_versions(task_id: str, artifact_id: str, before_version: int | None = None, limit: int = 100):
    return tasks.list_versions(task_id, artifact_id, before_version, limit)


@secured.patch("/tasks/{task_id}/input")
def input_patch(task_id: str, payload: InputPatch, key=Depends(action_key)):
    return tasks.mutate_input(task_id, payload, key)


@secured.post("/tasks/{task_id}/confirm")
def confirm(task_id: str, payload: Confirmation, key=Depends(action_key)):
    return tasks.mutate_input(task_id, payload, key, confirmation=True)


@secured.post("/external-imports", status_code=201)
async def external_import(files: list[UploadFile] = File(...), ai_assist: bool = Form(False), key=Depends(action_key)):
    """Owner Markdown import: deterministic structure, immutable bytes, no model calls."""
    from pathlib import PurePath
    markdown_files: list[UploadFile] = []
    for item in files:
        raw_name = item.filename or ""
        if "/" in raw_name or "\\" in raw_name or any(ord(c) < 32 for c in raw_name):
            raise DomainError("INVALID_EXTERNAL_FILENAME", 422)
        suffix = PurePath(raw_name).suffix.lower()
        if suffix in {".md", ".markdown"}:
            markdown_files.append(item)
            continue
        if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}:
            raise DomainError("UNSUPPORTED_EXTERNAL_ASSET", 422, filename=raw_name)
    if len(markdown_files) != 1:
        raise DomainError("ONE_EXTERNAL_MARKDOWN_REQUIRED", 422, count=len(markdown_files))
    ordered = [markdown_files[0], *[item for item in files if item is not markdown_files[0]]]
    data = []
    for item in ordered:
        raw = await item.read()
        if len(raw) > settings.material_max_bytes:
            raise DomainError("EXTERNAL_IMPORT_TOO_LARGE", 413, limit_bytes=settings.material_max_bytes)
        data.append(ImportedFile(filename=item.filename or "material.md", data=raw))
        await item.close()
    return import_markdown(data[0], data[1:], key, ai_assist=ai_assist)


@secured.get("/external-assets/{blob_hash}")
def external_asset(blob_hash: str):
    import re
    if not re.fullmatch(r"[0-9a-f]{64}", blob_hash):
        raise DomainError("INVALID_BLOB_HASH", 422)
    data = BlobStore().get(blob_hash)
    if data.startswith(b"\x89PNG\r\n\x1a\n"): media_type = "image/png"
    elif data.startswith(b"\xff\xd8\xff"): media_type = "image/jpeg"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"): media_type = "image/gif"
    elif data[:4] in {b"RIFF"} and data[8:12] == b"WEBP": media_type = "image/webp"
    elif data.lstrip()[:5].lower() in {b"<svg ", b"<?xml"}: media_type = "image/svg+xml"
    else: raise DomainError("EXTERNAL_ASSET_TYPE_UNKNOWN", 422)
    return asset_response(data, media_type)


@secured.post("/tasks/{task_id}/materials", status_code=201)
async def upload_material(task_id: str, request: Request, key=Depends(action_key)):
    from pathlib import PurePath
    from urllib.parse import unquote
    from ..v2.materials import ingest_request, resource_policy
    filename = request.headers.get("X-Filename", "material.txt")
    # Browser headers are byte strings; non-Latin filenames use explicit UTF-8 percent encoding.
    if "X-Filename-UTF8" in request.headers:
        try:
            filename = unquote(request.headers["X-Filename-UTF8"], encoding="utf-8", errors="strict")
        except UnicodeError:
            raise DomainError("INVALID_MATERIAL_FILENAME", 422) from None
    if not filename or any(ord(char) < 32 for char in filename):
        raise DomainError("INVALID_MATERIAL_FILENAME", 422)
    if "/" in filename or "\\" in filename or PurePath(filename).suffix.lower() not in {".txt", ".md", ".csv", ".markdown", ".pdf", ".docx", ".epub"}:
        raise DomainError("UNSUPPORTED_MATERIAL_TYPE", 422)
    try:
        revision = int(request.headers["X-Expected-Revision"])
    except (ValueError, KeyError):
        raise DomainError("EXPECTED_REVISION_REQUIRED", 422) from None
    # Fail closed before consuming the request body when capacity has not been measured.
    with SessionLocal() as db:
        before = tasks.goal(db, task_id)
        if before.input_revision != revision:
            raise DomainError("CONTEXT_STALE")
    result = await ingest_request(request, resource_policy(), filename=filename)
    request_hash = digest({"task_id": task_id, "raw_hash": result["raw_hash"], "filename": filename, "revision": revision})
    with write() as db:
        obj = tasks.goal(db, task_id)
        state = load(obj.task_state_json)
        prior = tasks.receipt(state, key, request_hash)
        if prior:
            return prior
        if obj.input_revision != revision:
            raise DomainError("CONTEXT_STALE")
        if db.query(GenerationJob).filter(GenerationJob.goal_id == task_id, GenerationJob.status.in_(["running", "queued"])).first():
            raise DomainError("ACTIVE_RUN")
        db.add(GoalMaterial(id=result["source_id"], goal_id=task_id, filename=filename, size=result["size"],
                           status="ready", extract_status="ok", raw_hash=result["raw_hash"],
                           extracted_hash=result["extracted_hash"], extractor_version=result["extractor_version"], manifest_json=dump(result)))
        tasks.remember(state, key, request_hash, result, "upload_material")
        obj.task_state_json = dump(state)
    return result


@secured.post("/tasks/{task_id}/runs", status_code=202)
def start(task_id: str, payload: StartRun, background: BackgroundTasks, key=Depends(action_key)):
    result = tasks.start_run(task_id, payload, key)
    background.add_task(execute_run, result["run_id"])
    return result


@secured.get("/tasks/{task_id}/snapshot", response_model=RunSnapshot | DraftSnapshot)
def snapshot(task_id: str, request: Request, run_id: str | None = None):
    result = tasks.snapshot(task_id, run_id)
    etag = '"' + digest(result) + '"'
    headers = {"ETag": etag, "Cache-Control": "private,no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=dump(result), headers=headers, media_type="application/json")


@secured.post("/runs/{run_id}/pause")
def pause(run_id: str, payload: Pause, key=Depends(action_key)):
    return tasks.pause(run_id, payload, key)


@secured.post("/runs/{run_id}/unknown-quarantines")
def quarantine_unknown(run_id: str, payload: QuarantineUnknown, key=Depends(action_key)):
    # Deliberately no BackgroundTasks / execute_run: isolation is not a resume.
    return tasks.quarantine_unknown(run_id, payload, key)


@secured.post("/runs/{run_id}/resume", status_code=202)
def resume(run_id: str, payload: Resume, background: BackgroundTasks, key=Depends(action_key)):
    result = tasks.resume(run_id, payload, key)
    background.add_task(execute_run, result["run_id"])
    return result


@secured.post("/runs/{run_id}/delivery-waivers")
def delivery_waiver(run_id: str, payload: DeliveryWaiverRequest, key=Depends(action_key)):
    return tasks.delivery_waiver(run_id, payload, key)


@secured.get("/artifacts/{artifact_id}/versions/{version_id}")
def read_version(artifact_id: str, version_id: str):
    with SessionLocal() as db:
        version = db.get(TutorialVersion, version_id)
        if version is None or version.tutorial_id != artifact_id:
            raise DomainError("VERSION_NOT_FOUND", 404)
        from ..v2.import_profiles import binding_status
        version_run = job(db, version.run_id)
        source_state = load(version_run.state_json)
        profile_binding = binding_status(db, source_state["context"])
        stored_quality = load(version.quality_report)
        quality = stored_quality
        if profile_binding["status"] == "stale":
            quality = {**stored_quality, "tqi_verdict": "blocked", "quality_status": "needs_review",
                "blocking_reasons": [*stored_quality.get("blocking_reasons", []), {"code": "IMPORT_ASSET_STALE"}]}
        content = materialize(version)
        from ..v2.adaptive_format import document_end_answers
        answers = document_end_answers(content.get("units", []))
        return {"version_id": version.id, "version": version.version, "content_hash": version.content_hash,
                "export_binding": {"run_id": version.run_id, "task_id": version_run.goal_id,
                    "state_revision": version_run.state_revision, "version_id": version.id,
                    "content_hash": version.content_hash},
                "quality_hash": version.quality_hash, "content": content, "quality": quality,
                "document_end_answers": answers,
                "answer_projection": {"method_version": "task-adaptive.v1", "sha256": sha(answers), "source_version_id": version.id},
                "stored_quality": stored_quality, "profile_binding": profile_binding,
                "quality_hash_scope": "immutable_stored_quality_not_current_eligibility_projection"}


@secured.post("/artifacts/{artifact_id}/export-sets", status_code=201)
def export_set(artifact_id: str, payload: ExportRequest, key=Depends(action_key)):
    return exports.create_export(artifact_id, payload, key)


@secured.get("/export-sets/{bundle_id}/files/{file_id}")
def download(bundle_id: str, file_id: str, request: Request):
    data, metadata = exports.download(bundle_id, file_id)
    etag = '"' + metadata["sha256"] + '"'
    headers = {"ETag": etag, "Cache-Control": "private,no-cache",
               "Content-Disposition": f'attachment; filename="{metadata["name"]}"'}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(content=data, media_type="application/zip" if file_id == "bundle" else "text/markdown; charset=utf-8", headers=headers)


@secured.get("/actions/receipt")
def action_receipt(key=Depends(action_key)):
    from ..model_settings import read_config
    private = read_config()
    managed = private.get("v2_management_receipts", {}).get(digest(key))
    if managed:
        return {"status": "completed", **managed}
    configured = private.get("v2_receipts", {}).get(key)
    if configured:
        return {"status": "completed", "response": {"applied": True, "applied_revision": configured["applied_revision"]}}
    with SessionLocal() as db:
        from ..models import PropositionConfig
        for prefix in ("insight-action:", "action:", "knowledge-import:", "principle-action:"):
            saved = db.get(PropositionConfig, prefix + digest(key))
            if saved:
                return {"status": "completed", **load(saved.settings_json)}
        for obj in db.query(LearningGoal).filter(LearningGoal.input_revision > 0):
            item = load(obj.task_state_json).get("receipts", {}).get(key)
            if item:
                return {"status": "completed", **item}
        for obj in db.query(GenerationJob).filter_by(job_type="task_v2"):
            item = load(obj.state_json).get("receipts", {}).get(key)
            if item:
                return {"status": "completed", **item}
    return {"status": "not_found"}


def locate_finding(db, finding_id):
    for obj in db.query(GenerationJob).filter_by(job_type="task_v2"):
        state = load(obj.state_json)
        for f in state.get("quality", {}).get("findings", []):
            if f["finding_id"] == finding_id:
                return obj, state, f
    raise DomainError("FINDING_NOT_FOUND", 404)


@secured.post("/findings/{finding_id}/user-reviews", status_code=201)
def user_review(finding_id: str, payload: UserReview, key=Depends(action_key)):
    body_hash = digest(payload.model_dump())
    with write() as db:
        obj, state, finding = locate_finding(db, finding_id)
        prior = tasks.receipt(state, key, body_hash)
        if prior:
            return prior
        cas(obj, payload.expected_revision)
        version = db.get(TutorialVersion, payload.version_id)
        if not version or version.content_hash != payload.content_hash or finding["location"]["version_id"] != version.id:
            raise DomainError("FINDING_STALE")
        review = {"review_id": str(uuid4()), "actor": "task_owner", "finding_id": finding_id, **payload.model_dump()}
        state.setdefault("user_reviews", []).append(review)
        obj.state_revision += 1
        tasks.remember(state, key, body_hash, review, "user_review")
        obj.state_json = dump(state)
        return review


@secured.post("/findings/{finding_id}/repairs", status_code=202)
def repair(finding_id: str, payload: RepairRequest, background: BackgroundTasks, key=Depends(action_key)):
    from ..v2.repairs import request_repair
    result = request_repair(finding_id, payload, key)
    background.add_task(execute_run, result["run_id"])
    return result


@secured.post("/findings/{finding_id}/evidence")
def evidence(finding_id: str, payload: RepairRequest, key=Depends(action_key)):
    with SessionLocal() as db:
        obj, state, finding = locate_finding(db, finding_id)
    raise DomainError("INDEPENDENT_VERIFIER_REQUIRED", 422)


@secured.post("/chat")
def reader_chat(payload: ChatRequest, key=Depends(action_key)):
    """阅读页 AI 聊天：v2 入口（旧 /api/chat 受 LEGACY 隔离门约束）。"""
    from ..llm.service import AIService
    from ..services import chat as chat_service
    with SessionLocal() as db:
        return chat_service.chat(db, payload, AIService(db))


router.include_router(secured)

"""Import an external Markdown file as an owner-readable, no-model task."""
from __future__ import annotations

import mimetypes
import os
from dataclasses import dataclass
from pathlib import PurePath
from uuid import uuid4


from ..config import settings
from ..models import GoalMaterial, GenerationJob, Tutorial
from .contracts import DomainError, TaskInput, SpecBinding, digest, sha
from .external_markdown import ASSET_EXTENSIONS, METHOD_VERSION, parse_markdown
from .quality import compile_plan, reduce_quality
from .review_contract import review_protocol_manifest
from .runtime import dump, load, publish, write
from .storage import BlobStore
from .tasks import create_task, goal
from .runtime import job as job_by_run


@dataclass(frozen=True)
class ImportedFile:
    filename: str
    data: bytes


def _safe_name(value: str) -> str:
    name = PurePath(value.replace("\\", "/")).name.strip()
    if not name or any(ord(c) < 32 for c in name):
        raise DomainError("INVALID_EXTERNAL_FILENAME", 422)
    return name[:255]


def _decode_markdown(raw: bytes) -> str:
    try:
        return raw.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise DomainError("EXTERNAL_MARKDOWN_ENCODING_UNKNOWN", 422) from exc


def _asset_type(name: str) -> str:
    suffix = PurePath(name).suffix.lower()
    return ASSET_EXTENSIONS.get(suffix, "")


def _match_assets(parsed: dict, attachments: list[ImportedFile]) -> list[dict]:
    assets = [dict(asset) for asset in parsed["manifest"]["assets"]]
    available = {item.filename: item for item in attachments}
    by_base: dict[str, list[ImportedFile]] = {}
    for item in attachments:
        by_base.setdefault(PurePath(item.filename).name.lower(), []).append(item)
    for asset in assets:
        path = PurePath(asset["resolved_path"].replace("\\", "/"))
        candidates = available.get(asset["resolved_path"]) or by_base.get(path.name.lower(), [])
        if len(candidates) == 1:
            asset["blob_hash"] = None
            asset["uploaded_filename"] = candidates[0].filename
            asset["status"] = "matched"
        elif len(candidates) > 1:
            asset["status"] = "ambiguous"
    return assets


def _persist_assets(assets: list[dict], attachments: list[ImportedFile], store: BlobStore) -> dict[str, str]:
    saved: dict[str, str] = {}
    for item in attachments:
        if _asset_type(item.filename) not in ASSET_EXTENSIONS.values():
            raise DomainError("UNSUPPORTED_EXTERNAL_ASSET", 422, filename=item.filename)
        saved[item.filename] = store.put(item.data)
    for asset in assets:
        filename = asset.get("uploaded_filename")
        if asset.get("status") == "matched" and filename in saved:
            asset["blob_hash"] = saved[filename]
            asset["status"] = "stored"
        elif asset.get("status") in {"missing", "ambiguous"}:
            asset["status"] = "unresolved"
    return saved


def _reader_units(parsed: dict, assets: list[dict], text: str) -> list[dict]:
    units = []
    for index, item in enumerate(parsed["units"], 1):
        body = item["body"]
        refs = {asset["original_ref"] for asset in assets if asset.get("status") == "stored"}
        unit_assets = [asset for asset in assets if asset["original_ref"] in body]
        unit = {
            "unit_id": str(uuid4()),
            "title": str(item["title"] or f"章节 {index}"),
            "body": body,
            "content_hash": sha(body),
            "artifact_role": "tutorial",
            "artifact_roles": ["tutorial"],
            "section_level": int(item["section_level"]),
            "heading_kind": item["heading_kind"],
            "source_start": int(item["source_start"]),
            "source_end": int(item["source_end"]),
            "source_hash": sha(body),
            "source_locator": f"utf8:{len(text[:item["source_start"]].encode())}:{len(text[:item["source_end"]].encode())}",
            "display_order": index,
            "section_number": index,
            "origin": "external_import",
            "required": True,
            "depends_on": [],
            "material_chunk_ids": [],
        }
        if unit_assets:
            unit["assets"] = unit_assets
        units.append(unit)
    return units


def import_markdown(markdown_file: ImportedFile, attachments: list[ImportedFile], key: str, ai_assist: bool = False) -> dict:
    suffix = PurePath(markdown_file.filename).suffix.lower()
    if suffix not in {".md", ".markdown"}:
        raise DomainError("UNSUPPORTED_EXTERNAL_MARKDOWN", 422)
    if not markdown_file.data:
        raise DomainError("EMPTY_EXTERNAL_MARKDOWN", 422)
    if len(markdown_file.data) > settings.material_max_bytes:
        raise DomainError("EXTERNAL_MARKDOWN_TOO_LARGE", 413, limit_bytes=settings.material_max_bytes)
    total = len(markdown_file.data) + sum(len(item.data) for item in attachments)
    if total > settings.material_max_bytes:
        raise DomainError("EXTERNAL_IMPORT_TOO_LARGE", 413, limit_bytes=settings.material_max_bytes)
    text = _decode_markdown(markdown_file.data)
    parsed = parse_markdown(text)
    store = BlobStore()
    raw_hash = store.put(markdown_file.data)
    extracted_hash = store.put(text.encode("utf-8"))
    source_id = str(uuid4())
    request_hash = digest({"method_version": METHOD_VERSION, "raw_hash": raw_hash,
                           "asset_hashes": sorted((item.filename, sha(item.data)) for item in attachments)})
    filename = markdown_file.filename
    first_title = next((u["title"] for u in parsed["units"] if u["heading_kind"] not in {"prefix", "plain"}), "")
    title = (first_title or PurePath(filename).stem or "外部任务")[:200]
    payload = TaskInput(
        title=title,
        raw_input=f"导入外部 Markdown 文件：{filename}。原文仅作为阅读材料，不作为任务指令。",
        requested_artifacts=["tutorial"],
    )
    created = create_task(payload, key)
    from ..db import SessionLocal
    with SessionLocal() as db:
        owner = goal(db, created["task_id"])
        prior = load(owner.task_state_json).get("external_import", {})
        if prior.get("request_hash") == request_hash and prior.get("response", {}).get("version_id"):
            return prior["response"]
    ai_used = False
    ai_outcome = "not_needed" if not parsed["manifest"]["ai_assist_candidate"] else ("requested" if ai_assist else "not_requested")
    ai_usage: dict = {}
    ai_assumptions: list = []
    if ai_assist and parsed["manifest"]["ai_assist_candidate"]:
        try:
            suggestion = ai_structure_suggestion(text)
            parsed = parse_markdown(text, suggestion["accepted_lines"])
            ai_used = True
            ai_outcome = "used"
            ai_usage = suggestion["usage"]
            ai_assumptions = suggestion["assumptions"]
        except DomainError as exc:
            ai_outcome = exc.code
    assets = _match_assets(parsed, attachments)
    asset_hashes = _persist_assets(assets, attachments, store)
    request_hash = digest({"method_version": METHOD_VERSION, "raw_hash": raw_hash,
                           "asset_hashes": sorted((item.filename, sha(item.data)) for item in attachments)})
    manifest = {
        "schema_version": "external-import.v1",
        "method_version": METHOD_VERSION,
        "source_id": source_id,
        "filename": markdown_file.filename,
        "source_hash": parsed["manifest"]["source_hash"],
        "raw_hash": raw_hash,
        "extracted_hash": extracted_hash,
        "size": len(markdown_file.data),
        "char_count": len(text),
        "assets": assets,
        "coverage": {
            "complete": True,
            "unit_count": len(parsed["units"]),
            "source_hash": sha(text),
        },
        "parser": parsed["manifest"] | {"assets": assets, "ai_used": ai_used, "ai_outcome": ai_outcome, "ai_usage": ai_usage, "ai_assumptions": ai_assumptions},
    }

    units = _reader_units(parsed, assets, text)
    plan_units = [{
        "unit_id": unit["unit_id"], "unit_key": f"external:{index}", "title": unit["title"],
        "artifact_role": "tutorial", "artifact_roles": ["tutorial"], "required": True,
        "depends_on": [], "material_chunk_ids": [], "display_order": unit["display_order"],
        "section_number": unit["section_number"], "origin": "external_import",
    } for index, unit in enumerate(units, 1)]
    plan = {"schema_version": "aiedu.plan.v1", "units": plan_units,
            "network_policy": {"method_version": "run-network-policy.v1", "research_requested": False,
                                "network_enabled": False, "planning_source_tools": False,
                                "network_tool_rounds": 0, "network_requests_per_round": 0,
                                "disabled_behavior": "external_import_reads_no_public_web_sources"}}
    plan["plan_hash"] = digest(plan)
    response = {
        "task_id": created["task_id"], "run_id": None, "artifact_id": None, "version_id": None,
        "title": title, "source_hash": manifest["source_hash"], "raw_hash": raw_hash,
        "parser": manifest["parser"], "warnings": manifest["parser"]["warnings"],
        "assets": assets, "unit_count": len(units),
    }
    with write() as db:
        owner = goal(db, created["task_id"])
        state = load(owner.task_state_json)
        prior = state.get("external_import", {})
        if prior.get("request_hash") == request_hash and prior.get("response", {}).get("version_id"):
            return prior["response"]
        state["task_state"] = "confirmed"
        state["confirmed_context_hash"] = created["context_hash"]
        state["external_import"] = {"request_hash": request_hash, "manifest": manifest,
                                    "response": response, "method_version": METHOD_VERSION}
        context = state["context"]
        from .quality import frozen_quality_layers
        state["check_plan"] = compile_plan(
            context,
            quality_layers=frozen_quality_layers(state),
        )
        state["plan"] = plan
        state["mode"] = "demo"
        state["artifact_id"] = str(uuid4())
        state["version_id"] = None
        state["stages"] = {"importing": "running"}
        state["budget"] = {"calls": 0, "tokens": 0, "amount": 0, "seconds": 0}
        state["quality_protocol"] = review_protocol_manifest()
        state["execution_policy"] = {
            "version": "external-import-no-model.v1", "model_calls_enabled": False,
            "generation_concurrency": 0, "check_concurrency": 0,
            "semantic_batch": False, "network_enabled": False, "network_tool_rounds": 0,
            "planning_source_tools": False, "criterion_assessment": False,
            "quality_loop": False,
        }
        run_id, root_id = str(uuid4()), str(uuid4())
        worker = str(uuid4())
        obj = GenerationJob(id=root_id, root_job_id=root_id, run_id=run_id, goal_id=owner.id,
                            job_type="task_v2", status="running", state_json=dump(state),
                            action_key=key + ":external-import", request_hash=request_hash,
                            state_revision=0, cancel_epoch=0, worker_instance_id=worker,
                            lease_until=__import__("time").time() + 60)
        db.add(obj)
        db.add(Tutorial(id=state["artifact_id"], goal_id=owner.id, title=title))
        db.add(GoalMaterial(id=source_id, goal_id=owner.id, filename=filename,
                            ext=PurePath(filename).suffix.lower(), content_type="text/markdown",
                            size=len(markdown_file.data), status="ready", extract_status="ok",
                            text_excerpt=text, char_count=len(text), source_char_count=len(text),
                            raw_hash=raw_hash, extracted_hash=extracted_hash,
                            extractor_version=METHOD_VERSION, manifest_json=dump(manifest)))
        state["receipts"][key + ":import_request"] = {
            "request_hash": request_hash, "response": response, "action_kind": "external_import"}
        owner.task_state_json = dump(state)
    quality = reduce_quality(state["check_plan"], [], [])
    version_id = publish(run_id, worker, 0, units, quality | {"_version_id": None},
                         f"{run_id}:external-import")
    with write() as db:
        obj = job_by_run(db, run_id)
        current = load(obj.state_json)
        current["stages"] = {"importing": "done", "generating": "not_applicable",
                             "checking": "not_applicable", "repairing": "not_applicable",
                             "verifying": "not_applicable"}
        current["stop_reason"] = "external_import_no_model_calls"
        current["external_import"]["response"] = response | {
            "run_id": run_id, "artifact_id": current["artifact_id"], "version_id": version_id,
        }
        obj.state_json = dump(current)
        obj.status = "completed"
        obj.state_revision += 1
        owner = goal(db, created["task_id"])
        owner_state = load(owner.task_state_json)
        owner_state["external_import"]["response"] = current["external_import"]["response"]
        owner.task_state_json = dump(owner_state)
    return current["external_import"]["response"]


def asset_response(data: bytes, content_type: str):
    from fastapi import Response
    return Response(content=data, media_type=content_type,
                    headers={"ETag": '"' + sha(data) + '"', "Cache-Control": "private, immutable"})


def ai_structure_suggestion(text: str) -> dict:
    """Use the user-selected reader-chat model only when rules are low-confidence.

    The model may only confirm candidate line numbers. Titles are taken from the
    original lines, so it cannot invent or rewrite sections.
    """
    from ..db import SessionLocal
    from ..llm.provider import OpenAICompatibleProvider
    from ..llm.service import AIService
    from .model_roles import for_role
    profile = for_role("chat")
    lines = text.splitlines(keepends=True)
    if len(text) > 120000:
        raise DomainError("EXTERNAL_AI_INPUT_TOO_LARGE", 422, limit_chars=120000)
    numbered = "".join(f"{index + 1}: {line}" for index, line in enumerate(lines))
    system = (
        "你是文档结构识别器。只确认原文中真实存在的标题行；不得改写、总结或新增内容。"
        "不确定的行一律不要确认。只输出 JSON 对象。"
    )
    user = (
        "以下是带行号的 Markdown 原文。请返回 {\"sections\":[{\"line\":行号,\"confidence\":0到1}],\"assumptions\":[...]}。"
        + "\n" + numbered
    )
    with SessionLocal() as db:
        ai = AIService(db)
        ai.provider = OpenAICompatibleProvider(profile)
        ai.role_profile_id = getattr(profile, "llm_profile_id", None)
        result = ai._real_json("external_markdown_structure", system, user, ("sections",), None,
                              prompt_manifest={"method_version": "external-markdown-structure-ai.v1",
                                               "model_role": "chat", "line_only": True})
    sections = result.get("sections")
    if not isinstance(sections, list):
        raise DomainError("EXTERNAL_AI_RESULT_INVALID", 502)
    accepted = []
    confidences = []
    for row in sections:
        if not isinstance(row, dict):
            continue
        line_no = row.get("line")
        confidence = row.get("confidence", 1)
        if not isinstance(line_no, int) or not 1 <= line_no <= len(lines):
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            continue
        if confidence >= 0.70 and lines[line_no - 1].strip():
            accepted.append(line_no - 1)
            confidences.append(confidence)
    return {
        "method_version": "external-markdown-structure-ai.v1",
        "accepted_lines": accepted,
        "rejected_lines": len(sections) - len(accepted),
        "confidence": min(confidences) if confidences else 0,
        "assumptions": result.get("assumptions", []),
        "usage": dict(ai.usage_last),
    }



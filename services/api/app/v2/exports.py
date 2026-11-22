"""Fixed version/renderer/options/bytes bundles; reads never render a new version."""
from __future__ import annotations

import io
import json
import html
import re
import zipfile
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from ..db import SessionLocal
from ..models import GenerationJob, Tutorial, TutorialVersion
from .contracts import DomainError, digest, sha
from .runtime import cas, dump, job, materialize, write
from .storage import BlobStore, ExportSet


def safe_markdown(body, targets, wiki_mode="relative"):
    """Portable Markdown, not active HTML. Source/version blobs stay unchanged."""
    changes = []
    def safe_url(value):
        decoded = re.sub(r"[\s\x00-\x20]", "", unquote(html.unescape(value))).replace("\\","")
        try:
            parsed = urlsplit(decoded)
        except ValueError:
            return False
        if parsed.scheme:
            return parsed.scheme.lower() in {"http", "https", "mailto"}
        return not (decoded.startswith(("/", "\\")) or ".." in decoded.split("/") or "\\" in decoded)
    def wiki(match):
        target, _, alias = match.group(1).partition("|")
        page, marker, heading = target.partition("#")
        label = alias or page or heading
        filename = targets.get(page) or targets.get(page.removesuffix(".md"))
        label = label.replace("[","").replace("]","")
        heading = heading.replace("(","").replace(")","").replace(" ","-")
        changes.append({"kind":"wikilink", "target":target, "resolved":bool(filename)})
        return f"[{label}]({filename}{'#'+heading if marker else ''})" if filename and wiki_mode == "relative" else label
    def part(text):
        # GFM requires text after a task marker. Only this export projection is
        # normalized; immutable source versions and older export sets stay fixed.
        def empty_task(match):
            changes.append({"kind": "empty_task_label_added"})
            return match.group(1) + " 待勾选"
        text = re.sub(r"(?m)^(\s*[-+*] \[[ xX]\])[ \t]*$", empty_task, text)
        text = re.sub(r"!?\[\[([^\]\n]+)\]\]", wiki, text)
        def link(match):
            prefix, label, url = match.groups()
            if prefix or not safe_url(url):
                changes.append({"kind":"image_or_unsafe_link_removed"})
                return label
            return match.group(0)
        text = re.sub(r"(!?)\[([^\]\n]*)\]\(([\s\S]*?)\)(?!\))", link, text)
        text = re.sub(r"!\[([^\]\n]*)\](?:\[[^\]\n]*\])?",lambda m:m.group(1),text)
        # Reference-link destinations get the same allowlist; HTML never stays executable.
        text = re.sub(r"(?m)^(\s*\[[^\]]+\]:\s*)(\S+).*$",
            lambda m: m.group(0) if safe_url(m.group(2)) else m.group(1)+"#", text)
        return text.replace("<", "&lt;").replace(">", "&gt;")
    # Preserve literal fenced and inline code, where escaping would change examples.
    pieces = re.split(r"(^\s*`{3,}[^\n]*\n.*?^\s*`{3,}\s*$|^\s*~{3,}[^\n]*\n.*?^\s*~{3,}\s*$|`[^`\n]+`)", body, flags=re.M|re.S)
    return "".join(piece if index%2 else part(piece) for index,piece in enumerate(pieces)), changes


def strict_delivery_gate(quality, profile_binding=None):
    """Final delivery requires the executable gate and the calibration policy.

    Calibration never excuses skipping checks or repairs. Uncalibrated output
    remains available only through the explicitly labelled draft export.
    """
    reasons = quality.get("blocking_reasons") or []
    binding_current = (profile_binding or {}).get("status", "current") == "current"
    return (quality.get("tqi_verdict") == "pass"
        and quality.get("quality_status") == "passed"
        and quality.get("calibration_status") == "calibrated"
            and not reasons
            and binding_current)


def create_export(artifact_id, payload, key):
    request = digest({"artifact_id": artifact_id, **payload.model_dump()})
    with SessionLocal() as db:
        old = db.query(ExportSet).filter_by(action_key=key).first()
        if old:
            if old.request_hash != request:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return json.loads(old.manifest_json)
        artifact = db.get(Tutorial, artifact_id)
        version = db.get(TutorialVersion, payload.version_id)
        if artifact is None or version is None or version.tutorial_id != artifact_id:
            raise DomainError("VERSION_MISMATCH")
        root = job(db, version.run_id)
        cas(root, payload.expected_revision)
        root_state = json.loads(root.state_json)
        frozen_context = root_state["context"]
        from .import_profiles import binding_status
        profile_binding = binding_status(db, frozen_context)
        body, quality = materialize(version), json.loads(version.quality_report)
        units = body.get("units", [])
        required = set(body.get("artifacts", []))
        demo = version.model_provider == "demo"
        synthesis_attachment = None
        if "synthesis" in payload.options:
            from .synthesis_projection import attachment
            synthesis_attachment = attachment(db, version, payload.options["synthesis"], delivery_kind=payload.kind)
    from .items import project_units
    selected = [u for u in project_units(units) if not payload.roles or u["artifact_role"] in payload.roles]
    missing = sorted(required - {u["artifact_role"] for u in selected})
    if not selected:
        raise DomainError("EMPTY_DRAFT", 422)
    if missing and payload.kind == "complete":
        raise DomainError("INCOMPLETE_REQUEST", 422, missing_required=missing)
    from .delivery import decision_from_state
    delivery_decision = decision_from_state(
        root_state, quality, selected,
        profile_binding_current=profile_binding.get("status") == "current",
    )
    delivery_status = "draft"
    if payload.kind == "complete" and profile_binding.get("status") != "current":
        raise DomainError("PROFILE_BINDING_STALE", 422, stale_refs=profile_binding.get("stale_refs", []))
    if payload.kind == "complete":
        if delivery_decision["state"] == "waiver_required":
            raise DomainError(
                "FINAL_WAIVER_CONFIRM_REQUIRED", 409,
                delivery_decision=delivery_decision,
            )
        if not delivery_decision["final_allowed"]:
            raise DomainError(
                "FINAL_EXPORT_NOT_PASSED", 422,
                tqi_verdict=quality.get("tqi_verdict"),
                quality_status=quality.get("quality_status"),
                calibration_status=quality.get("calibration_status"),
                blocking_reason_count=len(quality.get("blocking_reasons") or []),
                delivery_decision=delivery_decision,
            )
        delivery_status = "final"
    store, files, archive = BlobStore(), [], io.BytesIO()
    wiki_mode = payload.options.get("wikilinks", "relative")
    if wiki_mode not in {"relative", "text"}:
        raise DomainError("EXPORT_OPTION_INVALID", 422)
    filename_prefix = "draft-" if delivery_status == "draft" else ""
    main_filename = f"{filename_prefix}main.md"
    answers_filename = f"{filename_prefix}answers.md"
    bundle_filename = f"{filename_prefix}bundle.zip"
    targets = {}
    for unit in selected:
        for alias in set((unit["file_id"], unit["unit_id"], unit.get("title", ""))):
            if alias:
                targets[alias] = (answers_filename if unit["artifact_role"] == "answer" else main_filename) if alias not in targets else None
    transformations = []
    projections = []
    sanitized_by_id = {}
    for unit in selected:
        sanitized, changes = safe_markdown(unit["body"], targets, wiki_mode)
        final = sanitized.replace("\r\n", "\n").replace("\r", "\n").encode()
        transformations.append({"file_id":unit["file_id"], "changes":changes})
        sanitized_by_id[unit["file_id"]] = final
        projections.append({"file_id":unit["file_id"], "unit_id":unit["unit_id"], "name":f"{unit['file_id']}.md",
                            "artifact_role":unit["artifact_role"], "sha256":store.put(final),
                            "size":len(final), "downloadable":False})
    answer_rows = [u for u in selected if u["artifact_role"] == "answer"]
    primary_rows = [u for u in selected if u["artifact_role"] != "answer"]
    main_rows = primary_rows or ([] if answer_rows else selected)
    title = frozen_context.get("title") or artifact_id
    title_markdown, _ = safe_markdown(str(title), targets, "text")
    heading = title_markdown.replace("\r", " ").replace("\n", " ").strip()
    if not heading:
        heading = "学习内容"
    def render_document(title_text, rows):
        value = [f"# {title_text}", ""]
        value.append(f"> 交付状态：{'草稿' if delivery_status == 'draft' else '最终交付'}；质量：{quality['tqi_verdict']} / "
                     f"{quality['quality_status']}；校准：{quality['calibration_status']}；demo：{str(demo).lower()}。")
        if delivery_status == "draft":
            value.append("> 这是已有内容草稿，不是质量通过的最终交付；请勿当作已完成教程使用。")
        value.append("")
        for unit in rows:
            value.extend([sanitized_by_id[unit["file_id"]].decode("utf-8"), ""])
        return "\n".join(value).replace("\r\n", "\n").replace("\r", "\n").encode()
    main_body = render_document(heading, main_rows)
    from .adaptive_format import document_end_answers
    end_answers = document_end_answers(main_rows)
    if end_answers:
        safe_answers, answer_changes = safe_markdown(end_answers, targets, wiki_mode)
        main_body += ("\n## 文末参考答案\n\n" + safe_answers + "\n").encode()
        transformations.append({"file_id": "main-document-end-answers", "changes": answer_changes})
    if synthesis_attachment:
        safe_synthesis, synthesis_changes = safe_markdown(synthesis_attachment["body"], targets, wiki_mode)
        main_body += ("\n" + safe_synthesis + "\n").encode("utf-8")
        transformations.append({"file_id": "main-source-bound-synthesis", "changes": synthesis_changes})
    answer_body = render_document("参考答案", answer_rows) if answer_rows else b""
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        documents = []
        if main_rows:
            documents.append({"file_id": "main", "filename": main_filename, "body": main_body, "artifact_role": "document"})
        if answer_rows:
            documents.append({"file_id": "answers", "filename": answers_filename, "body": answer_body, "artifact_role": "answer"})
        if not documents:
            raise DomainError("EMPTY_DRAFT", 422)
        for item in documents:
            filename = item["filename"]
            final = item["body"]
            blob_hash = store.put(final)
            files.append({"file_id": item["file_id"], "name": filename, "sha256": blob_hash,
                          "size": len(final), "artifact_role": item["artifact_role"]})
            info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            bundle.writestr(info, final)
    archive_hash = store.put(archive.getvalue())
    files.append({"file_id": "bundle", "name": bundle_filename, "sha256": archive_hash, "size": len(archive.getvalue())})
    manifest = {"schema_version": "aiedu.export-set.v1", "bundle_id": str(uuid4()), "artifact_id": artifact_id,
                "task_id": root.goal_id, "run_id": version.run_id, "content_hash": version.content_hash,
                "quality_hash": version.quality_hash,
                "version_id": payload.version_id, "kind": payload.kind, "delivery_status": delivery_status,
                "renderer_version": "markdown.safe.merged.v9", "transformations":transformations,
                "options_hash": digest(payload.options), "files": files, "missing_required": missing,
                "original_required": sorted(required), "quality": {k: quality[k] for k in ("tqi_verdict", "quality_status", "calibration_status")}, "demo": demo,
                "profile_binding": profile_binding,
                "delivery_decision": delivery_decision,
                "delivery_calibration_status": delivery_decision["delivery_calibration_status"],
                "certificate_ref": {
                    "certificate_hash": (root_state.get("calibration_certificate") or {}).get("certificate_hash"),
                    "status": delivery_decision["certificate_status"],
                },
                "delivery_waiver_ref": {
                    key: (root_state.get("delivery_waiver") or {}).get(key)
                    for key in ("schema_version", "waiver_hash", "status", "soft_items")
                } if root_state.get("delivery_waiver") else None,
                "quality_gate": {"method": "delivery-decision.v1", "profile_binding_required": "current",
                                 "certificate_required_for_unwaived_claims": True,
                                 "soft_items": ["C3.correctness", "G2.knowledge_integration"],
                                 "passed": delivery_status == "final"},
                "legacy_role_projections": projections}
    from .resolver import spec_entries
    manifest["spec_snapshot_hashes"] = [b["snapshot_hash"] for b in frozen_context["spec_bindings"]]
    manifest["field_consumers"] = spec_entries(frozen_context,"exporting")
    if synthesis_attachment:
        manifest["synthesis_binding"] = synthesis_attachment["binding"]
    manifest["bundle_hash"] = digest(manifest)
    with write() as db:
        old = db.query(ExportSet).filter_by(action_key=key).first()
        if old:
            if old.request_hash != request:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return json.loads(old.manifest_json)
        if synthesis_attachment:
            # Reject a changed synthesis head/quality/source-current binding
            # rather than attaching different content to a fixed user choice.
            latest_version = db.get(TutorialVersion, payload.version_id)
            latest_attachment = attachment(db, latest_version, payload.options["synthesis"], delivery_kind=payload.kind)
            if latest_attachment != synthesis_attachment:
                raise DomainError("SYNTHESIS_EXPORT_SELECTION_STALE", 409)
        db.add(ExportSet(bundle_id=manifest["bundle_id"], artifact_id=artifact_id, version_id=payload.version_id,
                        kind=payload.kind, state="ready", action_key=key, request_hash=request,
                        renderer_version="markdown.safe.merged.v9", options_hash=digest(payload.options),
                        manifest_json=dump(manifest), manifest_hash=manifest["bundle_hash"]))
    return manifest


def download(bundle_id, file_id):
    with SessionLocal() as db:
        bundle = db.get(ExportSet, bundle_id)
        if bundle is None or bundle.state != "ready":
            raise DomainError("BUNDLE_NOT_FOUND", 404)
        manifest = json.loads(bundle.manifest_json)
        file = next((f for f in manifest["files"] if f["file_id"] == file_id), None)
        if file is None:
            raise DomainError("FILE_NOT_FOUND", 404)
    return BlobStore().get(file["sha256"]), file

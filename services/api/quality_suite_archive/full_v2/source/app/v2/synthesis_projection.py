"""Fixed synthesis attachment for the pending reader/merged-export wiring.

No model call, source mutation, implicit latest selection or certification.
"""
from ..models import Tutorial
from .contracts import DomainError, digest
from .insights import record


def attachment(db, version, selection, *, delivery_kind):
    if (not isinstance(selection, dict) or set(selection) != {"revision", "result_hash"}
            or type(selection.get("revision")) is not int or selection["revision"] < 1
            or not isinstance(selection.get("result_hash"), str)):
        raise DomainError("SYNTHESIS_EXPORT_SELECTION_INVALID", 422)
    head = record(db, "synthesis-head:" + version.id)
    if not head or head.get("revision") != selection["revision"] or head.get("result_hash") != selection["result_hash"]:
        raise DomainError("SYNTHESIS_EXPORT_SELECTION_STALE", 409)
    value = record(db, head["record_key"])
    if (not value or digest(value) != selection["result_hash"]
            or value.get("source_version_id") != version.id
            or value.get("source_content_hash") != version.content_hash
            or value.get("source_quality_hash") != version.quality_hash
            or value.get("revision") != selection["revision"]):
        raise DomainError("SYNTHESIS_RECORD_STALE", 409)
    artifact = db.get(Tutorial, version.tutorial_id)
    current = bool(artifact and artifact.current_version_id == version.id)
    # Automatic success does not promote candidate content or inherit the source
    # tutorial's certification. The current runtime has no calibrated synthesis
    # issuer, so a final export including its result cannot pass this gate.
    if delivery_kind != "available_draft":
        raise DomainError("SYNTHESIS_FINAL_NOT_CALIBRATED", 422)
    latest = value.get("rounds", [])[-1:]
    if not latest or latest[0]["proposal"]["proposal_hash"] != value.get("latest_proposal_hash"):
        raise DomainError("SYNTHESIS_RECORD_STALE", 409)
    proposal = latest[0]["proposal"]["proposal"]
    if digest(proposal) != value["latest_proposal_hash"]:
        raise DomainError("SYNTHESIS_RECORD_STALE", 409)
    units = {u["unit_id"]: u for u in value["source_index"]["units"]}
    spans = {s["source_span_id"]: s for s in value["source_index"]["spans"]}

    def sources(ids):
        try:
            return "、".join(dict.fromkeys(units[spans[ref]["unit_id"]]["title"] for ref in ids))
        except KeyError:
            raise DomainError("SYNTHESIS_RECORD_STALE", 409) from None

    lines = ["## " + proposal["title"].replace("\n", " ").replace("\r", " "),
        "> 跨章知识整合候选；自动检查：" + value["automatic_status"] +
        "；未专业校准，不代表原教程质量通过。demo：" + str(value["demo"]).lower() + "。"]
    if not current:
        lines.append("> 来源已不是当前版本；以下仅为所选固定版本的历史整合草稿。")
    for block in proposal["blocks"]:
        lines.extend(["### " + block["heading"].replace("\n", " ").replace("\r", " "), block["body"],
            "来源章节：" + sources(block["source_span_ids"])])
    if proposal["relations"]:
        lines.append("### 已记录的联系")
        for relation in proposal["relations"]:
            lines.extend([relation["explanation"], "来源章节：" + sources(relation["source_span_ids"])])
    if proposal["limitations"] or proposal["unintegrated_units"]:
        lines.append("### 范围与缺口")
        lines.extend(proposal["limitations"])
        lines.extend(units[g["unit_id"]]["title"] + "：" + g["reason"] for g in proposal["unintegrated_units"])
    return {"body": "\n\n".join(lines), "binding": {
        "method_version": "fixed-synthesis-projection.v1", "run_id": value["run_id"],
        "source_version_id": version.id, "source_content_hash": version.content_hash,
        "source_quality_hash": version.quality_hash, "source_bundle_hash": value["source_bundle_hash"],
        "revision": value["revision"], "result_hash": selection["result_hash"],
        "proposal_hash": value["latest_proposal_hash"], "automatic_status": value["automatic_status"],
        "calibration_status": value["calibration_status"], "source_is_current": current,
        "demo": value["demo"], "source_index": value["source_index"]}}

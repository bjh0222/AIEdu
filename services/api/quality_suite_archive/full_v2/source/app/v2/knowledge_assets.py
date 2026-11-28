"""Structured research-asset import without copying protected question text into Git."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import DesignPrinciple, DesignPrincipleRevision, EvaluationFramework, PropositionConfig
from ..services.proposition_knowledge import digest as text_digest
from .contracts import DomainError, digest
from .insights import record, save
from .runtime import load
from ..learning.disciplines import canonical_domain_key

CARD_PREFIX = "knowledge-card:"
CARD_HEAD_PREFIX = "knowledge-card-head:"
FRAMEWORK_PREFIX = "knowledge-framework:"
FRAMEWORK_HEAD_PREFIX = "knowledge-framework-head:"
VERSION = "research-asset-import.v1"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_evidence(refs, field="evidence_refs"):
    if not isinstance(refs, list) or not refs:
        raise DomainError("RESEARCH_ASSET_EVIDENCE_REQUIRED", 422, field=field)
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("id") or not ref.get("source_card"):
            raise DomainError("RESEARCH_ASSET_EVIDENCE_INVALID", 422, field=field)
        if not ref.get("license") or not ref.get("verification_level"):
            raise DomainError("RESEARCH_ASSET_LICENSE_OR_VERIFICATION_MISSING", 422, field=field)


def _publish_immutable(db: Session, prefix: str, head_prefix: str, identity: str, version: int, value: dict):
    content_hash = digest(value)
    key = f"{prefix}{identity}:{version}"
    head_key = f"{head_prefix}{identity}"
    previous = record(db, key)
    now = _now()
    wrapped = {
        "id": identity,
        "version": version,
        "hash": content_hash,
        "content": deepcopy(value),
        "status": value.get("status", "active"),
        "calibration_status": "not_calibrated",
        "professional_certification": False,
        "created_at": now,
        "updated_at": now,
    }
    if previous:
        semantic = {k: v for k, v in previous.items() if k not in {"created_at", "updated_at"}}
        if semantic != wrapped | {"created_at": previous.get("created_at"), "updated_at": previous.get("updated_at")}:
            raise DomainError("RESEARCH_ASSET_VERSION_CONFLICT", 409, id=identity, version=version)
        wrapped = previous
    else:
        save(db, key, wrapped)
    ref = {"id": identity, "version": version, "hash": content_hash}
    head = record(db, head_key)
    if not head or head["version"] <= version:
        save(db, head_key, ref)
    return ref, wrapped


def publish_card(db: Session, card: dict):
    for field in ("id", "domain_key", "title", "summary", "source_kind"):
        if not isinstance(card.get(field), str) or not card[field].strip():
            raise DomainError("KNOWLEDGE_CARD_FIELD_REQUIRED", 422, field=field)
    if card.get("status") != "confirmed":
        raise DomainError("KNOWLEDGE_CARD_IMPORT_REQUIRES_CONFIRMED", 422)
    _validate_evidence(card.get("evidence_refs"))
    card = {
        **deepcopy(card),
        "version": int(card.get("version", 1)),
        "calibration_status": "not_calibrated",
        "authority": "AI/research synthesis; not professional calibration",
    }
    return _publish_immutable(db, CARD_PREFIX, CARD_HEAD_PREFIX, card["id"], card["version"], card)


def imported_cards(db: Session):
    rows = []
    for head_row in db.query(PropositionConfig).filter(
            PropositionConfig.id.startswith(CARD_HEAD_PREFIX)).order_by(PropositionConfig.id):
        head = load(head_row.settings_json)
        value = record(db, f"{CARD_PREFIX}{head['id']}:{head['version']}")
        if value and value.get("status") == "confirmed":
            rows.append(deepcopy(value))
    return rows


def publish_framework(db: Session, framework: dict):
    standards = framework.get("standards")
    if not isinstance(standards, list) or not standards:
        raise DomainError("FRAMEWORK_STANDARDS_REQUIRED", 422)
    identities = set()
    for standard in standards:
        required = ("id", "name", "rule", "scope", "priority", "strength",
                    "evidence_required", "source_quote", "source_ref", "origin")
        if not isinstance(standard, dict) or any(standard.get(field) in (None, "", {}) for field in required):
            raise DomainError("FRAMEWORK_STANDARD_INVALID", 422)
        if standard["id"] in identities:
            raise DomainError("FRAMEWORK_DUPLICATE_STANDARD", 422)
        identities.add(standard["id"])
        if standard["strength"] not in {"hard", "core", "advisory"}:
            raise DomainError("FRAMEWORK_STRENGTH_INVALID", 422)
        if type(standard["priority"]) is not int or not 0 <= standard["priority"] <= 3:
            raise DomainError("FRAMEWORK_PRIORITY_INVALID", 422)
        if not isinstance(standard["scope"], dict) or not standard["scope"]:
            raise DomainError("FRAMEWORK_SCOPE_INVALID", 422)
    source_text = framework.get("source_text")
    if not isinstance(source_text, str) or not source_text.strip():
        raise DomainError("FRAMEWORK_SOURCE_TEXT_REQUIRED", 422)
    if any(standard["source_quote"] not in source_text for standard in standards):
        raise DomainError("FRAMEWORK_SOURCE_QUOTE_UNBOUND", 422)
    _validate_evidence(framework.get("evidence_refs"))
    identity = framework.get("id") or "research-framework-" + digest(framework)[:24]
    version = int(framework.get("version", 1))
    source_hash = text_digest(source_text)
    source_map = {
        "method_version": VERSION,
        "evidence_refs": deepcopy(framework["evidence_refs"]),
        "carrier_scope": "v2_quality_context",
        "consumers": ["legacy_proposition", "v2_research_context"],
        "calibration_status": "not_calibrated",
    }
    value = {
        "schema_version": "aiedu.research-evaluation-framework.v1",
        "id": identity,
        "version": version,
        "title": framework["title"],
        "domain_key": framework["domain_key"],
        "source_text": source_text,
        "source_hash": source_hash,
        "standards": deepcopy(standards),
        "source_map": source_map,
        "warnings": deepcopy(framework.get("warnings", ["authored_uncalibrated"])),
        "extraction_method": "research_schema_import",
        "status": framework.get("status", "active"),
        "evidence_refs": deepcopy(framework["evidence_refs"]),
        "calibration_status": "not_calibrated",
        "professional_certification": False,
    }
    if value["status"] not in {"proposed", "active"}:
        raise DomainError("FRAMEWORK_STATUS_INVALID", 422)
    row = db.get(EvaluationFramework, identity)
    if row is None:
        row = EvaluationFramework(
            id=identity,
            title=value["title"],
            domain_key=value["domain_key"],
            version=version,
            source_text=source_text,
            source_hash=source_hash,
            standards=__import__("json").dumps(standards, ensure_ascii=False),
            source_map=__import__("json").dumps(source_map, ensure_ascii=False),
            status=value["status"],
            extraction_method=value["extraction_method"],
            warnings=__import__("json").dumps(value["warnings"], ensure_ascii=False),
        )
        db.add(row)
        db.flush()
    existing = {
        "title": row.title,
        "domain_key": row.domain_key,
        "version": row.version,
        "source_hash": row.source_hash,
        "standards": load(row.standards),
    }
    expected = {
        "title": value["title"],
        "domain_key": value["domain_key"],
        "version": value["version"],
        "source_hash": value["source_hash"],
        "standards": value["standards"],
    }
    if existing != expected:
        raise DomainError("FRAMEWORK_ID_CONFLICT", 409, id=identity)
    ref, _ = _publish_immutable(db, FRAMEWORK_PREFIX, FRAMEWORK_HEAD_PREFIX, identity, version, value)
    return ref, value


def imported_frameworks(db: Session, domain_key: str | None = None):
    rows = []
    for head_row in db.query(PropositionConfig).filter(
            PropositionConfig.id.startswith(FRAMEWORK_HEAD_PREFIX)).order_by(PropositionConfig.id):
        head = load(head_row.settings_json)
        value = record(db, f"{FRAMEWORK_PREFIX}{head['id']}:{head['version']}")
        if not value or value.get("status") != "active":
            continue
        if domain_key and value["content"]["domain_key"] not in {domain_key, "general"}:
            continue
        rows.append(deepcopy(value))
    return rows


def freeze_evaluation_frameworks(db: Session, context):
    domains = {canonical_domain_key(context.get("domain_key"))}
    domains.update(canonical_domain_key(item["domain_key"])
                   for item in context.get("subject_bindings", []))
    rows = db.query(EvaluationFramework).filter(
        EvaluationFramework.status == "active",
        EvaluationFramework.domain_key.in_([*domains, "general"]),
    ).order_by(EvaluationFramework.domain_key, EvaluationFramework.version.desc(),
               EvaluationFramework.created_at.desc()).all()
    selected, seen = [], set()
    for row in rows:
        domain = canonical_domain_key(row.domain_key)
        if domain in seen:
            continue
        seen.add(domain)
        source_map = load(row.source_map)
        selected.append({
            "id": row.id,
            "version": row.version,
            "domain_key": domain,
            "title": row.title,
            "source_hash": row.source_hash,
            "standards": load(row.standards),
            "evidence_refs": deepcopy(source_map.get("evidence_refs", [])),
            "carrier_scope": source_map.get("carrier_scope", "legacy_proposition_only"),
            "calibration_status": "not_calibrated",
        })
    body = {
        "method_version": "evaluation-framework-freeze.v1",
        "domain_keys": sorted(domains),
        "frameworks": selected,
        "calibration_status": "not_calibrated",
    }
    return {**context, "evaluation_frameworks": {**body, "composition_hash": digest(body)}}


def framework_manifest_entries(context, consumer):
    return [{
        "source_id": "evaluation-framework:" + item["id"],
        "source_kind": "evaluation_framework",
        "version": item["version"],
        "sha256": item["source_hash"],
        "selected": True,
        "reason": "active_task_domain_framework",
        "consumer": consumer,
        "message_position": "context.evaluation_frameworks",
        "domain_key": item["domain_key"],
    } for item in context.get("evaluation_frameworks", {}).get("frameworks", [])]


def publish_principle(db: Session, principle: dict):
    required = ("id", "domain_key", "title", "statement", "mechanism", "guidance",
                "applicability", "scope")
    if any(principle.get(field) in (None, "", {}) for field in required):
        raise DomainError("PRINCIPLE_IMPORT_FIELD_REQUIRED", 422)
    if principle.get("status") != "confirmed":
        raise DomainError("PRINCIPLE_IMPORT_REQUIRES_CONFIRMED", 422)
    _validate_evidence(principle.get("evidence_refs"))
    identity = principle["id"]
    row = db.get(DesignPrinciple, identity)
    immutable = {
        "domain_key": principle["domain_key"],
        "title": principle["title"],
        "statement": principle["statement"],
        "mechanism": principle["mechanism"],
        "guidance": principle["guidance"],
        "applicability": principle["applicability"],
        "scope": principle["scope"],
        "evidence_refs": principle["evidence_refs"],
        "version": int(principle.get("version", 1)),
        "status": "confirmed",
    }
    evidence_hash = digest(immutable["evidence_refs"])
    if row is not None:
        current = {
            "domain_key": row.domain_key,
            "title": row.title,
            "statement": row.statement,
            "mechanism": row.mechanism,
            "guidance": row.guidance,
            "applicability": row.applicability,
            "scope": load(row.scope),
            "version": row.version,
            "status": row.status,
        }
        if current != {k: immutable[k] for k in current}:
            raise DomainError("PRINCIPLE_IMPORT_CONFLICT", 409, id=identity)
        return {"id": row.id, "version": row.version, "evidence_hash": evidence_hash}, row
    row = DesignPrinciple(
        id=identity,
        domain_key=principle["domain_key"],
        title=principle["title"],
        statement=principle["statement"],
        mechanism=principle["mechanism"],
        guidance=principle["guidance"],
        applicability=principle["applicability"],
        scope=__import__("json").dumps({
            **principle["scope"],
            "guidance_ablation_status": "validated",
            "research_import_version": VERSION,
            "evidence_hash": evidence_hash,
        }, ensure_ascii=False),
        positive_refs=__import__("json").dumps(principle["evidence_refs"], ensure_ascii=False),
        negative_refs="[]",
        counterexamples=__import__("json").dumps(principle.get("counterexamples", []), ensure_ascii=False),
        evidence_count=len(principle["evidence_refs"]),
        evidence_family_count=len({ref["id"] for ref in principle["evidence_refs"]}),
        confidence=principle.get("confidence"),
        version=int(principle.get("version", 1)),
        status="confirmed",
        approval_source="research_import",
        rationale=principle.get("rationale", ""),
    )
    db.add(row)
    db.flush()
    db.add(DesignPrincipleRevision(
        principle_id=row.id,
        version=row.version,
        action="research_import",
        actor="import",
        snapshot=__import__("json").dumps({c.name: getattr(row, c.name)
                                          for c in row.__table__.columns}, ensure_ascii=False),
        rationale="Research-derived reusable experience; not professional calibration.",
    ))
    return {"id": row.id, "version": row.version, "evidence_hash": evidence_hash}, row

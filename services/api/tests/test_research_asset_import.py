"""Structured research assets enter existing DB-backed contracts idempotently."""
from uuid import uuid4

import pytest

from test_task_run_v2 import owner, post


def evidence():
    return [{
        "id": "E-test",
        "source_card": "研究/来源卡/test.md",
        "title": "Fixture research",
        "url": "https://example.org/research",
        "license": "test-only metadata",
        "verification_level": "fixture",
    }]


def payload():
    card = {
        "id": "card-general-structure",
        "version": 1,
        "domain_key": "general",
        "branch": "",
        "title": "概念、关系与边界",
        "summary": "知识结构应同时记录概念、关系、先修和证据边界。",
        "knowledge_model": ["概念节点", "关系类型", "先修链"],
        "common_misconceptions": ["把摘要当作知识结构"],
        "difficulty_features": ["概念互动", "迁移距离"],
        "quality_features": ["可定位", "可复用"],
        "transfer_boundaries": ["不得越过学科适用域"],
        "evidence_refs": evidence(),
        "source_kind": "AI research synthesis",
        "status": "confirmed",
    }
    principle = {
        "id": "principle-difficulty-layers",
        "version": 1,
        "domain_key": "general",
        "title": "难度三层分离",
        "statement": "结构覆盖、先验特征与实测难度必须分层记录。",
        "mechanism": "避免把题量、标签或算量误作心理测量难度。",
        "guidance": "规划时先写覆盖，再写先修与认知操作；无作答数据时把实测层标为未知。",
        "applicability": "所有教程与命题任务。",
        "scope": {"question_types": [], "learner": "", "branch": ""},
        "counterexamples": ["题量多所以更难"],
        "evidence_refs": evidence(),
        "status": "confirmed",
        "confidence": 0.0,
        "rationale": "Fixture research import, not calibration.",
    }
    source_text = "All applicable requirements need locatable evidence. Missing evidence remains unknown."
    framework = {
        "id": "framework-general-fixture",
        "version": 1,
        "title": "通用研究评价量规",
        "domain_key": "general",
        "source_text": source_text,
        "standards": [{
            "id": "G-evidence",
            "name": "证据与未知",
            "rule": "每个适用要求都需要可定位证据；缺失证据保留未知。",
            "scope": {"scene": "all"},
            "priority": 0,
            "strength": "hard",
            "evidence_required": "正文或材料中的可定位证据",
            "source_quote": "All applicable requirements need locatable evidence.",
            "source_ref": "fixture:source",
            "origin": "research_synthesis_uncalibrated",
        }],
        "evidence_refs": evidence(),
        "warnings": ["authored_uncalibrated", "not professional certification"],
        "status": "active",
    }
    return {"cards": [card], "principles": [principle], "frameworks": [framework]}


def test_import_is_idempotent_visible_and_calibrated_false(client):
    from app.v2.contracts import digest

    headers = owner(client)
    body = payload()
    result = post(client, "/knowledge/asset-imports",
                  body | {"source_hash": digest(body)}, headers, "research-assets-1")
    assert result.status_code == 201, result.text
    receipt = result.json()
    assert receipt["calibration_status"] == "not_calibrated"
    assert receipt["cards"][0]["ref"]["id"] == "card-general-structure"
    assert post(client, "/knowledge/asset-imports",
                body | {"source_hash": digest(body)}, headers,
                "research-assets-1").json() == receipt
    cards = client.get("/api/v2/knowledge/cards").json()
    assert any(card["card_id"] == "imported:card-general-structure" for card in cards["items"])
    principles = client.get("/api/v2/knowledge/assets/principles").json()
    assert principles["total"] == 1 and principles["items"][0]["status"] == "confirmed"
    frameworks = client.get("/api/v2/evaluation-frameworks").json()
    imported = next(item for item in frameworks["items"] if item["id"] == "framework-general-fixture")
    assert imported["calibration_status"] == "not_calibrated"
    assert imported["professional_certification"] is False
    assert imported["evidence_refs"][0]["license"] == "test-only metadata"


def test_bad_source_quote_rejects_entire_import(client):
    from app.db import SessionLocal
    from app.models import DesignPrinciple, PropositionConfig
    from app.v2.contracts import digest

    headers = owner(client)
    body = payload()
    body["frameworks"][0]["standards"][0]["source_quote"] = "not present in source"
    response = post(client, "/knowledge/asset-imports",
                    body | {"source_hash": digest(body)}, headers)
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "FRAMEWORK_SOURCE_QUOTE_UNBOUND"
    with SessionLocal() as db:
        assert db.query(DesignPrinciple).count() == 0
        assert not db.query(PropositionConfig).filter(
            PropositionConfig.id.startswith("knowledge-card:")).all()


def test_question_import_preserves_source_identity_and_license(client):
    headers = owner(client)
    markdown = """## Fixture Exam

**1.** Compute the determinant.

### Answers

**1.** The determinant is 2.
"""
    body = {
        "filename": "fixture-exam.md",
        "markdown": markdown,
        "domain_key": "math_logic",
        "branch": "线性代数",
        "source_identity": {
            "title": "Fixture public exam",
            "url": "https://example.org/exam",
            "license": "CC BY 4.0 fixture",
            "attribution": "Fixture attribution",
            "verification_level": "fixture",
        },
    }
    response = post(client, "/knowledge/batches/import", body, headers, "fixture-batch")
    assert response.status_code == 201, response.text
    batch = response.json()
    assert batch["source_identity"]["license"] == "CC BY 4.0 fixture"
    assert batch["counts"]["parsed"] >= 1
    detail = client.get(f"/api/v2/knowledge/assets/batches?limit=10").json()["items"][0]
    assert detail["source_identity"]["attribution"] == "Fixture attribution"

"""Offline contract tests for rubric research binding and read-only evidence GET."""
from __future__ import annotations

import pytest
from uuid import uuid4

from app.v2.contracts import Confirmation, StartRun, TaskInput
from app.v2.tasks import create_task, mutate_input, start_run

ORIGIN = {"Origin": "http://127.0.0.1:3001"}


def owner(client):
    response = client.post("/api/v2/session", headers=ORIGIN)
    assert response.status_code == 200
    return ORIGIN | {"X-CSRF-Token": response.json()["csrf_token"]}


def source():
    return {
        "source_id": "paper-1",
        "source_hash": "a" * 64,
        "text": "证据句必须逐字匹配。评价锚点只在项目内操作化。",
        "access_level": "open_fulltext_xml",
    }


def claim_output():
    return {"claims": [{
        "claim": "observable quality anchors can be operationalized",
        "source_id": "paper-1",
        "quote": "证据句必须逐字匹配。",
        "construct_scope": "assessment quality",
        "limitations": ["paper claim is not project calibration"],
    }]}


def dimension(index: int, claim_id: str):
    return {
        "dimension_id": f"d{index}",
        "name": f"诊断维度 {index}",
        "scope": "assessment",
        "construct_definition": "仅评价可直接观察的项目诊断构念。",
        "source_refs": [claim_id],
        "indicators": [{
            "indicator_id": f"i{index}",
            "observation": "统计满足明确条件的样本数。",
            "numerator": "满足条件的样本数",
            "denominator": "适用样本总数",
            "unit": "ratio",
            "missing_denominator_rule": "denominator missing => score null",
        }],
        "applicability": "仅适用于有明确观察单位和适用样本的评价对象。",
        "evidence_requirements": ["unique quote", "observable denominator"],
        "missing_conflict_unknown_rules": {
            "missing": "evidence missing => score null",
            "conflict": "conflicting evidence => undecidable",
            "unknown": "cannot decide => score null",
        },
        "anchors": {
            "0": "零档：没有可观察证据或适用分母缺失。",
            "1": "一档：部分条件满足且证据可定位。",
            "2": "二档：全部操作条件满足且证据逐字定位。",
        },
        "examples": {
            "positive": "两条唯一证据定位且分母完整。",
            "negative": "证据不存在却被计为零分。",
            "unknown": "证据矛盾，不判定分数。",
        },
        "result_schema": {
            "unknown_score": None,
            "result": "satisfied|violated|undecidable|not_applicable",
            "scope": "assessment",
            "evidence": [],
            "score": None,
            "source": [],
            "version": 1,
        },
        "c_decision_procedure": ["locate unique literal evidence", "apply missing/conflict/unknown rule"],
        "repair_acceptance": {"confirmed_failure": "repair must satisfy the stated observable acceptance"},
        "operationalization_basis": "project diagnostic anchors derived from bound claims, not calibrated thresholds.",
        "limitations": ["not calibrated"],
    }


def imported_material():
    return {
        "source_id": "imported-1",
        "source_hash": "b" * 64,
        "text": "导入材料代表样本必须逐字匹配。这是唯一引用句。",
    }


def profile_part(quote: str):
    return {
        "structure": "从导入材料归纳的结构。",
        "difficulty_features": ["可观察步骤负担"],
        "quality_features": ["题面与答案一致"],
        "prerequisites": ["先修概念"],
        "representative_samples": [{
            "source_id": "imported-1",
            "quote": quote,
            "interpretation": "用于归纳难度和质量特征。",
        }],
        "coverage": ["唯一引用句所在范围"],
        "limitations": ["小样本不建立心理测量难度"],
        "unknown": ["材料未覆盖的学习收益"],
    }


def valid_profile_output():
    return {"knowledge": profile_part("导入材料代表样本必须逐字匹配。"),
            "proposition": profile_part("这是唯一引用句。")}


def test_bind_claims_keeps_literal_boundary_and_rejects_unbound_or_missing():
    from app.v2.contracts import DomainError
    from app.v2.rubric_research import bind_claims

    claims = bind_claims(claim_output(), [source()])
    assert len(claims) == 1
    row = claims[0]
    assert row["source_hash"] == "a" * 64
    assert row["support_status"] == "literal_bound_model_interpretation"
    text_bytes = source()["text"].encode()
    assert row["start_byte"] == text_bytes.find(row["quote"].encode())
    assert row["end_byte"] == row["start_byte"] + len(row["quote"].encode())

    repeated = {"text": "重复 重复", "source_id": "paper-1", "source_hash": "c" * 64, "access_level": "abstract"}
    with pytest.raises(DomainError, match="RESEARCH_CLAIMS_WITHOUT_LITERAL_EVIDENCE"):
        bind_claims({"claims": [{"claim": "x", "source_id": "paper-1", "quote": "重复"}]}, [repeated])
    with pytest.raises(DomainError, match="RESEARCH_CLAIMS_WITHOUT_LITERAL_EVIDENCE"):
        bind_claims({"claims": [{"claim": "x", "source_id": "unknown", "quote": "证据句"}]}, [source()])
    with pytest.raises(DomainError, match="RESEARCH_CLAIMS_WITHOUT_LITERAL_EVIDENCE"):
        bind_claims({}, [source()])


def test_validate_dimensions_accepts_seven_executable_dimensions_and_unknown_null():
    from app.v2.rubric_research import bind_claims, validate_dimensions

    claim_id = bind_claims(claim_output(), [source()])[0]["claim_id"]
    dimensions = validate_dimensions(
        {"dimensions": [dimension(index, claim_id) for index in range(1, 8)]},
        "assessment",
        [{"claim_id": claim_id}],
    )
    assert len(dimensions) == 7
    assert len({row["dimension_id"] for row in dimensions}) == 7
    assert all(row["scope"] == "assessment" for row in dimensions)
    assert all(row["result_schema"]["unknown_score"] is None for row in dimensions)
    assert all({"missing", "conflict", "unknown"} <= row["missing_conflict_unknown_rules"].keys() for row in dimensions)


@pytest.mark.parametrize("fault,code", [
    ("unbound_source", "RESEARCH_DIMENSION_SOURCE_UNBOUND"),
    ("missing_unknown_rule", "RESEARCH_DIMENSION_UNKNOWN_RULE_MISSING"),
    ("unknown_score_not_null", "RESEARCH_DIMENSION_UNKNOWN_RULE_INVALID"),
    ("schema_invalid", "RESEARCH_DIMENSION_SCHEMA_INVALID"),
])
def test_validate_dimensions_rejects_unbound_missing_or_non_null_unknown(fault, code):
    from app.v2.contracts import DomainError
    from app.v2.rubric_research import bind_claims, validate_dimensions

    claim_id = bind_claims(claim_output(), [source()])[0]["claim_id"]
    dimensions = [dimension(index, claim_id) for index in range(1, 8)]
    if fault == "unbound_source":
        dimensions[0]["source_refs"] = ["claim:not-bound"]
    elif fault == "missing_unknown_rule":
        dimensions[0]["missing_conflict_unknown_rules"].pop("unknown")
    elif fault == "unknown_score_not_null":
        dimensions[0]["result_schema"]["unknown_score"] = 0
    elif fault == "schema_invalid":
        dimensions[0].pop("anchors")
    with pytest.raises(DomainError, match=code):
        validate_dimensions({"dimensions": dimensions}, "assessment", [{"claim_id": claim_id}])


def test_validate_dimensions_does_not_require_fixed_dimension_quota():
    from app.v2.rubric_research import bind_claims, validate_dimensions

    claim_id = bind_claims(claim_output(), [source()])[0]["claim_id"]
    dimensions = validate_dimensions(
        {"dimensions": [dimension(1, claim_id), dimension(2, claim_id)]},
        "assessment", [{"claim_id": claim_id}],
    )
    assert [row["dimension_id"] for row in dimensions] == ["d1", "d2"]


def test_public_research_topic_ignores_legacy_free_text_and_uses_structured_facets():
    from app.v2.rubric_research import _public_research_topic

    context = {
        "raw_input": "私有学习背景：账户编号 secret-learner-123，不能外发",
        "title": "也不能公开的任务标题",
        "domain_key": "math_logic",
        "branch": "linear_algebra",
        "requested_artifacts": ["exercise"],
    }
    topic = _public_research_topic({
        "topic": "私有原话不应进入检索",
        "queries": ["私有原话不应进入检索"],
        "public_topic": {"domain_key": "math_logic", "scene": "exercise", "requested_artifacts": ["exercise"]},
    }, context)
    assert "secret-learner-123" not in topic
    assert "私有" not in topic
    assert "也不能公开" not in topic
    assert "linear_algebra" not in topic
    assert "branch" not in topic
    assert "raw_input" not in topic
    assert "domain_key:math_logic" in topic
    assert "scene:exercise" in topic


def test_bind_profile_preserves_unknown_and_rejects_missing_or_unbound_samples():
    from app.v2.contracts import DomainError
    from app.v2.rubric_research import bind_profile

    materials = [imported_material()]
    rubric_ref = {"id": "rubric-1", "version": 1, "hash": "d" * 64}
    profile = bind_profile(valid_profile_output(), materials, rubric_ref)
    assert profile["profile_id"]
    assert profile["version"] == 1
    assert profile["rubric_ref"] == rubric_ref
    assert profile["calibration_status"] == "not_calibrated"
    assert profile["aggregate_score"] is None
    assert profile["threshold"] is None
    assert profile["difficulty_type"] == "observable_features_not_psychometric"
    assert profile["knowledge"]["unknown"] == ["材料未覆盖的学习收益"]
    for kind in ("knowledge", "proposition"):
        sample = profile[kind]["representative_samples"][0]
        raw = imported_material()["text"].encode()
        assert sample["source_hash"] == "b" * 64
        assert sample["start_byte"] == raw.find(sample["quote"].encode())
        assert sample["end_byte"] == sample["start_byte"] + len(sample["quote"].encode())

    with pytest.raises(DomainError, match="IMPORT_PROFILE_INCOMPLETE"):
        bind_profile({"knowledge": profile_part("导入材料代表样本必须逐字匹配。")}, materials, rubric_ref)
    missing_unknown = valid_profile_output()
    missing_unknown["knowledge"].pop("unknown")
    with pytest.raises(DomainError, match="IMPORT_PROFILE_INCOMPLETE"):
        bind_profile(missing_unknown, materials, rubric_ref)
    unbound = valid_profile_output()
    unbound["proposition"]["representative_samples"][0]["source_id"] = "missing"
    with pytest.raises(DomainError, match="IMPORT_PROFILE_SOURCE_UNBOUND"):
        bind_profile(unbound, materials, rubric_ref)
    repeated_material = {**imported_material(), "text": "重复 重复"}
    repeated = valid_profile_output()
    repeated["knowledge"]["representative_samples"][0]["quote"] = "重复"
    with pytest.raises(DomainError, match="IMPORT_PROFILE_SOURCE_UNBOUND"):
        bind_profile(repeated, [repeated_material], rubric_ref)


def _start_research_run(client, *, enabled):
    payload = TaskInput(
        title="研究契约",
        raw_input="synthetic offline evidence only",
        research_request={"enabled": enabled},
    )
    created = create_task(payload, str(uuid4()))
    confirmed = mutate_input(created["task_id"], Confirmation(
        expected_revision=created["input_revision"], context_hash=created["context_hash"]
    ), "confirm-" + str(uuid4()), confirmation=True)
    run = start_run(created["task_id"], StartRun(
        expected_revision=confirmed["input_revision"],
        confirmed_context_hash=confirmed["context_hash"],
        mode="demo",
    ), "start-" + str(uuid4()))
    return owner(client), created, run


def test_research_evidence_get_is_read_only(monkeypatch, client):
    import app.v2.public_sources as public_sources
    import app.v2.rubric_research as rubric_research
    from app.db import SessionLocal
    from app.models import ModelCallLog
    from app.v2.contracts import canonical, digest
    from app.v2.runtime import dump, job, load, write
    from app.v2.storage import BlobStore, CallDispatch

    def no_network_or_inference(*args, **kwargs):
        raise AssertionError("research evidence GET must not trigger network or inference")

    for target, name in ((public_sources, "read_source"), (public_sources, "search_sources"),
                         (rubric_research, "read_source"), (rubric_research, "search_sources"),
                         (rubric_research, "invoke")):
        monkeypatch.setattr(target, name, no_network_or_inference)

    headers, _, run = _start_research_run(client, enabled=True)
    with SessionLocal() as db:
        calls_before = db.query(CallDispatch).count()
        logs_before = db.query(ModelCallLog).count()
    response = client.get(f"/api/v2/research/{run['run_id']}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "running"
    assert body["inference_triggered"] is False
    assert body["result"] is None
    assert body["artifacts"] == {}

    result = {
        "rubric_id": "research-rubric-synthetic",
        "profile_id": "understanding-synthetic",
        "calibration_status": "not_calibrated",
        "aggregate_score": None,
        "threshold": None,
    }
    blob_hash = BlobStore().put(canonical(result))
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        state["research_artifacts"] = {"result": {"blob_hash": blob_hash, "hash": digest(result)}}
        obj.state_json = dump(state)
    response = client.get(f"/api/v2/research/{run['run_id']}", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"] == result
    assert body["inference_triggered"] is False

    _, _, no_request_run = _start_research_run(client, enabled=False)
    response = client.get(f"/api/v2/research/{no_request_run['run_id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_requested"

    with SessionLocal() as db:
        assert db.query(CallDispatch).count() == calls_before == 0
        assert db.query(ModelCallLog).count() == logs_before == 0

def test_profile_literal_correction_patches_only_failed_samples_and_then_binds():
    from app.v2.contracts import DomainError
    from app.v2.rubric_research import apply_profile_corrections, bind_profile

    materials = [imported_material()]
    rubric_ref = {"id": "rubric-1", "version": 1, "hash": "d" * 64}
    output = valid_profile_output()
    output["knowledge"]["representative_samples"][0]["quote"] = "paraphrased evidence"
    with pytest.raises(DomainError, match="IMPORT_PROFILE_SOURCE_UNBOUND") as caught:
        bind_profile(output, materials, rubric_ref)
    invalid = caught.value.details["invalid_samples"]
    assert invalid == [{"kind": "knowledge", "index": 0, "source_id": "imported-1",
                        "quote": "paraphrased evidence", "occurrences": 0,
                        "reason": "SOURCE_ID_OR_UNIQUE_LITERAL_QUOTE_INVALID"}]
    corrected = apply_profile_corrections(output, {"knowledge": [{"index": 0, "source_id": "imported-1",
        "quote": "导入材料代表样本必须逐字匹配。"}]}, invalid)
    assert corrected["knowledge"]["representative_samples"][0]["interpretation"] == output["knowledge"]["representative_samples"][0]["interpretation"]
    assert corrected["proposition"] == output["proposition"]
    assert bind_profile(corrected, materials, rubric_ref)["knowledge"]["representative_samples"][0]["quote_hash"]

    with pytest.raises(DomainError, match="IMPORT_PROFILE_CORRECTION_INCOMPLETE"):
        apply_profile_corrections(output, {}, invalid)
    with pytest.raises(DomainError, match="IMPORT_PROFILE_CORRECTION_IDENTITY_MISMATCH"):
        apply_profile_corrections(output, {"knowledge": [{"index": 1, "source_id": "imported-1",
            "quote": "导入材料代表样本必须逐字匹配。"}]}, invalid)


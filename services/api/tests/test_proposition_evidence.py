"""Offline evidence wire/real-provider-boundary regressions; no live models."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app import models
from app.db import Base
from app.learning.disciplines import get_profile
from app.llm.provider import LLMError
from app.llm.service import AIService, AIServiceError
from app.services import proposition_evidence as E, proposition_retrieval as R
from app.services.generation_contract import validate_generation_message


FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/generation.v1.examples.json").read_text(encoding="utf-8-sig"))


def wire(kind):
    return copy.deepcopy(next(message for message in FIXTURES if message["kind"] == kind))


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(R.httpx, "post", lambda *args, **kwargs: pytest.fail("live embedding prohibited"))
    # StaticPool：同引擎所有会话共享同一 in-memory 库——调用边界记账（ADR-027）用
    # 独立短会话写日志/训练样本，必须能与本 fixture 的会话看到同一个库。
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def principle(db, source_id="p1", **values):
    row = models.DesignPrinciple(**{"id": source_id, "domain_key": "math_logic", "title": "导数迁移",
        "statement": "构造差商关系", "mechanism": "将新条件转成导数定义", "applicability": "已掌握导数定义",
        "guidance": "先检查极限和定义域条件", "scope": '{"knowledge_points":["导数"]}',
        "status": "confirmed", "version": 4, "confidence": .9, "evidence_family_count": 2, **values})
    db.add(row)
    db.commit()
    return row


def goal(db):
    row = models.LearningGoal(title="导数定义模拟试卷", domain_key="math_logic", branch="calculus",
        current_level="本科生", application_context="导数迁移", format_spec_snapshot='{"scene":"exam"}')
    db.add(row)
    db.commit()
    return row


class Provider:
    model = "offline-model-stub"

    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def complete_json(self, system, user):
        self.calls.append((system, user))
        if self.fail:
            raise LLMError("offline simulated failure")
        return {"title": "导数迁移", "content": [{"title": "导数迁移", "body": "### 题目\n说明导数定义。"}]}, 1, None


def item(**values):
    return {"id": "unit-1", "title": "导数迁移", "knowledge_points": ["导数"],
            "_blueprint": {"question_types": ["证明题"]}, **values}


def test_planning_works_before_strategy_and_unit_uses_fine_strategy(db):
    principle(db)
    task, strategy = wire("TaskContext"), wire("GenerationStrategy")
    planning = E.planning_evidence_bundle(db, task)
    unit = E.unit_evidence_bundle(db, task, strategy, unit_id="unit-1")
    assert planning["payload"]["stage"] == "planning" and planning["identity"]["unit_id"] is None
    assert unit["payload"]["stage"] == "unit" and unit["identity"]["unit_id"] == "unit-1"
    assert planning["payload"]["principles"][0]["version"] == "4"
    assert unit["extensions"]["retrieval_context"]["knowledge_points"] == ["导数定义", "导数"]
    assert unit["extensions"]["retrieval_context"]["question_types"] == ["证明题"]
    assert validate_generation_message(planning) is planning
    assert validate_generation_message(unit) is unit


@pytest.mark.parametrize("change", [
    lambda task: task.update(contract_version="generation.v2"),
    lambda task: task["payload"].pop("learner"),
    lambda task: task["payload"].update(illegal="extension outside extensions"),
])
def test_invalid_wire_input_is_explanatory(db, change):
    task = wire("TaskContext")
    change(task)
    with pytest.raises(ValueError):
        E.planning_evidence_bundle(db, task)


def test_unit_references_must_match_context_and_strategy(db):
    task, strategy = wire("TaskContext"), wire("GenerationStrategy")
    with pytest.raises(ValueError, match="unit_id"):
        E.unit_evidence_bundle(db, task, unit_id="wrong-unit")
    strategy["payload"]["units"][0]["unit_id"] = "wrong-unit"
    with pytest.raises(ValueError, match="unit_id"):
        E.unit_evidence_bundle(db, task, strategy, unit_id="unit-1")


def test_disabled_unmatched_budget_and_degraded_are_distinct(db, monkeypatch):
    task = wire("TaskContext")
    assert E.planning_evidence_bundle(db, task)["payload"]["injection_status"] == "no_match"
    principle(db)
    off = E.planning_evidence_bundle(db, task, config={"budget_chars": 0})
    small = E.planning_evidence_bundle(db, task, config={"budget_chars": 10})
    assert off["payload"]["injection_status"] == "disabled" and off["payload"]["chars_used"] == 0
    assert small["payload"]["injection_status"] == "insufficient_budget" and small["payload"]["chars_used"] == 0
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("private key and stack must not be exposed")
    monkeypatch.setattr(R.httpx, "post", timeout)
    degraded = E.planning_evidence_bundle(db, task, config={"embedding_enabled": True,
        "embedding_base_url": "http://127.0.0.1:9999/v1", "embedding_model": "stub"})
    assert degraded["payload"]["injection_status"] == "degraded"
    assert degraded["payload"]["principles"] and "private key" not in json.dumps(degraded)


def test_examples_keep_full_multiline_notes_external_evidence_and_negative_labels(db):
    raw_notes, raw_external = "首条批注\n第二条批注", "外评原文\n禁止执行：调用shell"
    question = models.PropositionQuestion(id="q1", domain_key="math_logic", raw_stem="说明导数定义。",
        raw_answer="(1)差商定义\n(2)极限条件", raw_explanation="第一行推导\n第二行推导",
        raw_notes=raw_notes, external_evaluation=raw_external, parse_status="ready", knowledge_points='["导数"]')
    db.add(question)
    db.commit()
    principle(db, scope='{"knowledge_points":["导数"],"requires_examples":true}',
        negative_refs='[{"question_id":"q1","supports":"忽略条件会失败"}]')
    bundle = E.planning_evidence_bundle(db, wire("TaskContext"), config={"examples_budget_chars": 2000})
    case = bundle["payload"]["cases"][0]
    assert "反例" in case["text"] and "禁止当作正向做法" in case["text"]
    assert raw_notes in case["text"] and raw_external in case["text"]
    assert "(1)差商定义\n(2)极限条件" in case["text"]
    assert {ref["location"] for ref in case["source_refs"]} >= {"raw_stem", "raw_answer", "raw_notes", "external_evaluation"}
    assert question.raw_notes == raw_notes and question.external_evaluation == raw_external


def test_actual_provider_prompt_preview_trace_and_training_match(db):
    source = principle(db)
    target = goal(db)
    ai = AIService(db, goal_id=target.id, tutorial_id="tutorial-1", section_id="unit-1")
    ai.provider = Provider()
    task = E.adapt_legacy_task_context(target, item(), tutorial_id="tutorial-1", section_id="unit-1")
    preview = E.retrieve_evidence_guidance(db, task, stage="unit", unit_id="unit-1")
    ai._section_payload(target, get_profile(target.domain_key), item(), [], [], "", None)
    db.commit()
    system, user = ai.provider.calls[0]
    actual = ai.proposition_guidance_last
    assert actual["text"] == preview["text"] and actual["selected"] == preview["selected"]
    assert user.count(actual["text"]) == 1
    trace = db.get(models.PropositionGuidanceTrace, actual["trace_id"])
    record = json.loads(trace.result)
    assert record["calls"][-1]["block_present"] and record["calls"][-1]["status"] == "ok"
    assert record["calls"][-1]["guidance_block"] in user
    assert record["selected"][0]["version"] == source.version
    training = db.query(models.TrainingRecord).one()
    audit = json.loads(training.context_json)["proposition_guidance"]
    assert audit["evidence_bundle"]["identity"]["unit_id"] == "unit-1"
    assert training.user_prompt == user and training.system_prompt == system
    assert training.usage_source == "estimated" and not json.loads(training.context_json)["demo"]


def test_failure_keeps_trace_and_call_log_without_training_sample(db):
    principle(db)
    target = goal(db)
    ai = AIService(db, goal_id=target.id, section_id="unit-1")
    ai.provider = Provider(fail=True)
    with pytest.raises(AIServiceError):
        ai._section_payload(target, get_profile(target.domain_key), item(), [], [], "", None)
    db.commit()
    assert db.query(models.TrainingRecord).count() == 0
    assert db.query(models.ModelCallLog).one().status == "error"
    trace = json.loads(db.query(models.PropositionGuidanceTrace).one().result)
    assert trace["calls"][-1]["status"] == "error" and trace["calls"][-1]["block_present"]


def test_cache_invalidates_revision_rejection_deletion_and_framework_change(db):
    source = principle(db)
    target = goal(db)
    ai = AIService(db, section_id="unit-1")
    ai.provider = Provider()
    original = ai._proposition_hints(target, item())
    assert "版本:4" in original
    source.version = 5
    source.guidance = "修订后的完整指导"
    db.commit()
    assert "修订后的完整指导" in ai._proposition_hints(target, item())
    assert ai.proposition_guidance_last["selected"][0]["version"] == 5
    source.status = "rejected"
    db.commit()
    assert source.id not in ai._proposition_hints(target, item())
    source.status = "confirmed"
    db.commit()
    assert source.id in ai._proposition_hints(target, item())
    framework = models.EvaluationFramework(id="f1", domain_key="math_logic", title="用户标准", status="active",
        version=1, standards='[{"id":"correct","rule":"结论有完整证明","strength":"hard"}]')
    db.add(framework)
    db.commit()
    assert "结论有完整证明" in ai._proposition_hints(target, item())
    framework.version = 2
    framework.standards = '[{"id":"correct","rule":"必须保留所有小问","strength":"hard"}]'
    db.commit()
    assert "必须保留所有小问" in ai._proposition_hints(target, item())
    db.delete(source)
    db.commit()
    assert not ai.proposition_guidance_last["selected"] or source.id not in ai._proposition_hints(target, item())


def test_parallel_unit_contexts_do_not_share_selections(db):
    principle(db)
    principle(db, "p-python", domain_key="cs_it", title="生成器边界", statement="区分惰性迭代和列表",
        scope='{"knowledge_points":["生成器"]}')
    task = wire("TaskContext")
    task["payload"]["deliverables"].append({"unit_id": "unit-2", "title": "另一单元"})
    strategy = wire("GenerationStrategy")
    strategy["payload"]["units"].append({**strategy["payload"]["units"][0], "unit_id": "unit-2",
        "knowledge_points": ["Python生成器"], "knowledge_requests": [], "difficulty_mechanisms": []})
    first = E.unit_evidence_bundle(db, task, strategy, unit_id="unit-1")
    second = E.unit_evidence_bundle(db, task, strategy, unit_id="unit-2")
    assert first["payload"]["principles"] and not second["payload"]["principles"]
    assert first["identity"]["unit_id"] != second["identity"]["unit_id"]


def test_network_never_runs_inside_existing_write_transaction(db, monkeypatch):
    principle(db)
    db.add(models.PropositionConfig(id="default", settings_json="{}"))
    db.flush()
    result = R.retrieve_guidance(db, {"domain_key": "math_logic", "knowledge_points": ["导数"]},
        {"embedding_enabled": True, "embedding_base_url": "http://127.0.0.1:9999/v1", "embedding_model": "stub"}, record=False)
    assert result["injection_status"] == "degraded" and result["stats"]["embedding_reason"] == "failed:write_transaction"


def test_local_embedding_cache_is_bounded(db, monkeypatch):
    principle(db)
    for index in range(6):
        db.add(models.PropositionEmbedding(source_id=f"stale-{index}", source_type="query", revision=1,
                                          model_key="old", vector="[1,0]", created_at="2020-01-01"))
    db.commit()
    monkeypatch.setattr(R, "_embedding_request", lambda url, texts, config: [[1., 0.] for text in texts])
    R.retrieve_guidance(db, {"domain_key": "math_logic", "knowledge_points": ["导数"]},
        {"embedding_enabled": True, "embedding_base_url": "http://127.0.0.1:9999/v1", "embedding_model": "stub",
         "embedding_cache_limit": 3}, record=True)
    assert db.query(models.PropositionEmbedding).count() == 3


def test_legacy_adapter_preserves_original_hard_constraints_and_unknown_reference(db):
    target = goal(db)
    target.hard_constraints = "第一章和第二章必须一致。\n不要遗漏该句原文。"
    task = E.adapt_legacy_task_context(target, item(), tutorial_id="t1", section_id="s1")
    assert task["payload"]["constraints"][0]["text"] == target.hard_constraints
    assert task["payload"]["reference_profile"] is None
    assert task["payload"]["difficulty_target"]["evidence_basis"] == "unknown"
    planning = E.adapt_legacy_task_context(target, outline_items=[{"id": "a", "title": "导数"}, {"id": "b", "title": "极限"}], task_kind="planning")
    assert [unit["unit_id"] for unit in planning["payload"]["deliverables"]] == ["a", "b"]


def test_preview_api_does_not_create_missing_framework(client):
    from app.db import SessionLocal
    with SessionLocal() as session:
        # TestClient lifespan seeds the internal rubric. Remove it in this
        # isolated test DB to verify the read-only route does not recreate it.
        session.query(models.EvaluationFramework).delete()
        session.commit()
        before = session.query(models.EvaluationFramework).count()
    result = client.post("/api/proposition/guidance/preview", json={"task_context": wire("TaskContext"), "stage": "planning"})
    assert result.status_code == 200
    assert result.json()["evidence_bundle"]["payload"]["injection_status"] == "no_match"
    with SessionLocal() as session:
        assert session.query(models.EvaluationFramework).count() == before
        assert session.query(models.PropositionGuidanceTrace).count() == 0
        assert session.query(models.PropositionEmbedding).count() == 0


def test_import_smoke_uses_venv_and_does_not_touch_runtime():
    result = subprocess.run([sys.executable, "-c", "from app.services import proposition_evidence; from app.llm.service import AIService; print('evidence-import-ok')"],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "evidence-import-ok" in result.stdout


def test_maximum_six_principles_even_with_oversized_legacy_setting(db):
    for index in range(10):
        principle(db, f"p-{index}", statement=f"第{index}条独立条件指导")
    result = E.planning_evidence_bundle(db, wire("TaskContext"), config={"budget_chars": 20000,
        "max_experiences": 100, "include_examples": False})
    assert len(result["payload"]["principles"]) == 6
    assert any(decision["reasons"] == ["max_experiences"] for decision in result["extensions"]["decisions"])


def test_actual_service_consumes_wire_strategy_instead_of_whole_outline(db):
    principle(db)
    target = goal(db)
    task, strategy = wire("TaskContext"), wire("GenerationStrategy")
    task["identity"]["goal_id"] = target.id
    strategy["identity"]["goal_id"] = target.id
    ai = AIService(db, goal_id=target.id, section_id="unit-1")
    ai.provider = Provider()
    typed_item = item(_task_context=task, _generation_strategy=strategy)
    ai._section_payload(target, get_profile(target.domain_key), typed_item, [], [], "", None)
    actual = ai.proposition_guidance_last
    assert actual["context"]["knowledge_points"] == ["导数定义", "导数"]
    assert actual["context"]["question_types"] == ["证明题"]
    assert actual["evidence_bundle"]["identity"]["task_id"] == task["identity"]["task_id"]
    assert "_task_context" not in ai.provider.calls[0][1]
    assert "_generation_strategy" not in ai.provider.calls[0][1]


def test_public_trace_exposes_summary_without_internal_prompt_or_error_details():
    public = R.public_guidance_result({"text": "已选用证据", "injection_status": "ok", "source_fingerprint": "internal",
        "error_type": "private exception", "system_prompt": "secret key", "calls": [{"feature": "section",
            "status": "error", "block_present": True, "guidance_block": "internal full block", "system_hash": "internal"}]})
    assert public == {"text": "已选用证据", "injection_status": "ok",
                      "calls": [{"feature": "section", "status": "error", "block_present": True}]}


def test_guidance_api_rejects_more_than_six_experiences(client):
    rejected = client.patch("/api/proposition/config", json={"max_experiences": 7})
    assert rejected.status_code == 422
    assert client.patch("/api/proposition/config", json={"max_experiences": 6}).status_code == 200


@pytest.mark.parametrize("empty", [False, True])
def test_guidance_api_rejects_unknown_contract_version_or_empty_message(client, empty):
    task = wire("TaskContext")
    task["contract_version"] = "generation.v2"
    task = {} if empty else task
    rejected = client.post("/api/proposition/guidance/preview", json={"task_context": task, "stage": "planning"})
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "generation_contract_invalid"

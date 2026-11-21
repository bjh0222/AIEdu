"""Cross-module proposition contracts; all LLM/network operations are test doubles."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, inspect

from app import db as database, models
from app.llm.provider import OpenAICompatibleProvider
from app.llm.service import AIService
from app.schemas import PropositionBatchImport
from app.services import proposition_knowledge as K
from app.services.utils import jdump, jload


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Contract tests must never call a real model/network")

    monkeypatch.setattr(httpx, "post", blocked)
    monkeypatch.setattr(httpx, "get", blocked)
    monkeypatch.setattr(OpenAICompatibleProvider, "_post", blocked)


def payload(markdown, filename="contracts.md", **kwargs):
    return PropositionBatchImport(filename=filename, markdown=markdown, auto_analyse=False, **kwargs)


def mixed_source(size=40):
    pieces = ["# 混合题目契约\n"]
    for i in range(1, size + 1):
        kind = (i - 1) % 8
        pieces.append(f"## 第{i}题\n")
        if kind == 2:
            pieces.append("\n")
        elif kind == 3:
            pieces.append("根据未转写图片作答。![图](missing.png)\n评注：不能推测图片。\n")
        elif kind == 1:
            pieces.append("说明 Python 函数返回值。\n解答：\n1. 先定义函数。\n```python\n## 第999题\nprint(1)\n```\n")
        elif kind == 5:
            pieces.append("解释列表与元组的区别。\n外部评价：EXTERNAL 不得覆盖。\n")
        elif kind == 6:
            pieces.append("证明可导函数连续。\n评注：" + "逐字证据" * 500 + "NOTE_END\n")
        else:
            pieces.append(f"求函数 x^2 在 x={i} 处的导数。\n评注：RAW_NOTE_{i}\n")
    return "".join(pieces)


def seed_ready_batch(db, size=37):
    result = K.import_batch(db, payload("".join(f"## 第{i + 1}题\n求 x^{i + 2} 的导数。\n外部评价：EXTERNAL_{i}\n" for i in range(size))))
    return result


def fake_ai(calls, on_call=None, fail_feature=None):
    lock = threading.Lock()

    class FakeAI:
        is_demo = False
        provider = SimpleNamespace(model="contract-mock")

        def __init__(self, **kwargs):
            pass

        def _real_json(self, feature, system, user, required, *args, **kwargs):
            with lock:
                calls.append((feature, system, user))
            if on_call:
                on_call(feature)
            if feature == fail_feature:
                raise RuntimeError("mock failure")
            if feature == "proposition_blind_solve":
                assert "EXTERNAL_" not in user
                return {"answer": "saved-blind-answer", "solvable": True, "confidence": .95, "verification": "静态推导"}
            if feature == "proposition_experience":
                return {"principles": []}
            return {"correctness": {"status": "supported", "reason": "独立推导"},
                    "difficulty": {"level": 2}, "quality": {"strengths": ["概念辨析"]},
                    "structure": {"mechanism": "导数定义"}, "recommendation": "consider",
                    "confidence": .95, "knowledge_points": ["导数"], "question_type": "计算题",
                    "annotation_comparison": [], "uncertainties": []}

    return FakeAI


def test_40_mixed_questions_keep_all_evidence_and_report_actionable_counts(client):
    source = mixed_source()
    with database.SessionLocal() as db:
        result = K.import_batch(db, payload(source))
        assert result["source_text"] == source
        assert len(result["questions"]) == 40
        assert result["counts"]["total"] == 40 and result["counts"]["parsed"] == 30
        assert result["counts"]["parse_failed"] == 10
        ready = [db.get(models.PropositionQuestion, q["id"]) for q in result["questions"] if q["parse_status"] == "ready"]
        for index, row in enumerate(ready):
            row.analysis_status = "analysed" if index < 5 else "demo" if index < 8 else "failed" if index < 12 else "pending"
        db.commit()
        batch_id = result["id"]
    detail = client.get(f"/api/proposition/batches/{batch_id}").json()
    assert detail["counts"] == {"total": 40, "parsed": 30, "parse_failed": 10,
                                "analysed": 5, "analysis_failed": 4, "pending": 18, "demo": 3}
    assert [q["question_number"] for q in detail["questions"]] == [str(i) for i in range(1, 41)]
    for q in detail["questions"]:
        assert q["raw_text"] == "".join(source.splitlines(keepends=True)[q["source_line_start"] - 1:q["source_line_end"]])
    assert "NOTE_END" in detail["questions"][6]["raw_notes"]
    assert "EXTERNAL" in detail["questions"][5]["external_evaluation"]
    overview = client.get("/api/proposition/overview").json()["counts"]
    assert overview["questions"] == 40 and overview["parse_failed"] == 10 and overview["evaluated"] == 5


def test_batch_pagination_keeps_total_and_has_no_duplicate_or_missing_rows(client):
    with database.SessionLocal() as db:
        rows = [models.PropositionBatch(filename=f"batch-{i}.md", source_hash=f"hash-{i}",
                                        created_at=f"2026-01-01T00:00:{i:02d}+00:00") for i in range(37)]
        db.add_all(rows)
        db.commit()
        expected = [row.id for row in reversed(rows)]
    actual = []
    for offset in range(0, 42, 7):
        page = client.get("/api/proposition/batches", params={"limit": 7, "offset": offset}).json()
        assert page["total"] == 37
        assert all("source_text" not in row and "questions" not in row for row in page["items"])
        actual.extend(row["id"] for row in page["items"])
    assert actual == expected and len(set(actual)) == 37
    assert client.get("/api/proposition/batches", params={"offset": 100}).json()["items"] == []


def test_question_pagination_is_batch_scoped_and_counts_beyond_first_page(client):
    with database.SessionLocal() as db:
        first = K.import_batch(db, payload(mixed_source()))
        second = K.import_batch(db, payload("## 第1题\n另一个批次的单题。", "other.md"))
    seen = []
    for offset in range(0, 40, 9):
        page = client.get("/api/proposition/questions", params={"batch_id": first["id"], "limit": 9, "offset": offset}).json()
        assert page["total"] == 40
        assert all(q["batch_id"] == first["id"] for q in page["items"])
        seen.extend(q["id"] for q in page["items"])
    assert set(seen) == {q["id"] for q in first["questions"]} and len(seen) == 40
    assert client.get("/api/proposition/questions").json()["total"] == 41
    assert client.get("/api/proposition/questions", params={"batch_id": second["id"]}).json()["total"] == 1


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
def test_pagination_invalid_boundaries_are_rejected(client, params):
    assert client.get("/api/proposition/batches", params=params).status_code == 422
    assert client.get("/api/proposition/questions", params=params).status_code == 422


def test_37_question_job_budget_counts_every_actual_call_and_defers_remainder(client):
    calls = []
    with database.SessionLocal() as db:
        result = seed_ready_batch(db)
        K.save_config(db, {"max_calls_per_job": 5, "analysis_workers": 1, "max_retries": 0})
        job = K.start_analysis(db, result["id"], background=False)
    K._run_job(job["id"], ai_factory=fake_ai(calls))
    with database.SessionLocal() as db:
        final = K.job_dict(db.get(models.PropositionAnalysisJob, job["id"]))
        assert final["status"] == "partial"
        assert final["counts"]["read"] == 37 and final["counts"]["calls"] == len(calls) == 5
        assert final["counts"]["analysed"] == 2 and final["counts"]["pending"] == 35
        assert final["counts"]["failed"] == 0
        assert db.get(models.PropositionBatch, result["id"]).status == "partial"
        assert db.query(models.PropositionQuestion).filter_by(batch_id=result["id"], analysis_status="analysed").count() == 2
        assert any(jload(e.blind_solution, {}) for e in db.query(models.PropositionEvaluation).filter_by(status="deferred"))
        assert all("EXTERNAL_" in q.external_evaluation for q in db.query(models.PropositionQuestion))


def test_mixed_batch_job_reports_parse_failures_even_when_all_ready_questions_complete(client):
    calls = []
    with database.SessionLocal() as db:
        result = K.import_batch(db, payload(mixed_source()))
        K.save_config(db, {"max_calls_per_job": 200, "analysis_workers": 1, "max_retries": 0})
        job = K.start_analysis(db, result["id"], background=False)
    K._run_job(job["id"], ai_factory=fake_ai(calls))
    with database.SessionLocal() as db:
        final = K.job_dict(db.get(models.PropositionAnalysisJob, job["id"]))
        assert final["counts"]["total"] == 40 and final["counts"]["parse_failed"] == 10
        assert final["counts"]["analysed"] == 30 and final["counts"]["pending"] == 0
        assert final["counts"]["failed"] == 0
        assert final["counts"]["calls"] == len(calls)
        assert final["status"] == "partial" and db.get(models.PropositionBatch, result["id"]).status == "partial"


@pytest.mark.parametrize("partial_status", ["deferred", "failed"])
def test_nonforce_resume_reuses_saved_blind_and_retains_old_evaluation(client, partial_status):
    first_calls = []
    with database.SessionLocal() as db:
        result = seed_ready_batch(db, 1)
        qid = result["questions"][0]["id"]
        fid = K.active_framework(db, "math_logic").id
    if partial_status == "deferred":
        budget = K._CallBudget(1)
        factory = lambda **kwargs: budget.wrap(fake_ai(first_calls)(**kwargs))
    else:
        factory = fake_ai(first_calls, fail_feature="proposition_evaluate")
    initial = K.analyse_question(qid, fid, ai_factory=factory)
    assert initial["status"] == ("pending" if partial_status == "deferred" else "failed")
    with database.SessionLocal() as db:
        previous = db.query(models.PropositionEvaluation).one()
        previous_id, saved = previous.id, previous.blind_solution
        assert previous.status == partial_status and saved != "{}"
    resume_calls = []
    assert K.analyse_question(qid, fid, ai_factory=fake_ai(resume_calls))["status"] == "analysed"
    assert [feature for feature, *_ in resume_calls] == ["proposition_evaluate"]
    with database.SessionLocal() as db:
        assert db.get(models.PropositionEvaluation, previous_id).blind_solution == saved
        assert db.get(models.PropositionEvaluation, previous_id).status == partial_status
        assert db.query(models.PropositionEvaluation).filter_by(status="done").one().blind_solution == saved
        assert db.query(models.PropositionEvaluation).count() == 2


def test_force_retry_performs_a_new_blind_call(client):
    calls = []
    with database.SessionLocal() as db:
        result = seed_ready_batch(db, 1)
        qid = result["questions"][0]["id"]
        fid = K.active_framework(db, "math_logic").id
    factory = fake_ai(calls, fail_feature="proposition_evaluate")
    assert K.analyse_question(qid, fid, ai_factory=factory)["status"] == "failed"
    fresh_calls = []
    assert K.analyse_question(qid, fid, force=True, ai_factory=fake_ai(fresh_calls))["status"] == "analysed"
    assert [feature for feature, *_ in fresh_calls] == ["proposition_blind_solve", "proposition_evaluate"]


def test_cancellation_preserves_pending_counts_and_stops_second_stage_calls(client, monkeypatch):
    scheduled, entered, release = threading.Event(), threading.Event(), threading.Event()
    calls = []
    submitted = []

    class TrackingExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            submitted.append(future)
            if len(submitted) == 37:
                scheduled.set()
            return future

    def on_call(feature):
        if feature == "proposition_blind_solve":
            entered.set()
            assert release.wait(5), "test failed to release in-flight call"

    monkeypatch.setattr(K, "ThreadPoolExecutor", TrackingExecutor)
    with database.SessionLocal() as db:
        result = seed_ready_batch(db)
        K.save_config(db, {"analysis_workers": 1, "max_retries": 0})
        job = K.start_analysis(db, result["id"], background=False)
    worker = threading.Thread(target=K._run_job, args=(job["id"], fake_ai(calls, on_call=on_call)), daemon=True)
    worker.start()
    try:
        assert entered.wait(5) and scheduled.wait(5)
        cancelled = client.post(f"/api/proposition/jobs/{job['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive()
    with database.SessionLocal() as db:
        final = K.job_dict(db.get(models.PropositionAnalysisJob, job["id"]))
        assert final["status"] == "cancelled" and final["counts"]["calls"] == len(calls) == 1
        assert final["counts"]["pending"] == 37 and final["counts"]["failed"] == 0
        assert final["counts"]["analysed"] == 0
        assert db.get(models.PropositionBatch, result["id"]).status == "cancelled"
        assert db.query(models.PropositionEvaluation).filter_by(status="done").count() == 0
        assert any(jload(e.blind_solution, {}).get("answer") == "saved-blind-answer" for e in db.query(models.PropositionEvaluation))
    assert [feature for feature, *_ in calls] == ["proposition_blind_solve"]


def test_restart_sweep_preserves_completed_rows_and_resume_skips_them(client):
    calls = []
    with database.SessionLocal() as db:
        result = seed_ready_batch(db, 2)
        qid, pending_id = [q["id"] for q in result["questions"]]
        fid = K.active_framework(db, "math_logic").id
    assert K.analyse_question(qid, fid, ai_factory=fake_ai(calls))["status"] == "analysed"
    with database.SessionLocal() as db:
        saved = db.query(models.PropositionEvaluation).filter_by(question_id=qid).one()
        saved_id, saved_report = saved.id, saved.report
        old_job = K.start_analysis(db, result["id"], background=False)
        job = db.get(models.PropositionAnalysisJob, old_job["id"])
        job.status = "running"
        db.get(models.PropositionQuestion, pending_id).analysis_status = "running"
        db.commit()
        K.sweep_stale_analyses(db)
        db.expire_all()
        assert db.get(models.PropositionAnalysisJob, job.id).status == "partial"
        assert db.get(models.PropositionQuestion, pending_id).analysis_status == "pending"
        assert db.get(models.PropositionQuestion, qid).analysis_status == "analysed"
        new_job = K.start_analysis(db, result["id"], background=False)
        assert new_job["id"] != old_job["id"]
    new_calls = []
    K._run_job(new_job["id"], ai_factory=fake_ai(new_calls))
    with database.SessionLocal() as db:
        final = K.job_dict(db.get(models.PropositionAnalysisJob, new_job["id"]))
        assert final["counts"]["skipped"] == 1 and final["counts"]["analysed"] == 1
        assert db.query(models.PropositionEvaluation).filter_by(question_id=qid).count() == 1
        assert db.get(models.PropositionEvaluation, saved_id).report == saved_report
    assert [feature for feature, *_ in new_calls].count("proposition_blind_solve") == 1


def test_legacy_schema_columns_and_indexes_migrate_twice_preserving_evidence(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE design_principles (id TEXT PRIMARY KEY, domain_key TEXT, statement TEXT, status TEXT, evidence_count INTEGER, created_at TEXT)")
            conn.exec_driver_sql("INSERT INTO design_principles VALUES ('old','math_logic','原经验全文','confirmed',7,'2026-01-01')")
            conn.exec_driver_sql("CREATE TABLE quality_records (id TEXT PRIMARY KEY, detail TEXT)")
            conn.exec_driver_sql("INSERT INTO quality_records VALUES ('quality-old','历史原始缺陷')")
            conn.exec_driver_sql("CREATE TABLE evaluation_frameworks (id TEXT PRIMARY KEY, source_text TEXT)")
            conn.exec_driver_sql("INSERT INTO evaluation_frameworks VALUES ('framework-old','逐字标准原文')")
            conn.exec_driver_sql("CREATE TABLE format_specs (id TEXT PRIMARY KEY)")
            conn.exec_driver_sql("CREATE TABLE learning_goals (id TEXT PRIMARY KEY, title TEXT, domain_key TEXT)")
            conn.exec_driver_sql("INSERT INTO learning_goals VALUES ('goal-old','历史目标','computer_cli')")
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "_is_sqlite", True)
        database._migrate_sqlite_columns()
        database._migrate_sqlite_columns()
        database._migrate_sqlite_indexes()
        database._migrate_sqlite_indexes()
        schema = inspect(engine)
        for model in (models.DesignPrinciple, models.QualityRecord, models.EvaluationFramework, models.LearningGoal):
            assert {c["name"] for c in schema.get_columns(model.__tablename__)} == {c.name for c in model.__table__.columns}
            assert {i["name"] for i in schema.get_indexes(model.__tablename__)} == {i.name for i in model.__table__.indexes}
        with engine.connect() as conn:
            assert tuple(conn.exec_driver_sql("SELECT title,domain_key,branch FROM learning_goals WHERE id='goal-old'").one()) == ("历史目标", "computer_cli", "")
            row = conn.exec_driver_sql("SELECT statement,status,version,scope,evidence_family_count FROM design_principles WHERE id='old'").one()
            assert tuple(row) == ("原经验全文", "confirmed", 1, "{}", 0)
            assert conn.exec_driver_sql("SELECT detail FROM quality_records WHERE id='quality-old'").scalar() == "历史原始缺陷"
            assert conn.exec_driver_sql("SELECT source_text FROM evaluation_frameworks WHERE id='framework-old'").scalar() == "逐字标准原文"
            index_sql = conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE name='uq_format_specs_default'").scalar()
            assert index_sql and "WHERE" in index_sql.upper() and "is_default" in index_sql
            plan = conn.exec_driver_sql("EXPLAIN QUERY PLAN SELECT id FROM design_principles WHERE domain_key='math_logic' AND status='confirmed' ORDER BY evidence_count DESC,created_at").all()
            assert any("ix_principle_rank" in str(line) for line in plan)
    finally:
        engine.dispose()


def test_legacy_stem_completeness_requires_explicit_source_complete_marker(client):
    with database.SessionLocal() as db:
        unknown = models.ItemModel(domain_key="math_logic", human_note="原始历史批注",
                                   radicals=jdump({"stem_sample": "看似完整但历史是否截断无法确认的题面。"}))
        complete = models.ItemModel(domain_key="math_logic", human_note="已知完整原始批注",
                                    radicals=jdump({"stem_sample": "证明可导函数必连续。", "source_complete": True}))
        db.add_all([unknown, complete])
        db.commit()
        assert K.migrate_legacy_evidence(db) == 2 and K.migrate_legacy_evidence(db) == 0
        unknown_q = db.query(models.PropositionQuestion).filter_by(item_model_id=unknown.id).one()
        complete_q = db.query(models.PropositionQuestion).filter_by(item_model_id=complete.id).one()
        assert unknown_q.parse_status == "failed" and unknown_q.raw_notes == "原始历史批注"
        assert unknown_q.error_message and unknown_q.raw_stem == "看似完整但历史是否截断无法确认的题面。"
        assert complete_q.parse_status == "ready" and complete_q.raw_stem == "证明可导函数必连续。"
        assert complete_q.raw_notes == "已知完整原始批注"
        assert db.query(models.ItemModel).count() == 2


def test_framework_quote_locations_and_conflicts_prevent_activation(client):
    source = "# 用户规范\n\n选择题答案必须唯一。\n困难不等于高质量。\n"

    class FrameworkAI:
        is_demo = False

        def _real_json(self, *args, **kwargs):
            return {"standards": [
                {"id": "unique", "rule": "答案唯一", "source_quote": "选择题答案必须唯一。", "scope": {"all_items": True}, "strength": "hard", "priority": 3, "evidence_required": "唯一答案和逐项判据"},
                {"id": "quality", "rule": "分别判断难度与质量", "source_quote": "困难不等于高质量。", "scope": {"all_items": True}, "strength": "core", "priority": 2, "evidence_required": "难度与质量分别记录"},
                {"id": "invented", "rule": "不得补造", "source_quote": "原文中没有的句子"},
            ], "conflicts": ["需用户裁定的互斥要求"], "relations": []}

    with database.SessionLocal() as db:
        old = K.active_framework(db, "math_logic")
        result = K.import_framework(db, "有冲突的标准", "math_logic", source, FrameworkAI())
        standards = result["standards"]
        assert [s["id"] for s in standards] == ["unique", "quality"]
        assert [s["source_ref"] for s in standards] == ["line:3", "line:4"]
        assert all(s["source_quote"] in source for s in standards)
        assert result["source_text"] == source and result["status"] == "proposed"
        assert result["carrier_scope"] == "v2_math_explicit_binding"
        assert result["consumers"] == ["legacy_proposition", "v2_math_explicit"]
        with pytest.raises(HTTPException) as error:
            K.activate_framework(db, result["id"])
        assert error.value.status_code == 409
        db.expire_all()
        assert db.get(models.EvaluationFramework, result["id"]).status == "proposed"
        assert db.get(models.EvaluationFramework, old.id).status == "active"


def test_framework_ingestion_rejects_duplicate_or_malformed_standard(client):
    class MalformedAI:
        is_demo = False

        def _real_json(self, *args, **kwargs):
            return {"standards": [
                {"id": "same", "rule": "规则一", "source_quote": "选择题答案必须唯一。", "scope": {"all_items": True}, "strength": "hard", "priority": 3, "evidence_required": "唯一判据"},
                {"id": "same", "rule": "规则二", "source_quote": "困难不等于高质量。", "scope": {"all_items": True}, "strength": "bad", "priority": True, "evidence_required": ""},
            ], "conflicts": []}

    source = "选择题答案必须唯一。\n困难不等于高质量。"
    with database.SessionLocal() as db:
        before = db.query(models.EvaluationFramework).count()
        with pytest.raises(HTTPException) as error:
            K.import_framework(db, "坏标准", "math_logic", source, MalformedAI())
        assert error.value.status_code == 422
        assert db.query(models.EvaluationFramework).count() == before


def test_framework_provenance_is_server_owned_and_consistent_across_apis(client):
    row = models.EvaluationFramework(
        id="provenance-fixture",
        title="provenance fixture",
        domain_key="chemistry_materials",
        version=7,
        source_text="source fixture",
        source_hash="a" * 64,
        standards=jdump([{"id": "H1", "rule": "traceable"}]),
        source_map=jdump({
            "calibration_status": "calibrated",
            "professional_certification": True,
        }),
        warnings=jdump(["authored_uncalibrated"]),
        status="active",
        extraction_method="fixture",
    )
    with database.SessionLocal() as db:
        db.add(row)
        db.commit()

    direct = K.framework_dict(row, source=False)
    assert direct["warnings"] == ["authored_uncalibrated"]
    assert direct["extraction_method"] == "fixture"
    assert direct["calibration_status"] == "not_calibrated"
    assert direct["professional_certification"] is False
    assert direct["carrier_scope"] == "legacy_proposition_only"
    assert direct["consumers"] == ["legacy_proposition"]

    origin = {"Origin": "http://127.0.0.1:3001"}
    assert client.post("/api/v2/session", headers=origin).status_code == 200
    response = client.get("/api/v2/evaluation-frameworks", headers=origin)
    assert response.status_code == 200
    item = next(value for value in response.json()["items"] if value["id"] == row.id)
    assert item["warnings"] == ["authored_uncalibrated"]
    assert item["extraction_method"] == "fixture"
    assert item["calibration_status"] == "not_calibrated"
    assert item["professional_certification"] is False
    assert item["carrier_scope"] == "legacy_proposition_only"
    assert item["consumers"] == ["legacy_proposition"]


def test_wholly_untraceable_framework_is_not_saved_or_activated(client):
    class UntraceableAI:
        is_demo = False

        def _real_json(self, *args, **kwargs):
            return {"standards": [{"rule": "凭空标准", "source_quote": "并不存在"}], "conflicts": []}

    with database.SessionLocal() as db:
        before = db.query(models.EvaluationFramework).count()
        with pytest.raises(HTTPException) as error:
            K.import_framework(db, "无来源", "math_logic", "只包含这段原文", UntraceableAI())
        assert error.value.status_code == 422
        assert db.query(models.EvaluationFramework).count() == before


def test_production_hint_trace_preview_and_training_audit_are_identical(client, monkeypatch):
    with database.SessionLocal() as db:
        goal = models.LearningGoal(title="微积分卷", domain_key="math_logic", branch="calculus", current_level="大学微积分初学者",
                                   application_context="期末试卷", motivation="测试")
        db.add(goal)
        db.commit()
        K.ensure_framework(db)
        p = models.DesignPrinciple(domain_key="math_logic", title="导数辨析", statement="干扰项对应真实误解", status="confirmed",
                                   version=4, mechanism="诊断理解", guidance="设计误解型干扰项", applicability="导数选择题",
                                   confidence=.9, evidence_family_count=3,
                                   scope=jdump({"knowledge_points": ["导数"], "question_types": ["选择题"], "branch": "calculus"}))
        db.add(p)
        db.commit()
        ai = AIService(db=db, goal_id=goal.id, tutorial_id="contract-tutorial", section_id="s-contract")
        ai.provider = SimpleNamespace(model="mock-real-path")
        item = {"title": "导数综合试卷", "purpose": "期末考核", "summary": "概念区分",
                "knowledge_points": ["导数"], "_blueprint": {"question_types": ["选择题"], "difficulty": "advanced", "practice_focus": "链式法则"}}
        hints = ai._proposition_hints(goal, item, [{"description": "判断可导条件"}])
        assert hints and p.id in hints
        actual = ai.proposition_guidance_last
        assert {"导数", "链式法则"} <= set(actual["context"]["knowledge_points"])
        assert not {"导数综合试卷", "期末考核", "概念区分", "判断可导条件"} & set(actual["context"]["knowledge_points"])
        assert actual["context"]["question_types"] == ["选择题"]
        assert actual["context"]["task_kind"] == "exam" and actual["context"]["complexity"] == "complex"
        assert actual["context"]["learner"] == goal.current_level
        trace_id = actual["trace_id"]
        assert trace_id and db.query(models.PropositionGuidanceTrace).count() == 1
        assert ai._proposition_hints(goal, item, [{"description": "判断可导条件"}]) == hints
        assert db.query(models.PropositionGuidanceTrace).count() == 1
        context = actual["context"]
        assert context["branch"] == goal.branch
        trace = db.get(models.PropositionGuidanceTrace, trace_id)
        assert jload(trace.result, {})["text"] == hints
        assert any(s["id"] == p.id and s["version"] == 4 for s in jload(trace.result, {})["selected"])
        captured = {}
        def capture_model_call(feature, system, user, *args, **kwargs):
            captured.update(system=system, user=user)
            return {"title": item["title"], "content": []}
        monkeypatch.setattr(ai, "_real_json", capture_model_call)
        from app.learning.disciplines import get_profile
        ai._section_payload(goal, get_profile(goal.domain_key), item, [{"description": "判断可导条件"}], [], "", None)
        assert hints in captured["user"]
        ai._record("section", system=captured["system"], user=captured["user"], output={"content": []},
                   usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11, "usage_source": "actual"}, goal=goal)
        db.commit()
        audit = db.query(models.TrainingRecord).filter_by(feature="section").one()
        assert jload(audit.context_json, {})["proposition_guidance"]["trace_id"] == trace_id
        assert jload(audit.context_json, {})["proposition_guidance"]["text"] == hints
    preview = client.post("/api/proposition/guidance/preview", json=context)
    assert preview.status_code == 200 and preview.json()["text"] == hints
    assert preview.json()["selected"] == actual["selected"] and preview.json()["trace_id"] is None
    assert client.get("/api/proposition/guidance/traces").json()["total"] == 1


def test_goal_branch_can_be_created_edited_and_cleared(client):
    created = client.post("/api/goals", json={"title": "微积分", "domain_key": "math_logic", "branch": "  微积分  "})
    assert created.status_code == 200
    goal = created.json()
    assert goal["branch"] == "微积分"
    path = f"/api/goals/{goal['id']}"
    assert client.patch(path, json={"branch": "线性代数"}).json()["branch"] == "线性代数"
    assert client.get(path).json()["branch"] == "线性代数"
    assert client.patch(path, json={"branch": ""}).json()["branch"] == ""
    assert client.patch(path, json={"branch": "x" * 101}).status_code == 422


def test_subject_catalog_is_shared_with_goal_creation(client):
    from app.learning.disciplines import SUBJECTS
    from test_task_run_v2 import owner

    root = Path(__file__).resolve().parents[3]
    owner(client)
    catalog = client.get("/api/v2/capabilities").json()["taxonomy"]
    options = [(entry["domain_key"], entry["display_name"]) for entry in catalog]
    assert options == [(key, name) for key, name, _ in SUBJECTS]
    form = (root / "apps/web/components/tasks/NewTask.tsx").read_text(encoding="utf-8")
    assert "caps?.taxonomy.map" in form and "value={t.domain_key}" in form


def test_one_shot_creation_preserves_user_branch(client, monkeypatch):
    from app.schemas import OneShotGenerateIn
    from app.services import pipeline

    monkeypatch.setattr(pipeline, "quickstart", lambda *args, **kwargs: {})
    with database.SessionLocal() as db:
        result = pipeline.create_and_quickstart(db, OneShotGenerateIn(
            requirement="生成一份微积分试卷", domain_key="math_logic", branch="微积分",
        ), None)
        assert result["goal"]["branch"] == "微积分"


def test_upload_old_computer_key_is_retrievable_with_goal_subject(client):
    result = client.post("/api/proposition/batches/import", json=payload(
        "## 题目 1\n计算 1+1。", domain_key="computer_cli", branch="用户自定义分支",
    ).model_dump())
    assert result.status_code == 200
    assert result.json()["domain_key"] == "cs_it"
    assert result.json()["branch"] == "用户自定义分支"
    again = client.post("/api/proposition/batches/import", json=payload(
        "## 题目 1\n计算 1+1。", domain_key="cs_it", branch="用户自定义分支",
    ).model_dump())
    assert again.json()["duplicate"] and again.json()["id"] == result.json()["id"]


@pytest.mark.parametrize("goal_branch,item_branch,expected", [
    ("自定义分支", "错误分支", "自定义分支"), ("", "章节分支", "章节分支"), ("", "", "蓝图分支"),
])
def test_generation_uses_saved_goal_branch_before_model_branch(client, goal_branch, item_branch, expected):
    with database.SessionLocal() as db:
        goal = models.LearningGoal(title="分支测试", domain_key="cs_it", branch=goal_branch)
        db.add(goal)
        db.commit()
        ai = AIService(db=db, goal_id=goal.id)
        ai.provider = SimpleNamespace(model="mock")
        ai._proposition_hints(goal, {"branch": item_branch, "_blueprint": {"branch": "蓝图分支"}})
        assert ai.proposition_guidance_last["context"]["branch"] == expected


def benchmark_module():
    path = Path(__file__).resolve().parents[3] / "scripts" / "proposition_benchmark.py"
    spec = importlib.util.spec_from_file_location("proposition_contract_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_benchmark_family_split_rejects_same_family_and_numeric_isomorphs():
    benchmark = benchmark_module()
    for case in ({"id": "holdout", "family_id": "same", "stem": "另一个题面"},
                 {"id": "holdout", "family_id": "different", "stem": "计算 9+8。"}):
        with pytest.raises(ValueError, match="家族重叠"):
            benchmark.validate_split({"learning_cases": [{"id": "train", "family_id": "same", "stem": "计算 1+2。"}], "cases": [case]})


def test_benchmark_offline_runner_seeds_examples_keeps_arms_comparable_and_gold_private(tmp_path):
    workspace = Path(__file__).resolve().parents[3]
    dataset = {"learning_cases": [{"id": "train-q", "family_id": "training-family", "stem": "计算 f(x)=x^3 的导数。",
                                  "domain_key": "math_logic", "branch": "calculus", "learner": "大学微积分初学者",
                                  "knowledge_points": ["导数"], "question_type": "选择题"}],
               "cases": [{"id": "holdout", "family_id": "blind-family", "stem": "讨论函数可导与连续的关系。",
                          "domain_key": "math_logic", "branch": "calculus", "learner": "大学微积分初学者",
                          "knowledge_points": ["导数"], "question_types": ["选择题"], "difficulty": "advanced",
                          "gold": {"answer": "GOLD_MUST_NOT_ENTER_PROMPT"}}],
               "principles": [{"id": "benchmark-p", "domain_key": "math_logic", "status": "confirmed", "title": "导数命题",
                               "statement": "诊断可导误解", "mechanism": "概念对照", "applicability": "导数选择题",
                               "guidance": "设计干扰项", "scope": {"knowledge_points": ["导数"], "question_types": ["选择题"], "requires_examples": True},
                               "positive_refs": [{"question_id": "train-q"}], "confidence": .9, "evidence_family_count": 2}]}
    source, output = tmp_path / "dataset.json", tmp_path / "report.json"
    source.write_text(json.dumps(dataset, ensure_ascii=False), encoding="utf-8")
    env = dict(os.environ, LLM_BASE_URL="http://127.0.0.1:1/v1", LLM_API_KEY="MUST_NOT_USE", LLM_MODEL="unreachable")
    # Block sockets inside the subprocess too; no accidental external call is possible.
    launch = "import runpy,socket,sys; socket.socket.connect=lambda *args: (_ for _ in ()).throw(AssertionError('network prohibited')); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    run = subprocess.run([sys.executable, "-c", launch, str(workspace / "scripts" / "proposition_benchmark.py"),
                          "--dataset", str(source), "--output", str(output)], cwd=workspace, env=env,
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["live"] is False and report["calls"] == 0 and report["model"] == "not_run"
    arms = {result["arm"]: result for result in report["results"]}
    assert set(arms) == {"none", "standards", "experiences", "examples"}
    assert all(row["status"] == "not_run" for row in arms.values())
    assert arms["none"]["guidance"]["text"] == "" and arms["standards"]["guidance"]["selected"] == []
    assert arms["experiences"]["guidance"]["selected"] and arms["examples"]["guidance"]["examples"]
    assert arms["experiences"]["guidance"]["context"] == arms["examples"]["guidance"]["context"]
    assert arms["experiences"]["guidance"]["budget_chars"] == arms["examples"]["guidance"]["budget_chars"]
    assert all("GOLD_MUST_NOT_ENTER_PROMPT" not in row["guidance"]["text"] for row in arms.values())


def test_benchmark_live_refuses_missing_budget_and_unconfigured_model(tmp_path):
    """--live 预算校验不碰生产 API：预算缺失先拒绝；模型未配置（demo）拒绝能力实验。"""
    workspace = Path(__file__).resolve().parents[3]
    dataset = tmp_path / "dataset.json"
    dataset.write_text(json.dumps(
        {"learning_cases": [{"id": "train-q", "family_id": "training-family", "stem": "计算 1+2。"}],
         "cases": [{"id": "holdout", "family_id": "blind-family", "stem": "讨论可导与连续的关系。"}],
         "principles": []}, ensure_ascii=False), encoding="utf-8")
    output = tmp_path / "report.json"
    launcher = "import runpy,sys; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
    base = [sys.executable, "-c", launcher, str(workspace / "scripts" / "proposition_benchmark.py"),
            "--dataset", str(dataset), "--output", str(output)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", LLM_BASE_URL="", LLM_API_KEY="", LLM_MODEL="")
    no_budget = subprocess.run([*base, "--live"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30, env=env)
    assert no_budget.returncode != 0 and "--live 必须显式给出" in (no_budget.stdout + no_budget.stderr)
    assert not output.exists()
    no_model = subprocess.run([*base, "--live", "--max-calls", "1", "--max-tokens", "1"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=60, env=env)
    assert no_model.returncode != 0 and "未配置真实模型" in (no_model.stdout + no_model.stderr)
    assert not output.exists()

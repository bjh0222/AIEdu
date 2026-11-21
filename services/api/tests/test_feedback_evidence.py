# -*- coding: utf-8 -*-
"""板块五反馈证据闭环测试（ADR-023 / 任务书第 7 条）：

- FeedbackEvidence 符合 generation.v1 线格式，用户原文逐字保存（hash 可验）；
- 问题定位只取可定位实质项（适用 violated/error、必需 undecidable、E0xx/DEFECT）；
- 正面评分只 recorded，不产生重评/回归/回流，更不改经验状态；
- 负面反馈产生：候选回流（板块三未接入时待评）、重评请求（pending_review）、回归待办；
- 逐字重复提交去重（occurrences 累计，原文与历史不动）；
- 撤回/修订以追加事件表达，历史不删除，重复撤回幂等；
- /api/training/feedback 的证据绑定失败时降级，不阻断反馈提交。

全部零模型调用。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.config import settings
from app.services import feedback_evidence as fe


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    target = tmp_path / "analysis"
    monkeypatch.setattr(settings, "analysis_dir", str(target))
    return target


QUALITY_REPORT = {
    "acceptance": {"payload": {"checks": [
        {"id": "constraint-1", "applicable": True, "required": True, "status": "violated", "detail": "违背硬约束"},
        {"id": "optional-1", "applicable": False, "required": False, "status": "violated", "detail": "不适用不取"},
        {"id": "need-1", "applicable": True, "required": True, "status": "undecidable", "detail": "必需未判定"},
    ]}},
    "issues": ["[E017] 第 3 题缺少答案与解析节", "[E999] 非命题码不取"],
}


def _message(record_id="rec-1", comment="第 3 题答案错了，缺解析", rating=-1,
             quality_report=QUALITY_REPORT, event_id=None):
    return fe.build_feedback_evidence(
        record_id=record_id, goal_id="g1", tutorial_id="t1", section_id="s1", version=2,
        rating=rating, tags=["题目"], comment=comment, quality_report=quality_report,
        event_id=event_id,
    )


def test_locate_problems_only_takes_actionable_items():
    problems = fe.locate_problems(QUALITY_REPORT)
    origins = {(p["origin"], p.get("check_id") or p.get("code")) for p in problems}
    assert ("acceptance_check", "constraint-1") in origins          # violated
    assert ("acceptance_check", "need-1") in origins                # 必需 undecidable
    assert ("optional-1", "")[0] and ("acceptance_check", "optional-1") not in origins  # 不适用跳过
    assert ("quality_issue", "E017") in origins                      # 命题码
    assert all(p.get("code") != "E999" for p in problems)            # 非命题码跳过
    e017 = next(p for p in problems if p.get("code") == "E017")
    assert e017["question_number"] == 3


def test_feedback_evidence_message_shape_and_verbatim_text(ledger_dir):
    message = _message()
    assert message["contract_version"] == "generation.v1" and message["kind"] == "FeedbackEvidence"
    assert message["payload"]["text"] == "第 3 题答案错了，缺解析"          # 原文逐字
    ref = message["payload"]["source_refs"][0]
    assert ref["kind"] == "feedback" and ref["location"] == "training_records.comment"
    assert ref["hash"] == hashlib.sha256("第 3 题答案错了，缺解析".encode("utf-8")).hexdigest()
    assert message["identity"]["version"] == 2
    # 再次走独立校验器（构造函数内部已校验一次）
    from app.services.generation_contract import validate_generation_message
    validate_generation_message(json.loads(json.dumps(message)), "FeedbackEvidence")


def test_positive_rating_recorded_without_revaluation_or_reflux(ledger_dir, monkeypatch):
    def _boom(*a, **k):  # 正面评分绝不允许触发回流
        raise AssertionError("positive rating must not reflux")
    monkeypatch.setattr("app.services.proposition.reflux_feedback_evidence", _boom)
    # 即便质量报告里有可定位问题，正面评分也只 recorded，不触发任何治理动作
    result = fe._append_event(None, _message(rating=1, quality_report=QUALITY_REPORT))
    assert result["outcome"] == "recorded" and result["located"] == 3
    assert result["reflux_written"] == 0 and not result["revaluation_requested"]
    assert result["regression_todos"] == 0
    ledger = fe.load_ledger()
    assert len(ledger["events"]) == 1 and not ledger["revaluations"] and not ledger["regressions"]


def test_duplicate_submission_deduplicates_and_keeps_original_text(ledger_dir):
    first = fe._append_event(None, _message(event_id="fe-a"))
    assert first["deduplicated"] is False and first["occurrences"] == 1
    duplicate = fe._append_event(None, _message(record_id="rec-2", event_id="fe-b"))
    assert duplicate["deduplicated"] is True and duplicate["occurrences"] == 2
    assert duplicate["event_id"] == "fe-a"                      # 归并到首个事件
    ledger = fe.load_ledger()
    assert len(ledger["events"]) == 1
    assert ledger["events"][0]["message"]["payload"]["text"] == "第 3 题答案错了，缺解析"
    # 措辞不同 → 新事件
    fe._append_event(None, _message(record_id="rec-3", comment="换一种说法：答案不对", event_id="fe-c"))
    assert len(fe.load_ledger()["events"]) == 2


def test_negative_feedback_reflux_revaluation_and_regression_todo(ledger_dir, monkeypatch):
    calls = []

    def fake_reflux(db, message):
        calls.append(message)
        return 1

    monkeypatch.setattr("app.services.proposition.reflux_feedback_evidence", fake_reflux)
    result = fe._append_event(None, _message())
    assert result["reflux_written"] == 1 and result["revaluation_requested"] is True
    assert result["regression_todos"] >= 1
    assert calls and calls[0]["kind"] == "FeedbackEvidence"
    ledger = fe.load_ledger()
    revaluation = ledger["revaluations"][0]
    assert revaluation["status"] == "pending_review"            # 板块三未接入 → 待评，不伪造闭环
    assert revaluation["scope"] == "proposition_re_evaluation"
    regression = ledger["regressions"][0]
    assert regression["status"] == "open"
    assert "exam-no-type-titles" in regression["suggested_benchmark_cases"]   # E017 → 固定基准包建议
    assert regression["note"].endswith("不自动执行。") or "不自动执行" in regression["note"]


def test_retraction_appends_history_and_is_idempotent(ledger_dir):
    event = _message(event_id="fe-x")
    text_before = event["payload"]["text"]
    fe._append_event(None, event)
    out = fe.retract_event("fe-x", reason="误报", replacement_comment="复核后：第 3 题其实正确")
    assert out["status"] == "retracted" and out["already"] is False
    ledger = fe.load_ledger()
    original = next(e for e in ledger["events"] if e["event_id"] == "fe-x")
    assert original["status"] == "retracted"
    assert original["message"]["payload"]["text"] == text_before        # 原事件 payload 不变
    retraction = next(e for e in ledger["events"] if e["event_id"] == out["retraction_event_id"])
    assert retraction["message"]["payload"]["outcome"] == "retracted"
    assert retraction["message"]["extensions"]["retracts"] == "fe-x"
    revision = next(e for e in ledger["events"] if e["event_id"] == out["revision_event_id"])
    assert revision["message"]["payload"]["outcome"] == "proposed"
    assert revision["message"]["extensions"]["revises"] == "fe-x"
    assert revision["message"]["payload"]["text"] == "复核后：第 3 题其实正确"
    again = fe.retract_event("fe-x")                                    # 重复撤回幂等
    assert again["already"] is True
    assert len(ledger["events"]) == len(fe.load_ledger()["events"])     # 幂等不再追加
    with pytest.raises(KeyError):
        fe.retract_event("fe-missing")


def test_retracted_event_fingerprint_does_not_absorb_new_submissions(ledger_dir):
    fe._append_event(None, _message(event_id="fe-r"))
    fe.retract_event("fe-r")
    result = fe._append_event(None, _message(record_id="rec-9", event_id="fe-r2"))
    assert result["deduplicated"] is False                     # 撤回后同文不再被归并进死事件
    assert result["event_id"] == "fe-r2"


def test_training_feedback_api_binds_evidence_and_retract(ledger_dir, client):
    resp = client.post("/api/training/feedback", json={
        "rating": 1, "comment": "讲解清晰", "target_type": "general", "tags": ["教程"],
    })
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert record["context"].get("feedback_evidence", {}).get("event_id")
    listing = client.get("/api/training/feedback-evidence").json()
    assert listing["total"] >= 1
    event = next(e for e in listing["events"] if e["event_id"] == record["context"]["feedback_evidence"]["event_id"])
    assert event["outcome"] == "recorded" and event["text"] == "讲解清晰"
    assert "governance_note" in listing
    retract = client.post(f"/api/training/feedback-evidence/{event['event_id']}/retract",
                          json={"reason": "误点"})
    assert retract.status_code == 200 and retract.json()["status"] == "retracted"
    after = client.get("/api/training/feedback-evidence").json()
    assert next(e for e in after["events"] if e["event_id"] == event["event_id"])["status"] == "retracted"
    missing = client.post("/api/training/feedback-evidence/fe-nope/retract", json={})
    assert missing.status_code == 404


def test_feedback_submission_survives_evidence_archiving_failure(ledger_dir, client, monkeypatch):
    def _boom(db, record):
        raise RuntimeError("archive down")

    monkeypatch.setattr(fe, "record_user_feedback", _boom)
    resp = client.post("/api/training/feedback", json={
        "rating": -1, "comment": "第 2 题解析跳步", "target_type": "general",
    })
    assert resp.status_code == 200, resp.text                  # 证据失败不阻断反馈提交
    assert "feedback_evidence" not in resp.json()["context"]
    assert fe.load_ledger()["events"] == []

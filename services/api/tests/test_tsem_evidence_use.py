# -*- coding: utf-8 -*-
"""R2-2：evidence_use 结构化绑定与消费校验。

来源：运行记录-两套线代试卷-20261108-修复后/监控与比较报告.md §7 第 2 行——
注入数量与正确应用之间缺消费验证；不能仅凭 selected 数量推断迁移成功。
本测试钉死：绑定字段（call_id/principle_id/version/题号/位置/hash）、位置存在、
来源实际注入、修订后过期（stale）；self-report ≠ 迁移验证。
零模型。
"""
from __future__ import annotations

import json

from app.db import SessionLocal
from app.models import PropositionGuidanceTrace
from app.services.proposition_evidence import (
    EVIDENCE_USE_VERSION,
    _hash_text,
    normalize_evidence_use,
    verify_evidence_use,
    verify_evidence_use_reports,
)


def _entry(**overrides):
    base = {
        "principle_id": "p-1",
        "version": 2,
        "question_numbers": [9],
        "location": "answers/9",
        "intended_application": "用子矩阵秩下界完成证明",
        "call_id": "call-1",
        "claimed_text_hash": "sha256:" + "0" * 64,
        "evidence_use_version": EVIDENCE_USE_VERSION,
    }
    base.update(overrides)
    return base


def test_normalize_keeps_structured_entries_only():
    raw = [
        {"principle_id": "p-1", "version": "2", "question_numbers": ["9", 4, "bad"],
         "location": "answers/9", "intended_application": "秩下界"},
        {"usage": "老格式自述"},          # 旧格式：丢弃（不能当作结构化绑定）
        "字符串条目",                     # 非法：丢弃
        {"principle_id": ""},             # 空条目：丢弃
    ]
    entries = normalize_evidence_use(raw)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["principle_id"] == "p-1"
    assert entry["version"] == 2
    assert entry["question_numbers"] == [4, 9]
    assert entry["evidence_use_version"] == EVIDENCE_USE_VERSION


def test_verify_confirmed_when_binding_location_source_and_hash_all_match():
    text = "**9.** 证明：$r(A+B)\\le r(A)+r(B)$。"
    entry = _entry(claimed_text_hash=_hash_text(text))
    result = verify_evidence_use(entry, final_sections={"s1": text},
                                 injected_principle_ids={"p-1"})
    assert result["status"] == "confirmed"
    assert result["checks"] == {"binding_complete": True, "location_exists": True,
                                "source_injected": True, "hash_matches": True}
    assert "不证明迁移" not in result["note"] if "note" in result else True


def test_verify_location_missing():
    entry = _entry(claimed_text_hash=_hash_text("**5.** 求秩。"))
    result = verify_evidence_use(entry, final_sections={"s1": "**5.** 求秩。"},
                                 injected_principle_ids={"p-1"})
    assert result["status"] == "location_missing"
    assert result["checks"]["location_exists"] is False


def test_verify_not_injected():
    text = "**9.** 证明：$r(A+B)\\le r(A)+r(B)$。"
    entry = _entry(claimed_text_hash=_hash_text(text))
    result = verify_evidence_use(entry, final_sections={"s1": text},
                                 injected_principle_ids={"p-2"})
    assert result["status"] == "not_injected"
    assert result["checks"]["source_injected"] is False


def test_verify_expired_after_revision():
    """声明 hash 是生成时正文；修订后最终正文 hash 不同 → expired（旧证据不得当 current）。"""
    claimed = _hash_text("**9.** 旧正文。")
    entry = _entry(claimed_text_hash=claimed)
    result = verify_evidence_use(entry, final_sections={"s1": "**9.** 修订后的新正文。"},
                                 injected_principle_ids={"p-1"})
    assert result["status"] == "expired"
    assert result["checks"]["hash_matches"] is False


def test_verify_unverified_when_binding_incomplete():
    entry = {"intended_application": "只说用途"}
    result = verify_evidence_use(entry, final_sections={"s1": "**9.** 正文。"})
    assert result["status"] == "unverified"
    assert set(result["missing_fields"]) >= {"principle_id", "version", "location"}


def test_verify_reports_reads_trace_and_final_content(client):
    """汇总核验：从 guidance trace 读取自述，对照最终正文与 trace 记录的注入块。"""
    text = "**9.** 证明：$r(A+B)\\le r(A)+r(B)$。"
    entry = _entry(call_id="call-9", claimed_text_hash=_hash_text(text))
    result_payload = {
        "calls": [{"call_id": "call-9", "feature": "section"}],
        "evidence_blocks": {"principles": [{"id": "p-1", "version": 2, "text": "秩下界经验"}]},
        "evidence_use_reports": [{"feature": "section", "status": "ok", "call_id": "call-9",
                                  "evidence_use": [entry]}],
    }
    with SessionLocal() as db:
        db.add(PropositionGuidanceTrace(goal_id="g-1", tutorial_id="t-evidence-use",
                                        result=json.dumps(result_payload, ensure_ascii=False)))
        db.commit()
    content = {"sections": [{"id": "s1", "content": [{"type": "text", "body": text}]}]}
    with SessionLocal() as db:
        summary = verify_evidence_use_reports(db, "t-evidence-use", content)
    assert summary["verified"] == 1
    assert summary["counts"] == {"confirmed": 1}
    assert summary["items"][0]["call_id"] == "call-9"
    assert "不证明迁移效果" in summary["note"]

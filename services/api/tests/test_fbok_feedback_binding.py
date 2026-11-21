# -*- coding: utf-8 -*-
"""C1（05 切片 A / 任务书 05）失败 fixture：反馈版本定位与绑定。

这些用例在 C1 实现前**预期失败（红）**，实现后转绿；对应任务书 05 验收门槛 1
与「执行切片 A」逐条要求：

- TrainingFeedbackIn 新增可选 tutorial_version_id / generation_record_id / run_id；
  服务端核对从属关系（版本/生成记录不属于该教程 → 明确 4xx，不静默丢弃）；
- 旧客户端无版本 → 绑定提交当时 current，binding_basis=current_at_submission；
  用户显式给版本 → binding_basis=user_viewed_version（不冒称所见版本）；
- 指纹纳入 artifact(version_id/content_hash/unit_id)：v1/v2 同文反馈各成一条，
  同版本重复才幂等；
- general 反馈只记全局体验：guidance_bindings=[]，不抓其他任务的 trace；
- 版本证据（format_spec）优先取 TutorialVersion.content_json，而非 goal 快照；
- comment 显式 API 长度校验（超长 4xx），原文逐字保留，不再静默 strip/截断。

全部零模型调用；只在测试隔离库与 tmp_path 的 analysis_dir 上运行。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import (
    LearningGoal,
    PropositionGuidanceTrace,
    TrainingRecord,
    Tutorial,
    TutorialVersion,
)
from app.services import feedback_evidence as fe


@pytest.fixture()
def ledger_dir(tmp_path, monkeypatch):
    target = tmp_path / "analysis"
    monkeypatch.setattr(settings, "analysis_dir", str(target))
    return target


def _seed_tutorial(*, goal_id="g-fbok", tutorial_id="t-fbok", scenes=("v1", "v2")):
    """建一个教程与 N 个版本；current 指向最后一个版本。返回 (tutorial_id, [tv_id 按版本序])。"""
    db = SessionLocal()
    try:
        db.add(LearningGoal(
            id=goal_id, title="线性代数", domain_key="math",
            format_spec_snapshot=json.dumps({"scene": "goal-snapshot"}, ensure_ascii=False),
        ))
        tutorial = Tutorial(id=tutorial_id, goal_id=goal_id, title="两套试卷", status="drafted")
        db.add(tutorial)
        ids = []
        for idx, scene in enumerate(scenes, start=1):
            content = {
                "sections": [{"id": f"s{idx}", "title": f"卷{idx}"}],
                "format_spec": {"scene": scene},
            }
            tv = TutorialVersion(
                id=f"tv-{tutorial_id}-{idx}", tutorial_id=tutorial_id, version=idx,
                content_json=json.dumps(content, ensure_ascii=False),
            )
            db.add(tv)
            ids.append(tv.id)
        db.commit()
        tutorial.current_version_id = ids[-1]
        db.commit()
        return tutorial.id, ids
    finally:
        db.close()


def _seed_generation_record(tutorial_id: str, *, goal_id="g-fbok", record_id="rec-gen") -> str:
    db = SessionLocal()
    try:
        rec = TrainingRecord(
            id=record_id, source_type="material", feature="section",
            goal_id=goal_id, tutorial_id=tutorial_id,
            system_prompt="sys", user_prompt="user", output_text="out",
        )
        db.add(rec)
        db.commit()
        return rec.id
    finally:
        db.close()


def _seed_trace(trace_id: str, tutorial_id: str, goal_id: str = "g-other") -> None:
    db = SessionLocal()
    try:
        db.add(PropositionGuidanceTrace(
            id=trace_id, goal_id=goal_id, tutorial_id=tutorial_id, section_id="s1",
            task_kind="section", context="{}",
            result=json.dumps({
                "injection_status": "injected",
                "selected": [{"id": "p-1", "version": 3, "status": "confirmed"}],
                "standards": [{"framework_id": "fw-1", "framework_version": "2", "id": "std-1"}],
                "examples": [{"id": "e-1"}],
            }, ensure_ascii=False),
        ))
        db.commit()
    finally:
        db.close()


def _binding(record: dict) -> dict:
    return (record.get("context") or {}).get("feedback_evidence", {}).get("binding") or {}


# ---------------------------------------------------------------------------
# 版本绑定：user_viewed_version / current_at_submission / 从属校验
# ---------------------------------------------------------------------------


def test_user_viewed_version_binding_wins_over_current(ledger_dir, client):
    tutorial_id, version_ids = _seed_tutorial()
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "第 2 题答案错了",
        "tutorial_id": tutorial_id, "tutorial_version_id": version_ids[0],
    })
    assert resp.status_code == 200, resp.text
    binding = _binding(resp.json())
    assert binding["basis"] == "user_viewed_version"
    assert binding["version"] == 1
    assert binding["tutorial_version_id"] == version_ids[0]
    db = SessionLocal()
    try:
        raw = db.get(TutorialVersion, version_ids[0]).content_json
    finally:
        db.close()
    assert binding["content_hash"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    event = fe.list_evidence()["events"][0]
    assert event["version"] == 1


def test_legacy_client_binds_current_with_explicit_basis(ledger_dir, client):
    tutorial_id, version_ids = _seed_tutorial()
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": 1, "comment": "不错",
        "tutorial_id": tutorial_id,  # 未传版本：旧客户端
    })
    assert resp.status_code == 200, resp.text
    binding = _binding(resp.json())
    assert binding["basis"] == "current_at_submission"
    assert binding["version"] == 2                       # 提交当时的 current
    assert binding["tutorial_version_id"] == version_ids[1]


def test_version_from_other_tutorial_rejected(ledger_dir, client):
    tutorial_id, _ = _seed_tutorial(goal_id="g-fbok", tutorial_id="t-fbok")
    other_id, other_versions = _seed_tutorial(goal_id="g-other", tutorial_id="t-other")
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "跨教程版本",
        "tutorial_id": tutorial_id, "tutorial_version_id": other_versions[0],
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "version_mismatch"
    assert fe.load_ledger()["events"] == []              # 拒绝时不留任何证据


def test_generation_record_and_run_ids_flow_into_binding(ledger_dir, client):
    tutorial_id, _ = _seed_tutorial()
    record_id = _seed_generation_record(tutorial_id)
    other_record = _seed_generation_record(
        *_seed_tutorial(goal_id="g-other", tutorial_id="t-other")[:1], record_id="rec-gen-other",
    )
    ok = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "带调用关联",
        "tutorial_id": tutorial_id, "generation_record_id": record_id, "run_id": "run-abc",
    })
    assert ok.status_code == 200, ok.text
    binding = _binding(ok.json())
    assert binding["generation_record_id"] == record_id
    assert binding["run_id"] == "run-abc"
    # 生成记录属于别的教程 → 从属错误
    bad = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "记录错绑",
        "tutorial_id": tutorial_id, "generation_record_id": other_record,
    })
    assert bad.status_code == 400, bad.text
    assert bad.json()["detail"]["code"] == "generation_record_mismatch"


# ---------------------------------------------------------------------------
# 指纹：同版本幂等，跨版本不去重
# ---------------------------------------------------------------------------


def test_same_text_across_versions_are_two_events(ledger_dir, client):
    tutorial_id, version_ids = _seed_tutorial()
    text = "同一句话对两个版本各评一次"
    first = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": text,
        "tutorial_id": tutorial_id, "tutorial_version_id": version_ids[0],
    })
    second = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": text,
        "tutorial_id": tutorial_id, "tutorial_version_id": version_ids[1],
    })
    assert first.status_code == 200 and second.status_code == 200
    listing = fe.list_evidence(limit=200)
    assert listing["total"] == 2                          # 跨版本不去重
    versions = sorted(e["version"] for e in listing["events"])
    assert versions == [1, 2]
    assert all(e["occurrences"] == 1 for e in listing["events"])


def test_same_version_duplicate_still_dedupes(ledger_dir, client):
    tutorial_id, version_ids = _seed_tutorial()
    body = {
        "target_type": "tutorial", "rating": -1, "comment": "重复原文",
        "tutorial_id": tutorial_id, "tutorial_version_id": version_ids[0],
    }
    assert client.post("/api/training/feedback", json=body).status_code == 200
    assert client.post("/api/training/feedback", json=body).status_code == 200
    listing = fe.list_evidence(limit=200)
    assert listing["total"] == 1
    assert listing["events"][0]["occurrences"] == 2
    assert listing["events"][0]["text"] == "重复原文"      # 原文不动


# ---------------------------------------------------------------------------
# general 反馈：不抓其他任务 trace
# ---------------------------------------------------------------------------


def test_general_feedback_binds_no_other_task_trace(ledger_dir, client):
    _seed_tutorial(goal_id="g-fbok", tutorial_id="t-fbok")
    for i in range(3):
        _seed_trace(f"trace-ghost-{i}", "t-fbok")          # 其他任务留下的最近 3 条
    resp = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": 1, "comment": "整体体验不错",
    })
    assert resp.status_code == 200, resp.text
    event = fe.list_evidence()["events"][0]
    assert event["guidance_bindings"] == []               # 全局体验不绑定任何 trace
    binding = _binding(resp.json())
    assert binding["basis"] == "unbound"                  # 无 artifact，明确标 unbound


def test_section_feedback_binds_only_own_tutorial_traces(ledger_dir, client):
    tutorial_id, _ = _seed_tutorial(goal_id="g-fbok", tutorial_id="t-fbok")
    _seed_tutorial(goal_id="g-other", tutorial_id="t-other")
    _seed_trace("trace-mine-1", "t-fbok", goal_id="g-fbok")
    _seed_trace("trace-mine-2", "t-fbok", goal_id="g-fbok")
    _seed_trace("trace-foreign", "t-other", goal_id="g-other")
    resp = client.post("/api/training/feedback", json={
        "target_type": "section", "rating": -1, "comment": "第 1 题解析跳步",
        "tutorial_id": tutorial_id, "section_id": "s1",
        "tutorial_version_id": "tv-t-fbok-1",
    })
    assert resp.status_code == 200, resp.text
    event = fe.list_evidence()["events"][0]
    bound_ids = {b.get("trace_id") for b in event["guidance_bindings"]}
    assert bound_ids <= {"trace-mine-1", "trace-mine-2"}  # 绝不掺入其他教程 trace


# ---------------------------------------------------------------------------
# 版本证据：format_spec 优先取版本 content_json
# ---------------------------------------------------------------------------


def test_format_spec_evidence_uses_version_content_over_goal_snapshot(ledger_dir, client):
    tutorial_id, version_ids = _seed_tutorial()
    resp = client.post("/api/training/feedback", json={
        "target_type": "tutorial", "rating": -1, "comment": "规范跑偏",
        "tutorial_id": tutorial_id, "tutorial_version_id": version_ids[0],
    })
    assert resp.status_code == 200, resp.text
    listing = fe.list_evidence(limit=10)
    event = next(e for e in listing["events"] if e["version"] == 1)
    message = fe.load_ledger()["events"][0]["message"]
    spec = message["extensions"]["format_spec"]
    assert spec.get("scene") == "v1"                      # 版本内嵌 spec，不是 goal 快照


# ---------------------------------------------------------------------------
# 原文保留：显式长度校验，不静默截断
# ---------------------------------------------------------------------------


def test_comment_over_limit_rejected_not_truncated(ledger_dir, client):
    long_text = "长" * 2100
    resp = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": -1, "comment": long_text,
    })
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "comment_too_long"
    assert fe.load_ledger()["events"] == []
    verbatim = " 带首尾空白的原文 \n"
    ok = client.post("/api/training/feedback", json={
        "target_type": "general", "rating": 1, "comment": verbatim,
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["comment"] == verbatim               # 逐字保留，不 strip 不截断

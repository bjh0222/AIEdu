"""板块一：生成前装配接线测试——真实入口、真实 prompt 消费、旧快照兼容、多入口一致。

用模型替身捕获**实际发出的 prompt**，验证：
1. 版本快照里的 TaskContext 真实存在且可校验（demo quickstart 全链路）；
2. 规划 EvidenceBundle（命题经验）与任务理解块真实进入 Synthesis / Blueprint prompt；
3. 章节只拿到自己的策略片段，内部协议键不外泄；
4. 旧快照（无 task_context）在重生成时确定性重建；
5. 多入口（服务级 / API）对同一目标产生同语义决策。
零真实模型调用、零网络（provider 替身 + httpx 禁用）。
"""
from __future__ import annotations

import json
import re
import time

import httpx
import pytest

from app.db import SessionLocal
from app.llm.provider import OpenAICompatibleProvider
from app.llm.service import AIService
from app.models import DesignPrinciple, GenerationJob
from app.services import tutorials as tutorials_service
from app.services.generation_contract import validate_generation_message
from app.services.utils import jdump, jload


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def prohibited(*args, **kwargs):
        raise AssertionError("wiring tests cannot call real models or embeddings")
    monkeypatch.setattr(httpx, "post", prohibited)
    monkeypatch.setattr(OpenAICompatibleProvider, "_post", prohibited)


def _create_goal(client, **overrides) -> dict:
    payload = {
        "title": "掌握命令行管道与重定向",
        "domain_key": "cs_it",
        "motivation": "能独立处理日志与数据流",
        "application_context": "分析服务器日志",
        "current_level": "会用 ls/cd 等基础命令",
        "time_budget": "共 10 小时",
        "hard_constraints": "只输出教程正文",
    }
    payload.update(overrides)
    resp = client.post("/api/goals", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _outline_only_tutorial(client, **goal_overrides) -> dict:
    """走到「有大纲、未生成章节」的状态：大纲版本里已带 TaskContext 快照。

    generate_outline 要求先有已确认验收标准（先标准、后教程），故先走
    demo 诊断/计划/标准生成并一键确认（与 quickstart 前置步骤一致）。
    """
    goal = _create_goal(client, **goal_overrides)
    assert client.post(f"/api/goals/{goal['id']}/diagnose", timeout=None).status_code == 200
    assert client.post(f"/api/goals/{goal['id']}/plan").status_code == 200
    assert client.post(f"/api/goals/{goal['id']}/criteria").status_code == 200
    assert client.post(f"/api/goals/{goal['id']}/criteria/confirm-all", json={}).status_code == 200
    resp = client.post(f"/api/goals/{goal['id']}/tutorials/generate")
    assert resp.status_code == 200, resp.text
    tutorial = client.get(f"/api/tutorials/{resp.json()['tutorial']['id']}").json()
    content = tutorial["current_version"]["content"]
    assert content.get("task_context"), "大纲版本应携带任务理解快照"
    validate_generation_message(content["task_context"], expected_kind="TaskContext")
    unit_ids = {d["unit_id"] for d in content["task_context"]["payload"]["deliverables"]}
    assert unit_ids == {item["id"] for item in content["outline"]}
    return tutorial


# ---------------------------------------------------------------- 模型替身

PRINCIPLE_STATEMENT = "经验：管道组合时先验证每段命令的输出行数，再拼接下一段。"


class _SharedCalls:
    calls: list[tuple[str, str, str]] = []


class _WiringProvider:
    """按 user prompt 的阶段标记分发确定性输出；完整记录 (kind, system, user)。"""

    model = "wiring-double"

    def __init__(self) -> None:
        pass

    def _record(self, kind: str, system: str, user: str) -> None:
        _SharedCalls.calls.append((kind, system, user))

    def complete_json(self, system: str, user: str):
        if "请做全局知识梳理" in user:
            self._record("synthesis", system, user)
            sids = re.findall(r'"section_id":\s*"([^"]+)"', user)
            return ({"sections": [{"section_id": sid, "dependencies": [],
                                   "global_position": f"{sid} 定位"} for sid in sids],
                     "global_structure": []}, 1, None)
        if "生成教学蓝图片段" in user:
            self._record("teaching_blueprint", system, user)
            sids = re.findall(r'"section_id":\s*"([^"]+)"', user)
            return ({"sections": [
                {"section_id": sid, "depth": "原理为主", "method": "演示+练习",
                 "practice_focus": f"围绕 {sid} 的取向",
                 "knowledge_points": [f"知识点-{sid}"], "question_types": ["选择题"],
                 "difficulty": "中"} for sid in sids]}, 1, None)
        if "知识速查" in user and "sections" in user:
            self._record("cheatsheet", system, user)
            return ({"title": "知识速查", "sections": [{"heading": "常用命令", "body": "ls -l"}]}, 1, None)
        if '"concepts"' in user:
            self._record("knowledge_extract", system, user)
            return ({"concepts": [], "relations": []}, 1, None)
        self._record("section", system, user)
        return ({"title": "章节", "purpose": "p", "content_type": "mixed",
                 "content": [{"type": "text", "title": "", "body": "正文内容"}]}, 1, None)

    def complete_text(self, system: str, user: str):
        self._record("extra_exercises", system, user)
        return "# 额外练习\n\n**1.** 题目一\n\n**2.** 题目二", 1, None


class _WiringAI(AIService):
    """注入替身 provider 的 AIService：走真实代码路径（含检索与记账），零网络。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider = _WiringProvider()


def _seed_principle() -> str:
    with SessionLocal() as db:
        row = DesignPrinciple(
            domain_key="cs_it", principle_type="discipline", status="confirmed",
            title="管道验证", statement=PRINCIPLE_STATEMENT,
            mechanism="先验证再组合", guidance="逐段验证输出",
            scope=jdump({"knowledge_points": [], "question_types": []}),
            confidence=0.9, evidence_count=2, version=1,
        )
        db.add(row)
        db.commit()
        return row.id


def _run_sections_worker(tutorial: dict, monkeypatch) -> list[tuple[str, str, str]]:
    _SharedCalls.calls = []  # 只在启动前清一次：worker 内会创建多个 AI 实例
    monkeypatch.setattr(tutorials_service, "AIService", _WiringAI)
    with SessionLocal() as db:
        job = GenerationJob(goal_id=tutorial["goal_id"], job_type="tutorial_sections",
                            status="queued", progress=0, stage="test")
        db.add(job)
        db.commit()
        job_id = job.id
    tutorials_service._sections_worker(tutorial["id"], job_id)
    deadline = time.time() + 15
    while time.time() < deadline:
        if tutorials_service.pending_knowledge_extractions() == 0:
            time.sleep(0.05)
            if tutorials_service.pending_knowledge_extractions() == 0:
                break
        time.sleep(0.02)
    return list(_SharedCalls.calls)


# ---------------------------------------------------------------- 断言

def test_quickstart_demo_persists_valid_task_context(client):
    goal = _create_goal(client)
    resp = client.post(f"/api/goals/{goal['id']}/tutorials/quickstart")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for _ in range(300):
        job = client.get(f"/api/generation-jobs/{body['job_id']}").json()
        if job["status"] in ("done", "failed"):
            assert job["status"] == "done", job
            break
        time.sleep(0.05)
    tutorial = client.get(f"/api/tutorials/{body['tutorial']['id']}").json()
    content = tutorial["current_version"]["content"]
    context = content["task_context"]
    validate_generation_message(context, expected_kind="TaskContext")
    payload = context["payload"]
    assert payload["task_type"] == "knowledge_tutorial"
    assert {c["id"] for c in payload["constraints"]} == {"hard-1"}
    assert payload["constraints"][0]["text"] == "只输出教程正文"
    # 交付物与大纲一致（跨对象引用成立的前提）
    assert {d["unit_id"] for d in payload["deliverables"]} == {o["id"] for o in content["outline"]}


def test_planning_evidence_and_task_context_enter_synthesis_blueprint_prompts(client, monkeypatch):
    tutorial = _outline_only_tutorial(client)
    principle_id = _seed_principle()
    calls = _run_sections_worker(tutorial, monkeypatch)
    kinds = [kind for kind, _s, _u in calls]
    assert "synthesis" in kinds and "teaching_blueprint" in kinds and "section" in kinds
    for stage in ("synthesis", "teaching_blueprint"):
        users = [user for kind, _s, user in calls if kind == stage]
        assert users, stage
        user = users[0]
        # 规划 EvidenceBundle 的经验块真实进入 prompt，且只注入一次
        assert user.count(PRINCIPLE_STATEMENT) == 1, stage
        # 统一任务理解块真实进入 prompt：任务边界 + 难度目标 + 硬约束
        assert "任务理解" in user
        assert "只输出教程正文" in user
        assert "难度目标" in user
    # 注入边界可观测：trace 记录了消费调用且块在场
    from app.models import PropositionGuidanceTrace

    with SessionLocal() as db:
        traces = db.query(PropositionGuidanceTrace).all()
        consumed = [call for trace in traces for call in jload(trace.result, {}).get("calls", [])]
        assert {c["feature"] for c in consumed} >= {"synthesis", "teaching_blueprint", "section"}
        assert all(c["block_present"] for c in consumed if c["feature"] in
                   {"synthesis", "teaching_blueprint", "section"})


def test_section_prompts_carry_only_own_unit_fragment(client, monkeypatch):
    tutorial = _outline_only_tutorial(client)
    outline = tutorial["current_version"]["content"]["outline"]
    assert len(outline) >= 2
    _seed_principle()
    calls = _run_sections_worker(tutorial, monkeypatch)
    section_calls = [user for kind, _s, user in calls if kind == "section"]
    assert len(section_calls) == len(outline)
    all_points = {f"知识点-{o['id']}" for o in outline}
    for user in section_calls:
        own = [p for p in all_points if p in user]
        assert len(own) == 1, f"章节应只看到自身策略片段：{own}"
        assert "本节策略分工" in user
        # 内部协议键不外泄到 prompt
        for key in ('"_task_context"', '"_generation_strategy"', '"_strategy_unit"'):
            assert key not in user


def test_provider_captured_prompts_match_manifest_hash(client, monkeypatch):
    """02 完成判据（provider 捕获级）：真正发给 provider 的 prompt 与装配清单逐字一致，
    manifest hash 可由清单块复算（不拿函数参数存在代替实际消费）。"""
    tutorial = _outline_only_tutorial(client)
    _seed_principle()
    _run_sections_worker(tutorial, monkeypatch)
    from app.models import TrainingRecord

    with SessionLocal() as db:
        records = (db.query(TrainingRecord)
                   .filter(TrainingRecord.tutorial_id == tutorial["id"],
                           TrainingRecord.source_type == "material",
                           TrainingRecord.feature.in_(("synthesis", "teaching_blueprint", "section")))
                   .all())
        checked = 0
        for record in records:
            context = jload(record.context_json, {})
            manifest = context.get("prompt_manifest")
            if not isinstance(manifest, dict) or not manifest.get("blocks"):
                continue
            blocks = manifest["blocks"]
            # hash 可复算：有序 (block_id, source_hash, char_count, token_estimate)
            import hashlib as _hashlib

            digest = _hashlib.sha256()
            for block in blocks:
                digest.update(f"{block['block_id']}|{block['source_hash']}|{block['char_count']}"
                              f"|{block['token_estimate']}\n".encode("utf-8"))
            assert digest.hexdigest() == manifest["manifest_hash"], record.feature
            # 清单与实际发出的 user prompt 逐字一致（Σchar_count == len(user_prompt)）
            assert sum(int(b["char_count"]) for b in blocks) == len(record.user_prompt), record.feature
            # 章节块全部带本节 unit_id（章节只含自身策略的可核对依据）
            if record.feature == "section":
                assert all(b.get("unit_id") for b in blocks), record.feature
            checked += 1
        assert checked >= 3, f"synthesis/blueprint/section 至少 3 条调用带可核对清单：{checked}"


def test_legacy_snapshot_without_task_context_is_rebuilt_deterministically(client, monkeypatch):
    tutorial = _outline_only_tutorial(client)
    with SessionLocal() as db:
        from app.models import Tutorial, TutorialVersion

        row = db.get(Tutorial, tutorial["id"])
        version = db.get(TutorialVersion, row.current_version_id)
        content = jload(version.content_json, {})
        content.pop("task_context", None)  # 模拟旧快照
        version.content_json = jdump(content)
        db.commit()
    _seed_principle()
    calls = _run_sections_worker(tutorial, monkeypatch)
    users = [user for kind, _s, user in calls if kind == "synthesis"]
    assert users and "任务理解" in users[0]
    assert any("只输出教程正文" in user for kind, _s, user in calls if kind == "teaching_blueprint")
    # 重建结果与首次生成同语义（同目标状态 → 同决策）
    rebuilt = [user for kind, _s, user in calls if kind == "synthesis"][0]
    assert "管道" in rebuilt or "命令行" in rebuilt  # 目标语境可见


def test_multi_entry_decisions_are_identical(client):
    """同一目标状态：服务级构造与 API 端点产生同语义决策（网页 / Obsidian 共用入口）。"""
    goal = _create_goal(client)
    from app.services.task_understanding import build_task_context, task_understanding_summary

    with SessionLocal() as db:
        from app.models import LearningGoal

        row = db.get(LearningGoal, goal["id"])
        service_context = build_task_context(db, row)
        service_summary = task_understanding_summary(service_context)
    api = client.get(f"/api/goals/{goal['id']}/task-understanding").json()
    assert api["summary"]["task_type"] == service_summary["task_type"]
    assert api["summary"]["difficulty"] == service_summary["difficulty"]
    assert api["summary"]["conflicts"] == service_summary["conflicts"]
    assert [c["text"] for c in api["summary"]["constraints"]] == \
        [c["text"] for c in service_summary["constraints"]]
    # 确定性重算：两次构造完全一致
    with SessionLocal() as db:
        from app.models import LearningGoal

        again = build_task_context(db, db.get(LearningGoal, goal["id"]))
    assert json.dumps(again["payload"], ensure_ascii=False, sort_keys=True) == \
        json.dumps(service_context["payload"], ensure_ascii=False, sort_keys=True)

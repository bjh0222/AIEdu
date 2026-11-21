# -*- coding: utf-8 -*-
"""S2 fixture：质量发现台账、修订登记、统一修复记账与验收方法拆分（脱敏合成）。

覆盖 01 任务书步骤 3/4/5 的核心行为：
- vA 的缺陷经 L1 对第 1 题修复后，最终发现不再拿旧第 1 题证据判错；未修复的
  第 3 题发现保留（location 级 hash 对齐）。
- 每生成单元模型内容修复最多一次；L0 可证明格式修复单独记账；保护器拒绝即结束。
- 只有模型错误时 structure 不得被标 deterministic violated；模型分数不替代
  tool/human/empirical；关闭模型 → correctness 待复核。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.db import SessionLocal
from app.services import exam_check as ec
from app.services import exam_verify as ev
from app.services import proposition_generated as generated
from app.services import proposition_knowledge as knowledge
from app.services import quality_findings as qf
from app.services.generation_acceptance import DELIVERY_RUBRIC_VERSION, quality_acceptance

BODY = """### 选择题

**1.**（20 分）设函数满足题设条件，选择正确结论（　　）

A. 甲　　B. 乙　　C. 丙　　D. 丁


### 填空题

**2.**（10 分）填空题面 ______。

### 解答题

**3.**（20 分）证明题面一。


### 参考答案

**1.** C

**2.** 甲

**3.** (1) 完整证明的第一步推理必须足够长以触发长答案保护，包含必要条件与推导链条。(2) 第二步继续推理直至结论成立，保持完整小问结构。
"""

CONTENT = {"format_spec": {"scene": "exam_paper"},
           "sections": [{"id": "s1", "title": "期中试卷", "content": [{"type": "text", "body": BODY}]}]}

REVIEW_DEFECTS = [
    {"severity": "blocking",
     "reason": "第1题参考答案错误：正确选项应为乙。",
     "evidence": "对题设条件逐一检验，只有选项乙满足约束。"},
    {"severity": "blocking",
     "reason": "第3题证明存在缺口：缺少第二步的必要性论证。",
     "evidence": "按现有推导只能得到充分性方向。"},
]


def _goal():
    return SimpleNamespace(id="goal-1", domain_key="math_logic", title="线代试卷",
                           motivation="", application_context="", current_level="",
                           time_budget="", hard_constraints="")


def _strategy():
    return {"contract_version": "generation.v1", "kind": "GenerationStrategy",
            "identity": {"task_id": "goal-1", "goal_id": "goal-1", "tutorial_id": "tut-1",
                         "version": 2, "unit_id": None},
            "payload": {"units": [{"unit_id": "s1", "knowledge_points": ["矩阵"],
                                   "question_types": ["解答题"], "boundaries": [],
                                   "difficulty_mechanisms": [], "knowledge_requests": [],
                                   "check_ids": ["correctness"]}],
                        "shared_constraints": [],
                        "source_refs": [{"kind": "user_task", "id": "goal-1", "version": "1",
                                         "hash": None, "location": "request"}]},
            "extensions": {}}


# ---------------------------------------------------------------- 台账单测

def test_revision_registration_requires_changed_text():
    ledger = qf.FindingLedger(tutorial_id="t", version=2)
    assert ledger.register_revision(actor="l1_rewrite", before_text=BODY, after_text=BODY,
                                    changed_locations=["answers/1"]) is None
    revision = ledger.register_revision(actor="l1_rewrite", before_text=BODY,
                                        after_text=BODY.replace("**1.** C", "**1.** B"),
                                        changed_locations=["answers/1"], finding_ids=["f-1"])
    assert revision and revision["actor"] == "l1_rewrite"
    assert revision["finding_ids"] == ["f-1"]
    assert revision["before_hash"] != revision["after_hash"]


def test_model_repair_quota_once_per_unit_and_l0_separate():
    ledger = qf.FindingLedger(tutorial_id="t")
    assert ledger.request_model_repair("s1:0", purpose="review_repair") is True
    ledger.settle_model_repair("s1:0", outcome="rejected")
    # 保护器拒绝后本轮修复结束：同单元不再发放额度（不换模块绕道）
    assert ledger.request_model_repair("s1:0", purpose="l1_retry") is False
    assert ledger.request_model_repair("s1:0", purpose="review_repair") is False
    other = qf.FindingLedger(tutorial_id="t")
    assert other.request_model_repair("s1:0", purpose="review_repair") is True
    other.settle_model_repair("s1:0", outcome="applied")
    assert other.request_model_repair("s1:0", purpose="review_repair") is False
    # L0 可证明格式修复单独记账，不占模型额度
    other.record_l0_fixes("s1:0", 3)
    assert other.repair_state("s1:0")["l0_fixes"] == 3
    assert other.repair_state("s1:0")["model_content_attempts"] == 1


# ---------------------------------------------------------------- 最终对齐

def test_finalize_resolves_l1_covered_finding_and_keeps_unrelated_violation():
    """vA/vB：L1 修复第 1 题后旧证据关闭；第 3 题未修复的发现保留。"""
    ledger = qf.FindingLedger(tutorial_id="t", version=2)
    paper = ec.parse_paper(BODY)
    ledger.add_finding(qf.ReviewFinding(
        finding_id="f-1", check_id="correctness", unit_id="s1:0", question_numbers=[1],
        location="answers/1", status="violated", evidence_method="model_static",
        content_hash=qf.content_hash(paper.answers[1]), reason="第1题参考答案错误"))
    ledger.add_finding(qf.ReviewFinding(
        finding_id="f-2", check_id="correctness", unit_id="s1:0", question_numbers=[3],
        location="answers/3", status="violated", evidence_method="model_static",
        content_hash=qf.content_hash(paper.answers[3]), reason="第3题证明存在缺口"))
    new_body = BODY.replace("**1.** C", "**1.** B")
    revision = ledger.register_revision(actor="l1_rewrite", before_text=BODY, after_text=new_body,
                                        changed_locations=["answers/1"], finding_ids=["f-1"])
    extension = ledger.finalize({"sections": [
        {"id": "s1", "title": "期中试卷", "content": [{"type": "text", "body": new_body}]}]})
    items = {i["finding_id"]: i for i in extension["items"]}
    assert items["f-1"]["status"] == "satisfied"
    assert items["f-1"]["resolved_by"] == revision["revision_id"]
    assert items["f-2"]["status"] == "violated"  # 第 3 题未修复：发现保留
    assert extension["final_text_hash"].startswith("sha256:")
    assert extension["history"] == []


def test_stale_finding_when_text_changed_without_coverage():
    """正文变了但没有覆盖证据：旧发现标 stale，不再给当前正文下结论。"""
    ledger = qf.FindingLedger(tutorial_id="t")
    paper = ec.parse_paper(BODY)
    ledger.add_finding(qf.ReviewFinding(
        finding_id="f-2", check_id="correctness", unit_id="s1:0", question_numbers=[2],
        location="answers/2", status="violated", evidence_method="model_static",
        content_hash=qf.content_hash(paper.answers[2]), reason="第2题参考答案存疑"))
    new_body = BODY.replace("**2.** 甲", "**2.** 乙")
    # 修订只登记了另一条发现的处置，未覆盖 f-2
    ledger.register_revision(actor="review_repair", before_text=BODY, after_text=new_body,
                             changed_locations=["answers/2"], finding_ids=["f-other"])
    extension = ledger.finalize({"sections": [
        {"id": "s1", "title": "期中试卷", "content": [{"type": "text", "body": new_body}]}]})
    assert [i["finding_id"] for i in extension["items"]] == []
    assert [i["finding_id"] for i in extension["history"]] == ["f-2"]
    assert extension["history"][0]["status"] == "stale"
    assert "hash 不一致" in extension["history"][0]["detail"]


def test_l0_paper_finding_is_rechecked_on_final_text():
    """L0 是确定性检查：正文变化后直接在最终卷面重判，违规消失则由复核关闭。"""
    bad_body = BODY.replace("**2.** 甲", "**2.** 此处结论需核验")
    clean_body = BODY
    ledger = qf.FindingLedger(tutorial_id="t")
    ledger.add_finding(qf.finding_from_l0(
        {"code": "E015", "rule": "答案区泄漏不确定性表述", "detail": "需核验",
         "severity": "blocking", "fixed": False, "auto_fixable": False},
        unit_id="s1:0", body=bad_body, finding_id="f-1", spec="exam"))
    extension = ledger.finalize({"sections": [
        {"id": "s1", "title": "期中试卷", "content": [{"type": "text", "body": clean_body}]}]})
    items = {i["finding_id"]: i for i in extension["items"]}
    assert items["f-1"]["status"] == "satisfied"
    assert items["f-1"]["resolved_by"] == "l0_recheck"

    still_bad_ledger = qf.FindingLedger(tutorial_id="t")
    still_bad_ledger.add_finding(qf.finding_from_l0(
        {"code": "E015", "rule": "答案区泄漏不确定性表述", "detail": "需核验",
         "severity": "blocking", "fixed": False, "auto_fixable": False},
        unit_id="s1:0", body=bad_body, finding_id="f-1", spec="exam"))
    still_bad = still_bad_ledger.finalize({"sections": [
        {"id": "s1", "title": "期中试卷", "content": [{"type": "text", "body": bad_body.replace("此处结论需核验", "此处结论需核验，另行存疑")}]}]})
    kept = {i["finding_id"]: i for i in still_bad["items"]}
    assert kept["f-1"]["status"] == "violated"  # 重判仍成立：更新 hash 并保持 violated


# ---------------------------------------------------------------- 验收方法拆分

def test_model_only_failures_do_not_violate_structure():
    """只有模型错误时 structure 不得被标 deterministic violated；模型结论归 correctness。"""
    quality = {"checks": [
        {"name": "试卷卷面账目校验", "passed": True, "detail": "ok"},
        {"name": "试卷答案盲解核验", "passed": True, "detail": "ok"},
        {"name": "命题内容评价", "passed": False, "detail": "ok"},
    ], "issues": ["[命题评价] 1 个生成单元仍有未修复的实质缺陷。"],
       "proposition_review": {"enabled": True, "reports": [{
           "id": "s1:0", "repair": "rejected_by_guard",
           "defects": REVIEW_DEFECTS}]}}
    result = quality_acceptance(_goal(), CONTENT, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy())
    payload = result["payload"]
    by_id = {c["id"]: c for c in payload["checks"]}
    assert by_id["structure"]["status"] == "satisfied"
    assert by_id["structure"]["evidence_method"] == "deterministic"
    assert by_id["correctness"]["status"] == "violated"
    assert payload["status"] == "failed"
    items = result["extensions"]["findings"]["items"]
    assert any(f["status"] == "violated" and f["question_numbers"] == [1] for f in items)
    assert any(f["status"] == "violated" and f["question_numbers"] == [3] for f in items)


def test_fallback_l1_rewrite_closes_review_finding_on_same_answer():
    """回退路径（无共享台账）：L1 已回写的答案按新证据关闭同位置的旧审查发现。"""
    quality = {"checks": [{"name": "试卷卷面账目校验", "passed": True}],
               "issues": [],
               "proposition_review": {"enabled": True, "reports": [{
                   "id": "s1:0", "repair": "rejected_by_guard", "defects": REVIEW_DEFECTS}]},
               "exam_verify": {"enabled": True, "papers": [{
                   "title": "期中试卷", "applied": [{"number": 1, "verdict": "paper_wrong",
                                                     "confidence": "high"}], "defects": []}]}}
    result = quality_acceptance(_goal(), CONTENT, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy())
    items = {f["question_numbers"][0]: f for f in result["extensions"]["findings"]["items"]
             if f.get("question_numbers")}
    assert items[1]["status"] == "satisfied" and items[1]["resolved_by"] == "l1_rewrite"
    assert items[3]["status"] == "violated"  # 第 3 题未修复
    by_id = {c["id"]: c for c in result["payload"]["checks"]}
    assert by_id["correctness"]["status"] == "violated"


def test_l1_fixed_old_defect_no_longer_fails_correctness():
    """审查缺陷的唯一目标已被 L1 修复：不得继续拿旧证据判错（correctness satisfied）。"""
    quality = {"checks": [{"name": "试卷卷面账目校验", "passed": True}],
               "issues": [],
               "proposition_review": {"enabled": True, "reports": [{
                   "id": "s1:0", "repair": "rejected_by_guard",
                   "defects": [REVIEW_DEFECTS[0]]}]},
               "exam_verify": {"enabled": True, "papers": [{
                   "title": "期中试卷", "applied": [{"number": 1}], "defects": []}]}}
    result = quality_acceptance(_goal(), CONTENT, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy())
    by_id = {c["id"]: c for c in result["payload"]["checks"]}
    assert by_id["correctness"]["status"] == "satisfied"
    assert result["payload"]["status"] == "passed"
    ref = by_id["correctness"]["evidence_refs"][0]
    assert ref["location"].startswith("unit:s1:0/answers/1#finding:")


def test_demo_review_leaves_correctness_pending_not_run():
    """关闭模型：correctness not_run（必需）→ 待复核，不暗开模型。"""
    quality = {"checks": [{"name": "试卷卷面账目校验", "passed": True}], "issues": [],
               "proposition_review": {"enabled": True, "demo": True, "status": "not_run"}}
    result = quality_acceptance(_goal(), CONTENT, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy())
    by_id = {c["id"]: c for c in result["payload"]["checks"]}
    assert by_id["correctness"]["status"] == "not_run"
    assert result["payload"]["status"] == "needs_review"
    assert result["extensions"]["rubric_version"] == DELIVERY_RUBRIC_VERSION


def test_l1_defects_feed_correctness_not_structure():
    """L1 盲解缺陷是 correctness 层证据（tool 方法），不污染 structure。"""
    quality = {"checks": [{"name": "试卷卷面账目校验", "passed": True},
                          {"name": "试卷答案盲解核验", "passed": True}],
               "issues": ["[DEFECT] 期中试卷·第2题：选项不完备，学生无解。"],
               "exam_verify": {"enabled": True, "papers": [{
                   "title": "期中试卷", "applied": [], "defects": [
                       {"number": 2, "defect": "选项不完备",
                        "reasoning": "[工具验证·sympy] 复算确认选项不完备"}]}]}}
    result = quality_acceptance(_goal(), CONTENT, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy())
    by_id = {c["id"]: c for c in result["payload"]["checks"]}
    assert by_id["structure"]["status"] == "satisfied"
    assert by_id["correctness"]["status"] == "violated"
    finding = next(f for f in result["extensions"]["findings"]["items"]
                   if f["question_numbers"] == [2])
    assert finding["evidence_method"] == "tool"
    assert finding["source"] == "l1"


# ---------------------------------------------------------------- 端到端（共享台账）

class ReviewAI:
    """审查 → 修复被保护器拒绝（零真实模型调用）。"""

    is_demo = False
    calls = []

    def __init__(self, **kwargs):
        pass

    def _real_json(self, *args):
        self.calls.append(args[0])
        return {"defects": [dict(d) for d in REVIEW_DEFECTS],
                "uncertainties": ["未进行学生实测或统计；该判断仅为先验。"]}

    def _real_text(self, *args):
        self.calls.append(args[0])
        return BODY.replace("（20 分）", "（25 分）")  # 改分值 → 契约签名保护拒绝


class VerifyReport:
    applied_number = 1

    @staticmethod
    def report(title):
        new_md = BODY.replace("**1.** C", "**1.** B")
        return {"title": title, "spec": "exam", "verified": 3, "conflicts": [],
                "defects": [], "applied": [{"number": VerifyReport.applied_number,
                                            "verdict": "paper_wrong", "confidence": "high",
                                            "correct_answer": "B", "correct_answer_text": "乙"}],
                "skipped": [], "changed": True, "error": "",
                "_markdown": new_md}


def test_end_to_end_l1_covers_review_finding_via_shared_ledger(client, monkeypatch):
    """共享台账全链：审查缺陷（f-1/f-2）→ 修复被拒 → L1 回写第 1 题 →
    最终发现 f-1 satisfied（修订覆盖）、f-2 violated；correctness 由第 3 题维持 failed。"""
    ReviewAI.calls = []
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    monkeypatch.setattr(ev, "_verify_one_paper",
                        lambda **kwargs: VerifyReport.report(kwargs.get("title", "")))
    ledger = qf.FindingLedger(tutorial_id="tut-1", version=2)
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        content = {"format_spec": {"scene": "exam_paper"},
                   "sections": [{"id": "s1", "title": "期中试卷",
                                 "content": [{"type": "text", "body": BODY}]}]}
        review = generated.review_generated(db, content, _goal(), "tut-1", "job",
                                            ledger=ledger)
        assert review["reports"][0]["repair"] == "rejected_by_guard"
        summary = ev.run_exam_quality(content, goal_id="g", tutorial_id="tut-1", job_id="job",
                                      discipline=None, verify_enabled=True, ledger=ledger)
    assert summary["l1"]["applied_total"] == 1
    state = ledger.repair_state("s1:0")
    assert state["outcome"] == "rejected" and state["l1_rounds"] == 1

    quality = {"checks": [{"name": "试卷卷面账目校验", "passed": True}], "issues": [],
               "proposition_review": review, "exam_verify": summary["l1"]}
    result = quality_acceptance(_goal(), content, quality, tutorial_id="tut-1", version=2,
                                strategy=_strategy(), ledger=ledger)
    findings = result["extensions"]["findings"]
    items = {f["finding_id"]: f for f in findings["items"]}
    assert items["f-1"]["status"] == "satisfied" and items["f-1"]["resolved_by"].startswith("r-")
    assert items["f-2"]["status"] == "violated"
    assert findings["revisions"] and findings["revisions"][0]["actor"] == "l1_rewrite"
    assert findings["repairs"]["s1:0"]["outcome"] == "rejected"
    by_id = {c["id"]: c for c in result["payload"]["checks"]}
    assert by_id["structure"]["status"] == "satisfied"
    assert by_id["correctness"]["status"] == "violated"
    assert result["payload"]["status"] == "failed"
    # correctness 的 evidence_refs 定位到**最终版本**：仍成立的 f-2（answers/3）与
    # 已解决并刷新到最终 hash 的 f-1（answers/1）都可回溯，旧正文 hash 不再出现。
    refs = {r["location"].split("#finding:")[1]: r for r in by_id["correctness"]["evidence_refs"]}
    assert set(refs) == {"f-1", "f-2"}
    assert "answers/3" in refs["f-2"]["location"]
    # L1 改写后的最终答案（不是旧正文）：hash 从最终正文重算
    final_paper = ec.parse_paper(BODY.replace("**1.** C", "**1.** B"))
    assert refs["f-1"]["hash"] == qf.content_hash(final_paper.answers[1])


def test_on_stage_callback_observes_review_stages(client, monkeypatch):
    """on_stage 只观测：审查阶段回调按契约触发，异常被吞掉不影响结果。"""
    ReviewAI.calls = []
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    events: list[tuple] = []

    def broken_callback(stage, status, meta):
        events.append((stage, status, meta.get("unit_id")))
        raise RuntimeError("callback must never break the review")

    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        content = {"format_spec": {"scene": "exam_paper"},
                   "sections": [{"id": "s1", "title": "期中试卷",
                                 "content": [{"type": "text", "body": BODY}]}]}
        report = generated.review_generated(db, content, _goal(), "tut-1", "job",
                                            on_stage=broken_callback)
    assert report["reports"][0]["repair"] == "rejected_by_guard"
    stages = [(s, st) for s, st, _u in events]
    assert ("reviewing", "running") in stages and ("reviewing", "done") in stages
    assert ("repairing", "skipped") in stages
    assert any(u == "s1:0" for _s, _st, u in events)

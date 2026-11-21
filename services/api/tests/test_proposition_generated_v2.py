"""可选后评预算、契约守卫和演示诚实性；不访问真实模型。"""
from types import SimpleNamespace

from app.db import SessionLocal
from app.services import proposition_generated as generated
from app.services import proposition_knowledge as knowledge


class ReviewAI:
    is_demo = False
    calls = []
    replacement = ""

    def __init__(self, **kwargs):
        pass

    def _real_json(self, *args):
        if args[0] == "proposition_generated_recheck":
            self.calls.append("recheck")
            return {"defects": [], "uncertainties": []}
        self.calls.append("review")
        return {"defects": [{"severity": "blocking", "reason": "运算错误", "evidence": "题1答案错误"}]}

    def _real_text(self, *args):
        self.calls.append("repair")
        return self.replacement


def _goal():
    return SimpleNamespace(id="test_goal", domain_key="math_logic", title="微积分", motivation="", application_context="", current_level="", time_budget="", hard_constraints="")


def _content(text):
    return {"format_spec": {"scene": "exam_paper"}, "sections": [{"id": "s1", "content": [{"type": "text", "body": text}]}]}


def test_post_evaluation_is_off_by_default(client, monkeypatch):
    monkeypatch.setattr(generated, "AIService", lambda **kwargs: (_ for _ in ()).throw(AssertionError("不应调用模型")))
    with SessionLocal() as db:
        assert generated.review_generated(db, _content("题目"), _goal(), "", "") == {"enabled": False}


def test_demo_never_claims_review_or_repair(client, monkeypatch):
    monkeypatch.setattr(generated, "AIService", lambda **kwargs: SimpleNamespace(is_demo=True))
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        result = generated.review_generated(db, _content("题目"), _goal(), "", "")
        assert result == {"enabled": True, "demo": True, "status": "not_run"}


def test_repair_only_once_and_preserves_contract(client, monkeypatch):
    original = "### 计算题\n**1.**（5分）求 $1+2$，答案为2。"
    ReviewAI.calls = []
    ReviewAI.replacement = original.replace("答案为2", "答案为3")
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        content = _content(original)
        report = generated.review_generated(db, content, _goal(), "", "")
        assert report["reports"][0]["repair"] == "applied_once"
        assert content["sections"][0]["content"][0]["body"] == ReviewAI.replacement
        assert report["reports"][0]["recheck_status"] == "satisfied"
        assert ReviewAI.calls == ["review", "repair", "recheck"]


def test_plain_question_deletion_and_score_change_are_rejected(client, monkeypatch):
    original = "### 计算题\n1.（5分）求 $1+2$。\n2.（5分）求 $2+3$。"
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True})
        for replacement in ["### 计算题\n1.（5分）求 $1+2$。", original.replace("5分", "6分")]:
            ReviewAI.calls = []
            ReviewAI.replacement = replacement
            content = _content(original)
            report = generated.review_generated(db, content, _goal(), "", "")
            assert report["reports"][0]["repair"] == "rejected_by_guard"
            assert content["sections"][0]["content"][0]["body"] == original


def test_budget_limits_post_review_and_repairs(client, monkeypatch):
    original = "**1.** 求 $1+2$。"
    ReviewAI.calls = []
    ReviewAI.replacement = original
    monkeypatch.setattr(generated, "AIService", ReviewAI)
    with SessionLocal() as db:
        knowledge.save_config(db, {"auto_evaluate_generated": True, "max_calls_per_job": 1})
        content = _content(original)
        content["sections"][0]["content"].append({"type": "text", "body": original})
        report = generated.review_generated(db, content, _goal(), "", "")
        assert report["calls"] == 1
        assert report["reports"][0]["repair"] == "deferred_budget"
        assert report["reports"][1]["status"] == "deferred"
        assert ReviewAI.calls == ["review"]

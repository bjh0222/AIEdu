"""证据不可变、盲解隔离、增量分析和框架迁移的业务契约。"""
from types import SimpleNamespace

from app.db import SessionLocal
from app.models import EvaluationFramework, PropositionEvaluation, PropositionQuestion, DesignPrinciple
from app.schemas import PropositionBatchImport
from app.services import proposition_knowledge as knowledge
from app.services.utils import jdump


MARKDOWN = """# 微积分典型题
## 第1题
题面：求 $f(x)=x^2$ 在 $x=3$ 处的导数。
评注：EXTERNAL_FIRST 原始评价，简单但有教学价值。
外部评价：不要因简单而扣收录价值。
## 第2题
题面：说明可导与连续的关系并给出反例。
评注：EXTERNAL_SECOND
"""


def payload(text=MARKDOWN, **kwargs):
    return PropositionBatchImport(filename="examples.md", markdown=text, auto_analyse=False, **kwargs)


class FakeAI:
    is_demo = False
    calls = []
    fail = False

    def __init__(self, **kwargs):
        pass

    def _real_json(self, feature, system, user, required, *args):
        self.calls.append((feature, system, user))
        if self.fail:
            raise RuntimeError("model unavailable")
        if feature == "proposition_blind_solve":
            assert "EXTERNAL_" not in user
            return {"answer": "6", "solvable": True, "confidence": 0.95, "verification": "按定义推导"}
        return {
            "correctness": {"status": "supported", "reason": "独立推导"},
            "difficulty": {"level": 1}, "quality": {"strengths": ["基础概念"]},
            "structure": {"mechanism": "定义到实例"}, "recommendation": "consider",
            "confidence": .95, "annotation_comparison": [], "knowledge_points": ["导数"],
        }


def test_import_all_questions_and_external_text_preserved(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        assert result["counts"]["total"] == 2
        assert result["source_text"] == MARKDOWN
        assert "EXTERNAL_FIRST" in result["questions"][0]["raw_notes"]
        assert "EXTERNAL_SECOND" in result["questions"][1]["raw_notes"]
        assert "EXTERNAL" not in result["questions"][0]["raw_stem"]
        assert all(q["parse_status"] == "ready" for q in result["questions"])


def test_no_math_definition_is_required_for_python(client):
    response = client.post("/api/proposition/batches/import", json=payload("## 第1题\n解释 Python 列表与元组的区别。\n评注：基础关键题", domain_key="computer_cli", branch="python").model_dump())
    assert response.status_code == 200
    assert response.json()["counts"]["parsed"] == 1


def test_reupload_idempotent_new_notes_create_new_evidence(client):
    with SessionLocal() as db:
        first = knowledge.import_batch(db, payload())
        duplicate = knowledge.import_batch(db, payload())
        changed = knowledge.import_batch(db, payload(MARKDOWN.replace("EXTERNAL_FIRST", "EXTERNAL_NEW")))
        assert duplicate["id"] == first["id"] and duplicate["duplicate"]
        assert changed["id"] != first["id"]
        old = db.get(PropositionQuestion, first["questions"][0]["id"])
        assert "EXTERNAL_FIRST" in old.raw_notes


def test_long_notes_are_not_truncated(client):
    text = "## 第1题\n证明可导函数连续。\n评注：" + "原始说明" * 1000 + "END_OF_NOTE"
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload(text))
        assert "END_OF_NOTE" in result["questions"][0]["raw_notes"]


def test_parse_failure_keeps_other_questions(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload("## 第1题\n\n## 第2题\n计算 $1+2$。\n"))
        assert result["counts"]["parse_failed"] == 1
        assert result["counts"]["parsed"] == 1
        assert len(result["questions"]) == 2


def test_unsupported_images_explicitly_fail(client):
    response = client.post("/api/proposition/batches/import", json=payload("## 第1题\n根据下图计算面积。![图](example.png)").model_dump())
    assert response.json()["counts"]["parse_failed"] == 1


def test_markdown_only(client):
    data = payload().model_dump()
    data["filename"] = "questions.pdf"
    assert client.post("/api/proposition/batches/import", json=data).status_code == 422


def test_blind_solve_and_external_comparison_separated(client):
    FakeAI.calls = []
    FakeAI.fail = False
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        framework = knowledge.active_framework(db, "math_logic")
        qid = result["questions"][0]["id"]
        framework_id = framework.id
    outcome = knowledge.analyse_question(qid, framework_id, ai_factory=FakeAI)
    assert outcome["status"] == "analysed"
    assert [c[0] for c in FakeAI.calls] == ["proposition_blind_solve", "proposition_evaluate"]
    assert "EXTERNAL_FIRST" in FakeAI.calls[1][2]
    with SessionLocal() as db:
        assert "EXTERNAL_FIRST" in db.get(PropositionQuestion, qid).raw_notes


def test_incremental_skips_unchanged_analysis(client):
    FakeAI.calls = []
    FakeAI.fail = False
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        qid = result["questions"][0]["id"]
        fid = knowledge.active_framework(db, "math_logic").id
    knowledge.analyse_question(qid, fid, ai_factory=FakeAI)
    outcome = knowledge.analyse_question(qid, fid, ai_factory=FakeAI)
    assert outcome == {"status": "skipped", "reason": "unchanged"}
    assert len(FakeAI.calls) == 2


def test_failure_does_not_destroy_external_evidence(client):
    FakeAI.fail = True
    try:
        with SessionLocal() as db:
            result = knowledge.import_batch(db, payload())
            qid = result["questions"][0]["id"]
            fid = knowledge.active_framework(db, "math_logic").id
        assert knowledge.analyse_question(qid, fid, ai_factory=FakeAI)["status"] == "failed"
        with SessionLocal() as db:
            assert "EXTERNAL_FIRST" in db.get(PropositionQuestion, qid).raw_notes
            assert db.query(PropositionEvaluation).filter(PropositionEvaluation.status == "failed").count() == 1
    finally:
        FakeAI.fail = False


def test_demo_explicit_not_evaluated(client):
    class DemoAI:
        is_demo = True
        def __init__(self, **kwargs):
            pass
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        qid = result["questions"][0]["id"]
        fid = knowledge.active_framework(db, "math_logic").id
    assert knowledge.analyse_question(qid, fid, ai_factory=DemoAI)["status"] == "demo"
    with SessionLocal() as db:
        evaluation = db.query(PropositionEvaluation).first()
        assert evaluation.demo and evaluation.status == "demo"
        assert db.query(DesignPrinciple).count() == 0


def test_framework_update_preserves_historical_evaluation(client):
    FakeAI.fail = False
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        old = knowledge.active_framework(db, "math_logic")
        old_id = old.id
        qid = result["questions"][0]["id"]
    knowledge.analyse_question(qid, old_id, ai_factory=FakeAI)
    with SessionLocal() as db:
        new = EvaluationFramework(title="修订评价标准", domain_key="general", version=2,
            standards=jdump([{"id": "updated", "rule": "新标准", "source_quote": "新标准",
                "scope": {"applies_to": "all"}, "priority": 2, "strength": "core",
                "evidence_required": "traceable evidence"}]),
            source_text="新标准", source_hash="new_hash", status="proposed")
        db.add(new)
        db.commit()
        knowledge.activate_framework(db, new.id)
        db.expire_all()
        assert db.get(PropositionQuestion, qid).analysis_status == "stale"
        assert db.query(PropositionEvaluation).filter(PropositionEvaluation.framework_id == old_id).count() == 1


def test_config_validates_budget_and_hides_key(client):
    assert client.patch("/api/proposition/config", json={"budget_chars": -1}).status_code == 422
    assert client.patch("/api/proposition/config", json={"embedding_base_url": "file:///tmp"}).status_code == 422
    response = client.patch("/api/proposition/config", json={"embedding_api_key": "secret", "budget_chars": 0})
    assert response.status_code == 200
    assert "secret" not in response.text
    assert response.json()["embedding_api_key_configured"]
    preview = client.post("/api/proposition/guidance/preview", json={"budget_chars": 20_000, "complexity": "complex"})
    assert preview.status_code == 200 and preview.json()["chars"] == 0
    assert preview.json()["text"] == ""


def test_legacy_migration_is_idempotent_and_honest(client):
    from app.models import ItemModel
    with SessionLocal() as db:
        model = ItemModel(domain_key="math_logic", human_note="OLD_NOTE", radicals=jdump({"stem_sample": "仅存历史题面片段"}))
        db.add(model)
        db.commit()
        assert knowledge.migrate_legacy_evidence(db) == 1
        assert knowledge.migrate_legacy_evidence(db) == 0
        assert db.query(ItemModel).count() == 1
        assert "历史记录可能" in db.query(PropositionQuestion).first().error_message
        row = db.query(PropositionQuestion).first()
        assert row.parse_status == "failed"
        qid = row.id
    response = client.post(f"/api/proposition/questions/{qid}/retry")
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.get(PropositionQuestion, qid).parse_status == "failed"


def test_call_budget_defers_remaining_work(client):
    FakeAI.fail = False
    budget = knowledge._CallBudget(1)
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        qid = result["questions"][0]["id"]
        fid = knowledge.active_framework(db, "math_logic").id
    outcome = knowledge.analyse_question(qid, fid, ai_factory=lambda **kwargs: budget.wrap(FakeAI(**kwargs)))
    assert outcome["status"] == "pending"
    with SessionLocal() as db:
        assert db.get(PropositionQuestion, qid).analysis_status == "pending"
        assert db.query(PropositionEvaluation).first().blind_solution != "{}"


def test_resume_reuses_saved_blind_solution(client):
    FakeAI.calls = []
    FakeAI.fail = False
    budget = knowledge._CallBudget(1)
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload())
        qid = result["questions"][0]["id"]
        fid = knowledge.active_framework(db, "math_logic").id
    assert knowledge.analyse_question(qid, fid, ai_factory=lambda **kw: budget.wrap(FakeAI(**kw)))["status"] == "pending"
    assert knowledge.analyse_question(qid, fid, ai_factory=FakeAI)["status"] == "analysed"
    assert [c[0] for c in FakeAI.calls] == ["proposition_blind_solve", "proposition_evaluate"]


def test_numbered_answers_are_assigned_to_each_original_question(client):
    source = """# 第一套试卷
**1.** 求 $1+2$。
**2.** 求 $2+3$。
## 参考答案
**1.** ANSWER_ONE
**2.** ANSWER_TWO
# 第二套试卷
**1.** 求 $3+4$。
## 参考答案
**1.** ANSWER_THREE
"""
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload(source))
        assert result["source_text"] == source
        assert result["counts"]["total"] == 3
        for q, expected in zip(result["questions"], ["ANSWER_ONE", "ANSWER_TWO", "ANSWER_THREE"]):
            assert expected in q["raw_answer"]
            assert q["raw_answer"].count("ANSWER_") == 1
            assert "ANSWER_" not in q["raw_stem"]
            assert q["source_map"]["answer_sections"]


def test_plain_numbered_questions_after_notes_and_answer_lists(client):
    source = "1. 说明导数概念。\n评注：FIRST\n\n2. 说明极限概念。\n答案：\n1. 第一步\n2. 第二步\n"
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload(source))
        assert result["counts"]["total"] == 2
        assert "FIRST" not in result["questions"][0]["raw_stem"]
        assert "第二步" in result["questions"][1]["raw_answer"]


def test_external_bulleted_report_is_never_in_blind_stem(client):
    source = "## 第1题\n说明导数概念。\n- A 独立解答：EXTERNAL_SOLUTION\n- C 评注复核：EXTERNAL_NOTE\n"
    with SessionLocal() as db:
        q = knowledge.import_batch(db, payload(source))["questions"][0]
        assert "EXTERNAL_" not in q["raw_stem"]
        assert "EXTERNAL_SOLUTION" in q["external_evaluation"]


def test_report_without_original_question_is_parse_failure(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload("# 候选题评价 #1\n- 输入：题面已提供\n- A 独立解答：6\n- H 结论：可考虑入库\n"))
        assert result["counts"]["parse_failed"] == 1
        assert result["source_text"].startswith("# 候选题评价")


def test_renaming_file_is_still_duplicate(client):
    with SessionLocal() as db:
        first = knowledge.import_batch(db, payload())
        renamed = payload().model_copy(update={"filename": "renamed.md"})
        second = knowledge.import_batch(db, renamed)
        assert second["duplicate"] and second["id"] == first["id"]


def test_question_numbers_and_numeric_variations_are_not_independent_families(client):
    with SessionLocal() as db:
        result = knowledge.import_batch(db, payload("## 第1题\n求 $x^2$ 的导数。\n## 第2题\n求 $x^3$ 的导数。\n"))
        assert len({q["family_key"] for q in result["questions"]}) == 1


class ExperienceAI:
    is_demo = False
    negative = []
    statement = "用边界反例说明定义的必要条件"

    def _real_json(self, feature, system, user, required):
        import json
        samples = json.loads(user.split('\nJSON：')[0])["samples"]
        return {"principles": [{
            "title": "定义边界", "statement": self.statement,
            "mechanism": "比较充分与必要条件", "applicability": "定义学习阶段",
            "guidance": "给出一个边界反例", "scope": {"branch": "calculus", "learner": ""},
            "positive_refs": [s["question_id"] for s in samples if s["question_id"] not in self.negative],
            "negative_refs": self.negative, "confidence": .95, "rationale": "独立家族证据",
        }]}


def _evaluated_questions(db, stems, *, disagreement=False):
    from app.services.utils import jload
    FakeAI.fail = False
    text = "\n".join(f"## 第{i + 1}题\n{stem}" for i, stem in enumerate(stems))
    result = knowledge.import_batch(db, payload(text))
    fid = knowledge.active_framework(db, "math_logic").id
    ids = [q["id"] for q in result["questions"]]
    db.commit()
    for qid in ids:
        knowledge.analyse_question(qid, fid, ai_factory=FakeAI)
    db.expire_all()
    if disagreement:
        e = db.query(PropositionEvaluation).filter_by(question_id=ids[0]).first()
        report = jload(e.report, {})
        report["annotation_comparison"] = [{"disagreement": True}]
        e.report = jdump(report)
        db.commit()
    return ids, fid


def test_single_family_proposed_then_new_independent_family_auto_accepted(client):
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"])
        pid = knowledge.distill_evaluations(db, ids[:1], fid, ExperienceAI())[0]
        assert db.get(DesignPrinciple, pid).status == "proposed"
        assert knowledge.distill_evaluations(db, ids[1:], fid, ExperienceAI()) == []
        row = db.get(DesignPrinciple, pid)
        assert row.status == "auto_accepted" and row.approval_source == "automatic"
        assert row.evidence_family_count == 2 and row.version == 2


def test_disagreement_or_negative_evidence_blocks_auto_accept(client):
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"], disagreement=True)
        pid = knowledge.distill_evaluations(db, ids, fid, ExperienceAI())[0]
        assert db.get(DesignPrinciple, pid).status == "proposed"
        ai = ExperienceAI()
        ai.negative = ids[1:]
        knowledge.distill_evaluations(db, ids, fid, ai)
        row = db.get(DesignPrinciple, pid)
        assert row.status == "proposed"
        from app.services.utils import jload
        assert {r["question_id"] for r in jload(row.negative_refs, [])} == {ids[1]}


def test_withdrawn_principle_does_not_automatically_revive(client):
    from app.services.proposition import review_principle
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。", "求分段函数的可导条件。"])
        pid = knowledge.distill_evaluations(db, ids[:2], fid, ExperienceAI())[0]
        assert db.get(DesignPrinciple, pid).status == "auto_accepted"
        review_principle(db, pid, "withdraw", "需要重新验证适用范围")
        knowledge.distill_evaluations(db, ids[2:], fid, ExperienceAI())
        assert db.get(DesignPrinciple, pid).status == "proposed"


def test_prior_disagreement_is_not_erased_by_new_positive_evidence(client):
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"], disagreement=True)
        pid = knowledge.distill_evaluations(db, ids[:1], fid, ExperienceAI())[0]
        knowledge.distill_evaluations(db, ids[1:], fid, ExperienceAI())
        assert db.get(DesignPrinciple, pid).status == "proposed"


def test_invalid_model_contract_creates_failure_log_not_training(client):
    import pytest
    from app.llm.service import AIService, AIServiceError
    from app.models import ModelCallLog, TrainingRecord
    with SessionLocal() as db:
        ai = AIService(db=db)
        ai.provider = SimpleNamespace(model="fake", complete_json=lambda *args: ({"answer": "6", "solvable": "yes", "confidence": .9}, 1, None))
        with pytest.raises(AIServiceError):
            ai._real_json("proposition_blind_solve", "test", "test", ("answer", "solvable", "confidence"))
        db.commit()
        assert db.query(TrainingRecord).count() == 0
        log = db.query(ModelCallLog).one()
        assert log.status == "error" and log.feature == "proposition_blind_solve"


def test_specific_framework_activation_invalidates_prior_general_evidence(client):
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"])
        pid = knowledge.distill_evaluations(db, ids, fid, ExperienceAI())[0]
        new = EvaluationFramework(title="微积分新量规", domain_key="math_logic", version=1,
            source_hash="same-text-new-scope", source_text="规则",
            standards=jdump([{"id": "scope", "rule": "新规则", "source_quote": "规则",
                "scope": {"applies_to": "calculus"}, "priority": 2, "strength": "core",
                "evidence_required": "traceable evidence"}]))
        db.add(new)
        db.commit()
        knowledge.activate_framework(db, new.id)
        db.expire_all()
        assert db.get(DesignPrinciple, pid).status == "proposed"
        assert db.get(PropositionQuestion, ids[0]).analysis_status == "stale"


def test_new_framework_identity_does_not_reuse_old_evaluation(client):
    FakeAI.calls = []
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。"])
        old = db.get(EvaluationFramework, fid)
        new = EvaluationFramework(title="相同原文不同适用范围", domain_key="math_logic", version=1, source_hash=old.source_hash, source_text=old.source_text, standards=old.standards, status="active")
        db.add(new)
        db.commit()
        new_id = new.id
    FakeAI.calls = []
    assert knowledge.analyse_question(ids[0], new_id, ai_factory=FakeAI)["status"] == "analysed"
    assert len(FakeAI.calls) == 2
    with SessionLocal() as db:
        assert db.query(PropositionEvaluation).filter_by(question_id=ids[0]).count() == 2


def test_reassessment_disagreement_revokes_old_accepted_experience(client):
    class DisagreeAI(FakeAI):
        def _real_json(self, *args, **kwargs):
            value = super()._real_json(*args, **kwargs)
            if args[0] == "proposition_evaluate":
                value["annotation_comparison"] = [{"disagreement": True, "reason": "新复核不一致"}]
            return value
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"])
        pid = knowledge.distill_evaluations(db, ids, fid, ExperienceAI())[0]
        assert db.get(DesignPrinciple, pid).status == "auto_accepted"
    assert knowledge.analyse_question(ids[0], fid, force=True, ai_factory=DisagreeAI)["status"] == "analysed"
    with SessionLocal() as db:
        assert db.get(DesignPrinciple, pid).status == "proposed"


def test_same_question_cannot_be_both_positive_and_negative(client):
    class ContradictoryAI(ExperienceAI):
        def _real_json(self, *args):
            value = super()._real_json(*args)
            p = value["principles"][0]
            p["negative_refs"] = p["positive_refs"][:1]
            return value
    from app.services.utils import jload
    with SessionLocal() as db:
        ids, fid = _evaluated_questions(db, ["证明可导函数连续。", "给出连续但不可导的反例。"])
        pid = knowledge.distill_evaluations(db, ids, fid, ContradictoryAI())[0]
        row = db.get(DesignPrinciple, pid)
        positive = {r["question_id"] for r in jload(row.positive_refs, [])}
        negative = {r["question_id"] for r in jload(row.negative_refs, [])}
        assert not positive & negative and negative
        assert row.status == "proposed"

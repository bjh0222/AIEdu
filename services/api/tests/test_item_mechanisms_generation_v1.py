"""真实评价入口、不可变反馈与专业机制的离线契约；零真实模型调用。"""
from copy import deepcopy
import json

import pytest
from fastapi import HTTPException

from app.db import SessionLocal
from app.llm.service import AIServiceError, _validate_proposition_json
from app.models import DesignPrinciple, DesignPrincipleRevision, PropositionAnalysisJob, PropositionEvaluation, PropositionQuestion
from app.schemas import PropositionBatchImport
from app.services import proposition_knowledge as K, proposition_mechanisms as M, proposition_rubric as R
from app.services.generation_contract import validate_generation_message
from app.services.utils import jdump, jload


STEM = "## 第1题\n题面：设 $f(x)=x^2$。\n(1) 求导数。\n(2) 用定义证明连续。\n答案：ANSWER_SECRET\n第二小问完整证明 END_ANSWER。\n评注：NOTE_SECRET\n外部评价：EXTERNAL_SECRET\n"


def report(**updates):
    value = {"correctness": {"status": "supported", "reason": "逐一完成两小问的静态推导"},
             "difficulty": {"level": 2, "learner": "模型假设的本科生", "sources": ["需识别可导与连续的联系"]},
             "quality": {"strengths": ["基础概念联系"], "limitations": []},
             "teaching_value": "训练定义的应用", "collection_value": "可作为基础概念正例",
             "structure": {"mechanism": "定义到局部性质", "invariants": ["差商极限关系"],
                           "variable_factors": ["函数表达式"], "boundaries": ["不可略去可导条件"]},
             "controlled_variations": [{"preserved": ["差商极限关系"], "changed": ["撤去可导条件"],
                                       "variant": "讨论连续是否蕴含可导", "expected_demand": "需主动构造反例", "failure_boundary": "绝对值函数原点", "evidence_basis": "empirical"}],
             "confidence": .95, "recommendation": "priority", "uncertainties": [], "annotation_comparison": [],
             "knowledge_points": ["导数", "连续性"], "question_type": "证明题"}
    value.update(updates)
    return value


def seed(db, stem=STEM, **kwargs):
    batch = K.import_batch(db, PropositionBatchImport(filename="item.md", markdown=stem,
                          branch="calculus", learner="已学导数定义的本科生", auto_analyse=False, **kwargs))
    return batch, K.active_framework(db, batch["domain_key"]).id


def factory(calls, value=None):
    class Fake:
        is_demo = False
        def __init__(self, **kwargs):
            pass
        def _real_json(self, feature, system, user, required):
            calls.append((feature, system, user))
            if feature == "proposition_blind_solve":
                assert all(secret not in user for secret in ("ANSWER_SECRET", "NOTE_SECRET", "EXTERNAL_SECRET", "FEEDBACK_SECRET"))
                assert "(1)" in user and "(2)" in user
                return {"answer": "(1) 2x\n(2) 完整证明 LONG_PROOF_END", "verification": "逐项核对", "solvable": True, "confidence": .95}
            assert feature == "proposition_evaluate"
            return deepcopy(value or report())
    return Fake


def feedback(batch, question, *, event="event-1", outcome="recorded", text="FEEDBACK_SECRET 请复核第二小问"):
    return {"contract_version": "generation.v1", "kind": "FeedbackEvidence",
            "identity": {"task_id": batch, "goal_id": None, "tutorial_id": None, "version": None, "unit_id": question},
            "payload": {"event_id": event, "origin": "user", "text": text, "outcome": outcome,
                        "source_refs": [M.source_ref("feedback", event, 1, text, "comment")],
                        "target_refs": [M.source_ref("material", question, 1, location="raw_stem")], "evidence_method": "human"},
            "extensions": {}}


def test_real_evaluation_entry_preserves_blind_answers_and_emits_valid_wire_message(client):
    calls = []
    with SessionLocal() as db:
        batch, fid = seed(db)
        qid = batch["questions"][0]["id"]
        original = K.question_dict(db.get(PropositionQuestion, qid))
    result = K.analyse_question(qid, fid, ai_factory=factory(calls))
    assert result["status"] == "analysed"
    assert [entry[0] for entry in calls] == ["proposition_blind_solve", "proposition_evaluate"]
    assert "ANSWER_SECRET" in calls[1][2] and "END_ANSWER" in calls[1][2]
    with SessionLocal() as db:
        q = db.get(PropositionQuestion, qid)
        assert q.raw_text == original["raw_text"] and q.external_evaluation == original["external_evaluation"]
        assert K.digest(q.external_evaluation) == K.digest(original["external_evaluation"])
        e = K.evaluation_dict(db.query(PropositionEvaluation).one())
        assert "LONG_PROOF_END" in e["blind_solution"]["answer"]
        wire = e["report"]["item_assessment"]
        validate_generation_message(wire, "ItemAssessment")
        assert wire["identity"]["version"] == 1
        assert wire["payload"]["difficulty"]["learner"] == original["learner"]
        assert wire["extensions"]["controlled_variations"][0]["evidence_basis"] == "prior"
        assert wire["payload"]["correctness"]["source_refs"][0]["hash"] == K.digest(q.raw_stem)
    api = client.get(f"/api/proposition/questions/{qid}/assessment")
    assert api.status_code == 200 and api.json()["kind"] == "ItemAssessment"


@pytest.mark.parametrize("state,expected", [("refuted", "reject"), ("undecidable", "defer"), ("supported", "priority")])
def test_difficulty_and_praise_cannot_override_correctness(state, expected):
    value = report(correctness={"status": state, "reason": "判定依据"}, difficulty={"level": 5, "learner": "本科生"})
    normalized = M.normalize_model_report(value, {"domain_key": "math_logic", "branch": "calculus", "learner": "本科生"}, {"solvable": True})
    assert normalized["recommendation"] == expected
    assert normalized["quality"]["strengths"] == ["基础概念联系"]


def test_incomplete_blind_proof_cannot_be_accepted_by_second_stage_praise():
    normalized = M.normalize_model_report(report(), {"domain_key": "math_logic", "branch": "calculus"}, {"solvable": False})
    assert normalized["correctness"]["status"] == "undecidable" and normalized["recommendation"] == "defer"


def test_unconfirmed_model_learner_is_an_explicit_assumption():
    q = {"id": "q", "batch_id": "b", "domain_key": "math_logic", "branch": "calculus", "learner": ""}
    normalized = M.normalize_model_report(report(), q, {"solvable": True})
    wire = M.item_assessment(q, {"report": normalized, "status": "done", "analysis_version": 1})
    assert wire["extensions"]["learner_origin"] == "model_assumption"
    assert any("模型假设" in entry for entry in wire["payload"]["uncertainties"])
    normalized = M.normalize_model_report(report(difficulty={"level": 5}), q, {"solvable": True})
    assert normalized["difficulty"]["level"] is None and normalized["difficulty"]["evidence_basis"] == "unknown"


def test_legacy_and_demo_reports_do_not_fabricate_new_evidence():
    q = {"id": "q", "batch_id": "b", "domain_key": "general", "learner": ""}
    old = M.item_assessment(q, {"status": "done", "report": report(difficulty={"level": 5})})
    assert old["payload"]["difficulty"]["level"] is None
    assert old["extensions"]["legacy_adapter"] is True
    demo = M.item_assessment(q, {"status": "demo", "demo": True, "report": report()})
    assert demo["payload"]["correctness"]["status"] == "undecidable"
    assert not demo["payload"]["correctness"]["source_refs"]


def test_python_concepts_do_not_need_symbolic_expression_and_other_domains_are_bounded():
    adapter = M.domain_adapter("computer_cli", "Python")
    assert adapter["supported"] and any("可变性" in check for check in adapter["checks"])
    assert "不执行" in adapter["boundary"]
    other = M.domain_adapter("humanities", "history")
    assert other["supported"] is False and "尚无专用适配" in other["boundary"]


def test_mechanism_rich_prompt_preserves_user_rubric_and_does_not_claim_statistical_cause():
    rules = [{"id": "user-correct", "rule": "全部小问须独立成立", "origin": "user_skill_extracted"}]
    system, user = R.evaluation_prompt({"domain_key": "computer_cli", "branch": "Python", "raw_stem": "解释列表可变性", "source_map": {}}, {"answer": "独立算法说明"}, rules)
    assert "用户激活量规优先" in system and "数字/名字/措辞" in system
    assert "全部小问须独立成立" in user and "controlled_variations" in user and "python.v1" in user


@pytest.mark.parametrize("bad", [True, "5", 0, 6, 2.3])
def test_malformed_difficulty_model_contract_is_rejected(bad):
    with pytest.raises(AIServiceError):
        _validate_proposition_json("proposition_evaluate", report(difficulty={"level": bad}))


def test_invalid_structure_lists_are_rejected_before_model_result_consumption():
    with pytest.raises(AIServiceError):
        _validate_proposition_json("proposition_evaluate", report(structure={"mechanism": "m", "invariants": "not-a-list"}))


def test_numeric_question_number_and_small_wording_changes_do_not_create_families():
    assert K._family_key("## 第1题\n求出 $x^2$ 的导数。") == K._family_key("## 第8题\n计算 $x^9$ 的导数！")
    assert K._family_key("试证明可导函数连续。") == K._family_key("请证明可导函数连续")
    assert K._family_key("证明可导函数连续") != K._family_key("构造连续但不可导的反例")


def test_feedback_is_valid_immutable_idempotent_and_does_not_start_model_for_record_only(client):
    with SessionLocal() as db:
        batch, _ = seed(db)
        qid = batch["questions"][0]["id"]
        q = db.get(PropositionQuestion, qid)
        original_external, original_answer, original_map = q.external_evaluation, q.raw_answer, jload(q.source_map, {})
        message = feedback(batch["id"], qid)
        validate_generation_message(message, "FeedbackEvidence")
        result = K.receive_feedback(db, qid, message, background=False)
        assert result["job"] is None and not result["duplicate"]
        assert K.receive_feedback(db, qid, message, background=False)["duplicate"] is True
        q = db.get(PropositionQuestion, qid)
        assert q.external_evaluation == original_external and q.raw_answer == original_answer
        saved = jload(q.source_map, {})
        assert {k: v for k, v in saved.items() if k != "feedback_events"} == original_map
        assert saved["feedback_events"] == [message]
        assert db.query(PropositionEvaluation).count() == db.query(PropositionAnalysisJob).count() == 0
        changed = deepcopy(message); changed["payload"]["text"] = "rewrite"
        with pytest.raises(HTTPException) as error:
            K.receive_feedback(db, qid, changed)
        assert error.value.status_code == 409


def test_feedback_reassessment_is_targeted_versioned_and_only_second_stage_sees_feedback(client):
    calls = []
    with SessionLocal() as db:
        batch, fid = seed(db)
        qid = batch["questions"][0]["id"]
    K.analyse_question(qid, fid, ai_factory=factory(calls))
    with SessionLocal() as db:
        first = db.query(PropositionEvaluation).one()
        first_report = first.report
        message = feedback(batch["id"], qid, outcome="review_requested")
        result = K.receive_feedback(db, qid, message, background=False)
        assert result["job"]["question_ids"] == [qid]
        assert first.report == first_report
    new_calls = []
    K._run_job(result["job"]["id"], ai_factory=factory(new_calls))
    assert any("FEEDBACK_SECRET" in user for feature, _, user in new_calls if feature == "proposition_evaluate")
    assert all("FEEDBACK_SECRET" not in user for feature, _, user in new_calls if feature == "proposition_blind_solve")
    with SessionLocal() as db:
        rows = db.query(PropositionEvaluation).order_by(PropositionEvaluation.analysis_version).all()
        assert len(rows) == 2 and rows[0].report == first_report and rows[1].analysis_version == 2
        assert rows[0].fingerprint != rows[1].fingerprint


@pytest.mark.parametrize("mutation", ["version", "unknown", "target"])
def test_feedback_rejects_unknown_protocol_and_wrong_references(client, mutation):
    with SessionLocal() as db:
        batch, _ = seed(db)
    qid = batch["questions"][0]["id"]
    message = feedback(batch["id"], qid)
    if mutation == "version": message["contract_version"] = "generation.v2"
    elif mutation == "unknown": message["payload"]["execute"] = "side effect"
    else: message["identity"]["unit_id"] = "wrong-unit"
    response = client.post(f"/api/proposition/questions/{qid}/feedback", json=message)
    assert response.status_code == 422


def test_scope_only_contains_retrieval_conditions_and_mechanism_details_are_preserved():
    value = {"mechanism": "改变条件结构", "scope": {"branch": "calculus", "knowledge_points": ["导数"], "invariants": ["差商"], "exclude": {"knowledge_points": ["偏导数"]}}}
    cleaned = M.retrieval_scope(value["scope"])
    assert "invariants" not in cleaned and cleaned["exclude"] == value["scope"]["exclude"]
    assert "差商" in M.principle_mechanism(value) and "先验比较" in M.principle_mechanism(value)


def test_grounded_comparisons_reject_invented_sources_and_keep_static_evidence():
    samples = {"q1": {"stem": "证明可导函数连续"}, "q2": {"stem": "构造连续不可导的反例"}}
    entries = [{"question_ids": ["q1", "q2"], "preserved": ["连续与可导的关系"], "changed": ["逆命题"], "evidence_basis": "empirical"},
               {"question_ids": ["q1", "invented-question"]}]
    accepted, warnings = M.grounded_comparisons(entries, samples)
    assert len(accepted) == len(warnings) == 1
    assert accepted[0]["evidence_basis"] == "prior"
    assert accepted[0]["source_refs"][0]["hash"] == K.digest(samples["q1"]["stem"])


def test_malformed_legacy_report_remains_readable_and_unknown():
    value = M.item_assessment({"id": "q", "domain_key": "general"}, {
        "status": "done", "report": {"correctness": "unknown old shape", "difficulty": True, "quality": None, "structure": [], "recommendation": "old-value"}})
    assert value["payload"]["correctness"]["status"] == "undecidable" and value["payload"]["recommendation"] == "defer"


def test_counterexample_revokes_prior_acceptance_and_preserves_revision(client):
    with SessionLocal() as db:
        batch, fid = seed(db)
        qid = batch["questions"][0]["id"]
        q = db.get(PropositionQuestion, qid)
        p = DesignPrinciple(domain_key=q.domain_key, statement="可迁移原理", mechanism="差商", applicability="微积分", status="confirmed",
                            scope=jdump({"branch": "calculus", "learner": q.learner}), positive_refs=jdump([{"question_id": qid, "family_key": q.family_key}]), framework_id=fid)
        db.add(p); db.commit()
        sample = {"question_id": qid, "family_key": q.family_key, "learner": q.learner, "branch": "calculus", "report": report()}
        result = {"principles": [{"statement": p.statement, "mechanism": "差商", "applicability": "微积分", "scope": {"branch": "calculus", "learner": q.learner},
                                  "positive_refs": [qid], "counterexamples": ["缺少可导条件时失效"], "confidence": .95}]}
        K._save_distilled_evaluations(db, [sample], fid, result)
        assert p.status == "proposed" and p.version == 2
        assert jload(p.counterexamples, []) == ["缺少可导条件时失效"]
        assert db.query(DesignPrincipleRevision).filter_by(principle_id=p.id, action="evidence_contested").count() == 1

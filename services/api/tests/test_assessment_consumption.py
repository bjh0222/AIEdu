"""Required automatic assessments are consumed, not decorative report fields."""
from copy import deepcopy

import pytest

from app.v2.contracts import sha


def fixed_assessments():
    from app.v2.assessment_binding import input_binding
    from app.v2.criterion_review import bind_rows, requests_for_unit, summarize
    from app.v2.difficulty_review import evaluate_difficulty
    # Use actual consumers and a complete source-bound synthetic C observation.
    from test_difficulty_review import fixture
    row, item, unit = fixture()
    unit.update(body="fixed", artifact_role="exercise", content_hash=sha("fixed"))
    state = {"execution_policy": {"criterion_assessment": True}, "context": {}, "check_plan": {"check_items": [
        {"item_id": "G1.semantic_alignment", "decision_type": "semantic", "verifier_binding": "fixture"}]}}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.v2.inference.invoke", lambda *a, **k: ({"items": [dict(row, item_id=item["item_id"])]}, {}))
        difficulty = evaluate_difficulty("r", "w", 0, state, [unit], "v")
    requests, _ = requests_for_unit(state, unit)
    raw = {"assessments": [{"request_id": r["request_id"], "clauses": [{"clause_id": c,
        "result": "satisfied", "reason": "A located synthetic content observation, not certification",
        "evidence": [{"field": "unit:body", "quote": "fixed"}]} for c in r["criterion"]["clauses"]]} for r in requests]}
    rows = bind_rows(raw, requests, {"unit:body": "fixed"}, {}, unit["unit_id"])
    automatic = summarize(rows, len(requests), []) | {"version_id": "v", "input_binding": input_binding(state, [unit], "v")}
    return state, [unit], {"difficulty_review": difficulty, "automated_assessment": automatic}


@pytest.mark.parametrize("outcome", ["violated", "undecidable", None, "satisfied"])
def test_broad_model_acceptance_cannot_override_actual_difficulty_gate(monkeypatch, outcome):
    from app.v2.optimization_acceptance import verify
    def invoke(*args, **kw):
        return {"criteria": [{"item_id": c["item_id"], "result": "satisfied",
            "evidence": [{"unit_id": "u1", "quote": "fixed"}]} for c in kw["data"]["criteria"]]}, {"call_id": "synthetic"}
    monkeypatch.setattr("app.v2.inference.invoke", invoke)
    state, units, checked = fixed_assessments()
    if outcome is None: del checked["difficulty_review"]
    else:
        checked["difficulty_review"]["result"] = outcome
        if outcome != "satisfied":
            checked["difficulty_review"]["items"][0]["result"] = outcome
    result = verify("r", "w", 0, state,
        {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "broad"}]},
        units, recheck=checked, version_id="v")
    assert (result["status"] == "satisfied") == (outcome == "satisfied")
    assert any(r["item_id"] == "task.difficulty_review" for r in result["criteria"])


@pytest.mark.parametrize("key", ["difficulty_review", "automated_assessment"])
@pytest.mark.parametrize("fault", ["label_only", "incomplete", "old_version", "changed_content", "changed_target",
    "changed_plan", "missing_row", "duplicate_row", "wrong_identity", "fake_coverage", "forged_aggregate", "malformed_report"])
def test_stale_or_incomplete_positive_report_never_waives_the_gate(key, fault, monkeypatch):
    from app.v2.optimization_acceptance import verify
    state, units, checked = fixed_assessments()
    report = checked[key]
    rows_key = "items" if key == "difficulty_review" else "criteria"
    if fault == "label_only": checked[key] = {"result": "satisfied", "status": "completed"}
    if fault == "incomplete": report["status"] = "incomplete"
    if fault == "old_version": report["version_id"] = "old"
    if fault == "changed_content": units[0]["items"][0]["answer"]["final"] += "changed without body hash change"
    if fault == "changed_target": units[0]["output_design"]["difficulty_target"]["minimum_dependency_depth"] += 1
    if fault == "changed_plan": state["check_plan"]["check_items"][0]["required"] = True
    if fault == "missing_row": report[rows_key] = []
    if fault == "duplicate_row": report[rows_key] *= 2
    if fault == "wrong_identity": report[rows_key][0]["unit_id"] = "another-unit"
    if fault == "fake_coverage": report["coverage"]["expected"] = 0
    if fault == "forged_aggregate": report[rows_key][0]["result"] = "violated"
    if fault == "malformed_report": checked[key] = ["satisfied"]
    monkeypatch.setattr("app.v2.inference.invoke", lambda *a, **kw: ({"criteria": [{"item_id": c["item_id"],
        "result": "satisfied", "evidence": [{"unit_id": "u1", "quote": "fixed"}]} for c in kw["data"]["criteria"]]},
        {"call_id": "synthetic"}))
    result = verify("r", "w", 0, state, {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "broad"}]},
        units, recheck=checked, version_id="v")
    row = next(r for r in result["criteria"] if r["item_id"] == "task." + key)
    assert row["result"] not in {"satisfied", "not_applicable"}
    assert result["status"] == "unresolved"


@pytest.mark.parametrize("fault", ["clause_result", "content_span", "boolean_coverage", "missing_version"])
def test_consumer_recomputes_inner_evidence_and_requires_candidate_identity(fault):
    from app.v2.assessment_binding import consume
    state, units, checked = fixed_assessments()
    report = checked["automated_assessment"]
    if fault == "clause_result": report["criteria"][0]["clauses"][0]["result"] = "violated"
    if fault == "content_span": report["criteria"][0]["clauses"][0]["evidence"][0]["end_byte"] += 1
    if fault == "boolean_coverage": report["coverage"]["expected"] = True
    outcome, errors = consume(report, "automated_assessment", state, units, None if fault == "missing_version" else "v")
    assert outcome == "undecidable" and errors


def test_difficulty_spans_and_known_negative_are_not_overridden_by_positive_aggregate():
    from app.v2.assessment_binding import consume
    from app.v2.difficulty_review import bind_item
    from test_difficulty_review import fixture
    state, units, checked = fixed_assessments()
    report = checked["difficulty_review"]
    report["items"][0]["dimensions"]["steps"][0]["evidence"][0]["end_byte"] += 1
    assert consume(report, "difficulty_review", state, units, "v")[0] == "undecidable"
    row, item, _ = fixture()
    row["nonroutine_operations"] = []
    report["items"] = [bind_item(row, item, units[0], {})]
    # Keep the erroneous aggregate deliberately: the valid negative survives.
    outcome, errors = consume(report, "difficulty_review", state, units, "v")
    assert outcome == "violated" and "assessment_aggregate_mismatch" in errors


@pytest.mark.parametrize("domain", ["biology_life", "cs_it", "history_culture", "economics_business", "math_logic", "interdisciplinary"])
def test_producer_and_acceptance_use_the_same_frozen_general_contract_across_domains(domain, monkeypatch):
    from app.v2.assessment_binding import consume, input_binding
    from app.v2.criterion_review import evaluate_criteria
    state, units, _ = fixed_assessments()
    state["context"]["domain_key"] = domain
    monkeypatch.setattr("app.v2.checker.blind_bundle", lambda *a: {"task": "frozen task", "sources": []})
    seen = []
    def generate(*a, **kw):
        seen.append(kw)
        return {"assessments": [{"request_id": r["request_id"], "clauses": [{"clause_id": c,
            "result": "satisfied", "reason": "synthetic located evidence for contract execution only",
            "evidence": [{"field": "unit:body", "quote": "fixed"}]} for c in r["criterion"]["clauses"]]}
            for r in kw["data"]["requests"]]}, {"call_id": "synthetic"}, {}
    monkeypatch.setattr("app.v2.generation_tools.generation_with_sources", generate)
    report = evaluate_criteria("r", "w", 0, state, units, "candidate-v")
    assert seen and report["version_id"] == "candidate-v"
    assert report["input_binding"] == input_binding(state, units, "candidate-v")
    assert consume(report, "automated_assessment", state, units, "candidate-v") == ("satisfied", [])
    assert not report["professional_certification"]


def test_per_question_regression_guard_uses_stable_slot_not_fresh_UUID():
    from app.v2.optimization import diagnostic_regressions
    before = {"difficulty_review": {"items": [{"unit_id": "u", "ordinal": 1, "item_id": "old", "result": "satisfied"}]},
              "automated_assessment": {"criteria": [{"unit_id": "u", "ordinal": 1, "item_id": "old", "check_id": "Q4.x",
                  "criterion_hash": "h", "result": "satisfied", "rating": 3}]}}
    after = deepcopy(before)
    after["difficulty_review"]["items"][0].update(item_id="new", result="violated")
    after["automated_assessment"]["criteria"][0].update(item_id="new", rating=1)
    assert len(diagnostic_regressions(before, after)) == 2
    after = deepcopy(before)
    after["difficulty_review"]["items"][0]["item_id"] = "new"
    assert diagnostic_regressions(before, after) == []


def test_rejected_regression_feedback_is_bounded_and_marked_unconfirmed():
    from app.v2.optimization import rejected_regression_feedback
    state = {"rejected_candidate_regressions": [{
        "candidate_hash": f"candidate-{i}", "candidate_version": f"version-{i}",
        "authority": "must be replaced by fixed contract",
        "regressions": [{
            "unit_id": "u", "dimension_id": "d", "binding_hash": "b",
            "before": "satisfied", "after": "violated", "reason": "score_lost",
            "large_unneeded_payload": "x" * 1000,
        }],
    } for i in range(6)]}
    rows = rejected_regression_feedback(state)
    assert [row["candidate_hash"] for row in rows] == [f"candidate-{i}" for i in range(2, 6)]
    assert all(row["authority"] == "unpublished_candidate_observation_not_confirmed_fact" for row in rows)
    assert set(rows[0]["regressions"][0]) == {
        "unit_id", "dimension_id", "binding_hash", "before", "after", "reason"}


def test_progress_identity_includes_hidden_document_end_answers_not_UUID():
    from app.v2.optimization import unit_content_identity
    original = {"unit_id": "u", "body": "题面", "items": [{"item_id": "old", "content_hash": "oldhash",
        "stem_markdown": "题面", "answer": {"final": "甲"}}]}
    renamed = deepcopy(original)
    renamed["items"][0].update(item_id="new", content_hash="newhash")
    renamed["items"][0]["difficulty_basis"] = {"label": "a newly invented claim of higher difficulty"}
    assert unit_content_identity(original) == unit_content_identity(renamed)
    renamed["items"][0]["answer"]["final"] = "乙"
    assert unit_content_identity(original) != unit_content_identity(renamed)


@pytest.mark.parametrize("fixed_recheck", [False, True])
def test_rejected_assessment_keeps_best_and_continues_only_generation_cycle(client, monkeypatch, fixed_recheck):
    from test_task_run_v2 import task_run
    from test_optimization_prompt_loop import _prepare
    from app.models import TutorialVersion
    from app.v2.runtime import job, load, materialize, write
    from app.v2.optimization_recheck import finish_candidate
    from app.v2.optimization import unit_content_identity
    from app.v2.contracts import digest
    _, _, snap = task_run(client)
    _prepare(client, snap)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["execution_policy"] = {"criterion_assessment": True}
        version = db.get(TutorialVersion, snap["version_id"])
        units = materialize(version)["units"]
        # Expire_on_commit is disabled by runtime; only immutable scalar fields
        # are read from version below. No provider call is made in this test.
    monkeypatch.setattr("app.v2.optimization_acceptance.verify", lambda *a, **k: {
        "status": "unresolved", "audit": {}, "criteria": [{"item_id": "task.difficulty_review", "result": "violated"}]})
    monkeypatch.setattr("app.v2.optimization._persist_artifact", lambda *a, **k: None)
    continued = finish_candidate(snap["run_id"], "fixture", 1, state, version, units, units, {},
        {"checks": [], "findings": []}, {}, {}, {}, {}, [], "new-version", 0,
        prior_observations=[] if fixed_recheck else None)
    assert continued is (not fixed_recheck)
    with write() as db:
        saved = load(job(db, snap["run_id"]).state_json)
        assert saved["version_id"] == snap["version_id"]
        if fixed_recheck:
            assert saved["optimization_stop_reason"] == "optimization_acceptance_unresolved"
        else:
            assert saved["last_rejected_candidate_hash"] == digest([(u["unit_id"], unit_content_identity(u)) for u in units])
            assert saved["optimization_rounds"] == 1


@pytest.mark.parametrize("fixed_recheck", [False, True])
def test_diagnostic_regression_continues_only_generation_cycle(client, monkeypatch, fixed_recheck):
    from test_task_run_v2 import task_run
    from test_optimization_prompt_loop import _prepare
    from app.models import TutorialVersion
    from app.v2.runtime import job, load, materialize, write
    from app.v2.optimization_recheck import finish_candidate
    from app.v2.contracts import digest
    from app.v2.optimization import unit_content_identity
    _, _, snap = task_run(client)
    _prepare(client, snap)
    with write() as db:
        obj = job(db, snap["run_id"])
        state = load(obj.state_json)
        state["execution_policy"] = {"criterion_assessment": True}
        version = db.get(TutorialVersion, snap["version_id"])
        units = materialize(version)["units"]
    base = {"diagnostic_review": {"dimensions": [{
        "unit_id": units[0]["unit_id"], "dimension_id": "explanation_and_representation",
        "binding_hash": "binding", "result": "satisfied", "score": 2,
    }]}, "checks": [], "findings": []}
    rejected = deepcopy(base)
    rejected["diagnostic_review"]["dimensions"][0].update(result="violated", score=1)
    monkeypatch.setattr("app.v2.optimization._persist_artifact", lambda *a, **k: None)
    continued = finish_candidate(
        snap["run_id"], "fixture", 1, state, version, units, units, base, rejected,
        {}, {}, {}, {}, [], "new-version", 0,
        prior_observations=[] if fixed_recheck else None,
    )
    assert continued is (not fixed_recheck)
    with write() as db:
        saved = load(job(db, snap["run_id"]).state_json)
        assert saved["version_id"] == snap["version_id"]
        if fixed_recheck:
            assert saved["optimization_stop_reason"] == "diagnostic_regression"
            assert not saved.get("rejected_candidate_regressions")
        else:
            assert saved["last_rejected_candidate_hash"] == digest(
                [(u["unit_id"], unit_content_identity(u)) for u in units])
            assert saved["optimization_rounds"] == 1
            feedback = saved["rejected_candidate_regressions"][-1]
            assert feedback["regressions"][0]["unit_id"] == units[0]["unit_id"]
            assert feedback["regressions"][0]["dimension_id"] == "explanation_and_representation"
            assert feedback["authority"].endswith("not_confirmed_fact")

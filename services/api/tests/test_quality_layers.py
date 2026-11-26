"""S10 quality_layers: frozen policy, layer annotation, honest disabled states."""
from copy import deepcopy
from uuid import uuid4

import pytest

MATH_TASK = {"title": "t", "raw_input": "r", "task_intent": {},
             "spec_bindings": [{"artifact_role": "paper", "scene": "exam_paper"}],
             "subject_bindings": [{"domain_key": "math_logic"}],
             "requested_artifacts": ["paper"]}


def compile(task=None, layers=None):
    from app.v2.quality import compile_plan, normalize_quality_layers
    return compile_plan(deepcopy(task or MATH_TASK), quality_layers=normalize_quality_layers(layers))


def math_state(plan, **policy):
    return {"execution_policy": {"criterion_assessment": True, **policy},
            "context": {"domain_key": "math_logic", "raw_input": "r", "hard_constraints": [],
                        "spec_bindings": [], "material_ids": []},
            "check_plan": plan}


def test_quality_layers_defaults_force_correctness_and_reject_invalid():
    from app.v2.contracts import DomainError
    from app.v2.quality import normalize_quality_layers
    assert normalize_quality_layers(None) is None
    frozen = normalize_quality_layers({})
    assert frozen == {"method_version": "quality-layers.v1", "correctness": True,
                      "completeness": True, "quality_specific": False}
    assert normalize_quality_layers({"completeness": False})["correctness"] is True
    assert normalize_quality_layers({"quality_specific": True})["quality_specific"] is True
    with pytest.raises(DomainError) as exc:
        normalize_quality_layers({"correctness": False})
    assert exc.value.code == "QUALITY_LAYER_POLICY_INVALID"
    for bad in ("x", [], ["correctness"], {"correctness": 1}, {"unknown": True}, {"completeness": "true"}):
        with pytest.raises(DomainError) as exc:
            normalize_quality_layers(bad)
        assert exc.value.code == "QUALITY_LAYER_POLICY_INVALID"


def test_compile_plan_annotates_layers_and_binds_policy_into_hash():
    plan_min = compile(layers={"completeness": False})
    ids = {i["item_id"]: i for i in plan_min["check_items"]}
    assert plan_min["quality_layers"]["method_version"] == "quality-layers.v1"
    for item in plan_min["check_items"]:
        assert item["quality_layer"] in {"correctness", "completeness", "quality_specific"}
        assert item["hard_baseline"] is (item["quality_layer"] == "correctness")
    for item_id in ("C1.coverage", "C2.structure", "C5.materials", "C6.context_integrity"):
        assert ids[item_id]["quality_layer"] == "correctness" and ids[item_id]["hard_baseline"]
    q1 = next(i for i in plan_min["check_items"] if i["item_id"].startswith("Q1."))
    q2 = next(i for i in plan_min["check_items"] if i["item_id"].startswith("Q2."))
    q6 = next(i for i in plan_min["check_items"] if i["item_id"].startswith("Q6."))
    assert all(i["quality_layer"] == "correctness" and i["hard_baseline"] for i in (q1, q2, q6))
    g1 = ids["G1.semantic_alignment"]
    assert g1["quality_layer"] == "completeness" and not g1["hard_baseline"]
    assert ids["G3.assessment_design"]["quality_layer"] == "quality_specific"
    assert ids["G4.evidence_boundaries"]["quality_layer"] == "correctness"
    assert next(i for i in plan_min["check_items"] if i["item_id"].startswith("S.format."))["quality_layer"] == "completeness"
    # 分层策略进入 check_plan_hash：不同策略不同 hash；同策略同 hash。
    assert plan_min["check_plan_hash"] != compile(layers={"completeness": True})["check_plan_hash"]
    assert plan_min["check_plan_hash"] == compile(layers={"completeness": False})["check_plan_hash"]
    assert plan_min["check_plan_hash"] != compile(layers=None)["check_plan_hash"]


def test_legacy_plan_without_quality_layers_keeps_current_behavior():
    legacy = compile(layers=None)
    assert "quality_layers" not in legacy
    from app.v2.checker_batch import semantic_check_items
    state = math_state(legacy)
    items = semantic_check_items(state)
    from app.v2.derivation import is_derived_target
    semantic_all = [i for i in legacy["check_items"] if i["decision_type"] != "deterministic"
                    and not is_derived_target(state, i)
                    and i.get("verifier_binding") not in {"research_rubric.v1", "ps51_sandbox.v1", "target_runtime.v1", "criterion_review.v1"}]
    assert sorted(i["item_id"] for i in items) == sorted(i["item_id"] for i in semantic_all)
    from app.v2.quality import finding_layer_enabled
    for item in legacy["check_items"]:
        assert finding_layer_enabled(legacy, {"item_id": item["item_id"]}) is True


def test_disabled_layers_record_honest_state_and_hard_baselines_still_block():
    from app.v2.quality import reduce_quality
    plan = compile(layers={"completeness": False, "quality_specific": False})
    q1 = next(i["item_id"] for i in plan["check_items"] if i["item_id"].startswith("Q1."))
    checks = [{"item_id": "C1.coverage", "result": "satisfied"},
              {"item_id": "G1.semantic_alignment", "result": "not_run"},
              {"item_id": "G3.assessment_design", "result": "not_run"},
              {"item_id": q1, "result": "not_run"}]
    reduced = reduce_quality(plan, checks, [])
    by = {c["item_id"]: c for c in reduced["checks"]}
    for disabled in ("G1.semantic_alignment", "G3.assessment_design"):
        assert by[disabled]["result"] == "not_applicable"
        assert by[disabled]["reason"] == "quality_layer_disabled_by_policy"
        assert not any(r.get("item_id") == disabled for r in reduced["blocking_reasons"])
    # 硬底线与 correctness 不因分层关闭而放行。
    assert any(r.get("item_id") == q1 for r in reduced["blocking_reasons"])
    assert any(r.get("item_id") == "C2.structure" for r in reduced["blocking_reasons"])
    assert reduced["tqi_verdict"] == "blocked"


def test_deterministic_checks_skip_disabled_rows_and_keep_hard_baselines():
    from app.v2.contracts import sha
    from app.v2.quality import deterministic_checks
    plan = compile(layers={"completeness": False, "quality_specific": False})
    unit = {"unit_id": "u", "body": "ok", "content_hash": sha("ok"), "artifact_role": "paper"}
    result = deterministic_checks(plan, [unit], "v")
    rows = {c["item_id"]: c for c in result["checks"]}
    disabled = [i for i in plan["check_items"] if not i["hard_baseline"]
                and i["quality_layer"] in {"completeness", "quality_specific"}]
    for item in disabled:
        row = rows[item["item_id"]]
        assert row["result"] == "not_applicable"
        assert row["reason"] == "quality_layer_disabled_by_policy"
    assert rows["C1.coverage"]["result"] == "satisfied"


def test_semantic_batch_filters_disabled_layers_and_keeps_correctness():
    from app.v2.checker_batch import semantic_check_items
    plan = compile(layers={"completeness": False, "quality_specific": False})
    items = semantic_check_items(math_state(plan))
    ids = {i["item_id"] for i in items}
    # Disabled completeness/quality_specific rows never dispatch.
    for item in plan["check_items"]:
        if item["hard_baseline"]:
            continue
        if item["quality_layer"] in {"completeness", "quality_specific"} and item["decision_type"] != "deterministic":
            assert item["item_id"] not in ids, item["item_id"]
    # 数学正确性行 Q1/Q2/Q6 仍逐题盲解+审阅；C3 经 derivation 由 Q1/Q2 派生。
    for prefix in ("Q1.", "Q2.", "Q6."):
        assert any(i["item_id"].startswith(prefix) for i in items)
    assert "C3.correctness" not in ids  # derived target, not a batch row


def test_criterion_requests_follow_enabled_layers_and_cannot_flow_back():
    from app.v2.criterion_review import requests_for_unit
    plan = compile(layers={"completeness": False, "quality_specific": False})
    state = math_state(plan)
    state["plan"] = {"units": [{"unit_id": "u", "teaching_arrangement": {
        "knowledge_chain": [{"source_concept": "a", "target_concept": "b", "relation": "supports",
                             "evidence_ref": "e1"}],
        "question_blueprint": [{"group": "g", "target": "t", "question_form": "calculation",
                                "quantity": 1, "answer_requirement": "coupled"}],
        "misconceptions": [{"plausible_idea": "x", "failure_boundary": "y", "contrast_or_counterexample": "z"}],
        "acceptance_criteria": ["frozen criterion"]}}]}
    unit = {"unit_id": "u", "body": "content", "artifact_role": "paper",
            "items": [{"item_id": "i1", "ordinal": 1, "stem_markdown": "1+1=?",
                       "answer": {"final": "2", "reasoning": "obvious"}}]}
    requests, semantic = requests_for_unit(state, unit)
    check_ids = {r["check_id"] for r in requests}
    assert "G1.semantic_alignment" not in check_ids
    assert "G3.assessment_design" not in check_ids
    assert "G4.evidence_boundaries" in check_ids
    # completeness=false 时 A 的知识链/蓝图/误解/验收行不再生成请求。
    assert not any(r["check_id"].startswith("A.") for r in requests)
    plan_full = compile(layers={"completeness": True, "quality_specific": True})
    requests_full, _ = requests_for_unit({**state, "check_plan": plan_full}, unit)
    full_ids = {r["check_id"] for r in requests_full}
    assert any(r["check_id"].startswith("A.knowledge") for r in requests_full)
    assert "G1.semantic_alignment" in full_ids


def test_disabled_criterion_items_return_honest_binding_state_not_unknown():
    from app.v2.assessment_binding import criterion_check_results, input_binding
    plan = compile(layers={"completeness": False, "quality_specific": False})
    state = math_state(plan)
    report = {"version_id": "v", "criteria": [], "coverage": {"expected": 0, "recorded": 0,
              "complete_observations": 0, "decided": 0}, "status": "completed", "result": "not_applicable",
              "input_binding": input_binding(state, [], "v")}
    reduced = criterion_check_results(report, state, [], "v")
    row = reduced["G3.assessment_design"]
    assert row["result"] == "not_applicable"
    assert row["reason"] == "quality_layer_disabled_by_policy"


def test_difficulty_and_research_review_only_run_with_quality_specific(monkeypatch):
    from app.v2 import checker
    plan = compile(layers={"completeness": True, "quality_specific": False})
    state = math_state(plan)
    state["context"]["research_rubrics"] = [{"id": "r"}]
    monkeypatch.setattr(checker, "_evaluate_core", lambda *a, **k: {"checks": [], "findings": []})
    calls = {"difficulty": 0, "criteria": 0, "research": 0}
    monkeypatch.setattr("app.v2.difficulty_review.evaluate_difficulty",
                        lambda *a, **k: calls.__setitem__("difficulty", calls["difficulty"] + 1) or {"result": "satisfied"})
    monkeypatch.setattr("app.v2.criterion_review.evaluate_criteria",
                        lambda *a, **k: calls.__setitem__("criteria", calls["criteria"] + 1) or {"result": "satisfied"})
    monkeypatch.setattr("app.v2.import_profiles.review_research_rubrics",
                        lambda *a, **k: calls.__setitem__("research", calls["research"] + 1) or {"dimensions": []})
    checker.evaluate("r", "w", 0, state, [], "v")
    # quality_specific=false：难度审阅与研究量规审阅不执行；Criterion Review（内部机制）仍执行。
    assert calls == {"difficulty": 0, "criteria": 1, "research": 0}
    plan_on = compile(layers={"completeness": True, "quality_specific": True})
    calls.clear(); calls.update({"difficulty": 0, "criteria": 0, "research": 0})
    checker.evaluate("r", "w", 0, {**state, "check_plan": plan_on}, [], "v")
    assert calls == {"difficulty": 1, "criteria": 1, "research": 1}


def test_optimization_and_repairs_only_process_enabled_layer_findings():
    from app.v2.optimization import enabled_confirmed_findings
    plan = compile(layers={"completeness": False, "quality_specific": False})
    findings = [
        {"finding_id": "f1", "item_id": "C2.structure", "status": "confirmed",
         "acceptance_assertion": {"method": "markdown.v1"}},
        {"finding_id": "f2", "item_id": "G1.semantic_alignment", "status": "confirmed",
         "acceptance_assertion": {"method": "criterion_review.v1"}},
        {"finding_id": "f3", "item_id": "G3.assessment_design", "status": "confirmed"},
    ]
    assert [f["finding_id"] for f in enabled_confirmed_findings(plan, findings)] == ["f1"]
    legacy = compile(layers=None)
    assert [f["finding_id"] for f in enabled_confirmed_findings(legacy, findings)] == ["f1", "f2", "f3"]
    # 未绑定到 plan 的 finding 保持可见，不静默消失。
    assert [f["finding_id"] for f in enabled_confirmed_findings(plan, [{"finding_id": "f4", "item_id": "unknown", "status": "confirmed"}])] == ["f4"]


def test_invalid_quality_layers_fail_explicitly_before_start(client, monkeypatch):
    from app.config import settings
    from app.v2.contracts import Confirmation, DomainError, StartRun, TaskInput
    from app.v2.tasks import create_task, mutate_input, start_run
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(TaskInput(title="t", raw_input="r",
                                    execution_policy={"quality_layers": {"correctness": False}}), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    with pytest.raises(DomainError) as exc:
        start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"],
                                              mode="configured"), str(uuid4()))
    assert exc.value.code == "QUALITY_LAYER_POLICY_INVALID"
    from app.db import SessionLocal
    from app.models import GenerationJob
    with SessionLocal() as db:
        assert db.query(GenerationJob).filter(GenerationJob.goal_id == created["task_id"]).count() == 0


def test_start_run_freezes_quality_layers_into_policy_and_plan(client, monkeypatch):
    from app.config import settings
    from app.v2.contracts import Confirmation, StartRun, TaskInput
    from app.v2.tasks import create_task, mutate_input, start_run
    from app.v2.runtime import job, load
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(TaskInput(title="t", raw_input="r",
                                    execution_policy={"quality_layers": {"completeness": False}}), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"],
                                                mode="configured"), str(uuid4()))
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    frozen = {"method_version": "quality-layers.v1", "correctness": True,
              "completeness": False, "quality_specific": False}
    assert state["execution_policy"]["quality_layers"] == frozen
    assert state["check_plan"]["quality_layers"] == frozen
    assert all("quality_layer" in i and "hard_baseline" in i for i in state["check_plan"]["check_items"])
    # recheck/replan 复用同一 frozen 策略：同层重新编译得到同一 check_plan_hash。
    from app.v2.quality import compile_plan, frozen_quality_layers
    recompiled = compile_plan(state["context"], state["plan"].get("network_policy"),
                              quality_layers=frozen_quality_layers(state))
    assert recompiled["check_plan_hash"] == state["check_plan"]["check_plan_hash"]


def test_legacy_start_run_keeps_current_behavior(client, monkeypatch):
    from app.config import settings
    from app.v2.contracts import Confirmation, StartRun, TaskInput
    from app.v2.tasks import create_task, mutate_input, start_run
    from app.db import SessionLocal
    from app.v2.runtime import job, load
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(TaskInput(title="t", raw_input="r"), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"],
                                                mode="configured"), str(uuid4()))
    with SessionLocal() as db:
        state = load(job(db, run["run_id"]).state_json)
    assert state["execution_policy"]["quality_layers"] is None
    assert "quality_layers" not in state["check_plan"]
    from app.v2.checker_batch import semantic_check_items
    assert any(i["item_id"] == "C3.correctness" for i in semantic_check_items(state))

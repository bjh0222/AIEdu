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


def satisfied_checks(plan, overrides=None):
    overrides = overrides or {}
    return [{"item_id": item["item_id"],
             "result": overrides.get(item["item_id"], "satisfied"),
             "method_version": item.get("verifier_binding")}
            for item in plan["check_items"]]


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
              {"item_id": "G3.assessment_design", "result": "violated"},
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


def test_unclassified_quality_rows_default_to_quality_specific(monkeypatch):
    from app.v2.criterion_review import requests_for_unit
    from app.v2.quality import layer_enabled, quality_layer_for

    assert quality_layer_for({"item_id": "NEW.unclassified"}) == "quality_specific"
    layers = {"correctness": True, "completeness": True, "quality_specific": False}
    assert layer_enabled({"item_id": "NEW.unclassified"}, layers) is False

    plan = compile(layers={"completeness": True, "quality_specific": False})
    state = math_state(plan)
    state["plan"] = {"units": [{"unit_id": "u", "teaching_arrangement": {}}]}
    ref = {
        "profile_id": "p", "rule_id": "G3.custom_quality",
        "criterion_hash": "c" * 64, "criterion": "custom quality",
        "pass_condition": "observed",
    }
    monkeypatch.setattr(
        "app.v2.planning.review_requirements",
        lambda *_: {"global_targets": {"quality_target_contracts": [ref]},
                    "unit_targets": {}, "blueprint": None},
    )
    unit = {"unit_id": "u", "body": "content", "artifact_role": "tutorial", "items": []}
    disabled, _ = requests_for_unit(state, unit)
    assert not any(row["check_id"].startswith("A.quality") for row in disabled)

    enabled_plan = compile(layers={"completeness": True, "quality_specific": True})
    enabled, _ = requests_for_unit({**state, "check_plan": enabled_plan}, unit)
    assert any(row["check_id"].startswith("A.quality") for row in enabled)


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


def test_bound_profile_review_follows_quality_specific(monkeypatch):
    from app.v2 import checker

    layers = {"correctness": True, "completeness": True, "quality_specific": False}
    plan = compile(layers=layers)
    state = math_state(plan, criterion_assessment=False)
    state["context"]["import_understanding"] = [{"binding_hash": "b"}]
    monkeypatch.setattr(checker, "_evaluate_core", lambda *a, **k: {"checks": [], "findings": []})
    calls = {"bound": 0}
    monkeypatch.setattr(
        "app.v2.import_profiles.review_bound_profiles",
        lambda *a, **k: calls.__setitem__("bound", calls["bound"] + 1) or {"dimensions": []},
    )
    checker.evaluate("r", "w", 0, state, [], "v")
    assert calls["bound"] == 0
    state["check_plan"] = compile(layers={**layers, "quality_specific": True})
    checker.evaluate("r", "w", 0, state, [], "v")
    assert calls["bound"] == 1


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
    assert [f["finding_id"] for f in enabled_confirmed_findings(
        plan, [{"finding_id": "f4", "item_id": "unknown", "status": "confirmed"}]
    )] == []


def test_optimization_trigger_requires_enabled_actionable_findings():
    from app.v2.optimization import optimization_trigger

    plan = compile(layers={"completeness": False, "quality_specific": False})
    checks = satisfied_checks(plan)
    quality = {"checks": checks, "findings": []}
    assert optimization_trigger(plan, quality)["status"] == "final"

    violated = deepcopy(quality)
    violated["checks"] = [{"item_id": row["item_id"],
                           "result": "violated" if row["item_id"] == "C2.structure" else row["result"]}
                          for row in checks]
    assert optimization_trigger(plan, violated) == {
        "status": "unactionable", "item_ids": ["C2.structure"],
    }

    actionable = deepcopy(violated)
    actionable["findings"] = [{
        "finding_id": "f-structure", "status": "confirmed", "item_id": "C2.structure",
        "quality_layer": "correctness", "hard_baseline": True,
        "location": {"unit_id": "u", "unit_hash": "u" * 64, "span_hash": "s" * 64,
                     "start_byte": 0, "end_byte": 1},
    }]
    trigger = optimization_trigger(plan, actionable)
    assert trigger["status"] == "repair"
    assert [row["finding_id"] for row in trigger["findings"]] == ["f-structure"]


def test_optimization_trigger_blocks_incomplete_enabled_checks():
    from app.v2.optimization import optimization_trigger

    plan = compile(layers={"completeness": True, "quality_specific": False})
    checks = satisfied_checks(plan, {"C2.structure": "not_run"})
    assert optimization_trigger(plan, {"checks": checks, "findings": []}) == {
        "status": "incomplete", "item_ids": ["C2.structure"],
    }


def test_actionable_finding_repairs_before_incomplete_unrelated_checks():
    from app.v2.optimization import optimization_trigger

    plan = compile(layers={"completeness": False, "quality_specific": False})
    checks = satisfied_checks(plan, {"C1.coverage": "undecidable", "C2.structure": "violated"})
    quality = {
        "checks": checks,
        "findings": [{
            "finding_id": "structure-finding", "status": "confirmed", "item_id": "C2.structure",
            "quality_layer": "correctness", "hard_baseline": True,
            "location": {"unit_id": "u", "unit_hash": "u" * 64, "span_hash": "s" * 64,
                         "start_byte": 0, "end_byte": 1},
        }],
    }
    trigger = optimization_trigger(plan, quality)
    assert trigger["status"] == "repair"
    assert [row["finding_id"] for row in trigger["findings"]] == ["structure-finding"]


def test_derived_violation_is_covered_by_enabled_source_finding():
    from app.v2.optimization import optimization_trigger

    plan = compile(layers={"completeness": False, "quality_specific": False})
    q1 = next(item["item_id"] for item in plan["check_items"] if item["item_id"].startswith("Q1."))
    checks = satisfied_checks(plan, {q1: "violated", "C3.correctness": "violated"})
    quality = {
        "checks": checks,
        "findings": [{
            "finding_id": "q1-finding", "status": "confirmed", "item_id": q1,
            "quality_layer": "correctness", "hard_baseline": True,
            "location": {"unit_id": "u", "unit_hash": "u" * 64, "span_hash": "s" * 64,
                         "start_byte": 0, "end_byte": 1},
        }],
    }
    trigger = optimization_trigger(plan, quality)
    assert trigger["status"] == "repair"
    assert [row["finding_id"] for row in trigger["findings"]] == ["q1-finding"]


def test_A_design_and_profile_aggregates_are_plan_bound(monkeypatch):
    from app.v2 import assessment_binding
    from app.v2.repair_directives import criterion_findings

    task = deepcopy(MATH_TASK) | {"import_understanding": [{"binding_hash": "b"}]}
    plan = compile(task=task, layers={"completeness": True, "quality_specific": True})
    ids = {item["item_id"]: item for item in plan["check_items"]}
    assert ids["A.completeness"]["quality_layer"] == "completeness"
    assert ids["A.quality_targets"]["quality_layer"] == "quality_specific"
    assert ids["P.import_profile"]["quality_layer"] == "quality_specific"

    state = {"check_plan": plan, "context": task}
    unit = {"unit_id": "u", "artifact_role": "paper", "body": "content",
            "content_hash": "u" * 64, "items": []}
    monkeypatch.setattr(assessment_binding, "consume", lambda *a, **k: ("satisfied", []))
    report = {"criteria": [{
        "check_id": "A.knowledge.abc", "unit_id": "u", "result": "satisfied",
        "evidence": [{"unit_id": "u", "quote": "content"}],
        "processing_status": "complete",
    }]}
    aggregate = assessment_binding.criterion_check_results(report, state, [unit], "v")
    assert aggregate["A.completeness"]["result"] == "satisfied"

    violated = deepcopy(report)
    violated["criteria"][0]["result"] = "violated"
    findings = criterion_findings(violated, state, [unit])
    assert findings and findings[0]["item_id"] == "A.completeness"


def test_clause_only_criterion_violation_creates_actionable_finding():
    from app.v2.repair_directives import criterion_findings

    plan = compile(layers={"completeness": True, "quality_specific": False})
    state = math_state(plan)
    unit = {"unit_id": "u", "artifact_role": "paper", "body": "题干与答案",
            "content_hash": "u" * 64}
    report = {"version_id": "v", "criteria": [{
        "check_id": "C3.correctness", "unit_id": "u", "result": "violated",
        "processing_status": "complete",
        "clauses": [{
            "clause_id": "answer", "result": "violated",
            "evidence": [{
                "field": "item:i1:answer.final", "quote": "答案",
                "start_byte": 0, "end_byte": 6, "span_hash": "s" * 64,
            }],
        }],
    }]}
    findings = criterion_findings(report, state, [unit])
    assert len(findings) == 1
    assert findings[0]["item_id"] == "C3.correctness"
    assert findings[0]["location"]["start_byte"] >= 0
    assert findings[0]["location"]["unit_hash"] == unit["content_hash"]


def test_Q5_observations_become_check_and_finding(monkeypatch):
    from app.v2 import assessment_binding
    from app.v2.repair_directives import difficulty_findings

    plan = compile(layers={"completeness": True, "quality_specific": True})
    q5 = next(item["item_id"] for item in plan["check_items"] if item["item_id"].startswith("Q5."))
    unit = {"unit_id": "u", "artifact_role": "paper", "body": "题干与答案",
            "content_hash": "u" * 64,
            "items": [{"item_id": "i1", "ordinal": 1, "stem_markdown": "题干",
                       "answer": {"final": "答案", "reasoning": "推理"}}]}
    state = math_state(plan)
    monkeypatch.setattr(assessment_binding, "consume", lambda *a, **k: ("violated", []))
    report = {
        "version_id": "v",
        "items": [{
            "unit_id": "u", "item_id": "i1", "result": "violated",
            "processing_status": "complete", "item_fields_hash": "f" * 64,
            "dimensions": {"nonroutine_operations": [{
                "quote": "题干", "unit_id": "u", "start_byte": 0, "end_byte": 6,
            }]},
        }],
    }
    checks = assessment_binding.difficulty_check_results(report, state, [unit], "v")
    assert checks[q5]["result"] == "violated"
    findings = difficulty_findings(report, state, [unit])
    assert findings and findings[0]["item_id"] == q5


def test_correctness_only_acceptance_does_not_add_completeness_criteria():
    from app.v2.optimization_acceptance import prepared_inputs

    plan = compile(layers={"completeness": False, "quality_specific": False})
    state = {
        "context": {
            "raw_input": "r", "hard_constraints": [], "spec_bindings": [],
            "generation_scope": "focused", "material_ids": [],
        },
        "check_plan": plan,
        "plan": {"units": []},
    }
    package = {
        "acceptance_criteria": [
            {"item_id": "C1.coverage", "pass_condition": "coverage",
             "verification_method": "coverage.v1"},
            {"item_id": "task.original_requirements", "pass_condition": "complete request",
             "verification_method": "model_observation"},
        ],
        "frozen_bindings": {},
    }
    unit = {"unit_id": "u", "title": "u", "artifact_role": "paper", "body": "content",
            "content_hash": "u" * 64, "items": []}
    criteria, _ = prepared_inputs(state, package, [unit])
    ids = {row["item_id"] for row in criteria}
    assert "C1.coverage" in ids
    assert "task.original_requirements" not in ids


def test_reducer_ignores_disabled_layer_findings_but_blocks_unbound_findings():
    from app.v2.quality import reduce_quality

    plan = compile(layers={"completeness": True, "quality_specific": False})
    checks = satisfied_checks(plan)
    disabled_finding = {
        "finding_id": "disabled", "status": "confirmed", "item_id": "G3.assessment_design",
        "severity_class": "blocker",
    }
    reduced = reduce_quality(plan, checks, [disabled_finding])
    assert reduced["tqi_verdict"] == "not_calibrated"
    assert reduced["blocking_reasons"] == []

    unbound = {"finding_id": "unbound", "status": "confirmed", "item_id": "missing.item",
               "severity_class": "blocker"}
    blocked = reduce_quality(plan, checks, [unbound])
    assert blocked["tqi_verdict"] == "blocked"
    assert {"finding_id": "unbound", "result": "unbound"} in blocked["blocking_reasons"]


def test_scope_recheck_keeps_disabled_findings_audit_only():
    from app.v2.recheck_scope import merge_reports

    plan = compile(layers={"completeness": True, "quality_specific": False})
    checks = satisfied_checks(plan)
    disabled_finding = {
        "finding_id": "disabled", "status": "confirmed",
        "item_id": "G3.assessment_design", "severity_class": "blocker",
        "location": {"unit_id": "u"},
    }
    base = {"checks": checks, "findings": [disabled_finding],
            "quality_status": "needs_review", "tqi_verdict": "blocked"}
    deterministic = {"check_items": plan["check_items"], "quality_layers": plan["quality_layers"],
                     "checks": checks}
    incremental = {"checks": checks, "findings": [], "calibration_status": "not_calibrated"}
    merged = merge_reports(base, deterministic, incremental, {"u"})
    assert merged["tqi_verdict"] == "not_calibrated"
    assert merged["blocking_reasons"] == []


def test_restart_quality_reuses_fixed_version_and_resets_downstream_state(client):
    from test_task_run_v2 import task_run
    from app.v2.contracts import Resume
    from app.v2.runtime import dump, job, load, write
    from app.v2.tasks import resume
    from app.db import SessionLocal
    from app.models import GenerationJob, Tutorial, TutorialVersion

    _, _, snap = task_run(client)
    with write() as db:
        old = job(db, snap["run_id"])
        state = load(old.state_json)
        artifact = db.get(Tutorial, state["artifact_id"])
        version_id = str(uuid4())
        db.add(TutorialVersion(
            id=version_id, tutorial_id=artifact.id,
            content_json=dump({"units": snap.get("units", [])}),
            quality_report=dump({"checks": [], "findings": []}),
            content_hash=snap.get("content_hash") or "0" * 64,
            quality_hash="q" * 64, run_id=snap["run_id"],
        ))
        artifact.current_version_id = version_id
        state.update(version_id=version_id,
                     stop_reason="user_pause", optimization_rounds=2, optimization_attempts=3,
                     optimization_stop_reason="legacy", optimization_request={"x": 1})
        old.state_json, old.status, old.state_revision = dump(state), "paused", old.state_revision + 1
        revision = old.state_revision
    result = resume(
        snap["run_id"],
        Resume(expected_revision=revision, checkpoint_hash=snap["checkpoint_hash"], restart_quality=True),
        str(uuid4()),
    )
    with SessionLocal() as db:
        child = db.query(GenerationJob).filter_by(run_id=result["run_id"]).one()
        new_state = load(child.state_json)
    assert new_state["version_id"] == version_id
    assert new_state["quality_restart_from_version"] == version_id
    assert new_state["quality_restart_force_fresh"] is True
    assert new_state["optimization_rounds"] == 0
    assert new_state["optimization_attempts"] == 0
    assert "optimization_request" not in new_state
    assert new_state["optimization_policy"]["max_attempts_per_finding"] == 2
    assert new_state["optimization_policy"]["max_repair_rounds_per_run"] == 2


@pytest.mark.parametrize("enabled_layers", [
    {"completeness": False, "quality_specific": False},
    {"completeness": True, "quality_specific": False},
    {"completeness": True, "quality_specific": True},
])
def test_enabled_layer_pass_matrix_allows_final(enabled_layers):
    from app.v2.delivery import delivery_decision
    from app.v2.quality import layer_enabled, reduce_quality

    plan = compile(layers=enabled_layers)
    checks = [
        {"item_id": item["item_id"], "result": "satisfied",
         "method_version": item.get("verifier_binding")}
        for item in plan["check_items"] if layer_enabled(item, plan["quality_layers"])
    ]
    quality = reduce_quality(plan, checks, [])
    by_id = {row["item_id"]: row for row in quality["checks"]}
    for item in plan["check_items"]:
        if not layer_enabled(item, plan["quality_layers"]):
            assert by_id[item["item_id"]]["result"] == "not_applicable"
    assert quality["blocking_reasons"] == []
    decision = delivery_decision(
        quality, plan, profile_binding_current=True,
        requested_scope={"domains": ["math_logic"], "artifact_roles": ["paper"],
                         "scenes": ["exam_paper"]},
    )
    assert decision["state"] == "eligible"
    assert decision["final_allowed"] is True


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
                                    execution_policy={"quality_layers": {"completeness": False},
                                                      "quality_loop": False}), str(uuid4()))
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
    assert state["optimization_policy"]["max_attempts_per_finding"] == 2
    assert state["optimization_policy"]["max_repair_rounds_per_run"] == 2
    assert state["execution_policy"]["quality_loop"] is True
    assert all("quality_layer" in i and "hard_baseline" in i for i in state["check_plan"]["check_items"])
    # recheck/replan 复用同一 frozen 策略：同层重新编译得到同一 check_plan_hash。
    from app.v2.quality import compile_plan, frozen_quality_layers
    recompiled = compile_plan(state["context"], state["plan"].get("network_policy"),
                                  quality_layers=frozen_quality_layers(state),
                                  quality_profile=state["quality_profile"])
    assert recompiled["check_plan_hash"] == state["check_plan"]["check_plan_hash"]


def test_legacy_start_run_without_quality_layers_keeps_full_profile(client, monkeypatch):
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
    assert state["quality_profile"]["id"] == "full_v1"

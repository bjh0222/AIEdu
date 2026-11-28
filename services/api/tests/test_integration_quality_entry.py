"""New-task policy wiring; synthetic, no network, no real model configuration."""
from copy import deepcopy
from uuid import uuid4

import pytest

from test_task_run_v2 import task_run


POLICY = {"policy_version": "aiedu.repair.v2", "enabled": True,
          "max_attempts_per_finding": 6, "max_repair_rounds_per_run": 8, "root_call_budget": None}


def queued(client, monkeypatch, *, mode="configured", policy=True, adaptive=True):
    from app.config import settings
    from app.v2.contracts import Confirmation, StartRun, TaskInput
    from app.v2.tasks import create_task, mutate_input, start_run
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    execution = {"quality_loop": True, "criterion_assessment": True, "task_adaptive_format": adaptive,
                 "optimization_policy": deepcopy(POLICY)} if policy else {"quality_loop": False,
                     "task_adaptive_format": False, "criterion_assessment": False}
    created = create_task(TaskInput(title="frozen lesson", raw_input="literal request", generation_scope="system",
                                   execution_policy=execution), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    return start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"],
                                                mode=mode), str(uuid4()))


def state_of(rid):
    from app.db import SessionLocal
    from app.v2.runtime import job, load
    with SessionLocal() as db:
        obj = job(db, rid)
        return load(obj.state_json), obj.status


def test_opt_in_freezes_unlimited_shared_budget_and_A_for_uploaded_entry(client, monkeypatch):
    run = queued(client, monkeypatch)
    state, status = state_of(run["run_id"])
    assert status == "queued" and state["intent_pending"]
    assert state["optimization_policy"] == POLICY
    assert state["budget"]["calls"] is None
    assert state["context"]["generation_scope"] == "system"
    assert state["execution_policy"]["criterion_assessment"] is True
    assert state["execution_policy"]["task_adaptive_format"] is True


@pytest.mark.parametrize("mode,policy", [("demo", True), ("configured", False)])
def test_demo_and_legacy_do_not_opt_in(client, monkeypatch, mode, policy):
    run = queued(client, monkeypatch, mode=mode, policy=policy)
    state, _ = state_of(run["run_id"])
    assert not state.get("optimization_policy")
    assert not state["intent_pending"]


def test_explicit_legacy_policy_opt_out_remains_false(client, monkeypatch):
    """S10: the frontend no longer exposes Criterion Review; the internal
    execution-policy field stays an explicit opt-out for isolated pipelines."""
    run = queued(client, monkeypatch, mode="configured", policy=False)
    state, _ = state_of(run["run_id"])
    assert state["execution_policy"]["criterion_assessment"] is False
    assert state["execution_policy"]["task_adaptive_format"] is False


def test_new_configured_API_defaults_cannot_skip_A_or_full_assessment(client, monkeypatch):
    from app.config import settings
    from app.v2.contracts import Confirmation, StartRun, TaskInput
    from app.v2.tasks import create_task, mutate_input, start_run
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    created = create_task(TaskInput(title="synthetic", raw_input="Explain a scientific mechanism"), str(uuid4()))
    mutate_input(created["task_id"], Confirmation(expected_revision=1, context_hash=created["context_hash"]), str(uuid4()), True)
    run = start_run(created["task_id"], StartRun(expected_revision=1, confirmed_context_hash=created["context_hash"], mode="configured"), str(uuid4()))
    state, _ = state_of(run["run_id"])
    assert state["intent_pending"] and state["plan"]["units"] == []
    assert state["execution_policy"]["task_adaptive_format"] and state["execution_policy"]["criterion_assessment"]
    assert state["quality_profile"]["id"] == "full_v1"
    assert state["execution_policy"]["quality_loop"] and state["budget"]["calls"] is None


def test_new_entry_ignores_legacy_call_count_env(client, monkeypatch):
    monkeypatch.setenv("AIEDU_V2_MAX_CALLS", "10")
    state, _ = state_of(queued(client, monkeypatch)["run_id"])
    assert state["budget"]["calls"] is None


def teaching(title, refs):
    return {"goals": ["preserve goal"], "learner_prerequisites": [], "scope": "system",
            "quality_targets": ["bound criterion"], "quality_target_refs": deepcopy(refs),
            "unresolved_conflicts": [], "generation_prompt": "complete plan for B",
            "units": [{"title": title, "concepts": ["concept"], "question_types": [], "difficulty_basis": {},
                       "quantity": None, "material_mapping": [], "acceptance_criteria": ["bound criterion"]}]}


@pytest.mark.parametrize("aligns", [True, False])
def test_A_reconciles_frozen_layout_once_or_fails_no_silent_partial(client, monkeypatch, aligns):
    from app.v2 import generation_tools, inference, worker
    from app.v2.contracts import DomainError
    from app.v2.runtime import claim_root
    run = queued(client, monkeypatch, adaptive=False)
    rid = run["run_id"]
    lease = claim_root(rid, "fixture")
    initial, _ = state_of(rid)
    refs = [{
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "profile_hash": profile["profile_hash"],
        "rule_id": rule["rule_id"],
        "rule_version": rule["version"],
        "criterion_hash": rule["criterion_hash"],
    } for profile in initial["context"].get("quality_profiles", [])
      for rule in profile.get("rules", [])]
    stages = []
    def invoke(*args, **kw):
        stages.append(kw["stage"])
        if kw["stage"] == "understanding":
            assert kw["data"]["existing_layout"]
            return {"teaching_arrangement": teaching("wrong title", refs), "conflicts": []}, {"call_id": "A"}
        assert kw["stage"] == "arrangement_alignment"
        assert any(entry.get("source_id") == "teaching-arrangement" and entry.get("consumer") == "A"
                   and entry.get("sha256") == kw["data"]["planning_skill"]["hash"] for entry in kw["entries"])
        title = kw["data"]["frozen_layout"][0]["title"] if aligns else "still wrong"
        return {"teaching_arrangement": teaching(title, refs)}, {"call_id": "alignment"}
    monkeypatch.setattr(inference, "invoke", invoke)
    monkeypatch.setattr(generation_tools, "invoke", invoke)
    if not aligns:
        with pytest.raises(DomainError, match="TEACHING_ARRANGEMENT_LAYOUT_UNBOUND"):
            worker.resolve_run_intent(rid, "fixture", lease["epoch"])
        assert state_of(rid)[0]["intent_pending"]
    else:
        worker.resolve_run_intent(rid, "fixture", lease["epoch"])
        state, _ = state_of(rid)
        assert state["plan"]["arrangement_mapping"]["status"] == "bound"
        assert state["plan"]["units"][0]["teaching_arrangement"]["concepts"] == ["concept"]
        assert state["intent_call"]["alignment"]["call_id"] == "alignment"
    assert stages == ["understanding", "arrangement_alignment"]


@pytest.mark.parametrize("review_only", [False, True])
def test_normal_worker_uses_C_B_on_same_root_but_fixed_recheck_never_regenerates(client, monkeypatch, review_only):
    from app.v2 import checker, optimization, repairs, worker
    from app.v2.runtime import dump, job, load, write
    from app.v2.contracts import DomainError
    _, _, snap = task_run(client)
    rid = snap["run_id"]
    with write() as db:
        obj = job(db, rid)
        state = load(obj.state_json)
        state.update(mode="configured", optimization_policy=deepcopy(POLICY))
        if review_only:
            state["review_only_source_version"] = snap["version_id"]
        obj.status, obj.state_json = "queued", dump(state)
    monkeypatch.setattr(checker, "evaluate", lambda *a, **kw: {"checks": [], "findings": []})
    monkeypatch.setattr(repairs, "auto_repair", lambda *a, **kw: None)
    seen = []
    def optimize(run_id, *a, **kw):
        seen.append(run_id)
        raise DomainError("UNKNOWN_CALL")
    monkeypatch.setattr(optimization, "execute_optimization", optimize)
    worker.execute_run(rid)
    current, status = state_of(rid)
    if review_only:
        assert seen == [] and status == "completed"
    else:
        assert seen == [rid] and status == "interrupted"
        assert current["stop_reason"] == "UNKNOWN_CALL"


def test_dimension_comparison_is_identity_bound_and_unknown_is_not_zero():
    from app.v2.optimization import diagnostic_regressions
    def report(result, score=None):
        return {"diagnostic_review": {"dimensions": [{"binding_hash": "b", "unit_id": "u", "dimension_id": "d",
                                                       "result": result, "score": score}]}}
    assert diagnostic_regressions(report("satisfied", 2), report("undecidable"))
    assert diagnostic_regressions(report("satisfied", 2), {})
    assert diagnostic_regressions(report("not_applicable"), report("violated", 1))
    assert diagnostic_regressions(report("violated", 1), report("violated", 0))
    assert not diagnostic_regressions(report("undecidable"), report("satisfied", 2))
    assert not diagnostic_regressions(report("violated", 1), report("violated", 1))

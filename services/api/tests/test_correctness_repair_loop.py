import time
from types import SimpleNamespace
from uuid import uuid4

from app.v2.contracts import Confirmation, StartRun, TaskInput, digest, sha


def configured_item_run(client, monkeypatch):
    from app.config import settings
    from app.v2.quality import compile_plan, normalize_quality_layers, reduce_quality
    from app.v2.quality_suite import CORRECTNESS_ONLY_V1, normalize_quality_profile
    from app.v2.runtime import job, load, publish, write
    from app.v2.tasks import create_task, mutate_input, start_run

    monkeypatch.setattr(settings, "v2_allow_paid", True)
    policy = {
        "policy_version": "aiedu.repair.v2",
        "enabled": True,
        "max_attempts_per_finding": 2,
        "max_repair_rounds_per_run": 2,
        "root_call_budget": None,
    }
    created = create_task(
        TaskInput(
            title="correctness repair",
            raw_input="generate one exercise",
            execution_policy={
                "quality_loop": True,
                "quality_layers": {
                    "correctness": True,
                    "completeness": False,
                    "quality_specific": False,
                },
                "quality_profile": CORRECTNESS_ONLY_V1,
                "optimization_policy": policy,
            },
        ),
        str(uuid4()),
    )
    mutate_input(
        created["task_id"],
        Confirmation(expected_revision=1, context_hash=created["context_hash"]),
        str(uuid4()),
        True,
    )
    run = start_run(
        created["task_id"],
        StartRun(
            expected_revision=1,
            confirmed_context_hash=created["context_hash"],
            mode="configured",
        ),
        str(uuid4()),
    )
    item = {
        "item_id": "i1",
        "ordinal": 1,
        "stem_markdown": "2+2=?",
        "answer": {"final": "5", "reasoning": "2+2=5"},
    }
    body = "2+2=?\n\n5\n\n2+2=5"
    unit = {
        "unit_id": "unit-1",
        "title": "exercises",
        "artifact_role": "tutorial",
        "body": body,
        "content_hash": sha(body),
        "display_order": 0,
        "depends_on": [],
        "items": [item],
    }
    with write() as db:
        obj = job(db, run["run_id"])
        state = load(obj.state_json)
        plan = {
            "schema_version": "aiedu.plan.v1",
            "units": [{
                "unit_id": "unit-1",
                "title": "exercises",
                "artifact_role": "tutorial",
                "required": True,
                "depends_on": [],
                "material_chunk_ids": [],
            }],
            "network_policy": {"enabled": False},
            "planning_skill": {"skill_id": "teaching-arrangement", "version": 1, "hash": "a" * 64},
        }
        plan["plan_hash"] = digest(plan)
        state["plan"] = plan
        state["intent_pending"] = False
        state["quality_profile"] = normalize_quality_profile(CORRECTNESS_ONLY_V1)
        state["execution_policy"]["quality_profile"] = state["quality_profile"]
        state["check_plan"] = compile_plan(
            state["context"],
            plan["network_policy"],
            quality_layers=normalize_quality_layers({
                "correctness": True,
                "completeness": False,
                "quality_specific": False,
            }),
            quality_profile=state["quality_profile"],
        )
        obj.status = "running"
        obj.cancel_epoch = 1
        obj.worker_instance_id = "fixture"
        obj.lease_until = time.time() + 60
        obj.state_json = __import__("app.v2.runtime", fromlist=["dump"]).dump(state)
    version_id = str(uuid4())
    with write() as db:
        state = load(job(db, run["run_id"]).state_json)
    finding = {
        "finding_id": "f1",
        "item_id": "Q.item_correctness",
        "check_plan_hash": state["check_plan"]["check_plan_hash"],
        "system": "correctness_only",
        "severity_class": "blocker",
        "quality_layer": "correctness",
        "hard_baseline": True,
        "location": {
            "unit_id": "unit-1",
            "version_id": version_id,
            "unit_hash": unit["content_hash"],
            "start_byte": body.encode().find(b"5"),
            "end_byte": body.encode().find(b"5") + 1,
            "span_hash": sha(b"5"),
        },
        "claim": "final answer is incorrect",
        "evidence": {"item_id": "i1", "field": "answer.final"},
        "verification_method": "correctness-only.v1",
        "verification_result": "violated",
        "method_version": "correctness-only.v1",
        "tool_raw_output_hash": digest({"item_id": "i1"}),
        "confidence": "model_observation",
        "suggested_fix": "replace the final answer with 4",
        "model_family": None,
        "input_hash": unit["content_hash"],
        "normalized_claim_hash": "claim-hash",
        "attempt": 1,
        "status": "confirmed",
        "reviewed_by": "correctness_only.v1",
        "review_evidence": [],
        "context_conflict": False,
        "category": "item_correctness",
        "acceptance_assertion": {
            "method": "correctness-only.v1",
            "item_id": "i1",
            "field": "answer.final",
        },
    }
    quality = reduce_quality(
        state["check_plan"],
        [{"item_id": "Q.item_correctness", "result": "violated",
          "method_version": "correctness-only.v1"}],
        [finding],
        calibration="not_calibrated",
    )
    quality["_version_id"] = version_id
    publish(
        run["run_id"], "fixture", 1, [unit], quality,
        f"{run['run_id']}:base", base_version=None,
    )
    return run["run_id"], version_id, unit


def test_correctness_repair_runs_one_b_call_target_recheck_then_final(client, monkeypatch):
    from app.v2 import generation_tools, model_roles
    from app.v2.optimization import CANDIDATE_SCHEMA_VERSION, execute_optimization
    from app.v2.quality_suite import evaluate as quality_evaluate
    from app.v2.runtime import job, load

    run_id, base_version_id, base_unit = configured_item_run(client, monkeypatch)
    b_calls = []
    c_calls = []
    monkeypatch.setattr(
        model_roles,
        "for_role",
        lambda role, **kwargs: SimpleNamespace(
            llm_base_url="https://fixture.invalid",
            llm_model="fixture",
            llm_model_family="fixture",
            llm_thinking="enabled",
            llm_reasoning_effort="high",
            llm_max_tokens=0,
            llm_configured=True,
        ),
    )

    def fake_generation(*args, **kwargs):
        package = kwargs["data"]["optimization_package"]
        directive_id = package["directives"][0]["directive_id"]
        assert "answer.final" not in package["directives"][0]["forbidden_changes"]
        assert "answer.final" not in package["scope"]["forbidden_changes"]
        candidate = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "base_version": base_version_id,
            "base_content_hash": kwargs["data"]["immutable_candidate_metadata"]["base_content_hash"],
            "package_hash": digest(package),
            "strategy": "whole_item",
            "directive_ids": [directive_id],
            "units": [{
                "unit_id": base_unit["unit_id"],
                "directive_ids": [directive_id],
                "items": [{
                    "stem_markdown": "2+2=?",
                    "answer": {"final": "4", "reasoning": "2+2=4"},
                }],
            }],
        }
        b_calls.append(candidate)
        return candidate, {"call_id": "b1", "request_hash": "br"}, {"receipts": [], "calls": []}

    def fake_invoke(*args, **kwargs):
        c_calls.append(kwargs)
        raise AssertionError("correctness-only repair must not call Criterion")

    def fake_evaluate(*args, **kwargs):
        state = args[3]
        return {
            "tqi_verdict": "pass",
            "quality_status": "passed",
            "calibration_status": "not_calibrated",
            "blocking_reasons": [],
            "check_plan_hash": state["check_plan"]["check_plan_hash"],
            "checks": [{
                "item_id": "Q.item_correctness",
                "result": "satisfied",
                "method_version": "correctness-only.v1",
            }],
            "findings": [],
            "item_correctness": {
                "quality_profile": state["quality_profile"],
                "unit_results": [{
                    "unit_id": "unit-1",
                    "result": "satisfied",
                    "items": [{"item_id": "new-i1", "result": "correct"}],
                }],
            },
        }

    monkeypatch.setattr(generation_tools, "generation_with_sources", fake_generation)
    from app.v2 import inference
    monkeypatch.setattr(inference, "invoke", fake_invoke)
    monkeypatch.setattr("app.v2.quality_suite.evaluate", fake_evaluate)
    execute_optimization(run_id, "fixture", 1)
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, run_id).state_json)
    assert c_calls == []
    assert len(b_calls) == 1
    assert state["optimization_stop_reason"] == "correctness_only_completed"
    assert state["best_so_far_version"] != base_version_id
    assert state["optimization_history"][0]["full_recheck"] is False


def test_correctness_only_pass_skips_optimization_without_b_or_c_calls(client, monkeypatch):
    from app.v2.runtime import dump, job, load, write

    run_id, base_version_id, _unit = configured_item_run(client, monkeypatch)
    with write() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        state["version_id"] = base_version_id
        obj.status = "queued"
        obj.worker_instance_id = None
        obj.lease_until = 0
        obj.state_json = dump(state)

    def passing_quality(*args, **kwargs):
        checked_state = args[3]
        return {
            "tqi_verdict": "pass",
            "quality_status": "passed",
            "calibration_status": "not_calibrated",
            "blocking_reasons": [],
            "check_plan_hash": checked_state["check_plan"]["check_plan_hash"],
            "checks": [{
                "item_id": "Q.item_correctness",
                "result": "satisfied",
                "method_version": "correctness-only.v1",
            }],
            "findings": [],
            "item_correctness": {
                "quality_profile": checked_state["quality_profile"],
                "unit_results": [{
                    "unit_id": "unit-1",
                    "result": "satisfied",
                    "items": [{"item_id": "i1", "result": "correct"}],
                }],
            },
        }

    monkeypatch.setattr("app.v2.worker.run_research_before_planning", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.v2.quality_suite.evaluate", passing_quality)
    monkeypatch.setattr(
        "app.v2.optimization.execute_optimization",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("a passing correctness-only run must not enter optimization")
        ),
    )
    from app.v2.worker import execute_run
    execute_run(run_id)

    from app.db import SessionLocal
    with SessionLocal() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
    assert obj.status == "completed"
    assert state["optimization_stop_reason"] == "optimization_not_needed"
    assert state["stages"]["optimizing"] == "not_needed"
    assert state["stop_reason"] == "optimization_not_needed"


def test_correctness_repair_allows_one_second_b_round_then_final(client, monkeypatch):
    from app.v2 import generation_tools, model_roles
    from app.v2.optimization import CANDIDATE_SCHEMA_VERSION, execute_optimization
    from app.v2.runtime import job, load

    run_id, base_version_id, base_unit = configured_item_run(client, monkeypatch)
    b_calls = []
    b_base_versions = []
    best_before_second = []
    checks = []
    monkeypatch.setattr(
        model_roles,
        "for_role",
        lambda role, **kwargs: SimpleNamespace(
            llm_base_url="https://fixture.invalid",
            llm_model="fixture",
            llm_model_family="fixture",
            llm_thinking="enabled",
            llm_reasoning_effort="high",
            llm_max_tokens=0,
            llm_configured=True,
        ),
    )

    def fake_generation(*args, **kwargs):
        if len(b_calls) == 1:
            from app.db import SessionLocal
            from app.v2.runtime import job, load
            with SessionLocal() as db:
                best_before_second.append(
                    load(job(db, run_id).state_json).get("best_so_far_version")
                )
        package = kwargs["data"]["optimization_package"]
        metadata = kwargs["data"]["immutable_candidate_metadata"]
        b_base_versions.append(metadata["base_version"])
        directive_id = package["directives"][0]["directive_id"]
        final = "3" if len(b_calls) == 0 else "4"
        candidate = {
            "schema_version": CANDIDATE_SCHEMA_VERSION,
            "base_version": metadata["base_version"],
            "base_content_hash": metadata["base_content_hash"],
            "package_hash": digest(package),
            "strategy": "whole_item",
            "directive_ids": [directive_id],
            "units": [{
                "unit_id": base_unit["unit_id"],
                "directive_ids": [directive_id],
                "items": [{
                    "stem_markdown": "2+2=?",
                    "answer": {"final": final, "reasoning": f"2+2={final}"},
                }],
            }],
        }
        b_calls.append(candidate)
        return candidate, {"call_id": f"b{len(b_calls)}", "request_hash": f"br{len(b_calls)}"}, {
            "receipts": [],
            "calls": [],
        }

    def fake_evaluate(*args, **kwargs):
        state = args[3]
        version_id = args[5]
        units = args[4]
        checks.append(version_id)
        if len(checks) == 1:
            unit = units[0]
            item = unit["items"][0]
            finding = {
                "finding_id": "f2",
                "item_id": "Q.item_correctness",
                "check_plan_hash": state["check_plan"]["check_plan_hash"],
                "system": "correctness_only",
                "severity_class": "blocker",
                "quality_layer": "correctness",
                "hard_baseline": True,
                "location": {
                    "unit_id": unit["unit_id"],
                    "version_id": version_id,
                    "unit_hash": unit["content_hash"],
                    "start_byte": 0,
                    "end_byte": len(unit["body"].encode()),
                    "span_hash": sha(unit["body"]),
                },
                "claim": "answer remains incorrect",
                "evidence": {"item_id": item["item_id"]},
                "verification_method": "correctness-only.v1",
                "verification_result": "violated",
                "method_version": "correctness-only.v1",
                "tool_raw_output_hash": digest({"item_id": item["item_id"]}),
                "confidence": "model_observation",
                "suggested_fix": "replace the final answer with 4",
                "model_family": None,
                "input_hash": unit["content_hash"],
                "normalized_claim_hash": "claim-hash-2",
                "attempt": 1,
                "status": "confirmed",
                "reviewed_by": "correctness_only.v1",
                "review_evidence": [],
                "context_conflict": False,
                "category": "item_correctness",
                "acceptance_assertion": {
                    "method": "correctness-only.v1",
                    "item_id": item["item_id"],
                    "field": "answer.final",
                },
            }
            return {
                "tqi_verdict": "needs_repair",
                "quality_status": "failed",
                "calibration_status": "not_calibrated",
                "blocking_reasons": [{"item_id": "Q.item_correctness", "result": "violated"}],
                "check_plan_hash": state["check_plan"]["check_plan_hash"],
                "checks": [{
                    "item_id": "Q.item_correctness",
                    "result": "violated",
                    "method_version": "correctness-only.v1",
                }],
                "findings": [finding],
                "item_correctness": {
                    "quality_profile": state["quality_profile"],
                    "unit_results": [{
                        "unit_id": unit["unit_id"],
                        "result": "violated",
                        "items": [{"item_id": item["item_id"], "result": "incorrect"}],
                    }],
                },
            }
        unit = units[0]
        return {
            "tqi_verdict": "pass",
            "quality_status": "passed",
            "calibration_status": "not_calibrated",
            "blocking_reasons": [],
            "check_plan_hash": state["check_plan"]["check_plan_hash"],
            "checks": [{
                "item_id": "Q.item_correctness",
                "result": "satisfied",
                "method_version": "correctness-only.v1",
            }],
            "findings": [],
            "item_correctness": {
                "quality_profile": state["quality_profile"],
                "unit_results": [{
                    "unit_id": unit["unit_id"],
                    "result": "satisfied",
                    "items": [{"item_id": unit["items"][0]["item_id"], "result": "correct"}],
                }],
            },
        }

    monkeypatch.setattr(generation_tools, "generation_with_sources", fake_generation)
    from app.v2 import inference
    monkeypatch.setattr(inference, "invoke", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("correctness-only repair must not call Criterion")
    ))
    monkeypatch.setattr("app.v2.quality_suite.evaluate", fake_evaluate)
    execute_optimization(run_id, "fixture", 1)
    from app.db import SessionLocal
    with SessionLocal() as db:
        state = load(job(db, run_id).state_json)
        final_version = db.get(__import__("app.models", fromlist=["TutorialVersion"]).TutorialVersion,
                               state["version_id"])
        final_quality = load(final_version.quality_report)
    assert len(b_calls) == 2
    assert len(checks) == 1
    assert b_base_versions[0] == base_version_id
    assert b_base_versions[1] != base_version_id
    assert best_before_second == [base_version_id]
    assert state["optimization_stop_reason"] == "correctness_only_completed"
    assert state["optimization_rounds"] == 2
    assert [row["full_recheck"] for row in state["optimization_history"]] == [False, False]
    assert final_quality["correctness_repair_finalization"]["status"] == "final"
    assert final_quality["correctness_repair_finalization"]["rechecked_after_final_repair"] is False
    assert final_quality["checks"][0]["result"] == "not_run"
    assert final_quality["repair_policy"]["policy_version"] == "aiedu.repair.v2"
    assert final_quality["repair_policy"]["max_repair_rounds_per_run"] == 2
    from app.v2.delivery import delivery_decision
    assert delivery_decision(
        final_quality, state["check_plan"], profile_binding_current=True
    )["final_allowed"] is True

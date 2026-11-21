"""Isolated orchestration tests; no API wiring, real model or certification."""
from copy import deepcopy

import pytest

from app.v2.contracts import DomainError
from app.v2 import synthesis
from test_synthesis_contracts import case


def diagnostics(result="satisfied", score=2):
    return {"dimensions": [{"rubric_hash": "frozen", "dimension_id": "concept-structure",
        "result": result, "score": score, "processing_status": "complete"}], "audits": [{"call_id": "research"}]}


def c_output(data, result="satisfied", repair=False):
    bound = data["candidate"]
    blocks = [b["block_id"] for b in bound["proposal"]["blocks"]]
    return {"source_bundle_hash": bound["source_bundle_hash"], "proposal_hash": bound["proposal_hash"],
        "checks": [{"check_id": c["check_id"], "result": result if c["check_id"] == "synthesis.source_support" else "satisfied",
            "reason": "Specific source-bound observation", "block_ids": blocks,
            "source_span_ids": bound["proposal"]["blocks"][0]["source_span_ids"],
            "evidence_gap": "Missing source support" if result == "undecidable" else None,
            "applicability_reason": "Actual scope reason" if result == "not_applicable" else None}
            for c in data["target_check_plan"]["checks"]],
        "optimization_prompt": "完整C提示：仅修b1，保留原题及其它块；按原量规复验。" if repair else None,
        "repair_block_ids": ["b1"] if repair else [], "preserve_block_ids": [] if repair else blocks,
        "recheck_conditions": ["Recheck source support and every original target"] if repair else []}


def run(case, invoke, *, research=None, progress=None, max_repairs=1, saved=None):
    checkpoints = [] if saved is None else saved
    return synthesis.run_loop("r", "w", 1, case[0], case[2], max_repairs=max_repairs, progress=progress,
        checkpoint=checkpoints.append, invoke_call=invoke,
        research_check=research or (lambda *args: diagnostics()))


def test_B_reads_all_source_bodies_A_and_rubric_and_C_sees_actual_candidate(case):
    calls, saved = [], []
    def invoke(*args, **kw):
        calls.append(kw)
        data = kw["data"]
        assert data["source_bundle"] == case[2]
        assert len(data["source_bundle"]["source_units"]) == 2
        if kw["role"] == "generation":
            assert "C_repair_package" not in data
            return case[3], {"call_id": "B"}
        assert data["candidate"] == case[4]
        assert data["current_research_diagnostics"] == diagnostics()
        return c_output(data), {"call_id": "C"}
    result = run(case, invoke, saved=saved)
    assert [c["stage"] for c in calls] == ["synthesis_generating", "synthesis_reviewing"]
    assert result["automatic_status"] == "satisfied" and result["status"] == "candidate"
    assert result["calibration_status"] == "not_calibrated" and result["final_delivery_eligible"] is False
    assert result["source_quality_promotion"] is False and len(saved) == 4


def test_C_complete_prompt_and_diagnostics_reach_B_and_new_proposal_rechecked(case):
    calls, first_review = [], None
    def invoke(*args, **kw):
        nonlocal first_review
        calls.append(kw)
        if kw["stage"] == "synthesis_generating":
            return case[3], {"call_id": "B0"}
        if kw["stage"] == "synthesis_repairing":
            package = kw["data"]["C_repair_package"]
            assert {k: package[k] for k in first_review} == first_review
            assert package["research_diagnostics"] == diagnostics()
            proposal = deepcopy(case[3])
            proposal["blocks"][0]["body"] = "由B整改后的新候选正文。"
            return proposal, {"call_id": "B1"}
        repair = not any(c["stage"] == "synthesis_repairing" for c in calls)
        output = c_output(kw["data"], "violated" if repair else "satisfied", repair)
        if repair:
            first_review = deepcopy(output)
        return output, {"call_id": "C0" if repair else "C1"}
    result = run(case, invoke)
    assert len(result["rounds"]) == 2 and result["automatic_status"] == "satisfied"
    assert result["rounds"][0]["review"]["review"]["checks"][0]["result"] == "violated"
    assert result["rounds"][0]["proposal"]["proposal_hash"] != result["latest_proposal_hash"]


def test_same_proposal_is_no_progress_not_another_C_call(case):
    calls = []
    def invoke(*args, **kw):
        calls.append(kw)
        if kw["role"] == "generation":
            return case[3], {"call_id": "B"}
        return c_output(kw["data"], "violated", True), {"call_id": "C"}
    with pytest.raises(DomainError, match="SYNTHESIS_NO_PROGRESS"):
        run(case, invoke)
    assert len(calls) == 3


def test_missing_research_cannot_pass_via_all_positive_engineering_checks(case):
    def invoke(*args, **kw):
        if kw["role"] == "generation":
            return case[3], {"call_id": "B"}
        return c_output(kw["data"]), {"call_id": "C"}
    with pytest.raises(DomainError, match="SYNTHESIS_REPAIR_PROMPT_REQUIRED"):
        run(case, invoke, research=lambda *args: {"dimensions": [], "audits": []})


@pytest.mark.parametrize("result", ["not_applicable", "undecidable"])
def test_mandatory_NA_or_unknown_never_becomes_satisfied(case, result):
    def invoke(*args, **kw):
        if kw["role"] == "generation":
            return case[3], {"call_id": "B"}
        output = c_output(kw["data"], result, True)
        # not_applicable is not a content-repair decision in the pure binder.
        if result == "not_applicable":
            output.update(repair_block_ids=[], preserve_block_ids=["b1"])
        return output, {"call_id": "C"}
    output = run(case, invoke, max_repairs=0)
    assert output["automatic_status"] == "needs_review" and output["final_delivery_eligible"] is False


def test_unknown_stops_and_resume_reuses_B_checkpoint(case):
    saved, calls = [], []
    def invoke(*args, **kw):
        calls.append(kw["stage"])
        if kw["role"] == "generation":
            return case[3], {"call_id": "B"}
        raise DomainError("UNKNOWN_CALL", 502)
    with pytest.raises(DomainError, match="UNKNOWN_CALL"):
        run(case, invoke, saved=saved)
    assert calls == ["synthesis_generating", "synthesis_reviewing"]
    assert saved[-1]["rounds"][0]["generation_audit"]["call_id"] == "B"
    def recheck(*args, **kw):
        assert kw["stage"] == "synthesis_reviewing"
        return c_output(kw["data"]), {"call_id": "C-new-explicit-attempt"}
    result = run(case, recheck, progress=saved[-1])
    assert result["automatic_status"] == "satisfied"


def test_checkpoint_is_bound_to_source_and_check_plan(case):
    with pytest.raises(DomainError, match="SYNTHESIS_CHECKPOINT_STALE"):
        run(case, lambda *a, **k: pytest.fail("No call on stale source"), progress={"source_bundle_hash": "old"})


def test_preserved_blocks_cannot_be_changed_by_B(case):
    proposal = deepcopy(case[3])
    proposal["blocks"].append({**proposal["blocks"][0], "block_id": "keep", "body": "保留正文"})
    calls = []
    def invoke(*args, **kw):
        calls.append(kw["stage"])
        if kw["stage"] == "synthesis_generating":
            return proposal, {"call_id": "B0"}
        if kw["stage"] == "synthesis_repairing":
            changed = deepcopy(proposal)
            changed["blocks"][1]["body"] = "擅自改变保留块"
            return changed, {"call_id": "B1"}
        output = c_output(kw["data"], "violated", True)
        output["preserve_block_ids"] = ["keep"]
        return output, {"call_id": "C0"}
    with pytest.raises(DomainError, match="SYNTHESIS_PRESERVED_BLOCK_CHANGED"):
        run(case, invoke)
    assert calls == ["synthesis_generating", "synthesis_reviewing", "synthesis_repairing"]


def test_research_regression_is_not_hidden_by_successful_repair(case):
    research_calls = []
    def research(*args):
        research_calls.append(1)
        return diagnostics("satisfied", 2) if len(research_calls) == 1 else diagnostics("violated", 0)
    def invoke(*args, **kw):
        if kw["role"] == "generation":
            proposal = deepcopy(case[3])
            if kw["stage"] == "synthesis_repairing":
                proposal["blocks"][0]["body"] = "新的B候选正文"
            return proposal, {"call_id": "B"}
        first = len(research_calls) == 1
        return c_output(kw["data"], "violated" if first else "satisfied", True), {"call_id": "C"}
    with pytest.raises(DomainError, match="SYNTHESIS_RESEARCH_REGRESSION"):
        run(case, invoke, research=research)


def test_repair_limit_is_not_automatic_quality_pass(case):
    def invoke(*args, **kw):
        if kw["role"] == "generation":
            return case[3], {"call_id": "B"}
        return c_output(kw["data"], "violated", True), {"call_id": "C"}
    result = run(case, invoke, max_repairs=0)
    assert result["stop_reason"] == "repair_limit_reached"
    assert result["automatic_status"] == "needs_repair" and result["final_delivery_eligible"] is False


def test_external_research_failure_tightens_C_prompt_gate_without_changing_C_labels(case):
    from app.v2.synthesis_contracts import bind_review
    data = {"candidate": case[4], "target_check_plan": synthesis.check_plan(case[2])}
    review = c_output(data, repair=True)
    bound = bind_review(review, case[4], case[2], [c["check_id"] for c in data["target_check_plan"]["checks"]],
        external_unresolved=True)
    assert all(r["result"] == "satisfied" for r in bound["review"]["checks"])
    assert bound["automatic_status"] == "needs_review"
    assert synthesis.automatic_status(bound, diagnostics("violated", 0), data["target_check_plan"]) == "needs_repair"


def test_all_NA_or_incomplete_research_is_unresolved_not_evidence_of_pass():
    assert synthesis.unresolved_research({"dimensions": []})
    assert synthesis.unresolved_research(diagnostics("not_applicable", None))
    incomplete = diagnostics()
    incomplete["dimensions"][0]["processing_status"] = "invalid_or_missing_observation"
    assert synthesis.unresolved_research(incomplete)


def test_research_full_indicator_contract_is_executed_not_a_bare_enum(case, monkeypatch):
    bundle = deepcopy(case[2])
    # This isolated test supplies a source-bound rubric; no new academic scale.
    dimension = {"dimension_id": "d", "source_refs": ["claim"], "indicators": [{
        "indicator_id": "i", "measurement": "enum", "enum_values": ["observed", "missing"]}]}
    rubric = {"dimensions": [dimension], "source_claims": [{"claim_id": "claim"}]}
    bundle["research_rubrics"] = [{"content": rubric}]
    seen = []
    def invoke(*args, **kw):
        seen.append(kw)
        assert kw["role"] == "review" and kw["output_tokens"] == 32768
        assert kw["data"]["research_rubric"]["dimensions"] == [dimension]
        assert kw["data"]["source_bundle"] == bundle
        assert "indicators" in str(kw["output_schema"])
        unit = kw["data"]["unit"]
        quote = "由B生成的整合候选。"
        assert unit["body"].count(quote) == 1
        return {"dimensions": [{"dimension_id": "d", "result": "satisfied", "score": 2,
            "reason": "Concrete observed relation", "evidence": [{"field": "body", "quote": quote}],
            "indicators": [{"indicator_id": "i", "applicable": True, "numerator": None,
                "denominator": None, "unit": None, "enum_value": "observed",
                "evidence": [{"field": "body", "quote": quote}], "unknown_reason": None}],
            "repair_condition": None, "limitations": []}]}, {"call_id": "C-research"}
    result = synthesis.research_diagnostics("r", "w", 1, case[0], bundle, case[4], invoke)
    assert len(seen) == 1 and result["dimensions"][0]["indicators"][0]["enum_value"] == "observed"
    assert result["dimensions"][0]["processing_status"] == "complete"


def durable_fixture(client):
    from uuid import uuid4
    from app.db import SessionLocal
    from app.models import GenerationJob, TutorialVersion
    from app.v2.runtime import dump, job, load, materialize
    from app.v2.synthesis_contracts import source_bundle
    from test_v2_optimization import heading_task
    _, snap = heading_task(client)
    with SessionLocal() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        version = db.get(TutorialVersion, snap["version_id"])
        source_quality = version.quality_report
        state["context"]["research_rubrics"] = [{"content": {"dimensions": [{"dimension_id": "d",
            "source_refs": ["claim"], "indicators": []}], "source_claims": [{"claim_id": "claim"}]}}]
        bundle = source_bundle(state, version.id, version.content_hash, materialize(version)["units"])
        state["synthesis_source"] = {"task_id": snap["task_id"], "version_id": version.id,
            "content_hash": version.content_hash, "quality_hash": version.quality_hash, "source_demo": True,
            "source_bundle_hash": bundle["bundle_hash"], "expected_revision": 0}
        run_id, root_id = str(uuid4()), str(uuid4())
        db.add(GenerationJob(id=root_id, root_job_id=root_id, parent_job_id=source.id, run_id=run_id,
            goal_id=source.goal_id, job_type="task_v2", status="queued", state_json=dump(state)))
        db.commit()
    return snap, run_id, state, bundle, source_quality


def test_durable_executor_checkpoints_and_immutable_record_do_not_promote_source(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import TutorialVersion, Tutorial
    from app.v2 import inference
    from app.v2.insights import record
    from app.v2.runtime import claim_root, job, load
    snap, run_id, state, bundle, source_quality = durable_fixture(client)
    calls = []
    def invoke(*args, **kw):
        calls.append(kw["stage"])
        if kw["stage"] == "synthesis_generating":
            proposal = {"source_bundle_hash": bundle["bundle_hash"], "title": "源版本整合",
                "blocks": [{"block_id": "b1", "heading": "联系", "body": "Generated integration statement.",
                    "source_span_ids": [s["source_span_id"] for s in bundle["source_spans"]]}],
                "relations": [], "unintegrated_units": [], "limitations": []}
            return proposal, {"call_id": "B"}
        if kw["stage"] == "synthesis_rubric_reviewing":
            return {"dimensions": [{"dimension_id": "d", "result": "satisfied", "score": 2,
                "reason": "Concrete observation", "evidence": [{"field": "body", "quote": "Generated integration statement."}],
                "indicators": [], "repair_condition": None, "limitations": []}]}, {"call_id": "C-rubric"}
        assert kw["stage"] == "synthesis_reviewing"
        return c_output(kw["data"]), {"call_id": "C-source"}
    monkeypatch.setattr(inference, "invoke", invoke)
    lease = claim_root(run_id, "fixture")
    synthesis.execute_synthesis(run_id, "fixture", lease["epoch"], state)
    with SessionLocal() as db:
        obj = job(db, run_id)
        saved = load(obj.state_json)
        assert obj.status == "completed" and len(saved["synthesis_progress"]["rounds"]) == 1
        head = record(db, "synthesis-head:" + snap["version_id"])
        result = record(db, head["record_key"])
        assert result["automatic_status"] == "satisfied" and result["final_delivery_eligible"] is False
        assert result["source_content_hash"] == snap["content_hash"]
        assert result["source_quality_hash"] == db.get(TutorialVersion, snap["version_id"]).quality_hash
        assert result["source_demo"] is True and result["demo"] is True
        assert result["provenance"] == "model_synthesis_of_demo_source"
        assert db.get(TutorialVersion, snap["version_id"]).quality_report == source_quality
        assert db.get(Tutorial, snap["artifact_id"]).current_version_id == snap["version_id"]
        assert db.query(TutorialVersion).count() == 1
    from app.api.v2_synthesis import SynthesisResult
    from app.v2.synthesis_requests import read
    public_result = SynthesisResult.model_validate(read(snap["task_id"], snap["version_id"]))
    assert public_result.demo is True and public_result.source_is_current is True
    assert public_result.final_delivery_eligible is False
    assert calls == ["synthesis_generating", "synthesis_rubric_reviewing", "synthesis_reviewing"]


@pytest.mark.parametrize("field", ["content_hash", "quality_hash", "source_bundle_hash"])
def test_durable_executor_rejects_stale_source_before_model_call(client, monkeypatch, field):
    from app.v2 import inference
    from app.v2.runtime import claim_root
    _, run_id, state, *_ = durable_fixture(client)
    state["synthesis_source"][field] = "stale"
    from app.db import SessionLocal
    from app.v2.runtime import dump, job
    with SessionLocal() as db:
        job(db, run_id).state_json = dump(state)
        db.commit()
    monkeypatch.setattr(inference, "invoke", lambda *a, **k: pytest.fail("No call on stale source"))
    lease = claim_root(run_id, "fixture")
    with pytest.raises(DomainError, match="SYNTHESIS_SOURCE_STALE"):
        synthesis.execute_synthesis(run_id, "fixture", lease["epoch"], state)


@pytest.mark.parametrize("source_demo", [False, "true", None])
def test_durable_executor_cannot_erase_or_invent_demo_provenance(client, monkeypatch, source_demo):
    from app.db import SessionLocal
    from app.v2 import inference
    from app.v2.runtime import claim_root, dump, job
    _, run_id, state, *_ = durable_fixture(client)
    state["synthesis_source"]["source_demo"] = source_demo
    with SessionLocal() as db:
        job(db, run_id).state_json = dump(state)
        db.commit()
    monkeypatch.setattr(inference, "invoke", lambda *a, **k: pytest.fail("No call on false provenance"))
    lease = claim_root(run_id, "fixture")
    with pytest.raises(DomainError, match="SYNTHESIS_SOURCE_PROVENANCE_INVALID"):
        synthesis.execute_synthesis(run_id, "fixture", lease["epoch"], state)


def test_source_quality_changed_during_execution_cannot_publish_stale_synthesis(client, monkeypatch):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.insights import record
    from app.v2.runtime import claim_root
    snap, run_id, state, *_ = durable_fixture(client)
    def loop(*args, **kwargs):
        with SessionLocal() as db:
            db.get(TutorialVersion, snap["version_id"]).quality_hash = "0" * 64
            db.commit()
        return {"automatic_status": "satisfied"}
    monkeypatch.setattr(synthesis, "run_loop", loop)
    lease = claim_root(run_id, "fixture")
    with pytest.raises(DomainError, match="SYNTHESIS_SOURCE_OR_REVISION_STALE"):
        synthesis.execute_synthesis(run_id, "fixture", lease["epoch"], state)
    with SessionLocal() as db:
        assert record(db, "synthesis-head:" + snap["version_id"]) is None

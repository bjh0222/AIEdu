"""Synthetic phase-scoping and fixed-candidate reuse; no live network/provider."""
from copy import deepcopy
from uuid import uuid4

import pytest


@pytest.mark.parametrize("last", ["valid", "missing", "duplicate"])
def test_out_of_batch_rows_never_fill_or_invalidate_another_batch(monkeypatch, last):
    from app.v2 import inference
    from app.v2.contracts import sha
    from app.v2.optimization_acceptance import verify
    calls = []
    def invoke(*args, **kwargs):
        calls.append(kwargs)
        row = lambda identity: {"item_id": identity, "result": "satisfied", "evidence": [{"unit_id": "u", "quote": "fixed"}]}
        rows = [row(c["item_id"]) for c in kwargs["data"]["criteria"]]
        if len(calls) == 1:
            rows += [row("3"), row("3")]
        elif last == "missing":
            rows = []
        elif last == "duplicate":
            rows *= 2
        return {"criteria": rows}, {"call_id": str(len(calls))}
    monkeypatch.setattr(inference, "invoke", invoke)
    report = verify("r", "w", 1, {}, {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": str(i)} for i in range(4)]},
                    [{"unit_id": "u", "body": "fixed", "content_hash": sha("fixed")}])
    assert report["criteria"][-1]["audit"]["call_id"] == "2"
    assert report["criteria"][-1]["result"] == ("satisfied" if last == "valid" else "undecidable")
    assert len(report["ignored_unrequested_rows"]) == 2


@pytest.mark.parametrize("bad", [None, "hash", "document", "quote", "scope"])
def test_structured_evidence_binds_actual_document_hash_quote_and_scope(bad):
    from app.v2.contracts import digest
    from app.v2.optimization_acceptance import bind_evidence
    text = '{"check_plan_hash":"bound","not_a_correctness_certificate":true}'
    document = {"text": text, "hash": digest({"check_plan_hash": "bound", "not_a_correctness_certificate": True}), "unit_ids": ["u"]}
    proposed = {"document_id": "facts", "document_hash": document["hash"], "quote": '"not_a_correctness_certificate":true'}
    if bad == "hash": proposed["document_hash"] = "0" * 64
    if bad == "document": proposed["document_id"] = "invented"
    if bad == "quote": proposed["quote"] = "not present"
    bound = bind_evidence(proposed, [], {"other"} if bad == "scope" else {"u"}, {"facts": document})
    assert bool(bound) == (bad is None)
    if bound:
        assert text.encode()[bound["start_byte"]:bound["end_byte"]].decode() == proposed["quote"]
        assert bound["kind"] == "structured_input"


def test_process_preservation_is_not_a_professional_pass_or_prior_semantic_judgment():
    from app.v2.optimization_acceptance import execution_manifest
    plan = {"check_plan_hash": "p", "check_items": [{"item_id": "i", "required": True, "verifier_binding": "m"}]}
    report = {**plan, "checks": [{"item_id": "i", "method_version": "m", "result": "undecidable", "reason": "prior judgment"}],
              "findings": [], "tqi_verdict": "unresolved", "calibration_status": "not_calibrated"}
    facts = execution_manifest(report, plan)
    assert facts["barrier_preservation"]["check_definitions_unchanged"]
    assert facts["barrier_preservation"]["all_check_results_present"]
    assert not facts["barrier_preservation"]["reducer_is_pass"]
    assert not facts["barrier_preservation"]["professionally_calibrated"]
    assert "prior judgment" not in str(facts) and "undecidable" not in str(facts)
    assert report["checks"][0]["result"] == "undecidable"
    report["check_items"] = []
    assert not execution_manifest(report, plan)["barrier_preservation"]["check_definitions_unchanged"]


def checkpoint_fixture(client, *, assessment_versions=None):
    from test_task_run_v2 import task_run
    from test_optimization_prompt_loop import _prepare, _package, _candidate
    from app.models import TutorialVersion
    from app.v2.optimization import _frozen_bindings, validate_candidate, _persist_artifact
    from app.v2.runtime import write, job, load, dump, materialize
    from app.v2.contracts import canonical, digest, request_hash
    from app.v2.storage import BlobStore, CallDispatch
    _, _, snap = task_run(client)
    _prepare(client, snap)
    with write() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        version = db.get(TutorialVersion, snap["version_id"])
        package = _package(snap, _frozen_bindings(state))
        candidate = _candidate(snap, package)
        units = validate_candidate(candidate, package, version, materialize(version)["units"])
        fixed = [{k: u[k] for k in ("unit_id", "body", "content_hash", "title", "artifact_role", "items", "format_contract", "source_sections") if k in u} for u in units]
        checked = {"check_plan_hash": state["check_plan"]["check_plan_hash"], "check_items": state["check_plan"]["check_items"],
                   "checks": deepcopy(load(version.quality_report)["checks"]), "findings": [],
                   "quality_status": "needs_review", "tqi_verdict": "unresolved", "calibration_status": "not_calibrated"}
        for kind, identity in (assessment_versions or {}).items():
            checked[kind] = {"version_id": identity}
        identities = {}
        for call_id, stage, result, data in (("plan-c", "optimization_prompt", package, {}),
            ("candidate-b", "optimization", candidate, {"optimization_package": package}),
            ("accept-c", "optimization_acceptance", {}, {"units": fixed, "candidate_hash": digest(units),
                "frozen_bindings": package["frozen_bindings"], "tool_evidence": [], "citation_bindings": [], "network_citations": []})):
            identity = BlobStore().put(canonical(result))
            identities[call_id] = identity
            manifest = {"schema_version": "aiedu.prompt-manifest.v1", "stage": stage, "unit_id": "optimization",
                        "context_hash": state["context"]["context_hash"], "plan_hash": state["plan"]["plan_hash"], "entries": []}
            messages = [{"role": "system", "content": "fixture"}, {"role": "user", "content": canonical(data).decode()}]
            db.add(CallDispatch(call_id=call_id, root_job_id=source.id, run_id=source.run_id, unit_id="optimization", epoch=1, attempt=1,
                idempotency_key=str(uuid4()), request_hash=request_hash(manifest, messages, "https://fixture.invalid", "fixture", {}, {}), input_hash=state["context"]["context_hash"],
                manifest_json=dump(manifest), request_json=dump({"messages": messages}), provider="https://fixture.invalid", model="fixture",
                model_family="fixture", status="settled", outcome="success", result_blob_hash=identity))
    for kind, value, call_id in (("optimization_package", package, "plan-c"), ("optimization_candidate", candidate, "candidate-b"),
                                 ("optimization_full_recheck", checked, "candidate-b")):
        _persist_artifact(snap["run_id"], "fixture", 1, value, kind, {"call_id": call_id})
    with write() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        state.update(optimization_stop_reason="optimization_acceptance_unresolved", optimization_attempts=6)
        source.status, source.state_json = "completed", dump(state)
    return snap, identities["candidate-b"]


@pytest.mark.parametrize("disagree", [False, True])
def test_fixed_recheck_retains_assessed_candidate_version_without_inventing_new_identity(client, disagree):
    from app.models import TutorialVersion
    from app.v2.contracts import DomainError
    from app.v2.optimization_recheck import load_checkpoint
    from app.v2.runtime import write, job, load
    expected = str(uuid4())
    snap, candidate_hash = checkpoint_fixture(client, assessment_versions={"difficulty_review": expected,
        "automated_assessment": str(uuid4()) if disagree else expected})
    with write() as db:
        source = job(db, snap["run_id"])
        version = db.get(TutorialVersion, snap["version_id"])
        if disagree:
            with pytest.raises(DomainError, match="OPTIMIZATION_CHECKPOINT_VERSION_AMBIGUOUS"):
                load_checkpoint(db, source, load(source.state_json), version, candidate_hash)
        else:
            checkpoint = load_checkpoint(db, source, load(source.state_json), version, candidate_hash)
            assert checkpoint["new_version"] == expected


def test_explicit_candidate_recheck_reuses_content_full_check_and_attempt_counter(client, monkeypatch):
    from app.config import settings
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.api.v2_optimization import OptimizationRequest
    from app.v2 import inference, checker, generation_tools
    from app.v2.optimization import request_optimization
    from app.v2.runtime import job, load
    from app.v2.worker import execute_run
    from app.v2.storage import CallDispatch
    from app.v2.tasks import snapshot
    snap, candidate_hash = checkpoint_fixture(client)
    monkeypatch.setattr(settings, "v2_allow_paid", True)
    monkeypatch.setattr(checker, "evaluate", lambda *a, **k: pytest.fail("must not repeat full recheck"))
    monkeypatch.setattr(generation_tools, "generation_with_sources", lambda *a, **k: pytest.fail("must not regenerate candidate"))
    seen = []
    def invoke(*args, **kwargs):
        assert kwargs["stage"] == "optimization_acceptance"
        seen.append(kwargs)
        unit = kwargs["data"]["units"][0]
        return {"criteria": [{"item_id": c["item_id"], "result": "satisfied", "evidence": [{"unit_id": unit["unit_id"],
            "quote": "优化说明：补充边界条件。"}]} for c in kwargs["data"]["criteria"]]}, {"call_id": "acceptance-fixture"}
    monkeypatch.setattr(inference, "invoke", invoke)
    with SessionLocal() as db:
        source = job(db, snap["run_id"])
        version = db.get(TutorialVersion, snap["version_id"])
        before_calls = db.query(CallDispatch).count()
        payload = OptimizationRequest(expected_revision=source.state_revision, run_id=source.run_id,
            version_id=snap["version_id"], content_hash=snap["content_hash"], quality_hash=version.quality_hash, candidate_recheck=candidate_hash)
    requested = request_optimization(payload, "explicit-recheck")
    assert request_optimization(payload, "explicit-recheck") == requested
    execute_run(requested["run_id"])
    result = snapshot(snap["task_id"])
    assert result["state"] == "completed" and result["version_id"] != snap["version_id"]
    assert result["units"][0]["body"] == snap["units"][0]["body"] + "\n\n优化说明：补充边界条件。"
    assert seen and result["quality"]["quality_status"] != "passed"
    with SessionLocal() as db:
        state = load(job(db, requested["run_id"]).state_json)
        assert state["optimization_attempts"] == 6
        assert state["quality"]["fixed_recheck_provenance"]["new_A_B_or_full_recheck_calls"] == 0
        assert db.query(CallDispatch).count() == before_calls  # synthetic invoke does not dispatch
        assert state["budget"]["calls"] == 12


@pytest.mark.parametrize("fault", ["unknown", "content", "plan", "ineligible", "unbound"])
def test_fixed_candidate_recheck_refuses_unknown_changed_or_unbound_evidence(client, fault):
    from app.db import SessionLocal
    from app.models import TutorialVersion
    from app.v2.runtime import write, job, load, dump
    from app.v2.optimization_recheck import load_checkpoint
    from app.v2.storage import CallDispatch
    from app.v2.contracts import DomainError
    snap, candidate_hash = checkpoint_fixture(client)
    with write() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        if fault == "unknown": db.get(CallDispatch, "candidate-b").status = "unknown"
        if fault == "content":
            call = db.get(CallDispatch, "accept-c")
            data = load(call.request_json)
            data["messages"][1]["content"] = '{"units":[]}'
            call.request_json = dump(data)
        if fault == "plan": state["check_plan"]["check_plan_hash"] = "changed"
        if fault == "ineligible": state["optimization_stop_reason"] = "diagnostic_regression"
        if fault == "unbound": candidate_hash = "0" * 64
        source.state_json = dump(state)
    with SessionLocal() as db:
        source = job(db, snap["run_id"])
        with pytest.raises(DomainError):
            load_checkpoint(db, source, load(source.state_json), db.get(TutorialVersion, snap["version_id"]), candidate_hash)


def test_candidate_recheck_preflights_remaining_root_budget_before_enqueuing(client):
    from app.db import SessionLocal
    from app.models import GenerationJob, TutorialVersion
    from app.api.v2_optimization import OptimizationRequest
    from app.v2.optimization import request_optimization, cumulative_calls
    from app.v2.runtime import job, load, dump, write
    from app.v2.contracts import DomainError
    snap, candidate_hash = checkpoint_fixture(client)
    with write() as db:
        source = job(db, snap["run_id"])
        state = load(source.state_json)
        state["budget"]["calls"] = state["optimization_policy"]["root_call_budget"] = cumulative_calls(db, source.id)
        source.state_json = dump(state)
        version = db.get(TutorialVersion, snap["version_id"])
        payload = OptimizationRequest(expected_revision=source.state_revision, run_id=source.run_id,
            version_id=version.id, content_hash=version.content_hash, quality_hash=version.quality_hash, candidate_recheck=candidate_hash)
        count = db.query(GenerationJob).count()
    with pytest.raises(DomainError, match="CALL_BUDGET_EXHAUSTED"):
        request_optimization(payload, "insufficient-root-budget")
    with SessionLocal() as db:
        assert db.query(GenerationJob).count() == count


def test_recheck_keeps_original_normalized_item_ids_and_rejects_semantic_changes():
    from app.v2.items import normalize_items
    from app.v2.optimization_recheck import same_fixed_content
    raw = {"items": [{"stem_markdown": "fixed question", "answer": {"final": "fixed answer", "reasoning": "fixed reasoning"}}]}
    first, second = [normalize_items(raw, "exercise", "u") for _ in range(2)]
    assert first[0]["item_id"] != second[0]["item_id"]
    units = lambda items: [{"unit_id": "u", "body": "fixed projection", "content_hash": "hash", "items": items}]
    assert same_fixed_content(units(first), units(second))
    old = deepcopy(second)
    second[0]["answer"]["reasoning"] = "changed reasoning"
    assert not same_fixed_content(units(first), units(second))
    assert first[0]["item_id"] != old[0]["item_id"]


@pytest.mark.parametrize("fault", [None, "content", "contract", "document", "criterion", "duplicate", "unrequested", "unbound", "unknown"])
def test_acceptance_reuse_requires_exact_context_criterion_and_bound_raw_evidence(fault):
    from app.v2.contracts import sha
    from app.v2.optimization_acceptance import prepared_inputs, reusable_observations
    units = [{"unit_id": "u", "title": "Title", "body": "fixed", "content_hash": sha("fixed")}]
    package = {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": "a", "pass_condition": "fixed"}]}
    criteria, data = prepared_inputs({}, package, units)
    row = {"item_id": "a", "result": "satisfied", "evidence": [{"unit_id": "u", "quote": "fixed"}]}
    observation = {"data": deepcopy(data) | {"criteria": deepcopy(criteria)}, "output": {"criteria": [row]},
                   "audit": {"call_id": "settled-c"}}
    if fault == "content": observation["data"]["units"][0]["body"] = "changed"
    if fault == "contract": observation["data"]["frozen_task_contract"]["raw_input"] = "changed"
    if fault == "document": observation["data"]["evidence_documents"]["candidate_execution_facts"]["hash"] = "bad"
    if fault == "criterion": observation["data"]["criteria"][0]["pass_condition"] = "weaker"
    if fault == "duplicate": observation["output"]["criteria"].append(deepcopy(row))
    if fault == "unrequested": observation["data"]["criteria"] = []
    if fault == "unbound": row["evidence"][0]["quote"] = "invented"
    if fault == "unknown": row["result"] = "undecidable"
    assert bool(reusable_observations(criteria, data, units, package, [observation])) == (fault is None)


def test_recheck_calls_only_unresolved_criteria_without_sending_previous_judgments(monkeypatch):
    from app.v2 import inference
    from app.v2.contracts import sha
    from app.v2.optimization_acceptance import prepared_inputs, verify
    # Repeated title is not unique body evidence; typed item fields are not body text.
    units = [{"unit_id": "u", "title": "Title", "body": "Title fixed Title", "content_hash": sha("Title fixed Title"),
              "items": [{"ordinal": 15, "stem_markdown": "question", "answer": {"final": "answer"}}]}]
    package = {"frozen_bindings": {}, "acceptance_criteria": [{"item_id": i} for i in ("content", "title", "schema")]}
    criteria, data = prepared_inputs({}, package, units)
    observation = {"data": deepcopy(data) | {"criteria": deepcopy(criteria)}, "output": {"criteria": [
        {"item_id": i, "result": "satisfied", "method": "prior semantic judgment", "evidence": [{"unit_id": "u", "quote": q}]}
        for i, q in (("content", "fixed"), ("title", "Title"), ("schema", '"ordinal":15'))]},
        "audit": {"call_id": "settled-c"}}
    seen = []
    def invoke(*args, **kwargs):
        seen.append(kwargs["data"])
        assert [c["item_id"] for c in kwargs["data"]["criteria"]] == ["title", "schema"]
        assert "prior semantic judgment" not in str(kwargs["data"])
        document = kwargs["data"]["evidence_documents"]["candidate_units"]
        return {"criteria": [{"item_id": i, "result": "satisfied", "evidence": [{"document_id": "candidate_units",
            "document_hash": document["hash"], "quote": q}]} for i, q in (("title", '"title":"Title"'), ("schema", '"ordinal":15'))]}, {"call_id": "new-c"}
    monkeypatch.setattr(inference, "invoke", invoke)
    report = verify("r", "w", 1, {}, package, units, prior_observations=[observation])
    assert len(seen) == report["new_acceptance_calls"] == 1
    assert report["status"] == "satisfied"
    assert report["reused_criteria"] == [{"item_id": "content", "call_id": "settled-c"}]
    assert [r["audit"]["call_id"] for r in report["criteria"]] == ["settled-c", "new-c", "new-c"]
    assert report["calibration_status"] == "not_calibrated" and not report["model_family_independent"]

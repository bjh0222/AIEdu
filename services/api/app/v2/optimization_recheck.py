"""Explicit fixed-candidate acceptance continuation; no A/B or content rewrite."""
from copy import deepcopy
from uuid import uuid4

from ..db import SessionLocal
from ..models import Tutorial, TutorialVersion
from .contracts import DomainError, digest, sha, request_hash
from .runtime import dump, fence, job, load, materialize, publish, write
from .storage import BlobStore, CallDispatch


def _artifact(state, kind, identity):
    ref = state.get(kind + "_artifacts", {}).get(identity, {})
    if ref.get("kind") != kind or ref.get("blob_hash") != identity:
        raise DomainError("OPTIMIZATION_CHECKPOINT_UNBOUND", 422)
    raw = BlobStore().get(identity)
    if sha(raw) != identity:
        raise DomainError("OPTIMIZATION_CHECKPOINT_HASH_CHANGED", 422)
    return load(raw.decode()), ref


def _settled(db, call_id, ancestors):
    call = db.get(CallDispatch, call_id)
    if not call or call.root_job_id not in ancestors or call.status != "settled" or call.outcome != "success":
        raise DomainError("OPTIMIZATION_CHECKPOINT_CALL_UNSETTLED", 422)
    try:
        saved = load(call.request_json)
        same = request_hash(load(call.manifest_json), saved.get("messages", []), call.provider, call.model,
                            saved.get("parameters", {}), saved.get("output_schema", {})) == call.request_hash
    except (KeyError, TypeError, ValueError):
        same = False
    if not same:
        raise DomainError("OPTIMIZATION_CHECKPOINT_REQUEST_CHANGED", 422)
    return call


def same_fixed_content(expected, saved):
    """Item UUIDs are wrapper-owned. Reuse the original checked UUIDs, not new ones."""
    if not isinstance(saved, list) or len(expected) != len(saved):
        return False
    for left, right in zip(expected, saved):
        if not isinstance(right, dict) or {k: v for k, v in left.items() if k != "items"} != {k: v for k, v in right.items() if k != "items"}:
            return False
        a, b = left.get("items", []), right.get("items", [])
        if len(a) != len(b) or len({i.get("item_id") for i in b if isinstance(i, dict)}) != len(b):
            return False
        for new, old in zip(a, b):
            if (not isinstance(old, dict) or not old.get("item_id")
                or old.get("content_hash") != digest({k: v for k, v in old.items() if k != "content_hash"})
                or {k: v for k, v in new.items() if k not in {"item_id", "content_hash"}} !=
                   {k: v for k, v in old.items() if k not in {"item_id", "content_hash"}}):
                return False
    return True


def load_checkpoint(db, source, state, version, candidate_hash):
    from .optimization import ancestor_job_ids, _frozen_bindings, validate_package, normalize_candidate_envelope, validate_candidate, unit_content_identity
    if source.status != "completed" or state.get("optimization_stop_reason") != "optimization_acceptance_unresolved":
        raise DomainError("OPTIMIZATION_CHECKPOINT_NOT_ELIGIBLE", 422)
    tutorial = db.get(Tutorial, version.tutorial_id)
    if tutorial is None or tutorial.current_version_id != version.id:
        raise DomainError("VERSION_STALE")
    ancestors = ancestor_job_ids(db, source.id)
    if db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(ancestors), CallDispatch.status.in_(["authorized", "sent", "unknown"])).count():
        raise DomainError("OPTIMIZATION_CHECKPOINT_CALL_UNSETTLED", 422)
    candidate, candidate_ref = _artifact(state, "optimization_candidate", candidate_hash)
    b_call = _settled(db, candidate_ref.get("call_id"), ancestors)
    if b_call.result_blob_hash != candidate_hash:
        raise DomainError("OPTIMIZATION_CHECKPOINT_UNBOUND", 422)
    request = load(b_call.request_json)
    b_data = load(request["messages"][1]["content"])
    package_hash = digest(b_data.get("optimization_package"))
    package, package_ref = _artifact(state, "optimization_package", package_hash)
    c_call = _settled(db, package_ref.get("call_id"), ancestors)
    if c_call.result_blob_hash != package_hash:
        raise DomainError("OPTIMIZATION_CHECKPOINT_UNBOUND", 422)
    validate_package(package, version, expected_bindings=_frozen_bindings(state))
    candidate, _ = normalize_candidate_envelope(candidate, package, version)
    base_units = materialize(version).get("units", [])
    units = validate_candidate(candidate, package, version, base_units)
    fixed = [{k: u[k] for k in ("unit_id", "body", "content_hash", "title", "artifact_role", "items", "format_contract", "source_sections") if k in u} for u in units]
    # Only this source run's actual acceptance dispatch can attest to the full
    # fixed content and network evidence used in its preceding full recheck.
    acceptances, observations = [], []
    for call in db.query(CallDispatch).filter_by(root_job_id=source.id):
        if load(call.manifest_json).get("stage") != "optimization_acceptance":
            continue
        _settled(db, call.call_id, ancestors)
        data = load(load(call.request_json)["messages"][1]["content"])
        if same_fixed_content(fixed, data.get("units")) and data.get("frozen_bindings") == package["frozen_bindings"]:
            acceptances.append(data)
            if call.result_blob_hash:
                raw = BlobStore().get(call.result_blob_hash)
                if sha(raw) != call.result_blob_hash:
                    raise DomainError("OPTIMIZATION_CHECKPOINT_HASH_CHANGED", 422)
                output = load(raw.decode())
                if isinstance(output, dict):
                    observations.append({"data": data, "output": output, "audit": {
                        "call_id": call.call_id, "request_hash": call.request_hash,
                        "model_family": call.model_family, "reuse": "settled_fixed_candidate"}})
    if not acceptances or len({d.get("candidate_hash") for d in acceptances}) != 1 or len({digest(d["units"]) for d in acceptances}) != 1:
        raise DomainError("OPTIMIZATION_CHECKPOINT_CONTENT_UNBOUND", 422)
    for unit, saved in zip(units, acceptances[0]["units"]):
        if "items" in saved:
            unit["items"] = deepcopy(saved["items"])
    fixed = acceptances[0]["units"]
    matches = [(identity, ref) for identity, ref in state.get("optimization_full_recheck_artifacts", {}).items()
               if ref.get("call_id") == b_call.call_id]
    if len(matches) != 1:
        raise DomainError("OPTIMIZATION_CHECKPOINT_RECHECK_AMBIGUOUS", 422)
    report_hash = matches[0][0]
    checked, _ = _artifact(state, "optimization_full_recheck", report_hash)
    if checked.get("check_plan_hash") != state.get("check_plan", {}).get("check_plan_hash"):
        raise DomainError("OPTIMIZATION_CHECKPOINT_PLAN_STALE", 422)
    expected_profiles = [p.get("profile_hash") for p in state.get("context", {}).get("quality_profiles", [])]
    if checked.get("calibration_scope", {}).get("profile_hashes", expected_profiles) != expected_profiles:
        raise DomainError("OPTIMIZATION_CHECKPOINT_PLAN_STALE", 422)
    # Every model observation reused below must have a successful settled call;
    # the original call IDs/usage/outcomes are retained, not copied as new calls.
    def audit_ids(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"call_id", "blind_solver_call_id"} and item:
                    yield item
                else:
                    yield from audit_ids(item)
        elif isinstance(value, list):
            for item in value:
                yield from audit_ids(item)
    for call_id in set(audit_ids(checked)):
        _settled(db, call_id, ancestors)
    evidence = acceptances[0]
    if any({k: d.get(k) for k in ("tool_evidence", "citation_bindings", "network_citations")} !=
           {k: evidence.get(k) for k in ("tool_evidence", "citation_bindings", "network_citations")} for d in acceptances):
        raise DomainError("OPTIMIZATION_CHECKPOINT_SOURCES_AMBIGUOUS", 422)
    network = {"method_version": "settled-candidate-source-reuse.v1", "receipts": evidence.get("tool_evidence", []),
               "citation_bindings": evidence.get("citation_bindings", []), "calls": [{"call_id": b_call.call_id}],
               "original_candidate_hash": evidence["candidate_hash"], "new_generation_calls": 0}
    if any(c.get("status") != "literal_bound" for c in network["citation_bindings"]):
        raise DomainError("OPTIMIZATION_CITATION_UNBOUND", 422)
    for unit, base in zip(units, base_units):
        if unit_content_identity(unit) != unit_content_identity(base):
            unit["network_research"] = network
    versions = {f.get("location", {}).get("version_id") for f in checked.get("findings", [])} - {None}
    # A clean candidate may have no findings. Its actual C assessment identity
    # still exists; do not invent a fresh UUID and then attach the old review.
    for kind in ("difficulty_review", "automated_assessment"):
        report = checked.get(kind)
        if not isinstance(report, dict) or report.get("version_id") is None:
            continue  # Legacy reports remain unbound; consumption rejects them.
        identity = report["version_id"]
        if not isinstance(identity, str) or not identity:
            raise DomainError("OPTIMIZATION_CHECKPOINT_VERSION_AMBIGUOUS", 422)
        versions.add(identity)
    if len(versions) > 1 or any(db.get(TutorialVersion, v) for v in versions):
        raise DomainError("OPTIMIZATION_CHECKPOINT_VERSION_AMBIGUOUS", 422)
    audit = lambda call: {"call_id": call.call_id, "request_hash": call.request_hash,
                          "model_family": call.model_family, "reuse": "settled_fixed_candidate"}
    return {"package": package, "units": units, "base_units": base_units, "checked": checked,
            "prior_observations": observations,
            "c_audit": audit(c_call), "b_audit": audit(b_call), "network_trace": network,
            "citations": evidence.get("network_citations", []), "new_version": next(iter(versions), str(uuid4())),
            "provenance": {"source_run_id": source.run_id, "candidate_blob_hash": candidate_hash,
                "package_hash": package_hash, "full_recheck_hash": report_hash,
                "original_candidate_hash": evidence["candidate_hash"], "fixed_content_hash": digest(fixed),
                "content_changed": False, "new_A_B_or_full_recheck_calls": 0}}


def execute_recheck(run_id, worker, epoch, provider_factory=None):
    from .optimization import _persist_artifact, _stop
    with SessionLocal() as db:
        current = job(db, run_id)
        state = load(current.state_json)
        request = state["optimization_request"]
        source = job(db, request["base_run_id"])
        version = db.get(TutorialVersion, request["base_version_id"])
        if version is None or version.id != state.get("version_id") or version.content_hash != request["base_content_hash"] or version.quality_hash != request["base_quality_hash"]:
            raise DomainError("VERSION_STALE")
        checkpoint = load_checkpoint(db, source, load(source.state_json), version, request["candidate_recheck"])
    _persist_artifact(run_id, worker, epoch, checkpoint["provenance"], "optimization_candidate_recheck", checkpoint["b_audit"])
    checked = deepcopy(checkpoint["checked"])
    checked["fixed_recheck_provenance"] = checkpoint["provenance"]
    continued = finish_candidate(run_id, worker, epoch, state, version, checkpoint["base_units"], checkpoint["units"],
        load(version.quality_report), checked, checkpoint["package"], checkpoint["c_audit"], checkpoint["b_audit"],
        checkpoint["network_trace"], checkpoint["citations"], checkpoint["new_version"],
        int(state.get("optimization_rounds", 0)), provider_factory, prior_observations=checkpoint["prior_observations"])
    if continued:
        # This explicit action never silently starts a fresh generation cycle.
        _stop(run_id, worker, epoch, "candidate_recheck_completed_uncalibrated")


def finish_candidate(run_id, worker, epoch, state, version, units, new_units, quality, checked,
                     package, c_audit, b_audit, network_trace, citations, new_version, rounds, provider_factory=None,
                     *, prior_observations=None):
    from .optimization import diagnostic_regressions, _persist_artifact, _stop, OPTIMIZATION_SKILL, unit_content_identity
    from .optimization_acceptance import verify
    from .quality import dependency_closure
    regressions = diagnostic_regressions(quality, checked)
    if regressions:
        _persist_artifact(run_id, worker, epoch, {"base_version": version.id,
            "candidate_version": new_version, "regressions": regressions}, "optimization_regression", b_audit)
        _stop(run_id, worker, epoch, "diagnostic_regression")
        return False
    old_violations = {c["item_id"] for c in quality.get("checks", []) if c.get("result") == "violated"}
    new_violations = {c["item_id"] for c in checked.get("checks", []) if c.get("result") == "violated"}
    resolved_old, unresolved_old = [], []
    for finding in (f for f in quality.get("findings", []) if f.get("status") == "confirmed"):
        assertion = finding.get("acceptance_assertion")
        check = next((c for c in checked.get("checks", []) if c.get("item_id") == finding.get("item_id")), None)
        method_matches = not isinstance(assertion, dict) or not assertion.get("method") or check is None or check.get("method_version") == assertion.get("method")
        if check and check.get("result") == "satisfied" and isinstance(assertion, dict) and method_matches:
            resolved = deepcopy(finding)
            resolved.update(status="resolved", resolved_in_version=new_version, review_evidence=[{
                "method_version": "optimization.v1", "plan_call_id": c_audit.get("call_id"),
                "candidate_call_id": b_audit.get("call_id"), "check_result": "satisfied",
                "acceptance_assertion": assertion, "new_regressions": False}])
            resolved_old.append(resolved)
        else:
            unresolved_old.append(finding)
    if new_violations - old_violations or unresolved_old or any(f.get("status") == "confirmed" for f in checked.get("findings", [])):
        _stop(run_id, worker, epoch, "regression_or_unresolved")
        return False
    acceptance = verify(run_id, worker, epoch, state, package, new_units, provider_factory,
                        network_trace=network_trace, recheck=checked, candidate_citations=citations,
                        prior_observations=prior_observations, version_id=new_version)
    _persist_artifact(run_id, worker, epoch, acceptance, "optimization_acceptance", acceptance["audit"])
    if acceptance["status"] != "satisfied":
        if state.get("execution_policy", {}).get("criterion_assessment") and prior_observations is None:
            candidate_hash = digest([(u["unit_id"], unit_content_identity(u)) for u in new_units])
            with write() as db:
                obj = job(db, run_id)
                fence(obj, worker, epoch)
                current = load(obj.state_json)
                previous = current.setdefault("rejected_candidate_hashes", [])
                if candidate_hash not in previous:
                    previous.append(candidate_hash)
                    current["last_rejected_candidate_hash"] = candidate_hash
                    current["optimization_rounds"] = rounds + 1
                    obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
                    # Best content is unchanged; C receives source-bound rejection
                    # feedback on the next bounded round, not a fake acceptance.
                    return True
        _stop(run_id, worker, epoch, "optimization_acceptance_unresolved")
        return False
    package_hash = digest(package)
    changed_ids = {b["unit_id"] for a, b in zip(units, new_units) if unit_content_identity(a) != unit_content_identity(b)}
    checked.setdefault("findings", []).extend(resolved_old)
    checked.update(semantic_recheck=dependency_closure(new_units, changed_ids),
        skipped_checks=[c["item_id"] for c in checked.get("checks", []) if c.get("result") == "not_run"],
        repair_correctness="plausible_not_correct", optimization_skill=OPTIMIZATION_SKILL,
        optimization_package_hash=package_hash, network_trace=network_trace,
        optimization_acceptance=acceptance, _version_id=new_version)
    published = publish(run_id, worker, epoch, new_units, checked,
                        f"{run_id}:optimization:{rounds}:publish", base_version=version.id)
    with write() as db:
        obj = job(db, run_id)
        fence(obj, worker, epoch)
        current = load(obj.state_json)
        current["best_so_far_version"] = published
        current["optimization_rounds"] = rounds + 1
        current.setdefault("optimization_history", []).append({
            "version_id": published, "parent_version_id": version.id, "package_hash": package_hash,
            "skill_hash": OPTIMIZATION_SKILL["hash"], "plan_call_id": c_audit.get("call_id"),
            "candidate_call_id": b_audit.get("call_id"), "full_recheck": True,
            "network_sources": len(network_trace.get("receipts", [])),
            "professional_progress": "not_run", "new_deterministic_regressions": False})
        obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
    diagnostic_failures = (any(r.get("result") == "violated" for r in (checked.get("diagnostic_review") or {}).get("dimensions", [])) or
        any((checked.get(key) or {}).get("result") not in {"satisfied", "not_applicable"}
            for key in ("difficulty_review", "automated_assessment") if state.get("execution_policy", {}).get("criterion_assessment")))
    if not unresolved_old and not package.get("confirmed_findings") and not diagnostic_failures:
        _stop(run_id, worker, epoch, "optimization_completed_uncalibrated")
        return False
    return True

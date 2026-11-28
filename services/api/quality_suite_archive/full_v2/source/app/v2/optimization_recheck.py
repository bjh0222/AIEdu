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
    validate_package(package, version, skill=package.get("skill", {}),
                     expected_bindings=_frozen_bindings(state),
                     require_directives=bool(state.get("plan", {}).get("blueprint")))
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
        int(state.get("optimization_rounds", 0)), provider_factory,
        prior_observations=checkpoint["prior_observations"],
        repair_ledger=state.get("repair_ledger"))
    if continued:
        # This explicit action never silently starts a fresh generation cycle.
        _stop(run_id, worker, epoch, "candidate_recheck_completed_uncalibrated")


def finalize_second_correctness_repair(
        run_id, worker, epoch, state, version, units, new_units, previous_recheck,
        package, b_audit, network_trace, new_version, rounds, repair_ledger=None):
    from .optimization import OPTIMIZATION_SKILL, _persist_artifact, _stop, unit_content_identity
    from .quality import POLICY as DEFAULT_REPAIR_POLICY
    from .repair_directives import update_ledger

    changed_ids = {
        candidate["unit_id"] for base, candidate in zip(units, new_units)
        if unit_content_identity(base) != unit_content_identity(candidate)
    }
    candidate_hash = digest([
        (unit["unit_id"], unit_content_identity(unit)) for unit in new_units
    ])
    finalization = {
        "schema_version": "correctness-repair-finalization.v1",
        "status": "final",
        "reason": "second_b_repair_completed",
        "profile_id": "correctness_only.v1",
        "candidate_version": new_version,
        "candidate_hash": candidate_hash,
        "parent_version_id": version.id,
        "directive_ids": [
            row["directive_id"] for row in package.get("directives", [])
        ],
        "finding_ids": [
            row["finding_id"] for row in package.get("confirmed_findings", [])
        ],
        "previous_recheck_quality_hash": digest(previous_recheck),
        "rechecked_after_final_repair": False,
    }
    unit_results = [{
        "unit_id": unit["unit_id"],
        "result": "not_rechecked",
        "items": [
            {"item_id": item.get("item_id"), "result": "not_rechecked"}
            for item in unit.get("items", [])
        ],
    } for unit in new_units]
    report = {
        **deepcopy(previous_recheck),
        "tqi_verdict": "conditional_pass",
        "quality_status": "needs_review",
        "calibration_status": "not_calibrated",
        "blocking_reasons": [],
        "checks": [{
            "item_id": "Q.item_correctness",
            "result": "not_run",
            "reason": "second_b_repair_completed_without_final_c_recheck",
            "method_version": "correctness-only.v1",
        }],
        "findings": [],
        "item_correctness": {
            "method_version": "correctness-only.v1",
            "quality_profile": deepcopy(state.get("quality_profile")),
            "unit_results": unit_results,
            "repair_directives": [],
            "repair_acceptance": [],
            "traces": [],
            "scope": "generated_item_fields_only",
            "scope_description": (
                "correctness_only: checks only generated question and answer errors. "
                "It does not certify professional correctness, teaching quality, "
                "measured difficulty, learning gain, or the archived full quality suite."
            ),
            "professional_certification": False,
        },
        "correctness_repair_finalization": finalization,
        "repair_policy": deepcopy(state.get("optimization_policy") or DEFAULT_REPAIR_POLICY),
        "repair_correctness": "not_calibrated",
        "optimization_skill": OPTIMIZATION_SKILL,
        "optimization_package_hash": digest(package),
        "network_trace": network_trace,
        "semantic_recheck": {
            "method_version": "second-repair-finalization.v1",
            "changed_unit_ids": sorted(changed_ids),
            "full_semantic_rerun": False,
            "final_c_recheck": False,
        },
        "_version_id": new_version,
    }
    ledger = None
    if repair_ledger and repair_ledger.get("events"):
        results = [{
            "directive_id": row["directive_id"],
            "finding_id": row["finding_id"],
            "result": "not_rechecked",
        } for row in package.get("directives", [])]
        ledger = update_ledger(
            repair_ledger,
            candidate_hash=candidate_hash,
            candidate_version=new_version,
            recheck_scope=report["semantic_recheck"],
            results=results,
            status="finalized",
            call_ids=[b_audit.get("call_id")],
        )
        report["repair_ledger"] = ledger
        _persist_artifact(run_id, worker, epoch, ledger, "repair_ledger", b_audit)
    _persist_artifact(
        run_id, worker, epoch,
        {"report": report, "candidate_hash": candidate_hash},
        "correctness_second_repair_finalization",
        b_audit,
    )
    published = publish(
        run_id, worker, epoch, new_units, report,
        f"{run_id}:correctness-second-repair-final:{rounds}",
        base_version=version.id,
    )
    with write() as db:
        obj = job(db, run_id)
        fence(obj, worker, epoch)
        current = load(obj.state_json)
        current["best_so_far_version"] = published
        current["optimization_rounds"] = rounds + 1
        if ledger is not None:
            current["repair_ledger"] = ledger
        current.setdefault("optimization_history", []).append({
            "version_id": published,
            "parent_version_id": version.id,
            "package_hash": digest(package),
            "skill_hash": OPTIMIZATION_SKILL["hash"],
            "candidate_call_id": b_audit.get("call_id"),
            "full_recheck": False,
            "network_sources": len(network_trace.get("receipts", [])),
            "professional_progress": "not_run",
            "new_deterministic_regressions": False,
            "status": "finalized_without_recheck",
        })
        obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
    _stop(run_id, worker, epoch, "correctness_only_completed")
    return published


def finish_candidate(run_id, worker, epoch, state, version, units, new_units, quality, checked,
                     package, c_audit, b_audit, network_trace, citations, new_version, rounds, provider_factory=None,
                     *, prior_observations=None, repair_ledger=None):
    from .optimization import diagnostic_regressions, _persist_artifact, _stop, OPTIMIZATION_SKILL, unit_content_identity
    from .optimization_acceptance import verify
    from .quality import dependency_closure, enabled_findings_for_plan, layer_enabled
    candidate_hash = digest([(unit["unit_id"], unit_content_identity(unit)) for unit in new_units])

    def write_ledger(status, resolved_finding_ids):
        if not repair_ledger or not repair_ledger.get("events"):
            return None
        from .repair_directives import directive_recheck_results, update_ledger
        results = directive_recheck_results(package, checked, new_units, resolved_finding_ids)
        status_value = status
        if isinstance(status, str):
            status_value = {
                row["finding_id"]: (
                    "resolved" if row.get("result") == "satisfied"
                    else "blocked" if status == "resolved" else status
                )
                for row in results
            }
        updated = update_ledger(
            repair_ledger,
            candidate_hash=candidate_hash,
            candidate_version=new_version,
            recheck_scope=checked.get("recheck_scope"),
            results=results,
            status=status_value,
            call_ids=[c_audit.get("call_id"), b_audit.get("call_id")],
        )
        checked["repair_ledger"] = updated
        _persist_artifact(run_id, worker, epoch, updated, "repair_ledger", b_audit)
        with write() as db:
            obj = job(db, run_id)
            fence(obj, worker, epoch)
            current = load(obj.state_json)
            current["repair_ledger"] = updated
            obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
        return updated

    regressions = diagnostic_regressions(quality, checked, state.get("check_plan"))
    if regressions:
        _persist_artifact(run_id, worker, epoch, {"base_version": version.id,
            "candidate_version": new_version, "regressions": regressions}, "optimization_regression", b_audit)
        # A diagnostic regression rejects this candidate, but it is also useful
        # source-bound feedback for the next bounded generation cycle. The
        # optimization loop already carries rejected full-recheck reports to C;
        # preserve the candidate as rejected and let that loop decide whether
        # its finite round/attempt budgets allow another correction.
        continued = False
        if state.get("execution_policy", {}).get("criterion_assessment") and prior_observations is None:
            with write() as db:
                obj = job(db, run_id)
                fence(obj, worker, epoch)
                current = load(obj.state_json)
                previous = current.setdefault("rejected_candidate_hashes", [])
                if candidate_hash not in previous:
                    previous.append(candidate_hash)
                    current["last_rejected_candidate_hash"] = candidate_hash
                    current.setdefault("rejected_candidate_regressions", []).append({
                        "candidate_hash": candidate_hash,
                        "base_version": version.id,
                        "candidate_version": new_version,
                        "regressions": deepcopy(regressions),
                        "authority": "unpublished_candidate_observation_not_confirmed_fact",
                    })
                    current["optimization_rounds"] = rounds + 1
                    obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
                    continued = True
        if continued:
            write_ledger("blocked", set())
            return True
        write_ledger("blocked", set())
        _stop(run_id, worker, epoch, "diagnostic_regression")
        return False
    def enabled_violations(report):
        checks = {row.get("item_id"): row for row in report.get("checks", [])
                  if isinstance(row, dict) and row.get("item_id")}
        return {
            item["item_id"] for item in state.get("check_plan", {}).get("check_items", [])
            if item.get("required")
            and layer_enabled(item, state.get("check_plan", {}).get("quality_layers"))
            and checks.get(item["item_id"], {}).get("result") == "violated"
        }

    old_violations = enabled_violations(quality)
    new_violations = enabled_violations(checked)
    resolved_old, unresolved_old = [], []
    for finding in enabled_findings_for_plan(
            state.get("check_plan", {}), quality.get("findings", [])):
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
    new_confirmed = enabled_findings_for_plan(
        state.get("check_plan", {}), checked.get("findings", []))
    from .quality_suite import quality_profile_is_correctness_only
    correctness_only = quality_profile_is_correctness_only(state)
    if (new_violations - old_violations or unresolved_old
            or any(f.get("status") == "confirmed" for f in new_confirmed)):
        write_ledger("blocked", {row["finding_id"] for row in resolved_old})
        if correctness_only:
            policy = state.get("optimization_policy") or {}
            if rounds + 1 < int(policy.get("max_repair_rounds_per_run", 0)):
                package_hash = digest(package)
                changed_ids = {
                    b["unit_id"] for a, b in zip(units, new_units)
                    if unit_content_identity(a) != unit_content_identity(b)
                }
                checked.update(
                    semantic_recheck=dependency_closure(new_units, changed_ids),
                    skipped_checks=[
                        row["item_id"] for row in checked.get("checks", [])
                        if row.get("result") == "not_run"
                    ],
                    repair_correctness="not_calibrated",
                    optimization_skill=OPTIMIZATION_SKILL,
                    optimization_package_hash=package_hash,
                    network_trace=network_trace,
                    _version_id=new_version,
                )
                published = publish(
                    run_id, worker, epoch, new_units, checked,
                    f"{run_id}:correctness-repair:{rounds}:publish",
                    base_version=version.id,
                )
                _persist_artifact(run_id, worker, epoch, {
                    "method_version": "correctness-repair-rejection.v1",
                    "candidate_hash": candidate_hash,
                    "candidate_version": new_version,
                    "quality": checked,
                }, "correctness_repair_rejected", b_audit)
                old_issue_count = len(enabled_findings_for_plan(
                    state.get("check_plan", {}), quality.get("findings", [])))
                remaining_issue_count = (
                    len(unresolved_old)
                    + len(new_confirmed)
                )
                with write() as db:
                    obj = job(db, run_id)
                    fence(obj, worker, epoch)
                    current = load(obj.state_json)
                    rejected = current.setdefault("rejected_candidate_hashes", [])
                    if candidate_hash not in rejected:
                        rejected.append(candidate_hash)
                    current["last_rejected_candidate_hash"] = candidate_hash
                    if remaining_issue_count < old_issue_count:
                        current["best_so_far_version"] = published
                    else:
                        current.setdefault("best_so_far_version", version.id)
                    current["optimization_rounds"] = rounds + 1
                    current.setdefault("optimization_history", []).append({
                        "version_id": published,
                        "candidate_version": new_version,
                        "parent_version_id": version.id,
                        "package_hash": package_hash,
                        "skill_hash": OPTIMIZATION_SKILL["hash"],
                        "plan_call_id": c_audit.get("call_id"),
                        "candidate_call_id": b_audit.get("call_id"),
                        "full_recheck": False,
                        "network_sources": len(network_trace.get("receipts", [])),
                        "professional_progress": "not_run",
                        "new_deterministic_regressions": False,
                        "status": "rejected",
                    })
                    obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
                return True
            _stop(run_id, worker, epoch, "correctness_only_repair_failed")
            return False
        _stop(run_id, worker, epoch, "regression_or_unresolved")
        return False
    from .repair_directives import directive_recheck_results
    if correctness_only:
        directive_results = directive_recheck_results(
            package, checked, new_units,
            {row["finding_id"] for row in resolved_old},
        )
        if any(row.get("result") != "satisfied" for row in directive_results):
            write_ledger("blocked", {row["finding_id"] for row in resolved_old})
            _stop(run_id, worker, epoch, "REPAIR_DIRECTIVE_ACCEPTANCE_UNSATISFIED")
            return False
        package_hash = digest(package)
        changed_ids = {
            b["unit_id"] for a, b in zip(units, new_units)
            if unit_content_identity(a) != unit_content_identity(b)
        }
        checked.setdefault("findings", []).extend(resolved_old)
        checked.update(
            semantic_recheck=dependency_closure(new_units, changed_ids),
            skipped_checks=[
                row["item_id"] for row in checked.get("checks", [])
                if row.get("result") == "not_run"
            ],
            repair_correctness="not_calibrated",
            optimization_skill=OPTIMIZATION_SKILL,
            optimization_package_hash=package_hash,
            network_trace=network_trace,
            directive_recheck=directive_results,
            _version_id=new_version,
        )
        published = publish(
            run_id, worker, epoch, new_units, checked,
            f"{run_id}:correctness-repair:{rounds}:publish",
            base_version=version.id,
        )
        with write() as db:
            obj = job(db, run_id)
            fence(obj, worker, epoch)
            current = load(obj.state_json)
            current["best_so_far_version"] = published
            current["optimization_rounds"] = rounds + 1
            current.setdefault("optimization_history", []).append({
                "version_id": published,
                "parent_version_id": version.id,
                "package_hash": package_hash,
                "skill_hash": OPTIMIZATION_SKILL["hash"],
                "plan_call_id": c_audit.get("call_id"),
                "candidate_call_id": b_audit.get("call_id"),
                "full_recheck": False,
                "network_sources": len(network_trace.get("receipts", [])),
                "professional_progress": "not_run",
                "new_deterministic_regressions": False,
            })
            obj.state_json, obj.state_revision = dump(current), obj.state_revision + 1
        write_ledger("resolved", {row["finding_id"] for row in resolved_old})
        _stop(run_id, worker, epoch, "correctness_only_completed")
        return False
    acceptance = verify(run_id, worker, epoch, state, package, new_units, provider_factory,
                        network_trace=network_trace, recheck=checked, candidate_citations=citations,
                        prior_observations=prior_observations, version_id=new_version)
    _persist_artifact(run_id, worker, epoch, acceptance, "optimization_acceptance", acceptance["audit"])
    if acceptance["status"] != "satisfied":
        continued = False
        if state.get("execution_policy", {}).get("criterion_assessment") and prior_observations is None:
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
                    continued = True
        if continued:
            write_ledger("blocked", {row["finding_id"] for row in resolved_old})
            # Best content is unchanged; C receives source-bound rejection
            # feedback on the next bounded round, not a fake acceptance.
            return True
        write_ledger("blocked", {row["finding_id"] for row in resolved_old})
        _stop(run_id, worker, epoch, "optimization_acceptance_unresolved")
        return False
    checked["optimization_acceptance"] = acceptance
    directive_results = directive_recheck_results(
        package, checked, new_units,
        {row["finding_id"] for row in resolved_old},
    )
    if any(row.get("result") != "satisfied" for row in directive_results):
        write_ledger("blocked", {row["finding_id"] for row in resolved_old})
        _stop(run_id, worker, epoch, "REPAIR_DIRECTIVE_ACCEPTANCE_UNSATISFIED")
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
    write_ledger("resolved", {row["finding_id"] for row in resolved_old})
    from .optimization import optimization_trigger
    next_trigger = optimization_trigger(
        state["check_plan"], checked, units=new_units, version_id=new_version
    )
    if next_trigger["status"] == "final":
        _stop(run_id, worker, epoch, "optimization_completed_uncalibrated")
        return False
    if next_trigger["status"] in {"incomplete", "unactionable"}:
        _stop(run_id, worker, epoch,
              "QUALITY_CHECK_INCOMPLETE" if next_trigger["status"] == "incomplete"
              else "ACTIONABLE_FINDING_REQUIRED", next_trigger)
        return False
    return True

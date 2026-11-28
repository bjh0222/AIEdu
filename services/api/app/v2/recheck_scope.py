from __future__ import annotations

from copy import deepcopy

from .contracts import DomainError, digest


def _result_for_unit_rows(rows):
    if not rows:
        return "undecidable"
    results = {row.get("result") for row in rows if isinstance(row, dict)}
    if "violated" in results:
        return "violated"
    if "undecidable" in results or "not_run" in results:
        return "undecidable"
    if results and results <= {"satisfied", "not_applicable"}:
        return "not_applicable" if results == {"not_applicable"} else "satisfied"
    return "undecidable"


def _merge_rows(base_rows, new_rows, affected):
    combined = [row for row in (base_rows or [])
                if isinstance(row, dict) and row.get("unit_id") not in affected]
    combined.extend(row for row in (new_rows or []) if isinstance(row, dict))
    return combined


def merge_reports(base, deterministic, incremental, affected):
    result = deepcopy(deterministic)
    base_checks = {row["item_id"]: row for row in base.get("checks", [])}
    new_checks = {row["item_id"]: row for row in incremental.get("checks", [])}
    checks = []
    for item in deterministic.get("check_items", []):
        item_id = item["item_id"]
        if item.get("decision_type") == "deterministic":
            row = next((value for value in deterministic.get("checks", [])
                        if value.get("item_id") == item_id), None)
            if row:
                checks.append(row)
            continue
        prior, current = base_checks.get(item_id), new_checks.get(item_id)
        if current and current.get("unit_results"):
            unit_results = _merge_rows(
                (prior or {}).get("unit_results", []),
                current.get("unit_results", []),
                affected,
            )
            merged = deepcopy(current)
            merged["unit_results"] = unit_results
            merged["result"] = _result_for_unit_rows(unit_results)
            checks.append(merged)
        elif current:
            checks.append(deepcopy(current))
        elif prior:
            checks.append(deepcopy(prior))
    findings = {
        row["normalized_claim_hash"]: deepcopy(row)
        for row in base.get("findings", [])
        if not isinstance(row.get("location"), dict)
        or row.get("location", {}).get("unit_id") not in affected
    }
    for row in incremental.get("findings", []):
        findings.setdefault(row["normalized_claim_hash"], deepcopy(row))
    from .quality import reduce_quality
    quality_profile = (
        incremental.get("quality_profile")
        or deterministic.get("quality_profile")
        or ((incremental.get("item_correctness") or {}).get("quality_profile"))
        or base.get("quality_profile")
    )
    reduced = reduce_quality(
        {"check_items": deterministic.get("check_items", []),
         "check_plan_hash": deterministic.get("check_plan_hash"),
         "quality_layers": deterministic.get("quality_layers"),
         "quality_profile": deepcopy(quality_profile)},
        checks,
        list(findings.values()),
        calibration=incremental.get("calibration_status", "not_calibrated"),
        repair_policy=incremental.get("repair_policy"),
    )
    result.update(reduced)
    for key in ("automated_assessment", "diagnostic_review", "bound_profile_review",
                "difficulty_review", "item_quality"):
        base_value, new_value = base.get(key), incremental.get(key)
        if not isinstance(base_value, dict) or not isinstance(new_value, dict):
            result[key] = deepcopy(new_value if new_value is not None else base_value)
            continue
        merged = deepcopy(base_value)
        for row_key in ("dimensions", "criteria", "items", "reviews"):
            if row_key in base_value or row_key in new_value:
                merged[row_key] = _merge_rows(base_value.get(row_key, []),
                                              new_value.get(row_key, []), affected)
        result[key] = merged
    result.update(
        quality_preflight=deterministic.get("quality_preflight"),
        material_coverage=deterministic.get("material_coverage"),
        calibration_scope=deterministic.get("calibration_scope"),
        item_correctness=deepcopy(incremental.get("item_correctness")),
        quality_profile=deepcopy(quality_profile),
        recheck_scope={
            "method_version": "scope-aware-recheck.v1",
            "changed_unit_ids": sorted(affected),
            "reused_check_rows": sorted(
                item_id for item_id, current in new_checks.items()
                if item_id in base_checks and current.get("unit_results")
            ),
            "rerun_check_rows": sorted(
                item_id for item_id, current in new_checks.items()
                if item_id not in base_checks or current.get("unit_results")
            ),
            "deterministic_first": True,
            "full_semantic_rerun": False,
        },
        evaluation_phase="completed",
    )
    result["recheck_scope"]["scope_hash"] = digest({
        "affected": sorted(affected),
        "base_quality": base.get("quality_hash"),
        "incremental": incremental.get("quality_hash"),
    })
    return result


def evaluate_candidate(run_id, worker, epoch, state, base_units, new_units, base_quality,
                       package, version_id, provider_factory=None):
    if not state.get("research_strategy") and not package.get("directives"):
        from .quality_suite import evaluate
        return evaluate(run_id, worker, epoch, state, new_units, version_id,
                        provider_factory)
    from .optimization import unit_content_identity
    from .quality import check_snapshot, dependency_closure
    from .repair_directives import deterministic_results

    changed = {
        old["unit_id"]
        for old, new in zip(base_units, new_units)
        if unit_content_identity(old) != unit_content_identity(new)
    }
    closure = dependency_closure(base_units, changed)
    affected = set(closure["unit_ids"])
    aggregate_bindings = {"criterion_aggregate.v1", "diagnostic_profile.v1"}
    from .quality_suite import quality_profile_is_correctness_only
    profile_id = "correctness_only.v1" if quality_profile_is_correctness_only(state) else None
    has_aggregate = any(
        item.get("verifier_binding") in aggregate_bindings
        for item in state.get("check_plan", {}).get("check_items", [])
    )
    if profile_id != "correctness_only.v1" and has_aggregate:
        closure["full_semantic_rerun"] = True
        closure["full_semantic_reason"] = "aggregate_checks_require_complete_scope"
    directives = package.get("directives", [])
    deterministic = deterministic_results(directives, new_units)
    failed = [row for row in deterministic if row["result"] != "satisfied"]
    if failed:
        raise DomainError(
            "REPAIR_DETERMINISTIC_ASSERTION_FAILED",
            422,
            changed_unit_ids=sorted(changed),
            dependency_closure=closure,
            directives=failed,
        )
    if closure.get("full_semantic_rerun"):
        from .quality_suite import evaluate
        checked = evaluate(run_id, worker, epoch, state, new_units, version_id,
                           provider_factory, _l4=False)
        checked["recheck_scope"] = {
            "method_version": "scope-aware-recheck.v1",
            "changed_unit_ids": sorted(changed),
            "dependency_closure": closure,
            "deterministic_first": True,
            "full_semantic_rerun": True,
            "directive_deterministic_results": deterministic,
        }
        return checked
    deterministic_report = check_snapshot(state, new_units, version_id)
    deterministic_report["check_items"] = deepcopy(
        state.get("check_plan", {}).get("check_items", [])
    )
    deterministic_report["quality_layers"] = deepcopy(
        state.get("check_plan", {}).get("quality_layers")
    )
    affected_units = [row for row in new_units if row["unit_id"] in affected]
    from .quality_suite import evaluate
    incremental = evaluate(run_id, worker, epoch, state, affected_units, version_id,
                           provider_factory, _l4=False)
    merged = merge_reports(base_quality, deterministic_report, incremental, affected)
    merged["recheck_scope"].update(
        changed_unit_ids=sorted(changed),
        dependency_closure=closure,
        directive_deterministic_results=deterministic,
    )
    return merged

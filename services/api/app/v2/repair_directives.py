from __future__ import annotations

from copy import deepcopy

from .contracts import DomainError, digest, sha

VERSION = "repair-directive.v1"
LEDGER_VERSION = "repair-ledger.v1"
LAYERS = {"correctness", "completeness", "quality_specific"}
DETERMINISTIC_METHODS = {"maximum_codepoints", "required_literals"}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _text_list(value, field, *, nonempty=True):
    if (not isinstance(value, list) or (nonempty and not value)
            or any(not _text(row) for row in value)):
        raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field=field)


def _normalize_test(test, scope_ids):
    if not isinstance(test, dict):
        raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="acceptance_assertion.deterministic_test")
    method = test.get("method")
    if method not in DETERMINISTIC_METHODS:
        raise DomainError("REPAIR_DIRECTIVE_DETERMINISTIC_METHOD_INVALID", 422)
    unit_ids = test.get("unit_ids")
    _text_list(unit_ids, "acceptance_assertion.deterministic_test.unit_ids")
    if not set(unit_ids) <= set(scope_ids):
        raise DomainError("REPAIR_DIRECTIVE_SCOPE_VIOLATION", 422,
                          field="acceptance_assertion.deterministic_test.unit_ids")
    result = {"method": method, "unit_ids": list(dict.fromkeys(unit_ids))}
    if method == "maximum_codepoints":
        limit = test.get("limit")
        if type(limit) is not int or limit < 1:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.deterministic_test.limit")
        result["limit"] = limit
        if set(test) - {"method", "unit_ids", "limit"}:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.deterministic_test")
    else:
        literals = test.get("literals")
        _text_list(literals, "acceptance_assertion.deterministic_test.literals")
        result["literals"] = literals
        if set(test) - {"method", "unit_ids", "literals"}:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.deterministic_test")
    return result


def _normalize_criterion(check, acceptance_items):
    if not isinstance(check, dict):
        raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="acceptance_assertion.criterion_check")
    kind = check.get("kind")
    if kind == "criterion":
        if set(check) != {"kind", "check_id", "criterion_hash", "pass_condition"}:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.criterion_check")
        if not all(_text(check.get(key)) for key in ("check_id", "criterion_hash", "pass_condition")):
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.criterion_check")
        return deepcopy(check)
    if kind == "acceptance_item":
        if set(check) != {"kind", "item_id"} or not _text(check.get("item_id")):
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422,
                              field="acceptance_assertion.criterion_check")
        if check["item_id"] not in acceptance_items:
            raise DomainError("REPAIR_DIRECTIVE_ACCEPTANCE_UNBOUND", 422,
                              item_id=check["item_id"])
        return deepcopy(check)
    raise DomainError("REPAIR_DIRECTIVE_ACCEPTANCE_INVALID", 422)


def normalize_directives(package):
    directives = package.get("directives")
    if not directives:
        return deepcopy(package), []
    if not isinstance(directives, list):
        raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="directives")
    finding_rows = {row["finding_id"]: row for row in package.get("confirmed_findings", [])}
    findings = set(finding_rows)
    scope_ids = package.get("scope", {}).get("unit_ids", [])
    allowed_modes = set(package.get("scope", {}).get("allowed_change_modes", []))
    acceptance_items = {row.get("item_id") for row in package.get("acceptance_criteria", [])}
    normalized, finding_ids, directive_ids = [], set(), set()
    for raw in directives:
        if not isinstance(raw, dict) or set(raw) != {
                "finding_id", "layer", "target", "change_mode", "required_change",
                "acceptance_assertion", "forbidden_changes"}:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="directives")
        finding_id = raw.get("finding_id")
        if not _text(finding_id) or finding_id not in findings or finding_id in finding_ids:
            raise DomainError("REPAIR_DIRECTIVE_FINDING_UNBOUND", 422, finding_id=finding_id)
        if raw.get("layer") not in LAYERS:
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="layer")
        original_finding = finding_rows[finding_id]
        if (original_finding.get("quality_layer")
                and original_finding["quality_layer"] != raw["layer"]):
            raise DomainError("REPAIR_DIRECTIVE_LAYER_MISMATCH", 422, finding_id=finding_id)
        target = raw.get("target")
        if (not isinstance(target, dict) or set(target) != {"unit_id", "item_ordinal", "location_hash"}
                or target.get("unit_id") not in scope_ids
                or (target.get("item_ordinal") is not None
                    and (type(target.get("item_ordinal")) is not int or target["item_ordinal"] < 1))
                or (target.get("location_hash") is not None and not _text(target.get("location_hash")))):
            raise DomainError("REPAIR_DIRECTIVE_TARGET_INVALID", 422, finding_id=finding_id)
        finding_unit = (original_finding.get("location") or {}).get("unit_id")
        if finding_unit and target["unit_id"] != finding_unit:
            raise DomainError("REPAIR_DIRECTIVE_TARGET_INVALID", 422, finding_id=finding_id)
        if raw.get("change_mode") not in allowed_modes:
            raise DomainError("REPAIR_DIRECTIVE_CHANGE_MODE_INVALID", 422, finding_id=finding_id)
        if not _text(raw.get("required_change")):
            raise DomainError("REPAIR_DIRECTIVE_INVALID", 422, field="required_change")
        forbidden = raw.get("forbidden_changes")
        _text_list(forbidden, "forbidden_changes")
        assertion = raw.get("acceptance_assertion")
        if not isinstance(assertion, dict) or set(assertion) != {"deterministic_test", "criterion_check"}:
            raise DomainError("REPAIR_DIRECTIVE_ACCEPTANCE_INVALID", 422, finding_id=finding_id)
        deterministic = assertion.get("deterministic_test")
        criterion = assertion.get("criterion_check")
        if deterministic is None and criterion is None:
            raise DomainError("REPAIR_DIRECTIVE_ACCEPTANCE_INVALID", 422, finding_id=finding_id)
        normalized_assertion = {
            "deterministic_test": _normalize_test(deterministic, scope_ids) if deterministic is not None else None,
            "criterion_check": _normalize_criterion(criterion, acceptance_items) if criterion is not None else None,
        }
        row = {
            "finding_id": finding_id,
            "layer": raw["layer"],
            "target": deepcopy(target),
            "change_mode": raw["change_mode"],
            "required_change": raw["required_change"],
            "acceptance_assertion": normalized_assertion,
            "forbidden_changes": list(dict.fromkeys(forbidden)),
        }
        directive_id = "rd:" + digest({"method_version": VERSION, "directive": row})[:24]
        if directive_id in directive_ids:
            raise DomainError("REPAIR_DIRECTIVE_DUPLICATE", 422)
        finding_ids.add(finding_id)
        directive_ids.add(directive_id)
        normalized.append({
            **row,
            "directive_id": directive_id,
            "directive_hash": digest(row),
            "method_version": VERSION,
        })
    if finding_ids != findings:
        raise DomainError("REPAIR_DIRECTIVE_COVERAGE_MISMATCH", 422,
                          missing=sorted(findings - finding_ids),
                          extra=sorted(finding_ids - findings))
    result = deepcopy(package)
    result["directives"] = normalized
    return result, normalized


def deterministic_results(directives, units):
    bodies = {unit["unit_id"]: unit.get("body", "") for unit in units}
    rows = []
    for directive in directives:
        test = (directive.get("acceptance_assertion") or {}).get("deterministic_test")
        if test is None:
            continue
        satisfied = True
        details = []
        for unit_id in test["unit_ids"]:
            body = bodies.get(unit_id)
            if body is None:
                satisfied = False
                details.append({"unit_id": unit_id, "result": "violated", "reason": "UNIT_MISSING"})
                continue
            if test["method"] == "maximum_codepoints":
                result = len(body) <= test["limit"]
                details.append({"unit_id": unit_id, "result": "satisfied" if result else "violated",
                                "observed": len(body), "limit": test["limit"]})
            else:
                missing = [literal for literal in test["literals"] if literal not in body]
                result = not missing
                details.append({"unit_id": unit_id, "result": "satisfied" if result else "violated",
                                "missing_literals": missing})
            satisfied = satisfied and result
        rows.append({"directive_id": directive["directive_id"],
                     "finding_id": directive["finding_id"],
                     "result": "satisfied" if satisfied else "violated",
                     "checks": details})
    return rows


def validate_candidate_bindings(candidate, package, changed_ids):
    directives = package.get("directives", [])
    if not directives:
        return
    expected = {row["directive_id"] for row in directives}
    supplied = candidate.get("directive_ids")
    if (not isinstance(supplied, list) or not supplied
            or any(not _text(value) for value in supplied)):
        raise DomainError("REPAIR_DIRECTIVE_BINDING_REQUIRED", 422,
                          field="directive_ids")
    _text_list(supplied, "directive_ids")
    if len(supplied) != len(set(supplied)) or set(supplied) != expected:
        raise DomainError("REPAIR_DIRECTIVE_BINDING_REQUIRED", 422,
                          missing=sorted(expected - set(supplied)),
                          extra=sorted(set(supplied) - expected))
    by_unit = {}
    covered = set()
    for unit in candidate.get("units", []):
        unit_id = unit.get("unit_id")
        values = unit.get("directive_ids")
        if values is None:
            if unit_id in changed_ids:
                raise DomainError("REPAIR_DIRECTIVE_BINDING_REQUIRED", 422, unit_id=unit_id)
            continue
        _text_list(values, "units.directive_ids")
        if len(values) != len(set(values)) or not set(values) <= expected:
            raise DomainError("REPAIR_DIRECTIVE_UNKNOWN", 422, unit_id=unit_id)
        if unit_id not in changed_ids:
            raise DomainError("REPAIR_DIRECTIVE_UNBOUND_CHANGE", 422, unit_id=unit_id)
        by_unit[unit_id] = set(values)
        covered.update(values)
    if covered != expected:
        raise DomainError("REPAIR_DIRECTIVE_BINDING_REQUIRED", 422,
                          missing=sorted(expected - covered))
    for directive in directives:
        target_unit = directive["target"]["unit_id"]
        bound_units = {unit_id for unit_id, values in by_unit.items()
                       if directive["directive_id"] in values}
        if target_unit not in bound_units:
            raise DomainError("REPAIR_DIRECTIVE_SCOPE_VIOLATION", 422,
                              directive_id=directive["directive_id"],
                              unit_id=target_unit)


def build_ledger(package, findings, base_version, base_content_hash):
    directives = package.get("directives", [])
    finding_by_id = {row["finding_id"]: row for row in findings}
    events = [{
        "finding_id": row["finding_id"],
        "directive_id": row["directive_id"],
        "directive_hash": row["directive_hash"],
        "candidate_hash": None,
        "candidate_version": None,
        "recheck_scope": None,
        "recheck_result": "pending",
        "status": "pending",
        "call_ids": [],
        "recorded_at": None,
    } for row in directives]
    ledger = {
        "schema_version": LEDGER_VERSION,
        "base_version": base_version,
        "base_content_hash": base_content_hash,
        "check_plan_hash": package.get("frozen_bindings", {}).get("check_plan_hash"),
        "directives": deepcopy(directives),
        "findings": [{key: deepcopy(value) for key, value in finding_by_id[row["finding_id"]].items()}
                     for row in directives if row["finding_id"] in finding_by_id],
        "events": events,
    }
    ledger["ledger_hash"] = digest(ledger)
    return ledger


def criterion_findings(report, state, units):
    from .quality import finding_layer_enabled
    rows = (report or {}).get("criteria", [])
    items = {row["item_id"]: row for row in state.get("check_plan", {}).get("check_items", [])}
    unit_by_id = {row["unit_id"]: row for row in units}
    result = []
    for row in rows:
        if row.get("processing_status") != "complete" or row.get("result") != "violated":
            continue
        evidence = row.get("evidence")
        if not isinstance(evidence, list):
            evidence = [
                entry
                for clause in row.get("clauses", [])
                if isinstance(clause, dict) and clause.get("result") == "violated"
                for entry in clause.get("evidence", [])
                if isinstance(entry, dict)
            ]
        if not evidence:
            continue
        item = items.get(row.get("check_id"))
        if item is None and isinstance(row.get("check_id"), str):
            if row["check_id"].startswith("A.quality"):
                item = items.get("A.quality_targets")
            elif row["check_id"].startswith("A."):
                item = items.get("A.completeness")
        unit = unit_by_id.get(row.get("unit_id"))
        if item is None or unit is None:
            continue
        candidate = {"item_id": item["item_id"], "quality_layer": item.get("quality_layer"),
                     "hard_baseline": item.get("hard_baseline")}
        if not finding_layer_enabled(state.get("check_plan", {}), candidate):
            continue
        evidence = deepcopy(evidence)
        located = next((entry for entry in evidence if isinstance(entry, dict)
                        and entry.get("unit_id") == unit["unit_id"]), None)
        if located is None:
            body = unit["body"]
            quote_entry = next(
                (entry for entry in evidence
                 if isinstance(entry, dict) and isinstance(entry.get("quote"), str)
                 and entry["quote"] and body.count(entry["quote"]) == 1),
                None,
            )
            if quote_entry is not None:
                quote = quote_entry["quote"]
                start = body.encode().find(quote.encode())
                located = {
                    "unit_id": unit["unit_id"],
                    "start_byte": start,
                    "end_byte": start + len(quote.encode()),
                    "span_hash": sha(quote),
                    "quote": quote,
                }
            else:
                located = {
                    "unit_id": unit["unit_id"],
                    "start_byte": 0,
                    "end_byte": len(body.encode()),
                    "span_hash": unit["content_hash"],
                }
        clause_ids = [clause.get("clause_id") for clause in row.get("clauses", [])
                      if clause.get("result") == "violated"]
        identity = digest({
            "check_id": row["check_id"],
            "criterion_hash": (row.get("criterion") or {}).get("criterion_hash"),
            "unit_id": unit["unit_id"],
            "item_id": row.get("item_id"),
            "clause_ids": clause_ids,
        })
        finding_id = "criterion:" + identity[:24]
        result.append({
            "finding_id": finding_id,
            "item_id": item["item_id"],
            "check_plan_hash": state["check_plan"]["check_plan_hash"],
            "system": "criterion_review",
            "severity_class": item.get("severity_class", "blocker"),
            "quality_layer": item.get("quality_layer"),
            "hard_baseline": bool(item.get("hard_baseline")),
            "location": {
                "unit_id": unit["unit_id"],
                "version_id": report.get("version_id"),
                "unit_hash": unit["content_hash"],
                "start_byte": located.get("start_byte", 0),
                "end_byte": located.get("end_byte", 0),
                "span_hash": located.get("span_hash") or located.get("quote_hash") or identity,
            },
            "claim": "Criterion review returned violated for the frozen criterion.",
            "evidence": evidence,
            "verification_method": "criterion-review.v1",
            "verification_result": "violated",
            "method_version": "criterion-review.v1",
            "tool_raw_output_hash": digest(row),
            "confidence": "model_observation",
            "suggested_fix": None,
            "model_family": None,
            "input_hash": unit["content_hash"],
            "normalized_claim_hash": identity,
            "attempt": 1,
            "status": "confirmed",
            "reviewed_by": "criterion-review.v1",
            "review_evidence": [{"authority": "isolated_criterion_observation_not_professional_certification",
                                 "clause_ids": clause_ids}],
            "context_conflict": False,
            "category": "criterion_observation",
            "acceptance_assertion": {
                "method": ("assessment-input-binding.v1"
                           if item.get("verifier_binding") == "criterion_aggregate.v1"
                           else "criterion-review.v1"),
                "check_id": row["check_id"],
                "criterion_hash": (row.get("criterion") or {}).get("criterion_hash"),
            },
        })
    return result


def difficulty_findings(report, state, units):
    from .quality import finding_layer_enabled

    items = {
        item["item_id"]: item
        for item in state.get("check_plan", {}).get("check_items", [])
        if item.get("item_id", "").startswith("Q5.")
    }
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    result = []
    for item_id, item in items.items():
        if not finding_layer_enabled(state.get("check_plan", {}), item):
            continue
        role_units = [
            unit for unit in units
            if unit.get("artifact_role") in (item.get("scope") or {}).get("roles", [])
        ]
        violated = [
            row for row in (report or {}).get("items", [])
            if isinstance(row, dict) and row.get("unit_id") in {u["unit_id"] for u in role_units}
            and row.get("processing_status") == "complete" and row.get("result") == "violated"
        ]
        if not violated:
            continue
        row = violated[0]
        unit = unit_by_id[row["unit_id"]]
        evidence = []
        for axis in (row.get("dimensions") or {}).values():
            if isinstance(axis, list):
                evidence.extend(
                    entry for entry in axis
                    if isinstance(entry, dict) and entry.get("quote")
                )
        located = next((entry for entry in evidence if entry.get("unit_id") == unit["unit_id"]
                        and isinstance(entry.get("start_byte"), int)), None)
        start = located["start_byte"] if located else 0
        end = located["end_byte"] if located else len(unit["body"].encode())
        identity = digest({
            "check_id": item_id,
            "unit_id": unit["unit_id"],
            "item_id": row["item_id"],
            "item_fields_hash": row.get("item_fields_hash"),
        })
        result.append({
            "finding_id": "difficulty:" + identity[:24],
            "item_id": item_id,
            "check_plan_hash": state["check_plan"]["check_plan_hash"],
            "system": "difficulty_review",
            "severity_class": item.get("severity_class", "blocker"),
            "quality_layer": item.get("quality_layer"),
            "hard_baseline": bool(item.get("hard_baseline")),
            "location": {
                "unit_id": unit["unit_id"],
                "version_id": (report or {}).get("version_id"),
                "unit_hash": unit["content_hash"],
                "start_byte": start,
                "end_byte": end,
                "span_hash": located.get("quote_hash") if located else unit["content_hash"],
            },
            "claim": "The frozen difficulty requirement was not met by the fixed candidate.",
            "evidence": deepcopy(row),
            "verification_method": "difficulty_review.v1",
            "verification_result": "violated",
            "method_version": "difficulty_review.v1",
            "tool_raw_output_hash": digest(row),
            "confidence": "model_observation",
            "suggested_fix": None,
            "model_family": None,
            "input_hash": unit["content_hash"],
            "normalized_claim_hash": identity,
            "attempt": 1,
            "status": "confirmed",
            "reviewed_by": "difficulty_review.v1",
            "review_evidence": [{
                "authority": "wrapper_counted_operations_not_professional_difficulty",
                "source_item_id": row.get("item_id"),
            }],
            "context_conflict": False,
            "category": "difficulty_observation",
            "acceptance_assertion": {
                "method": "difficulty_review.v1",
                "check_id": item_id,
            },
        })
    return result


def diagnostic_profile_findings(report, state, units):
    from .quality import finding_layer_enabled

    item = next(
        (row for row in state.get("check_plan", {}).get("check_items", [])
         if row.get("item_id") == "P.import_profile"),
        None,
    )
    if item is None or not finding_layer_enabled(state.get("check_plan", {}), item):
        return []
    unit_by_id = {unit["unit_id"]: unit for unit in units}
    result = []
    for row in (report or {}).get("dimensions", []):
        if not isinstance(row, dict) or row.get("result") != "violated":
            continue
        unit = unit_by_id.get(row.get("unit_id"))
        evidence = [
            entry for entry in row.get("evidence", [])
            if isinstance(entry, dict) and entry.get("unit_id") == row.get("unit_id")
            and isinstance(entry.get("start_byte"), int)
        ]
        if unit is None or not evidence:
            continue
        located = evidence[0]
        identity = digest({
            "check_id": item["item_id"],
            "unit_id": unit["unit_id"],
            "dimension_id": row.get("dimension_id"),
            "binding_hash": row.get("binding_hash"),
        })
        result.append({
            "finding_id": "profile:" + identity[:24],
            "item_id": item["item_id"],
            "check_plan_hash": state["check_plan"]["check_plan_hash"],
            "system": "import_profile",
            "severity_class": item.get("severity_class", "blocker"),
            "quality_layer": item.get("quality_layer"),
            "hard_baseline": bool(item.get("hard_baseline")),
            "location": {
                "unit_id": unit["unit_id"],
                "version_id": (report or {}).get("version_id"),
                "unit_hash": unit["content_hash"],
                "start_byte": located["start_byte"],
                "end_byte": located["end_byte"],
                "span_hash": located.get("quote_hash") or unit["content_hash"],
            },
            "claim": "A bound imported profile observation was not satisfied.",
            "evidence": deepcopy(row),
            "verification_method": "bound-rubric-model-review.v2",
            "verification_result": "violated",
            "method_version": "bound-rubric-model-review.v2",
            "tool_raw_output_hash": digest(row),
            "confidence": "model_observation",
            "suggested_fix": None,
            "model_family": None,
            "input_hash": unit["content_hash"],
            "normalized_claim_hash": identity,
            "attempt": 1,
            "status": "confirmed",
            "reviewed_by": "bound-rubric-model-review.v2",
            "review_evidence": [{
                "authority": "same_family_profile_observation_not_professional_certification",
                "dimension_id": row.get("dimension_id"),
            }],
            "context_conflict": False,
            "category": "profile_observation",
            "acceptance_assertion": {
                "method": "bound-rubric-model-review.v2",
                "check_id": item["item_id"],
            },
        })
    return result


def update_ledger(ledger, *, candidate_hash, candidate_version, recheck_scope, results,
                  status, call_ids):
    result_by_finding = {row["finding_id"]: row for row in results}
    updated = deepcopy(ledger)
    for event in updated["events"]:
        result = result_by_finding.get(event["finding_id"], {})
        event.update({
            "candidate_hash": candidate_hash,
            "candidate_version": candidate_version,
            "recheck_scope": deepcopy(recheck_scope),
            "recheck_result": result.get("result", "pending"),
            "status": status.get(event["finding_id"], "blocked") if isinstance(status, dict) else status,
            "call_ids": list(dict.fromkeys(call_ids)),
        })
    updated.pop("ledger_hash", None)
    updated["ledger_hash"] = digest(updated)
    return updated


def directive_recheck_results(package, checked, new_units, resolved_finding_ids):
    rows = deterministic_results(package.get("directives", []), new_units)
    by_id = {row["directive_id"]: row for row in rows}
    criteria = (checked.get("automated_assessment") or {}).get("criteria", [])
    acceptance = (checked.get("optimization_acceptance") or {}).get("criteria", [])
    for directive in package.get("directives", []):
        current = by_id.get(directive["directive_id"])
        check = (directive.get("acceptance_assertion") or {}).get("criterion_check")
        if not check:
            continue
        if check["kind"] == "acceptance_item":
            row = next((entry for entry in acceptance if entry.get("item_id") == check["item_id"]), None)
            criterion_result = row.get("result") if row else "undecidable"
        else:
            row = next((entry for entry in criteria
                        if entry.get("check_id") == check["check_id"]
                        and (entry.get("criterion") or {}).get("criterion_hash") == check["criterion_hash"]), None)
            criterion_result = row.get("result") if row else "undecidable"
        deterministic_result = current.get("result") if current else None
        if deterministic_result is None:
            result = criterion_result
        elif criterion_result == "violated" or deterministic_result == "violated":
            result = "violated"
        elif criterion_result == "satisfied" and deterministic_result == "satisfied":
            result = "satisfied"
        else:
            result = "undecidable"
        by_id[directive["directive_id"]] = {
            "directive_id": directive["directive_id"],
            "finding_id": directive["finding_id"],
            "result": result,
            "deterministic_result": deterministic_result,
            "criterion_result": criterion_result,
        }
    for directive in package.get("directives", []):
        if directive["directive_id"] not in by_id:
            by_id[directive["directive_id"]] = {
                "directive_id": directive["directive_id"],
                "finding_id": directive["finding_id"],
                "result": "satisfied" if directive["finding_id"] in resolved_finding_ids else "undecidable",
            }
    return list(by_id.values())

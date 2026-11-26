"""Wrapper-owned assessment identity and execution checks, not certification.

Legacy reports without these receipts remain historical evidence; never attach
a new binding to an old pass label. Original provider requests are unchanged.
"""
from .contracts import digest

VERSION = "assessment-input-binding.v1"
UNIT_KEYS = ("unit_id", "title", "artifact_role", "body", "content_hash", "items", "output_design",
             "teaching_arrangement", "subject_bindings", "format_contract", "source_sections", "injected_chunk_ids")
RESULTS = ("satisfied", "violated", "undecidable", "not_applicable")


def input_binding(state, units, version_id):
    # Recompute hashes from content, not caller-supplied hash labels. The full
    # typed answers are included even when rendered bodies hide final answers.
    return {"method_version": VERSION, "version_id": version_id,
        "units_hash": digest([{k: u[k] for k in UNIT_KEYS if k in u} for u in units]),
        "context_hash": digest(state.get("context", {})), "plan_hash": digest(state.get("plan", {})),
        "check_plan_hash": digest(state.get("check_plan", {}))}


def consume(report, kind, state, units, version_id):
    """Recompute expected coverage and reduction; never accept result alone."""
    errors = []
    if not isinstance(report, dict):
        return "undecidable", ["assessment_report_required"]
    if not isinstance(version_id, str) or not version_id:
        return "undecidable", ["candidate_version_required"]
    if report.get("version_id") != version_id or report.get("input_binding") != input_binding(state, units, version_id):
        return "undecidable", ["assessment_input_unbound_or_stale"]
    try:
        if kind == "difficulty_review":
            outcome, expected, rows, complete = _difficulty(report, state, units)
        elif kind == "automated_assessment":
            outcome, expected, rows, complete = _criteria(report, state, units)
        else:
            return "undecidable", ["assessment_kind_unknown"]
        coverage = report.get("coverage")
        if not isinstance(coverage, dict) or any(type(coverage.get(key)) is not int or coverage[key] != count for key, count in {
            "expected": expected, "recorded": len(rows), "complete_observations": complete,
            "decided": sum(r["result"] != "undecidable" for r in rows)}.items()):
            errors.append("assessment_coverage_mismatch")
        if report.get("status") != "completed" or len(rows) != expected or complete != expected:
            errors.append("assessment_execution_incomplete")
        if kind == "difficulty_review" and (report.get("processing_status") != "complete"
                or type(coverage.get("invalid_response_entries")) is not int
                or coverage.get("invalid_response_entries") != 0 or report.get("protocol_errors")):
            errors.append("assessment_response_contract_incomplete")
        if report.get("result") != outcome:
            errors.append("assessment_aggregate_mismatch")
        # A located negative must not be deleted just because another check is
        # incomplete. It is still not an identity-independent professional truth.
        return ("violated" if outcome == "violated" else "undecidable" if errors else outcome), errors
    except (KeyError, TypeError, ValueError, AttributeError):
        return "undecidable", ["assessment_rows_malformed_or_unbound"]


def criterion_check_results(report, state, units, version_id):
    """Turn source-bound criterion rows back into plan-level checks.

    ``consume`` revalidates every row, span and aggregate. On any binding or
    coverage fault this returns an explicit undecidable check rather than the
    misleading generic "no verifier" marker.
    """
    from .checker import applies
    from .quality import disabled_layer_check, frozen_quality_layers, layer_enabled
    layers = frozen_quality_layers(state)
    criterion_items = {item["item_id"]: item for item in state.get("check_plan", {}).get("check_items", [])
                       if item.get("verifier_binding") == "criterion_review.v1"}
    check_ids = list(criterion_items)
    disabled_ids = {item_id for item_id, item in criterion_items.items() if not layer_enabled(item, layers)}
    applicable = {
        item_id: [unit["unit_id"] for unit in units
                  if applies(item, unit, state.get("context", {}))]
        for item_id, item in criterion_items.items()
    }
    reduced = {
        item_id: {
            "item_id": item_id,
            "result": "not_applicable",
            "reason": "no_units_in_declared_domain_scene_role_or_question_form_scope",
            "method_version": "assessment-input-binding.v1",
            "matched_unit_ids": [],
            "quality_matrix_hash": (report.get("quality_preflight") or {}).get("matrix_hash")
            if isinstance(report, dict) else None,
        }
        for item_id, matched in applicable.items() if not matched
        and item_id not in disabled_ids
    }
    outcome, errors = consume(report, "automated_assessment", state, units, version_id)
    if errors or outcome not in RESULTS:
        reduced.update({
            item_id: {"item_id": item_id, "result": "undecidable",
                      "reason": "ASSESSMENT_BINDING_INCOMPLETE", "binding_errors": errors,
                      "method_version": "assessment-input-binding.v1"}
            for item_id in check_ids if applicable[item_id]
        })
        return reduced
    grouped = {}
    for row in report.get("criteria", []):
        if isinstance(row, dict) and row.get("check_id") in check_ids:
            grouped.setdefault(row["check_id"], []).append(row.get("result"))
    for item_id in check_ids:
        if item_id in disabled_ids:
            # Requests_for_unit never issued this row; keep the honest
            # policy-skipped state instead of a misleading missing-row unknown.
            reduced[item_id] = disabled_layer_check(criterion_items[item_id])
            continue
        if not applicable[item_id]:
            continue
        results = grouped.get(item_id, [])
        if not results:
            reduced[item_id] = {"item_id": item_id, "result": "undecidable",
                                "reason": "ASSESSMENT_ROWS_MISSING", "method_version": "assessment-input-binding.v1"}
        elif "violated" in results:
            result = "violated"
        elif "undecidable" in results:
            result = "undecidable"
        elif results and all(value == "not_applicable" for value in results):
            result = "not_applicable"
        else:
            result = "satisfied"
        reduced[item_id] = {"item_id": item_id, "result": result,
                            "method_version": "assessment-input-binding.v1",
                            "matched_unit_ids": applicable[item_id]}
    return reduced


def _difficulty(report, state, units):
    from .difficulty_review import bind_item
    from .quality_policy import difficulty_applicability
    binding_units = list(units)
    expected_applicability = difficulty_applicability(state.get("context", {}), binding_units)
    if report.get("result") == "not_applicable":
        if (expected_applicability["decision"] != "skip"
                or report.get("applicability") != expected_applicability
                or report.get("reason") != expected_applicability["reason"]
                or report.get("items") != []
                or report.get("processing_status") != "complete"):
            raise ValueError("not_applicable_lacks_recomputed_scope")
        return "not_applicable", 0, [], 0
    applicable_ids = set(expected_applicability["applicable_unit_ids"])
    units = [unit for unit in binding_units if unit.get("unit_id") in applicable_ids]
    rows = report["items"]
    if not isinstance(rows, list):
        raise ValueError("rows required")
    expected = [(unit, item) for unit in units for item in unit.get("items", [])]
    reduced, complete = [], 0
    for unit, item in expected:
        matches = [r for r in rows if isinstance(r, dict) and r.get("unit_id") == unit["unit_id"] and r.get("item_id") == item["item_id"]]
        if len(matches) != 1:
            raise ValueError("exactly one source-bound row required")
        row = matches[0]
        rebound = bind_item({**row["dimensions"], "computation_load": row["computation_load"]}, item, unit, {})
        if any(row.get(k) != rebound[k] for k in ("ordinal", "item_fields_hash", "target", "counts", "comparisons", "result",
                                                   "dimensions", "computation_load", "input_binding_complete", "processing_status")):
            raise ValueError("difficulty evidence no longer binds")
        complete += row["processing_status"] == "complete"
        reduced.append(row["result"])
    outcome = ("violated" if "violated" in reduced else "undecidable" if "undecidable" in reduced else
               "satisfied" if reduced else "not_applicable")
    return outcome, len(expected), rows, complete


def _criteria(report, state, units):
    from .criterion_review import bound_quote, requests_for_unit, summarize
    from .criterion_protocol import _rating
    rows = report["criteria"]
    if not isinstance(rows, list):
        raise ValueError("rows required")
    expected, complete = [], 0
    for unit in units:
        fields = {"unit:body": unit["body"]}
        for item in unit.get("items", []):
            fields.update({"item:" + item["item_id"] + ":" + name: value for name, value in {
                "stem_markdown": item["stem_markdown"], "answer.final": item["answer"]["final"],
                "answer.reasoning": item["answer"]["reasoning"]}.items()})
        requests, _ = requests_for_unit(state, unit)
        for request in requests:
            expected.append(request)
            matches = [r for r in rows if isinstance(r, dict) and r.get("unit_id") == unit["unit_id"]
                and r.get("check_id") == request["check_id"] and r.get("item_id") == request["item_id"]]
            if len(matches) != 1:
                raise ValueError("exactly one expected row required")
            row = matches[0]
            if (row.get("ordinal") != request["ordinal"] or row.get("criterion_hash") != request["criterion"]["criterion_hash"]
                    or row.get("result") not in RESULTS):
                raise ValueError("criterion binding mismatch")
            clauses = row["clauses"]
            if len(clauses) != len(request["criterion"]["clauses"]) or {c["clause_id"] for c in clauses} != set(request["criterion"]["clauses"]):
                raise ValueError("criterion clauses mismatch")
            for clause in clauses:
                if clause.get("result") not in RESULTS:
                    raise ValueError("criterion result invalid")
                evidence = clause["evidence"]
                actual = [e for e in evidence if e.get("field") in fields]
                if not actual or any(bound_quote(e, fields) != e for e in actual):
                    raise ValueError("criterion content spans unbound")
                if request.get("item_id"):
                    own = "item:" + request["item_id"] + ":"
                    relational = request["check_id"].startswith("Q4.") and clause["clause_id"] == "transfer"
                    if (not any(e["field"].startswith(own) for e in actual)
                            or clause["result"] == "not_applicable"
                            or not relational and any(not e["field"].startswith(own) for e in actual)):
                        raise ValueError("criterion content scope mismatch")
                    if relational and clause["result"] in ("satisfied", "violated") and not any(
                            e["field"].startswith("item:") and not e["field"].startswith(own) for e in actual):
                        raise ValueError("criterion comparison endpoint missing")
            clause_outcome = ("violated" if any(c["result"] == "violated" for c in clauses) else
                "undecidable" if any(c["result"] == "undecidable" for c in clauses) else
                "not_applicable" if clauses and all(c["result"] == "not_applicable" for c in clauses) else "satisfied")
            rating, valid, row_outcome = _rating(row, request, clause_outcome)
            if not valid or row.get("rating") != rating or row["result"] != row_outcome:
                raise ValueError("criterion row reduction mismatch")
            complete += row.get("processing_status") == "complete" and not row.get("protocol_errors") and all(
                c.get("result") in RESULTS and not c.get("invalid_or_missing_observation", True) for c in clauses)
    reduced = summarize(rows, len(expected), [])
    return reduced["result"], len(expected), rows, complete

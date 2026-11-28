"""Domain-neutral, clause-level comparisons with published professional anchors.

The experiment supplies explicit source/mapping metadata. Nothing here grants
calibration, rewrites a scorer, collapses a total score into labels or changes a
live task. Expected labels are never copied into the content supplied to C.
"""
from __future__ import annotations

from copy import deepcopy

from .contracts import DomainError, digest, sha
from .criterion_review import bound_quote
from .external_calibration import summarize

VERSION = "external-professional-anchor.v1"


def anchor_case(*, domain, task, body, checks, expected, source):
    """Freeze one source response with explicit professional clause mappings.

    A source label describes only its mapped construct, not whole-item quality.
    Missing context belongs in the experiment's exclusions, never in a fabricated
    expert label. Source rights and reading level remain the caller's audit duty.
    """
    required_source = ("url", "sha256", "response_location", "label_location", "label_protocol")
    valid = (isinstance(domain, str) and bool(domain.strip())
             and isinstance(task, str) and bool(task.strip())
             and isinstance(body, str) and bool(body.strip())
             and isinstance(source, dict)
             and all(isinstance(source.get(k), str) and source[k].strip() for k in required_source)
             and len(source["sha256"]) == 64
             and all(c in "0123456789abcdef" for c in source["sha256"])
             and isinstance(checks, list) and bool(checks)
             and isinstance(expected, list) and bool(expected))
    if not valid:
        raise DomainError("EXTERNAL_ANCHOR_CASE_INVALID", 422)
    ids, pairs = set(), set()
    for check in checks:
        if not isinstance(check, dict) or not isinstance(check.get("criterion_contract"), dict):
            raise DomainError("EXTERNAL_ANCHOR_CONTRACT_INVALID", 422)
        contract = check["criterion_contract"]
        check_id = check.get("item_id")
        clauses = contract.get("clauses")
        if (not isinstance(check_id, str) or not check_id.strip() or check_id in ids
                or contract.get("target") != "unit" or contract.get("check_id") != check_id
                or not isinstance(clauses, dict) or not clauses
                or any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
                       for k, v in clauses.items())
                or contract.get("criterion_hash") != digest({k: v for k, v in contract.items() if k != "criterion_hash"})):
            raise DomainError("EXTERNAL_ANCHOR_CONTRACT_INVALID", 422)
        ids.add(check_id)
        pairs.update((check_id, clause) for clause in clauses)
    observed = []
    for label in expected:
        if (not isinstance(label, dict) or label.get("result") not in {"satisfied", "violated"}
                or any(not isinstance(label.get(k), str) or not label[k].strip() for k in ("check_id", "clause_id"))
                or not isinstance(label.get("evidence_location"), str) or not label["evidence_location"].strip()):
            raise DomainError("EXTERNAL_ANCHOR_LABEL_INVALID", 422)
        observed.append((label.get("check_id"), label.get("clause_id")))
    # Complete predeclared mapping: no dropping the difficult clauses after C.
    if len(set(observed)) != len(observed) or set(observed) != pairs:
        raise DomainError("EXTERNAL_ANCHOR_LABEL_COVERAGE_INVALID", 422)
    value = {"version": VERSION, "domain_key": domain, "task": task, "body": body,
             "checks": deepcopy(checks), "expected": deepcopy(expected), "source": deepcopy(source),
             "source_group": sha(task)}
    value["case_hash"] = digest(value)
    return value


def blind_unit(case):
    # Identity uses content only: no score, source sample ID or label-derived hash.
    opaque = "anchor-" + digest([case["domain_key"], case["task"], case["body"]])[:20]
    return {"unit_id": opaque, "title": "Fixed response review", "artifact_role": "tutorial",
            "body": case["body"], "content_hash": sha(case["body"]), "items": []}


def compare(case, report):
    """Compare only exact bound clauses, distinguishing missing from abstention."""
    if (not isinstance(case, dict)
            or case.get("case_hash") != digest({k: v for k, v in case.items() if k != "case_hash"})):
        raise DomainError("EXTERNAL_ANCHOR_CASE_HASH_MISMATCH", 422)
    rows = report.get("criteria", []) if isinstance(report, dict) else []
    rows = rows if isinstance(rows, list) else []
    fields = {"unit:body": case["body"]}
    contracts = {c["item_id"]: c["criterion_contract"] for c in case["checks"]}
    records = []
    for expected in case["expected"]:
        check_id, clause_id = expected["check_id"], expected["clause_id"]
        candidates = [r for r in rows if isinstance(r, dict) and r.get("check_id") == check_id
                      and r.get("unit_id") == blind_unit(case)["unit_id"] and r.get("item_id") is None]
        row = candidates[0] if len(candidates) == 1 else {}
        raw_clauses = row.get("clauses", [])
        clauses = [c for c in raw_clauses if isinstance(c, dict) and c.get("clause_id") == clause_id] if isinstance(raw_clauses, list) else []
        clause = clauses[0] if len(clauses) == 1 else {}
        evidence = clause.get("evidence")
        evidence_valid = isinstance(evidence, list) and bool(evidence) and all(
            isinstance(e, dict) and (bound := bound_quote(e, fields)) is not None
            and all(e.get(k) == v for k, v in bound.items()) for e in evidence)
        valid = (row.get("criterion_hash") == contracts[check_id]["criterion_hash"]
                 and clause.get("invalid_or_missing_observation") is False and evidence_valid
                 and isinstance(clause.get("reason"), str) and len(clause["reason"].strip()) >= 8
                 and clause.get("result") in {"satisfied", "violated", "undecidable"})
        predicted = clause.get("result") if valid else None
        execution = ("valid_abstention" if predicted == "undecidable" else "valid_decision") if valid else "invalid_or_missing_execution"
        records.append({"case_hash": digest([case["case_hash"], check_id, clause_id]),
            "source_case_id": case["source"]["response_location"], "source_group": case["source_group"],
            "domain_key": case["domain_key"], "check_id": check_id, "clause_id": clause_id,
            "expected": expected["result"], "prediction": predicted,
            "agreement": predicted == expected["result"] if execution == "valid_decision" else None,
            "execution_category": execution, "evidence_valid": bool(evidence_valid),
            "criterion_hash": contracts[check_id]["criterion_hash"], "audit": row.get("audit"),
            "observation": clause, "label_evidence_location": expected["evidence_location"]})
    return records


def summary(records, expected_count):
    result = summarize(records, expected_count)
    result.update(method_version=VERSION,
                  scope="explicitly mapped published professional clauses, not full tutorial quality")
    result["limitations"] = [s for s in result["limitations"] if not s.startswith("First-error")]
    result["limitations"].append("Multiple clauses/responses of the same task are clustered, not independent replications.")
    return result

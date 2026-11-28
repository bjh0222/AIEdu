"""Compare public expert process labels with the actual C clause scorer.

This is a scoped calibration experiment, not an automatic certificate. Dataset
labels stay on the comparison side; neither source identity nor expected label
is included in the unit supplied to C. No threshold is invented here.
"""
from __future__ import annotations

import math
import random

from .contracts import DomainError, digest, sha
from .benchmark import wilson

VERSION = "external-process-clause-calibration.v3"
BOOTSTRAP_SEED = 20261122
BOOTSTRAP_ITERATIONS = 2000


def _percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _clustered_bootstrap(records, seed=BOOTSTRAP_SEED, iterations=BOOTSTRAP_ITERATIONS):
    groups = {}
    for record in records:
        group = record.get("source_group")
        key = group if isinstance(group, str) and group.strip() else "__missing__:" + str(record.get("case_hash"))
        groups.setdefault(key, []).append(record)
    keys = sorted(groups)
    agreement, recall, false_positive = [], [], []
    if not keys:
        return {
            "agreement_decided_ci95_clustered": None,
            "error_recall_ci95_clustered": None,
            "false_positive_ci95_clustered": None,
            "clusters": 0,
            "cases": len(records),
        }
    rng = random.Random(seed)
    for _ in range(iterations):
        sampled = []
        for _ in keys:
            sampled.extend(groups[keys[rng.randrange(len(keys))]])
        decided = [
            row for row in sampled
            if row.get("execution_category") == "valid_decision"
            and row.get("prediction") in {"satisfied", "violated"}
            and row.get("evidence_valid") is True
        ]
        negatives = [row for row in decided if row.get("expected") == "violated"]
        positives = [row for row in decided if row.get("expected") == "satisfied"]
        if decided:
            agreement.append(sum(
                row.get("prediction") == row.get("expected") for row in decided
            ) / len(decided))
        if negatives:
            recall.append(sum(
                row.get("prediction") == "violated" for row in negatives
            ) / len(negatives))
        if positives:
            false_positive.append(sum(
                row.get("prediction") == "violated" for row in positives
            ) / len(positives))
    return {
        "agreement_decided_ci95_clustered": [
            _percentile(agreement, 0.025), _percentile(agreement, 0.975)
        ] if agreement else None,
        "error_recall_ci95_clustered": [
            _percentile(recall, 0.025), _percentile(recall, 0.975)
        ] if recall else None,
        "false_positive_ci95_clustered": [
            _percentile(false_positive, 0.025), _percentile(false_positive, 0.975)
        ] if false_positive else None,
        "clusters": len(keys),
        "cases": len(records),
    }


def process_case(row, source):
    """ProcessBench label -1 means no error; nonnegative is first error index."""
    label, steps = row.get("label"), row.get("steps")
    if (type(label) is not int or not isinstance(steps, list) or not steps
            or any(not isinstance(s, str) or not s.strip() for s in steps)
            or label < -1 or label >= len(steps)
            or not isinstance(row.get("problem"), str) or not row["problem"].strip()
            or not all(source.get(k) for k in ("url", "sha256", "label_protocol"))):
        raise DomainError("EXTERNAL_PROCESS_CASE_INVALID", 422)
    value = {"source_case_id": row["id"], "source": dict(source),
        "problem": row["problem"], "steps": list(steps), "expert_first_error": label,
        "expected_process_result": "satisfied" if label == -1 else "violated",
        "label_semantics": "expert_first_error_index_not_final_answer_accuracy",
        "source_final_answer_correct": row.get("final_answer_correct"),
        "source_group": sha(row["problem"]), "purpose": "scoped_external_calibration_probe"}
    value["case_hash"] = digest(value)
    return value


def blind_unit(case):
    """Mechanical formatting only; retain every original question/solution byte."""
    opaque = "probe-" + digest([case["problem"], case["steps"]])[:20]
    reasoning = "\n\n".join(case["steps"])
    body = case["problem"] + "\n\n" + reasoning
    return {"unit_id": opaque, "title": "Mathematical solution review", "artifact_role": "exercise",
        "body": body, "content_hash": sha(body),
        "items": [{"item_id": opaque + "-item", "ordinal": 1,
            "stem_markdown": case["problem"],
            "answer": {"final": case["steps"][-1], "reasoning": reasoning}}]}


def comparison(case, report):
    """Q2.reasoning is the closest mapped construct, not all of Q2 or Q1."""
    from .criterion_review import bound_quote

    raw = report.get("criteria", []) if isinstance(report, dict) else []
    criteria = [r for r in raw if isinstance(r, dict) and r.get("check_id") == "Q2.exercise"] if isinstance(raw, list) else []
    clauses = [c for r in criteria for c in (r.get("clauses") if isinstance(r.get("clauses"), list) else [])
               if isinstance(c, dict) and c.get("clause_id") == "reasoning"]
    clause = clauses[0] if len(criteria) == len(clauses) == 1 else {}
    unit = blind_unit(case)
    item = unit["items"][0]
    fields = {"unit:body": unit["body"], "item:" + item["item_id"] + ":stem_markdown": item["stem_markdown"],
        **{"item:" + item["item_id"] + ":answer." + k: item["answer"][k] for k in ("final", "reasoning")}}
    evidence = clause.get("evidence")
    # The mapped observation must retain the actual wrapper-owned byte binding.
    # An absent/invalid model result is execution failure, NOT a scored abstention.
    evidence_valid = isinstance(evidence, list) and bool(evidence) and all(
        isinstance(e, dict) and isinstance(e.get("field"), str) and e["field"].startswith("item:" + item["item_id"] + ":")
        and (bound := bound_quote(e, fields)) is not None
        and all(e.get(k) == v for k, v in bound.items()) for e in evidence)
    valid = (clause.get("invalid_or_missing_observation") is False and evidence_valid
        and isinstance(clause.get("reason"), str) and len(clause["reason"].strip()) >= 8
        and clause.get("result") in {"satisfied", "violated", "undecidable"})
    predicted = clause.get("result") if valid else None
    execution = ("valid_abstention" if predicted == "undecidable" else "valid_decision") if valid else "invalid_or_missing_execution"
    expected = case["expected_process_result"]
    return {"case_hash": case["case_hash"], "source_case_id": case["source_case_id"],
        "source_group": case["source_group"], "expected": expected, "prediction": predicted,
        "agreement": predicted == expected if execution == "valid_decision" else None,
        "execution_category": execution, "evidence_valid": bool(evidence_valid),
        "criterion_processing_status": criteria[0].get("processing_status") if len(criteria) == 1 else None,
        "mapped_construct": "Q2.reasoning_process_correctness_only", "criterion_hash": criteria[0].get("criterion_hash") if len(criteria) == 1 else None,
        "audit": criteria[0].get("audit") if len(criteria) == 1 else None,
        "observation": clause}


def summarize(records, expected_count):
    if type(expected_count) is not int or expected_count < 1 or len(records) > expected_count:
        raise DomainError("EXTERNAL_CALIBRATION_COUNT_INVALID", 422)
    if len({r.get("case_hash") for r in records}) != len(records):
        raise DomainError("EXTERNAL_CALIBRATION_CASE_DUPLICATED", 422)
    decided = [r for r in records if r.get("execution_category") == "valid_decision"
        and r.get("prediction") in {"satisfied", "violated"} and r.get("evidence_valid") is True]
    abstained = [r for r in records if r.get("execution_category") == "valid_abstention"
        and r.get("prediction") == "undecidable" and r.get("evidence_valid") is True]
    invalid = len(records) - len(decided) - len(abstained)
    # Error/false-positive metrics are CONDITIONAL on an actual decision.
    # Missing execution must not silently become either a true negative or an
    # incorrect model prediction. Its coverage impact remains explicit below.
    negatives = [r for r in decided if r["expected"] == "violated"]
    positives = [r for r in decided if r["expected"] == "satisfied"]
    hits = sum(r["prediction"] == r["expected"] for r in decided)
    tp = sum(r["prediction"] == "violated" for r in negatives)
    fp = sum(r["prediction"] == "violated" for r in positives)
    clustered = _clustered_bootstrap(records)
    missing_source_groups = sum(
        not isinstance(r.get("source_group"), str) or not r.get("source_group").strip()
        for r in records
    )
    return {"method_version": VERSION, "expected": expected_count, "recorded": len(records),
        "decided": len(decided), "abstentions": len(abstained), "invalid_observations": invalid,
        "unrecorded_cases": expected_count - len(records),
        "execution_coverage": (len(decided) + len(abstained)) / expected_count,
        "decision_coverage": len(decided) / expected_count,
        "agreement_all": hits / len(records) if records else None,
        "agreement_ci95": wilson(hits, len(records)),
        "agreement_decided": hits / len(decided) if decided else None,
        "agreement_decided_ci95": wilson(hits, len(decided)),
        "agreement_expected_case_yield": hits / expected_count,
        "metric_denominators": {"agreement_all": "all recorded cases; agreement yield, NOT conditional scoring accuracy",
            "agreement_decided": "valid decisions only", "agreement_expected_case_yield": "all predeclared cases",
            "error_recall": "expert-negative cases with valid decisions only",
            "false_positive_rate": "expert-positive cases with valid decisions only"},
        "metric_denominators_detail": {
            "expected_cases": expected_count,
            "recorded_cases": len(records),
            "decided_cases": len(decided),
            "positive_cases_decided": len(positives),
            "negative_cases_decided": len(negatives),
            "true_positive_cases": tp,
            "false_positive_cases": fp,
            "abstentions": len(abstained),
            "invalid_observations": invalid,
            "unrecorded_cases": expected_count - len(records),
            "clusters": clustered["clusters"],
            "cases_with_missing_source_group": missing_source_groups,
        },
        "error_recall": tp / len(negatives) if negatives else None,
        "error_recall_ci95": wilson(tp, len(negatives)),
        "false_positive_rate": fp / len(positives) if positives else None,
        "false_positive_ci95": wilson(fp, len(positives)),
        **clustered,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_iterations": BOOTSTRAP_ITERATIONS,
        "status": "completed" if len(records) == expected_count and invalid == 0 else "incomplete",
        "calibration_status": "not_calibrated", "professional_certification": False,
        "threshold": None, "records": records,
        "scope": "published expert process labels versus actual C Q2.reasoning observations",
        "limitations": ["Small topical sample; no global or tutorial/difficulty calibration follows.",
            "Public benchmark contamination in the selected model is unknown.",
            "First-error position is not evaluated; only existence of process errors is mapped.",
            "Source label disagreement/unknown observations are preserved, never relabelled.",
            "Unrecorded cases may be provider failures or unknown calls; this summary does not infer their outcomes.",
            "Intervals are descriptive; repeated source groups are not independent trials."]}

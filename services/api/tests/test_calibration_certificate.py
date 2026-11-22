"""Certificate v1 is fail-closed; synthetic valid data is only an engineering fixture."""
from copy import deepcopy

from app.v2.calibration_certificate import (
    certificate_covers,
    certificate_state,
    issue_certificate,
)
from app.v2.external_calibration import summarize


def records(*, clusters=16, positives=35, negatives=9, missing_group=False):
    values = []
    total = positives + negatives
    for index in range(total):
        expected = "satisfied" if index < positives else "violated"
        values.append({
            "case_hash": f"case-{index}",
            "source_group": "" if missing_group and index == 0 else f"group-{index % clusters}",
            "expected": expected,
            "prediction": expected,
            "execution_category": "valid_decision",
            "evidence_valid": True,
        })
    return values


def summary():
    return summarize(records(), 44)


def bindings():
    return {
        "implementation_files": [
            {"path": "services/api/app/v2/checker.py", "sha256": "a" * 64}
        ],
        "review_protocol_hash": "b" * 64,
        "checker_protocol_hash": "c" * 64,
        "check_plan_hash": "d" * 64,
        "criterion_contract_version": "aiedu.criterion-assessment.v1",
        "criterion_hashes": ["e" * 64],
        "prompt_hashes": ["f" * 64],
        "model_families": ["deepseek"],
        "tool_versions": ["pytest@8.4"],
    }


def anchor():
    return {
        "commit": "1" * 40,
        "archive_sha256": "2" * 64,
        "protocol_hash": "3" * 64,
        "license": "CC0-1.0",
        "storage_identity": "workspace://calibration-fixture",
    }


def scope():
    return {
        "domains": ["math_logic"],
        "claim_classes": ["anchored"],
        "artifact_roles": ["tutorial"],
        "scenes": ["knowledge_tutorial"],
    }


def test_clustered_bootstrap_and_hard_sample_gates_can_issue_certificate():
    summary_value = summary()
    assert summary_value["clusters"] == 16
    assert summary_value["cases"] == 44
    assert summary_value["agreement_decided_ci95_clustered"] == [1.0, 1.0]
    assert summary_value["error_recall_ci95_clustered"] == [1.0, 1.0]
    assert summary_value["false_positive_ci95_clustered"] == [0.0, 0.0]
    assert summary_value["metric_denominators_detail"]["positive_cases_decided"] == 35
    assert summary_value["metric_denominators_detail"]["negative_cases_decided"] == 9

    certificate = issue_certificate(scope(), summary_value, bindings(), anchor())
    assert certificate["status"] == "calibrated"
    assert certificate["eligibility"]["eligible"] is True
    assert certificate_state(certificate) == "calibrated"
    assert certificate_covers(certificate, scope()) is True
    assert certificate_covers(certificate, {**scope(), "domains": ["math_data"]}) is True


def test_small_or_missing_group_samples_cannot_be_compensated_by_perfect_metrics():
    small = summarize(records(clusters=15), 44)
    small_cert = issue_certificate(scope(), small, bindings(), anchor())
    assert small_cert["status"] == "not_calibrated"
    assert "clusters_below_16" in small_cert["eligibility"]["errors"]

    missing = summarize(records(missing_group=True), 44)
    missing_cert = issue_certificate(scope(), missing, bindings(), anchor())
    assert missing_cert["status"] == "not_calibrated"
    assert "source_group_missing" in missing_cert["eligibility"]["errors"]

    too_few_positives = summarize(records(positives=34, negatives=10), 44)
    positives_cert = issue_certificate(scope(), too_few_positives, bindings(), anchor())
    assert positives_cert["status"] == "not_calibrated"
    assert "expert_positives_below_35" in positives_cert["eligibility"]["errors"]

    too_few_negatives = summarize(records(positives=36, negatives=8), 44)
    negatives_cert = issue_certificate(scope(), too_few_negatives, bindings(), anchor())
    assert negatives_cert["status"] == "not_calibrated"
    assert "expert_negatives_below_9" in negatives_cert["eligibility"]["errors"]


def test_perfect_agreement_cannot_compensate_the_false_positive_threshold():
    values = records()
    positive = next(row for row in values if row["expected"] == "satisfied")
    positive["prediction"] = "violated"
    imperfect = summarize(values, 44)
    certificate = issue_certificate(scope(), imperfect, bindings(), anchor())
    assert certificate["status"] == "not_calibrated"
    assert "case_false_positive_wilson_upper_above_0_10" in certificate["eligibility"]["errors"]


def test_binding_or_anchor_drift_marks_certificate_stale_and_handwritten_status_does_not_cover():
    certificate = issue_certificate(scope(), summary(), bindings(), anchor())
    changed = deepcopy(bindings())
    changed["implementation_files"][0]["sha256"] = "9" * 64
    assert certificate_state(certificate, current_bindings=changed) == "stale"
    assert certificate_covers(certificate, scope(), current_bindings=changed) is False

    changed_anchor = deepcopy(anchor())
    changed_anchor["commit"] = "4" * 40
    assert certificate_state(certificate, current_anchor=changed_anchor) == "stale"

    forged = deepcopy(certificate)
    forged["status"] = "calibrated"
    forged["bindings"]["prompt_hashes"] = ["8" * 64]
    assert certificate_state(forged) == "not_calibrated"
    assert certificate_covers({"calibration_status": "calibrated"}, scope()) is False

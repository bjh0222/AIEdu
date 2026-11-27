"""Final delivery remains fail-closed and user waivers never rewrite quality."""
from copy import deepcopy

from app.v2.calibration_certificate import issue_certificate
from app.v2.contracts import digest
from app.v2.delivery import delivery_decision
from app.v2.delivery_waiver import (
    build_delivery_waiver,
    revoke_delivery_waiver,
)
from app.v2.external_calibration import summarize


def plan(*, include_g2=True):
    items = [
        {"item_id": "C1.coverage", "required": True, "decision_type": "deterministic"},
        {"item_id": "G4.evidence_boundaries", "required": True,
         "decision_type": "semantic_rubric",
         "criterion_contract": {"criterion_hash": "b" * 64}},
        {"item_id": "C3.correctness", "required": True,
         "decision_type": "semantic_rubric",
         "criterion_contract": {"criterion_hash": "c" * 64}},
    ]
    if include_g2:
        items.append({
            "item_id": "G2.knowledge_integration", "required": True,
            "decision_type": "semantic_rubric",
            "criterion_contract": {"criterion_hash": "d" * 64},
        })
    return {
        "schema_version": "aiedu.check-plan.v2",
        "assessment_contract": "aiedu.criterion-assessment.v1",
        "check_plan_hash": "a" * 64,
        "check_items": items,
    }


def quality(status="needs_review", *, c3="undecidable", c1="satisfied", g4="satisfied"):
    return {
        "tqi_verdict": "not_calibrated",
        "quality_status": status,
        "calibration_status": "not_calibrated",
        "checks": [
            {"item_id": "C1.coverage", "result": c1},
            {"item_id": "G4.evidence_boundaries", "result": g4},
            {"item_id": "C3.correctness", "result": c3},
            {"item_id": "G2.knowledge_integration", "result": "satisfied"},
        ],
        "findings": [],
        "blocking_reasons": [],
    }


def certificate_bindings():
    return {
        "implementation_files": [{"path": "checker.py", "sha256": "1" * 64}],
        "review_protocol_hash": "2" * 64,
        "checker_protocol_hash": "3" * 64,
        "check_plan_hash": "a" * 64,
        "criterion_contract_version": "aiedu.criterion-assessment.v1",
        "criterion_hashes": ["b" * 64, "c" * 64, "d" * 64],
        "prompt_hashes": ["4" * 64],
        "model_families": ["deepseek"],
        "tool_versions": ["pytest@fixture"],
    }


def anchor():
    return {
        "commit": "5" * 40,
        "archive_sha256": "6" * 64,
        "protocol_hash": "7" * 64,
        "license": "CC0-1.0",
        "storage_identity": "workspace://delivery-fixture",
    }


def certificate(*claim_classes):
    records = []
    for index in range(44):
        expected = "satisfied" if index < 35 else "violated"
        records.append({
            "case_hash": f"case-{index}",
            "source_group": f"group-{index % 16}",
            "expected": expected,
            "prediction": expected,
            "execution_category": "valid_decision",
            "evidence_valid": True,
        })
    summary = summarize(records, 44)
    return issue_certificate(
        {
            "domains": ["math_logic"],
            "claim_classes": list(claim_classes),
            "artifact_roles": ["tutorial"],
            "scenes": ["knowledge_tutorial"],
        },
        summary,
        certificate_bindings(),
        anchor(),
    )


def requested_scope():
    return {
        "domains": ["math_logic"],
        "artifact_roles": ["tutorial"],
        "scenes": ["knowledge_tutorial"],
    }


def test_c3_soft_item_requires_explicit_waiver_after_certificate_covers_hard_claims():
    plan_value = plan(include_g2=False)
    quality_value = quality()
    quality_value["blocking_reasons"] = [{"item_id": "C3.correctness", "result": "undecidable"}]
    cert = certificate("computable", "anchored")
    pending = delivery_decision(
        quality_value, plan_value,
        profile_binding_current=True,
        certificate=cert,
        certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(),
        requested_scope=requested_scope(),
    )
    assert pending["state"] == "waiver_required"
    assert pending["soft_item_ids"] == ["C3.correctness"]

    waiver = build_delivery_waiver(
        plan_value, quality_value, {"C3.correctness": "undecidable"},
        {"event_id": "user-click-1", "action": "accept"},
    )
    eligible = delivery_decision(
        quality_value, plan_value,
        profile_binding_current=True,
        certificate=cert,
        certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(),
        waiver=waiver,
        requested_scope=requested_scope(),
    )
    assert eligible["state"] == "eligible"
    assert eligible["delivery_calibration_status"] == "waiver_authorized"
    assert eligible["quality_unchanged"] is True
    assert quality_value["calibration_status"] == "not_calibrated"


def test_hard_items_and_unbound_findings_cannot_be_waived():
    cert = certificate("computable", "anchored")
    hard = quality(status="failed", c1="violated")
    hard["blocking_reasons"] = [{"item_id": "C1.coverage", "result": "violated"}]
    waiver = build_delivery_waiver(
        plan(), quality(), {"C3.correctness": "undecidable"},
        {"event_id": "user-click-2"},
    )
    result = delivery_decision(
        hard, plan(), profile_binding_current=True,
        certificate=cert, certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(), waiver=waiver,
        requested_scope=requested_scope(),
    )
    assert result["state"] == "blocked"
    assert any(row["code"] == "HARD_CHECK_NOT_SATISFIED" for row in result["hard_blockers"])

    finding_quality = quality()
    finding_quality["blocking_reasons"] = [{"finding_id": "missing"}]
    ambiguous = delivery_decision(
        finding_quality, plan(), profile_binding_current=True,
        certificate=cert, certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(), requested_scope=requested_scope(),
    )
    assert ambiguous["state"] == "blocked"
    assert any(row["code"] == "BLOCKING_REASON_BINDING_AMBIGUOUS"
               for row in ambiguous["hard_blockers"])


def test_required_checks_without_certificate_can_be_final_and_revoked_waiver_is_not_silently_recreated():
    no_cert = delivery_decision(
        quality(c3="satisfied"), plan(), profile_binding_current=True,
        requested_scope=requested_scope(),
    )
    assert no_cert["state"] == "eligible"
    assert no_cert["final_allowed"] is True
    assert no_cert["delivery_calibration_status"] == "not_calibrated"
    assert no_cert["quality_unchanged"] is True

    quality_value = quality()
    quality_value["blocking_reasons"] = [{"item_id": "C3.correctness", "result": "undecidable"}]
    waiver = build_delivery_waiver(
        plan(include_g2=False), quality_value, {"C3.correctness": "undecidable"},
        {"event_id": "accept"},
    )
    revoked = revoke_delivery_waiver(waiver, {"event_id": "revoke"})
    result = delivery_decision(
        quality_value, plan(include_g2=False), profile_binding_current=True,
        certificate=certificate("computable", "anchored"),
        certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(), waiver=revoked,
        requested_scope=requested_scope(),
    )
    assert result["state"] == "waiver_required"
    assert result["waiver_status"] == "revoked"


def test_certificate_must_cover_remaining_professional_claims_when_not_waived():
    quality_value = quality(c3="satisfied")
    result = delivery_decision(
        quality_value, plan(), profile_binding_current=True,
        certificate=certificate("computable", "anchored"),
        certificate_current_bindings=certificate_bindings(),
        certificate_current_anchor=anchor(),
        requested_scope=requested_scope(),
    )
    assert result["state"] == "blocked"
    assert any(row["code"] == "CALIBRATION_SCOPE_NOT_COVERED"
               for row in result["hard_blockers"])

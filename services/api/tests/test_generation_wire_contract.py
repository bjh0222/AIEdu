"""The published fixtures and cross-block wire invariants (no models/network)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.generation_contract import (
    GenerationContractError,
    validate_generation_exchange,
    validate_generation_message,
)


FIXTURES = json.loads((Path(__file__).resolve().parents[3] / "docs/contracts/generation.v1.examples.json").read_text(encoding="utf-8-sig"))


def fixture(kind):
    return copy.deepcopy(next(message for message in FIXTURES if message["kind"] == kind))


@pytest.mark.parametrize("message", FIXTURES, ids=lambda m: m["kind"])
def test_all_published_messages_validate_without_filling_unknowns(message):
    before = copy.deepcopy(message)
    assert validate_generation_message(message) is message
    assert message == before


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(contract_version="generation.v2"),
    lambda m: m.update(unknown="not an extension"),
    lambda m: m["payload"].pop("learner"),
    lambda m: m["payload"].update(unknown="nested unknown"),
    lambda m: m["identity"].update(version=True),
    lambda m: m["payload"]["constraints"][0].update(strength="mandatory"),
    lambda m: m["payload"].update(constraints={"text": "wrong shape"}),
    lambda m: m["payload"]["scope"].update(include=[None]),
    lambda m: m["extensions"].update(cost=float("nan")),
])
def test_wire_rejects_unknown_fields_missing_fields_and_wrong_types(mutate):
    message = fixture("TaskContext")
    mutate(message)
    with pytest.raises(GenerationContractError):
        validate_generation_message(message)


def test_reference_null_is_preserved_and_does_not_become_known_identity():
    task = fixture("TaskContext")
    strategy = fixture("GenerationStrategy")
    strategy["identity"]["goal_id"] = "known-goal"
    with pytest.raises(GenerationContractError, match="identity mismatch"):
        validate_generation_exchange(task, strategy)


def test_planning_bundle_does_not_need_final_strategy():
    validate_generation_exchange(fixture("TaskContext"), evidence_bundles=[fixture("EvidenceBundle")])


def test_unknown_deliverable_and_constraint_references_are_rejected():
    task, strategy = fixture("TaskContext"), fixture("GenerationStrategy")
    strategy["payload"]["units"][0]["unit_id"] = "other-unit"
    with pytest.raises(GenerationContractError, match="unit_id"):
        validate_generation_exchange(task, strategy)
    strategy = fixture("GenerationStrategy")
    strategy["payload"]["shared_constraints"].append("unknown-constraint")
    with pytest.raises(GenerationContractError, match="constraint reference"):
        validate_generation_exchange(task, strategy)
    strategy = fixture("GenerationStrategy")
    strategy["payload"]["units"][0]["check_ids"].append("constraint-unknown")
    with pytest.raises(GenerationContractError, match="unknown constraint"):
        validate_generation_exchange(task, strategy)


def test_duplicate_identifiers_and_negative_or_exceeded_budgets_are_rejected():
    task = fixture("TaskContext")
    task["payload"]["constraints"].append(copy.deepcopy(task["payload"]["constraints"][0]))
    with pytest.raises(GenerationContractError, match="duplicate"):
        validate_generation_message(task)
    evidence = fixture("EvidenceBundle")
    for used in (-1, 3001, True):
        evidence["payload"]["chars_used"] = used
        with pytest.raises(GenerationContractError):
            validate_generation_message(evidence)


@pytest.mark.parametrize("status", ["not_run", "undecidable", "error", "violated"])
def test_required_unknown_or_failed_check_cannot_report_passed(status):
    report = fixture("AcceptanceReport")
    report["payload"].update(status="passed", passed=True)
    report["payload"]["checks"][0].update(status=status, evidence_method="model_static")
    with pytest.raises(GenerationContractError, match="required checks"):
        validate_generation_message(report)


def test_optional_disabled_does_not_invalidate_satisfied_required_checks():
    report = fixture("AcceptanceReport")
    check = report["payload"]["checks"][0]
    check.update(status="satisfied", evidence_method="deterministic")
    optional = copy.deepcopy(check)
    optional.update(id="optional-review", required=False, status="not_run", evidence_method=None)
    report["payload"]["checks"].append(optional)
    report["payload"].update(status="passed", passed=True)
    validate_generation_message(report)


def test_exchange_checks_cannot_disappear_or_be_downgraded_after_planning():
    task, strategy, report = fixture("TaskContext"), fixture("GenerationStrategy"), fixture("AcceptanceReport")
    with pytest.raises(GenerationContractError, match="strategy checks missing"):
        validate_generation_exchange(task, strategy, acceptance_report=report)
    report["payload"]["checks"].append({
        "id": "constraint-c1", "applicable": True, "required": True,
        "status": "satisfied", "evidence_method": "deterministic", "evidence_refs": [],
        "detail": "只输出一套试卷：对应unit-1",
    })
    report["extensions"]["check_plan"] = [
        {"id": "correctness", "applicable": True, "required": True},
        {"id": "constraint-c1", "applicable": True, "required": True},
    ]
    validate_generation_exchange(task, strategy, acceptance_report=report)
    report["payload"]["checks"][0]["required"] = False
    with pytest.raises(GenerationContractError, match="required changed"):
        validate_generation_exchange(task, strategy, acceptance_report=report)


def test_unit_bundle_must_reference_a_known_unit():
    task, evidence = fixture("TaskContext"), fixture("EvidenceBundle")
    evidence["payload"]["stage"] = "unit"
    with pytest.raises(GenerationContractError, match="unit identifier"):
        validate_generation_exchange(task, evidence_bundles=[evidence])
    evidence["identity"]["unit_id"] = "other-unit"
    with pytest.raises(GenerationContractError, match="unknown unit"):
        validate_generation_exchange(task, evidence_bundles=[evidence])


def test_hard_task_constraint_cannot_disappear_without_a_strategy():
    task, report = fixture("TaskContext"), fixture("AcceptanceReport")
    with pytest.raises(GenerationContractError, match="hard constraint"):
        validate_generation_exchange(task, acceptance_report=report)
    report["payload"]["checks"].append({
        "id": "constraint-c1", "applicable": True, "required": False,
        "status": "not_run", "evidence_method": None, "evidence_refs": [], "detail": "开关关闭",
    })
    with pytest.raises(GenerationContractError, match="downgraded"):
        validate_generation_exchange(task, acceptance_report=report)

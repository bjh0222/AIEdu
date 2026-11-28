"""Validate the repository's fixed generation.v1 wire contract without extra dependencies.

This interpreter intentionally supports only the vocabulary used by the bundled
schema. It is not a general JSON Schema implementation. Unknown schema keywords
fail closed so an expanded protocol cannot silently weaken validation.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


class GenerationContractError(ValueError):
    """An invalid wire message or inconsistent exchange, with a field location."""


_KEYWORDS = {
    "$schema", "$defs", "$ref", "description", "title", "type", "properties",
    "required", "additionalProperties", "items", "enum", "const", "minimum",
    "maximum", "minLength", "oneOf", "anyOf", "allOf", "if", "then", "else",
}


@lru_cache(maxsize=1)
def _schema() -> dict:
    path = Path(__file__).resolve().parents[4] / "docs" / "contracts" / "generation.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _equal(left: Any, right: Any) -> bool:
    # JSON booleans are distinct from numbers, unlike Python's True == 1.
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _is_type(value: Any, name: str) -> bool:
    return {
        "null": lambda: value is None,
        "object": lambda: isinstance(value, dict) and all(isinstance(k, str) for k in value),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: isinstance(value, bool),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
    }[name]()


def _check(value: Any, spec: dict, root: dict, path: str) -> None:
    unknown = set(spec) - _KEYWORDS
    if unknown:
        raise GenerationContractError(f"{path}: unsupported schema keyword {sorted(unknown)}")
    if "$ref" in spec:
        ref = spec["$ref"]
        if not ref.startswith("#/$defs/"):
            raise GenerationContractError(f"{path}: unsupported schema reference {ref}")
        _check(value, root["$defs"][ref.removeprefix("#/$defs/")], root, path)
    if "type" in spec:
        names = spec["type"] if isinstance(spec["type"], list) else [spec["type"]]
        if not any(_is_type(value, name) for name in names):
            raise GenerationContractError(f"{path}: expected {' or '.join(names)}")
    if "const" in spec and not _equal(value, spec["const"]):
        raise GenerationContractError(f"{path}: expected {spec['const']!r}")
    if "enum" in spec and not any(_equal(value, option) for option in spec["enum"]):
        raise GenerationContractError(f"{path}: invalid enum value {value!r}")
    if isinstance(value, str) and len(value) < spec.get("minLength", 0):
        raise GenerationContractError(f"{path}: string is too short")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise GenerationContractError(f"{path}: number must be finite")
        if "minimum" in spec and value < spec["minimum"]:
            raise GenerationContractError(f"{path}: below minimum {spec['minimum']}")
        if "maximum" in spec and value > spec["maximum"]:
            raise GenerationContractError(f"{path}: above maximum {spec['maximum']}")
    if isinstance(value, dict):
        missing = set(spec.get("required", [])) - set(value)
        if missing:
            raise GenerationContractError(f"{path}: missing required fields {sorted(missing)}")
        properties = spec.get("properties", {})
        extras = set(value) - set(properties)
        if spec.get("additionalProperties") is False and extras:
            raise GenerationContractError(f"{path}: unknown fields {sorted(extras)}; use extensions")
        for key in value.keys() & properties.keys():
            _check(value[key], properties[key], root, f"{path}.{key}")
    if isinstance(value, list) and "items" in spec:
        for index, item in enumerate(value):
            _check(item, spec["items"], root, f"{path}[{index}]")
    for option in spec.get("allOf", []):
        _check(value, option, root, path)
    for keyword in ("anyOf", "oneOf"):
        if keyword not in spec:
            continue
        matches = 0
        errors = []
        for option in spec[keyword]:
            try:
                _check(value, option, root, path)
                matches += 1
            except GenerationContractError as exc:
                errors.append(str(exc))
        if not matches or (keyword == "oneOf" and matches != 1):
            raise GenerationContractError(f"{path}: no valid {keyword} branch; {'; '.join(errors)}")
    if "if" in spec:
        try:
            _check(value, spec["if"], root, path)
            branch = "then"
        except GenerationContractError:
            branch = "else"
        if branch in spec:
            _check(value, spec[branch], root, path)


def _unique(values: Iterable[str], path: str) -> set[str]:
    items = list(values)
    if len(items) != len(set(items)):
        raise GenerationContractError(f"{path}: duplicate identifiers")
    return set(items)


def validate_generation_message(message: dict, expected_kind: str | None = None) -> dict:
    """Return the unchanged validated message; do not fill missing/unknown values."""
    root = _schema()
    # Select the tagged branch so a malformed known message reports useful fields.
    if not isinstance(message, dict):
        raise GenerationContractError("$: expected an object")
    try:
        json.dumps(message, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise GenerationContractError("$: message must contain only finite JSON values") from exc
    if message.get("contract_version") != "generation.v1":
        raise GenerationContractError("$.contract_version: expected generation.v1")
    kind = message.get("kind")
    if expected_kind is not None and kind != expected_kind:
        raise GenerationContractError(f"$.kind: expected {expected_kind}")
    branch = next((b for b in root["oneOf"] if b["properties"]["kind"]["const"] == kind), None)
    if branch is None:
        raise GenerationContractError(f"$.kind: unknown message kind {kind!r}")
    _check(message, branch, root, "$")
    payload = message["payload"]
    if kind == "TaskContext":
        _unique((c["id"] for c in payload["constraints"]), "$.payload.constraints")
        _unique((d["unit_id"] for d in payload["deliverables"]), "$.payload.deliverables")
    elif kind == "GenerationStrategy":
        _unique((u["unit_id"] for u in payload["units"]), "$.payload.units")
        for unit in payload["units"]:
            _unique(unit["check_ids"], "$.payload.units.check_ids")
    elif kind == "EvidenceBundle":
        blocks = payload["standards"] + payload["principles"] + payload["cases"]
        if payload["chars_used"] > payload["budget_chars"]:
            raise GenerationContractError("$.payload.chars_used: exceeds budget_chars")
        if payload["injection_status"] in {"disabled", "no_match"} and (blocks or payload["chars_used"]):
            raise GenerationContractError("$.payload: disabled/no_match cannot contain injected evidence")
        if payload["injection_status"] == "ok" and not blocks:
            raise GenerationContractError("$.payload: ok requires selected evidence")
        if payload["stage"] == "unit" and message["identity"]["unit_id"] is None:
            raise GenerationContractError("$.identity.unit_id: unit evidence requires a unit identifier")
    elif kind == "AcceptanceReport":
        _unique((c["id"] for c in payload["checks"]), "$.payload.checks")
        required = [c for c in payload["checks"] if c["applicable"] and c["required"]]
        for check in payload["checks"]:
            if not check["applicable"] and check["status"] != "not_applicable":
                raise GenerationContractError(f"$.payload.checks.{check['id']}: inapplicable check must be not_applicable")
            if check["applicable"] and check["status"] == "not_applicable":
                raise GenerationContractError(f"$.payload.checks.{check['id']}: applicable check cannot be not_applicable")
        if payload["passed"] and (not required or any(c["status"] != "satisfied" or c["evidence_method"] is None for c in required)):
            raise GenerationContractError("$.payload.passed: applicable required checks need a satisfied result and evidence method")
    return message


def _same_task(task: dict, other: dict) -> None:
    for field in ("task_id", "goal_id", "tutorial_id", "version"):
        if task["identity"][field] != other["identity"][field]:
            raise GenerationContractError(f"$.identity.{field}: exchange identity mismatch")


def validate_generation_exchange(
    task_context: dict,
    strategy: dict | None = None,
    *,
    evidence_bundles: Iterable[dict] = (),
    acceptance_report: dict | None = None,
    item_assessments: Iterable[dict] = (),
) -> None:
    """Check references/identity/required semantics across actual wire objects.

    Planning evidence needs only TaskContext. A strategy, when supplied, must name
    exactly the task's deliverables. Unknown/null identities are preserved.
    """
    validate_generation_message(task_context, "TaskContext")
    units = {u["unit_id"] for u in task_context["payload"]["deliverables"]}
    constraints = {c["id"] for c in task_context["payload"]["constraints"]}
    check_ids: set[str] = set()
    if strategy is not None:
        validate_generation_message(strategy, "GenerationStrategy")
        _same_task(task_context, strategy)
        if {u["unit_id"] for u in strategy["payload"]["units"]} != units:
            raise GenerationContractError("$.payload.units: must match TaskContext.deliverables unit_id")
        if not set(strategy["payload"]["shared_constraints"]) <= constraints:
            raise GenerationContractError("$.payload.shared_constraints: unknown constraint reference")
        for unit in strategy["payload"]["units"]:
            check_ids.update(unit["check_ids"])
            for check_id in unit["check_ids"]:
                if check_id.startswith("constraint-") and check_id.removeprefix("constraint-") not in constraints:
                    raise GenerationContractError(f"$.payload.units.check_ids: unknown constraint {check_id}")
    for bundle in evidence_bundles:
        validate_generation_message(bundle, "EvidenceBundle")
        _same_task(task_context, bundle)
        if bundle["payload"]["stage"] == "unit" and bundle["identity"]["unit_id"] not in units:
            raise GenerationContractError("$.identity.unit_id: unknown unit evidence reference")
    for assessment in item_assessments:
        validate_generation_message(assessment, "ItemAssessment")
        _same_task(task_context, assessment)
        unit_id = assessment["identity"]["unit_id"]
        if unit_id is not None and unit_id not in units:
            raise GenerationContractError("$.identity.unit_id: unknown assessment unit reference")
    if acceptance_report is not None:
        validate_generation_message(acceptance_report, "AcceptanceReport")
        _same_task(task_context, acceptance_report)
        report_unit = acceptance_report["identity"]["unit_id"]
        if report_unit is not None and report_unit not in units:
            raise GenerationContractError("$.identity.unit_id: unknown acceptance unit reference")
        if report_unit is not None and strategy is not None:
            check_ids = set(next(u for u in strategy["payload"]["units"] if u["unit_id"] == report_unit)["check_ids"])
        reported = {c["id"] for c in acceptance_report["payload"]["checks"]}
        if not check_ids <= reported:
            raise GenerationContractError(f"$.payload.checks: strategy checks missing {sorted(check_ids - reported)}")
        # A runtime plan can retain required/applicable decisions in extensions.
        plan = acceptance_report.get("extensions", {}).get("check_plan", [])
        results = {c["id"]: c for c in acceptance_report["payload"]["checks"]}
        for check_id in reported:
            if check_id.startswith("constraint-") and check_id.removeprefix("constraint-") not in constraints:
                raise GenerationContractError(f"$.payload.checks: unknown constraint {check_id}")
        for constraint in task_context["payload"]["constraints"]:
            if constraint["strength"] == "hard":
                result = results.get("constraint-" + constraint["id"])
                if result is None or not result["applicable"] or not result["required"]:
                    raise GenerationContractError(f"$.payload.checks: hard constraint {constraint['id']} missing or downgraded")
        for check in plan:
            result = results.get(check["id"])
            if result is None or any(result[key] != check[key] for key in ("applicable", "required")):
                raise GenerationContractError(f"$.payload.checks.{check['id']}: check plan applicability/required changed")

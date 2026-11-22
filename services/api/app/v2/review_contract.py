"""Deterministic review contracts shared by every model-assisted quality consumer.

This module owns only structure and scheduling facts. It never decides whether
learner content is good, substitutes a professional judgment for a model, or
changes provider outcomes. Existing quote-based observations remain readable;
new requests may bind a short server-owned evidence ID instead.
"""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
import re
from typing import Any, Mapping

from ..learning.disciplines import canonical_domain_key
from .contracts import DomainError, digest, sha
from .quality_policy import context_domains, unit_domains

EVIDENCE_VERSION = "server-evidence-catalog.v1"
NORMALIZER_VERSION = "provider-contract-normalizer.v1"
APPLICABILITY_VERSION = "zero-model-applicability.v1"
PROTOCOL_VERSION = "quality-protocol-freeze.v5"

_RESULT_ALIASES = {
    "pass": "satisfied",
    "passed": "satisfied",
    "support": "satisfied",
    "supported": "satisfied",
    "compliant": "satisfied",
    "满足": "satisfied",
    "通过": "satisfied",
    "fail": "violated",
    "failed": "violated",
    "noncompliant": "violated",
    "未通过": "violated",
    "unknown": "undecidable",
    "indeterminate": "undecidable",
    "未判定": "undecidable",
    "na": "not_applicable",
    "n/a": "not_applicable",
    "notapplicable": "not_applicable",
    "不适用": "not_applicable",
}
_TRUE = {"true", "yes", "y", "是"}
_FALSE = {"false", "no", "n", "否"}
_NULL = {"", "null", "none"}
_EVIDENCE_ID = re.compile(r"^E[0-9]{4,}$")
_QUALITY_STAGES = {
    "checking",
    "checking_perturbation",
    "item_reviewing",
    "blind_solving",
    "selective_review",
    "criterion_assessing",
    "criterion_contract_correcting",
    "difficulty_reviewing",
    "difficulty_contract_correcting",
    "research_reviewing",
    "research_content_correcting",
    "optimizing",
    "optimization",
    "optimization_acceptance",
    "repairing",
    "verifying",
}


def _change(changes: list[dict[str, str]], path: str, code: str, before: Any = None, after: Any = None) -> None:
    """Keep normalization evidence bounded and free of learner/provider text."""
    if len(changes) >= 256:
        return
    row = {"path": path[:160], "code": code}
    if type(before) is bool or type(before) in {int, float} or before is None:
        row["before_type"] = type(before).__name__
    if type(after) is bool or type(after) in {int, float} or after is None:
        row["after_type"] = type(after).__name__
    changes.append(row)


def _number(value: Any) -> tuple[Any, bool]:
    if value is None or type(value) in {int, float}:
        return value, False
    if isinstance(value, str):
        text = value.strip()
        if text.casefold() in _NULL:
            return None, True
    return value, False


def _evidence_shape(value: Any, path: str, changes: list[dict[str, str]]) -> list:
    if value is None:
        _change(changes, path, "evidence_null_to_empty")
        return []
    if isinstance(value, list):
        result = []
        for index, item in enumerate(value[:256]):
            if isinstance(item, dict):
                result.append(deepcopy(item))
            elif isinstance(item, str) and _EVIDENCE_ID.fullmatch(item.strip()):
                result.append({"evidence_id": item.strip()})
                _change(changes, f"{path}[{index}]", "evidence_scalar_id_to_object")
            else:
                result.append({"invalid_evidence_shape": type(item).__name__})
                _change(changes, f"{path}[{index}]", "invalid_evidence_entry_preserved_as_failure")
        if len(value) > 256:
            result.append({"invalid_evidence_shape": "too_many_entries"})
            _change(changes, path, "evidence_list_over_limit")
        return result
    if isinstance(value, dict):
        _change(changes, path, "evidence_object_to_list")
        return [deepcopy(value)]
    if isinstance(value, str) and _EVIDENCE_ID.fullmatch(value.strip()):
        _change(changes, path, "evidence_scalar_id_to_list")
        return [{"evidence_id": value.strip()}]
    _change(changes, path, "invalid_evidence_shape_preserved_as_failure")
    return [{"invalid_evidence_shape": type(value).__name__}]


def normalize_contract(value: Any) -> tuple[Any, list[dict[str, str]]]:
    """Normalize only unambiguous provider wire-shape variants.

    Semantic values are retained. Bad evidence still fails binding; it is made
    into a bounded, explicit shape so one malformed scalar cannot crash the
    consumer or masquerade as an abstention.
    """
    changes: list[dict[str, str]] = []

    def walk(item: Any, path: str) -> Any:
        if isinstance(item, list):
            return [walk(child, f"{path}[{index}]") for index, child in enumerate(item[:256])]
        if not isinstance(item, dict):
            return item
        result = {key: walk(child, f"{path}.{key}") for key, child in item.items()}
        if "applicability" in result and "applicable" not in result:
            result["applicable"] = result.pop("applicability")
            _change(changes, f"{path}.applicability", "applicability_key_alias_to_applicable")
        for key in ("applicable",):
            current = result.get(key)
            if isinstance(current, str):
                token = current.strip().casefold()
                if token in _TRUE:
                    result[key] = True
                    _change(changes, f"{path}.{key}", "string_boolean_normalized", current, True)
                elif token in _FALSE:
                    result[key] = False
                    _change(changes, f"{path}.{key}", "string_boolean_normalized", current, False)
        for key in ("numerator", "denominator", "rating", "score"):
            if key in result:
                before = result[key]
                normalized, changed = _number(before)
                if changed:
                    result[key] = normalized
                    _change(changes, f"{path}.{key}", "numeric_string_or_null_normalized", before, normalized)
        if "enum_value" in result and isinstance(result["enum_value"], str):
            token = result["enum_value"].strip().casefold()
            if token in _NULL:
                _change(changes, f"{path}.enum_value", "null_marker_to_null")
                result["enum_value"] = None
        if isinstance(result.get("result"), str):
            raw = result["result"].strip()
            token = re.sub(r"[\s_-]+", "", raw.casefold())
            normalized = _RESULT_ALIASES.get(token)
            if normalized and normalized != raw:
                _change(changes, f"{path}.result", "result_enum_normalized")
                result["result"] = normalized
        if "evidence" in result:
            result["evidence"] = _evidence_shape(result["evidence"], f"{path}.evidence", changes)
        return result

    return walk(deepcopy(value), "$"), changes


def _bounded_parts(text: str, limit: int) -> list[str]:
    parts: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sentences = re.split(r"(?<=[。！？!?；;])|(?<=\.)(?=\s)", stripped)
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if len(sentence) <= limit:
                parts.append(sentence)
            else:
                parts.extend(sentence[start:start + limit] for start in range(0, len(sentence), limit))
    return parts


def build_evidence_catalog(
    fields: Mapping[str, str],
    *,
    max_entries: int = 320,
    max_span_chars: int = 280,
) -> list[dict[str, Any]]:
    """Return short, unique, server-owned spans ordered by supplied field."""
    if not isinstance(fields, Mapping):
        raise DomainError("EVIDENCE_FIELDS_INVALID", 422)
    clean = {key: value for key, value in fields.items()
             if isinstance(key, str) and isinstance(value, str) and value}
    if not clean:
        return []
    share = max(8, math.ceil(max_entries / len(clean)))
    catalog: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for field, text in clean.items():
        accepted = 0
        for quote in _bounded_parts(text, max_span_chars):
            if accepted >= share or len(catalog) >= max_entries:
                break
            if (field, quote) in seen or text.count(quote) != 1:
                continue
            seen.add((field, quote))
            start = text.encode("utf-8").find(quote.encode("utf-8"))
            evidence_id = f"E{len(catalog) + 1:04d}"
            kind = ("context" if field.startswith("context:") else
                    "source" if field.startswith("source:") else "content")
            catalog.append({"evidence_id": evidence_id, "field": field, "quote": quote,
                            "start_byte": start, "end_byte": start + len(quote.encode("utf-8")),
                            "span_hash": sha(quote), "kind": kind})
            accepted += 1
    return catalog


def hydrate_evidence_ids(value: Any, catalog: list[dict[str, Any]] | None) -> tuple[Any, list[dict[str, str]]]:
    """Replace model-written field/quote pairs with the catalog's fixed span."""
    by_id = {row["evidence_id"]: row for row in catalog or []
             if isinstance(row, dict) and isinstance(row.get("evidence_id"), str)}
    changes: list[dict[str, str]] = []

    def walk(item: Any, path: str) -> Any:
        if isinstance(item, list):
            return [walk(child, f"{path}[{index}]") for index, child in enumerate(item)]
        if not isinstance(item, dict):
            return item
        result = {key: walk(child, f"{path}.{key}") for key, child in item.items()}
        evidence_id = result.get("evidence_id")
        if isinstance(evidence_id, str):
            bound = by_id.get(evidence_id.strip())
            if bound:
                if result.get("field") != bound["field"] or result.get("quote") != bound["quote"]:
                    _change(changes, path, "model_quote_replaced_by_server_evidence_id")
                result.update(field=bound["field"], quote=bound["quote"],
                              evidence_id=bound["evidence_id"], span_hash=bound["span_hash"])
            else:
                _change(changes, path, "unknown_evidence_id_preserved_as_failure")
                result.update(field="__invalid_evidence_id__", quote="")
        return result

    return walk(deepcopy(value), "$"), changes


def prepare_review_output(output: Any, catalog: list[dict[str, Any]] | None = None) -> tuple[Any, dict[str, Any]]:
    """Apply bounded structural normalization and evidence-ID hydration."""
    normalized, normalization = normalize_contract(output)
    hydrated, evidence = hydrate_evidence_ids(normalized, catalog)

    def digest_safe(value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): digest_safe(child) for key, child in value.items()}
        if isinstance(value, list):
            return [digest_safe(child) for child in value]
        if type(value) is float and not math.isfinite(value):
            return {"nonfinite_float": repr(value)}
        return value

    return hydrated, {
        "method_version": NORMALIZER_VERSION,
        "evidence_catalog_version": EVIDENCE_VERSION,
        "changes": [*normalization, *evidence],
        "change_count": len(normalization) + len(evidence),
        "normalized_output_hash": digest(digest_safe(hydrated)),
    }


def _unit_scenes(unit: dict, context: dict) -> set[str]:
    role = unit.get("artifact_role")
    scenes = {str(binding.get("scene")) for binding in context.get("spec_bindings", [])
              if binding.get("scene") and (
                  binding.get("artifact_role") == role
                  or role in {"chapter", "book_level", "cheatsheet"}
                  and binding.get("artifact_role") == "tutorial"
              )}
    if unit.get("scene"):
        scenes.add(str(unit["scene"]))
    return scenes


def _unit_question_forms(unit: dict) -> set[str]:
    forms: set[str] = set()
    for item in unit.get("items", []) if isinstance(unit.get("items"), list) else []:
        if not isinstance(item, dict):
            continue
        for key in ("question_form", "question_type"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                forms.add(re.sub(r"[\s_-]+", "_", value.strip().casefold()))
    design = unit.get("output_design")
    if isinstance(design, dict) and isinstance(design.get("question_form"), str):
        forms.add(re.sub(r"[\s_-]+", "_", design["question_form"].strip().casefold()))
    return forms


def applicability_decision(item: dict, unit: dict, context: dict) -> dict[str, Any]:
    """Zero-model unit x dimension x scene x question-form applicability."""
    scope = item.get("scope") or {}
    dimension = str(item.get("dimension") or item.get("item_id") or "")
    roles = set(scope.get("roles") or [])
    scenes = _unit_scenes(unit, context)
    domains = {canonical_domain_key(str(value)) for value in scope.get("domains") or []
               if isinstance(value, str) and value.strip()}
    forms = _unit_question_forms(unit)
    scoped_forms = {re.sub(r"[\s_-]+", "_", str(value).strip().casefold())
                    for value in (scope.get("question_types") or scope.get("question_forms") or [])
                    if isinstance(value, str) and value.strip()}
    effective_domains = unit_domains(context, unit)
    matched_domains = sorted(domains & effective_domains)
    requires_items = bool(scope.get("requires_items") or re.match(
        r"^(?:Q[1-9][0-9]*\.|M\.items\.|I\.)", dimension))

    reason = "applicable"
    execute = True
    if roles and unit.get("artifact_role") not in roles:
        execute, reason = False, "role_not_in_scope"
    elif scope.get("scenes") and not scenes.intersection(set(scope["scenes"])):
        execute, reason = False, "scene_not_in_scope"
    elif domains and not effective_domains.intersection(domains):
        execute, reason = False, "domain_not_in_scope"
    elif requires_items and not unit.get("items"):
        execute, reason = False, "item_content_not_present"
    elif scoped_forms and not forms.intersection(scoped_forms):
        execute, reason = False, "question_form_not_in_scope"
    return {
        "method_version": APPLICABILITY_VERSION,
        "decision": "execute" if execute else "skip",
        "reason": reason,
        "check_id": item.get("item_id"),
        "dimension": dimension,
        "unit_id": unit.get("unit_id"),
        "artifact_role": unit.get("artifact_role"),
        "scenes": sorted(scenes),
        "question_forms": sorted(forms),
        "consumer": item.get("verifier_binding"),
        "applicability_reason": reason,
        "scope": {
            **deepcopy(scope),
            **({"domains": sorted(domains)} if "domains" in scope else {}),
        },
        "canonical_domain_keys": sorted(effective_domains),
        "context_domain_keys": sorted(context_domains(context)),
        "matched_domains": matched_domains,
    }


def quality_preflight(items: list[dict], units: list[dict], context: dict) -> dict[str, Any]:
    """Build a complete scheduling matrix before any concurrent model work."""
    rows = [applicability_decision(item, unit, context)
            for item in items for unit in units]
    if len(rows) > 20_000:
        raise DomainError("QUALITY_PREFLIGHT_RESOURCE_LIMIT", 422, rows=len(rows))
    execute = [row for row in rows if row["decision"] == "execute"]
    skipped = [row for row in rows if row["decision"] == "skip"]
    return {
        "method_version": APPLICABILITY_VERSION,
        "rows": rows,
        "summary": {
            "pairs": len(rows),
            "execute": len(execute),
            "skip": len(skipped),
            "protected_runtime_or_research_consumers": sum(
                row["consumer"] in {"ps51_sandbox.v1", "target_runtime.v1",
                                    "research_rubric.v1", "criterion_review.v1"}
                for row in execute),
        },
        "matrix_hash": digest(rows),
    }


def review_protocol_manifest() -> dict[str, Any]:
    """Freeze every behavior that can alter quality execution or evidence shape."""
    from .skills import review_skill
    from .criterion_protocol import VERSION as CRITERION_PROTOCOL
    from .criterion_review import VERSION as CRITERION_REVIEW
    from .research_observations import VERSION as RESEARCH_OBSERVATIONS
    from .research_review_corrections import METHOD, SCHEMA_METHOD
    from .difficulty_protocol import VERSION as DIFFICULTY_PROTOCOL
    from .derivation import VERSION as DERIVATION_VERSION
    from .external_calibration import VERSION as EXTERNAL_CALIBRATION
    from .calibration_certificate import VERSION as CALIBRATION_CERTIFICATE
    from .delivery import VERSION as DELIVERY_DECISION
    from .delivery_waiver import VERSION as DELIVERY_WAIVER

    skill = review_skill()
    components = {
        "review_skill": {"version": skill["version"], "sha256": skill["hash"]},
        "evidence_catalog": {"version": EVIDENCE_VERSION},
        "normalizer": {"version": NORMALIZER_VERSION},
        "applicability": {"version": APPLICABILITY_VERSION},
        "criterion_observation": {"version": CRITERION_PROTOCOL},
        "criterion_review": {"version": CRITERION_REVIEW},
        "research_observations": {"version": RESEARCH_OBSERVATIONS},
        "research_correction": {"version": METHOD, "schema_version": SCHEMA_METHOD},
        "difficulty_observation": {"version": DIFFICULTY_PROTOCOL},
        "assessment_derivation": {"version": DERIVATION_VERSION},
        "external_calibration": {"version": EXTERNAL_CALIBRATION},
        "calibration_certificate": {"version": CALIBRATION_CERTIFICATE},
        "delivery_decision": {"version": DELIVERY_DECISION},
        "delivery_waiver": {"version": DELIVERY_WAIVER},
        "model_output_budget": {"version": "model-output-budget.v1"},
        "research_evidence_degradation": {"version": "research-evidence-degrade.v1"},
    }
    module_dir = Path(__file__).resolve().parent
    implementation_files = {
        filename: sha((module_dir / filename).read_bytes())
        for filename in (
            "checker.py",
            "checker_batch.py",
            "derivation.py",
            "external_calibration.py",
            "calibration_certificate.py",
            "delivery.py",
            "delivery_waiver.py",
            "capacity.py",
            "inference.py",
            "research_length_recovery.py",
            "worker.py",
            "quality.py",
            "quality_policy.py",
            "criterion_protocol.py",
            "criterion_review.py",
            "research_observations.py",
            "research_review_corrections.py",
            "difficulty_protocol.py",
            "difficulty_review.py",
            "review_contract.py",
        )
    }
    manifest = {"method_version": PROTOCOL_VERSION, "components": components,
                "implementation_files": implementation_files}
    manifest["manifest_hash"] = digest(manifest)
    return manifest


def ensure_quality_protocol(state: dict) -> dict[str, Any]:
    """Return the frozen manifest; refuse silent protocol drift on a live run."""
    current = review_protocol_manifest()
    frozen = state.get("quality_protocol")
    if frozen is not None and frozen != current:
        raise DomainError("QUALITY_PROTOCOL_DRIFT", 409,
                          frozen_hash=frozen.get("manifest_hash"),
                          current_hash=current.get("manifest_hash"))
    return current


def delivery_outcome(
    *,
    status: str,
    stages: dict[str, str],
    quality: dict[str, Any],
    version_id: str | None,
    calls: list[dict[str, Any]],
    binding_current: bool = True,
    delivery_decision: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Map run facts to the four user-visible delivery states without changing quality."""
    if not version_id:
        return {"state": "not_started", "label": "尚无草稿",
                "detail": "还没有可用于阅读的固定版本。"}
    final_ready = bool(delivery_decision and delivery_decision.get("final_allowed")) if delivery_decision else (
        quality.get("tqi_verdict") == "pass"
        and quality.get("quality_status") == "passed"
        and quality.get("calibration_status") == "calibrated"
        and not quality.get("blocking_reasons")
        and binding_current
    )
    active_quality_stage = any(
        stages.get(stage) == "running" for stage in _QUALITY_STAGES
    ) or any(
        call.get("status") in {"authorized", "sent"} and call.get("stage") in _QUALITY_STAGES
        for call in calls
    )
    quality_prefixes = ("research_", "rubric_", "criterion_", "difficulty_", "synthesis_")
    active_quality_stage = active_quality_stage or any(
        value == "running" and str(stage).startswith(quality_prefixes)
        for stage, value in stages.items()
    ) or any(
        call.get("status") in {"authorized", "sent"}
        and str(call.get("stage", "")).startswith(quality_prefixes)
        for call in calls
    )
    if final_ready:
        return {"state": "final_ready", "label": "final 就绪",
                "detail": "固定版本满足最终交付门；仍需遵守导出清单与专业范围。"}
    if delivery_decision and delivery_decision.get("state") == "waiver_required":
        return {"state": "quality_blocked", "label": "待确认交付",
                "detail": "必需检查已完成，但 C3/G2 专业项需要用户显式确认；授权不改变质检结果或校准事实。"}
    if status in {"queued", "running"} and active_quality_stage:
        return {"state": "quality_checking", "label": "质检进行中",
                "detail": "已有固定草稿；当前耗时属于复核或整改，不是仍在撰写未保存正文。"}
    if status in {"failed", "interrupted", "completed"} and (
        quality.get("tqi_verdict") in {"blocked", "needs_repair"}
        or quality.get("quality_status") in {"needs_review", "failed"}
        or status != "completed"
    ):
        return {"state": "quality_blocked", "label": "质检受阻",
                "detail": "草稿可读，但必需检查未完成、未通过或最终交付门未满足。"}
    return {"state": "draft_ready", "label": "草稿已就绪",
            "detail": "固定草稿可读；这不表示质量已通过或可以最终交付。"}


def summarize_calls(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Same-scope token, price, latency, stage and retry-source accounting."""
    token_total = 0
    token_unknown = 0
    price_known = 0
    price_missing = 0
    started_or_settled = 0
    retry_sources: dict[str, int] = {}
    stages: dict[str, dict[str, Any]] = {}
    elapsed_total = 0.0
    for call in calls:
        stage = str(call.get("stage") or "unclassified")
        bucket = stages.setdefault(stage, {"calls": 0, "tokens_known": 0,
                                           "token_unknown_calls": 0,
                                           "elapsed_seconds": 0.0})
        bucket["calls"] += 1
        elapsed = call.get("elapsed_seconds")
        if type(elapsed) in {int, float} and math.isfinite(elapsed) and elapsed >= 0:
            bucket["elapsed_seconds"] += float(elapsed)
            elapsed_total += float(elapsed)
        usage = call.get("usage")
        total = usage.get("total_tokens") if isinstance(usage, dict) else None
        if call.get("status") != "not_sent":
            started_or_settled += 1
            if type(total) in {int, float} and math.isfinite(total) and total >= 0:
                token_total += int(total)
                bucket["tokens_known"] += int(total)
            else:
                token_unknown += 1
                bucket["token_unknown_calls"] += 1
            price = call.get("price")
            amount = price.get("amount", price.get("total")) if isinstance(price, dict) else None
            currency = price.get("currency") if isinstance(price, dict) else None
            if type(amount) in {int, float} and math.isfinite(amount) and amount >= 0 and isinstance(currency, str) and currency.strip():
                price_known += 1
            else:
                price_missing += 1
        source = call.get("retry_source")
        if not isinstance(source, str):
            source = ("explicit_retry" if call.get("supersedes_call_id")
                      else "ancestor_resume" if call.get("current_run") is False
                      else "stage_reattempt" if type(call.get("attempt")) is int and call["attempt"] > 1
                      else "initial")
        retry_sources[source] = retry_sources.get(source, 0) + 1
    for bucket in stages.values():
        bucket["elapsed_seconds"] = round(bucket["elapsed_seconds"], 3)
    return {
        "method_version": "run-accounting-summary.v1",
        "calls": len(calls),
        "calls_started_or_settled": started_or_settled,
        "tokens_known": token_total,
        "token_unknown_calls": token_unknown,
        "price_known_calls": price_known,
        "price_missing_calls": price_missing,
        "elapsed_seconds": round(elapsed_total, 3),
        "retry_source_counts": retry_sources,
        "stage_distribution": dict(sorted(stages.items())),
    }

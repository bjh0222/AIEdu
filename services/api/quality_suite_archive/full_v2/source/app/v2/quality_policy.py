"""Single-source quality applicability policy.

This module contains only deterministic domain facts. It must not import the
checker or reducer modules, which lets producers and consumers share the same
rule without creating an import cycle.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..learning.disciplines import canonical_domain_key


DIFFICULTY_AXIS_DOMAINS = frozenset({"math_logic"})


def canonical_domains(values: Iterable[object]) -> set[str]:
    """Return canonical nonempty domain keys from a bounded iterable."""
    result: set[str] = set()
    for value in values:
        if isinstance(value, str) and value.strip():
            result.add(canonical_domain_key(value))
    return result


def context_domains(context: Mapping | None) -> set[str]:
    """Collect the canonical domains declared by a frozen task context."""
    value = context if isinstance(context, Mapping) else {}
    domains = canonical_domains([value.get("domain_key")])
    for key in ("subject_bindings", "subjects"):
        rows = value.get(key)
        if isinstance(rows, list):
            domains.update(canonical_domains(
                row.get("domain_key") for row in rows if isinstance(row, Mapping)
            ))
    return domains or {"general"}


def unit_domains(context: Mapping | None, unit: Mapping | None) -> set[str]:
    """Return a unit's effective domains, falling back to task context only if absent."""
    row = unit if isinstance(unit, Mapping) else {}
    domains = canonical_domains([row.get("domain_key")])
    bindings = row.get("subject_bindings")
    if isinstance(bindings, list):
        domains.update(canonical_domains(
            binding.get("domain_key") for binding in bindings
            if isinstance(binding, Mapping)
        ))
    return domains or context_domains(context)


def task_domains(context: Mapping | None, units: Iterable[Mapping] = ()) -> set[str]:
    """Collect task and unit declarations without changing their stored identity."""
    domains = context_domains(context)
    for unit in units:
        if not isinstance(unit, Mapping):
            continue
        domains.update(canonical_domains([unit.get("domain_key")]))
        bindings = unit.get("subject_bindings")
        if isinstance(bindings, list):
            domains.update(canonical_domains(
                row.get("domain_key") for row in bindings if isinstance(row, Mapping)
            ))
    return domains


def difficulty_applicability(context: Mapping | None, units: Iterable[Mapping]) -> dict:
    """Return the frozen task-level difficulty-axis applicability decision."""
    unit_rows = [unit for unit in units if isinstance(unit, Mapping)]
    domains = set()
    matched = set()
    applicable_unit_ids = []
    requires_items = any(bool(unit.get("items")) for unit in unit_rows)
    for unit in unit_rows:
        current = unit_domains(context, unit)
        domains.update(current)
        overlap = current & DIFFICULTY_AXIS_DOMAINS
        matched.update(overlap)
        if overlap and unit.get("items"):
            applicable_unit_ids.append(unit.get("unit_id"))
    matched = sorted(matched)
    applicable = bool(applicable_unit_ids)
    reason = "applicable" if applicable else (
        "item_content_not_present" if matched and not requires_items
        else "domain_not_in_scope"
    )
    return {
        "method_version": "difficulty-axis-applicability.v1",
        "decision": "execute" if applicable else "skip",
        "reason": reason,
        "canonical_domains": sorted(domains),
        "matched_domains": matched,
        "requires_items": requires_items,
        "applicable_unit_ids": applicable_unit_ids,
        "scope": {
            "domains": sorted(DIFFICULTY_AXIS_DOMAINS),
            "requires_items": True,
        },
    }

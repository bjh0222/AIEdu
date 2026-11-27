"""Deterministic TQI-E plan, evidence wrapper, reducer and bounded repair guards.

Semantic/model checks never manufacture a trusted verification result.
"""
from __future__ import annotations

import re
from copy import deepcopy
from uuid import uuid4

from .contracts import DomainError, digest, sha
from .quality_policy import DIFFICULTY_AXIS_DOMAINS, task_domains
from .resolver import SCENES, canonical_scene
from ..learning.disciplines import canonical_domain_key

POLICY = {"policy_version": "aiedu.repair.v1", "enabled": True,
          "max_attempts_per_finding": 3, "max_repair_rounds_per_run": 5}


QUALITY_LAYER_METHOD_VERSION = "quality-layers.v1"
QUALITY_LAYERS = ("correctness", "completeness", "quality_specific")


def normalize_quality_layers(value) -> dict | None:
    """Freeze the explicit layer policy; absent input keeps legacy behavior.

    ``correctness`` is forced: sending ``false`` is an explicit contract error,
    never a silent re-enable.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DomainError("QUALITY_LAYER_POLICY_INVALID", 422)
    unknown = sorted(str(key) for key in value if key not in QUALITY_LAYERS)
    if unknown:
        raise DomainError("QUALITY_LAYER_POLICY_INVALID", 422, keys=unknown)
    for key, enabled in value.items():
        if type(enabled) is not bool:
            raise DomainError("QUALITY_LAYER_POLICY_INVALID", 422, key=key)
    if value.get("correctness") is False:
        raise DomainError("QUALITY_LAYER_POLICY_INVALID", 422, reason="correctness_layer_is_forced")
    return {"method_version": QUALITY_LAYER_METHOD_VERSION, "correctness": True,
            "completeness": value.get("completeness", True),
            "quality_specific": value.get("quality_specific", False)}


def quality_layer_for(item: dict) -> str:
    """Frozen S10 mapping. Unknown rows default to completeness (safe: on)."""
    item_id = item.get("item_id", "")
    if item.get("system") == "discipline":
        return "correctness" if item.get("decision_type") == "atomic_claim" else "quality_specific"
    if item_id in {"C1.coverage", "C2.structure", "C3.correctness", "C5.materials",
                   "C6.context_integrity", "G4.evidence_boundaries"}:
        return "correctness"
    if item_id.startswith(("S.layout.", "M.items.", "P.execution.")) or re.match(r"^Q[126]\.", item_id):
        return "correctness"
    if (item_id in {"C4.constraints", "G1.semantic_alignment", "G2.knowledge_integration"}
            or item_id.startswith(("S.format.", "Q3."))):
        return "completeness"
    if item_id.startswith(("G3.", "Q4.", "Q5.", "R.", "I.")):
        return "quality_specific"
    return "completeness"


def layer_enabled(item: dict, layers: dict | None) -> bool:
    """Legacy/unknown rows stay enabled; hard baselines ignore layer policy."""
    if not layers:
        return True
    if item.get("hard_baseline"):
        return True
    return layers.get(item.get("quality_layer", ""), True) is True


def frozen_quality_layers(state: dict) -> dict | None:
    """start_run freezes the same policy into execution_policy and check_plan;
    read either, preferring the execution policy."""
    layers = (state.get("execution_policy") or {}).get("quality_layers")
    if isinstance(layers, dict):
        return layers
    layers = (state.get("check_plan") or {}).get("quality_layers")
    return layers if isinstance(layers, dict) else None


def disabled_layer_check(item: dict) -> dict:
    """Honest policy-skipped state; never rendered as satisfied."""
    return {"item_id": item["item_id"], "result": "not_applicable",
            "reason": "quality_layer_disabled_by_policy",
            "quality_layer": item.get("quality_layer"),
            "method_version": item.get("verifier_binding", QUALITY_LAYER_METHOD_VERSION)}


def finding_layer_enabled(plan: dict, finding: dict) -> bool:
    """Unbound findings stay visible; disabled layers never drive repair spend."""
    layers = plan.get("quality_layers") if isinstance(plan, dict) else None
    if not layers:
        return True
    item = next((i for i in plan.get("check_items", []) if i["item_id"] == finding.get("item_id")), None)
    return True if item is None else layer_enabled(item, layers)


def compile_plan(task: dict, network_policy: dict | None = None, quality_layers: dict | None = None) -> dict:
    items, conflicts = [], list(task.get("task_intent", {}).get("conflicts", []))
    declared_task_domains = sorted(task_domains(task))

    def canonical_scope(scope: dict | None) -> dict:
        value = deepcopy(scope) if isinstance(scope, dict) else {}
        if "domains" in value:
            value["domains"] = sorted({
                canonical_domain_key(str(domain))
                for domain in value.get("domains") or []
                if isinstance(domain, str) and domain.strip()
            })
        if "scenes" in value:
            value["scenes"] = sorted({
                canonical_scene(str(scene))
                for scene in value.get("scenes") or []
                if isinstance(scene, str) and scene.strip()
            })
        return value

    def add(item_id, system, method, condition, severity="blocker", kind="deterministic", scope=None):
        items.append({"item_id": item_id, "system": system, "dimension": item_id,
                      "decision_type": kind, "verifier_binding": method,
                      "severity_class": severity, "required": True,
                      "required_evidence": [method], "pass_condition": condition,
                      "scope": canonical_scope(scope), "applicability_reason": "general frozen task requirement"})
    add("C1.coverage", "general", "coverage.v1", "all required units and source chunks are accounted for")
    add("C2.structure", "general", "markdown.v1", "nonempty content; balanced code fences; unique stable unit IDs")
    add("C3.correctness", "general", "independent_oracle.v1", "no confirmed severe factual error", kind="semantic_rubric")
    add(
        "C4.constraints",
        "task",
        "independent_oracle.v1",
        "every frozen hard constraint is verified",
        kind="semantic_rubric",
        scope={"requires_nonempty_hard_constraints": True},
    )
    add("C5.materials", "general", "source_manifest.v1", "all selected materials complete and hash-bound")
    add("C6.context_integrity", "general", "context-directive.v1", "untrusted data cannot grant execution, model, or verification authority")
    # Cross-disciplinary constructs are first-class C checks.  They are
    # intentionally separate from a format/spec check and from a domain pack:
    # a subject-specific rule cannot make semantic alignment or evidence
    # boundaries disappear.  criterion_review.v1 is the sole consumer so the
    # legacy candidate-finding loop cannot duplicate or weaken these checks.
    from .check_criteria import GENERAL
    for item_id, contract in GENERAL.items():
        add(item_id, "general", "criterion_review.v1", contract["pass_condition"],
            kind="semantic_rubric", scope={})
    bindings = task.get("spec_bindings", [])
    roles = {}
    artifacts = task.get("requested_artifacts", ["tutorial"])
    if "answer" in artifacts and not set(artifacts) & {"paper", "exercise"}:
        conflicts.append({"code": "ANSWER_REQUIRES_QUESTIONS"})
    for binding in bindings:
        scene, role = binding["scene"], binding["artifact_role"]
        if scene not in SCENES:
            conflicts.append({"code": "UNSUPPORTED_SCENE", "scene": scene})
        if role in roles and roles[role] != binding:
            conflicts.append({"code": "SPEC_CONFLICT", "artifact_role": role})
        roles[role] = binding
        normalized_scene = canonical_scene(scene)
        from .format_runtime import format_layout
        from .adaptive_format import LAYOUT
        layout = LAYOUT if task.get("output_design") else format_layout(binding.get("snapshot", {}))
        if layout:
            add(f"S.layout.{role}", "spec", "inherited_layout.v1", "task-derived projection is intact" if task.get("output_design") else "frozen inherited structural projection is intact",
                scope={"roles": [role], "format_contract": layout})
        if normalized_scene in {"exam_paper", "exercise"}:
            add(f"M.items.{role}", "spec", "items.v1",
                "coupled stem/answer/steps; paper scored, exercise unscored",
                scope={"roles": [role], "requires_items": True})
        domains = {canonical_domain_key(s["domain_key"]) for s in task.get("subject_bindings", [])} or {canonical_domain_key(task.get("domain_key"))}
        if normalized_scene in {"exam_paper", "exercise"} and domains & DIFFICULTY_AXIS_DOMAINS:
            for q in range(1, 7):
                add(f"Q{q}.{role}", "spec", "independent_math.v1", f"baseline Q{q}; no invented thresholds", kind="semantic_rubric",
                    scope={"domains": ["math_logic"], "roles": [role], "requires_items": True})
        runtime = task.get("runtime_target", "").lower().replace(" ", "")
        if runtime in {"ps5.1", "powershell5.1", "powershell-5.1-desktop", "5.1/desktop"} and "cs_it" in domains:
            add(f"P.execution.{role}", "spec", "ps51_sandbox.v1", "PS5.1 claims only; verified isolated runtime required", kind="atomic_claim",
                scope={"domains": ["cs_it"], "roles": [role], "runtime_target": runtime})
        elif runtime:
            add(f"P.execution.{role}", "spec", "target_runtime.v1", "only the declared target runtime may verify executable claims", kind="atomic_claim",
                scope={"roles": [role], "runtime_target": runtime})
        add(f"S.format.{role}", "spec", "spec_fields.v1", "frozen required/forbidden format requirements are individually evaluated",
            kind="semantic_rubric", scope={"roles": [role]})

    derivation_targets = []
    item_roles = [
        role for role, binding in roles.items()
        if canonical_scene(binding["scene"]) in {"exercise", "exam_paper"}
    ]
    if (declared_task_domains
            and set(declared_task_domains) <= set(DIFFICULTY_AXIS_DOMAINS)
            and item_roles):
        by_id = {item["item_id"]: item for item in items}
        blueprints = {
            "C3.correctness": [
                {"check_id": "Q1.{role}", "clauses": ["premises", "solvability"]},
                {"check_id": "Q2.{role}", "clauses": ["answer", "reasoning"]},
            ],
            "C4.constraints": [
                {"check_id": "Q3.{role}", "clauses": ["coverage", "completeness"]},
            ],
            "G3.assessment_design": [
                {"check_id": "Q4.{role}", "clauses": ["core_method", "transfer"]},
            ],
        }
        for target_id, source_blueprints in blueprints.items():
            sources = []
            complete = target_id in by_id
            for role in item_roles:
                for source_blueprint in source_blueprints:
                    source_id = source_blueprint["check_id"].format(role=role)
                    source = by_id.get(source_id)
                    if source is None:
                        complete = False
                        break
                    sources.append({
                        "check_id": source_id,
                        "role": role,
                        "clauses": list(source_blueprint["clauses"]),
                    })
                if not complete:
                    break
            if complete and sources:
                target = by_id[target_id]
                target["derivation_target"] = True
                target["derivation_source_ids"] = [source["check_id"] for source in sources]
                derivation_targets.append({
                    "target_id": target_id,
                    "sources": sources,
                })
    seen_rules = set()
    for profile in task.get("quality_profiles", []):
        for rule in profile["rules"]:
            if rule["rule_id"] in seen_rules:
                continue
            seen_rules.add(rule["rule_id"])
            rule_domain = canonical_domain_key(profile["domain_key"])
            add(rule["rule_id"], "discipline", "independent_oracle.v1", rule["criterion"], kind=rule["decision_type"],
                scope={"domains": [rule_domain], "scenes": [canonical_scene(profile["scene"])]})
            items[-1]["source_rule"] = {"rule_id": rule["rule_id"], "version": rule["version"], "source_ref": rule["source_ref"]}
        # Item quality is only applicable to item-bearing artifacts.  The
        # common G1-G4 checks above cover all tutorial units; these rows add
        # the declared domain distinction without applying mathematics rules
        # to unrelated subjects.
        rubric = profile.get("item_quality_rubric") or {}
        for dimension in rubric.get("dimensions", []):
            dimension_id = dimension.get("id")
            if not dimension_id or profile.get("scene") not in {"exercise", "exam", "exam_paper"}:
                continue
            profile_domain = canonical_domain_key(profile["domain_key"])
            profile_scene = canonical_scene(profile["scene"])
            item_id = f"I.{profile_domain}.{dimension_id}.{profile_scene}"
            if any(existing["item_id"] == item_id for existing in items):
                continue
            add(item_id, "discipline", "criterion_review.v1",
                "0=confirmed defect; 1=partial evidence/limitation remains reviewable; 2=all applicable clauses supported; missing evidence=unknown",
                kind="semantic_rubric", scope={"domains": [profile_domain],
                    "scenes": [profile_scene], "requires_items": True})
            items[-1]["quality_dimension"] = {"id": dimension_id, "criterion": dimension.get("criterion"),
                                                "name": dimension.get("name"), "source_ref": dimension.get("source_ref")}
    # Public research rubrics are frozen task assets, not global default
    # dimensions. C executes these rows directly and can only pass them with
    # literal candidate evidence; missing evidence remains undecidable.
    for binding in task.get("research_rubrics", []):
        if not isinstance(binding, dict):
            conflicts.append({
                "code": "RESEARCH_RUBRIC_BINDING_INVALID",
                "reason": "binding_must_be_an_object",
            })
            continue
        rubric = binding.get("content", {})
        if not isinstance(rubric, dict):
            conflicts.append({
                "code": "RESEARCH_RUBRIC_CONTENT_INVALID",
                "research_rubric_id": binding.get("id"),
                "reason": "content_must_be_an_object",
            })
            continue
        frozen_scope = binding.get("scope")
        if not isinstance(frozen_scope, dict):
            frozen_scope = rubric.get("applicability_scope")
        if not isinstance(frozen_scope, dict):
            frozen_scope = rubric.get("scope")
        if not isinstance(frozen_scope, dict):
            frozen_scope = {}
        research_domains = sorted({
            canonical_domain_key(str(domain))
            for domain in frozen_scope.get("domains") or []
            if isinstance(domain, str) and domain.strip()
        })
        legacy_scope_derived = False
        if not research_domains and rubric.get("domain_key"):
            research_domains = [canonical_domain_key(str(rubric["domain_key"]))]
        if not research_domains and len(declared_task_domains) == 1:
            research_domains = declared_task_domains
            legacy_scope_derived = True
        if not research_domains:
            conflicts.append({
                "code": "RESEARCH_RUBRIC_SCOPE_AMBIGUOUS",
                "research_rubric_id": rubric.get("rubric_id") or binding.get("id"),
                "reason": "legacy_or_external_rubric_has_no_auditable_unique_domain",
            })
            continue
        research_scope = canonical_scope({
            **frozen_scope,
            "domains": research_domains,
        })
        rubric_ref = {
            **{k: binding[k] for k in ("id", "version", "hash") if k in binding},
            "canonical_domain_key": research_domains[0] if len(research_domains) == 1 else None,
            "scope": research_scope,
            "legacy_scope_derived": legacy_scope_derived,
        }
        gaps = rubric.get("scope_gaps", [])
        gaps = gaps if isinstance(gaps, list) else []
        gap_id = "R.scope_completion." + str(rubric.get("rubric_id") or "research")
        add(gap_id, "research", "research_scope_completion.v1",
            "all research-derived scope dimensions applicable before learner-content checks",
            kind="deterministic", scope=research_scope)
        items[-1]["scope_gaps"] = deepcopy(gaps)
        items[-1]["scope_completion_status"] = rubric.get("scope_completion_status")
        items[-1]["research_rubric_ref"] = deepcopy(rubric_ref)
        items[-1]["applicability_reason"] = "research_rubric_task_scope"
        dimension_scopes = rubric.get("dimension_scopes", {})
        dimension_scopes = dimension_scopes if isinstance(dimension_scopes, dict) else {}
        for dimension in rubric.get("dimensions", []):
            dimension_id = dimension.get("dimension_id") or dimension.get("id")
            if not dimension_id:
                continue
            dimension_scope = dimension_scopes.get(dimension_id)
            dimension_scope = canonical_scope({
                **research_scope,
                **(dimension_scope if isinstance(dimension_scope, dict) else {}),
            })
            add(f"R.{dimension_id}", "research", "research_rubric.v1",
                "the research-derived dimension is executed with evidence and no unknown result",
                kind="semantic_rubric", scope=dimension_scope)
            items[-1]["research_dimension_id"] = dimension_id
            items[-1]["research_rubric_ref"] = deepcopy(rubric_ref)
            items[-1]["applicability_reason"] = (
                "legacy_research_rubric_scope_derived_from_unique_task_domain"
                if legacy_scope_derived else "research_rubric_task_scope"
            )
    # Quality-layer annotations are part of the plan itself, so the
    # check_plan_hash binds the executed strategy (quality-layers.v1).
    for item in items:
        layer = quality_layer_for(item)
        item["quality_layer"] = layer
        item["hard_baseline"] = layer == "correctness"
    from .check_criteria import bind_criteria, VERSION
    bind_criteria(items)
    if derivation_targets:
        for target in derivation_targets:
            for source in target["sources"]:
                source_item = next(
                    item for item in items
                    if item["item_id"] == source["check_id"]
                )
                source["criterion_hash"] = source_item["criterion_contract"]["criterion_hash"]
        derivation_contract = {
            "schema_version": "aiedu.derivation-contract.v1",
            "method_version": "assessment-source-derivation.v1",
            "enabled": True,
            "canonical_domain": "math_logic",
            "requires_items": True,
            "target_ids": [target["target_id"] for target in derivation_targets],
            "targets": derivation_targets,
        }
    else:
        derivation_contract = {
            "schema_version": "aiedu.derivation-contract.v1",
            "method_version": "assessment-source-derivation.v1",
            "enabled": False,
            "reason": "not_math_item_plan_or_sources_incomplete",
        }
    research = network_policy or task.get("research_policy") or {
        "method_version": "task-research-policy.v1",
        "enabled": task.get("research_request", {}).get("enabled") is True,
    }
    plan = {"schema_version": "aiedu.check-plan.v2", "assessment_contract": VERSION, "check_items": items, "conflicts": conflicts,
            "spec_hash": digest(bindings), "constraints_hash": digest(task.get("hard_constraints", [])),
            "research_policy": deepcopy(research), "derivation_contract": derivation_contract}
    if quality_layers is not None:
        plan["quality_layers"] = deepcopy(quality_layers)
    plan["check_plan_hash"] = digest(plan)
    return plan


def finding(item: dict, plan: dict, unit: dict, version_id: str, start: int, end: int, claim: str,
            raw_output: dict, verified: bool = True) -> dict:
    location = {"unit_id": unit["unit_id"], "version_id": version_id, "unit_hash": unit["content_hash"],
                "start_byte": start, "end_byte": end, "span_hash": sha(unit["body"].encode()[start:end])}
    method = item["verifier_binding"]
    return {"finding_id": str(uuid4()), "item_id": item["item_id"], "check_plan_hash": plan["check_plan_hash"],
            "system": item["system"], "severity_class": item["severity_class"], "location": location,
            "claim": claim, "evidence": raw_output, "verification_method": method,
            "verification_result": "violated" if verified else "undecidable", "method_version": method,
            "tool_raw_output_hash": digest(raw_output), "confidence": None, "suggested_fix": None,
            "model_family": None, "input_hash": unit["content_hash"],
            "normalized_claim_hash": digest([item["item_id"], " ".join(claim.split()),
                {k: location[k] for k in ("unit_id", "start_byte", "end_byte")}, method]),
            "attempt": 1, "status": "confirmed" if verified else "suspected", "reviewed_by": None,
            "review_evidence": [], "context_conflict": False, "category": "structure"}


def reduce_quality(plan: dict, checks: list[dict], findings: list[dict], calibration="not_calibrated") -> dict:
    # Disabled-layer rows are normalized to an honest policy-skipped state here:
    # the single reducer owns the "not passed, skipped by policy" semantics so
    # no dispatch path can render a disabled check as satisfied.
    layers = plan.get("quality_layers")
    if layers:
        disabled = {i["item_id"]: i for i in plan.get("check_items", []) if not layer_enabled(i, layers)}
        present = {c.get("item_id") for c in checks}
        for c in checks:
            if c.get("item_id") in disabled and c.get("result") not in {"satisfied", "violated"}:
                c.update(result="not_applicable", reason="quality_layer_disabled_by_policy")
        for item_id, item in disabled.items():
            if item_id not in present:
                checks.append(disabled_layer_check(item))
    by_id = {c["item_id"]: c for c in checks}
    reasons, completed, violations = [], 0, []
    for item in plan.get("check_items", []):
        result = by_id.get(item["item_id"], {"result": "not_run"})["result"]
        completed += result in {"satisfied", "violated", "not_applicable"}
        if item.get("required") and result not in {"satisfied", "not_applicable"}:
            reasons.append({"item_id": item["item_id"], "result": result})
        if result == "violated" and (item.get("required") or item["severity_class"] == "blocker"):
            violations.append(item["item_id"])
    confirmed = [f for f in findings if f.get("status") == "confirmed"
                 and f.get("severity_class") == "blocker"]
    unavailable = any(item.get("required") and by_id.get(item["item_id"],{"result":"not_run"})["result"]
                      not in {"satisfied","violated","not_applicable"} for item in plan.get("check_items",[]))
    if not plan.get("check_items") or unavailable or plan.get("conflicts") or any(
            f.get("status") in {"suspected", "unsupported", "escalated"} for f in findings):
        verdict, legacy = "blocked", "needs_review"
    elif violations or confirmed:
        verdict, legacy = "needs_repair", "failed"
    elif any(not item.get("required") and by_id.get(item["item_id"],{}).get("result") != "satisfied" for item in plan.get("check_items",[])):
        verdict, legacy = "conditional_pass", "needs_review"
    elif calibration != "calibrated":
        # Execute checks and repairs regardless of calibration, but keep the
        # user's final-delivery policy: diagnostics alone cannot certify pass.
        verdict, legacy = "not_calibrated", "needs_review"
    else:
        verdict, legacy = "pass", "passed"
    if violations or confirmed:
        legacy = "failed"  # not_calibrated/blocked never hides a known severe failure.
    if not checks and not findings:
        legacy = "not_evaluated"
    return {"tqi_verdict": verdict, "calibration_status": calibration, "quality_status": legacy,
            "benchmark_build_status": "not_run", "evaluation_phase": "running" if any(c["result"] == "not_run" for c in checks) else "completed" if checks else "pending",
            "check_coverage": {"completed": completed, "total": len(plan.get("check_items", []))},
            "blocking_reasons": reasons + [{"finding_id": f["finding_id"], "result": "violated"} for f in confirmed],
            "check_plan_hash": plan.get("check_plan_hash"), "check_items": plan.get("check_items", []),
            "checks": checks, "findings": findings, "repair_policy": POLICY}


def deterministic_checks(plan: dict, units: list[dict], version_id: str, materials_complete=True,
                         expected_unit_ids=None) -> dict:
    checks, findings = [], []
    ids = [u["unit_id"] for u in units]
    stale_units = [u["unit_id"] for u in units if u.get("content_hash") != sha(u["body"])]
    for item in plan["check_items"]:
        if not layer_enabled(item, plan.get("quality_layers")):
            checks.append(disabled_layer_check(item))
            continue
        method = item["verifier_binding"]
        if method == "coverage.v1":
            passed = bool(units) and all(u.get("body", "").strip() for u in units)
            if expected_unit_ids is not None:
                passed = passed and set(ids) == set(expected_unit_ids) and len(ids) == len(set(ids))
        elif method == "source_manifest.v1":
            passed = materials_complete
        elif method == "markdown.v1":
            passed = bool(units) and len(ids) == len(set(ids))
            for unit in units:
                fences = re.findall(r"^\s*(`{3,}|~{3,})", unit["body"], flags=re.M)
                opened = None
                for fence in fences:
                    if opened is None:
                        opened = fence
                    elif fence[0] == opened[0] and len(fence) >= len(opened):
                        opened = None
                if opened is not None:
                    passed = False
                    f = finding(item, plan, unit, version_id, 0, len(unit["body"].encode()),
                                "代码围栏未闭合", {"unclosed_fence": opened})
                    f["acceptance_assertion"] = {"method": "markdown.v1", "kind": "balanced_fences"}
                    f["category"] = "unclosed_fence"
                    findings.append(f)
        elif method == "items.v1":
            matching = [u for u in units if u["artifact_role"] in item.get("scope", {}).get("roles", [])]
            passed = bool(matching) and all(u.get("items") and all(i.get("stem_markdown") and
                i.get("answer", {}).get("final") and i.get("answer", {}).get("reasoning") and
                ((u["artifact_role"] == "paper" and type(i.get("score")) in {int,float} and i["score"] > 0) or
                 (u["artifact_role"] == "exercise" and "score" not in i)) for i in u["items"]) for u in matching)
        elif method == "inherited_layout.v1":
            from .format_runtime import check_layout
            matching = [u for u in units if u.get("format_contract") == item["scope"]["format_contract"]]
            passed = bool(matching) and all(check_layout(u) for u in matching)
        elif method == "research_scope_completion.v1":
            gaps = item.get("scope_gaps")
            if not isinstance(gaps, list):
                result, reason = "violated", "RESEARCH_SCOPE_COMPLETION_CONTRACT_INVALID"
                gaps = []
            elif item.get("scope_completion_status") == "complete" and not gaps:
                result, reason = "satisfied", "RESEARCH_RUBRIC_SCOPE_COMPLETE"
            elif gaps and all(
                    (gap.get("decision") or {}).get("result") == "not_applicable"
                    for gap in gaps if isinstance(gap, dict)):
                result, reason = "not_applicable", "RESEARCH_RUBRIC_SCOPE_EXCLUDED"
            elif item.get("scope_completion_status") == "partial_unresolved" and gaps:
                result, reason = "violated", "RESEARCH_RUBRIC_SCOPE_PARTIAL"
            else:
                result, reason = "violated", "RESEARCH_SCOPE_COMPLETION_CONTRACT_INVALID"
            checks.append({"item_id": item["item_id"], "result": result, "reason": reason,
                           "method_version": method,
                           "scope_gaps": deepcopy(gaps),
                           "scope_completion_status": item.get("scope_completion_status"),
                           "research_rubric_ref": deepcopy(item.get("research_rubric_ref", {}))})
            continue
        elif method == "criterion_review.v1":
            checks.append({"item_id": item["item_id"], "result": "not_run",
                           "reason": "CRITERION_ASSESSMENT_NOT_STARTED"})
            continue
        else:
            checks.append({"item_id": item["item_id"], "result": "not_run", "reason": "independent verifier unavailable"})
            continue
        checks.append({"item_id": item["item_id"], "result": "satisfied" if passed else "violated", "method_version": method})
    if stale_units:
        for check in checks:
            check.update(result="undecidable",reason="INPUT_HASH_MISMATCH",unit_ids=stale_units)
    for f in findings:
        # The deterministic adapter owns location/input/method/output; model text
        # cannot set any of these. Only now upgrade the L1 candidate.
        unit = next(u for u in units if u["unit_id"] == f["location"]["unit_id"])
        location = f["location"]
        if unit.get("content_hash") != sha(unit["body"]):
            f.update(status="unsupported",verification_result="undecidable",location_status="needs_evidence")
        else:
            f.update(reviewed_by="L3.deterministic-wrapper",location_status="located",
                review_evidence=[{"method_version":f["method_version"],"input_hash":sha(unit["body"]),"span_hash":location["span_hash"],"wrapper_validated":True}])
    return reduce_quality(plan, checks, findings)


def check_snapshot(state, units, version_id):
    """Coverage derives from the frozen plan and wrapper-owned injection records."""
    expected = {u["unit_id"] for u in state["plan"]["units"] if u.get("required", True)}
    required_chunks = {c for u in state["plan"]["units"] for c in u.get("material_chunk_ids", [])}
    injected = {c for u in units for c in u.get("injected_chunk_ids", [])}
    result = deterministic_checks(state["check_plan"], units, version_id,
                                materials_complete=required_chunks <= injected,
                                expected_unit_ids=expected)
    conflicts = context_conflicts(state.get("context",{}),state["check_plan"],version_id)
    context_item = next((c for c in result["checks"] if c["item_id"] == "C6.context_integrity"),None)
    if context_item:
        context_item.update(result="undecidable" if conflicts else "satisfied",method_version="context-directive.v1",
            reason="lexical_directive_requires_review_no_authority_granted" if conflicts else "no_known_directive_signature; authority_is_enforced_by_call_gate")
    result = reduce_quality(state["check_plan"],result["checks"],result["findings"]+conflicts)
    result["material_coverage"] = {"selected": len(required_chunks), "injected": len(required_chunks & injected),
                                   "consumed": "not_run", "verified": "not_run"}
    from .assessment_assets import quantity_summary
    result["item_quality"] = quantity_summary(state, units)
    return result


def context_conflicts(context,plan,version_id):
    """Source-bound *candidates*, not a claim of general injection detection.

    Lexical matches are retained as unsupported with precise source offsets.
    They never become repairable tutorial defects or new runtime permissions.
    """
    item = next((i for i in plan["check_items"] if i["item_id"] == "C6.context_integrity"),None)
    if not item:
        return []
    sources = []
    for binding in context.get("spec_bindings",[]):
        snapshot = binding.get("snapshot",{})
        sources.append(("spec",binding.get("spec_id") or digest(snapshot),__import__("json").dumps(snapshot,ensure_ascii=False)))
    for value in context.get("knowledge_sources",[]):
        sources.append(("knowledge",value["id"],value.get("guidance", "")))
    if context.get("material_ids"):
        from ..db import SessionLocal
        from ..models import GoalMaterial
        from .storage import BlobStore
        from .runtime import load
        with SessionLocal() as db:
            for mid in context["material_ids"]:
                material = db.get(GoalMaterial,mid)
                if material and material.extract_status == "ok":
                    manifest = load(material.manifest_json)
                    sources.append(("material",mid,BlobStore().get(manifest["extracted_hash"]).decode()))
    pattern = re.compile(r"忽略(?:所有|之前|先前|以上|系统)(?:的)?(?:规则|指令|要求)|泄露(?:模型)?(?:密钥|API密钥)|ignore\s+(?:all\s+|the\s+)?(?:previous|system)\s+(?:instructions|rules)",re.I)
    results = []
    for kind,source_id,text in sources:
        source_unit = {"unit_id":"source:"+str(source_id),"content_hash":sha(text),"body":text}
        for match in pattern.finditer(text):
            start,end = len(text[:match.start()].encode()),len(text[:match.end()].encode())
            f = finding(item,plan,source_unit,version_id,start,end,"不可信来源含指令式文本；不授予权限，待复核上下文",
                {"quote":match.group(),"source_id":source_id,"source_kind":kind,"source_hash":sha(text)},verified=False)
            f.update(context_conflict=True,status="unsupported",verification_result="undecidable",category="context_conflict",
                reviewed_by="L1.source-directive-signature",location_status="located_in_source")
            f["location"].update(source_id=source_id,source_kind=kind)
            results.append(f)
    return results


def repair_contract(f: dict, unit: dict, base_version: str, plan_hash: str, forbidden_spans: list[tuple[int, int]]) -> dict:
    if unit.get("items"):
        raise DomainError("STRUCTURED_ITEM_REPAIR_UNSUPPORTED", 422)
    loc = f["location"]
    if (f["status"] != "confirmed" or not f.get("tool_raw_output_hash") or not f.get("acceptance_assertion")
            or f["verification_result"] != "violated" or f["check_plan_hash"] != plan_hash):
        raise DomainError("NOT_ELIGIBLE", 422)
    if loc["version_id"] != base_version or loc["unit_hash"] != unit["content_hash"]:
        raise DomainError("FINDING_STALE")
    start, end = loc["start_byte"], loc["end_byte"]
    raw = unit["body"].encode()
    if not 0 <= start < end <= len(raw) or sha(raw[start:end]) != loc["span_hash"]:
        raise DomainError("NOT_LOCATABLE", 422)
    if any(start < b and a < end for a, b in forbidden_spans):
        raise DomainError("PROTECTED_SPAN", 422)
    templates = {"unclosed_fence": "Close the unmatched Markdown code fence without changing existing claims.",
                 "polynomial_identity": "Replace only the literal polynomial equation; preserve its left side and make the right side identically equal. No extra prose or new claim."}
    if f.get("category") not in templates:
        raise DomainError("UNSUPPORTED_REPAIR_CATEGORY", 422)
    return {"finding_id": f["finding_id"], "base_version": base_version, "check_plan_hash": plan_hash,
            "input_hash": unit["content_hash"], "edit_scope": [start, end], "claim": f["claim"],
            "evidence": f["evidence"], "required_change": templates[f["category"]],
            "acceptance_assertion": f["acceptance_assertion"], "policy_version": POLICY["policy_version"]}


def apply_patch(unit: dict, contract: dict, search: str, replacement: str) -> dict:
    raw, needle = unit["body"].encode(), search.encode()
    start, end = contract["edit_scope"]
    if unit["content_hash"] != contract["input_hash"] or not needle or raw[start:end].count(needle) != 1:
        raise DomainError("PATCH_REJECTED", 422)
    position = raw.find(needle, start, end)
    changed = raw[:position] + replacement.encode() + raw[position + len(needle):]
    suffix_size = len(raw) - end
    if changed[:start] != raw[:start] or (suffix_size and changed[-suffix_size:] != raw[end:]):
        raise DomainError("SCOPE_VIOLATION", 422)
    result = deepcopy(unit)
    result["body"] = changed.decode("utf-8")
    result["content_hash"] = sha(changed)
    return result


def dependency_closure(units: list[dict], changed: set[str]) -> dict:
    graph = {u["unit_id"]: set(u.get("depends_on", [])) for u in units}
    if any(not deps <= graph.keys() for deps in graph.values()):
        return {"unit_ids": sorted(graph), "full_semantic_rerun": True, "reason": "missing_edge"}
    affected = set(changed)
    for key in changed:
        affected.update(graph.get(key, []))
    while True:
        following = affected | {key for key, deps in graph.items() if deps & affected}
        if following == affected:
            break
        affected = following
    return {"unit_ids": sorted(affected), "full_semantic_rerun": False}


def progress_fingerprint(units: list[dict], findings: list[dict], checks: list[dict]) -> str:
    return digest({"units": [(u["unit_id"], u["content_hash"]) for u in units],
                   "unresolved": sorted(f["normalized_claim_hash"] for f in findings if f["status"] not in {"resolved", "rejected"}),
                   "checks": sorted((c["item_id"], c["result"]) for c in checks)})

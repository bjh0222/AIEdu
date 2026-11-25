"""Single task application service: receipts, immutable contexts and read snapshots."""
from __future__ import annotations

import base64
import os
import json
import time
import threading
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from ..config import settings
from ..db import SessionLocal
from ..models import GenerationJob, GoalMaterial, LearningGoal, Tutorial, TutorialVersion
from .contracts import DomainError, digest, sha
from .quality import compile_plan, reduce_quality
from .runtime import cas, dump, job, load, materialize, normalize_stopped_run_calls, write
from .storage import CallDispatch, ExportSet
from .resolver import bind_specs, build_units, resolve_task

_start_lock = threading.RLock()


def goal(db, task_id):
    obj = db.get(LearningGoal, task_id)
    if obj is None or not load(obj.task_state_json).get("schema_version"):
        raise DomainError("TASK_NOT_FOUND", 404)
    return obj


def receipt(state, key, body_hash):
    old = state.get("receipts", {}).get(key)
    if old is not None and old["request_hash"] != body_hash:
        raise DomainError("IDEMPOTENCY_CONFLICT")
    return old["response"] if old else None


def remember(state, key, body_hash, response, kind):
    state.setdefault("receipts", {})[key] = {"request_hash": body_hash, "response": response, "action_kind": kind}


def context_of(payload, revision):
    value = payload.model_dump() if hasattr(payload, "model_dump") else dict(payload)
    value.update(schema_version="aiedu.task-run.v2", input_revision=revision, raw_input_hash=sha(value["raw_input"]))
    value["context_hash"] = digest(value)
    return value


def research_policy(context):
    """Freeze the requested research mode as a deterministic task identity."""
    enabled = context.get("research_request", {}).get("enabled") is True
    return {
        "method_version": "task-research-policy.v1",
        "enabled": enabled,
        "when_disabled": "no_public_web_retrieval_or_planning_source_tools",
    }


def live_context(db, context):
    """New bindings exclude trash; old frozen contexts remain unchanged."""
    from .management_lifecycle import overlay_ids
    hidden = {kind: set(db.scalars(overlay_ids(kind))) for kind in ("materials", "principles", "questions")}
    if hidden["materials"].intersection(context.get("material_ids", [])) or hidden["questions"].intersection(context.get("knowledge", {}).get("question_ids", [])):
        raise DomainError("RESOURCE_TRASHED", 409)
    return {**context, "knowledge_sources": [s for s in context.get("knowledge_sources", []) if s.get("id") not in hidden["principles"]]}


def require_live_task(db, task_id):
    from .insights import record
    if record(db, "managed:tasks:"+task_id, {}).get("trashed"):
        raise DomainError("RESOURCE_TRASHED", 409)


def create_task(payload, key, explicit_fields=None):
    body = digest(payload.model_dump())
    with write() as db:
        existing = db.query(LearningGoal).filter_by(creation_key=key).first()
        if existing:
            if existing.request_hash != body:
                raise DomainError("IDEMPOTENCY_CONFLICT")
            return load(existing.task_state_json)["creation_receipt"]
        resolved = resolve_task(payload.model_dump(), explicit_fields if explicit_fields is not None else payload.model_fields_set)
        resolved["research_policy"] = research_policy(resolved)
        from .knowledge import freeze_guidance
        from .insights import freeze_profile
        from .import_profiles import freeze_import_profiles
        from .understanding_assets import freeze_subject_understanding
        from .knowledge_assets import freeze_evaluation_frameworks
        frozen = freeze_import_profiles(db, freeze_profile(db, freeze_guidance(db, bind_specs(db, resolved))))
        frozen = freeze_evaluation_frameworks(db, frozen)
        context = context_of(live_context(db, freeze_subject_understanding(frozen)), 1)
        plan = compile_plan(context)
        task_id = str(uuid4())
        response = {"task_id": task_id, "input_revision": 1, "context_hash": context["context_hash"],
                    "task_state": "awaiting_input" if plan["conflicts"] else "draft", "conflicts": plan["conflicts"]}
        state = {"schema_version": "aiedu.task-run.v2", "context": context, "history": [],
                 "task_state": response["task_state"], "creation_receipt": response, "receipts": {}}
        remember(state, key, body, response, "create")
        db.add(LearningGoal(id=task_id, title=context["title"], domain_key=context["domain_key"], branch=context["branch"],
                           hard_constraints="\n".join(payload.hard_constraints), input_revision=1,
                           difficulty_mode=payload.difficulty_mode, difficulty_note=payload.difficulty_note,
                           task_state_json=dump(state), creation_key=key, request_hash=body))
        return response


def mutate_input(task_id, payload, key, confirmation=False):
    body = digest(payload.model_dump())
    with write() as db:
        obj = goal(db, task_id)
        state = load(obj.task_state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        require_live_task(db, task_id)
        if obj.input_revision != payload.expected_revision:
            raise DomainError("CONTEXT_STALE", current_revision=obj.input_revision)
        if db.query(GenerationJob).filter(GenerationJob.goal_id == task_id, GenerationJob.job_type == "task_v2",
                GenerationJob.status.in_(["running", "queued"])).first():
            raise DomainError("ACTIVE_RUN")
        if confirmation:
            if payload.context_hash != state["context"]["context_hash"]:
                raise DomainError("CONTEXT_STALE")
            plan = compile_plan(state["context"])
            if plan["conflicts"]:
                raise DomainError("UNRESOLVED_CONFLICT", conflicts=plan["conflicts"])
            missing = []
            for mid in state["context"]["material_ids"]:
                material = db.get(GoalMaterial, mid)
                from .insights import record
                if material is None or material.goal_id != task_id or material.extract_status != "ok" or record(db, "managed:materials:"+mid, {}).get("trashed"):
                    missing.append(mid)
            if missing:
                raise DomainError("MATERIAL_PREFLIGHT_FAILED", missing_source_ids=missing)
            state["task_state"] = "confirmed"
            state["confirmed_context_hash"] = payload.context_hash
        else:
            state["history"].append(state["context"])
            obj.input_revision += 1
            from .knowledge import freeze_guidance
            from .insights import freeze_profile
            from .import_profiles import freeze_import_profiles
            from .understanding_assets import freeze_subject_understanding
            from .knowledge_assets import freeze_evaluation_frameworks
            resolved = resolve_task(payload.input.model_dump(), payload.input.model_fields_set)
            resolved["research_policy"] = research_policy(resolved)
            frozen = freeze_import_profiles(db, freeze_profile(db, freeze_guidance(db, bind_specs(db, resolved))))
            frozen = freeze_evaluation_frameworks(db, frozen)
            state["context"] = context_of(live_context(db, freeze_subject_understanding(frozen)), obj.input_revision)
            obj.title = payload.input.title
            state["task_state"] = "draft"
        response = {"task_id": task_id, "input_revision": obj.input_revision, "task_state": state["task_state"],
                    "context_hash": state["context"]["context_hash"]}
        remember(state, key, body, response, "confirm" if confirmation else "input")
        obj.task_state_json = dump(state)
        return response


def start_run(task_id, payload, key, automatic=False, envelope=None):
    body = digest(payload.model_dump() if envelope is None else {"payload":payload.model_dump(),"envelope":envelope})
    with write() as db:
        obj = goal(db, task_id)
        state = load(obj.task_state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        require_live_task(db, task_id)
        live_context(db, state["context"])
        if obj.input_revision != payload.expected_revision or payload.confirmed_context_hash != state.get("confirmed_context_hash") or state["task_state"] != "confirmed":
            raise DomainError("CONTEXT_STALE")
        if payload.mode == "configured" and not settings.v2_allow_paid:
            raise DomainError("PAID_RUN_NOT_AUTHORIZED", 403)
        if db.query(GenerationJob).filter(GenerationJob.goal_id == task_id, GenerationJob.job_type == "task_v2",
                GenerationJob.status.in_(["running", "queued"])).first():
            raise DomainError("ACTIVE_RUN")
        run_id, root_id, artifact_id = str(uuid4()), str(uuid4()), str(uuid4())
        context = state["context"]
        chunks = []
        if context["material_ids"]:
            # Partition every required source chunk, preserving full coverage across calls.
            for source_id in context["material_ids"]:
                material = db.get(GoalMaterial, source_id)
                if material is None or material.goal_id != task_id or not material.manifest_json:
                    raise DomainError("MATERIAL_INCOMPLETE")
                chunks.extend(load(material.manifest_json).get("chunks", []))
            if not chunks:
                raise DomainError("MATERIAL_INCOMPLETE")
        requested_policy = context.get("execution_policy", {})
        research_enabled = context.get("research_request", {}).get("enabled") is True
        requested_planning_sources = requested_policy.get("planning_source_tools", True)
        planning_sources = requested_planning_sources
        if type(planning_sources) is not bool:
            raise DomainError("PLANNING_SOURCE_POLICY_INVALID", 422)
        requested_rounds = requested_policy.get("network_tool_rounds", 3)
        if type(requested_rounds) is not int or not 0 <= requested_rounds <= 12:
            raise DomainError("NETWORK_TOOL_POLICY_INVALID", 422)
        network_enabled = payload.mode == "configured" and research_enabled
        planning_sources = network_enabled and planning_sources
        rounds = requested_rounds if network_enabled else 0
        network_policy = {
            "method_version": "run-network-policy.v1",
            "research_requested": research_enabled,
            "network_enabled": network_enabled,
            "planning_source_tools": planning_sources,
            "network_tool_rounds": rounds,
            "network_requests_per_round": requested_policy.get("network_requests_per_round", 4),
            "disabled_behavior": "all_public_web_tools_are_off",
        }
        adaptive = payload.mode == "configured" and bool(requested_policy.get("task_adaptive_format", True))
        # Do not first bind a new task to a legacy layout and only later ask A
        # to design it. Existing runs already contain their immutable plans.
        units = [] if adaptive else build_units(context, chunks, demo=payload.mode == "demo")
        plan = {"schema_version": "aiedu.plan.v1", "units": units, "network_policy": network_policy}
        plan["plan_hash"] = digest(plan)
        frozen = {"context": context, "plan": plan, "check_plan": compile_plan(context, network_policy), "mode": payload.mode,
                  "artifact_id": artifact_id, "version_id": None, "stages": {}, "stop_reason": None,
                  "intent_pending": (adaptive or automatic or requested_policy.get("structured_arrangement", False)) and payload.mode == "configured", "automatic": automatic,
                  "budget": {"calls": None, "tokens": None, "amount": None, "seconds": None}}
        from .review_contract import review_protocol_manifest
        frozen["quality_protocol"] = review_protocol_manifest()
        from ..model_settings import configured_concurrency
        concurrency = configured_concurrency()
        # Defaults belong to new configured runs, not to historical frozen runs.
        # Explicit opt-out/demo retain their contracts. A new root is unlimited;
        # resuming an old root still requires its explicit budget amendment.
        if requested_policy.get("quality_loop", True) and payload.mode == "configured":
            from .optimization import validate_policy
            from .quality import POLICY
            policy = validate_policy(requested_policy.get("optimization_policy") or
                {**POLICY, "policy_version": "aiedu.repair.v2", "root_call_budget": None})
            frozen["optimization_policy"] = policy
            frozen["budget"]["calls"] = policy["root_call_budget"]
            frozen["intent_pending"] = True
        frozen["execution_policy"] = {"version": "single-complete-check.v1", "provider_concurrency": concurrency,
                                      "check_concurrency": concurrency,
                                      "semantic_batch": True,
                                      "generation_concurrency": min(concurrency, max(1, int(context.get("execution_policy", {}).get("generation_concurrency", concurrency if automatic else 1)))),
                                      "network_enabled": network_enabled,
                                      "network_tool_rounds": rounds,
                                      "network_requests_per_round": context.get("execution_policy", {}).get("network_requests_per_round", 4),
                                      "network_stop_policy": "stop_after_budget_or_no_new_cached_evidence",
                                      "planning_source_tools": payload.mode == "configured" and planning_sources,
                                      "criterion_assessment": payload.mode == "configured" and bool(requested_policy.get("criterion_assessment", True)),
                                      "task_adaptive_format": adaptive}
        frozen["execution_policy"]["quality_loop"] = bool(frozen.get("optimization_policy"))
        if envelope is not None:
            frozen["benchmark_request"] = envelope
        db.add(Tutorial(id=artifact_id, goal_id=task_id, title=obj.title))
        db.add(GenerationJob(id=root_id, root_job_id=root_id, run_id=run_id, goal_id=task_id, job_type="task_v2",
                            status="queued", state_json=dump(frozen), action_key=key, request_hash=body,
                            state_revision=0, cancel_epoch=0))
        response = {"run_id": run_id, "root_job_id": root_id, "snapshot_url": f"/api/v2/tasks/{task_id}/snapshot?run_id={run_id}", "state_revision": 0}
        remember(state, key, body, response, "start")
        obj.task_state_json = dump(state)
        return response


def task_start(payload, key):
    with _start_lock:
        return _task_start(payload, key)


def _task_start(payload, key):
    """One public action, same durable run for understanding/planning and all follow-ups."""
    from .contracts import Confirmation, StartRun, TaskInput
    from ..model_settings import runtime_settings
    if payload.mode == "configured":
        if not settings.v2_allow_paid:
            raise DomainError("PAID_RUN_NOT_AUTHORIZED", 403)
        from .model_roles import for_role,role_config
        configured = for_role("generation") if role_config()[1].get("generation") else runtime_settings()
        if not configured.llm_configured:
            raise DomainError("MODEL_NOT_CONFIGURED", 422)
    value = payload.model_dump(exclude={"mode"})
    created = create_task(TaskInput.model_validate(value), key, payload.model_fields_set - {"mode"})
    with SessionLocal() as db:
        saved = load(goal(db, created["task_id"]).task_state_json)
        prior = saved.get("receipts", {}).get("start:"+digest(key))
        if prior:
            return prior["response"] | {"task_id": created["task_id"]}
    if created["conflicts"]:
        return created | {"run_id": None, "snapshot_url": f"/api/v2/tasks/{created['task_id']}/snapshot"}
    confirmed = mutate_input(created["task_id"], Confirmation(expected_revision=created["input_revision"],
                              context_hash=created["context_hash"]), "confirm:"+digest(key), confirmation=True)
    result = start_run(created["task_id"], StartRun(expected_revision=confirmed["input_revision"],
        confirmed_context_hash=confirmed["context_hash"], mode=payload.mode), "start:"+digest(key), automatic=True)
    return result | {"task_id": created["task_id"]}


def pause(run_id, payload, key):
    body = digest(payload.model_dump())
    with write() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        cas(obj, payload.expected_revision)
        if obj.status not in {"queued", "running", "interrupted", "awaiting_input"}:
            raise DomainError("RUN_NOT_PAUSABLE")
        obj.status, obj.cancel_epoch = "paused", obj.cancel_epoch + 1
        obj.state_revision += 1
        state["stop_reason"] = "user_pause"
        normalize_stopped_run_calls(db, obj)
        response = {"run_id": run_id, "state": "paused", "state_revision": obj.state_revision,
                    "version_id": state.get("version_id"), "checkpoint_hash": digest(state["plan"]),
                    "inflight_count": db.query(CallDispatch).filter_by(root_job_id=obj.id, status="sent").count(),
                    "bundle_status": "not_started", "available_draft": bool(state.get("version_id"))}
        remember(state, key, body, response, "pause")
        obj.state_json = dump(state)
        return response


def quarantine_unknown(run_id, payload, key):
    """Isolate exact unknown attempts without queuing work or settling a bill.

    This transaction is also safe to call from an explicit-path maintenance
    process before the serving worker can be reloaded. Resumption is a separate
    user action; neither an execution permit nor a provider receipt is invented.
    """
    body = digest(payload.model_dump(mode="json"))
    with write() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        cas(obj, payload.expected_revision)
        if not payload.acknowledge_possible_duplicate_cost:
            raise DomainError("UNKNOWN_QUARANTINE_REQUIRES_RISK_ACK", 422)
        if obj.status not in {"paused", "interrupted", "failed"}:
            raise DomainError("UNKNOWN_QUARANTINE_REQUIRES_STOPPED_RUN")
        if digest(state["plan"]) != payload.checkpoint_hash:
            raise DomainError("CHECKPOINT_STALE")
        require_live_task(db, obj.goal_id)
        if db.query(GenerationJob).filter(GenerationJob.goal_id == obj.goal_id,
                GenerationJob.status.in_(["queued", "running"])).first():
            raise DomainError("ACTIVE_RUN")
        ids = [str(entry.call_id) for entry in payload.calls]
        if len(ids) != len(set(ids)):
            raise DomainError("DUPLICATE_CALL_ID", 422)

        def receiptless_sent(call):
            try:
                receipt = load(call.receipt_json)
            except (TypeError, ValueError):
                return False
            return (not call.outcome and receipt == {}
                    and call.usage_json is None and call.price_json is None
                    and call.provider_request_id is None
                    and call.result_blob_hash is None
                    and not call.finished_at)

        selected = []
        for entry in payload.calls:
            call = db.get(CallDispatch, str(entry.call_id))
            if call is None or call.root_job_id != obj.id or call.run_id != run_id:
                raise DomainError("CALL_NOT_FOUND", 404)
            if call.request_hash != entry.request_hash:
                raise DomainError("CALL_REQUEST_MISMATCH")
            # A worker can terminate after marking a provider request sent but
            # before it can persist the provider receipt.  A failed or
            # interrupted run is a stopped execution boundary, so the explicit
            # user-authorized quarantine may convert that exact receipt-less
            # sent attempt to unknown.  Paused/active runs remain strict: they
            # must first receive a provider receipt or be stopped by the
            # worker lease reaper.  This does not infer failure, usage, price,
            # or provider settlement.
            if call.status == "sent" and obj.status in {"failed", "interrupted"}:
                if not receiptless_sent(call):
                    raise DomainError("CALL_SENT_RECEIPT_PRESENT")
                call.outcome = "unknown"
                call.receipt_json = dump({
                    "outcome": "unknown", "usage": None,
                    "provider_request_id": None, "result_hash": None,
                    "failure_code": "PROVIDER_TRANSPORT_UNKNOWN",
                    "failure_diagnostics": {"validation_reason": "WORKER_TERMINATED_WITHOUT_RECEIPT"},
                })
                call.usage_json = None
            elif call.status == "sent":
                raise DomainError("CALL_NOT_UNKNOWN")
            if call.status not in {"unknown", "sent"}:
                raise DomainError("CALL_NOT_UNKNOWN")
            selected.append(call)
        from ..models import ModelCallLog
        recorded_at = time.time()
        for call in selected:
            # For an already-unknown call only execution status changes.  A
            # failed-run receipt-less sent call was normalized above to an
            # explicit unknown receipt; quarantine itself never settles it.
            call.status = "quarantined"
            log = db.query(ModelCallLog).filter_by(call_id=call.call_id).first()
            if log is None:
                log = ModelCallLog(
                    call_id=call.call_id,
                    run_id=call.run_id,
                    unit_id=call.unit_id,
                    goal_id=obj.goal_id,
                    job_id=obj.id,
                    attempt=call.attempt,
                    feature="task_v2",
                    model=call.model,
                    provider=call.provider,
                    outcome="unknown",
                    status="quarantined",
                    usage_source="unknown",
                )
                db.add(log)
            usage = load(call.usage_json) if call.usage_json else {}
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = usage.get(field)
                if type(value) is int and value >= 0:
                    setattr(log, field, value)
            log.outcome = call.outcome or "unknown"
            log.status = "quarantined"
            log.usage_source = "provider" if call.usage_json else "unknown"
            state.setdefault("unknown_quarantines", {})[call.call_id] = {
                "method_version": "explicit-unknown-isolation.v1",
                "call_id": call.call_id, "request_hash": call.request_hash,
                "request_action_hash": body, "recorded_at": recorded_at,
                "acknowledge_possible_duplicate_cost": True,
                "provider_settlement": "unresolved", "scheduled": False,
            }
        obj.cancel_epoch += 1  # A delayed worker cannot publish into this run.
        obj.state_revision += 1
        response = {"run_id": run_id, "state": obj.status,
            "state_revision": obj.state_revision, "checkpoint_hash": digest(state["plan"]),
            "call_ids": ids, "scheduled": False, "provider_settlement": "unresolved"}
        remember(state, key, body, response, "quarantine_unknown")
        obj.state_json = dump(state)
        return response


def resume(run_id, payload, key):
    body = digest(payload.model_dump(mode="json"))
    with write() as db:
        old = job(db, run_id)
        state = load(old.state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        cas(old, payload.expected_revision)
        if state.get("synthesis_source") and (payload.recheck_completed or payload.replan or payload.refresh_check_plan):
            raise DomainError("SYNTHESIS_REQUIRES_SOURCE_BOUND_OPERATION", 422)
        if payload.enable_research and not payload.replan:
            raise DomainError("RESEARCH_ENABLE_REQUIRES_EXPLICIT_REPLAN", 422)
        allowed = old.status in {"paused", "interrupted", "failed"} or (old.status == "completed" and (payload.recheck_completed or payload.replan))
        if not allowed and old.status == "completed":
            # Historical defect: contract-error optimization stops were recorded
            # as "completed". They are not a clean finish and must stay
            # explicitly resumable without rewriting the old job row.
            from .worker import optimization_run_status
            allowed = optimization_run_status(state.get("optimization_stop_reason"))[0] == "failed"
        if not allowed or digest(state["plan"]) != payload.checkpoint_hash:
            raise DomainError("CHECKPOINT_STALE")
        from .review_contract import review_protocol_manifest
        frozen_protocol = state.get("quality_protocol")
        current_protocol = review_protocol_manifest()
        protocol_upgrade = None
        if frozen_protocol is not None and frozen_protocol != current_protocol:
            if not payload.quality_protocol_upgrade:
                raise DomainError("QUALITY_PROTOCOL_UPGRADE_REQUIRED", 409,
                                  frozen_manifest_hash=frozen_protocol.get("manifest_hash"),
                                  current_manifest_hash=current_protocol.get("manifest_hash"))
            protocol_upgrade = {"method_version": "explicit-quality-protocol-upgrade.v1",
                "previous_manifest_hash": frozen_protocol.get("manifest_hash"),
                "previous_method_version": frozen_protocol.get("method_version"),
                "current_manifest_hash": current_protocol.get("manifest_hash"),
                "current_method_version": current_protocol.get("method_version"),
                "request_hash": body, "source": "explicit_resume_acknowledgment"}
        if payload.recheck_completed and state.get("card_source"):
            raise DomainError("RECHECK_REQUIRES_GENERATION_RUN", 422)
        if payload.recheck_completed and old.status != "completed":
            raise DomainError("RECHECK_REQUIRES_COMPLETED_RUN", 422)
        if payload.refresh_check_plan and not payload.recheck_completed:
            raise DomainError("CHECK_PLAN_REFRESH_REQUIRES_FIXED_RECHECK", 422)
        if payload.replan and (not payload.acknowledge_new_generation or payload.recheck_completed or state.get("card_source")):
            raise DomainError("REPLAN_REQUIRES_EXPLICIT_GENERATION_ACK", 422)
        if db.query(GenerationJob).filter(GenerationJob.goal_id == old.goal_id, GenerationJob.job_type == "task_v2",
                GenerationJob.status.in_(["queued", "running"])).first():
            raise DomainError("ACTIVE_RUN")
        owner = goal(db, old.goal_id)
        require_live_task(db, old.goal_id)
        if load(owner.task_state_json)["context"]["context_hash"] != state["context"]["context_hash"]:
            raise DomainError("INPUT_CHANGED")
        if db.query(CallDispatch).filter_by(root_job_id=old.id).filter(
                CallDispatch.status.in_(["unknown", "sent"])).first():
            raise DomainError("UNKNOWN_QUARANTINE_REQUIRED")
        decisions = {str(r.call_id): r for r in payload.unknown_resolutions}
        if len(decisions) != len(payload.unknown_resolutions):
            raise DomainError("DUPLICATE_CALL_ID", 422)
        for call_id, decision in decisions.items():
            call = db.get(CallDispatch, call_id)
            if (call is None or call.root_job_id != old.id
                    or call.status != "quarantined"):
                raise DomainError("CALL_NOT_QUARANTINED", 422)
            if decision.action == "verify_provider_status":
                raise DomainError("PROVIDER_STATUS_UNSUPPORTED", 422)
            if decision.action == "retry_as_new_attempt":
                if not decision.acknowledge_possible_duplicate_cost:
                    raise DomainError("RETRY_REQUIRES_DUPLICATE_COST_ACK", 422)
                state.setdefault("retry_links", {})[call.unit_id] = call.call_id
        previous_version = db.get(TutorialVersion, state.get("version_id")) if state.get("version_id") else None
        current_artifact = db.get(Tutorial, state.get("artifact_id")) if state.get("artifact_id") else None
        if ((payload.recheck_completed or (payload.replan and state.get("version_id"))) and
                (not previous_version or not current_artifact or
                 current_artifact.current_version_id != previous_version.id)):
            raise DomainError("VERSION_MISMATCH")
        completed = {u["unit_id"] for u in materialize(previous_version).get("units", [])} if previous_version else set()
        if payload.recheck_completed and not {u["unit_id"] for u in state["plan"]["units"]} <= completed:
            raise DomainError("RECHECK_REQUIRES_COMPLETE_VERSION", 422)
        recovered = {} if payload.replan else deepcopy(state.get("recovered_candidates", {}))
        recovery_calls = [] if payload.replan else db.query(CallDispatch).filter_by(root_job_id=old.id, status="settled", outcome="success").order_by(CallDispatch.finished_at)
        for call in recovery_calls:
            manifest = load(call.manifest_json)
            if call.unit_id in completed or manifest.get("stage") not in {"generating", "generating_source_continuation"}:
                continue
            if call.input_hash != state["context"]["context_hash"] or manifest.get("plan_hash") != state["plan"]["plan_hash"]:
                raise DomainError("RECOVERY_CONTEXT_MISMATCH")
            if not call.result_blob_hash:
                raise DomainError("SETTLED_RESULT_REQUIRES_RECOVERY")
            from .storage import BlobStore
            candidate = load(BlobStore().get(call.result_blob_hash).decode())
            if candidate.get("tool_requests"):
                continue  # a paid tool decision is reusable, never a finished chapter.
            if not candidate.get("body") and not candidate.get("items"):
                continue
            recovered[call.unit_id] = {"call_id": call.call_id, "result_blob_hash": call.result_blob_hash,
                "injected_chunk_ids": [entry["chunk_id"] for entry in manifest.get("entries", []) if entry.get("status") == "injected"]}
        new_id, root_id = str(uuid4()), str(uuid4())
        new_state = deepcopy({k: v for k, v in state.items() if k != "receipts"})
        if payload.transport_retries:
            from .transport_retry import authorize
            grants = authorize(db, old, state, payload.transport_retries, body)
            new_state.setdefault("transport_retry_permissions", {}).update(grants)
        new_state["recovered_candidates"] = recovered
        new_state["stop_reason"] = None
        if protocol_upgrade is not None:
            new_state["quality_protocol"] = current_protocol
            new_state["quality_protocol_upgrade"] = protocol_upgrade
        if payload.replan:
            if payload.enable_research:
                # Explicit policy amendment on the same task ancestry. Never
                # alter the parent's frozen context or replay its research.
                context = deepcopy(state["context"])
                context.pop("context_hash", None)
                context["research_request"] = {**context.get("research_request", {}), "enabled": True}
                context["research_policy"] = research_policy(context)
                new_state["context"] = context_of(context, owner.input_revision)
                owner_state = load(owner.task_state_json)
                owner_state.update(context=new_state["context"],
                    confirmed_context_hash=new_state["context"]["context_hash"])
                owner.task_state_json = dump(owner_state)
                for field in ("research_binding_frozen", "research_result", "research_artifacts"):
                    new_state.pop(field, None)
            # Preserve the parent run, all receipts and the current artifact/version
            # as the publish CAS base. New layout IDs never reuse old learner text.
            research_enabled = new_state["context"].get("research_request", {}).get("enabled") is True
            network_enabled = research_enabled and new_state.get("mode") == "configured"
            requested_rounds = new_state.get("execution_policy", {}).get("network_tool_rounds", 3)
            new_state.setdefault("execution_policy", {}).update(
                network_enabled=network_enabled,
                network_tool_rounds=requested_rounds if network_enabled else 0,
                planning_source_tools=network_enabled,
            )
            new_state["plan"] = {"schema_version": "aiedu.plan.v1", "units": [], "network_policy": {
                "method_version": "run-network-policy.v1",
                "research_requested": research_enabled,
                "network_enabled": network_enabled,
                "planning_source_tools": network_enabled,
                "network_tool_rounds": requested_rounds if network_enabled else 0,
                "network_requests_per_round": new_state.get("execution_policy", {}).get("network_requests_per_round", 4),
                "disabled_behavior": "all_public_web_tools_are_off",
            }}
            new_state["plan"]["plan_hash"] = digest(new_state["plan"])
            new_state["check_plan"] = compile_plan(new_state["context"], new_state["plan"]["network_policy"])
            new_state.update(intent_pending=True, stages={}, replan_from_version_id=state.get("version_id"))
            for field in ("intent_call", "review_only_source_version", "optimization_request", "best_so_far_version",
                          "optimization_rounds", "optimization_stop_reason", "repair_rounds", "repair_stop_reason",
                          "optimization_attempts", "optimization_attempts_by_finding", "last_rejected_candidate_hash", "rejected_candidate_hashes"):
                new_state.pop(field, None)
            for field in list(new_state):
                if field.startswith("optimization_") and field.endswith("_artifacts"):
                    new_state.pop(field)
            new_state.setdefault("execution_policy", {}).update(task_adaptive_format=True, criterion_assessment=True)
            if not new_state.get("optimization_policy"):
                from .optimization import validate_policy
                from .quality import POLICY
                new_state["optimization_policy"] = validate_policy({**POLICY, "policy_version": "aiedu.repair.v2", "root_call_budget": None})
            new_state["execution_policy"]["quality_loop"] = True
            new_state["replan_amendment"] = {"method_version": "explicit-task-replan.v1", "parent_run_id": run_id,
                "parent_plan_hash": state["plan"]["plan_hash"], "parent_version_id": state.get("version_id"),
                "parent_context_hash": state["context"]["context_hash"],
                "context_hash": new_state["context"]["context_hash"],
                "enable_research": payload.enable_research,
                "request_hash": body, "content_reuse": "none_across_new_layout", "accounting_ancestry_preserved": True}
        if payload.recheck_completed:
            new_state["review_only_source_version"] = previous_version.id
        if payload.refresh_check_plan:
            new_state["check_plan"] = compile_plan(
                new_state["context"], new_state.get("plan", {}).get("network_policy"))
            new_state["check_plan_amendment"] = {
                "method_version": "explicit-fixed-recheck.v1",
                "parent_run_id": run_id, "source_version_id": previous_version.id,
                "previous_check_plan_hash": state["check_plan"]["check_plan_hash"],
                "check_plan_hash": new_state["check_plan"]["check_plan_hash"],
                "request_hash": body,
            }
            new_state.setdefault("execution_policy", {})["criterion_assessment"] = True
        db.add(GenerationJob(id=root_id, run_id=new_id, root_job_id=root_id, parent_job_id=old.id,
                goal_id=old.goal_id, job_type="task_v2", status="queued", state_json=dump(new_state),
                action_key=key, request_hash=body, state_revision=0, cancel_epoch=0))
        response = {"run_id": new_id, "parent_run_id": run_id, "state_revision": 0}
        remember(state, key, body, response, "resume")
        old.state_json = dump(state)
        old.state_revision += 1
        return response


def delivery_waiver(run_id, payload, key):
    """Record an explicit final-delivery decision without changing quality facts."""
    body = digest(payload.model_dump(mode="json"))
    with write() as db:
        obj = job(db, run_id)
        state = load(obj.state_json)
        prior = receipt(state, key, body)
        if prior:
            return prior
        cas(obj, payload.expected_revision)
        if obj.status in {"queued", "running"}:
            raise DomainError("ACTIVE_RUN")
        if digest(state["plan"]) != payload.checkpoint_hash:
            raise DomainError("CHECKPOINT_STALE")
        if state.get("version_id") != payload.version_id:
            raise DomainError("VERSION_MISMATCH")
        version = db.get(TutorialVersion, payload.version_id)
        artifact = db.get(Tutorial, state.get("artifact_id"))
        if (version is None or artifact is None or version.tutorial_id != artifact.id
                or artifact.current_version_id != version.id):
            raise DomainError("VERSION_MISMATCH")
        units = materialize(version).get("units", [])
        quality = json.loads(version.quality_report)
        from .import_profiles import binding_status
        from .delivery import decision_from_state
        from .delivery_waiver import build_delivery_waiver, revoke_delivery_waiver
        profile_binding = binding_status(db, state.get("context", {}))

        def decide(waiver=None):
            decision_state = (
                {**state, "delivery_waiver": waiver}
                if waiver is not None else state
            )
            return decision_from_state(
                decision_state, quality, units,
                profile_binding_current=profile_binding.get("status") == "current",
            )

        recorded_at = datetime.now(timezone.utc).isoformat()
        event = {
            "event_id": str(uuid4()),
            "recorded_at": recorded_at,
            "source": "explicit_delivery_waiver_api",
            "version_id": payload.version_id,
            "action": payload.action,
        }
        if payload.action == "decline":
            state["delivery_waiver_declined_at"] = recorded_at
            state.setdefault("delivery_waiver_events", []).append(event)
            decision = decide(state.get("delivery_waiver"))
        elif payload.action == "revoke":
            existing = state.get("delivery_waiver")
            if not existing:
                raise DomainError("DELIVERY_WAIVER_NOT_FOUND", 404)
            state["delivery_waiver"] = revoke_delivery_waiver(
                existing, event, revoked_at=recorded_at
            )
            state.setdefault("delivery_waiver_events", []).append(event)
            decision = decide(state["delivery_waiver"])
        else:
            if not payload.acknowledge_professional_uncertainty:
                raise DomainError("DELIVERY_WAIVER_CONFIRMATION_REQUIRED", 422)
            soft_ids = sorted(set(payload.soft_item_ids))
            if not soft_ids or len(soft_ids) != len(payload.soft_item_ids):
                raise DomainError("DELIVERY_WAIVER_ITEM_INVALID", 422)
            pending = decide()
            if pending["hard_blockers"]:
                raise DomainError(
                    "DELIVERY_WAIVER_HARD_BLOCKED", 422,
                    hard_blockers=pending["hard_blockers"],
                )
            if set(soft_ids) != set(pending["soft_item_ids"]):
                raise DomainError(
                    "DELIVERY_WAIVER_ITEM_MISMATCH", 422,
                    expected=pending["soft_item_ids"], supplied=soft_ids,
                )
            results = {
                row["item_id"]: row["result"] for row in pending["soft_blockers"]
            }
            event.update(
                soft_item_ids=soft_ids,
                acknowledge_professional_uncertainty=True,
            )
            state["delivery_waiver"] = build_delivery_waiver(
                state["check_plan"], quality, results, event,
                signer="local-owner", confirmed_at=recorded_at,
            )
            state.setdefault("delivery_waiver_events", []).append(event)
            decision = decide(state["delivery_waiver"])
            if not decision["final_allowed"]:
                raise DomainError(
                    "DELIVERY_WAIVER_NOT_EFFECTIVE", 422,
                    delivery_decision=decision,
                )
        obj.state_json = dump(state)
        obj.state_revision += 1
        response = {
            "run_id": run_id,
            "action": payload.action,
            "state_revision": obj.state_revision,
            "waiver": state.get("delivery_waiver"),
            "delivery_decision": decision,
        }
        remember(state, key, body, response, "delivery_waiver")
        obj.state_json = dump(state)
        return response


def snapshot(task_id, run_id=None):
    from sqlalchemy import func
    with SessionLocal() as db:
        owner = goal(db, task_id)
        query = db.query(GenerationJob).filter_by(goal_id=task_id, job_type="task_v2")
        # Supplemental analysis has its own run status. It must not replace
        # a failed/completed generation's status on the task or reader page.
        obj = (query.filter_by(run_id=run_id).first() if run_id else
            query.filter(func.json_extract(GenerationJob.state_json, "$.card_source").is_(None),
                         func.json_extract(GenerationJob.state_json, "$.synthesis_source").is_(None))
                .order_by(GenerationJob.created_at.desc(), GenerationJob.id.desc()).first())
        if run_id and not obj:
            raise DomainError("RUN_NOT_FOUND", 404)
        if not obj:
            return {"task_id": task_id, "input_revision": owner.input_revision, "task": load(owner.task_state_json)["context"], "run": None}
        state = load(obj.state_json)
        # Retry/resume creates a new root but must not erase the original bill or
        # hide a late receipt on an ancestor. Recursive SQL keeps the read bounded
        # to this run's ancestry, not unrelated runs for the same task.
        from sqlalchemy import select
        chain = select(GenerationJob.id, GenerationJob.parent_job_id).where(GenerationJob.id == obj.id).cte("run_chain", recursive=True)
        chain = chain.union_all(select(GenerationJob.id, GenerationJob.parent_job_id).join(chain, GenerationJob.id == chain.c.parent_job_id))
        chain_ids = list(db.scalars(select(chain.c.id)))
        late_receipts = {}
        for ancestor_id in chain_ids:
            ancestor = db.get(GenerationJob, ancestor_id)
            if ancestor is not None:
                late_receipts.update(load(ancestor.state_json).get("late_receipts", {}))
        calls = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(chain.c.id)))\
                  .order_by(CallDispatch.started_at, CallDispatch.call_id).all()
        version = db.get(TutorialVersion, state["version_id"]) if state.get("version_id") else None
        quality = load(version.quality_report) if version else reduce_quality(state["check_plan"], [], [])
        from .import_profiles import binding_status
        import_status = binding_status(db, state["context"])
        if import_status["status"] == "stale":
            quality = {**quality, "tqi_verdict": "blocked", "quality_status": "needs_review",
                "blocking_reasons": [*quality.get("blocking_reasons", []), {"code": "IMPORT_ASSET_STALE"}]}
        snapshot_units = materialize(version).get("units", []) if version else []
        from .delivery import decision_from_state
        delivery_decision = decision_from_state(
            state, quality, snapshot_units,
            profile_binding_current=import_status.get("status") == "current",
        )
        visible_stages = dict(state["stages"])
        for stage in {load(c.manifest_json).get("stage") for c in calls}:
            related = [c for c in calls if load(c.manifest_json).get("stage") == stage]
            if stage and visible_stages.get(stage) == "running" and related and not any(
                    c.status in {"authorized", "sent", "unknown"} for c in related):
                latest = max(related, key=lambda c: c.finished_at or c.started_at or 0)
                visible_stages[stage] = "done" if latest.outcome == "success" else "failed"
        understanding = state["context"].get("subject_understanding", {})
        framework_bundle = state["context"].get("evaluation_frameworks", {})
        tutorial_refs = [profile.get("planning_guidance") for profile in state["context"].get("quality_profiles", [])
                         if profile.get("planning_guidance")]
        from .review_contract import delivery_outcome, summarize_calls
        call_rows = []
        observed_at = time.time()
        for call in calls:
            manifest = load(call.manifest_json)
            current_run = call.root_job_id == obj.id
            retry_source = ("explicit_retry" if call.supersedes_call_id else
                            "ancestor_resume" if not current_run else
                            "stage_reattempt" if call.attempt > 1 else "initial")
            receipt = load(call.receipt_json)
            late = late_receipts.get(call.call_id)
            unresolved = ((call.status in {"unknown", "sent"} or call.outcome == "unknown")
                          and call.status != "quarantined" and bool(call.started_at))
            unknown_age = observed_at - call.started_at if unresolved else None
            call_rows.append({
                "call_id": call.call_id, "unit_id": call.unit_id, "status": call.status,
                "outcome": call.outcome, "attempt": call.attempt,
                "usage": load(call.usage_json) if call.usage_json else None,
                "price": load(call.price_json) if call.price_json else None,
                "model_family": call.model_family, "model": call.model,
                "stage": manifest.get("stage"),
                "provider_request_id": call.provider_request_id,
                "failure_code": receipt.get("failure_code"),
                "failure_diagnostics": receipt.get("failure_diagnostics"),
                "elapsed_seconds": call.finished_at-call.started_at if call.finished_at and call.started_at else None,
                "supersedes_call_id": call.supersedes_call_id,
                "current_run": current_run, "retry_source": retry_source,
                "request_hash": call.request_hash,
                "started_at": call.started_at or None,
                "finished_at": call.finished_at or None,
                "unknown_age_seconds": unknown_age,
                "unknown_age_warning": unknown_age is not None and unknown_age >= 6 * 3600,
                "late_receipt": {
                    key: late.get(key) for key in (
                        "late_outcome", "receipt_hash", "late_result_blob_hash",
                        "late_usage", "usage_source", "provider_request_id", "received_at",
                    )
                } if isinstance(late, dict) else None,
            })
        delivery = delivery_outcome(
            status=obj.status, stages=visible_stages, quality=quality,
            version_id=version.id if version else None, calls=call_rows,
            binding_current=import_status.get("status") == "current",
            delivery_decision=delivery_decision)
        return {"schema_version": "aiedu.task-run.v2", "task_id": task_id, "input_revision": owner.input_revision,
                "run_id": obj.run_id, "root_job_id": obj.id, "state_revision": obj.state_revision,
                "state": obj.status, "cancel_epoch": obj.cancel_epoch, "demo": state["mode"] == "demo",
                "artifact_id": state["artifact_id"], "version_id": version.id if version else None,
                "content_hash": version.content_hash if version else None, "context_hash": state["context"]["context_hash"],
                "resolved_task":state["context"],
                "plan_hash": state["plan"]["plan_hash"], "checkpoint_hash": digest(state["plan"]),
                  "units": snapshot_units, "stages": visible_stages,
                "quality": quality, "budget": state["budget"], "stop_reason": state.get("stop_reason"),
                "knowledge_results":state.get("knowledge_results",[]),"embedding_status":state.get("embedding_status","disabled"),
                "benchmark_result":state.get("benchmark_result"),
                "import_profile_status": import_status,
                "calls": call_rows,
                "accounting_revision": digest([
                    (c.call_id, c.status, c.usage_json,
                     (late_receipts.get(c.call_id) or {}).get("receipt_hash"))
                    for c in calls
                ]),
                "delivery_state": delivery["state"],
                "delivery_detail": delivery["detail"],
                "delivery_decision": delivery_decision,
                "run_metrics": summarize_calls(call_rows),
                "research_policy": state.get("check_plan", {}).get("research_policy", {}),
                "research_status": (state.get("research_result") or {}).get("status"),
                "research_degradation": (
                    (state.get("research_result") or {}).get("degradation")
                    or (state.get("context") or {}).get("research_degradation")
                ),
                "tutorial_rubric_refs": [{k: item.get(k) for k in (
                    "rubric_id", "version", "hash", "status", "calibration_status")} for item in tutorial_refs],
                "subject_understanding_ref": {
                    "composition_hash": understanding.get("composition_hash"),
                    "refs": [
                        {k: item.get(k) for k in ("id", "version", "hash", "domain_key")}
                        for item in [understanding.get("general"), *understanding.get("subjects", [])]
                        if isinstance(item, dict)
                    ],
                },
                "evaluation_framework_refs": [
                    {k: item.get(k) for k in ("id", "version", "source_hash", "domain_key")}
                    for item in framework_bundle.get("frameworks", [])
                ],
                "cost_final": bool(calls) and all(c.status == "not_sent" or
                    (c.status in {"settled", "late"} and c.usage_json is not None and
                     (c.model_family == "demo" or c.price_json is not None)) for c in calls),
                "next_actions": ["pause"] if obj.status in {"running", "queued"} else (["resume"] if obj.status in {"paused", "interrupted", "failed"} else [])}


def list_versions(task_id, artifact_id, before_version=None, limit=100):
    if not 1 <= limit <= 200 or (before_version is not None and before_version < 1):
        raise DomainError("INVALID_PAGINATION", 422)
    with SessionLocal() as db:
        goal(db, task_id)
        artifact = db.get(Tutorial, artifact_id)
        if artifact is None or artifact.goal_id != task_id:
            raise DomainError("ARTIFACT_NOT_FOUND", 404)
        rows = db.query(TutorialVersion.id, TutorialVersion.version,
                        TutorialVersion.content_hash, TutorialVersion.quality_hash)\
                 .filter(TutorialVersion.tutorial_id == artifact_id)
        if before_version is not None:
            rows = rows.filter(TutorialVersion.version < before_version)
        page = rows.order_by(TutorialVersion.version.desc()).limit(limit + 1).all()
        return {"items": [{"version_id": v.id, "artifact_id": artifact_id, "version": v.version,
                           "content_hash": v.content_hash, "quality_hash": v.quality_hash} for v in page[:limit]],
                "next_before_version": page[limit - 1].version if len(page) > limit else None}


def list_tasks(cursor=None, limit=50, search=""):
    if not 1 <= limit <= 200 or len(search) > 200:
        raise DomainError("INVALID_PAGINATION", 422)
    query_hash = digest({"search": search, "sort": "needs_action,updated_at,task_id"})
    from sqlalchemy import and_, case, func, or_
    with SessionLocal() as db:
        ranked = db.query(GenerationJob.id.label("id"), GenerationJob.goal_id.label("goal_id"),
                          func.row_number().over(partition_by=GenerationJob.goal_id,
                          order_by=(GenerationJob.created_at.desc(), GenerationJob.id.desc())).label("rn"))\
                   .filter(GenerationJob.job_type == "task_v2",
                           func.json_extract(GenerationJob.state_json, "$.card_source").is_(None),
                           func.json_extract(GenerationJob.state_json, "$.synthesis_source").is_(None)).subquery()
        needs = case((GenerationJob.id.is_not(None), case((GenerationJob.status.in_(
            ["awaiting_input", "failed", "interrupted", "paused"]), 1), else_=0)),
            else_=case((func.json_extract(LearningGoal.task_state_json, "$.task_state") != "confirmed", 1), else_=0))
        updated = case((LearningGoal.updated_at > func.coalesce(GenerationJob.updated_at, ""), LearningGoal.updated_at),
                       else_=GenerationJob.updated_at)
        raw = func.json_extract(LearningGoal.task_state_json, "$.context.raw_input")
        rows = db.query(LearningGoal, GenerationJob, needs.label("needs"), updated.label("updated"), raw.label("raw"))\
                 .outerjoin(ranked, and_(ranked.c.goal_id == LearningGoal.id, ranked.c.rn == 1))\
                 .outerjoin(GenerationJob, GenerationJob.id == ranked.c.id)\
                 .filter(LearningGoal.input_revision > 0)
        if search:
            rows = rows.filter(LearningGoal.title.contains(search, autoescape=True))
        from .management_lifecycle import overlay_ids
        rows = rows.filter(LearningGoal.id.notin_(overlay_ids("tasks")))
        if cursor:
            try:
                decoded = json.loads(base64.urlsafe_b64decode(cursor).decode())
                if decoded["query_hash"] != query_hash:
                    raise ValueError()
                last = decoded["last"]
                if (not isinstance(last, list) or len(last) != 3 or type(last[0]) is not bool
                        or not all(isinstance(v, str) for v in last[1:])):
                    raise ValueError()
                rows = rows.filter(or_(needs < int(last[0]), and_(needs == int(last[0]), updated < last[1]),
                    and_(needs == int(last[0]), updated == last[1], LearningGoal.id < last[2])))
            except (ValueError, KeyError, TypeError, UnicodeError):
                raise DomainError("INVALID_CURSOR", 422) from None
        bounded = rows.order_by(needs.desc(), updated.desc(), LearningGoal.id.desc()).limit(limit+1).all()
        page = []
        for owner, obj, needs_action, updated_at, raw_input_text in bounded[:limit]:
            st = load(obj.state_json) if obj else {}
            compact = " ".join(raw_input_text.split()) if isinstance(raw_input_text, str) else ""
            summary = (compact[:160] + ("…" if len(compact) > 160 else "")) if compact else None
            page.append({"task_id": owner.id, "title": owner.title, "summary": summary, "run_id": obj.run_id if obj else None,
                         "demo": st.get("mode") == "demo" if obj else None,
                         "state": obj.status if obj else load(owner.task_state_json)["task_state"],
                         "needs_action": bool(needs_action), "state_revision": obj.state_revision if obj else owner.input_revision,
                         "version_id": st.get("version_id"), "updated_at": updated_at,
                         "quality": {k: st.get("quality", {}).get(k) for k in ("tqi_verdict", "calibration_status", "quality_status")}})
        next_cursor = None
        if len(bounded) > limit:
            last = page[-1]
            next_cursor = base64.urlsafe_b64encode(dump({"query_hash": query_hash, "last": [last["needs_action"], last["updated_at"], last["task_id"]]}).encode()).decode()
        return {"items": page, "next_cursor": next_cursor, "read_at": time.time()}

"""Exactly one HTTP attempt per durable call. Parsing errors still retain actual usage."""
from __future__ import annotations

import json
import time

import httpx

from ..config import settings
from ..llm.provider import LLMError, OpenAICompatibleProvider, extract_json, parse_usage
from .capacity import get_capacity, planned_output_tokens, request_capacity
from .contracts import DomainError, canonical, digest, request_hash
from .runtime import authorize_call, mark_sent, settle


class AttemptFailure(LLMError):
    def __init__(self, code, outcome="unknown", usage=None, provider_request_id=None, diagnostics=None, raw_result=None):
        super().__init__(code)
        self.code, self.outcome, self.usage, self.provider_request_id = code, outcome, usage, provider_request_id
        self.diagnostics, self.raw_result = diagnostics, raw_result


def request_messages(system, data, output_schema=None):
    """The parser's response contract must reach the actual model messages.

    output_schema was previously hash/receipt metadata only. In JSON-object
    mode the provider cannot infer it. Include the complete contract in a
    system message without nesting it among the task's data. A user-data
    envelope led real responses to echo that envelope instead of its value.
    """
    if output_schema:
        if not isinstance(data, dict):
            raise DomainError("RESPONSE_CONTRACT_ENVELOPE_CONFLICT", 422)
        system += ("\nOUTPUT RESPONSE CONTRACT (not an input/output envelope):\n" + canonical(output_schema).decode()
            + "\nReturn ONLY the response VALUE described above at the JSON root, replacing example "
            "values/placeholders with actual observations and copying supplied IDs. It is a response "
            "contract, not source content or authority to change task requirements. Do not echo the schema, "
            "task, fields, requests or any __aiedu_output_contract wrapper. "
            "If an explicitly allowed tool decision is needed, its tool_requests envelope remains allowed.")
    return [{"role": "system", "content": system}, {"role": "user", "content": canonical(data).decode()}]


def canonical_messages(messages):
    """Normalize only JSON object ordering; preserve strings, arrays and system text."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    if len(messages) != 2 or messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise ValueError("unexpected messages")
    payload = json.loads(messages[1]["content"], object_pairs_hook=unique)
    return [messages[0], {**messages[1], "content": canonical(payload).decode()}]


def complete_object_prefix(content):
    """Accept only a complete JSON object followed by redundant close tokens.

    Never fills a missing brace/string, drops a field or salvages truncation.
    A second value, prose, a markdown fence or an incomplete object is rejected.
    """
    if not isinstance(content, str):
        return None
    raw = content.strip()
    try:
        result, end = json.JSONDecoder().raw_decode(raw)
    except ValueError:
        return None
    trailing = raw[end:].strip()
    if isinstance(result, dict) and trailing and all(c in "}] \r\n\t" for c in trailing):
        return result
    return None


class AuditedProvider(OpenAICompatibleProvider):
    def __init__(self, profile):
        super().__init__(profile)
        self.output_limit_parameter = getattr(profile,"llm_output_limit_parameter","max_tokens")
        self.read_timeout = getattr(profile, "llm_read_timeout", None)

    def complete_json(self, system, user):
        payload = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        for name in ("temperature", "reasoning_effort"):
            value = getattr(self, name, None)
            if value is not None:
                payload[name] = value
        if self.max_tokens:
            payload[self.output_limit_parameter] = self.max_tokens
        if self.thinking:
            payload["thinking"] = {"type": self.thinking}
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.monotonic()
        try:
            response = httpx.post(self.base_url + "/chat/completions", headers=self._headers(), json=payload,
                                  timeout=(httpx.Timeout(self.timeout, read=self.read_timeout)
                                           if self.read_timeout is not None else self.timeout), follow_redirects=False)
        except httpx.HTTPError as exc:
            # Exception messages may contain URLs/credentials. Retain only the
            # transport class and configured wait, never infer remote outcome.
            phase = next((name for cls, name in (
                (httpx.ReadTimeout, "read"), (httpx.ConnectTimeout, "connect"),
                (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool"))
                if isinstance(exc, cls)), "unspecified_transport")
            raise AttemptFailure("PROVIDER_TRANSPORT_UNKNOWN", diagnostics={
                "transport_exception": type(exc).__name__, "timeout_phase": phase,
                "configured_timeout_seconds": self.timeout,
                **({"read_timeout_seconds": self.read_timeout} if self.read_timeout is not None else {}),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "remote_outcome": "unknown"}) from None
        request_id = response.headers.get("x-request-id")
        # An explicit rejection is known to have failed; no automatic retry is issued.
        if response.status_code >= 300:
            raise AttemptFailure(f"PROVIDER_HTTP_{response.status_code}", "failed", provider_request_id=request_id)
        try:
            data = response.json()
        except ValueError:
            raise AttemptFailure("PROVIDER_INVALID_RESPONSE", "failed", provider_request_id=request_id) from None
        usage = parse_usage(data)
        request_id = data.get("id") or request_id
        content, finish_reason, validation = None, None, "MISSING_RESPONSE_FIELDS"
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                validation = "OUTPUT_LENGTH_LIMIT"
                raise ValueError("truncated")
            if not isinstance(content, str) or not content.strip():
                validation = "EMPTY_OR_NON_STRING_CONTENT"
                raise ValueError("empty")
            if self.api_key:
                content = content.replace(self.api_key, "[REDACTED]")
            validation = "JSON_PARSE_FAILED"
            try:
                output = extract_json(content, str(finish_reason or ""))
            except LLMError:
                output = complete_object_prefix(content) if finish_reason == "stop" else None
                if output is None:
                    raise
                self.response_normalization = {"method": "complete_object_redundant_closers.v1", "raw_hash": digest(content)}
        except (KeyError, IndexError, TypeError, ValueError, LLMError):
            safe_content = content.replace(self.api_key, "[REDACTED]") if self.api_key and isinstance(content, str) else content
            diagnostics = {"validation_reason": validation, "finish_reason": finish_reason if finish_reason in
                {"stop", "length", "content_filter", "tool_calls", "function_call"} else "unrecognized_or_missing",
                "content_characters": len(content) if isinstance(content, str) else 0}
            raise AttemptFailure("PROVIDER_OUTPUT_INVALID_OR_TRUNCATED", "failed", usage, request_id,
                diagnostics, {"provider_content": safe_content if isinstance(safe_content, str) else None,
                    "diagnostics": diagnostics}) from None
        self.provider_request_id = request_id
        return output, int((time.monotonic()-started)*1000), usage


def invoke(run_id, worker, epoch, state, *, stage, unit_id, data, system, role="generation", profile=None,
           key=None, entries=None, provider_factory=None, output_schema=None, output_tokens=None):
    from .model_roles import for_role
    if not settings.v2_allow_paid:
        raise DomainError("PAID_RUN_NOT_AUTHORIZED", 403)
    profile = profile or for_role(role)
    messages = request_messages(system, data, output_schema)
    capacity = get_capacity(profile.llm_base_url, profile.llm_model)
    planned = planned_output_tokens(profile, output_tokens, capacity)
    estimate = request_capacity(messages, capacity, planned["reserved_output_tokens"])
    estimate.update({
        key: value for key, value in planned.items()
        if key not in {"reserved_output_tokens", "capacity_source"}
    })
    estimate["capacity_source"] = planned["capacity_source"]
    limit_parameter = planned["output_limit_parameter"]
    profile.llm_max_tokens = planned["reserved_output_tokens"]
    profile.llm_output_limit_parameter = limit_parameter
    params = {limit_parameter: profile.llm_max_tokens, "temperature": getattr(profile, "llm_temperature", None),
              "thinking": getattr(profile, "llm_thinking", None), "reasoning_effort": getattr(profile, "llm_reasoning_effort", None),
              "json_mode": getattr(profile, "llm_json_mode", True)}
    manifest = {"schema_version": "aiedu.prompt-manifest.v1", "context_hash": state["context"]["context_hash"],
                "plan_hash": state["plan"]["plan_hash"], "unit_id": unit_id, "stage": stage, "role": role, "entries": entries or []}
    req = request_hash(manifest, messages, profile.llm_base_url, profile.llm_model, params, output_schema or {})
    # Reuse only a successful, durable result with IDENTICAL request identity in
    # this root's ancestry. Failed/late/unknown results are never reused/retried.
    from ..db import SessionLocal
    from ..models import GenerationJob
    from .runtime import job,load
    from .storage import CallDispatch,BlobStore
    from sqlalchemy import select
    with SessionLocal() as db:
        root = job(db,run_id)
        chain = select(GenerationJob.id,GenerationJob.parent_job_id).where(GenerationJob.id == root.id).cte("inference_ancestors",recursive=True)
        chain = chain.union_all(select(GenerationJob.id,GenerationJob.parent_job_id).join(chain,GenerationJob.id == chain.c.parent_job_id))
        candidates = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(chain.c.id)),
            CallDispatch.unit_id == unit_id, CallDispatch.provider == profile.llm_base_url,
            CallDispatch.model == profile.llm_model, CallDispatch.model_family == getattr(profile, "llm_model_family", "unknown"),
            CallDispatch.input_hash == state["context"]["context_hash"],
            CallDispatch.status == "settled", CallDispatch.outcome == "success")
        cached = candidates.filter(
            CallDispatch.request_hash == req,CallDispatch.input_hash == state["context"]["context_hash"],
            CallDispatch.status == "settled",CallDispatch.outcome == "success").first()
        normalization = None
        if not cached:
            # Legacy requests used insertion-order JSON inside a string. Validate
            # their original identity before comparing the canonical JSON form.
            for candidate in candidates.order_by(CallDispatch.started_at, CallDispatch.call_id):
                if not candidate.result_blob_hash:
                    continue
                saved_manifest, saved = load(candidate.manifest_json), load(candidate.request_json)
                if saved_manifest.get("stage") != stage or saved_manifest.get("role") != role:
                    continue
                try:
                    arguments = (saved_manifest, saved["messages"], candidate.provider, candidate.model,
                                 saved["parameters"], saved.get("output_schema", {}))
                    if request_hash(*arguments) != candidate.request_hash:
                        continue
                    normalized = request_hash(saved_manifest, canonical_messages(saved["messages"]),
                        candidate.provider, candidate.model, saved["parameters"], saved.get("output_schema", {}))
                except (KeyError, TypeError, ValueError):
                    continue
                if normalized == req:
                    cached, normalization = candidate, "json_object_order_v1"
                    break
        if cached and cached.result_blob_hash:
            return load(BlobStore().get(cached.result_blob_hash).decode()), {"call_id":cached.call_id,"cache":"identical_ancestral_request",
                "latency_ms":0,"usage":load(cached.usage_json) if cached.usage_json else None,
                "model_family":cached.model_family,"request_hash":cached.request_hash,
                "requested_request_hash":req,"identity_normalization":normalization}
        # Explicit resume may recover a LOCAL parse failure without another
        # provider dispatch. Original failed outcome/usage/request stay intact.
        if root.parent_job_id:
            failed = db.query(CallDispatch).filter(CallDispatch.root_job_id.in_(select(chain.c.id)),
                CallDispatch.request_hash == req, CallDispatch.status == "settled", CallDispatch.outcome == "failed",
                CallDispatch.provider == profile.llm_base_url, CallDispatch.model == profile.llm_model).all()
            for prior in failed:
                receipt = load(prior.receipt_json)
                diagnostic = receipt.get("failure_diagnostics", {})
                if (diagnostic.get("finish_reason") != "stop" or diagnostic.get("validation_reason") != "JSON_PARSE_FAILED"
                        or not prior.result_blob_hash):
                    continue
                raw = load(BlobStore().get(prior.result_blob_hash).decode())
                recovered = complete_object_prefix(raw.get("provider_content"))
                if recovered is not None:
                    return recovered, {"call_id": prior.call_id, "cache": "explicit_resume_local_parse_recovery",
                        "processing_method": "complete_object_redundant_closers.v1", "original_dispatch_outcome": "failed",
                        "request_hash": req, "raw_result_blob_hash": prior.result_blob_hash,
                        "usage": load(prior.usage_json) if prior.usage_json else None, "model_family": prior.model_family,
                        "new_provider_dispatch": False}
    call_id = authorize_call(run_id, unit_id, req, state["context"]["context_hash"], epoch, worker,
        key or f"{run_id}:{stage}:{digest([unit_id, req])[:40]}", profile.llm_base_url, profile.llm_model,
        getattr(profile, "llm_model_family", "unknown"), manifest=manifest,
        request={"messages": messages, "parameters": params, "output_schema": output_schema or {}, "capacity": estimate,
            "transport": {"configured_timeout_seconds": getattr(profile, "llm_timeout", None),
                **({"read_timeout_seconds": profile.llm_read_timeout} if getattr(profile, "llm_read_timeout", None) is not None else {})}})
    mark_sent(call_id, worker, epoch)
    provider = provider_factory(profile) if provider_factory else AuditedProvider(profile)
    try:
        output, latency, usage = provider.complete_json(messages[0]["content"], messages[1]["content"])
    except LLMError as exc:
        outcome = getattr(exc, "outcome", "unknown")
        settle(call_id, outcome, getattr(exc, "usage", None), getattr(exc, "provider_request_id", None),
            failure_code=getattr(exc,"code","PROVIDER_FAILURE_UNCLASSIFIED"),
            failure_diagnostics=getattr(exc,"diagnostics",None), raw_result=getattr(exc,"raw_result",None))
        raise DomainError("UNKNOWN_CALL" if outcome == "unknown" else "PROVIDER_CALL_FAILED", 502) from None
    receipt = settle(call_id, "success", usage, getattr(provider, "provider_request_id", None), raw_result=output)
    if receipt["status"] != "settled":
        raise DomainError("FENCED")
    return output, {"call_id": call_id, "latency_ms": latency, "usage": usage,
                    "response_normalization": getattr(provider, "response_normalization", None),
                    "model_family": getattr(profile, "llm_model_family", "unknown"), "request_hash": req}

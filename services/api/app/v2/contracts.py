"""Public contracts and canonical identities. Provider output never grants authority."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool

SCHEMA = "aiedu.task-run.v2"
CANONICAL = "aiedu.canonical-json.v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def digest(value: Any) -> str:
    return sha(canonical(value))


def request_hash(manifest: dict, messages: list, provider: str, model: str, parameters: dict, output_schema: dict) -> str:
    # Explicit allowlist: post-authorization call_id cannot alter identity.
    fields = ("schema_version", "context_hash", "plan_hash", "unit_id", "stage", "entries")
    return digest({"canonical_version": CANONICAL, "manifest": {k: manifest[k] for k in fields},
                   "messages_hash": digest(messages), "provider": provider, "model": model,
                   "parameters": parameters, "output_schema": output_schema})


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SpecBinding(Contract):
    artifact_role: str = "tutorial"
    scene: str = "knowledge_tutorial"
    spec_id: str | None = None
    snapshot: dict = Field(default_factory=dict)
    spec_version: int | None = None
    scope: dict = Field(default_factory=dict)


class SubjectBinding(Contract):
    domain_key: str
    branch: str = ""
    unit_keys: list[str] = Field(default_factory=list)


class KnowledgeRequest(Contract):
    question_ids: list[str] = Field(default_factory=list)
    analyse: bool = False
    guidance_enabled: bool = True
    embedding_enabled: bool = False


class ImportAssetRef(Contract):
    id: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    hash: str = Field(pattern="^[a-f0-9]{64}$")


class ImportProfileRef(Contract):
    profile: ImportAssetRef
    rubric: ImportAssetRef


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="allow", strict=True)
    enabled: StrictBool = False


class TaskInput(Contract):
    title: str = Field(min_length=1, max_length=200)
    raw_input: str = Field(min_length=1)
    domain_key: str = "general"
    branch: str = ""
    runtime_target: str = ""
    difficulty_mode: str = ""
    difficulty_note: str = ""
    generation_scope: Literal["focused", "system"] | None = None
    hard_constraints: list[str] = Field(default_factory=list)
    material_ids: list[str] = Field(default_factory=list)
    requested_artifacts: list[str] = Field(default_factory=lambda: ["tutorial"])
    spec_bindings: list[SpecBinding] = Field(default_factory=lambda: [SpecBinding()])
    subject_bindings: list[SubjectBinding] = Field(default_factory=list)
    execution_policy: dict = Field(default_factory=dict)
    knowledge: KnowledgeRequest = Field(default_factory=KnowledgeRequest)
    research_request: ResearchRequest = Field(default_factory=ResearchRequest)
    import_profile_refs: list[ImportProfileRef] = Field(default_factory=list)
    operation: Literal["generate", "knowledge_analysis", "benchmark_build"] = "generate"


class TaskStart(TaskInput):
    title: str = Field(default="新学习任务", min_length=1, max_length=200)
    mode: Literal["demo", "configured"] = "configured"


class Revision(Contract):
    expected_revision: int = Field(ge=0)


class InputPatch(Revision):
    input: TaskInput


class Confirmation(Revision):
    context_hash: str


class StartRun(Contract):
    expected_revision: int = Field(ge=0)
    confirmed_context_hash: str
    mode: Literal["demo", "configured"] = "demo"


class Pause(Revision):
    reason: Literal["user_pause"] = "user_pause"


class UnknownResolution(Contract):
    call_id: UUID
    action: Literal["verify_provider_status", "retry_as_new_attempt", "keep_quarantined"]
    provider_evidence_ref: str | None = None
    acknowledge_possible_duplicate_cost: bool = False


class QuarantineCall(Contract):
    call_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class QuarantineUnknown(Revision):
    checkpoint_hash: str
    calls: list[QuarantineCall] = Field(min_length=1, max_length=100)
    acknowledge_possible_duplicate_cost: bool = False


class TransportRetry(Contract):
    call_id: UUID
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    previous_read_timeout_seconds: int = Field(ge=5, le=600)
    read_timeout_seconds: int = Field(ge=5, le=600)
    acknowledge_possible_duplicate_cost: bool = False


class Resume(Revision):
    checkpoint_hash: str
    unknown_resolutions: list[UnknownResolution] = Field(default_factory=list)
    transport_retries: list[TransportRetry] = Field(default_factory=list, max_length=100)
    recheck_completed: bool = False
    refresh_check_plan: bool = False
    replan: bool = False
    acknowledge_new_generation: bool = False
    enable_research: bool = False


class ExportRequest(Revision):
    version_id: str
    kind: Literal["complete", "available_draft"] = "complete"
    roles: list[str] = Field(default_factory=list)
    options: dict = Field(default_factory=dict)


class UserReview(Revision):
    version_id: str
    content_hash: str
    claim: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)


class RepairRequest(Revision):
    version_id: str
    unit_hash: str
    location_hash: str


class DomainError(Exception):
    def __init__(self, code: str, status: int = 409, **details):
        self.code, self.status, self.details = code, status, details
        super().__init__(code)

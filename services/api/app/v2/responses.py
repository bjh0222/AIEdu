"""Backend-owned response schemas consumed by generated OpenAPI/types."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Output(BaseModel):
    model_config = ConfigDict(extra="allow")


class DiagnosticIndicator(Output):
    indicator_id: str
    applicable: bool | None = None
    numerator: float | None = None
    denominator: float | None = None
    unit: str | None = None
    enum_value: str | None = None
    evidence: list[dict[str, Any]]
    unknown_reason: str | None = None


class DiagnosticDimension(Output):
    dimension_id: str
    name: str | None = None
    unit_id: str
    score: Literal[0, 1, 2] | None
    result: str
    reason: str
    evidence: list[dict[str, Any]]
    indicators: list[DiagnosticIndicator] = Field(default_factory=list)
    scope: str | None = None
    source_refs: list[str] = Field(default_factory=list)
    profile_ref: dict[str, Any]
    rubric_ref: dict[str, Any]
    binding_hash: str


class DiagnosticReview(Output):
    status: str
    version_id: str
    dimensions: list[DiagnosticDimension]
    model_family_independent: bool
    calibration_status: Literal["not_calibrated"]
    aggregate_score: None
    threshold: None
    research_evidence: dict[str, Any] | None = None


class QualityReport(Output):
    tqi_verdict: Literal["pass", "conditional_pass", "needs_repair", "blocked", "not_calibrated"]
    calibration_status: str
    quality_status: str
    check_coverage: dict[str, int]
    blocking_reasons: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    diagnostic_review: DiagnosticReview | None = None
    automated_assessment: dict[str, Any] | None = None
    difficulty_review: dict[str, Any] | None = None
    mathematical_evidence: dict[str, Any] | None = None
    research_evidence: dict[str, Any] | None = None


class UnitSnapshot(Output):
    unit_id: str
    artifact_role: str
    artifact_roles: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    body: str
    content_hash: str
    blob_hash: str
    generation_state: str


class CallSnapshot(Output):
    call_id: str
    unit_id: str
    status: str
    outcome: str
    attempt: int
    usage: dict[str, Any] | None
    price: dict[str, Any] | None
    model_family: str
    request_hash: str


class RunSnapshot(Output):
    schema_version: Literal["aiedu.task-run.v2"]
    task_id: str
    input_revision: int
    run_id: str
    root_job_id: str
    state_revision: int
    state: str
    cancel_epoch: int
    demo: bool
    artifact_id: str
    version_id: str | None
    content_hash: str | None
    context_hash: str
    plan_hash: str
    checkpoint_hash: str
    units: list[UnitSnapshot]
    stages: dict[str, str]
    quality: QualityReport
    budget: dict[str, Any]
    stop_reason: str | None
    calls: list[CallSnapshot]
    accounting_revision: str
    cost_final: bool
    next_actions: list[str]
    research_policy: dict[str, Any] = Field(default_factory=dict)
    tutorial_rubric_refs: list[dict[str, Any]] = Field(default_factory=list)
    subject_understanding_ref: dict[str, Any] = Field(default_factory=dict)
    evaluation_framework_refs: list[dict[str, Any]] = Field(default_factory=list)


class DraftSnapshot(Output):
    task_id: str
    input_revision: int
    task: dict[str, Any]
    run: None


class TaskListItem(Output):
    task_id: str
    title: str
    run_id: str | None
    state: str
    needs_action: bool
    state_revision: int
    version_id: str | None
    updated_at: str
    quality: dict[str, Any]
    demo: bool | None = None
    # 任务页卡片摘要：raw_input 服务端截断（§18 任务自适应，列表不携带全文）
    summary: str | None = None


class TaskPage(Output):
    items: list[TaskListItem]
    next_cursor: str | None
    read_at: float


class VersionListItem(Output):
    version_id: str
    artifact_id: str
    version: int
    content_hash: str
    quality_hash: str


class VersionPage(Output):
    items: list[VersionListItem]
    next_before_version: int | None


class KnowledgeNode(Output):
    id: str
    kind: str
    title: str
    summary: str | None
    status: str
    unit_id: str
    version_id: str | None = None
    source_hash: str | None = None


class KnowledgeEdge(Output):
    source: str
    target: str
    kind: Literal["contains", "prerequisite", "organization"]
    evidence: str


class KnowledgeGraph(Output):
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge]
    version_id: str | None
    demo: bool
    status: str
    graph_hash: str | None = None
    logical_relations: str | None = None


class RunManifest(Output):
    schema_version: Literal["aiedu.run-manifest.v1"]
    task_id: str
    run_id: str
    root_job_id: str
    manifest_hash: str
    calls: list[dict[str, Any]]
    replay: dict[str, Any]

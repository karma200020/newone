from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(StrEnum):
    LOGS = "logs"
    MONITORING = "monitoring"
    DEPLOYMENTS = "deployments"
    GITHUB = "github"
    JIRA = "jira"
    SLACK = "slack"
    DOCUMENTATION = "documentation"
    DATABASE = "database"


class Status(StrEnum):
    QUEUED = "queued"
    UNDERSTANDING = "understanding"
    COLLECTING = "collecting"
    TESTING = "testing"
    DIAGNOSED = "diagnosed"
    NEEDS_CLARIFICATION = "needs_clarification"
    FAILED = "failed"


class Principal(StrictModel):
    subject: str
    tenant_id: str
    role: Literal["viewer", "operator", "approver", "admin"]
    services: list[str] = Field(min_length=1)
    tools: list[str] = Field(default_factory=lambda: ["*"])


class Window(StrictModel):
    start: datetime
    end: datetime

    @model_validator(mode="after")
    def ordered(self) -> Window:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Time window must include timezone")
        if self.end <= self.start or (self.end - self.start).total_seconds() > 86400:
            raise ValueError("Window must be positive and at most 24 hours")
        return self


class Record(StrictModel):
    source: Source
    source_id: str
    tenant_id: str
    service: str
    timestamp: datetime
    content: str
    attributes: dict[str, str | float | bool] = Field(default_factory=dict)
    reference: str | None = None

    @model_validator(mode="after")
    def timestamp_has_timezone(self) -> Record:
        if self.timestamp.tzinfo is None:
            raise ValueError("Source records require timezone-aware timestamps")
        return self


class Evidence(StrictModel):
    id: str
    source_type: Source
    source_id: str
    timestamp: datetime
    content: str
    relevance: float = Field(ge=0, le=1)
    tool: str
    reference: str | None = None
    content_hash: str
    suspicious: bool = False
    attributes: dict[str, str | float | bool] = Field(default_factory=dict)

    @staticmethod
    def digest(content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()


class Hypothesis(StrictModel):
    id: str
    title: str
    required_signals: list[str]
    supporting_evidence: list[str] = Field(default_factory=list)
    contradictory_evidence: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=list)
    score: float = Field(default=0, ge=0, le=1)
    test_summary: str = ""


class Diagnosis(StrictModel):
    root_cause: str
    hypothesis_id: str | None
    confidence: Literal["low", "medium", "high"]
    confidence_explanation: str
    supporting_evidence: list[str]
    contradictory_evidence: list[str]
    uncertainty: list[str]
    calibrated_probability: Literal[False] = False


class ToolCall(StrictModel):
    id: str = Field(default_factory=new_id)
    tool: str
    started_at: datetime = Field(default_factory=utcnow)
    duration_ms: float = 0
    attempts: int = 1
    cache_hit: bool = False
    evidence_ids: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error: str | None = None


class TimelineEntry(StrictModel):
    timestamp: datetime
    summary: str
    evidence_ids: list[str]


class ActionKind(StrEnum):
    ROLLBACK = "rollback_deployment"
    RESTART = "restart_service"
    CONFIGURE = "modify_configuration"
    FLAG = "change_feature_flag"
    ROTATE = "rotate_credentials"
    REPORT = "create_incident_report"
    JIRA = "create_jira_issue"
    SLACK = "post_slack_message"


class Action(StrictModel):
    id: str = Field(default_factory=new_id)
    kind: ActionKind
    service: str
    environment: Literal["synthetic"] = "synthetic"
    reason: str
    evidence_ids: list[str]
    parameters: dict[str, str] = Field(default_factory=dict)
    risk: Literal["low", "high"]
    status: Literal[
        "proposed", "approved", "rejected", "executing", "succeeded", "failed", "unverified"
    ] = "proposed"
    verification: str | None = None


class Approval(StrictModel):
    id: str = Field(default_factory=new_id)
    action_id: str
    action_digest: str
    decision: Literal["approve", "reject"]
    actor: str
    timestamp: datetime = Field(default_factory=utcnow)
    expires_at: datetime


class TraceEvent(StrictModel):
    timestamp: datetime = Field(default_factory=utcnow)
    stage: str
    summary: str
    evidence_ids: list[str] = Field(default_factory=list)


class Usage(StrictModel):
    model: str = "deterministic-evidence-engine"
    model_version: str = "1"
    prompt_version: str = "diagnosis-v1"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    retrieval_calls: int = 0


class Investigation(StrictModel):
    id: str = Field(default_factory=new_id)
    request_id: str = Field(default_factory=new_id)
    run_id: str = Field(default_factory=new_id)
    tenant_id: str
    requested_by: Principal
    description: str
    scenario_id: str
    affected_services: list[str] = Field(default_factory=list)
    window: Window | None = None
    severity: Literal["unknown", "low", "medium", "high"] = "unknown"
    status: Status = Status.QUEUED
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    plan: list[str] = Field(default_factory=list)
    observations: list[str] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    trace: list[TraceEvent] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    report: str | None = None


class DiagnosticRule(StrictModel):
    id: str
    title: str
    signals: list[str]
    contradictions: list[str]
    minimum_sources: int = 2
    action: ActionKind
    action_parameters: dict[str, str]
    remediation: str


class Scenario(StrictModel):
    id: str
    description: str
    service: str
    window: Window
    records: list[Record]
    ground_truth: str
    expected_evidence: list[str]
    expected_timeline: list[str]
    acceptable_hypotheses: list[str]
    expected_remediation: ActionKind
    prohibited_actions: list[str]

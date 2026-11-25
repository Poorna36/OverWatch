"""Literal Pydantic models mirroring schemas in docs/CONTRACTS.md.

Fixed technical decisions from docs/AGENTS.md:
- Pydantic models mirror docs/CONTRACTS.md names exactly.
- Errors use the codes in docs/CONTRACTS.md section 'Error codes'.
- Timestamps: UTC ISO 8601. IDs: prefixed ULIDs (s_, p_, a_, u_, e_).
"""

from enum import StrEnum
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Section 1. Primitives
# ---------------------------------------------------------------------------
Id = str  # prefixed ULID: s_ session, p_ plan, a_ approval, u_ undo, e_ event
Iso = str  # UTC ISO 8601

Decision = Literal["allow", "ask", "block"]
Tier = Literal["read", "reversible", "sensitive", "forbidden"]
SessionState = Literal[
    "CREATED",
    "PLANNING",
    "AWAITING_PLAN_APPROVAL",
    "STARTING",
    "ACTIVE",
    "PAUSED",
    "ENDING",
    "ENDED",
    "FAILED",
]


# ---------------------------------------------------------------------------
# Section 2. Error codes
# ---------------------------------------------------------------------------
class ErrorCode(StrEnum):
    SESSION_INACTIVE = "SESSION_INACTIVE"
    NOT_IN_PROFILE = "NOT_IN_PROFILE"
    POLICY_BLOCK = "POLICY_BLOCK"
    ARG_INVALID = "ARG_INVALID"
    PATH_JAIL = "PATH_JAIL"
    DENY_PATTERN = "DENY_PATTERN"
    USER_DENIED = "USER_DENIED"
    APPROVAL_TIMEOUT = "APPROVAL_TIMEOUT"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    LOOP_DETECTED = "LOOP_DETECTED"
    TIMEOUT = "TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    FATAL = "FATAL"


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Section 3. Tool call contract (agent <-> gateway)
# ---------------------------------------------------------------------------
class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: Id
    call_id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


ToolResultDecision = Literal[
    "allow", "ask", "block", "asked_approved", "asked_denied", "dry_run"
]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call_id: str
    ok: bool
    decision: ToolResultDecision
    result: Any | None = None
    truncated: bool | None = None
    error: ErrorBody | None = None
    audit_seq: int
    undo_id: Id | None = None


# ---------------------------------------------------------------------------
# Section 5. Policy file (policy.yaml)
# ---------------------------------------------------------------------------
class PolicyDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unknown_tool: Decision = "block"
    approval_timeout_s: int = 120
    output_cap_bytes: int = 200000
    tool_timeout_s: int = 30


class TierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    log: bool = True
    undo: Literal["capture"] | None = None


class ToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Tier
    path_jail: str | None = None
    max_arg_bytes: int | None = None
    timeout_s: int | None = None
    deny_patterns: list[str] = Field(default_factory=list)


class EscalationRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    when: dict[str, Any]
    decision: Decision | None = None
    reason: str | None = None
    action: str | None = None


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    defaults: PolicyDefaults = Field(default_factory=PolicyDefaults)
    tiers: dict[Tier, TierConfig] = Field(default_factory=dict)
    tools: dict[str, ToolPolicy] = Field(default_factory=dict)
    escalations: list[EscalationRule] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Section 6. Profile file (profiles/*.yaml)
# ---------------------------------------------------------------------------
class ProfileResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cpus: float = 1.0
    memory: str = "1g"
    pids: int = 256
    ttl_minutes: int = 60


class ProfileBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tool_calls: int = 200
    max_usd: float = 1.00


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str  # "assistant" | "coder" | "routine"
    system_prompt_file: str | None = None
    image: str
    resources: ProfileResources = Field(default_factory=ProfileResources)
    budget: ProfileBudget = Field(default_factory=ProfileBudget)
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    egress_allowlist: list[str] = Field(default_factory=list)
    tool_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Section 7. Plan (STRETCH, F-11)
# ---------------------------------------------------------------------------
class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    tier: Tier
    note: str | None = None


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Id
    session_id: Id
    summary: str
    profile: str
    steps: list[PlanStep] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    est_budget_usd: float | None = None
    plan_hash: str


# ---------------------------------------------------------------------------
# Section 8. Approval card
# ---------------------------------------------------------------------------
class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: Id
    session_id: Id
    call_id: str
    tool: str
    tier: Tier
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    reason: str
    irreversible: bool
    warnings: list[str] | None = None
    expires_at: Iso


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_id: Id
    approve: bool


# ---------------------------------------------------------------------------
# Section 9. Events (WebSocket, server -> phone)
# ---------------------------------------------------------------------------
EventType = Literal[
    "session_state",
    "tool_call",
    "tool_result",
    "blocked",
    "approval_request",
    "approval_resolved",
    "plan",
    "dry_run_diff",
    "problem_report",
    "tier_change",
    "budget",
    "agent_message",
    "session_ended",
]

T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    seq: int
    session_id: Id
    ts: Iso
    type: EventType
    data: T


# ---------------------------------------------------------------------------
# Section 10. Problem report (STRETCH, F-14)
# ---------------------------------------------------------------------------
class ProblemFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    action: Literal["retry", "skip_step", "reconnect", "end_session", "edit_plan"]


class ProblemReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    what_failed: str
    why: str
    tried: list[str] = Field(default_factory=list)
    fixes: list[ProblemFix] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Section 11. Audit entry
# ---------------------------------------------------------------------------
AuditDecision = Literal[
    "allowed", "asked_approved", "asked_denied", "blocked", "dry_run"
]


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    session_id: Id
    ts: Iso
    tool: str
    args_redacted: dict[str, Any] = Field(default_factory=dict)
    decision: AuditDecision
    reason: str
    tier: Tier | None = None
    undo_id: Id | None = None
    duration_ms: float | None = None
    prev_hash: str
    hash: str


# ---------------------------------------------------------------------------
# Section 12. Undo record (STRETCH, F-13)
# ---------------------------------------------------------------------------
class UndoRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    undo_id: Id
    session_id: Id
    audit_seq: int
    tool: str
    inverse_tool: str
    inverse_args: dict[str, Any] = Field(default_factory=dict)
    state: Literal["available", "used", "expired"]


# ---------------------------------------------------------------------------
# Section 13. Connector token modes (DEMO)
# ---------------------------------------------------------------------------
ConnectorTokenMode = Literal["session", "linked"]

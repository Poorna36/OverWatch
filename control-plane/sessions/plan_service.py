"""F-11 Plan preview generation, canonical hashing, persistence, and approval."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from control_plane.audit.audit_writer import (
    canonical_json,
    format_utc_iso,
    truncate_string,
)
from control_plane.contracts import Plan, PlanStep, Policy, Profile, Tier
from control_plane.db.models import PlanModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

PLAN_ID_PREFIX = "p_"
_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class PlanServiceError(Exception):
    """Base error for F-11 plan preview operations."""


class PlanNotFoundError(PlanServiceError):
    """Raised when a requested plan does not exist for a session."""


class PlanValidationError(PlanServiceError):
    """Raised when plan edit or approval input is invalid."""


def _encode_crockford_base32(value: int, length: int) -> str:
    chars = ["0"] * length
    for idx in range(length - 1, -1, -1):
        chars[idx] = _CROCKFORD_ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(chars)


def generate_plan_id(now: datetime | None = None) -> str:
    """Generate a contract-valid ``p_``-prefixed 26-character Crockford ULID."""
    dt = datetime.now(UTC) if now is None else now.astimezone(UTC)
    timestamp_ms = int(dt.timestamp() * 1000)
    ts_part = _encode_crockford_base32(timestamp_ms, 10)
    rand_int = int.from_bytes(secrets.token_bytes(10), "big")
    rand_part = _encode_crockford_base32(rand_int, 16)
    return f"{PLAN_ID_PREFIX}{ts_part}{rand_part}"


def compute_plan_hash(
    *,
    plan_id: str,
    session_id: str,
    summary: str,
    profile: str,
    steps: Sequence[PlanStep],
    scopes: Sequence[str],
    est_budget_usd: float | None,
) -> str:
    """Compute deterministic SHA-256 hash over canonical JSON of plan content."""
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "session_id": session_id,
        "summary": summary,
        "profile": profile,
        "steps": [step.model_dump(exclude_none=True) for step in steps],
        "scopes": list(scopes),
        "est_budget_usd": est_budget_usd,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_plan_for_profile(
    *,
    session_id: str,
    profile: Profile,
    policy: Policy,
    summary: str | None = None,
    plan_id: str | None = None,
    now: datetime | None = None,
) -> Plan:
    """Build a deterministic ``Plan`` from the session's ``Profile`` and ``Policy``.

    Never stores raw user prompts; uses a safe profile-based or truncated summary.
    """
    pid = plan_id or generate_plan_id(now)
    steps: list[PlanStep] = []
    allowed_servers = set(profile.mcp_servers.keys())

    for tool_name, tool_policy in sorted(policy.tools.items()):
        server = tool_name.split(".", 1)[0]
        if server not in allowed_servers:
            continue
        effective_tier: Tier = tool_policy.tier
        override = profile.tool_overrides.get(tool_name)
        if isinstance(override, dict) and isinstance(override.get("tier"), str):
            effective_tier = override["tier"]  # type: ignore[assignment]
        if effective_tier == "forbidden":
            continue
        # Default plan includes non-sensitive profile tools; sensitive tools are
        # left off-plan so they require explicit approval if invoked.
        if effective_tier == "sensitive":
            continue
        steps.append(
            PlanStep(
                tool=tool_name,
                tier=effective_tier,
                note=f"Use {tool_name} within {profile.name} profile",
            )
        )

    scopes = [f"mcp:{srv}" for srv in sorted(allowed_servers)]
    scopes.extend(f"egress:{host}" for host in sorted(profile.egress_allowlist))
    safe_summary = truncate_string(
        summary
        or f"Execute task with {profile.name} profile ({len(steps)} planned tools)",
        max_len=200,
    )
    est_budget_usd = profile.budget.max_usd
    plan_hash = compute_plan_hash(
        plan_id=pid,
        session_id=session_id,
        summary=safe_summary,
        profile=profile.name,
        steps=steps,
        scopes=scopes,
        est_budget_usd=est_budget_usd,
    )
    return Plan(
        plan_id=pid,
        session_id=session_id,
        summary=safe_summary,
        profile=profile.name,
        steps=steps,
        scopes=scopes,
        est_budget_usd=est_budget_usd,
        plan_hash=plan_hash,
    )


def _row_to_plan(row: PlanModel) -> Plan:
    steps = [PlanStep.model_validate(item) for item in json.loads(row.steps_json)]
    scopes = [str(item) for item in json.loads(row.scopes_json)]
    expected_hash = compute_plan_hash(
        plan_id=row.plan_id,
        session_id=row.session_id,
        summary=row.summary,
        profile=row.profile,
        steps=steps,
        scopes=scopes,
        est_budget_usd=row.est_budget_usd,
    )
    if expected_hash != row.plan_hash:
        raise PlanValidationError(
            f"Stored plan '{row.plan_id}' failed plan_hash integrity verification."
        )
    return Plan(
        plan_id=row.plan_id,
        session_id=row.session_id,
        summary=row.summary,
        profile=row.profile,
        steps=steps,
        scopes=scopes,
        est_budget_usd=row.est_budget_usd,
        plan_hash=row.plan_hash,
    )


class PlanService:
    """SQLite persistence and lifecycle operations for F-11 plans."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock

    def _now_iso(self) -> str:
        return format_utc_iso(self._clock())

    def create_plan(
        self,
        *,
        session_id: str,
        profile: Profile,
        policy: Policy,
        summary: str | None = None,
    ) -> Plan:
        """Generate and persist a pending ``Plan`` for ``session_id``."""
        now_dt = self._clock()
        now_iso = format_utc_iso(now_dt)
        plan = build_plan_for_profile(
            session_id=session_id,
            profile=profile,
            policy=policy,
            summary=summary,
            now=now_dt,
        )
        with self._session_factory() as db:
            db.add(
                PlanModel(
                    plan_id=plan.plan_id,
                    session_id=plan.session_id,
                    summary=plan.summary,
                    profile=plan.profile,
                    steps_json=canonical_json([s.model_dump() for s in plan.steps]),
                    scopes_json=canonical_json(list(plan.scopes)),
                    est_budget_usd=plan.est_budget_usd,
                    plan_hash=plan.plan_hash,
                    status="pending",
                    created_at=now_iso,
                    updated_at=now_iso,
                )
            )
            db.commit()
        return plan

    def get_plan(self, *, session_id: str, plan_id: str | None = None) -> Plan:
        """Load and hash-verify a stored plan for ``session_id``."""
        with self._session_factory() as db:
            stmt = select(PlanModel).where(PlanModel.session_id == session_id)
            if plan_id is not None:
                stmt = stmt.where(PlanModel.plan_id == plan_id)
            else:
                stmt = stmt.order_by(PlanModel.created_at.desc())
            row = db.scalar(stmt)
            if row is None:
                raise PlanNotFoundError(f"No plan found for session '{session_id}'.")
            return _row_to_plan(row)

    def edit_plan(
        self,
        *,
        session_id: str,
        plan_id: str,
        remove_steps: Sequence[int],
    ) -> Plan:
        """Remove selected 0-based step indices from a pending plan and re-hash."""
        with self._session_factory() as db:
            row = db.scalar(
                select(PlanModel).where(
                    PlanModel.session_id == session_id,
                    PlanModel.plan_id == plan_id,
                )
            )
            if row is None:
                raise PlanNotFoundError(
                    f"Plan '{plan_id}' not found for session '{session_id}'."
                )
            if row.status != "pending":
                raise PlanValidationError(
                    f"Plan '{plan_id}' is already {row.status} and cannot be edited."
                )
            current = _row_to_plan(row)
            remove_set = set(remove_steps)
            for idx in remove_set:
                if idx < 0 or idx >= len(current.steps):
                    raise PlanValidationError(
                        f"Step index {idx} out of range for plan '{plan_id}'."
                    )
            new_steps = [
                step for i, step in enumerate(current.steps) if i not in remove_set
            ]
            new_hash = compute_plan_hash(
                plan_id=current.plan_id,
                session_id=current.session_id,
                summary=current.summary,
                profile=current.profile,
                steps=new_steps,
                scopes=current.scopes,
                est_budget_usd=current.est_budget_usd,
            )
            row.steps_json = canonical_json([s.model_dump() for s in new_steps])
            row.plan_hash = new_hash
            row.updated_at = self._now_iso()
            db.commit()
            return Plan(
                plan_id=current.plan_id,
                session_id=current.session_id,
                summary=current.summary,
                profile=current.profile,
                steps=new_steps,
                scopes=current.scopes,
                est_budget_usd=current.est_budget_usd,
                plan_hash=new_hash,
            )

    def approve_plan(self, *, session_id: str, plan_id: str) -> Plan:
        """Approve a pending plan after verifying its ``plan_hash``."""
        with self._session_factory() as db:
            row = db.scalar(
                select(PlanModel).where(
                    PlanModel.session_id == session_id,
                    PlanModel.plan_id == plan_id,
                )
            )
            if row is None:
                raise PlanNotFoundError(
                    f"Plan '{plan_id}' not found for session '{session_id}'."
                )
            if row.status != "pending":
                raise PlanValidationError(f"Plan '{plan_id}' is already {row.status}.")
            plan = _row_to_plan(row)
            row.status = "approved"
            row.updated_at = self._now_iso()
            db.commit()
            return plan

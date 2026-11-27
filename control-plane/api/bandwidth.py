"""F-15 adaptive live-view bandwidth tiering, hysteresis, and measurement store."""

from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from control_plane.audit.audit_writer import format_utc_iso

BandwidthTier = Literal["video", "screenshots", "text"]

VIDEO_DOWNGRADE_KBPS = 1500.0
SCREENSHOTS_DOWNGRADE_KBPS = 150.0
VIDEO_UPGRADE_KBPS = 1800.0
SCREENSHOTS_UPGRADE_KBPS = 200.0
MAX_REPORTED_KBPS = 10_000_000.0
UPGRADE_STABLE_SAMPLES = 3
UPGRADE_COOLDOWN_SECONDS = 5.0
MAX_MEASUREMENTS_PER_SESSION = 200


def validate_kbps(value: object) -> float:
    """Validate a reported throughput measurement in kbps."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("kbps must be a finite non-negative number.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0 or numeric > MAX_REPORTED_KBPS:
        raise ValueError(
            f"kbps must be a finite number between 0 and {MAX_REPORTED_KBPS}."
        )
    return numeric


@dataclass(frozen=True)
class BandwidthMeasurement:
    """Single recorded client throughput sample and resulting active tier."""

    ts: str
    kbps: float
    tier: BandwidthTier


@dataclass(frozen=True)
class TierTransition:
    """Emitted when a session's adaptive live-view tier changes."""

    previous_tier: BandwidthTier
    tier: BandwidthTier
    kbps: float
    ts: str


@dataclass
class _SessionBandwidthState:
    current_tier: BandwidthTier = "video"
    last_change_at: datetime | None = None
    upgrade_streak: int = 0
    measurements: deque[BandwidthMeasurement] = field(
        default_factory=lambda: deque(maxlen=MAX_MEASUREMENTS_PER_SESSION)
    )


class BandwidthController:
    """Tracks per-session throughput with asymmetric hysteresis (down fast, up slow)."""

    def __init__(
        self,
        *,
        upgrade_stable_samples: int = UPGRADE_STABLE_SAMPLES,
        upgrade_cooldown_seconds: float = UPGRADE_COOLDOWN_SECONDS,
    ) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _SessionBandwidthState] = {}
        self._upgrade_stable_samples = upgrade_stable_samples
        self._upgrade_cooldown_seconds = upgrade_cooldown_seconds

    def record(
        self,
        session_id: str,
        kbps: object,
        *,
        now: datetime | None = None,
    ) -> TierTransition | None:
        """Validate ``kbps``, record measurement, and return any tier transition."""
        valid_kbps = validate_kbps(kbps)
        ts_dt = (
            datetime.now(UTC)
            if now is None
            else (
                now.replace(tzinfo=UTC)
                if now.tzinfo is None or now.utcoffset() is None
                else now.astimezone(UTC)
            )
        )
        ts_iso = format_utc_iso(ts_dt)

        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                state = _SessionBandwidthState()
                self._sessions[session_id] = state

            old_tier = state.current_tier
            new_tier = old_tier

            # Fast downgrade path
            if valid_kbps < SCREENSHOTS_DOWNGRADE_KBPS and old_tier != "text":
                new_tier = "text"
                state.upgrade_streak = 0
            elif valid_kbps < VIDEO_DOWNGRADE_KBPS and old_tier == "video":
                new_tier = "screenshots"
                state.upgrade_streak = 0
            else:
                # Slow stepwise upgrade path with hysteresis + cooldown
                eligible_for_upgrade = False
                candidate_tier: BandwidthTier | None = None
                if old_tier == "text" and valid_kbps >= SCREENSHOTS_UPGRADE_KBPS:
                    eligible_for_upgrade = True
                    candidate_tier = "screenshots"
                elif old_tier == "screenshots" and valid_kbps >= VIDEO_UPGRADE_KBPS:
                    eligible_for_upgrade = True
                    candidate_tier = "video"

                if eligible_for_upgrade and candidate_tier is not None:
                    state.upgrade_streak += 1
                    cooldown_ok = (
                        state.last_change_at is None
                        or (ts_dt - state.last_change_at).total_seconds()
                        >= self._upgrade_cooldown_seconds
                    )
                    if (
                        state.upgrade_streak >= self._upgrade_stable_samples
                        and cooldown_ok
                    ):
                        new_tier = candidate_tier
                        state.upgrade_streak = 0
                else:
                    state.upgrade_streak = 0

            rounded_kbps = round(valid_kbps, 2)
            state.measurements.append(
                BandwidthMeasurement(ts=ts_iso, kbps=rounded_kbps, tier=new_tier)
            )
            if new_tier != old_tier:
                state.current_tier = new_tier
                state.last_change_at = ts_dt
                return TierTransition(
                    previous_tier=old_tier,
                    tier=new_tier,
                    kbps=rounded_kbps,
                    ts=ts_iso,
                )
            return None

    def get_tier(self, session_id: str) -> BandwidthTier:
        """Return current active tier for ``session_id`` (defaults to ``video``)."""
        with self._lock:
            state = self._sessions.get(session_id)
            return state.current_tier if state is not None else "video"

    def get_measurements(self, session_id: str) -> list[BandwidthMeasurement]:
        """Return recorded throughput measurements for ``session_id``."""
        with self._lock:
            state = self._sessions.get(session_id)
            return list(state.measurements) if state is not None else []

    def clear_session(self, session_id: str) -> None:
        """Discard in-memory measurements for a torn-down session."""
        with self._lock:
            self._sessions.pop(session_id, None)


_DEFAULT_CONTROLLER = BandwidthController()


def get_bandwidth_controller() -> BandwidthController:
    """FastAPI dependency returning the process-wide bandwidth controller."""
    return _DEFAULT_CONTROLLER

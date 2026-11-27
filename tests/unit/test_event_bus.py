"""Unit tests for control_plane.events.event_bus."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from control_plane.contracts import EventEnvelope
from control_plane.db.models import EventModel, SessionModel
from control_plane.db.session import create_db_engine, create_session_factory, init_db
from control_plane.events import EventBus, EventBusError
from sqlalchemy import select

SESSION_A = "s_01ABCDEFGHJKMNPTVW12345678"
SESSION_B = "s_01ABCDEFGHJKMNPTVW98765432"


def _seed_session(session_factory, session_id: str) -> None:
    with session_factory() as db:
        db.add(
            SessionModel(
                session_id=session_id,
                state="ACTIVE",
                profile="coding",
                owner_id="owner_test",
                dry_run=0,
                created_at="2026-11-26T00:00:00Z",
                updated_at="2026-11-26T00:00:00Z",
            )
        )
        db.commit()


def _make_bus(db_path: Path):
    engine = create_db_engine(f"sqlite:///{db_path}")
    init_db(engine)
    factory = create_session_factory(engine)
    return engine, factory, EventBus(factory)


def test_sequence_assignment_independent_per_session(tmp_path: Path) -> None:
    """Each session gets independent monotonic sequence numbers starting at 1."""
    engine, factory, bus = _make_bus(tmp_path / "events.db")
    try:
        _seed_session(factory, SESSION_A)
        _seed_session(factory, SESSION_B)

        a1 = bus.publish(
            session_id=SESSION_A,
            event_type="session_state",
            data={"state": "ACTIVE"},
        )
        b1 = bus.publish(
            session_id=SESSION_B,
            event_type="session_state",
            data={"state": "ACTIVE"},
        )
        a2 = bus.publish(
            session_id=SESSION_A,
            event_type="agent_message",
            data={"text": "working"},
        )

        assert isinstance(a1, EventEnvelope)
        assert (a1.seq, a2.seq) == (1, 2)
        assert b1.seq == 1
        assert a1.session_id == SESSION_A
        assert b1.session_id == SESSION_B
    finally:
        engine.dispose()


def test_concurrent_publishing_assigns_contiguous_sequences(tmp_path: Path) -> None:
    """Concurrent publishes produce unique, contiguous 1..N sequences."""
    engine, factory, bus = _make_bus(tmp_path / "concurrent.db")
    try:
        _seed_session(factory, SESSION_A)
        total = 24

        def _worker(idx: int) -> int:
            env = bus.publish(
                session_id=SESSION_A,
                event_type="agent_message",
                data={"text": f"msg-{idx}"},
            )
            return env.seq

        with ThreadPoolExecutor(max_workers=6) as pool:
            seqs = list(pool.map(_worker, range(total)))

        assert sorted(seqs) == list(range(1, total + 1))

        replayed = bus.replay(session_id=SESSION_A, last_seq=0)
        assert [e.seq for e in replayed] == list(range(1, total + 1))
    finally:
        engine.dispose()


def test_replay_ordering_last_seq_and_persistence_across_instances(
    tmp_path: Path,
) -> None:
    """Replay returns seq > last_seq in ascending order across bus instances."""
    db_file = tmp_path / "persist.db"
    engine1, factory1, bus1 = _make_bus(db_file)
    try:
        _seed_session(factory1, SESSION_A)
        for i in range(5):
            bus1.publish(
                session_id=SESSION_A,
                event_type="agent_message",
                data={"text": f"step-{i + 1}"},
            )
    finally:
        engine1.dispose()

    # New engine + new EventBus instance reading the same SQLite DB
    engine2 = create_db_engine(f"sqlite:///{db_file}")
    factory2 = create_session_factory(engine2)
    bus2 = EventBus(factory2)
    try:
        all_events = bus2.replay(session_id=SESSION_A)
        assert [e.seq for e in all_events] == [1, 2, 3, 4, 5]

        tail = bus2.replay(session_id=SESSION_A, last_seq=3)
        assert [e.seq for e in tail] == [4, 5]
        assert [e.data["text"] for e in tail] == ["step-4", "step-5"]

        assert bus2.replay(session_id=SESSION_A, last_seq=5) == []
        assert bus2.replay(session_id=SESSION_A, last_seq=99) == []

        # New publish on bus2 continues from seq 6
        e6 = bus2.publish(
            session_id=SESSION_A,
            event_type="session_ended",
            data={"receipt_url": "/sessions/receipt"},
        )
        assert e6.seq == 6
    finally:
        engine2.dispose()


def test_payload_sanitization_and_rejection_of_invalid_data(tmp_path: Path) -> None:
    """Secrets are stripped, bodies redacted, strings truncated, bad JSON fails."""
    engine, factory, bus = _make_bus(tmp_path / "sanitize.db")
    try:
        _seed_session(factory, SESSION_A)

        long_text = "x" * 300
        env = bus.publish(
            session_id=SESSION_A,
            event_type="tool_call",
            data={
                "call_id": "c1",
                "tool": "files.write",
                "decision": "allow",
                "args_redacted": {
                    "path": "a.txt",
                    "content": "full secret file body",
                    "api_key": "sk-live-secret",
                    "nested": {"authorization": "Bearer abc", "note": long_text},
                },
            },
        )

        # Verify returned envelope & stored JSON are both sanitized
        stored_args = env.data["args_redacted"]
        assert "api_key" not in stored_args
        assert "authorization" not in stored_args["nested"]
        assert stored_args["content"].startswith("[REDACTED ")
        assert len(stored_args["nested"]["note"]) == 200
        assert stored_args["nested"]["note"].endswith("...")

        with factory() as db:
            row = db.scalar(
                select(EventModel).where(
                    EventModel.session_id == SESSION_A, EventModel.seq == env.seq
                )
            )
            assert row is not None
            assert "sk-live-secret" not in row.data
            assert "Bearer abc" not in row.data
            assert "full secret file body" not in row.data

        # Non-finite floats and non-JSON objects must fail safely
        for bad_payload in [
            {"value": float("nan")},
            {"value": float("inf")},
            {"obj": object()},
            ["not", "a", "mapping"],
        ]:
            with pytest.raises(EventBusError):
                bus.publish(
                    session_id=SESSION_A,
                    event_type="agent_message",
                    data=bad_payload,  # type: ignore[arg-type]
                )
    finally:
        engine.dispose()


def test_invalid_session_event_type_and_last_seq_fail_safely(tmp_path: Path) -> None:
    """Unknown sessions, bad event types, and bad last_seq raise EventBusError."""
    engine, factory, bus = _make_bus(tmp_path / "errors.db")
    try:
        _seed_session(factory, SESSION_A)

        with pytest.raises(EventBusError):
            bus.publish(
                session_id="s_01NONEXISTENT0000000000000",
                event_type="agent_message",
                data={"text": "nope"},
            )

        with pytest.raises(EventBusError):
            bus.publish(
                session_id=SESSION_A,
                event_type="invented_event",  # type: ignore[arg-type]
                data={"text": "nope"},
            )

        with pytest.raises(EventBusError):
            bus.replay(session_id="s_01NONEXISTENT0000000000000")

        for bad_seq in [-1, True, "1"]:
            with pytest.raises(EventBusError):
                bus.replay(
                    session_id=SESSION_A,
                    last_seq=bad_seq,  # type: ignore[arg-type]
                )
    finally:
        engine.dispose()


def test_malformed_persisted_event_data_fails_safely(tmp_path: Path) -> None:
    """Corrupted JSON, non-object JSON, or invalid type in DB raises EventBusError."""
    engine, factory, bus = _make_bus(tmp_path / "corrupt.db")
    try:
        _seed_session(factory, SESSION_A)

        # Case 1: invalid JSON in data column
        with factory() as db:
            db.add(
                EventModel(
                    session_id=SESSION_A,
                    seq=1,
                    ts="2026-11-26T00:00:00Z",
                    type="agent_message",
                    data="{bad json",
                )
            )
            db.commit()

        with pytest.raises(EventBusError, match="Malformed JSON"):
            bus.replay(session_id=SESSION_A)

        # Case 2: non-object JSON in data column
        with factory() as db:
            row = db.scalar(select(EventModel).where(EventModel.seq == 1))
            assert row is not None
            row.data = "[1, 2, 3]"
            db.commit()

        with pytest.raises(EventBusError, match="JSON object"):
            bus.replay(session_id=SESSION_A)

        # Case 3: invalid EventType in type column
        with factory() as db:
            row = db.scalar(select(EventModel).where(EventModel.seq == 1))
            assert row is not None
            row.data = "{}"
            row.type = "unknown_type"
            db.commit()

        with pytest.raises(EventBusError, match="Malformed persisted event"):
            bus.replay(session_id=SESSION_A)
    finally:
        engine.dispose()

# DATABASE SPECIFICATION (PHASE 0 APPROVED)

> [!NOTE]
> **STATUS: APPROVED SPECIFICATION**  
> This specification documents the owner-approved database design for Phase 0. The initial migration covers the four core MVP tables: `sessions`, `audit_entries`, `events`, and `approvals`. Later-phase tables (`connections`, `plans`, `undo_records`, `device_push_tokens`) are deferred.

---

## 1. Overview & Baseline Requirements

### 1.1 Non-Negotiable Architectural Rules
1. **Engine**: SQLite via SQLAlchemy ([AGENTS.md](AGENTS.md)).
2. **Migrations**: Alembic managed migrations ([BUILD_PLAN.md](BUILD_PLAN.md)).
3. **Audit & Session Immutability**: SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers are defined on `audit_entries` to guarantee an append-only audit trail. A SQLite `BEFORE DELETE` trigger is defined on `sessions` to reject deletion of session records ([BUILD_PLAN.md](BUILD_PLAN.md), [TESTING.md](TESTING.md), [SECURITY.md](SECURITY.md)).
4. **Audit & Session Retention**: `ENDED` session records and all audit rows are permanently retained. Neither audit entries nor session records are ever hard-deleted. Retaining sessions avoids foreign-key cascade deletion conflicts with the audit triggers. Furthermore, Alembic migration downgrades explicitly refuse to drop tables if `sessions` or `audit_entries` contain populated records.
5. **Caller Identity & Ownership**: `sessions.owner_id` is required and stable, derived from the authenticated caller identity. Bearer tokens must never be written to the database.
6. **Zero Raw Secrets / Privacy**: Plaintext tokens, secrets, unredacted tool arguments, raw tool results, and raw `request_text` must never be written to persistent tables ([SECURITY.md](SECURITY.md)).
7. **Dynamic URLs**: Short-lived, session-scoped token-bearing URLs (`stream_url`, `view_url`) are issued dynamically by the API at call time and are never persisted in the database.
8. **Foreign Keys**: Enforced on all SQLite database connections via `PRAGMA foreign_keys = ON;`.
9. **Durable Storage**: SQLite data is stored durably in a named Docker volume (`overwatch-data`) mounted at `/app/data/overwatch.db` in Compose. Local development uses a configurable `DATABASE_URL` (defaulting to local `sqlite:///./overwatch.db`).

---

## 2. Phase 0 Core Tables (Approved for Initial Migration)

### Table 1: `sessions` (F-01, F-05, F-08)
Tracks session lifecycle state, environment profile binding, and ownership.

#### Columns
| Column Name | SQLite Type | Nullable | Description / Contract Mapping |
|---|---|---|---|
| `session_id` | `TEXT` | NO | Primary key ULID (`s_...`) |
| `state` | `TEXT` | NO | `CHECK (state IN ('CREATED', 'PLANNING', 'AWAITING_PLAN_APPROVAL', 'STARTING', 'ACTIVE', 'PAUSED', 'ENDING', 'ENDED', 'FAILED'))` |
| `profile` | `TEXT` | NO | Environment profile name (`coding`, `general`) |
| `owner_id` | `TEXT` | NO | Stable caller/device identity; never bearer tokens |
| `dry_run` | `INTEGER` | NO | `0` or `1`; default `0` |
| `container_id` | `TEXT` | YES | Docker container ID running the session |
| `container_ip` | `TEXT` | YES | Internal management network IP |
| `receipt_url` | `TEXT` | YES | Relative URL/path of generated audit receipt (optional) |
| `created_at` | `TEXT` | NO | UTC ISO 8601 creation timestamp |
| `updated_at` | `TEXT` | NO | UTC ISO 8601 last update timestamp |

#### Retention Trigger
```sql
CREATE TRIGGER sessions_prevent_delete
BEFORE DELETE ON sessions
BEGIN
    SELECT RAISE(ABORT, 'Session records are permanent: DELETE not allowed');
END;
```

---

### Table 2: `audit_entries` (F-07)
Stores the hash-chained, append-only audit log for all gateway decisions.

#### Columns
| Column Name | SQLite Type | Nullable | Description / Contract Mapping |
|---|---|---|---|
| `session_id` | `TEXT` | NO | Foreign key references `sessions(session_id)` |
| `seq` | `INTEGER` | NO | Monotonic sequence number per session |
| `ts` | `TEXT` | NO | UTC ISO 8601 timestamp |
| `tool` | `TEXT` | NO | Tool name called (e.g. `files.write`) |
| `args_redacted` | `TEXT` | NO | Canonical JSON of redacted tool arguments |
| `decision` | `TEXT` | NO | `CHECK (decision IN ('allowed', 'asked', 'asked_approved', 'asked_denied', 'blocked', 'dry_run'))` |
| `reason` | `TEXT` | NO | Error code or rationale string |
| `tier` | `TEXT` | YES | Policy tier (`read`, `reversible`, `sensitive`, `forbidden`) or `NULL` |
| `undo_id` | `TEXT` | YES | Prefixed ULID (`u_...`) if reversible write |
| `duration_ms` | `REAL` | YES | Tool execution duration in milliseconds |
| `prev_hash` | `TEXT` | NO | SHA-256 hash of previous audit row |
| `hash` | `TEXT` | NO | `sha256(prev_hash + canonical_json(entry without hash))` |

#### Constraints & Indices
- **Primary Key**: `(session_id, seq)`
- **Index**: `CREATE INDEX idx_audit_lookup ON audit_entries(session_id, decision, seq);`

#### Immutability Triggers
```sql
CREATE TRIGGER audit_entries_prevent_update
BEFORE UPDATE ON audit_entries
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable: UPDATE not allowed');
END;

CREATE TRIGGER audit_entries_prevent_delete
BEFORE DELETE ON audit_entries
BEGIN
    SELECT RAISE(ABORT, 'Audit log entries are immutable: DELETE not allowed');
END;
```

---

### Table 3: `events` (F-10)
Stores sequenced WebSocket event frames to enable reconnection replay from `last_seq`.

#### Columns
| Column Name | SQLite Type | Nullable | Description / Contract Mapping |
|---|---|---|---|
| `session_id` | `TEXT` | NO | Foreign key references `sessions(session_id)` |
| `seq` | `INTEGER` | NO | Monotonic sequence number per session |
| `ts` | `TEXT` | NO | UTC ISO 8601 timestamp |
| `type` | `TEXT` | NO | EventType string ([CONTRACTS.md](CONTRACTS.md) Section 9) |
| `data` | `TEXT` | NO | Canonical JSON of redacted/sanitized event payload |

#### Constraints & Indices
- **Primary Key**: `(session_id, seq)`
- **Index**: `CREATE INDEX idx_events_replay ON events(session_id, seq);`

---

### Table 4: `approvals` (F-06)
Tracks pending and resolved human-in-the-loop approval cards.

#### Columns
| Column Name | SQLite Type | Nullable | Description / Contract Mapping |
|---|---|---|---|
| `approval_id` | `TEXT` | NO | Primary key ULID (`a_...`) |
| `session_id` | `TEXT` | NO | Foreign key references `sessions(session_id)` |
| `call_id` | `TEXT` | NO | Tool call ID awaiting approval |
| `tool` | `TEXT` | NO | Tool name (e.g. `files.upload`) |
| `tier` | `TEXT` | NO | Risk tier (`read`, `reversible`, `sensitive`, `forbidden`) |
| `args_redacted` | `TEXT` | NO | Redacted arguments JSON |
| `reason` | `TEXT` | NO | Rationale shown on card |
| `irreversible` | `INTEGER` | NO | `1` if irreversible, `0` otherwise |
| `warnings` | `TEXT` | YES | JSON array of warning strings |
| `expires_at` | `TEXT` | NO | UTC ISO 8601 expiration timestamp |
| `status` | `TEXT` | NO | `CHECK (status IN ('pending', 'approved', 'denied', 'timed_out'))`; default `'pending'` |
| `resolved_by` | `TEXT` | YES | `'user'` or `'timeout'` |
| `resolved_at` | `TEXT` | YES | UTC ISO 8601 resolution timestamp |

---

### 2.1 Migration Downgrade Protection (Retention Guard)
To prevent accidental destruction of permanent audit and session records:
- The initial Alembic migration (`0001_initial_core_tables.py`) checks table record counts in `downgrade()`. If rows exist in either `sessions` or `audit_entries`, the downgrade raises an error and refuses to drop any tables, guaranteeing that permanent history cannot be dropped on a populated database.
- Migration `0002_add_asked_audit_decision.py` checks `audit_entries` for any row with `decision = 'asked'` in `downgrade()`. If any `'asked'` rows exist, the downgrade raises an error and refuses to revert the `CHECK` constraint.

---

## 3. Deferred Tables (Implemented in Later Phases)

The following tables are explicitly deferred and are **not** part of the initial Phase 0 migration:

1. **`connections` (Deferred to Phase 7 / F-18)**:
   - Stores encrypted OAuth/provider credentials.
   - Requires `VAULT_KEY` encryption at rest (`connections.secret_enc`).
2. **`plans` (Deferred to Phase 6 / F-11)**:
   - Stores generated multi-step plans and approval hashes.
3. **`undo_records` (Deferred to Phase 6 / F-13)**:
   - Stores compensating inverse operations for reversible tools.
4. **`device_push_tokens` (Deferred until push notification delivery implementation)**:
   - Push tokens must be encrypted with a dedicated `PUSH_TOKEN_KEY`, associated with a stable caller/device, and purged when revoked or rejected by provider gateways.

"""Shared, cross-profile persistence for incident-scoped responder tracking (rebuilt from
scratch to replace an earlier, rejected area-co-location design -- see
`persistence/schema.py::INCIDENT_RESPONDERS_TABLE_DDL`'s own docstring). Links an identity (a
person's `telegram_identity`, or an apparatus's `apparatus_id`) to the one specific `events` row
it is actually responding to. Never answers "who else is with me" from area co-location alone:
someone merely stationed in an area is not the same as someone responding to an incident there.

Connection-per-operation against the SAME db_path as the profile's own core `events` table
(`profiles.response_team.DB_PATH` / `profiles.firefighting.DB_PATH`), matching every other store
in this codebase. Runs its own `CREATE TABLE IF NOT EXISTS` DDL (imported from
`persistence/schema.py`, the single source of truth also used by `persistence.schema.MIGRATIONS`)
so it is self-sufficient even when nothing has constructed a `SQLitePersistence` against that
same file yet (e.g. in a test that only builds one profile-owned agent).

One shared mechanism for every profile: response_team's roster agent and firefighting's crew
status agent both open this same store class against their own respective db_path, rather than
each profile growing its own copy of this resolution logic.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from persistence.schema import INCIDENT_RESPONDERS_TABLE_DDL

# How far back a real event can be and still count as something an identity could be
# joining "now": long enough to span an evolving incident's own response window (SEC_001's
# phase 2/3 unfolds over tens of minutes to a few hours from first report to resolution),
# short enough that a report from a much earlier shift is never mistaken for something
# currently being responded to. Not derived from any corpus constant -- an explicit,
# documented choice for this mechanism specifically.
CANDIDATE_WINDOW_HOURS = 4


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentResponderStore:
    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(INCIDENT_RESPONDERS_TABLE_DDL)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def find_candidate_events(self, area: str, *, now: str | None = None) -> list[dict]:
        """Real, persisted, non-retracted `events` rows in `area` within
        `CANDIDATE_WINDOW_HOURS` hours -- the only source of candidacy. Never falls back to
        area co-location or general roster availability."""

        cleaned = area.strip()
        reference = now or _utc_now()
        since = (datetime.fromisoformat(reference) - timedelta(hours=CANDIDATE_WINDOW_HOURS)).isoformat()
        with self._connect() as conn:
            try:
                # COALESCE(occurred_at, received_at): same precedent-lookback fix as
                # `SQLitePersistence.fetch_events_by_type_area_window` -- an event whose
                # occurred_at is unresolved must not be silently excluded.
                rows = conn.execute(
                    "SELECT event_id, classification, description, occurred_at, received_at "
                    "FROM events WHERE area = ? AND retracted = 0 "
                    "AND COALESCE(occurred_at, received_at) >= ? AND COALESCE(occurred_at, received_at) <= ? "
                    "ORDER BY COALESCE(occurred_at, received_at)",
                    (cleaned, since, reference),
                ).fetchall()
            except sqlite3.OperationalError:
                # The core `events` table is created by `persistence.schema.run_migrations`
                # (via `SQLitePersistence.__init__`), not by this store -- in production some
                # `SQLitePersistence` for this same db_path always runs before any tool call.
                # A profile agent built standalone (e.g. in a unit test) with no events table
                # yet simply has no candidates, not a crash.
                return []
        return [dict(row) for row in rows]

    def resolve_single_candidate(self, area: str, *, now: str | None = None) -> tuple[dict | None, str | None]:
        """The one candidate-resolution used identically by the join and the list/read path.
        Returns (event, None) when exactly one recent, non-retracted incident is on record for
        `area`; otherwise (None, a plain-language explanation) -- never a guess."""

        cleaned = area.strip()
        candidates = self.find_candidate_events(cleaned, now=now)
        if not candidates:
            return None, f"No recent incident is on record for '{cleaned}'."
        if len(candidates) > 1:
            return None, (
                f"Multiple recent incidents are on record for '{cleaned}'; which one is meant is unclear."
            )
        return candidates[0], None

    def find_open_link(self, identity: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM incident_responders WHERE identity = ? AND left_at IS NULL", (identity,)
            ).fetchone()
        return dict(row) if row is not None else None

    def join(self, event_id: str, identity: str) -> dict:
        """Idempotent: already holding the sole open link to `event_id` for `identity`
        leaves that link unchanged. Reassignment: any other open link for `identity` is
        closed (left_at set) before the new one is opened."""

        now = _utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM incident_responders WHERE identity = ? AND event_id = ? AND left_at IS NULL",
                (identity, event_id),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            conn.execute(
                "UPDATE incident_responders SET left_at = ? WHERE identity = ? AND left_at IS NULL",
                (now, identity),
            )
            conn.execute(
                "INSERT INTO incident_responders (event_id, identity, joined_at, left_at) VALUES (?, ?, ?, NULL)",
                (event_id, identity, now),
            )
            row = conn.execute(
                "SELECT * FROM incident_responders WHERE identity = ? AND event_id = ? AND left_at IS NULL",
                (identity, event_id),
            ).fetchone()
        return dict(row)

    def leave(self, identity: str) -> dict | None:
        """Closes `identity`'s own open link, if any. Returns the closed row, or None when
        there was nothing open -- never an error, since leaving with nothing to leave is a
        normal, harmless outcome."""

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM incident_responders WHERE identity = ? AND left_at IS NULL", (identity,)
            ).fetchone()
            if existing is None:
                return None
            now = _utc_now()
            conn.execute("UPDATE incident_responders SET left_at = ? WHERE link_id = ?", (now, existing["link_id"]))
        closed = dict(existing)
        closed["left_at"] = now
        return closed

    def list_open_responders(self, event_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incident_responders WHERE event_id = ? AND left_at IS NULL ORDER BY joined_at",
                (event_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def open_incident_responder_store(db_path: str) -> IncidentResponderStore:
    return IncidentResponderStore(db_path)

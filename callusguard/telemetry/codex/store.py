"""Storage backends for parsed Codex sessions.

Default: SQLite at ~/.codex-logger/codex.db (zero-config, works immediately).
Optional: Postgres (mirrors cc-logger's Neon layout) when a postgresql:// URL is
given, so Claude Code + Codex telemetry can share one warehouse. Every row carries
source='codex' so a future UNION view against cc-logger is trivial.

Idempotent: rollout files are append-only, so on any size/mtime change we re-parse
the whole (small) file and upsert. Session totals are cumulative, so a full
re-parse is the correct way to refresh them.
"""
from __future__ import annotations

import os
import sqlite3
from typing import Optional

from .parse import Session

DEFAULT_DB = os.path.expanduser("~/.codex-logger/codex.db")

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id        TEXT PRIMARY KEY,
    source            TEXT DEFAULT 'codex',
    parent_thread_id  TEXT,
    thread_source     TEXT,
    originator        TEXT,
    subagent_type     TEXT,
    cwd               TEXT,
    cli_version       TEXT,
    model_provider    TEXT,
    model             TEXT,
    started_at        TEXT,
    ended_at          TEXT,
    num_turns         INTEGER,
    num_tool_calls    INTEGER,
    input_tokens      INTEGER,
    cached_input_tokens INTEGER,
    output_tokens     INTEGER,
    reasoning_tokens  INTEGER,
    total_tokens      INTEGER,
    rollout_path      TEXT,
    ingested_at       TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS tool_calls (
    session_id  TEXT,
    call_id     TEXT,
    seq         INTEGER,
    tool_name   TEXT,
    arguments   TEXT,
    output      TEXT,
    exit_code   INTEGER,
    status      TEXT,
    ts          TEXT,
    PRIMARY KEY (session_id, call_id)
);
CREATE TABLE IF NOT EXISTS messages (
    session_id  TEXT,
    seq         INTEGER,
    role        TEXT,
    phase       TEXT,
    text        TEXT,
    ts          TEXT,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS turns (
    session_id  TEXT,
    turn_id     TEXT,
    model       TEXT,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    total_tokens INTEGER,
    ts          TEXT,
    PRIMARY KEY (session_id, turn_id)
);
CREATE TABLE IF NOT EXISTS ingest_state (
    rollout_path TEXT PRIMARY KEY,
    size         INTEGER,
    mtime        REAL,
    session_id   TEXT,
    updated_at   TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_model ON sessions(model);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
"""

TRUNCATE_LEN = 200_000  # guard against a runaway tool output blowing up a row


def _clip(v: Optional[str]) -> Optional[str]:
    if v is not None and len(v) > TRUNCATE_LEN:
        return v[:TRUNCATE_LEN] + f"\n...[truncated {len(v) - TRUNCATE_LEN} chars]"
    return v


class SQLiteStore:
    def __init__(self, path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SQLITE_SCHEMA)
        self.conn.commit()

    # --- ingest bookkeeping -------------------------------------------------
    def needs_ingest(self, path: str, size: int, mtime: float) -> bool:
        row = self.conn.execute(
            "SELECT size, mtime FROM ingest_state WHERE rollout_path=?", (path,)
        ).fetchone()
        if row is None:
            return True
        return row["size"] != size or row["mtime"] != mtime

    def mark_ingested(self, path: str, size: int, mtime: float, session_id: str):
        self.conn.execute(
            "INSERT INTO ingest_state(rollout_path,size,mtime,session_id,updated_at) "
            "VALUES(?,?,?,?,datetime('now')) "
            "ON CONFLICT(rollout_path) DO UPDATE SET "
            "size=excluded.size, mtime=excluded.mtime, "
            "session_id=excluded.session_id, updated_at=datetime('now')",
            (path, size, mtime, session_id),
        )

    # --- upserts ------------------------------------------------------------
    def upsert_session(self, s: Session):
        self.conn.execute(
            """INSERT INTO sessions(
                session_id, source, parent_thread_id, thread_source, originator,
                subagent_type, cwd, cli_version, model_provider, model,
                started_at, ended_at, num_turns, num_tool_calls,
                input_tokens, cached_input_tokens, output_tokens, reasoning_tokens,
                total_tokens, rollout_path, ingested_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(session_id) DO UPDATE SET
                parent_thread_id=excluded.parent_thread_id,
                thread_source=excluded.thread_source,
                originator=excluded.originator,
                subagent_type=excluded.subagent_type,
                cwd=excluded.cwd, cli_version=excluded.cli_version,
                model_provider=excluded.model_provider, model=excluded.model,
                started_at=excluded.started_at, ended_at=excluded.ended_at,
                num_turns=excluded.num_turns, num_tool_calls=excluded.num_tool_calls,
                input_tokens=excluded.input_tokens,
                cached_input_tokens=excluded.cached_input_tokens,
                output_tokens=excluded.output_tokens,
                reasoning_tokens=excluded.reasoning_tokens,
                total_tokens=excluded.total_tokens,
                rollout_path=excluded.rollout_path,
                ingested_at=datetime('now')""",
            (
                s.session_id, "codex", s.parent_thread_id, s.thread_source,
                s.originator, s.subagent_type, s.cwd, s.cli_version,
                s.model_provider, s.model, s.started_at, s.ended_at,
                s.num_turns, s.num_tool_calls, s.input_tokens,
                s.cached_input_tokens, s.output_tokens, s.reasoning_tokens,
                s.total_tokens, s.rollout_path,
            ),
        )
        for tc in s.tool_calls:
            self.conn.execute(
                """INSERT INTO tool_calls(
                    session_id, call_id, seq, tool_name, arguments, output,
                    exit_code, status, ts)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id, call_id) DO UPDATE SET
                    seq=excluded.seq, tool_name=excluded.tool_name,
                    arguments=excluded.arguments, output=excluded.output,
                    exit_code=excluded.exit_code, status=excluded.status,
                    ts=excluded.ts""",
                (s.session_id, tc.call_id, tc.seq, tc.tool_name,
                 _clip(tc.arguments), _clip(tc.output), tc.exit_code,
                 tc.status, tc.ts),
            )
        for m in s.messages:
            self.conn.execute(
                """INSERT INTO messages(session_id, seq, role, phase, text, ts)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(session_id, seq) DO UPDATE SET
                    role=excluded.role, phase=excluded.phase,
                    text=excluded.text, ts=excluded.ts""",
                (s.session_id, m.seq, m.role, m.phase, _clip(m.text), m.ts),
            )
        for t in s.turns:
            self.conn.execute(
                """INSERT INTO turns(session_id, turn_id, model, input_tokens,
                    cached_input_tokens, output_tokens, reasoning_tokens,
                    total_tokens, ts)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(session_id, turn_id) DO UPDATE SET
                    model=excluded.model, input_tokens=excluded.input_tokens,
                    cached_input_tokens=excluded.cached_input_tokens,
                    output_tokens=excluded.output_tokens,
                    reasoning_tokens=excluded.reasoning_tokens,
                    total_tokens=excluded.total_tokens, ts=excluded.ts""",
                (s.session_id, t.turn_id, t.model, t.input_tokens,
                 t.cached_input_tokens, t.output_tokens, t.reasoning_tokens,
                 t.total_tokens, t.ts),
            )

    def commit(self):
        self.conn.commit()

    def query(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchall()

    def close(self):
        self.conn.close()


def open_store(db: Optional[str] = None):
    """Return a store for a path/URL. sqlite by default; postgres for a URL."""
    db = db or os.environ.get("CODEX_LOGGER_DB") or DEFAULT_DB
    if db.startswith("postgres://") or db.startswith("postgresql://"):
        from .store_pg import PostgresStore  # lazy: psycopg only needed here
        return PostgresStore(db)
    return SQLiteStore(db)

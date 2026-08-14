"""Postgres backend — mirrors the SQLite schema so Codex telemetry can live in
the same Neon warehouse as cc-logger (every row carries source='codex').

Lazy-imported by store.open_store() only when a postgresql:// URL is given, so
psycopg is an optional dependency. Untested against a live DB in this repo's CI;
the DDL and upserts mirror the SQLite path 1:1.
"""
from __future__ import annotations

from .parse import Session
from .store import _clip

PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS codex_sessions (
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
    started_at        TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    num_turns         INTEGER,
    num_tool_calls    INTEGER,
    input_tokens      BIGINT,
    cached_input_tokens BIGINT,
    output_tokens     BIGINT,
    reasoning_tokens  BIGINT,
    total_tokens      BIGINT,
    rollout_path      TEXT,
    ingested_at       TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS codex_tool_calls (
    session_id TEXT, call_id TEXT, seq INTEGER, tool_name TEXT,
    arguments TEXT, output TEXT, exit_code INTEGER, status TEXT, ts TIMESTAMPTZ,
    PRIMARY KEY (session_id, call_id)
);
CREATE TABLE IF NOT EXISTS codex_messages (
    session_id TEXT, seq INTEGER, role TEXT, phase TEXT, text TEXT, ts TIMESTAMPTZ,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS codex_turns (
    session_id TEXT, turn_id TEXT, model TEXT, input_tokens BIGINT,
    cached_input_tokens BIGINT, output_tokens BIGINT, reasoning_tokens BIGINT,
    total_tokens BIGINT, ts TIMESTAMPTZ,
    PRIMARY KEY (session_id, turn_id)
);
CREATE TABLE IF NOT EXISTS codex_ingest_state (
    rollout_path TEXT PRIMARY KEY, size BIGINT, mtime DOUBLE PRECISION,
    session_id TEXT, updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_codex_tool_calls_session ON codex_tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_codex_sessions_model ON codex_sessions(model);
"""


class PostgresStore:
    def __init__(self, url: str):
        import psycopg  # lazy
        self.conn = psycopg.connect(url, autocommit=False)
        with self.conn.cursor() as cur:
            cur.execute(PG_SCHEMA)
        self.conn.commit()

    def needs_ingest(self, path, size, mtime):
        with self.conn.cursor() as cur:
            cur.execute("SELECT size, mtime FROM codex_ingest_state WHERE rollout_path=%s", (path,))
            row = cur.fetchone()
        return row is None or row[0] != size or row[1] != mtime

    def mark_ingested(self, path, size, mtime, session_id):
        with self.conn.cursor() as cur:
            cur.execute(
                "INSERT INTO codex_ingest_state(rollout_path,size,mtime,session_id,updated_at) "
                "VALUES(%s,%s,%s,%s,now()) ON CONFLICT(rollout_path) DO UPDATE SET "
                "size=EXCLUDED.size, mtime=EXCLUDED.mtime, session_id=EXCLUDED.session_id, updated_at=now()",
                (path, size, mtime, session_id))

    def upsert_session(self, s: Session):
        with self.conn.cursor() as cur:
            cur.execute(
                """INSERT INTO codex_sessions(session_id,source,parent_thread_id,thread_source,
                    originator,subagent_type,cwd,cli_version,model_provider,model,started_at,
                    ended_at,num_turns,num_tool_calls,input_tokens,cached_input_tokens,
                    output_tokens,reasoning_tokens,total_tokens,rollout_path,ingested_at)
                   VALUES(%s,'codex',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
                   ON CONFLICT(session_id) DO UPDATE SET
                    parent_thread_id=EXCLUDED.parent_thread_id, thread_source=EXCLUDED.thread_source,
                    originator=EXCLUDED.originator, subagent_type=EXCLUDED.subagent_type,
                    cwd=EXCLUDED.cwd, cli_version=EXCLUDED.cli_version,
                    model_provider=EXCLUDED.model_provider, model=EXCLUDED.model,
                    started_at=EXCLUDED.started_at, ended_at=EXCLUDED.ended_at,
                    num_turns=EXCLUDED.num_turns, num_tool_calls=EXCLUDED.num_tool_calls,
                    input_tokens=EXCLUDED.input_tokens, cached_input_tokens=EXCLUDED.cached_input_tokens,
                    output_tokens=EXCLUDED.output_tokens, reasoning_tokens=EXCLUDED.reasoning_tokens,
                    total_tokens=EXCLUDED.total_tokens, rollout_path=EXCLUDED.rollout_path,
                    ingested_at=now()""",
                (s.session_id, s.parent_thread_id, s.thread_source, s.originator,
                 s.subagent_type, s.cwd, s.cli_version, s.model_provider, s.model,
                 s.started_at, s.ended_at, s.num_turns, s.num_tool_calls, s.input_tokens,
                 s.cached_input_tokens, s.output_tokens, s.reasoning_tokens,
                 s.total_tokens, s.rollout_path))
            for tc in s.tool_calls:
                cur.execute(
                    """INSERT INTO codex_tool_calls(session_id,call_id,seq,tool_name,arguments,
                        output,exit_code,status,ts) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(session_id,call_id) DO UPDATE SET seq=EXCLUDED.seq,
                        tool_name=EXCLUDED.tool_name, arguments=EXCLUDED.arguments,
                        output=EXCLUDED.output, exit_code=EXCLUDED.exit_code,
                        status=EXCLUDED.status, ts=EXCLUDED.ts""",
                    (s.session_id, tc.call_id, tc.seq, tc.tool_name, _clip(tc.arguments),
                     _clip(tc.output), tc.exit_code, tc.status, tc.ts))
            for m in s.messages:
                cur.execute(
                    """INSERT INTO codex_messages(session_id,seq,role,phase,text,ts)
                       VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(session_id,seq) DO UPDATE SET
                        role=EXCLUDED.role, phase=EXCLUDED.phase, text=EXCLUDED.text, ts=EXCLUDED.ts""",
                    (s.session_id, m.seq, m.role, m.phase, _clip(m.text), m.ts))
            for t in s.turns:
                cur.execute(
                    """INSERT INTO codex_turns(session_id,turn_id,model,input_tokens,
                        cached_input_tokens,output_tokens,reasoning_tokens,total_tokens,ts)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(session_id,turn_id) DO UPDATE SET model=EXCLUDED.model,
                        input_tokens=EXCLUDED.input_tokens, cached_input_tokens=EXCLUDED.cached_input_tokens,
                        output_tokens=EXCLUDED.output_tokens, reasoning_tokens=EXCLUDED.reasoning_tokens,
                        total_tokens=EXCLUDED.total_tokens, ts=EXCLUDED.ts""",
                    (s.session_id, t.turn_id, t.model, t.input_tokens, t.cached_input_tokens,
                     t.output_tokens, t.reasoning_tokens, t.total_tokens, t.ts))

    def commit(self):
        self.conn.commit()

    def query(self, sql, params=()):
        # translate sqlite ? placeholders + datetime() to postgres for the CLI's queries
        sql = sql.replace("?", "%s").replace(
            "datetime('now', %s)", "now() + (%s)::interval")
        with self.conn.cursor(row_factory=_dict_row()) as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def close(self):
        self.conn.close()


def _dict_row():
    from psycopg.rows import dict_row
    return dict_row

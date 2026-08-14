"""codex-logger CLI: ingest Codex rollout files and inspect the telemetry.

    python3 -m codex_logger ingest [--once] [--watch] [--force] [--verbose]
    python3 -m codex_logger sessions [--limit N] [--days N]
    python3 -m codex_logger inspect <session-id-prefix>
    python3 -m codex_logger stats [--days N]

DB target: --db, or $CODEX_LOGGER_DB, else ~/.codex-logger/codex.db (SQLite).
Pass a postgresql:// URL to co-locate with cc-logger.
"""
from __future__ import annotations

import argparse
import sys

from .ingest import DEFAULT_SESSIONS_DIR, ingest_once, watch
from .store import open_store


def _fmt_int(n):
    return f"{n:,}" if isinstance(n, int) else (n or "")


def cmd_ingest(a):
    if a.watch:
        watch(db=a.db, sessions_dir=a.sessions_dir, interval=a.interval)
        return
    s = ingest_once(db=a.db, sessions_dir=a.sessions_dir, force=a.force,
                    verbose=a.verbose)
    print(f"scanned {s['scanned']}  ingested {s['ingested']}  "
          f"skipped {s['skipped']}  empty {s['empty']}")


def cmd_sessions(a):
    store = open_store(a.db)
    where = ""
    params: list = []
    if a.days:
        where = "WHERE started_at >= datetime('now', ?)"
        params.append(f"-{int(a.days)} days")
    rows = store.query(
        f"""SELECT session_id, model, originator, subagent_type, num_tool_calls,
                   total_tokens, started_at, cwd
            FROM sessions {where}
            ORDER BY started_at DESC LIMIT ?""",
        (*params, a.limit),
    )
    if not rows:
        print("no sessions ingested yet — run:  python3 -m codex_logger ingest")
        return
    print(f"{'session':10} {'model':18} {'origin':14} {'sub':10} "
          f"{'calls':>5} {'tokens':>9}  started")
    for r in rows:
        sid = (r["session_id"] or "")[:8]
        print(f"{sid:10} {(r['model'] or '-'):18.18} "
              f"{(r['originator'] or '-'):14.14} {(r['subagent_type'] or '-'):10.10} "
              f"{_fmt_int(r['num_tool_calls'] or 0):>5} "
              f"{_fmt_int(r['total_tokens'] or 0):>9}  {r['started_at'] or ''}")
    store.close()


def cmd_inspect(a):
    store = open_store(a.db)
    rows = store.query(
        "SELECT * FROM sessions WHERE session_id LIKE ? ORDER BY started_at DESC LIMIT 1",
        (a.session + "%",),
    )
    if not rows:
        print(f"no session matching {a.session!r}")
        return
    s = rows[0]
    print(f"session   {s['session_id']}")
    print(f"model     {s['model']}  ({s['model_provider']})")
    print(f"origin    {s['originator']}  subagent={s['subagent_type']}  "
          f"thread_source={s['thread_source']}")
    if s["parent_thread_id"]:
        print(f"parent    {s['parent_thread_id']}")
    print(f"cwd       {s['cwd']}")
    print(f"cli       {s['cli_version']}")
    print(f"time      {s['started_at']} -> {s['ended_at']}")
    print(f"tokens    in={_fmt_int(s['input_tokens'])} "
          f"cached={_fmt_int(s['cached_input_tokens'])} "
          f"out={_fmt_int(s['output_tokens'])} "
          f"reasoning={_fmt_int(s['reasoning_tokens'])} "
          f"total={_fmt_int(s['total_tokens'])}")
    print(f"turns     {s['num_turns']}   tool_calls {s['num_tool_calls']}")
    calls = store.query(
        "SELECT seq, tool_name, status, exit_code, arguments FROM tool_calls "
        "WHERE session_id=? ORDER BY seq", (s["session_id"],))
    if calls:
        print("\ntool calls:")
        for c in calls:
            arg = (c["arguments"] or "").replace("\n", " ")
            print(f"  {c['seq']:>3} {(c['tool_name'] or '?'):16.16} "
                  f"{(c['status'] or ''):8.8} {arg[:90]}")
    store.close()


def cmd_stats(a):
    store = open_store(a.db)
    where = ""
    params: list = []
    if a.days:
        where = "WHERE started_at >= datetime('now', ?)"
        params.append(f"-{int(a.days)} days")
    print("by model:")
    for r in store.query(
        f"""SELECT model, COUNT(*) n, SUM(num_tool_calls) calls,
                   SUM(total_tokens) tok
            FROM sessions {where} GROUP BY model ORDER BY tok DESC""", tuple(params)):
        print(f"  {(r['model'] or '-'):20.20} {r['n']:>4} sessions "
              f"{_fmt_int(r['calls'] or 0):>7} calls  {_fmt_int(r['tok'] or 0):>11} tok")
    print("\ntop tools:")
    for r in store.query(
        """SELECT tool_name, COUNT(*) n,
                  SUM(CASE WHEN status='failure' THEN 1 ELSE 0 END) fails
           FROM tool_calls GROUP BY tool_name ORDER BY n DESC LIMIT 15"""):
        print(f"  {(r['tool_name'] or '?'):20.20} {r['n']:>6}  "
              f"{r['fails'] or 0} failures")
    store.close()


def main(argv=None):
    p = argparse.ArgumentParser(prog="codex-logger",
                                description="Codex CLI telemetry from rollout files")
    p.add_argument("--db", help="sqlite path or postgresql:// URL "
                                 "(default $CODEX_LOGGER_DB or ~/.codex-logger/codex.db)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("ingest", help="scan sessions dir and load changed files")
    pi.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR)
    pi.add_argument("--once", action="store_true", help="(default) one pass")
    pi.add_argument("--watch", action="store_true", help="poll continuously")
    pi.add_argument("--interval", type=float, default=10.0)
    pi.add_argument("--force", action="store_true", help="re-parse even if unchanged")
    pi.add_argument("--verbose", action="store_true")
    pi.set_defaults(func=cmd_ingest)

    ps = sub.add_parser("sessions", help="list recent sessions")
    ps.add_argument("--limit", type=int, default=30)
    ps.add_argument("--days", type=int)
    ps.set_defaults(func=cmd_sessions)

    px = sub.add_parser("inspect", help="show one session in detail")
    px.add_argument("session", help="session id (or unique prefix)")
    px.set_defaults(func=cmd_inspect)

    pt = sub.add_parser("stats", help="token + tool aggregates")
    pt.add_argument("--days", type=int)
    pt.set_defaults(func=cmd_stats)

    a = p.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    sys.exit(main())

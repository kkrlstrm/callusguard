"""Walk ~/.codex/sessions and ingest changed rollout files into the store."""
from __future__ import annotations

import os
import time
from typing import Optional

from .parse import parse_session
from .store import open_store

DEFAULT_SESSIONS_DIR = os.path.expanduser("~/.codex/sessions")


def find_rollouts(sessions_dir: str = DEFAULT_SESSIONS_DIR):
    for root, _dirs, files in os.walk(sessions_dir):
        for fn in files:
            if fn.startswith("rollout-") and fn.endswith(".jsonl"):
                yield os.path.join(root, fn)


def ingest_once(db: Optional[str] = None,
                sessions_dir: str = DEFAULT_SESSIONS_DIR,
                force: bool = False,
                verbose: bool = False) -> dict:
    store = open_store(db)
    stats = {"scanned": 0, "ingested": 0, "skipped": 0, "empty": 0}
    try:
        for path in find_rollouts(sessions_dir):
            stats["scanned"] += 1
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not force and not store.needs_ingest(path, st.st_size, st.st_mtime):
                stats["skipped"] += 1
                continue
            session = parse_session(path)
            if session is None:
                stats["empty"] += 1
                continue
            store.upsert_session(session)
            store.mark_ingested(path, st.st_size, st.st_mtime, session.session_id)
            store.commit()
            stats["ingested"] += 1
            if verbose:
                print(f"  ingested {session.session_id[:8]}  "
                      f"{session.num_tool_calls:>3} calls  "
                      f"{session.total_tokens:>8} tok  {os.path.basename(path)}")
    finally:
        store.close()
    return stats


def watch(db: Optional[str] = None,
          sessions_dir: str = DEFAULT_SESSIONS_DIR,
          interval: float = 10.0,
          verbose: bool = True):
    """Poll the sessions dir on an interval, ingesting new/changed files."""
    print(f"watching {sessions_dir} every {interval:.0f}s (Ctrl-C to stop)")
    while True:
        s = ingest_once(db=db, sessions_dir=sessions_dir, verbose=verbose)
        if s["ingested"]:
            print(f"[{time.strftime('%H:%M:%S')}] +{s['ingested']} sessions "
                  f"({s['skipped']} unchanged)")
        time.sleep(interval)

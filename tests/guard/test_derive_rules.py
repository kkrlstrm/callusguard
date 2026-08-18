#!/usr/bin/env python3
"""Tests for bin/derive_rules.py — the telemetry -> candidate-rules path.

Uses the zero-dependency --from-log mode (no DB needed). Verifies clustering,
thresholding, MCP-namespace aggregation, and that derived candidates are valid
rules the engine accepts.
"""
import os
import sys
import json
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "callusguard", "derive"))

from callusguard.guard import engine  # noqa: E402
import rules as derive_rules  # noqa: E402

LOG = [
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 1'"}, "status": "failure", "error": 'FATAL: database "a" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 2'"}, "status": "failure", "error": 'FATAL: database "b" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 3'"}, "status": "failure", "error": 'FATAL: database "c" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "ls"}, "status": "success"},
    {"tool_name": "mcp__Acme__run_sql", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "mcp__Acme__run_sql", "tool_input": {}, "status": "failure", "error": "403 forbidden 9"},
    {"tool_name": "mcp__Acme__list_projects", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "WebFetch", "tool_input": {"url": "http://x"}, "status": "failure", "error": "timeout"},
]


class DeriveRules(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode="w")
        for row in LOG:
            self.tmp.write(json.dumps(row) + "\n")
        self.tmp.close()
        self.path = self.tmp.name

    def tearDown(self):
        os.unlink(self.path)

    def _candidates(self, min_count=3):
        rows = derive_rules.load_rows_from_log(self.path, days=7, min_count=min_count)
        return derive_rules.derive_from_rows(rows, 7, min_count=min_count)

    def test_bash_cluster_becomes_command_pattern(self):
        cands = self._candidates()
        psql = [c for c in cands if c["tool"] == "Bash"]
        self.assertEqual(len(psql), 1)
        self.assertIn(r"\bpsql\b", psql[0]["any"])
        self.assertEqual(psql[0]["action"], "monitor")
        self.assertEqual(psql[0]["meta"]["fail_count"], 3)

    def test_mcp_namespace_aggregation(self):
        # 2 run_sql + 1 list_projects = 3 across the mcp__Acme__ surface.
        cands = self._candidates()
        server = [c for c in cands if c["tool"] == "mcp__Acme__*"]
        self.assertEqual(len(server), 1)
        self.assertEqual(server[0]["meta"]["fail_count"], 3)
        self.assertNotIn("any", server[0])  # tool-wide

    def test_threshold_excludes_singletons(self):
        cands = self._candidates(min_count=3)
        self.assertFalse(any(c["tool"] == "WebFetch" for c in cands))  # only 1 failure

    def test_candidates_are_valid_rules(self):
        cands = self._candidates()
        self.assertEqual(engine.verify_rules(cands), [])
        # And they all ship as monitor (never auto-armed).
        self.assertTrue(all(c["action"] == "monitor" for c in cands))

    def test_normalize_clusters_across_ids(self):
        a = derive_rules.normalize_error('FATAL: database "kai" does not exist')
        b = derive_rules.normalize_error('FATAL: database "bob" does not exist')
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ExcludedTools(unittest.TestCase):
    """Read-only / context tools must not become candidate rules by default.

    A telemetry logger may capture context-attribution tools — cc-logger records
    `Read` and `Skill` so you can tell which instruction or skill a run loaded. Their
    "failures" are ordinary agent behaviour (a Read that misses is the agent probing
    whether a file exists), and they are high-volume enough to clear the threshold
    every window. Deriving on them puts the same junk candidate in every review.
    """

    ROWS = [
        {"tool_name": "Read", "error_signature": "file not found", "fail_count": 47,
         "sample_error": "ENOENT: no such file"},
        {"tool_name": "Skill", "error_signature": "unknown skill", "fail_count": 5,
         "sample_error": "no such skill"},
        {"tool_name": "WebFetch", "error_signature": "timeout", "fail_count": 9,
         "sample_error": "timeout"},
    ]

    def _tools(self, candidates):
        return {c.get("tool") for c in candidates}

    def test_read_and_skill_are_excluded_by_default(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertNotIn("Read", self._tools(out))
        self.assertNotIn("Skill", self._tools(out))

    def test_real_failures_still_derive(self):
        """The exclusion must be surgical — a genuinely failing tool still surfaces."""
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertIn("WebFetch", self._tools(out))

    def test_include_tool_opts_a_default_exclusion_back_in(self):
        excluded = derive_rules.DEFAULT_EXCLUDED_TOOLS - {"Read"}
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3,
                                            excluded_tools=excluded)
        self.assertIn("Read", self._tools(out))

    def test_explicit_empty_set_disables_the_filter(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3, excluded_tools=set())
        self.assertIn("Read", self._tools(out))

    def test_bash_is_never_excluded_by_default(self):
        self.assertNotIn("Bash", derive_rules.DEFAULT_EXCLUDED_TOOLS)

    def test_excluding_everything_yields_no_candidates(self):
        out = derive_rules.derive_from_rows(
            self.ROWS, 7, min_count=3, excluded_tools={"Read", "Skill", "WebFetch"})
        self.assertEqual(out, [])

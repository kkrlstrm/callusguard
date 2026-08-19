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
from callusguard.core import tiers  # noqa: E402
import rules as derive_rules  # noqa: E402

#: Five psql attempts, three of which failed — enough attempts to be gradeable
#: (>= tiers.MIN_ATTEMPTS) and a 60% failure rate, i.e. `reproducible`.
#:
#: The two successful psql lines are load-bearing, and were added when tiering
#: landed. Before that a cluster was three failures and a shrug; now three
#: failures out of three attempts and three out of thirty grade differently, so a
#: fixture without successes could only ever produce one tier.
LOG = [
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 1'"}, "status": "failure", "error": 'FATAL: database "a" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 2'"}, "status": "failure", "error": 'FATAL: database "b" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -c 'SELECT 3'"}, "status": "failure", "error": 'FATAL: database "c" does not exist'},
    {"tool_name": "Bash", "tool_input": {"command": "psql -d ok -c 'SELECT 4'"}, "status": "success"},
    {"tool_name": "Bash", "tool_input": {"command": "psql -d ok -c 'SELECT 5'"}, "status": "success"},
    {"tool_name": "Bash", "tool_input": {"command": "ls"}, "status": "success"},
    # 3 failures over 6 calls on the mcp__Acme__ surface -> reproducible.
    {"tool_name": "mcp__Acme__run_sql", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "mcp__Acme__run_sql", "tool_input": {}, "status": "failure", "error": "403 forbidden 9"},
    {"tool_name": "mcp__Acme__list_projects", "tool_input": {}, "status": "failure", "error": "401 unauthorized"},
    {"tool_name": "mcp__Acme__run_sql", "tool_input": {}, "status": "success"},
    {"tool_name": "mcp__Acme__list_projects", "tool_input": {}, "status": "success"},
    {"tool_name": "mcp__Acme__list_projects", "tool_input": {}, "status": "success"},
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

    def test_successes_become_the_denominator(self):
        """The psql cluster is 3 failures over 5 psql attempts, not 3 over 3."""
        rows = derive_rules.load_rows_from_log(self.path, days=7, min_count=3)
        psql = [r for r in rows if r["command_shape"] == "psql"]
        self.assertEqual(len(psql), 1)
        self.assertEqual(psql[0]["fail_count"], 3)
        self.assertEqual(psql[0]["attempt_count"], 5)

    def test_candidate_carries_its_tier_and_ceiling(self):
        cands = self._candidates()
        psql = [c for c in cands if c["tool"] == "Bash"][0]
        self.assertEqual(psql["meta"]["tier"], tiers.REPRODUCIBLE)   # 3/5 = 60%
        self.assertEqual(psql["meta"]["fail_rate"], 0.6)
        self.assertEqual(psql["meta"]["action_ceiling"], "deny")

    def test_mcp_denominator_is_the_server_surface_not_the_sum(self):
        """Two failing methods on one server share ONE denominator (6), not 12."""
        cands = self._candidates()
        server = [c for c in cands if c["tool"] == "mcp__Acme__*"][0]
        self.assertEqual(server["meta"]["fail_count"], 3)
        self.assertEqual(server["meta"]["attempt_count"], 6)
        self.assertEqual(server["meta"]["tier"], tiers.REPRODUCIBLE)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class AnecdotalIsWithheld(unittest.TestCase):
    """Thin evidence must not become a rule, and must not vanish silently either."""

    ROWS = [
        # 3 failures out of 3 attempts: a 100% rate on a sample of three.
        {"tool_name": "WebFetch", "error_signature": "timeout", "fail_count": 3,
         "attempt_count": 3, "sample_error": "timeout"},
    ]

    def test_thin_evidence_is_not_proposed(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertEqual(out, [], "3-of-3 is anecdotal, not deterministic")

    def test_the_withheld_cluster_is_reported_to_the_caller(self):
        dropped = []
        derive_rules.derive_from_rows(self.ROWS, 7, min_count=3, dropped_out=dropped)
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0][2]["tier"], tiers.ANECDOTAL)

    def test_keep_anecdotal_surfaces_it_capped_at_monitor(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3,
                                            keep_anecdotal=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["meta"]["tier"], tiers.ANECDOTAL)
        self.assertEqual(out[0]["meta"]["action_ceiling"], "monitor")

    def test_more_attempts_promote_the_same_rate_out_of_anecdotal(self):
        """Identical 100% rate, larger sample -> deterministic. The rate did not
        change; the confidence did, which is the whole point of the tier."""
        rows = [dict(self.ROWS[0], fail_count=12, attempt_count=12)]
        out = derive_rules.derive_from_rows(rows, 7, min_count=3)
        self.assertEqual(out[0]["meta"]["tier"], tiers.DETERMINISTIC)
        self.assertEqual(out[0]["meta"]["action_ceiling"], "block")


class MissingDenominator(unittest.TestCase):
    """No attempt count is `unknown` — never a free deterministic grade."""

    ROWS = [{"tool_name": "WebFetch", "error_signature": "timeout", "fail_count": 40,
             "sample_error": "timeout"}]

    def test_rows_without_attempts_tier_unknown(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertEqual(out[0]["meta"]["tier"], tiers.UNKNOWN)
        self.assertIsNone(out[0]["meta"]["attempt_count"])
        self.assertIsNone(out[0]["meta"]["fail_rate"])

    def test_unknown_is_capped_at_a_nudge(self):
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertEqual(out[0]["meta"]["action_ceiling"], "nudge")

    def test_unknown_is_still_proposed(self):
        """Unknown is not anecdotal: we could not look, so we do not withhold."""
        out = derive_rules.derive_from_rows(self.ROWS, 7, min_count=3)
        self.assertEqual(len(out), 1)


class FailuresOnlyLog(unittest.TestCase):
    """A log filtered down to failures must not manufacture a 100% failure rate."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl", mode="w")
        for i in range(9):
            self.tmp.write(json.dumps({
                "tool_name": "Bash",
                "tool_input": {"command": f"psql -c 'SELECT {i}'"},
                "status": "failure",
                "error": 'FATAL: database "x" does not exist',
            }) + "\n")
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_no_successes_means_no_denominator(self):
        rows = derive_rules.load_rows_from_log(self.tmp.name, days=7, min_count=3)
        self.assertNotIn("attempt_count", rows[0])

    def test_and_therefore_tiers_unknown_not_deterministic(self):
        rows = derive_rules.load_rows_from_log(self.tmp.name, days=7, min_count=3)
        out = derive_rules.derive_from_rows(rows, 7, min_count=3)
        self.assertEqual(out[0]["meta"]["tier"], tiers.UNKNOWN)


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

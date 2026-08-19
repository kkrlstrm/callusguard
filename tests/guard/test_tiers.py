#!/usr/bin/env python3
"""Tests for core/tiers.py and the two places the tier is ENFORCED rather than shown.

A tier that only decorated a rule would be a comment. The tests that matter here
are the ones proving it changes an outcome: `engine.verify_rules` refusing an
over-armed rule, and `lifecycle.classify` declining to promote a popular rule with
a weak failure rate.
"""
import os
import sys
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from callusguard.core import tiers  # noqa: E402
from callusguard.guard import engine, lifecycle  # noqa: E402


class Classify(unittest.TestCase):
    def test_high_rate_over_a_real_sample_is_deterministic(self):
        tier, rate = tiers.classify(20, 20)
        self.assertEqual(tier, tiers.DETERMINISTIC)
        self.assertEqual(rate, 1.0)

    def test_majority_failure_is_reproducible(self):
        self.assertEqual(tiers.classify(6, 10)[0], tiers.REPRODUCIBLE)

    def test_minority_failure_is_probabilistic(self):
        self.assertEqual(tiers.classify(3, 30)[0], tiers.PROBABILISTIC)

    def test_the_sample_size_gate_outranks_the_rate(self):
        """4-of-4 is 100% and still says nothing. This is the boundary the old
        `--min-count 3` heuristic could not express."""
        self.assertEqual(tiers.classify(4, 4)[0], tiers.ANECDOTAL)
        self.assertEqual(tiers.classify(5, 5)[0], tiers.DETERMINISTIC)

    def test_no_denominator_is_unknown_not_deterministic(self):
        self.assertEqual(tiers.classify(99, 0)[0], tiers.UNKNOWN)
        self.assertEqual(tiers.classify(99, None)[0], tiers.UNKNOWN)
        self.assertIsNone(tiers.classify(99, None)[1])

    def test_boundaries_are_inclusive(self):
        self.assertEqual(tiers.classify(95, 100)[0], tiers.DETERMINISTIC)
        self.assertEqual(tiers.classify(50, 100)[0], tiers.REPRODUCIBLE)
        self.assertEqual(tiers.classify(49, 100)[0], tiers.PROBABILISTIC)

    def test_a_denominator_smaller_than_the_numerator_clamps(self):
        """Bad data must not yield a 300% failure rate in a report."""
        tier, rate = tiers.classify(30, 10)
        self.assertEqual(rate, 1.0)
        self.assertEqual(tier, tiers.DETERMINISTIC)

    def test_garbage_input_is_unknown_not_a_crash(self):
        self.assertEqual(tiers.classify("x", "y")[0], tiers.UNKNOWN)

    def test_an_unrecognised_tier_gets_the_strictest_ceiling(self):
        """An unknown label must never widen authority."""
        self.assertEqual(tiers.ceiling("something-new"), "monitor")

    def test_describe_always_carries_the_sample_size(self):
        text = tiers.describe(tiers.REPRODUCIBLE, 0.6, 5)
        self.assertIn("60%", text)
        self.assertIn("5 attempts", text)


class CeilingIsEnforced(unittest.TestCase):
    """engine.verify_rules refuses enforcement that outran its evidence."""

    def _rule(self, action, tier):
        return {"id": "r1", "tool": "Bash", "any": ["psql"], "action": action,
                "message": "m", "meta": {"tier": tier}}

    def test_block_from_a_probabilistic_tier_is_refused(self):
        problems = engine.verify_rules([self._rule("block", tiers.PROBABILISTIC)])
        self.assertEqual(len(problems), 1)
        self.assertIn("exceeds the ceiling", problems[0])

    def test_block_from_a_deterministic_tier_is_allowed(self):
        self.assertEqual(engine.verify_rules([self._rule("block", tiers.DETERMINISTIC)]), [])

    def test_deny_is_the_reproducible_ceiling(self):
        self.assertEqual(engine.verify_rules([self._rule("deny", tiers.REPRODUCIBLE)]), [])
        self.assertEqual(len(engine.verify_rules([self._rule("block", tiers.REPRODUCIBLE)])), 1)

    def test_unknown_tier_cannot_be_armed_past_a_nudge(self):
        self.assertEqual(engine.verify_rules([self._rule("nudge", tiers.UNKNOWN)]), [])
        self.assertEqual(len(engine.verify_rules([self._rule("deny", tiers.UNKNOWN)])), 1)

    def test_an_untiered_rule_is_left_alone(self):
        """Hand-written rules have no telemetry behind them and are governed by
        review. Capping them here would break every ruleset shipped in the repo."""
        rule = {"id": "r1", "tool": "Bash", "any": ["rm -rf /"], "action": "block",
                "message": "no"}
        self.assertEqual(engine.verify_rules([rule]), [])

    def test_the_shipped_rulesets_still_validate(self):
        import glob
        import json
        rules_dir = os.path.join(REPO, "callusguard", "guard", "rules")
        found = glob.glob(os.path.join(rules_dir, "*.json"))
        self.assertTrue(found, "no shipped rulesets found — check the path")
        for path in found:
            with open(path) as fh:
                rules = json.load(fh).get("rules", [])
            self.assertEqual(engine.verify_rules(rules), [],
                             "%s stopped validating" % os.path.basename(path))


class PromoteRequiresTheRate(unittest.TestCase):
    """A rule graduates on how reliably the command FAILED, not on how often the
    pattern MATCHED. The two numbers are different, and only one is grounds to arm."""

    def _stat(self, count):
        return {"count": count, "last_seen": None, "actions": {"monitor"}}

    def test_popular_but_flaky_stays_at_monitor(self):
        rule = {"id": "r", "action": "monitor", "meta": {"tier": tiers.PROBABILISTIC}}
        verdict, reason = lifecycle.classify(rule, self._stat(50), 30)
        self.assertEqual(verdict, lifecycle.KEEP)
        self.assertIn("not the same as failing reliably", reason)

    def test_reproducible_promotes_with_its_ceiling_named(self):
        rule = {"id": "r", "action": "monitor", "meta": {"tier": tiers.REPRODUCIBLE}}
        verdict, reason = lifecycle.classify(rule, self._stat(7), 30)
        self.assertEqual(verdict, lifecycle.PROMOTE)
        self.assertIn("deny", reason)

    def test_deterministic_may_promote_to_block(self):
        rule = {"id": "r", "action": "monitor", "meta": {"tier": tiers.DETERMINISTIC}}
        verdict, reason = lifecycle.classify(rule, self._stat(7), 30)
        self.assertEqual(verdict, lifecycle.PROMOTE)
        self.assertIn("block", reason)

    def test_an_untiered_monitor_rule_keeps_the_old_behaviour(self):
        """Pre-tiering rules must not silently stop being proposed for promotion."""
        rule = {"id": "r", "action": "monitor"}
        verdict, _ = lifecycle.classify(rule, self._stat(7), 30)
        self.assertEqual(verdict, lifecycle.PROMOTE)

    def test_below_the_firing_threshold_nothing_promotes(self):
        rule = {"id": "r", "action": "monitor", "meta": {"tier": tiers.DETERMINISTIC}}
        verdict, _ = lifecycle.classify(rule, self._stat(1), 30)
        self.assertEqual(verdict, lifecycle.KEEP)


if __name__ == "__main__":
    unittest.main(verbosity=2)

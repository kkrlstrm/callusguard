"""Rule lifecycle — the stage that makes the name honest.

A callus resolves when the friction is engineered away. These pin that the report
actually says so, and — more importantly — that it cannot quietly recommend deleting
a live rule because the audit log was empty or unreadable.
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)

from callusguard.guard import lifecycle as L  # noqa: E402


def _event(rule_id, action, days_ago, decision="allow"):
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {"ts": ts.isoformat(timespec="seconds"), "tool": "Bash",
            "ruleset": "r.json", "fired": [rule_id], "actions": [action],
            "decision": decision}


class LifecycleBase(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lc-")
        self.audit = os.path.join(self.dir, "audit.jsonl")
        self.ruleset = os.path.join(self.dir, "rules.json")

    def write_audit(self, events):
        with open(self.audit, "w", encoding="utf-8") as fh:
            for e in events:
                fh.write(json.dumps(e) + "\n")

    def write_rules(self, rules):
        with open(self.ruleset, "w", encoding="utf-8") as fh:
            json.dump({"rules": rules}, fh)

    def verdict_for(self, rule_id, data):
        return next(r["verdict"] for r in data["rules"] if r["id"] == rule_id)


class TestClassification(LifecycleBase):

    def test_rule_that_never_fires_is_a_prune_candidate(self):
        self.write_rules([{"id": "ghost", "action": "nudge", "message": "m"}])
        self.write_audit([_event("other", "nudge", 1)])
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(self.verdict_for("ghost", data), L.PRUNE)

    def test_monitor_rule_with_evidence_is_promoted(self):
        self.write_rules([{"id": "cand", "action": "monitor", "message": "m"}])
        self.write_audit([_event("cand", "monitor", i) for i in range(L.PROMOTE_AFTER)])
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(self.verdict_for("cand", data), L.PROMOTE)

    def test_monitor_rule_below_threshold_is_not_promoted(self):
        self.write_rules([{"id": "cand", "action": "monitor", "message": "m"}])
        self.write_audit([_event("cand", "monitor", 1)])
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(self.verdict_for("cand", data), L.KEEP)

    def test_acting_rule_firing_constantly_is_flagged_for_review(self):
        """A guard firing every day is a workflow nobody fixed."""
        self.write_rules([{"id": "noisy", "action": "nudge", "message": "m"}])
        self.write_audit([_event("noisy", "nudge", i % 5)
                          for i in range(L.REVIEW_ABOVE + 5)])
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(self.verdict_for("noisy", data), L.REVIEW)

    def test_settled_rule_is_kept(self):
        self.write_rules([{"id": "calm", "action": "block", "message": "m"}])
        self.write_audit([_event("calm", "block", 2) for _ in range(3)])
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(self.verdict_for("calm", data), L.KEEP)


class TestWindowing(LifecycleBase):

    def test_events_outside_the_window_do_not_count(self):
        self.write_rules([{"id": "old", "action": "nudge", "message": "m"}])
        self.write_audit([_event("old", "nudge", 90)])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertEqual(self.verdict_for("old", data), L.PRUNE)
        self.assertEqual(data["events_in_window"], 0)

    def test_widening_the_window_recovers_the_rule(self):
        self.write_rules([{"id": "old", "action": "nudge", "message": "m"}])
        self.write_audit([_event("old", "nudge", 90)])
        data = L.report(self.ruleset, self.audit, window_days=180)
        self.assertEqual(self.verdict_for("old", data), L.KEEP)


class TestSafety(LifecycleBase):
    """The report must never be quietly wrong in the delete-things direction."""

    def test_missing_audit_log_is_visible_not_silent(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        data = L.report(self.ruleset, os.path.join(self.dir, "nope.jsonl"))
        self.assertEqual(data["events_in_window"], 0)
        # Everything reads as prune — so the renderer must warn that the reason is
        # absent evidence, not evidence of absence.
        self.assertIn("No readable audit log", L.render(data))
        self.assertIn("lack of evidence", L.render(data))

    def test_corrupt_lines_are_skipped_not_fatal(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        with open(self.audit, "w", encoding="utf-8") as fh:
            fh.write("{not json\n")
            fh.write(json.dumps(_event("a", "nudge", 1)) + "\n")
        data = L.report(self.ruleset, self.audit)
        self.assertEqual(data["events_in_window"], 1)
        self.assertEqual(self.verdict_for("a", data), L.KEEP)

    def test_orphan_rules_are_surfaced(self):
        """Fired but no longer declared — usually a rename that half-happened."""
        self.write_rules([{"id": "current", "action": "nudge", "message": "m"}])
        self.write_audit([_event("removed", "nudge", 1)])
        data = L.report(self.ruleset, self.audit)
        self.assertIn("removed", data["orphan_rule_ids"])

    def test_report_never_edits_the_ruleset(self):
        rules = [{"id": "a", "action": "monitor", "message": "m"}]
        self.write_rules(rules)
        self.write_audit([_event("a", "monitor", 1) for _ in range(20)])
        before = open(self.ruleset).read()
        L.report(self.ruleset, self.audit)
        self.assertEqual(open(self.ruleset).read(), before)


class TestRendering(LifecycleBase):

    def test_clean_library_says_so(self):
        self.write_rules([{"id": "a", "action": "block", "message": "m"}])
        self.write_audit([_event("a", "block", 1)])
        self.assertIn("Nothing to cut back", L.render(L.report(self.ruleset, self.audit)))

    def test_prune_message_frames_shrinking_as_success(self):
        self.write_rules([{"id": "ghost", "action": "nudge", "message": "m"}])
        self.write_audit([_event("other", "nudge", 1)])
        out = L.render(L.report(self.ruleset, self.audit))
        self.assertIn("the tool working, not rotting", out)


class TestEmptyLogVersusAgedLog(LifecycleBase):
    """Zero events in the window means two very different things."""

    def test_unreadable_log_warns_and_is_marked_unreadable(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        data = L.report(self.ruleset, os.path.join(self.dir, "absent.jsonl"))
        self.assertFalse(data["audit_readable"])
        self.assertIn("No readable audit log", L.render(data))

    def test_aged_log_is_a_real_prune_signal_not_a_warning(self):
        """Log present, enforcement ran, rule simply stopped firing."""
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([_event("a", "nudge", 60)])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertTrue(data["audit_readable"])
        self.assertEqual(data["events_in_window"], 0)
        self.assertEqual(data["events_total"], 1)
        out = L.render(data)
        self.assertNotIn("No readable audit log", out)
        self.assertIn("every verdict predates the window", out)
        self.assertEqual(self.verdict_for("a", data), L.PRUNE)


class TestUndatedEvents(LifecycleBase):
    """A pre-0.3 guard wrote verdicts with no `ts`. Those cannot be windowed.

    Counting them is the correct fail-open choice — dropping them would invent a
    prune signal out of missing metadata. Counting them *silently* is not: a real
    97-day log of 1,914 undated verdicts rendered as "1916 verdict(s) in the last
    30 days", which is an all-time number wearing a windowed label.
    """

    def _undated(self, rule_id, action="nudge"):
        return {"tool": "Bash", "ruleset": "r.json", "fired": [rule_id],
                "actions": [action], "decision": "allow"}

    def test_undated_events_are_counted_not_dropped(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([self._undated("a")])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertEqual(data["events_in_window"], 1)
        self.assertEqual(self.verdict_for("a", data), L.KEEP)

    def test_undated_events_are_reported_as_such(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([self._undated("a") for _ in range(3)])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertEqual(data["undated_events"], 3)

    def test_all_undated_log_is_labelled_all_time_not_windowed(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([self._undated("a") for _ in range(3)])
        out = L.render(L.report(self.ruleset, self.audit, window_days=30))
        self.assertIn("ALL-TIME", out)
        self.assertNotIn("3 verdict(s) in the last 30 days", out)

    def test_mixed_log_names_the_undated_share(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([_event("a", "nudge", 1), self._undated("a")])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertEqual(data["events_in_window"], 2)
        self.assertEqual(data["undated_events"], 1)
        out = L.render(data)
        self.assertIn("1 of them undated", out)

    def test_fully_dated_log_carries_no_warning(self):
        self.write_rules([{"id": "a", "action": "nudge", "message": "m"}])
        self.write_audit([_event("a", "nudge", 1)])
        data = L.report(self.ruleset, self.audit, window_days=30)
        self.assertEqual(data["undated_events"], 0)
        self.assertNotIn("carry no `ts`", L.render(data))


if __name__ == "__main__":
    unittest.main()

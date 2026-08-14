"""The wrapper must not silently drop a predecessor's commands.

Merging five CLIs behind one front door creates a specific failure: the wrapper
enumerates a subset of the real sub-CLI's verbs, and the rest become unreachable
without any error — they simply are not offered. That happened here. The first cut
declared `choices=("serve", "ingest")` and quietly removed `migrate`, `sessions`,
`inspect`, `insights` and `rate`, all of which worked in cc-logger.

These tests read the verbs out of the actual sub-parsers and assert the wrapper
offers every one. If someone adds a verb to a sub-CLI, this fails until the wrapper
routes it.
"""

import os
import re
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from callusguard import cli  # noqa: E402


def _declared_verbs(source: str) -> set:
    """Sub-parser names as spelled in a sub-CLI's own source."""
    return set(re.findall(r'add_parser\(\s*["\']([a-z][a-z0-9_-]*)["\']', source))


class TestRecordCoverage(unittest.TestCase):

    def test_wrapper_offers_every_claude_recorder_verb(self):
        try:
            import inspect
            from callusguard.telemetry.cc import cli as cc_cli
        except ImportError:
            self.skipTest("telemetry extra not installed")
        real = _declared_verbs(inspect.getsource(cc_cli))
        missing = real - set(cli.CC_VERBS)
        self.assertFalse(
            missing,
            "`callus record` does not route these cc verbs, so they are "
            "unreachable: %s" % sorted(missing))

    def test_wrapper_offers_every_codex_recorder_verb(self):
        try:
            import inspect
            from callusguard.telemetry.codex import cli as codex_cli
        except ImportError:
            self.skipTest("telemetry extra not installed")
        real = _declared_verbs(inspect.getsource(codex_cli))
        offered = set(cli.CODEX_VERBS)
        # The codex CLI's non-ingest verbs (sessions/inspect/stats) overlap the cc
        # names; routing is by recorder, so only `ingest` is claimed here. Assert
        # that explicitly rather than leaving it implicit.
        self.assertIn("ingest", real)
        self.assertEqual(offered, {"ingest"})

    def test_declared_verbs_do_not_collide_between_recorders(self):
        overlap = set(cli.CC_VERBS) & set(cli.CODEX_VERBS)
        self.assertFalse(overlap, "ambiguous routing for: %s" % sorted(overlap))


class TestTopLevelSurface(unittest.TestCase):

    STAGES = ("record", "derive", "guard", "scope")

    def test_every_stage_is_present(self):
        parser = cli.build_parser()
        actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
        names = set()
        for action in actions:
            if action.dest == "command":
                names |= set(action.choices)
        for stage in self.STAGES:
            self.assertIn(stage, names)

    def test_guard_prune_actually_runs(self):
        """The lifecycle stage is what makes the name honest; it must work end to end.

        Asserting it *runs and reports* rather than that `--help` prints: the guard
        parser owns `--help`, and a help-text assertion would pass on a prune that
        was wired but broken.
        """
        import json, tempfile
        d = tempfile.mkdtemp(prefix="cli-")
        audit = os.path.join(d, "audit.jsonl")
        rules = os.path.join(d, "r.json")
        with open(rules, "w") as fh:
            json.dump({"rules": [{"id": "ghost", "action": "nudge", "message": "m"}]}, fh)
        open(audit, "w").close()
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", "callus"), "guard", "prune",
             rules, "--audit", audit], capture_output=True, text=True, cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ghost", proc.stdout)
        self.assertIn("PRUNE", proc.stdout)

    def test_missing_telemetry_extra_instructs_rather_than_crashes(self):
        """On a stdlib-only install, `record` must explain, not traceback."""
        proc = subprocess.run(
            [sys.executable, "-S", os.path.join(REPO, "bin", "callus"),
             "record", "serve"], capture_output=True, text=True, cwd=REPO)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("pip install", proc.stderr)


if __name__ == "__main__":
    unittest.main()

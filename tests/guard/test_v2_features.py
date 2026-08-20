#!/usr/bin/env python3
"""Tests for the v0.2 additions: dotted-field matching, regex lint, enriched +
secret-safe audit events, the rule-validation CLI, and doctor."""
import os
import sys
import json
import tempfile
import subprocess
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, REPO)
from callusguard.guard import engine  # noqa: E402

MAIN = os.path.join(REPO, "bin", "guard-hook.py")


class DottedField(unittest.TestCase):
    def test_get_field_nested_dict(self):
        ti = {"args": {"repository": "acme/prod", "op": "delete"}}
        self.assertEqual(engine.get_field(ti, "args.repository"), "acme/prod")

    def test_get_field_list_index(self):
        ti = {"params": [{"name": "x"}, {"name": "drop"}]}
        self.assertEqual(engine.get_field(ti, "params.1.name"), "drop")

    def test_get_field_missing_returns_empty(self):
        self.assertEqual(engine.get_field({"a": {}}, "a.b.c"), "")

    def test_get_field_nonstring_leaf_is_json(self):
        self.assertIn("delete", engine.get_field({"args": {"ops": ["delete", "x"]}}, "args.ops"))

    def test_rule_matches_nested_mcp_arg(self):
        rule = {"id": "gh-delete", "tool": "mcp__GitHub__*", "field": "args.operation",
                "any": ["delete|force_merge"], "action": "deny", "message": "no"}
        self.assertTrue(engine.rule_fires(rule, "mcp__GitHub__repo", {"args": {"operation": "delete"}}))
        self.assertFalse(engine.rule_fires(rule, "mcp__GitHub__repo", {"args": {"operation": "read"}}))


class RegexLint(unittest.TestCase):
    def test_flags_nested_quantifier(self):
        warns = engine.warn_rules([{"id": "bad", "tool": "Bash", "any": ["(a+)+b"]}])
        self.assertTrue(any("bad" in w for w in warns))

    def test_clean_rule_no_warning(self):
        self.assertEqual(engine.warn_rules([{"id": "ok", "tool": "Bash", "any": ["\\bfoo\\b"]}]), [])

    def test_shipped_rules_have_no_lint_warnings(self):
        for name in ("starter.rules.json", "readonly-db.rules.json"):
            with open(os.path.join(REPO, "callusguard", "guard", "rules", name)) as f:
                rules = json.load(f)["rules"]
            self.assertEqual(engine.warn_rules(rules), [], msg=name)


class AuditEnrichment(unittest.TestCase):
    def _drive(self, command, tool="Bash", extra=None):
        audit_path = tempfile.mktemp(suffix=".jsonl")
        env = dict(os.environ, AGENT_GUARD_AUDIT=audit_path)
        payload = {"tool_name": tool, "tool_input": {"command": command},
                   "session_id": "sess-123", "cwd": "/tmp/proj"}
        payload.update(extra or {})
        subprocess.run([sys.executable, MAIN], input=json.dumps(payload),
                       capture_output=True, text=True, env=env)
        rows = [json.loads(l) for l in open(audit_path) if l.strip()]
        os.unlink(audit_path)
        return rows

    def test_event_has_metadata(self):
        rows = self._drive("psql -c 'SELECT 1'")  # fires bare-psql nudge
        self.assertTrue(rows)
        ev = rows[-1]
        for key in ("ts", "version", "command_sha256", "command_preview", "session_id", "cwd", "dryrun"):
            self.assertIn(key, ev)
        self.assertEqual(ev["session_id"], "sess-123")

    def test_tool_use_id_is_recorded_as_the_telemetry_join_key(self):
        """Without it, "did the nudge work?" can only ever be inferred from rates."""
        rows = self._drive("psql -c 'SELECT 1'", extra={"tool_use_id": "toolu_abc123"})
        self.assertEqual(rows[-1]["tool_use_id"], "toolu_abc123")

    def test_absent_tool_use_id_is_omitted_not_nulled(self):
        """A host that does not send one must not litter the log with nulls."""
        self.assertNotIn("tool_use_id", self._drive("psql -c 'SELECT 1'")[-1])

    def test_secret_is_redacted_not_logged(self):
        secret = "AKIAIOSFODNN7EXAMPLE"
        rows = self._drive(f"AWS_KEY={secret} aws s3 ls")  # fires secret-inline nudge
        ev = rows[-1]
        self.assertNotIn(secret, json.dumps(ev))          # raw secret never persisted
        self.assertIn("***", ev["command_preview"])        # but redacted preview kept
        self.assertEqual(len(ev["command_sha256"]), 64)    # hash gives exact identity


class HostVersionPin(unittest.TestCase):
    """The `block` guarantee is the host's, not ours, and it can change silently.

    doctor must notice a drift — and must never turn one into a failed run, because a
    host upgrade is normal and the guard still works on it.
    """

    def setUp(self):
        from callusguard.guard import doctor
        self.doctor = doctor
        doctor._fails.clear()
        doctor._warns.clear()

    def _with_version(self, text, returncode=0):
        from unittest import mock
        result = subprocess.CompletedProcess([], returncode, stdout=text, stderr="")
        return mock.patch.object(subprocess, "run", return_value=result)

    def test_matching_version_passes_clean(self):
        verified = self.doctor.VERIFIED_HOSTS["claude"]
        with self._with_version("%s (Claude Code)\n" % verified):
            self.doctor.check_hosts()
        self.assertEqual(self.doctor._warns, [])
        self.assertEqual(self.doctor._fails, [])

    def test_drifted_version_warns_and_names_both(self):
        with self._with_version("9.9.9 (Claude Code)\n"):
            self.doctor.check_hosts()
        self.assertEqual(len(self.doctor._warns), 1)
        self.assertIn("9.9.9", self.doctor._warns[0])
        self.assertIn(self.doctor.VERIFIED_HOSTS["claude"], self.doctor._warns[0])

    def test_drift_is_never_a_hard_failure(self):
        with self._with_version("9.9.9 (Claude Code)\n"):
            self.doctor.check_hosts()
        self.assertEqual(self.doctor._fails, [])

    def test_absent_host_is_informational_only(self):
        from unittest import mock
        with mock.patch.object(subprocess, "run", side_effect=OSError):
            self.doctor.check_hosts()
        self.assertEqual(self.doctor._warns, [])
        self.assertEqual(self.doctor._fails, [])


class CLIs(unittest.TestCase):
    def test_check_rules_passes_on_shipped(self):
        proc = subprocess.run(
            [sys.executable, os.path.join(REPO, "bin", "check_rules.py"),
             os.path.join(REPO, "callusguard", "guard", "rules", "starter.rules.json"),
             os.path.join(REPO, "callusguard", "guard", "rules", "readonly-db.rules.json")],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("example assertion", proc.stdout)

    def test_check_rules_fails_on_broken_example(self):
        bad = tempfile.mktemp(suffix=".json")
        json.dump({"rules": [{"id": "x", "tool": "Bash", "any": ["\\bfoo\\b"], "action": "nudge",
                              "message": "m", "examples": {"should_fire": ["bar only"]}}]}, open(bad, "w"))
        proc = subprocess.run([sys.executable, os.path.join(REPO, "bin", "check_rules.py"), bad],
                              capture_output=True, text=True)
        os.unlink(bad)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("example FAIL", proc.stdout)

    def test_doctor_runs_clean(self):
        proc = subprocess.run([sys.executable, os.path.join(REPO, "bin", "doctor.py")],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("HEALTHY", proc.stdout)


class TestDoctorDetectsRealWiring(unittest.TestCase):
    """`doctor --project` must recognise the wiring `install.py` actually writes.

    It did not. `check_project` matched only `pretooluse-guard.py` while the installer
    writes `guard-hook.py`, so a correct install was reported as "guard NOT found" —
    and the remedy it printed, `install.sh`, is not a file in this repo. THREAT_MODEL
    names this command as the mitigation for "disabled or unwired hooks", which makes a
    false negative here the worst kind: the verification step tells a protected user
    they are unprotected.

    These tests build the settings document from the installer's own HOOKS spec rather
    than a copied literal, so adding an entry script without teaching doctor about it
    fails here.
    """

    def setUp(self):
        from callusguard.guard import doctor
        self.doctor = doctor
        doctor._fails.clear()
        doctor._warns.clear()
        doctor._oks.clear() if hasattr(doctor, "_oks") else None

    @staticmethod
    def _project_with(command):
        import json
        import tempfile
        root = tempfile.mkdtemp(prefix="doctor-wiring-")
        os.makedirs(os.path.join(root, ".claude"))
        with open(os.path.join(root, ".claude", "settings.json"), "w") as fh:
            json.dump({"hooks": {"PreToolUse": [
                {"matcher": "Bash|Edit|Write",
                 "hooks": [{"type": "command", "command": command}]}]}}, fh)
        return root

    def _installer_guard_script(self):
        """The guard entry script `install.py` wires, read from the installer itself."""
        import importlib.util
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        spec = importlib.util.spec_from_file_location(
            "_installer", os.path.join(repo, "install.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return os.path.basename(mod.HOOKS["guard"]["script"])

    def test_detects_the_script_the_installer_writes(self):
        script = self._installer_guard_script()
        self.assertIn(script, self.doctor.GUARD_ENTRY_SCRIPTS,
                      "install.py wires %r but doctor does not recognise it" % script)
        root = self._project_with('python3 "/somewhere/bin/%s"' % script)
        self.doctor.check_project(root)
        self.assertEqual(self.doctor._warns, [], "correct wiring reported as missing")
        self.assertEqual(self.doctor._fails, [])

    def test_detects_the_legacy_alias_too(self):
        root = self._project_with('python3 "/somewhere/bin/pretooluse-guard.py"')
        self.doctor.check_project(root)
        self.assertEqual(self.doctor._warns, [])

    def test_genuinely_unwired_project_still_warns(self):
        root = self._project_with('python3 "/somewhere/bin/unrelated-tool.py"')
        self.doctor.check_project(root)
        self.assertEqual(len(self.doctor._warns), 1)
        self.assertNotIn("install.sh", self.doctor._warns[0],
                         "remedy names a file that does not exist in this repo")
        self.assertIn("install.py", self.doctor._warns[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)

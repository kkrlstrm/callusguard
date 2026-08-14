"""stdlib unittest only — no pytest, matching agent-guard and codex-guard.

The hook tests drive the real entry script as a subprocess with a JSON payload on
stdin and assert on exit code and stdout, because the behavioural contract with the
host *is* the exit code and the stdout shape. Testing the Python function instead
would pass while the thing the host actually sees was broken.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOK = os.path.join(REPO, "bin", "wroteonly-hook.py")
CLI = os.path.join(REPO, "bin", "wroteonly")
sys.path.insert(0, REPO)

from callusguard.wroteonly import baseline as B          # noqa: E402
from callusguard.wroteonly import observe as O           # noqa: E402
from callusguard.wroteonly import report as R            # noqa: E402
from callusguard.core import verdict as V           # noqa: E402
from callusguard.wroteonly.declare import (               # noqa: E402
    Declaration, DeclarationError, PatternSet, normalize_path,
)


class TempTree(unittest.TestCase):
    """A scratch tree plus an isolated state dir, torn down after each test."""

    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="wo-test-"))
        self.state = os.path.realpath(tempfile.mkdtemp(prefix="wo-state-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.state, ignore_errors=True)

    def write(self, rel, text="x"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def env(self, **extra):
        env = dict(os.environ)
        env["WROTEONLY_STATE"] = self.state
        env.pop("WROTEONLY_DISABLE", None)
        env.update(extra)
        return env


# ---------------------------------------------------------------------------
# glob semantics — the bug that would silently widen every declaration
# ---------------------------------------------------------------------------

class TestGlobs(unittest.TestCase):

    CASES = [
        # (pattern, path, should_match)
        ("src/*.py", "src/app.py", True),
        ("src/*.py", "src/sub/app.py", False),      # * must not cross a separator
        ("src/**/*.py", "src/sub/deep/app.py", True),
        ("src/**/*.py", "src/app.py", True),        # **/ matches zero directories
        ("**/*.env", "config/prod.env", True),
        ("**/*.env", "prod.env", True),
        ("docs/**", "docs/a/b/c.md", True),
        ("docs/**", "src/a.md", False),
        ("a?c.txt", "abc.txt", True),
        ("a?c.txt", "a/c.txt", False),              # ? must not cross a separator
        ("exact.md", "exact.md", True),
        ("exact.md", "other.md", False),
        ("file+v1.py", "file+v1.py", True),         # regex metachars are escaped
        ("file+v1.py", "fileeev1.py", False),
    ]

    def test_matching(self):
        for pattern, path, expected in self.CASES:
            with self.subTest(pattern=pattern, path=path):
                got = PatternSet([pattern]).match(path) is not None
                self.assertEqual(got, expected)

    def test_fnmatch_would_have_been_wrong(self):
        """Documents why we do not use fnmatch: it crosses separators."""
        import fnmatch
        self.assertTrue(fnmatch.fnmatch("src/sub/app.py", "src/*.py"))
        self.assertIsNone(PatternSet(["src/*.py"]).match("src/sub/app.py"))


class TestNormalizePath(TempTree):

    def test_relative_and_posix(self):
        self.assertEqual(
            normalize_path(os.path.join(self.root, "a", "b.py"), self.root), "a/b.py")

    def test_escape_returns_none(self):
        self.assertIsNone(normalize_path("../outside.py", self.root))
        self.assertIsNone(normalize_path("/etc/passwd", self.root))

    def test_root_itself_is_not_a_path(self):
        self.assertIsNone(normalize_path(".", self.root))


# ---------------------------------------------------------------------------
# declaration
# ---------------------------------------------------------------------------

class TestDeclaration(unittest.TestCase):

    def decl(self, **kw):
        data = {"run_id": "r1"}
        data.update(kw)
        return Declaration.from_dict(data)

    def test_forbid_beats_allow(self):
        d = self.decl(modify=["**/*"], forbid=["**/*.env"])
        ok, why = d.permits("config/prod.env", "modify")
        self.assertFalse(ok)
        self.assertTrue(why.startswith("forbid:"))

    def test_modify_subsumes_create(self):
        d = self.decl(modify=["src/app.py"])
        self.assertTrue(d.permits("src/app.py", "create")[0])

    def test_create_does_not_permit_modify(self):
        d = self.decl(create=["src/new.py"])
        self.assertTrue(d.permits("src/new.py", "create")[0])
        self.assertFalse(d.permits("src/new.py", "modify")[0])

    def test_delete_is_separate(self):
        d = self.decl(modify=["src/app.py"])
        self.assertFalse(d.permits("src/app.py", "delete")[0])

    def test_rejects_bad_fail_direction(self):
        with self.assertRaises(DeclarationError):
            self.decl(fail_direction="sideways")

    def test_rejects_missing_run_id(self):
        with self.assertRaises(DeclarationError):
            Declaration.from_dict({"create": ["a.py"]})

    def test_rejects_check_without_command(self):
        with self.assertRaises(DeclarationError):
            self.decl(checks=[{"name": "lint"}])

    def test_parse_failure_never_degrades_to_empty(self):
        """An unparseable declaration must raise, not become permissive."""
        with self.assertRaises(DeclarationError):
            Declaration.from_json("{not json")
        with self.assertRaises(DeclarationError):
            Declaration.from_dict({"run_id": "r", "create": 7})


# ---------------------------------------------------------------------------
# observation
# ---------------------------------------------------------------------------

class TestSnapshotDiff(TempTree):

    def test_create_modify_delete(self):
        self.write("a.py", "one")
        self.write("b.py", "two")
        before = O.snapshot(self.root)

        self.write("c.py", "three")            # created
        self.write("a.py", "one changed")      # modified
        os.unlink(os.path.join(self.root, "b.py"))   # deleted

        changes = {c["path"]: c["kind"] for c in O.diff(before, O.snapshot(self.root))}
        self.assertEqual(changes,
                         {"c.py": O.CREATED, "a.py": O.MODIFIED, "b.py": O.DELETED})

    def test_identical_rewrite_is_not_a_modification(self):
        """A formatter that rewrites identical bytes did not change anything."""
        self.write("a.py", "same")
        before = O.snapshot(self.root)
        os.utime(os.path.join(self.root, "a.py"), (0, 0))  # move mtime
        self.write("a.py", "same")
        self.assertEqual(O.diff(before, O.snapshot(self.root)), [])

    def test_ignores_noise_directories(self):
        self.write("node_modules/pkg/index.js")
        self.write(".git/objects/ab/cdef")
        self.write("__pycache__/x.pyc")
        snap = O.snapshot(self.root)
        self.assertEqual(snap, {})

    def test_scan_limit_raises(self):
        for i in range(5):
            self.write("f%d.py" % i)
        with self.assertRaises(O.ScanLimitExceeded):
            O.snapshot(self.root, max_files=2)

    def test_attribution_from_hook_stream(self):
        changes = [{"path": "src/app.py", "kind": O.MODIFIED, "hashed": True}]
        obs = [{"tool": "Edit", "tool_use_id": "t9", "paths": ["src/app.py"]}]
        self.assertEqual(O.attribute(changes, obs)[0]["tool"], "Edit")

    def test_attribution_absent_is_not_an_error(self):
        changes = [{"path": "src/app.py", "kind": O.MODIFIED, "hashed": True}]
        self.assertNotIn("tool", O.attribute(changes, [])[0])

    def test_paths_from_tool_input(self):
        self.assertEqual(O.paths_from_tool_input("Write", {"file_path": "/a/b.py"}),
                         ["/a/b.py"])
        # Bash is deliberately not parsed.
        self.assertEqual(O.paths_from_tool_input("Bash", {"command": "rm -rf /"}), [])


# ---------------------------------------------------------------------------
# baseline / new-errors-only
# ---------------------------------------------------------------------------

class TestErrorIdentity(unittest.TestCase):

    def test_line_drift_does_not_create_a_new_finding(self):
        a = B.parse_output("src/a.py:10: E001 unused import 'os'")[0]
        b = B.parse_output("src/a.py:42: E001 unused import 'os'")[0]
        self.assertEqual(a.identity, b.identity)

    def test_different_file_is_a_different_finding(self):
        a = B.parse_output("src/a.py:10: E001 unused import 'os'")[0]
        b = B.parse_output("src/b.py:10: E001 unused import 'os'")[0]
        self.assertNotEqual(a.identity, b.identity)

    def test_different_symbol_is_a_different_finding(self):
        a = B.parse_output("src/a.py:10: E001 unused import 'os'")[0]
        b = B.parse_output("src/a.py:10: E001 unused import 'sys'")[0]
        self.assertNotEqual(a.identity, b.identity)

    def test_unparseable_lines_are_still_findings(self):
        """A checker we cannot parse must never look clean."""
        findings = B.parse_output("something went badly wrong\nand again")
        self.assertEqual(len(findings), 2)
        self.assertEqual(findings[0].path, "")

    def test_new_findings_subtracts_baseline(self):
        before = {"checks": [{"name": "lint", "ok": True, "command": "c",
                              "findings": [B.Finding("a.py", "E1 old", "a.py:1: E1 old",
                                                     code="E1").to_dict()]}]}
        after = {"checks": [{"name": "lint", "ok": True, "command": "c", "findings": [
            B.Finding("a.py", "E1 old", "a.py:1: E1 old", code="E1").to_dict(),
            B.Finding("a.py", "E2 new", "a.py:9: E2 new", code="E2").to_dict(),
        ]}]}
        new, degraded = B.new_findings(before, after)
        self.assertEqual(degraded, [])
        self.assertEqual([f.code for f in new["lint"]], ["E2"])

    def test_failed_baseline_degrades_rather_than_blaming_the_agent(self):
        before = {"checks": [{"name": "lint", "ok": False, "command": "c",
                              "error": "command not found", "findings": []}]}
        after = {"checks": [{"name": "lint", "ok": True, "command": "c", "findings": [
            B.Finding("a.py", "E1 x", "a.py:1: E1 x", code="E1").to_dict()]}]}
        new, degraded = B.new_findings(before, after)
        self.assertEqual(new, {})
        self.assertEqual(len(degraded), 1)

    def test_missing_binary_is_not_a_clean_run(self):
        result = B.run_check("lint", "definitely-not-a-real-binary-xyz", ".")
        self.assertFalse(result.ok)

    def test_nonzero_exit_is_normal_for_a_linter(self):
        result = B.run_check("lint", "echo 'a.py:1: E1 found' && exit 1", ".")
        self.assertTrue(result.ok)
        self.assertEqual(len(result.findings), 1)


# ---------------------------------------------------------------------------
# verdict algebra
# ---------------------------------------------------------------------------

class TestVerdict(unittest.TestCase):

    def test_rejects_non_acs_decision(self):
        with self.assertRaises(ValueError):
            V.Verdict("block")   # agent-guard's word, not an ACS decision

    def test_most_restrictive_wins(self):
        self.assertEqual(
            V.worst([V.Verdict(V.ALLOW), V.Verdict(V.DENY), V.Verdict(V.WARN)]).decision,
            V.DENY)
        self.assertEqual(
            V.worst([V.Verdict(V.WARN), V.Verdict(V.ESCALATE)]).decision, V.ESCALATE)

    def test_empty_composition_is_allow(self):
        self.assertEqual(V.worst([]).decision, V.ALLOW)

    def test_acs_shape_has_no_extra_top_level_keys(self):
        payload = V.Verdict(V.DENY, "wroteonly:x", "m", evidence={"a": 1}).to_dict()
        self.assertLessEqual(set(payload),
                             {"decision", "reason", "message", "transform",
                              "evidence", "result_labels"})

    def test_tooling_failure_honours_fail_direction_and_flags_degraded(self):
        closed = V.tooling_failure(V.R_CHECK_FAILED, "boom", "closed")
        opened = V.tooling_failure(V.R_CHECK_FAILED, "boom", "open")
        self.assertEqual(closed.decision, V.DENY)
        self.assertEqual(opened.decision, V.WARN)
        self.assertTrue(opened.degraded)   # never a silent fail-open


# ---------------------------------------------------------------------------
# the decision table
# ---------------------------------------------------------------------------

class TestReport(TempTree):

    def decl(self, **kw):
        data = {"run_id": "r", "root": self.root}
        data.update(kw)
        return Declaration.from_dict(data, root=self.root)

    def build(self, decl, changes, **kw):
        return R.build(decl, changes, self.root, **kw)[0]

    def test_clean_run_allows(self):
        d = self.decl(create=["docs/a.md"])
        v = self.build(d, [{"path": "docs/a.md", "kind": O.CREATED, "hashed": True}])
        self.assertEqual(v.decision, V.ALLOW)

    def test_undeclared_write_denies(self):
        d = self.decl(create=["docs/a.md"])
        v = self.build(d, [{"path": "src/x.py", "kind": O.CREATED, "hashed": True}])
        self.assertEqual(v.decision, V.DENY)
        self.assertEqual(v.reason, V.R_UNDECLARED_WRITE)

    def test_forbidden_path_denies(self):
        d = self.decl(modify=["**/*"], forbid=["**/*.env"])
        v = self.build(d, [{"path": "a.env", "kind": O.MODIFIED, "hashed": True}])
        self.assertEqual(v.reason, V.R_FORBIDDEN_PATH)

    def test_undeclared_delete_denies(self):
        d = self.decl(modify=["src/**"])
        v = self.build(d, [{"path": "src/x.py", "kind": O.DELETED, "hashed": True}])
        self.assertEqual(v.reason, V.R_UNDECLARED_DELETE)

    def test_no_declaration_but_writes_escalates(self):
        v = self.build(self.decl(), [{"path": "a.py", "kind": O.CREATED,
                                      "hashed": True}])
        self.assertEqual(v.decision, V.ESCALATE)
        self.assertEqual(v.reason, V.R_NOTHING_DECLARED)

    def test_no_declaration_and_no_writes_allows(self):
        self.assertEqual(self.build(self.decl(), []).decision, V.ALLOW)

    def test_new_errors_warn_open_deny_closed(self):
        findings = {"lint": [B.Finding("a.py", "E1 x", "a.py:1: E1 x", code="E1")]}
        self.assertEqual(
            self.build(self.decl(create=["a.py"]), [], new_by_check=findings).decision,
            V.WARN)
        self.assertEqual(
            self.build(self.decl(create=["a.py"], fail_direction="closed"), [],
                       new_by_check=findings).decision,
            V.DENY)

    def test_scan_failure_is_degraded_not_silent(self):
        v = self.build(self.decl(create=["a.py"]), [], scan_ok=False,
                       scan_error="disk on fire")
        self.assertTrue(v.degraded)
        self.assertEqual(v.decision, V.WARN)

    def test_unfulfilled_only_enforced_when_strict(self):
        self.assertEqual(self.build(self.decl(create=["a.py"]), []).decision, V.ALLOW)
        v = self.build(self.decl(create=["a.py"], strict_unfulfilled=True), [])
        self.assertEqual(v.reason, V.R_UNFULFILLED)

    def test_unfulfilled_ignores_wildcards(self):
        d = self.decl(create=["docs/**/*.md"], strict_unfulfilled=True)
        self.assertEqual(self.build(d, []).decision, V.ALLOW)

    def test_escape_outside_root_is_reported(self):
        d = self.decl(modify=["**/*"])
        v = self.build(d, [{"path": "/etc/passwd", "kind": O.MODIFIED, "hashed": True}])
        self.assertEqual(v.decision, V.DENY)


# ---------------------------------------------------------------------------
# hook contract — driven as a subprocess, both hosts
# ---------------------------------------------------------------------------

class TestHookContract(TempTree):

    def hook(self, payload, host=None, **env):
        env_vars = self.env(**env)
        if host:
            env_vars["WROTEONLY_HOST"] = host
        proc = subprocess.run(
            [sys.executable, HOOK], input=json.dumps(payload),
            capture_output=True, text=True, env=env_vars, cwd=self.root)
        return proc

    def declare(self, **kw):
        data = {"intent": "docs only", "create": ["docs/**/*.md"],
                "forbid": ["**/*.env"]}
        data.update(kw)
        with open(os.path.join(self.root, ".wroteonly.json"), "w") as fh:
            json.dump(data, fh)

    def pre(self, path, host):
        return self.hook({
            "hook_event_name": "PreToolUse", "session_id": "s1", "cwd": self.root,
            "tool_name": "Write", "tool_input": {"file_path": path},
            "tool_use_id": "t1",
        }, host=host)

    def stop(self, host, active=False, session="s1"):
        return self.hook({
            "hook_event_name": "Stop", "session_id": session, "cwd": self.root,
            "stop_hook_active": active,
        }, host=host)

    # -- pre-emptive gate ---------------------------------------------------

    def test_pre_blocks_undeclared_write_on_both_hosts(self):
        self.declare()
        for host in ("claude-code", "codex"):
            with self.subTest(host=host):
                proc = self.pre(os.path.join(self.root, "src/x.py"), host)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("outside the declared write set", proc.stderr)

    def test_pre_allows_declared_write_on_both_hosts(self):
        self.declare()
        for host in ("claude-code", "codex"):
            with self.subTest(host=host):
                proc = self.pre(os.path.join(self.root, "docs/a.md"), host)
                self.assertEqual(proc.returncode, 0)
                self.assertEqual(proc.stdout.strip(), "")

    def test_pre_blocks_a_write_outside_the_root(self):
        self.declare()
        proc = self.pre("/etc/passwd", "claude-code")
        self.assertEqual(proc.returncode, 2)

    # -- the Stop gate, per host --------------------------------------------

    def test_stop_claude_code_uses_exit_2(self):
        self.declare()
        self.pre(os.path.join(self.root, "docs/a.md"), "claude-code")  # take baseline
        self.write("src/via_bash.py", "written by a shell command")
        proc = self.stop("claude-code")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("via_bash.py", proc.stderr)

    def test_stop_codex_uses_decision_block_json(self):
        self.declare()
        self.pre(os.path.join(self.root, "docs/a.md"), "codex")
        self.write("src/via_bash.py", "written by a shell command")
        proc = self.stop("codex")
        self.assertEqual(proc.returncode, 0)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("via_bash.py", payload["reason"])

    def test_stop_hook_active_does_not_loop(self):
        self.declare()
        self.pre(os.path.join(self.root, "docs/a.md"), "claude-code")
        self.write("src/via_bash.py", "x")
        proc = self.stop("claude-code", active=True)
        self.assertEqual(proc.returncode, 0)

    def test_clean_run_stops_silently(self):
        self.declare()
        self.pre(os.path.join(self.root, "docs/a.md"), "claude-code")
        self.write("docs/a.md", "legitimate content")
        proc = self.stop("claude-code")
        self.assertEqual(proc.returncode, 0)

    # -- the invariant ------------------------------------------------------

    def test_no_declaration_means_wroteonly_stays_out_of_the_way(self):
        proc = self.pre(os.path.join(self.root, "anything.py"), "claude-code")
        self.assertEqual(proc.returncode, 0)

    def test_garbage_stdin_never_wedges_the_session(self):
        for payload in ("", "not json at all", "[]", "null"):
            with self.subTest(payload=payload):
                proc = subprocess.run(
                    [sys.executable, HOOK], input=payload, capture_output=True,
                    text=True, env=self.env(), cwd=self.root)
                self.assertEqual(proc.returncode, 0)

    def test_malformed_declaration_does_not_block_mid_run(self):
        with open(os.path.join(self.root, ".wroteonly.json"), "w") as fh:
            fh.write("{ this is not json")
        proc = self.pre(os.path.join(self.root, "src/x.py"), "claude-code")
        self.assertEqual(proc.returncode, 0)

    def test_malformed_declaration_is_surfaced_at_stop(self):
        with open(os.path.join(self.root, ".wroteonly.json"), "w") as fh:
            fh.write("{ this is not json")
        proc = self.stop("claude-code")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("could not be parsed", proc.stdout)

    def test_disable_switch_is_honoured(self):
        self.declare()
        proc = self.pre(os.path.join(self.root, "src/x.py"), "claude-code")
        self.assertEqual(proc.returncode, 2)
        env = self.env()
        env["WROTEONLY_DISABLE"] = "1"
        proc = subprocess.run(
            [sys.executable, HOOK],
            input=json.dumps({"hook_event_name": "PreToolUse", "session_id": "s2",
                              "cwd": self.root, "tool_name": "Write",
                              "tool_input": {"file_path": self.root + "/src/x.py"}}),
            capture_output=True, text=True, env=env, cwd=self.root)
        self.assertEqual(proc.returncode, 0)

    def test_unknown_event_is_ignored(self):
        self.declare()
        proc = self.hook({"hook_event_name": "PreCompact", "session_id": "s1",
                          "cwd": self.root}, host="codex")
        self.assertEqual(proc.returncode, 0)


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------

class TestCLI(TempTree):

    def cli(self, *args):
        return subprocess.run([sys.executable, CLI, *args], capture_output=True,
                              text=True, env=self.env(), cwd=self.root)

    def test_declare_then_verify_catches_a_violation(self):
        self.write("src/app.py", "one")
        proc = self.cli("declare", "--run-id", "j1", "--modify", "src/app.py",
                        "--root", self.root)
        self.assertEqual(proc.returncode, 0)

        self.write("src/app.py", "two")        # declared
        self.write("src/other.py", "sneaky")   # not declared

        proc = self.cli("verify", "--run-id", "j1", "--root", self.root)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("src/other.py", proc.stdout)

    def test_clean_run_exits_zero(self):
        self.write("src/app.py", "one")
        self.cli("declare", "--run-id", "j2", "--modify", "src/app.py",
                 "--root", self.root)
        self.write("src/app.py", "two")
        proc = self.cli("verify", "--run-id", "j2", "--root", self.root)
        self.assertEqual(proc.returncode, 0)

    def test_empty_declaration_is_refused_without_the_flag(self):
        proc = self.cli("declare", "--run-id", "j3", "--root", self.root)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("names no paths", proc.stderr)

    def test_allow_empty_asserts_touch_nothing(self):
        self.write("a.py", "one")
        proc = self.cli("declare", "--run-id", "j4", "--allow-empty",
                        "--root", self.root)
        self.assertEqual(proc.returncode, 0)
        self.write("a.py", "changed")
        proc = self.cli("verify", "--run-id", "j4", "--root", self.root)
        self.assertEqual(proc.returncode, 0)   # escalate, not deny
        self.assertIn("escalate", proc.stdout)

    def test_verify_without_declare_is_an_input_error(self):
        proc = self.cli("verify", "--run-id", "never-declared", "--root", self.root)
        self.assertEqual(proc.returncode, 3)

    def test_json_output_is_acs_shaped(self):
        self.write("a.py", "one")
        self.cli("declare", "--run-id", "j5", "--modify", "a.py", "--root", self.root)
        proc = self.cli("verify", "--run-id", "j5", "--root", self.root, "--json")
        payload = json.loads(proc.stdout)
        self.assertIn(payload["decision"], ("allow", "warn", "deny", "escalate"))

    def test_strict_promotes_warn_to_a_failing_exit(self):
        self.write("a.py", "one")
        self.cli("declare", "--run-id", "j6", "--allow-empty", "--root", self.root)
        self.write("a.py", "changed")
        proc = self.cli("verify", "--run-id", "j6", "--root", self.root, "--strict")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()

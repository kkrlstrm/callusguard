"""The dependency wall, enforced.

`guard`, `wroteonly` and `core` run synchronously inside every tool call. "No
dependencies. No network. No model calls." is what makes that defensible, and it is
the claim the whole enforcement layer rests on.

A claim like that rots the first time someone adds a convenient import. So it is a
test, not a sentence in a README.

HOW
    Import the enforcement modules in a subprocess started with `-S`, which skips
    `site` and therefore makes site-packages unimportable. If anything under
    core/guard/wroteonly has picked up a third-party dependency, the import fails.

    Then assert the converse: telemetry, which is *allowed* dependencies, is not
    reachable from the enforcement layer by accident.
"""

import os
import subprocess
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Everything that runs on the hot path. Adding a module here is a promise.
ENFORCEMENT_MODULES = [
    "callusguard",
    "callusguard.core",
    "callusguard.core.hosts",
    "callusguard.core.audit",
    "callusguard.core.verdict",
    "callusguard.guard",
    "callusguard.guard.engine",
    "callusguard.guard.runner",
    "callusguard.guard.check_rules",
    "callusguard.guard.doctor",
    "callusguard.wroteonly",
    "callusguard.wroteonly.declare",
    "callusguard.wroteonly.observe",
    "callusguard.wroteonly.baseline",
    "callusguard.wroteonly.report",
    "callusguard.wroteonly.state",
    "callusguard.wroteonly.runner",
    "callusguard.wroteonly.cli",
]


def _import_without_site(modules):
    """Import `modules` in a subprocess that cannot see site-packages."""
    code = "import sys; sys.path.insert(0, %r)\n" % REPO
    code += "".join("import %s\n" % m for m in modules)
    code += "print('OK')"
    return subprocess.run([sys.executable, "-S", "-c", code],
                          capture_output=True, text=True, cwd=REPO)


class TestDependencyWall(unittest.TestCase):

    def test_enforcement_layer_imports_with_no_third_party_packages(self):
        proc = _import_without_site(ENFORCEMENT_MODULES)
        self.assertEqual(
            proc.returncode, 0,
            "The enforcement layer picked up a third-party dependency.\n"
            "It runs inside every tool call and must stay stdlib-only.\n"
            "Move whatever needs the dependency into callusguard.telemetry.\n\n"
            + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_entry_scripts_run_with_no_third_party_packages(self):
        """The hook binaries, not just the modules — that is what the host runs."""
        for entry in ("bin/guard-hook.py", "bin/guard-readonly.py",
                      "bin/wroteonly-hook.py"):
            with self.subTest(entry=entry):
                proc = subprocess.run(
                    [sys.executable, "-S", os.path.join(REPO, entry)],
                    input="", capture_output=True, text=True, cwd=REPO)
                # Empty stdin -> fall open, exit 0. A missing dependency would
                # surface as a non-zero exit with an ImportError instead.
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertNotIn("ModuleNotFoundError", proc.stderr)

    def test_telemetry_is_not_imported_by_the_enforcement_layer(self):
        """A stray `from ..telemetry import ...` would drag FastAPI onto the hot path."""
        code = ("import sys; sys.path.insert(0, %r)\n" % REPO +
                "".join("import %s\n" % m for m in ENFORCEMENT_MODULES) +
                "leaked = [m for m in sys.modules if m.startswith('callusguard.telemetry')]\n"
                "print('LEAKED:' + ','.join(sorted(leaked)))")
        proc = subprocess.run([sys.executable, "-S", "-c", code],
                              capture_output=True, text=True, cwd=REPO)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        leaked = proc.stdout.strip().removeprefix("LEAKED:")
        self.assertEqual(leaked, "",
                         "the enforcement layer imported telemetry: " + leaked)


if __name__ == "__main__":
    unittest.main()

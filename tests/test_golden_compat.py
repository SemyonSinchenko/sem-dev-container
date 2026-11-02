"""Golden compatibility tests for the bins/run-* launchers.

Every invocation that uses no multi-agent flag (MULTI_AGENT_ROLES_PLAN.md
§2) must keep producing the exact `--dry-run` command it produces today: same
mounts, env, network options and container command, byte for byte. The
expected output lives in tests/golden/<launcher>-<case>.txt, captured from
the launchers before the shared-module refactor.

Machine-dependent inputs are pinned so the files compare equal everywhere:
  - PATH starts with stub `podman`/`docker` executables, so `--engine auto`
    resolves to podman as on the host (the dev container has neither);
  - stdin is /dev/null, so TUI/web cases emit `-i`, never `-it`;
  - provider keys are cleared, so nothing is passed through;
  - temp paths become <HOME>, <PROJECT> and <PROJECT_ESCAPED> (the volume
    name form), and run-dsh's picked web port becomes <PORT>.

Golden files change only deliberately, in a commit that explains why.
Re-capture them with:

    UPDATE_GOLDEN=1 python3 -m unittest tests.test_golden_compat

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BINS = ROOT / "bins"
GOLDEN = Path(__file__).resolve().parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

# Union of every launcher's passthrough list: cleared so no -e KEY appears.
PASSTHROUGH_KEYS = (
    "ZAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "PRIME_API_KEY",
)

# run-dsh web mode publishes a free port from PORT_BASE..PORT_BASE+9
# (30780-30789). Only that range is normalised: SEARXNG_URL's 127.0.0.1:7331
# must stay literal.
DSH_WEB_PORT_RE = re.compile(r"127\.0\.0\.1:3078\d\b")

# Cases shared by every launcher: (case name, launcher args).
COMMON_CASES = [
    ("default", []),
    ("shell", ["--shell"]),
    ("exec", ["--exec", "true"]),
    ("engine-docker", ["--engine", "docker"]),
    ("no-selinux", ["--no-selinux"]),
    ("env", ["--env", "X=1"]),
]

CASES = {
    "run-claude": COMMON_CASES + [
        ("acp", ["--acp"]),
        ("acp-args", ["--acp", "--", "--debug"]),
        ("agent-args", ["--", "--continue"]),
    ],
    "run-dsh": COMMON_CASES + [
        ("acp", ["--acp"]),
        ("acp-args", ["--acp", "--", "--debug"]),
        ("web-args", ["--", "--port", "1234"]),
    ],
    "run-pi": COMMON_CASES,
    "run-pa": COMMON_CASES,
}

# Every long option run-claude/run-dsh accept today, with a sample value.
# New options must not make any of these full names unparseable (§2.6).
CURRENT_OPTIONS = [
    ["--shell"],
    ["--acp"],
    ["--exec", "true"],
    ["--engine", "podman"],
    ["--image", "some/image:tag"],
    ["--no-selinux"],
    ["--env", "X=1"],
    ["--dry-run"],
]


class GoldenCompatTest(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        # Resolve: launchers see Path.cwd(), which is the real path.
        root = Path(os.path.realpath(cls._tmp.name))
        cls.home = root / "home"
        cls.home.mkdir()
        # A project with .git so the read-only .git mount is exercised.
        cls.project = root / "project"
        cls.project.mkdir()
        (cls.project / ".git").mkdir()
        stubs = root / "stub-bin"
        stubs.mkdir()
        for engine in ("podman", "docker"):
            stub = stubs / engine
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        env = dict(os.environ, HOME=str(cls.home))
        env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        cls.env = env

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def dry_run(self, launcher: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BINS / launcher), "--dry-run", *args],
            cwd=self.project,
            env=self.env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
        )

    def normalise(self, text: str) -> str:
        project = str(self.project)
        text = text.replace(project.replace("/", "_"), "<PROJECT_ESCAPED>")
        text = text.replace(project, "<PROJECT>")
        text = text.replace(str(self.home), "<HOME>")
        return DSH_WEB_PORT_RE.sub("127.0.0.1:<PORT>", text)

    def check_golden(self, launcher: str) -> None:
        for case, args in CASES[launcher]:
            with self.subTest(launcher=launcher, case=case):
                proc = self.dry_run(launcher, *args)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                actual = self.normalise(proc.stdout)
                path = GOLDEN / f"{launcher}-{case}.txt"
                if UPDATE:
                    GOLDEN.mkdir(exist_ok=True)
                    path.write_text(actual)
                    continue
                self.assertTrue(path.exists(),
                                f"{path} missing; capture it with UPDATE_GOLDEN=1")
                expected = path.read_text()
                if actual != expected:
                    # One long line per file: compare token lists so the
                    # failure shows which argument changed.
                    self.assertEqual(actual.split(" "), expected.split(" "),
                                     f"{launcher} {case}: dry-run differs from {path}")
                    self.assertEqual(actual, expected)

    def test_run_claude_golden(self) -> None:
        self.check_golden("run-claude")

    def test_run_dsh_golden(self) -> None:
        self.check_golden("run-dsh")

    def test_run_pi_golden(self) -> None:
        self.check_golden("run-pi")

    def test_run_pa_golden(self) -> None:
        self.check_golden("run-pa")

    def test_current_option_names_still_parse(self) -> None:
        for launcher in ("run-claude", "run-dsh"):
            for option in CURRENT_OPTIONS:
                with self.subTest(launcher=launcher, option=option[0]):
                    proc = self.dry_run(launcher, *option)
                    self.assertEqual(proc.returncode, 0, proc.stderr)


if __name__ == "__main__":
    unittest.main()

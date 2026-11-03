"""Dry-run tests for --label, --name and --timeout (MULTI_AGENT_ROLES_PLAN.md
§8) in bins/run-claude and bins/run-dsh.

They are passed to the engine right after `run --rm -i/-it`, before the -v
list: automatic role labels first, then user labels in order (a user label
replaces an automatic one with the same key), then --name, then --timeout.
--timeout needs podman.

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BINS = Path(__file__).resolve().parents[1] / "bins"
LAUNCHERS = {"run-claude": ("claude", "claude-acp-entry"),
             "run-dsh": ("dsh", "dsh-acp-entry")}

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


class IdentityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(cls._tmp.name))
        home = root / "home"
        home.mkdir()
        cls.project = root / "project"
        cls.project.mkdir()
        (cls.project / ".git").mkdir()
        stubs = root / "stub-bin"
        stubs.mkdir()
        for engine in ("podman", "docker"):
            stub = stubs / engine
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        env = dict(os.environ, HOME=str(home))
        env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        cls.env = env

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def launch(self, launcher: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BINS / launcher), "--dry-run", *args],
            cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
            text=True, capture_output=True)

    def tokens(self, launcher: str, *args: str) -> list[str]:
        proc = self.launch(launcher, *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return shlex.split(proc.stdout)

    @staticmethod
    def engine_options(tokens: list[str]) -> list[str]:
        """Everything between `run --rm -i` and the first -v."""
        return tokens[4:tokens.index("-v")]

    def test_options_emitted_in_order(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--label", "sem.instance=chg-17",
                                     "--label", "team=a", "--name", "review-chg-17",
                                     "--timeout", "1800")
                self.assertEqual(self.engine_options(tokens), [
                    "--label", "sem.instance=chg-17",
                    "--label", "team=a",
                    "--name", "review-chg-17",
                    "--timeout", "1800",
                ])

    def test_user_labels_follow_role_labels(self) -> None:
        for launcher, (harness, _) in LAUNCHERS.items():
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--role", "review",
                                     "--label", "sem.instance=chg-17")
                self.assertEqual(self.engine_options(tokens), [
                    "--label", f"sem.harness={harness}",
                    "--label", f"sem.project={self.project}",
                    "--label", "sem.role=review",
                    "--label", "sem.instance=chg-17",
                ])

    def test_user_label_overrides_automatic_one(self) -> None:
        tokens = self.tokens("run-claude", "--role", "review",
                             "--label", "sem.project=chg-17-copy")
        options = self.engine_options(tokens)
        self.assertEqual(options, [
            "--label", "sem.harness=claude",
            "--label", "sem.role=review",
            "--label", "sem.project=chg-17-copy",
        ])

    def test_label_values_may_contain_equals_and_be_empty(self) -> None:
        options = self.engine_options(self.tokens(
            "run-claude", "--label", "k=a=b", "--label", "empty="))
        self.assertEqual(options, ["--label", "k=a=b", "--label", "empty="])

    def test_invalid_labels_rejected(self) -> None:
        for launcher in LAUNCHERS:
            for label in ("novalue", "=v", "Key=v", "-k=v", "k y=v", "k/y=v"):
                with self.subTest(launcher=launcher, label=label):
                    proc = self.launch(launcher, f"--label={label}")
                    self.assertEqual(proc.returncode, 2)
                    self.assertIn("invalid label", proc.stderr)

    def test_invalid_timeouts_rejected(self) -> None:
        for launcher in LAUNCHERS:
            for value in ("0", "-5", "1.5", "abc", "", "10s"):
                with self.subTest(launcher=launcher, value=value):
                    proc = self.launch(launcher, f"--timeout={value}")
                    self.assertEqual(proc.returncode, 2)
                    self.assertIn("invalid timeout", proc.stderr)

    def test_timeout_needs_podman(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                proc = self.launch(launcher, "--engine", "docker", "--timeout", "5")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--timeout needs podman (docker run has no --timeout)",
                              proc.stderr)

    def test_label_and_name_work_with_docker(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--engine", "docker",
                                     "--label", "k=v", "--name", "x")
                self.assertEqual(tokens[0], "docker")
                self.assertEqual(self.engine_options(tokens),
                                 ["--label", "k=v", "--name", "x"])

    def test_acp_invariants_with_identity_options(self) -> None:
        for launcher, (_, entry) in LAUNCHERS.items():
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--acp", "--role", "review",
                                     "--label", "sem.instance=x", "--name", "n",
                                     "--timeout", "60", "--", "--flag")
                self.assertEqual(tokens[:4], ["podman", "run", "--rm", "-i"])
                self.assertNotIn("-t", tokens)
                self.assertEqual(tokens[-2:], [entry, "--flag"])

    def test_dsh_web_mode_keeps_trusted_host_last(self) -> None:
        tokens = self.tokens("run-dsh", "--name", "web", "--timeout", "60")
        self.assertEqual(tokens[-2], "--trusted-host")
        self.assertEqual(self.engine_options(tokens),
                         ["--name", "web", "--timeout", "60"])


if __name__ == "__main__":
    unittest.main()

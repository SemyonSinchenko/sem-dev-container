"""Dry-run contract tests for bins/run-dsh.

ACP mode must reuse web mode's container shape (mounts, working directory,
environment, SearXNG loopback forwarding) while publishing no port, allocating
no pseudo-TTY, and handing stdio to dsh-acp-entry. These tests run only with
--dry-run, so they need neither podman/docker nor the built image.

Run with:  make test-run-dsh
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import pty
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUN_DSH = Path(__file__).resolve().parents[1] / "bins" / "run-dsh"

# run-dsh passes these through when they are set on the host. Clear them so
# the -e assertions below are deterministic on any machine.
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


class RunDshDryRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.home = root / "home"
        cls.home.mkdir()
        # A project with .git so the read-only .git mount and
        # DSH_GIT_READ_ONLY are exercised in both modes.
        cls.project = root / "project"
        cls.project.mkdir()
        (cls.project / ".git").mkdir()
        env = dict(os.environ, HOME=str(cls.home))
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        cls.env = env

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def dry_run(self, *args: str, stdin=None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RUN_DSH), "--dry-run", *args],
            cwd=self.project,
            env=self.env,
            stdin=stdin,
            text=True,
            capture_output=True,
        )

    def tokens(self, *args: str) -> list[str]:
        proc = self.dry_run(*args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return shlex.split(proc.stdout)

    @staticmethod
    def option_values(tokens: list[str], option: str) -> list[str]:
        """Values following every occurrence of OPTION in TOKENS."""
        return [tokens[i + 1] for i, token in enumerate(tokens) if token == option]

    def test_acp_command_shape(self) -> None:
        proc = self.dry_run("--engine", "podman", "--acp")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        tokens = shlex.split(proc.stdout)

        self.assertIn("dsh-acp-entry", tokens)
        self.assertEqual(tokens[-1], "dsh-acp-entry")
        self.assertIn("--rm", tokens)
        self.assertIn("-i", tokens)
        # No TTY, no published port, no web trust fence, no web entry, and no
        # published-port translation (--host-lo-to-ns-lo).
        for forbidden in ("-t", "-it", "-p", "--trusted-host", "dsh-web-entry"):
            self.assertNotIn(forbidden, tokens)
        self.assertNotIn("host-lo-to-ns-lo", proc.stdout)
        # SearXNG loopback forwarding stays, like web mode.
        self.assertIn("pasta:-T,7331", tokens)

    def test_acp_reuses_web_isolation(self) -> None:
        web = self.tokens("--engine", "podman")
        acp = self.tokens("--engine", "podman", "--acp")

        for option in ("-v", "-w", "-e"):
            self.assertEqual(
                self.option_values(web, option),
                self.option_values(acp, option),
                f"{option} specs differ between web and ACP modes",
            )

    def test_web_mode_still_publishes_the_port(self) -> None:
        tokens = self.tokens("--engine", "podman")

        self.assertIn("dsh-web-entry", tokens)
        self.assertIn("--rm", tokens)
        self.assertIn("-p", tokens)
        self.assertIn("--trusted-host", tokens)
        self.assertIn("pasta:--host-lo-to-ns-lo,-T,7331", tokens)
        port_spec = self.option_values(tokens, "-p")
        self.assertEqual(len(port_spec), 1)
        self.assertRegex(port_spec[0], r"^127\.0\.0\.1:\d+:3080$")

    def test_acp_docker_keeps_web_network_policy(self) -> None:
        web = self.tokens("--engine", "docker")
        acp = self.tokens("--engine", "docker", "--acp")

        self.assertNotIn("--network", acp)
        self.assertNotIn("-p", acp)
        self.assertEqual(
            self.option_values(web, "-v"), self.option_values(acp, "-v")
        )
        self.assertEqual(
            self.option_values(web, "-e"), self.option_values(acp, "-e")
        )

    def test_acp_forwards_trailing_arguments(self) -> None:
        tokens = self.tokens("--engine", "podman", "--acp", "--", "--future-flag")

        self.assertEqual(tokens[-2:], ["dsh-acp-entry", "--future-flag"])

    def test_acp_is_mutually_exclusive_with_shell_and_exec(self) -> None:
        for args in (
            ("--engine", "podman", "--acp", "--shell"),
            ("--engine", "podman", "--acp", "--exec", "dsh", "--help"),
        ):
            proc = self.dry_run(*args)
            self.assertNotEqual(proc.returncode, 0, args)
            self.assertIn("cannot be combined", proc.stderr)

    def test_acp_forces_i_even_with_tty_stdin(self) -> None:
        master_fd, slave_fd = pty.openpty()
        try:
            proc = self.dry_run(
                "--engine", "podman", "--acp", stdin=slave_fd
            )
        finally:
            os.close(master_fd)
            os.close(slave_fd)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        tokens = shlex.split(proc.stdout)
        self.assertIn("-i", tokens)
        self.assertNotIn("-it", tokens)
        self.assertNotIn("-t", tokens)


if __name__ == "__main__":
    unittest.main()

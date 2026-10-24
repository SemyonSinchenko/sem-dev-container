"""Dry-run contract tests for bins/run-claude.

ACP mode must reuse TUI mode's container shape (mounts, working directory,
environment, SearXNG loopback forwarding) while allocating no pseudo-TTY and
handing stdio to claude-acp-entry. The per-project state entries must be
over-mounted after the shared ~/.claude mount, and ANTHROPIC_API_KEY must not
leak in unless asked for. These tests run only with --dry-run, so they need
neither podman/docker nor the built image.

Run with:  make test-bins
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

RUN_CLAUDE = Path(__file__).resolve().parents[1] / "bins" / "run-claude"

# run-claude passes these through when they are set on the host. Clear them
# so the -e assertions below are deterministic on any machine.
PASSTHROUGH_KEYS = (
    "ZAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "PRIME_API_KEY",
)


class RunClaudeDryRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.home = root / "home"
        cls.home.mkdir()
        # A project with .git so the read-only .git mount and
        # CLAUDE_GIT_READ_ONLY are exercised in both modes.
        cls.project = root / "project"
        cls.project.mkdir()
        (cls.project / ".git").mkdir()
        env = dict(os.environ, HOME=str(cls.home))
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        # Set on the "host" to prove it is not passed through by default.
        env["ANTHROPIC_API_KEY"] = "sk-test"
        cls.env = env

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def dry_run(self, *args: str, stdin=None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RUN_CLAUDE), "--dry-run", *args],
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
        tokens = self.tokens("--engine", "podman", "--acp")

        self.assertEqual(tokens[-2:], ["dev-claude", "claude-acp-entry"])
        self.assertIn("--rm", tokens)
        self.assertIn("-i", tokens)
        for forbidden in ("-t", "-it", "-p"):
            self.assertNotIn(forbidden, tokens)
        self.assertIn("pasta:-T,7331", tokens)

    def test_tui_mode_runs_claude(self) -> None:
        tokens = self.tokens("--engine", "podman", "--", "--continue")

        self.assertEqual(tokens[-3:], ["dev-claude", "claude", "--continue"])

    def test_acp_reuses_tui_isolation(self) -> None:
        tui = self.tokens("--engine", "podman")
        acp = self.tokens("--engine", "podman", "--acp")

        for option in ("-v", "-w", "-e"):
            self.assertEqual(
                self.option_values(tui, option),
                self.option_values(acp, option),
                f"{option} specs differ between TUI and ACP modes",
            )

    def test_project_state_overmounts_follow_shared_home(self) -> None:
        mounts = self.option_values(self.tokens("--engine", "podman", "--acp"), "-v")
        targets = [spec.split(":")[1] for spec in mounts]
        state = self.project / ".claude-container"

        shared = targets.index("/root/.claude")
        for name in ("projects", "file-history", "history.jsonl"):
            self.assertIn(f"{state / name}:/root/.claude/{name}:z", mounts)
            self.assertGreater(targets.index(f"/root/.claude/{name}"), shared)
        self.assertTrue((state / "projects").is_dir())
        self.assertTrue((state / "history.jsonl").is_file())
        self.assertIn(f"{self.project / '.git'}:{self.project / '.git'}:ro,z", mounts)

    def test_env_defaults(self) -> None:
        envs = self.option_values(self.tokens("--engine", "podman", "--acp"), "-e")

        self.assertIn("CLAUDE_PERMISSION_MODE=bypassPermissions", envs)
        self.assertIn(f"CLAUDE_PROJECT_HOME={self.project / '.claude-container'}", envs)
        self.assertIn("CLAUDE_GIT_READ_ONLY=1", envs)
        self.assertNotIn("ANTHROPIC_API_KEY", envs)

    def test_env_overrides_win(self) -> None:
        envs = self.option_values(
            self.tokens("--engine", "podman", "--acp",
                        "--env", "CLAUDE_PERMISSION_MODE=default",
                        "--env", "ANTHROPIC_API_KEY"),
            "-e",
        )

        self.assertIn("CLAUDE_PERMISSION_MODE=default", envs)
        self.assertNotIn("CLAUDE_PERMISSION_MODE=bypassPermissions", envs)
        self.assertIn("ANTHROPIC_API_KEY", envs)

    def test_docker_drops_pasta_network(self) -> None:
        tui = self.tokens("--engine", "docker")
        acp = self.tokens("--engine", "docker", "--acp")

        self.assertNotIn("--network", acp)
        self.assertNotIn("SEARXNG_URL=http://127.0.0.1:7331", self.option_values(acp, "-e"))
        self.assertEqual(self.option_values(tui, "-v"), self.option_values(acp, "-v"))
        self.assertEqual(self.option_values(tui, "-e"), self.option_values(acp, "-e"))

    def test_acp_forwards_trailing_arguments(self) -> None:
        tokens = self.tokens("--engine", "podman", "--acp", "--", "--future-flag")

        self.assertEqual(tokens[-2:], ["claude-acp-entry", "--future-flag"])

    def test_acp_is_mutually_exclusive_with_shell_and_exec(self) -> None:
        for args in (
            ("--engine", "podman", "--acp", "--shell"),
            ("--engine", "podman", "--acp", "--exec", "claude", "--help"),
        ):
            proc = self.dry_run(*args)
            self.assertNotEqual(proc.returncode, 0, args)
            self.assertIn("cannot be combined", proc.stderr)

    def test_acp_forces_i_even_with_tty_stdin(self) -> None:
        master_fd, slave_fd = pty.openpty()
        try:
            proc = self.dry_run("--engine", "podman", "--acp", stdin=slave_fd)
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

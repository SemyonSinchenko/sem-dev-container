"""Tests for bins/run-check (MULTI_AGENT_ROLES_PLAN.md §9).

A check container gets the project (overlay by default), the read-only .git,
the caches, a /target volume and an optional outbox, but no harness state, no
provider keys and no SearXNG. Its exit status is the container's. Dry-run
tests need neither podman nor the image; the exit-status tests use a stub
engine.

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
RUN_CHECK = BINS / "run-check"

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


class RunCheckTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self._tmp.name))
        self.home = root / "home"
        self.home.mkdir()
        self.project = root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.stubs = root / "stub-bin"
        self.stubs.mkdir()
        for engine in ("podman", "docker"):
            self.write_stub(engine, "exit 0")
        env = dict(os.environ, HOME=str(self.home))
        env["PATH"] = f"{self.stubs}{os.pathsep}{env.get('PATH', '')}"
        # Every provider key is set on the "host": none may reach the check.
        for key in PASSTHROUGH_KEYS:
            env[key] = "sk-host-secret"
        self.env = env

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_stub(self, engine: str, body: str) -> None:
        stub = self.stubs / engine
        stub.write_text(f"#!/bin/sh\n{body}\n")
        stub.chmod(0o755)

    def run_check(self, *args: str, dry_run: bool = True,
                  launcher: Path = RUN_CHECK) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, str(launcher)] + (["--dry-run"] if dry_run else [])
        return subprocess.run(argv + list(args), cwd=self.project, env=self.env,
                              stdin=subprocess.DEVNULL, text=True, capture_output=True)

    def tokens(self, *args: str, launcher: Path = RUN_CHECK) -> list[str]:
        proc = self.run_check(*args, launcher=launcher)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return shlex.split(proc.stdout)

    @staticmethod
    def values(tokens: list[str], option: str) -> list[str]:
        return [tokens[i + 1] for i, t in enumerate(tokens) if t == option]

    def escaped(self) -> str:
        return str(self.project).replace("/", "_")

    # --- shape ------------------------------------------------------------------

    def test_default_command_shape(self) -> None:
        tokens = self.tokens("--", "make", "test")
        p, git = self.project, self.project / ".git"
        self.assertEqual(tokens[:4], ["podman", "run", "--rm", "-i"])
        self.assertEqual(tokens[-3:], ["dev-shared", "make", "test"])
        mounts = self.values(tokens, "-v")
        self.assertEqual(mounts[0], f"{p}:{p}:O")
        self.assertEqual(mounts[-2:], [f"{git}:{git}:ro,z",
                                       f"check-target{self.escaped()}:/target"])
        self.assertEqual(len(mounts), 1 + 7 + 2)  # project, caches, .git, volume
        self.assertEqual(self.values(tokens, "-w"), [str(p)])
        self.assertEqual(self.values(tokens, "-e"),
                         ["CARGO_TARGET_DIR=/target", "AGENT_MOUNT_MODE=overlay"])
        self.assertNotIn("--network", tokens)
        self.assertNotIn("--label", tokens)

    def test_no_harness_state_keys_or_searxng(self) -> None:
        tokens = self.tokens("--role", "impl", "--", "env")
        joined = " ".join(tokens)
        for forbidden in (".claude", ".dsh", ".agents", "SEARXNG", "pasta",
                          "sk-host-secret", *PASSTHROUGH_KEYS):
            self.assertNotIn(forbidden, joined)

    def test_env_opt_in_still_possible(self) -> None:
        envs = self.values(self.tokens("--env", "OPENAI_API_KEY", "--env", "X=1",
                                       "--", "env"), "-e")
        self.assertIn("OPENAI_API_KEY", envs)
        self.assertIn("X=1", envs)

    def test_exec_and_dashdash_are_equivalent(self) -> None:
        a = self.tokens("--", "cargo", "test", "--release")
        b = self.tokens("--exec", "cargo", "test", "--release")
        self.assertEqual(a, b)
        self.assertEqual(a[-4:], ["dev-shared", "cargo", "test", "--release"])

    def test_shell(self) -> None:
        self.assertEqual(self.tokens("--shell")[-2:], ["dev-shared", "bash"])

    def test_command_errors(self) -> None:
        for args, message in (
            ([], "no check command"),
            (["--shell", "--", "make"], "--shell cannot be combined"),
            (["--exec"], "--exec needs a command"),
            (["--allow-duplicate-role", "--", "true"], "unrecognized arguments"),
        ):
            with self.subTest(args=args):
                proc = self.run_check(*args)
                self.assertEqual(proc.returncode, 2)
                self.assertIn(message, proc.stderr)

    # --- mounts and engines -----------------------------------------------------

    def test_mount_modes(self) -> None:
        p = self.project
        for mode, spec in (("overlay", f"{p}:{p}:O"), ("ro", f"{p}:{p}:ro,z"),
                           ("rw", f"{p}:{p}:z")):
            with self.subTest(mode=mode):
                tokens = self.tokens("--mount", mode, "--", "true")
                self.assertEqual(self.values(tokens, "-v")[0], spec)
                self.assertIn(f"AGENT_MOUNT_MODE={mode}", self.values(tokens, "-e"))

    def test_docker_needs_explicit_mount(self) -> None:
        proc = self.run_check("--engine", "docker", "--", "true")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("the default --mount overlay needs podman; with docker pass "
                      "--mount ro or --mount rw", proc.stderr)
        proc = self.run_check("--engine", "docker", "--mount", "overlay", "--", "true")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--mount overlay needs podman", proc.stderr)
        tokens = self.tokens("--engine", "docker", "--mount", "ro", "--", "true")
        self.assertEqual(tokens[0], "docker")

    def test_offline(self) -> None:
        tokens = self.tokens("--offline", "--", "true")
        self.assertEqual(self.values(tokens, "--network"), ["none"])
        self.assertLess(tokens.index("--network"), tokens.index("dev-shared"))

    def test_outbox(self) -> None:
        out = self.project / ".loop" / "t1"
        tokens = self.tokens("--outbox", ".loop/t1", "--", "true")
        mounts = self.values(tokens, "-v")
        self.assertEqual(mounts[-2], f"{out}:{out}:z")
        self.assertIn(f"AGENT_OUTBOX={out}", self.values(tokens, "-e"))
        self.assertTrue(out.is_dir())
        proc = self.run_check("--outbox", "..", "--", "true")
        self.assertEqual(proc.returncode, 2)

    # --- roles, volumes, identity -----------------------------------------------

    def test_role_volume_labels_and_no_lock(self) -> None:
        tokens = self.tokens("--role", "impl", "--", "true")
        self.assertIn(f"check-target.impl{self.escaped()}:/target", self.values(tokens, "-v"))
        self.assertIn("AGENT_ROLE=impl", self.values(tokens, "-e"))
        self.assertEqual(tokens[4:10], [
            "--label", "sem.harness=check",
            "--label", f"sem.project={self.project}",
            "--label", "sem.role=impl",
        ])
        # No role dir and no lock: parallel checks for one role are fine.
        self.assertFalse((self.project / ".claude-container").exists())
        self.assertFalse((self.project / ".dsh").exists())
        self.assertEqual(self.tokens("--shared-target", "--role", "impl", "--", "true")
                         [-2:], ["dev-shared", "true"])

    def test_target_from_matches_agent_volumes(self) -> None:
        for harness in ("claude", "dsh"):
            for role_args in ([], ["--role", "impl"], ["--role", "impl", "--shared-target"]):
                with self.subTest(harness=harness, role=role_args):
                    agent = self.tokens(*role_args, launcher=BINS / f"run-{harness}")
                    check = self.tokens(*role_args, "--target-from", harness, "--", "true")
                    agent_vol = [m for m in self.values(agent, "-v") if m.endswith(":/target")]
                    check_vol = [m for m in self.values(check, "-v") if m.endswith(":/target")]
                    self.assertEqual(check_vol, agent_vol)
        proc = self.run_check("--target-from", "pi", "--", "true")
        self.assertEqual(proc.returncode, 2)

    def test_identity_options(self) -> None:
        tokens = self.tokens("--label", "sem.instance=chg-17", "--name", "chk",
                             "--timeout", "600", "--", "true")
        self.assertEqual(tokens[4:tokens.index("-v")], [
            "--label", "sem.instance=chg-17", "--name", "chk", "--timeout", "600"])
        proc = self.run_check("--engine", "docker", "--mount", "ro", "--timeout", "5",
                              "--", "true")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--timeout needs podman", proc.stderr)

    # --- running ----------------------------------------------------------------

    def test_exit_status_is_the_containers(self) -> None:
        for code in (0, 1, 7, 255):
            with self.subTest(code=code):
                self.write_stub("podman", f"exit {code}")
                proc = self.run_check("--engine", "podman", "--", "true", dry_run=False)
                self.assertEqual(proc.returncode, code, proc.stderr)
                self.assertEqual(proc.stderr, "")

    def test_output_passes_through(self) -> None:
        self.write_stub("podman", 'echo "out:$#"; echo err >&2; exit 3')
        proc = self.run_check("--engine", "podman", "--", "make", "test", dry_run=False)
        self.assertEqual(proc.returncode, 3)
        self.assertRegex(proc.stdout, r"^out:\d+\n$")
        self.assertEqual(proc.stderr, "err\n")


if __name__ == "__main__":
    unittest.main()

"""Dry-run tests for --mount rw|ro|overlay (MULTI_AGENT_ROLES_PLAN.md §6.2)
in bins/run-claude and bins/run-dsh.

The project mount spec follows the mode, the read-only .git remount stays in
every mode, AGENT_MOUNT_MODE is exported only for an explicit --mount, overlay
refuses docker instead of falling back, and run-dsh keeps its project data
writable with an extra bind in ro/overlay. Without --mount nothing changes
(tests/test_golden_compat.py).

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
LAUNCHERS = {"run-claude": "claude-acp-entry", "run-codex": "codex-acp-entry",
             "run-dsh": "dsh-acp-entry"}

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


class MountModeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(cls._tmp.name))
        cls.home = root / "home"
        cls.home.mkdir()
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
    def values(tokens: list[str], option: str) -> list[str]:
        return [tokens[i + 1] for i, t in enumerate(tokens) if t == option]

    def test_project_mount_spec_per_mode(self) -> None:
        p = self.project
        expected = {
            ("rw", True): f"{p}:{p}:z",
            ("rw", False): f"{p}:{p}",
            ("ro", True): f"{p}:{p}:ro,z",
            ("ro", False): f"{p}:{p}:ro",
            ("overlay", True): f"{p}:{p}:O",
            ("overlay", False): f"{p}:{p}:O",
        }
        for launcher in LAUNCHERS:
            for (mode, selinux), spec in expected.items():
                with self.subTest(launcher=launcher, mode=mode, selinux=selinux):
                    args = ["--mount", mode] + ([] if selinux else ["--no-selinux"])
                    mounts = self.values(self.tokens(launcher, *args), "-v")
                    self.assertEqual(mounts[0], spec)

    def test_git_remount_in_every_mode(self) -> None:
        git = self.project / ".git"
        for launcher in LAUNCHERS:
            for mode in ("rw", "ro", "overlay"):
                with self.subTest(launcher=launcher, mode=mode):
                    mounts = self.values(self.tokens(launcher, "--mount", mode), "-v")
                    self.assertIn(f"{git}:{git}:ro,z", mounts)
                    self.assertGreater(mounts.index(f"{git}:{git}:ro,z"), 0)

    def test_agent_mount_mode_only_when_explicit(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                default = self.tokens(launcher)
                self.assertFalse(any(e.startswith("AGENT_MOUNT_MODE=")
                                     for e in self.values(default, "-e")))
                explicit_rw = self.tokens(launcher, "--mount", "rw")
                self.assertIn("AGENT_MOUNT_MODE=rw", self.values(explicit_rw, "-e"))
                # Explicit rw changes nothing but the env var.
                self.assertEqual(self.values(explicit_rw, "-v"), self.values(default, "-v"))
                for mode in ("ro", "overlay"):
                    self.assertIn(f"AGENT_MOUNT_MODE={mode}",
                                  self.values(self.tokens(launcher, "--mount", mode), "-e"))

    def test_overlay_refuses_docker(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                proc = self.launch(launcher, "--mount", "overlay", "--engine", "docker")
                self.assertEqual(proc.returncode, 2)
                self.assertEqual(proc.stdout, "")
                self.assertIn("--mount overlay needs podman", proc.stderr)
                # ro works on docker.
                self.assertEqual(
                    self.launch(launcher, "--mount", "ro", "--engine", "docker").returncode, 0)

    def test_overlay_refuses_auto_resolved_docker(self) -> None:
        stubs = self._tmp.name + "/docker-only"
        os.makedirs(stubs, exist_ok=True)
        stub = Path(stubs) / "docker"
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
        # PATH with docker but no podman (python itself is called by path).
        env = dict(self.env, PATH=stubs)
        proc = subprocess.run(
            [sys.executable, str(BINS / "run-claude"), "--dry-run", "--mount", "overlay"],
            cwd=self.project, env=env, stdin=subprocess.DEVNULL,
            text=True, capture_output=True)
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("--mount overlay needs podman (docker has no :O", proc.stderr)

    def test_invalid_mode_rejected(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                proc = self.launch(launcher, "--mount", "tmpfs")
                self.assertEqual(proc.returncode, 2)
                self.assertIn("invalid choice", proc.stderr)

    def test_dsh_state_bind_in_non_rw_modes(self) -> None:
        legacy = self.project / ".dsh"
        role = legacy / "roles" / "review"
        git = self.project / ".git"
        cases = [
            ([], None),
            (["--role", "review"], None),
            (["--mount", "rw"], None),
            (["--mount", "ro"], f"{legacy}:{legacy}:z"),
            (["--mount", "overlay"], f"{legacy}:{legacy}:z"),
            (["--mount", "overlay", "--no-selinux"], f"{legacy}:{legacy}"),
            (["--mount", "overlay", "--role", "review"], f"{role}:{role}:z"),
            (["--mount", "ro", "--role", "review"], f"{role}:{role}:z"),
        ]
        for args, bind in cases:
            with self.subTest(args=args):
                mounts = self.values(self.tokens("run-dsh", *args), "-v")
                binds = [m for m in mounts if m.startswith(f"{legacy}:") or
                         m.startswith(f"{role}:")]
                if bind is None:
                    self.assertEqual(binds, [])
                    continue
                self.assertEqual(binds, [bind])
                index = mounts.index(bind)
                # After the project mount and the shared mounts, before .git.
                self.assertGreater(index, 0)
                git_specs = [m for m in mounts if m.startswith(f"{git}:")]
                self.assertLess(index, mounts.index(git_specs[0]))

    def test_claude_needs_no_state_bind(self) -> None:
        state = self.project / ".claude-container"
        for mode in ("ro", "overlay"):
            with self.subTest(mode=mode):
                mounts = self.values(self.tokens("run-claude", "--mount", mode), "-v")
                self.assertFalse(any(m.startswith(f"{state}:") for m in mounts))
                # The over-mounts target /root/.claude, outside the project.
                self.assertIn(f"{state}/projects:/root/.claude/projects:z", mounts)

    def test_mount_works_in_every_mode(self) -> None:
        for launcher, entry in LAUNCHERS.items():
            for mode_args in (["--acp"], ["--shell"], ["--exec", "true"], []):
                with self.subTest(launcher=launcher, mode=mode_args):
                    tokens = self.tokens(launcher, "--mount", "overlay", *mode_args)
                    self.assertEqual(self.values(tokens, "-v")[0],
                                     f"{self.project}:{self.project}:O")
                    if mode_args == ["--acp"]:
                        self.assertEqual(tokens[:4], ["podman", "run", "--rm", "-i"])
                        self.assertEqual(tokens[-1], entry)


if __name__ == "__main__":
    unittest.main()

"""Dry-run tests for --role, --shared-target and --allow-duplicate-role
(MULTI_AGENT_ROLES_PLAN.md §6.1) in bins/run-claude and bins/run-dsh.

A role gives a container its own session state, its own /target volume,
AGENT_ROLE and sem.* labels, and only one container per role and project may
run at a time. Without --role nothing changes (tests/test_golden_compat.py).

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import fcntl
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

BINS = Path(__file__).resolve().parents[1] / "bins"

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

# Per launcher: harness label, state dir, volume prefix, state env var, ACP entry.
LAUNCHERS = {
    "run-claude": {
        "harness": "claude",
        "state": ".claude-container",
        "volume": "claude-target",
        "state_env": "CLAUDE_PROJECT_HOME",
        "acp_entry": "claude-acp-entry",
    },
    "run-codex": {
        "harness": "codex",
        "state": ".codex-container",
        "volume": "codex-target",
        "state_env": "CODEX_PROJECT_HOME",
        "acp_entry": "codex-acp-entry",
    },
    "run-dsh": {
        "harness": "dsh",
        "state": ".dsh",
        "volume": "dsh-target",
        "state_env": "DSH_PROJECT_HOME",
        "acp_entry": "dsh-acp-entry",
    },
}


class RoleTest(unittest.TestCase):
    def setUp(self) -> None:
        # Fresh project per test: role dirs and lock files must not leak.
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(os.path.realpath(self._tmp.name))
        self.home = root / "home"
        self.home.mkdir()
        self.project = root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        stubs = root / "stub-bin"
        stubs.mkdir()
        for engine in ("podman", "docker"):
            stub = stubs / engine
            stub.write_text("#!/bin/sh\nexit 0\n")
            stub.chmod(0o755)
        env = dict(os.environ, HOME=str(self.home))
        env["PATH"] = f"{stubs}{os.pathsep}{env.get('PATH', '')}"
        for key in PASSTHROUGH_KEYS:
            env.pop(key, None)
        self.env = env

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def launch(self, launcher: str, *args: str, dry_run: bool = True
               ) -> subprocess.CompletedProcess[str]:
        argv = [sys.executable, str(BINS / launcher)]
        if dry_run:
            argv.append("--dry-run")
        return subprocess.run(argv + list(args), cwd=self.project, env=self.env,
                              stdin=subprocess.DEVNULL, text=True,
                              capture_output=True)

    def tokens(self, launcher: str, *args: str) -> list[str]:
        proc = self.launch(launcher, *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return shlex.split(proc.stdout)

    @staticmethod
    def values(tokens: list[str], option: str) -> list[str]:
        return [tokens[i + 1] for i, t in enumerate(tokens) if t == option]

    def escaped(self) -> str:
        return str(self.project).replace("/", "_")

    def role_dir(self, launcher: str, role: str) -> Path:
        return self.project / LAUNCHERS[launcher]["state"] / "roles" / role

    # --- layout ---------------------------------------------------------------

    def test_role_state_volume_env_and_labels(self) -> None:
        for launcher, spec in LAUNCHERS.items():
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--role", "impl")
                role_dir = self.role_dir(launcher, "impl")
                envs = self.values(tokens, "-e")
                self.assertIn(f"{spec['state_env']}={role_dir}", envs)
                self.assertIn("AGENT_ROLE=impl", envs)
                self.assertIn(f"{spec['volume']}.impl{self.escaped()}:/target",
                              self.values(tokens, "-v"))
                # Labels come right after `run --rm -i`, before any -v.
                self.assertEqual(tokens[4:10], [
                    "--label", f"sem.harness={spec['harness']}",
                    "--label", f"sem.project={self.project}",
                    "--label", "sem.role=impl",
                ])
                self.assertTrue(role_dir.is_dir())

    def test_claude_role_overmounts_point_at_role_dir(self) -> None:
        tokens = self.tokens("run-claude", "--role", "impl")
        role_dir = self.role_dir("run-claude", "impl")
        mounts = self.values(tokens, "-v")
        self.assertIn(f"{role_dir}/projects:/root/.claude/projects:z", mounts)
        self.assertIn(f"{role_dir}/history.jsonl:/root/.claude/history.jsonl:z", mounts)
        legacy = self.project / ".claude-container"
        self.assertFalse(any(m.startswith(f"{legacy}/projects:") for m in mounts))
        self.assertTrue((role_dir / "history.jsonl").is_file())
        self.assertTrue((role_dir / "projects").is_dir())

    def test_codex_role_overmounts_point_at_role_dir(self) -> None:
        tokens = self.tokens("run-codex", "--role", "impl")
        role_dir = self.role_dir("run-codex", "impl")
        mounts = self.values(tokens, "-v")
        for name in ("sessions", "sqlite", "history.jsonl"):
            self.assertIn(f"{role_dir}/{name}:/root/.codex/{name}:z", mounts)
        legacy = self.project / ".codex-container"
        self.assertFalse(any(m.startswith(f"{legacy}/sessions:") for m in mounts))
        self.assertTrue((role_dir / "history.jsonl").is_file())
        self.assertTrue((role_dir / "sqlite").is_dir())

    def test_dsh_role_sessions_and_workspace(self) -> None:
        envs = self.values(self.tokens("run-dsh", "--role", "impl"), "-e")
        role_dir = self.role_dir("run-dsh", "impl")
        self.assertIn(f"DSH_PROJECT_SESSIONS={role_dir}/sessions", envs)
        self.assertIn(f"DSH_PROJECT_STORAGES={role_dir}/storages", envs)
        # The workspace stays the project, not the role dir.
        self.assertIn(f"DSH_PROJECT_DIR={self.project}", envs)

    def test_shared_target_keeps_legacy_volume(self) -> None:
        for launcher, spec in LAUNCHERS.items():
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--role", "impl", "--shared-target")
                self.assertIn(f"{spec['volume']}{self.escaped()}:/target",
                              self.values(tokens, "-v"))
                self.assertIn("AGENT_ROLE=impl", self.values(tokens, "-e"))

    def test_roles_get_distinct_volumes(self) -> None:
        sys.path.insert(0, str(BINS))
        import devc_common as dc
        # The collision a "__<role>" suffix would have.
        self.assertNotEqual(dc.target_volume("p", Path("/a__b")),
                            dc.target_volume("p", Path("/a"), "b"))
        self.assertNotEqual(dc.target_volume("p", Path("/a"), "b"),
                            dc.target_volume("p", Path("/a"), "b-c"))
        self.assertEqual(dc.target_volume("p", Path("/a"), "b", shared=True), "p_a")

    def test_acp_invariants_with_role(self) -> None:
        for launcher, spec in LAUNCHERS.items():
            with self.subTest(launcher=launcher):
                tokens = self.tokens(launcher, "--role", "impl", "--acp", "--", "--x")
                self.assertEqual(tokens[:4], ["podman", "run", "--rm", "-i"])
                self.assertNotIn("-it", tokens)
                self.assertNotIn("-t", tokens)
                self.assertEqual(tokens[-2:], [spec["acp_entry"], "--x"])

    # --- validation -----------------------------------------------------------

    def test_invalid_role_names_rejected(self) -> None:
        bad = ["", "Impl", "1impl", "-impl", "im_pl", "im.pl", "im pl",
               "a" * 33, "../x"]
        for launcher in LAUNCHERS:
            for role in bad:
                with self.subTest(launcher=launcher, role=role):
                    # --role=VALUE so "-impl" reaches the validator instead
                    # of being taken for an option.
                    proc = self.launch(launcher, f"--role={role}")
                    self.assertEqual(proc.returncode, 2)
                    self.assertIn("invalid role", proc.stderr)
        for role in ("a", "impl", "review-2", "a" * 32):
            with self.subTest(valid=role):
                self.assertEqual(self.launch("run-claude", "--role", role).returncode, 0)

    def test_role_modifiers_require_role(self) -> None:
        for launcher in LAUNCHERS:
            for flag in ("--shared-target", "--allow-duplicate-role"):
                with self.subTest(launcher=launcher, flag=flag):
                    proc = self.launch(launcher, flag)
                    self.assertEqual(proc.returncode, 2)
                    self.assertIn(f"{flag} requires --role", proc.stderr)

    # --- one container per role -----------------------------------------------

    def hold_lock(self, launcher: str, role: str) -> int:
        role_dir = self.role_dir(launcher, role)
        role_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(role_dir / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def test_role_lock(self) -> None:
        for launcher in LAUNCHERS:
            with self.subTest(launcher=launcher):
                fd = self.hold_lock(launcher, "impl")
                try:
                    busy = self.launch(launcher, "--role", "impl")
                    self.assertEqual(busy.returncode, 2)
                    self.assertEqual(busy.stdout, "")
                    self.assertIn(
                        f"{launcher}: role impl is already running for this project "
                        "(use --allow-duplicate-role to override)", busy.stderr)
                    dup = self.launch(launcher, "--role", "impl", "--allow-duplicate-role")
                    self.assertEqual(dup.returncode, 0, dup.stderr)
                    other = self.launch(launcher, "--role", "review")
                    self.assertEqual(other.returncode, 0, other.stderr)
                    legacy = self.launch(launcher)
                    self.assertEqual(legacy.returncode, 0, legacy.stderr)
                finally:
                    os.close(fd)
                free = self.launch(launcher, "--role", "impl")
                self.assertEqual(free.returncode, 0, free.stderr)

    def test_lock_is_held_while_container_runs(self) -> None:
        # A stub engine that blocks until the test probes the lock from outside.
        stub = Path(self.env["PATH"].split(os.pathsep)[0]) / "podman"
        marker = self.project / "started"
        release = self.project / "release"
        stub.write_text("#!/bin/sh\n"
                        f"touch '{marker}'\n"
                        f"while [ ! -e '{release}' ]; do sleep 0.05; done\n")
        lock = self.role_dir("run-claude", "impl") / ".lock"
        for mode in (["--acp"], ["--shell"]):
            with self.subTest(mode=mode[0]):
                marker.unlink(missing_ok=True)
                release.unlink(missing_ok=True)
                proc = subprocess.Popen(
                    [sys.executable, str(BINS / "run-claude"), "--engine", "podman",
                     "--role", "impl", *mode],
                    cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                try:
                    for _ in range(200):
                        if marker.exists() or proc.poll() is not None:
                            break
                        time.sleep(0.05)
                    if proc.returncode is not None:
                        self.fail(f"launcher exited early: {proc.stderr.read()}")
                    self.assertTrue(marker.exists(), "stub engine never started")
                    fd = os.open(lock, os.O_RDWR)
                    try:
                        with self.assertRaises(BlockingIOError):
                            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    finally:
                        os.close(fd)
                finally:
                    release.touch()
                    proc.wait(timeout=10)
                    proc.stderr.close()
                fd = os.open(lock, os.O_RDWR)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again
                finally:
                    os.close(fd)

    # --- run-dsh web banner -----------------------------------------------------

    def test_dsh_banner_shows_role_state_dir(self) -> None:
        proc = self.launch("run-dsh", "--engine", "podman", "--role", "impl",
                           dry_run=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"dsh state:      {self.role_dir('run-dsh', 'impl')}\n", proc.stdout)
        legacy = self.launch("run-dsh", "--engine", "podman", dry_run=False)
        self.assertIn(f"dsh state:      {self.project / '.dsh'}\n", legacy.stdout)


if __name__ == "__main__":
    unittest.main()

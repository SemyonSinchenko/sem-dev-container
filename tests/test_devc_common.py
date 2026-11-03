"""Tests for bins/devc_common.py and how the launchers find it.

The launchers are installed as symlinks into ~/.local/bin (see README), so
they must locate devc_common through the symlink-resolved script path; a
copied script must fail with a one-line hint instead of a traceback.

Run with:  make test-bins
       or:  python3 -m unittest discover -s tests -p 'test_*.py'
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

BINS = Path(__file__).resolve().parents[1] / "bins"
LAUNCHERS = ("run-claude", "run-dsh", "run-pi", "run-pa", "run-check")

sys.path.insert(0, str(BINS))
import devc_common as dc  # noqa: E402


class DevcCommonTest(unittest.TestCase):
    def test_resolve_engine_explicit(self) -> None:
        self.assertEqual(dc.resolve_engine("docker"), "docker")

    def test_resolve_engine_auto_prefers_podman(self) -> None:
        with mock.patch.object(dc.shutil, "which", lambda name: f"/usr/bin/{name}"):
            self.assertEqual(dc.resolve_engine("auto"), "podman")
        with mock.patch.object(dc.shutil, "which",
                               lambda name: "/usr/bin/docker" if name == "docker" else None):
            self.assertEqual(dc.resolve_engine("auto"), "docker")

    def test_resolve_engine_none_found_exits(self) -> None:
        with mock.patch.object(dc.shutil, "which", lambda name: None):
            with self.assertRaises(SystemExit):
                dc.resolve_engine("auto")

    def test_cache_mounts_order_and_selinux(self) -> None:
        home = Path("/h")
        self.assertEqual(dc.cache_mounts(home, ":z"), [
            "/h/.cargo/registry:/usr/local/cargo/registry:z",
            "/h/.cache/coursier:/cache/coursier:z",
            "/h/.ivy2:/cache/ivy2:z",
            "/h/.cache/uv:/cache/uv:z",
            "/h/.cache/pip:/cache/pip:z",
            "/h/.cache/pypoetry:/cache/poetry:z",
            "/h/.m2/repository:/cache/maven:z",
        ])
        self.assertTrue(all(not m.endswith(":z") for m in dc.cache_mounts(home, "")))
        self.assertEqual(len(dc.cache_dirs(home)), len(dc.CACHES))

    def test_git_ro_mount(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pwd = Path(tmp)
            self.assertEqual(dc.git_ro_mount(pwd, True), [])
            (pwd / ".git").mkdir()
            self.assertEqual(dc.git_ro_mount(pwd, True), [f"{pwd}/.git:{pwd}/.git:ro,z"])
            self.assertEqual(dc.git_ro_mount(pwd, False), [f"{pwd}/.git:{pwd}/.git:ro"])

    def test_target_volume(self) -> None:
        self.assertEqual(dc.target_volume("claude-target", Path("/home/me/proj")),
                         "claude-target_home_me_proj")

    def test_merge_envs_last_one_wins(self) -> None:
        with mock.patch.dict(os.environ, {"K1": "host"}, clear=False):
            os.environ.pop("K2", None)
            envs = dc.merge_envs({"A": "A=1", "B": "B=2"}, ["K1", "K2"],
                                 ["B=3", "K1=x", "C"], forward_searxng=True)
        self.assertEqual(envs, ["A=1", "B=3", "SEARXNG_URL=http://127.0.0.1:7331",
                                "K1=x", "C"])


class LauncherInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.env = dict(os.environ, HOME=str(self.root / "home"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_launcher(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(path), "--dry-run", "--engine", "podman", "--shell"],
            cwd=self.project, env=self.env, stdin=subprocess.DEVNULL,
            text=True, capture_output=True,
        )

    def test_symlinked_launcher_finds_module(self) -> None:
        bindir = self.root / "bin"
        bindir.mkdir()
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                link = bindir / name
                link.symlink_to(BINS / name)
                proc = self.run_launcher(link)
                self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_copied_launcher_fails_with_hint(self) -> None:
        bindir = self.root / "copied"
        bindir.mkdir()
        for name in LAUNCHERS:
            with self.subTest(launcher=name):
                copy = bindir / name
                shutil.copy(BINS / name, copy)
                proc = self.run_launcher(copy)
                self.assertNotEqual(proc.returncode, 0)
                self.assertEqual(
                    proc.stderr.strip(),
                    f"{name}: bins/devc_common.py not found next to the real "
                    "script; install the launcher as a symlink (see README)")


if __name__ == "__main__":
    unittest.main()

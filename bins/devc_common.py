"""devc_common — helpers shared by the bins/run-* launchers.

Stdlib only (Python 3.10+). The launchers import it from the directory of
their real (symlink-resolved) path, so installing them as symlinks into
~/.local/bin keeps working; a copied script fails with a hint instead.

Only mechanics that are identical across launchers live here: engine
resolution, the cache bind mounts, the read-only .git remount, the /target
volume name and the env merge. Harness-specific parts (state mounts,
passthrough key lists, ports, entries) stay in the scripts.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Host cache dirs (relative to $HOME) and where they are mounted, in the
# order every launcher emits them.
CACHES = [
    (".cargo/registry", "/usr/local/cargo/registry"),
    (".cache/coursier", "/cache/coursier"),
    (".ivy2", "/cache/ivy2"),
    (".cache/uv", "/cache/uv"),
    (".cache/pip", "/cache/pip"),
    (".cache/pypoetry", "/cache/poetry"),
    (".m2/repository", "/cache/maven"),
]

# Container loopback URL of the host SearXNG instance; podman forwards the
# port with `--network pasta:-T,7331`.
SEARXNG_URL = "http://127.0.0.1:7331"


def resolve_engine(engine: str) -> str:
    """Pick a container engine: explicit choice, or auto (podman -> docker)."""
    if engine and engine != "auto":
        return engine
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    sys.exit("error: no container engine found (tried podman, docker)")


def has_git_dir(pwd: Path) -> bool:
    """Whether the project has a .git to re-mount read-only (and report)."""
    return (pwd / ".git").exists()


def cache_dirs(home: Path) -> list[Path]:
    """Host cache dirs to create before launch (podman refuses to create them)."""
    return [home / rel for rel, _ in CACHES]


def cache_mounts(home: Path, z: str) -> list[str]:
    """-v specs for the shared build caches; z is ":z" or ""."""
    return [f"{home / rel}:{target}{z}" for rel, target in CACHES]


def git_ro_mount(pwd: Path, selinux: bool) -> list[str]:
    """-v spec re-mounting .git read-only over the project mount.

    The agent can read history and diffs, but every git write (commit, push,
    stash, checkout, rebase) fails inside the container -- commits belong on
    the host, signed with the operator's key. Empty for non-git projects:
    podman would create a stray .git/ dir in them.
    """
    if not has_git_dir(pwd):
        return []
    git_dir = pwd / ".git"
    return [f"{git_dir}:{git_dir}:{'ro,z' if selinux else 'ro'}"]


def target_volume(prefix: str, pwd: Path) -> str:
    """Named volume for /target, one per host project dir."""
    return prefix + str(pwd).replace("/", "_")


def merge_envs(fixed: dict[str, str], passthrough_keys: list[str],
               extra: list[str], *, forward_searxng: bool = False) -> list[str]:
    """-e specs: fixed, SearXNG, passthrough keys set on the host, then extras.

    `fixed` maps names to KEY=VALUE specs. Kept in a dict keyed by variable
    name so a later entry replaces an earlier one of the same key (last one
    wins): an --env in `extra` overrides a fixed default or a passthrough.
    """
    envs = dict(fixed)
    if forward_searxng:
        envs["SEARXNG_URL"] = f"SEARXNG_URL={SEARXNG_URL}"
    for key in passthrough_keys:
        if os.environ.get(key) is not None:
            envs[key] = key  # bare KEY: container copies the host value
    for spec in extra:
        envs[spec.split("=", 1)[0]] = spec
    return list(envs.values())

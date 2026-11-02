"""devc_common — helpers shared by the bins/run-* launchers.

Stdlib only (Python 3.10+). The launchers import it from the directory of
their real (symlink-resolved) path, so installing them as symlinks into
~/.local/bin keeps working; a copied script fails with a hint instead.

Only mechanics that are identical across launchers live here: engine
resolution, the cache bind mounts, the read-only .git remount, the /target
volume name, the env merge, and the multi-agent options (roles; see
MULTI_AGENT_ROLES_PLAN.md). Harness-specific parts (state mounts,
passthrough key lists, ports, entries) stay in the scripts.

The multi-agent options are opaque capabilities: a role name is only used
for paths, volume names and labels. Without them every launcher behaves
exactly as before (tests/test_golden_compat.py).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import re
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


def target_volume(prefix: str, pwd: Path, role: str | None = None,
                  shared: bool = False) -> str:
    """Named volume for /target, one per host project dir (and role).

    Legacy: <prefix><escaped pwd>. With a role: <prefix>.<role><escaped pwd>.
    The escaped path always starts with "_" and role names contain neither
    "." nor "_", so no project/role pair can produce another pair's name.
    `shared` keeps the legacy name even with a role.
    """
    escaped = str(pwd).replace("/", "_")
    if role is None or shared:
        return prefix + escaped
    return f"{prefix}.{role}{escaped}"


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


# --- multi-agent options ----------------------------------------------------

# Role names end up in paths, volume names and label values.
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")

# Label keys set automatically with --role.
LABEL_HARNESS = "sem.harness"
LABEL_PROJECT = "sem.project"
LABEL_ROLE = "sem.role"

# Exit status when the role lock is already held.
EXIT_ROLE_BUSY = 2


def role_name(value: str) -> str:
    """argparse type for --role."""
    if not ROLE_RE.match(value):
        raise argparse.ArgumentTypeError(
            f"invalid role {value!r}: use a lowercase letter followed by up to "
            "31 lowercase letters, digits or '-'")
    return value


def add_role_args(parser: argparse.ArgumentParser, *, lock: bool = True) -> None:
    """Register --role, --shared-target and (if the launcher locks roles)
    --allow-duplicate-role."""
    parser.add_argument("--role", type=role_name, metavar="NAME",
                        help="run as role NAME: per-role session state, per-role "
                             "/target volume, AGENT_ROLE and sem.* labels "
                             "(default: the legacy per-project layout)")
    parser.add_argument("--shared-target", action="store_true",
                        help="with --role: use the project's legacy /target volume "
                             "instead of the per-role one")
    if lock:
        parser.add_argument("--allow-duplicate-role", action="store_true",
                            help="with --role: start even if a container for this "
                                 "role and project is already running")


def check_role_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Reject role modifiers given without --role."""
    if args.role is not None:
        return
    if args.shared_target:
        parser.error("--shared-target requires --role")
    if getattr(args, "allow_duplicate_role", False):
        parser.error("--allow-duplicate-role requires --role")


def role_labels(harness: str, pwd: Path, role: str | None) -> list[str]:
    """`--label` options identifying a role container (none without a role)."""
    if role is None:
        return []
    labels = [f"{LABEL_HARNESS}={harness}", f"{LABEL_PROJECT}={pwd}",
              f"{LABEL_ROLE}={role}"]
    return [opt for label in labels for opt in ("--label", label)]


def acquire_role_lock(lock_path: Path, role: str, prog: str) -> int:
    """Take the one-container-per-role lock, or exit EXIT_ROLE_BUSY.

    An exclusive, non-blocking flock on `lock_path`, held in an inheritable
    fd so it survives os.execvp into podman/docker (ACP mode). In the other
    modes the launcher itself waits for the container client, so it holds
    the lock. Either way the lock lives exactly as long as the foreground
    container client. The kernel releases it when the last holder exits,
    even on a crash, so there is no stale-lock cleanup. The fd is
    deliberately never closed by the caller.
    """
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print(f"{prog}: role {role} is already running for this project "
              "(use --allow-duplicate-role to override)", file=sys.stderr)
        sys.exit(EXIT_ROLE_BUSY)
    os.set_inheritable(fd, True)
    return fd


# How the project dir is mounted. rw is today's behaviour; ro makes every
# project write fail; overlay (podman `:O`) lets the agent edit, build and
# test, but discards every project change when the container stops.
MOUNT_MODES = ("rw", "ro", "overlay")


def add_mount_args(parser: argparse.ArgumentParser) -> None:
    """Register --mount. Its default is None so an explicit `--mount rw` can
    be told apart from no flag (only the former exports AGENT_MOUNT_MODE)."""
    parser.add_argument("--mount", choices=MOUNT_MODES, metavar="MODE",
                        help="how the project dir is mounted: rw (default), ro, "
                             "or overlay (throwaway writes; podman only)")


def mount_mode(args: argparse.Namespace) -> str:
    """Effective mount mode (rw when --mount was not given)."""
    return args.mount or "rw"


def check_engine_support(args: argparse.Namespace, engine: str, prog: str) -> None:
    """Exit 2 for options the resolved engine cannot honour. Never fall back."""
    if mount_mode(args) == "overlay" and engine != "podman":
        print(f"{prog}: error: --mount overlay needs podman "
              f"({engine} has no :O overlay mounts)", file=sys.stderr)
        sys.exit(2)


def project_mount(pwd: Path, mode: str, selinux: bool) -> str:
    """-v spec for the project dir itself (always the first -v).

    podman rejects :O combined with :z, and the §4 spike showed :O works on
    never-labelled projects, so overlay carries no SELinux suffix.
    """
    if mode == "overlay":
        return f"{pwd}:{pwd}:O"
    if mode == "ro":
        return f"{pwd}:{pwd}:{'ro,z' if selinux else 'ro'}"
    return f"{pwd}:{pwd}{':z' if selinux else ''}"


def mount_envs(args: argparse.Namespace) -> list[str]:
    """AGENT_MOUNT_MODE, only when --mount was given explicitly."""
    return [] if args.mount is None else [f"AGENT_MOUNT_MODE={args.mount}"]

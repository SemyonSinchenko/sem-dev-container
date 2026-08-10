# sem-dev-container

Layered dev containers for agentic development. A single heavy **shared**
base image carries all compilers, runtimes, and tools; each agent harness
(pi, prime-agent) is a thin layer on top.

```
┌────────────────────────────┐   ┌──────────────────────────────┐
│ pi (thin)                  │   │ prime-agent (thin)           │
│  • pi coding agent         │   │  • prime-agent CLI           │
│  • openspec CLI            │   │  • IPython kernel venv       │
├────────────────────────────┴───┴──────────────────────────────┤
│ shared (heavy, ~everything)                                    │
│  Ubuntu 24.04 base                                             │
│  • build-essential, cmake, protoc, gdb (built from source)     │
│  • Java 8/11/17/21 (+use-java), sbt, Maven (cache → /cache)    │
│  • Node 22, buf, uv + Python 3.10–3.13, poetry, pre-commit     │
│  • zig (cargo-zigbuild), Rust toolchain + rust-analyzer/clippy │
│  • Go + open-spdd, hardwood-cli (Parquet)                      │
│  • fd/rg preinstalled so agents reuse them                     │
│  • shared caches under /cache for host bind-mounts             │
└────────────────────────────────────────────────────────────────┘
```

## Build

```sh
make shared        # podman build -f shared/Dockerfile -t dev-shared .
make pi            # + pi agent
make prime-agent   # + prime-agent agent
make all           # both
```

Docker instead of podman: `make ENGINE=docker all`.

The shared base is reusable: a new harness is just a `FROM dev-shared`
Dockerfile in its own subfolder plus a `make` target.

## Run

```sh
# pi
podman run --rm -it -v "$PWD:/work" dev-pi

# prime-agent (run inside the project dir it should work on)
cd /path/to/project
podman run --rm -it -v "$PWD:/work" -w /work dev-prime-agent
```

## Layout

| Path | Role |
|------|------|
| `shared/Dockerfile`    | heavy base: compilers, runtimes, generic tools, caches |
| `pi/Dockerfile`        | `FROM dev-shared` + pi coding agent + openspec |
| `prime-agent/Dockerfile` | `FROM dev-shared` + prime-agent + its IPython kernel venv |
| `bins/run-pi`          | run the `dev-pi` container with host caches/state mounted |
| `bins/run-pa`          | run the `dev-prime-agent` container with host caches/state mounted |
| `.m2/settings.xml`     | global Maven settings, COPYed into the shared layer |
| `Makefile`             | build targets (`make shared|pi|prime-agent|all`) |

## Running: bins/run-pi and bins/run-pa

The old `run-pi` bash function in `.bashrc` is replaced by two standalone
Python 3 scripts (stdlib only, Python 3.10+), one per harness. Symlink them
into `~/.local/bin` (or anywhere on `$PATH`):

```sh
ln -s ~/sem-dev-container/bins/run-pi ~/.local/bin/run-pi
ln -s ~/sem-dev-container/bins/run-pa ~/.local/bin/run-pa
```

Both scripts do what the bash function did: `mkdir -p` the host cache dirs,
mount them into `/cache/*` and `/usr/local/cargo/registry`, mount the agent
state dir (`~/.pi` for pi, `~/.prime` for prime-agent) to `/root`, mount the
current dir at the same path, attach a per-project named volume for
`/target`, and pass through API keys that are set on the host.

```sh
run-pi                       # start the pi agent in $PWD
run-pi -- --resume <sess>    # flags after -- go to the agent binary
run-pi --shell               # drop into bash in the container (resume/manage sessions)
run-pi --exec ls /work       # run an arbitrary command instead of the agent

run-pa                       # start prime-agent in $PWD
run-pa agents                # browse sessions (=> prime-agent agents)
run-pa status                # inspect background services
run-pa attach <agent>        # reattach to a running session
run-pa --shell               # drop into bash in the container

run-pi --engine docker       # docker instead of podman (default: auto-detect)
run-pi --no-selinux          # drop the :z suffix on bind mounts
run-pi --env FOO=bar         # extra env var; KEY alone passes the host value
run-pi --dry-run             # print the podman/docker command without running
```

> Script options must come before `--` or `--exec`; everything after is
> container-side.

## Notes

- Base is Ubuntu 24.04. To share a compiled cache (e.g. cargo `target/`)
  with your host, match the base to your host distro/glibc.
- Prime agent's kernel venv is prebuilt at `/opt/prime-agent/kernel-venv`
  (world-writable) during the image build, so it works under any runtime
  UID incl. `--userns=keep-id`. Bump `PRIME_AGENT_VERSION` in
  `prime-agent/Dockerfile` to upgrade.
- Bind-mount host caches onto `/cache/*` to share downloads across runs
  (see `shared/Dockerfile` step 7b for the full list).

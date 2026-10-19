# sem-dev-container

Layered dev containers for agentic development. A single heavy **shared**
base image carries all compilers, runtimes, and tools; each agent harness
(pi, prime-agent, dsh) is a thin layer on top.

┌────────────────────────┐   ┌──────────────────────┐   ┌───────────────────────┐
│ pi (thin)              │   │ prime-agent (thin)   │   │ dsh (thin)            │
│ • pi coding agent      │   │ • prime-agent CLI    │   │ • dsh web (npm) + pnpm│
│ • openspec CLI (shared)│   │ • IPython kernel venv│   │ • RPC for dsh-emacs   │
├────────────────────────┴──────────────────────────┴───────────────────────────┤
│ shared (heavy, ~everything)                                                   │
│  Ubuntu 24.04 base                                                            │
│  • build-essential, cmake, protoc, gdb (built from source)                    │
│  • Java 8/11/17/21/25 (+use-java), sbt, Maven (cache → /cache)                │
│  • Node 22, buf, uv + Python 3.10–3.15, poetry, pre-commit, openspec CLI      │
│  • zig (cargo-zigbuild), Rust toolchain + rust-analyzer/clippy                │
│  • Go + open-spdd, hardwood-cli (Parquet)                                     │
│  • fd/rg preinstalled so agents reuse them                                    │
│  • shared caches under /cache for host bind-mounts                            │
└───────────────────────────────────────────────────────────────────────────────┘

## Build

```sh
make shared        # podman build -f shared/Dockerfile -t dev-shared .
make pi            # + pi agent
make prime-agent   # + prime-agent agent
make dsh           # + DeepSeek Harness (dsh web profile)
make all           # all three
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

# dsh — use run-dsh: it mounts caches/state and picks a free host port
cd /path/to/project
run-dsh
```

## Layout

| Path | Role |
|------|------|
| `shared/Dockerfile`    | heavy base: compilers, runtimes, generic tools, caches, openspec CLI |
| `pi/Dockerfile`        | `FROM dev-shared` + pi coding agent |
| `prime-agent/Dockerfile` | `FROM dev-shared` + prime-agent + its IPython kernel venv |
| `bins/run-pi`          | run the `dev-pi` container with host caches/state mounted |
| `bins/run-pa`          | run the `dev-prime-agent` container with host caches/state mounted |
| `bins/run-dsh`         | run the `dev-dsh` container attached to its logs; publishes a free port from 30780–30789 |
| `dsh/Dockerfile`       | `FROM dev-shared` + dsh CLI (npm) + pnpm + the foreground wrapper |
| `dsh/dsh-web-entry`    | container-side wrapper: binds the container IP; Ctrl-D/Ctrl-C stops the harness |
| `.m2/settings.xml`     | global Maven settings, COPYed into the shared layer |
| `Makefile`             | build targets (`make shared|pi|prime-agent|dsh|all`) |

## Running: bins/run-pi, run-pa, and run-dsh

The old `run-pi` bash function in `.bashrc` is replaced by standalone
Python 3 scripts (stdlib only, Python 3.10+), one per harness. Symlink them
into `~/.local/bin` (or anywhere on `$PATH`):

```sh
ln -s ~/sem-dev-container/bins/run-pi ~/.local/bin/run-pi
ln -s ~/sem-dev-container/bins/run-pa ~/.local/bin/run-pa
ln -s ~/sem-dev-container/bins/run-dsh ~/.local/bin/run-dsh
```

The scripts do what the bash function did: `mkdir -p` the host cache dirs,
mount them into `/cache/*` and `/usr/local/cargo/registry`, mount the agent
state dir (`~/.pi` for pi, `~/.prime` for prime-agent, `~/.dsh` for dsh) to
`/root`, mount the current dir at the same path with `.git` layered over it
read-only (git writes fail inside the container; commit on the host), attach
a per-project named volume for `/target`, and pass through API keys that are
set on the host.

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

### DeepSeek Harness: bins/run-dsh

`run-dsh` starts the dsh web profile (server + web UI) on the current
project folder and attaches to its logs:

```sh
cd /path/to/project
run-dsh                        # foreground: logs stream; Ctrl-D or Ctrl-C stops it
run-dsh -- --verbose           # flags after -- go to `dsh web` (container side)
run-dsh --shell                # bash in the container instead of the server
run-dsh --dry-run              # print the podman command
```

Port: the container serves on 3080; `run-dsh` publishes it on
`127.0.0.1:<port>` with a free port picked from the rare range
**30780–30789**, so up to 10 sessions can run in parallel (different
project folders; harness state in `~/.dsh` is shared). It also passes
`--public-url`/`--trusted-host`, so the server's `dsh web:
http://127.0.0.1:<port>/?token=…` line is valid from the host as printed.

Connect Emacs ([dsh-emacs](https://github.com/vritser/dsh-emacs)) to the
printed URL:

```emacs-lisp
(add-to-list 'load-path (expand-file-name "~/dsh-emacs"))
(require 'dsh-emacs)
(setq dsh-emacs-server-auto-start nil)  ; never spawn a host dsh behind the container
;; M-x dsh-emacs-edit-config, then paste the URL run-dsh printed, e.g.
;;   http://127.0.0.1:30781/?token=...      (token changes every run)
```

The same URL opens the Web UI in a host browser for the initial
provider/model setup (Settings → Models); the key persists in `~/.dsh`
across runs. Ctrl-D (stdin EOF) and Ctrl-C (SIGINT) both stop the harness
gracefully and remove the container.

## Web search (SearXNG)

The host runs SearXNG 24/7 (quadlet
`~/.config/containers/systemd/searxng.container`, published on host
`127.0.0.1:7331`). All launchers run podman with `--network pasta:--map-gw`,
which maps the container's gateway address to the host loopback, so
containers reach the instance at `http://host.containers.internal:7331`.
prime-agent gets the matching `searxng` skill from the host state dir
(`~/.prime/agent/skills/searxng`), not from the image; override the URL with
`SEARXNG_URL`.

Docker cannot map host loopback (`--network pasta:...` is podman-only):
publish the SearXNG port on a reachable address in the quadlet
(`PublishPort=`) and run with `--env SEARXNG_URL=http://<host>:7331`.

## Notes

- Base is Ubuntu 24.04. To share a compiled cache (e.g. cargo `target/`)
  with your host, match the base to your host distro/glibc.
- Prime agent's kernel venv is prebuilt at `/opt/prime-agent/kernel-venv`
  (world-writable) during the image build, so it works under any runtime
  UID incl. `--userns=keep-id`. Bump `PRIME_AGENT_VERSION` in
  `prime-agent/Dockerfile` to upgrade.
- Concurrent `run-pa` containers (different project folders) are safe. The
  daemon supervisor registry is moved to the container-private
  `/tmp/prime-agent-supervisor-owners` via
  `PRIME_AGENT_INTERNAL_DAEMON_SUPERVISOR_REGISTRY_DIR` (set in both
  `prime-agent/Dockerfile` and `bins/run-pa`). The default location,
  `~/.prime/supervisor-owners`, is "global per user, one machine" state whose
  records arbitrate local daemons by socket-path string + pid — both
  meaningless across containers, so a shared registry let the second
  container's daemon steal the first one's entry and killed its daemon with
  "supervisor generation ... no longer owns its registry entry". `run-pa`
  also overlays the host-mounted `~/.prime/agent/daemon-workers` with a tmpfs:
  worker descriptors use that same default socket-path string but refer to
  processes and worker sockets in a container-private `/tmp`; keeping them
  would make a later container try to adopt dead workers. Sessions, auth, and
  config still live in the shared host `~/.prime`; only daemon-lifetime
  ownership and worker state are per-container. Container `/tmp` needs no
  mount — it is private per container.
- Bind-mount host caches onto `/cache/*` to share downloads across runs
  (see `shared/Dockerfile` step 7b for the full list).
- dsh containers share the host `~/.dsh` (profiles, plugins, provider keys,
  sessions) the way prime-agent shares `~/.prime`. The web profile
  initializes on first use into that directory — the first `run-dsh` on a
  machine runs pnpm once (the image installs pnpm 11 for it; the dsh npm
  package resolves pnpm from PATH), later runs start instantly. Parallel
  `run-dsh` containers in different project folders share that state
  read-mostly; two servers on the *same* workspace is not a supported
  shape. dsh rejects wildcard bind addresses by design, so
  `dsh/dsh-web-entry` binds the container's own IP and `run-dsh` publishes
  the port on `127.0.0.1` only — dsh-emacs and the browser are expected on
  the same machine.

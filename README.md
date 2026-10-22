# sem-dev-container

Layered dev containers for agentic development. A single heavy **shared**
base image carries all compilers, runtimes, and tools; each agent harness
(pi, prime-agent, dsh) is a thin layer on top.

┌────────────────────────┐   ┌──────────────────────┐   ┌───────────────────────┐
│ pi (thin)              │   │ prime-agent (thin)   │   │ dsh (thin)            │
│ • pi coding agent      │   │ • prime-agent CLI    │   │ • dsh web + ACP (npm) │
│ • openspec CLI (shared)│   │ • IPython kernel venv│   │ • RPC + ACP clients   │
├────────────────────────┴──────────────────────────┴───────────────────────────┤
│ shared (heavy, ~everything)                                                   │
│  Ubuntu 26.04 base                                                            │
│  • build-essential, cmake, protoc, gdb (built from source)                    │
│  • Java 8/11/17/21/25 (+use-java), sbt, Maven (cache → /cache)                │
│  • Node 22, buf, uv + Python 3.10–3.15, poetry, pre-commit, openspec CLI      │
│  • zig (cargo-zigbuild), Rust toolchain + rust-analyzer/clippy                │
│  • Go + open-spdd, hardwood-cli (Parquet)                                     │
│  • emacs-nox (30.x) + gnuplot; fd/rg preinstalled so agents reuse them        │
│  • shared caches under /cache for host bind-mounts                            │
└───────────────────────────────────────────────────────────────────────────────┘

## Build

```sh
make shared        # podman build -f shared/Dockerfile -t dev-shared .
make pi            # + pi agent
make prime-agent   # + prime-agent agent
make dsh           # + DeepSeek Harness (dsh web and acp profiles)
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

# dsh as an ACP stdio server for agent-shell/acp.el (no port, no URL)
run-dsh --acp
```

## Layout

| Path | Role |
|------|------|
| `shared/Dockerfile`    | heavy base: compilers, runtimes, generic tools, caches, openspec CLI |
| `pi/Dockerfile`        | `FROM dev-shared` + pi coding agent |
| `prime-agent/Dockerfile` | `FROM dev-shared` + prime-agent + its IPython kernel venv |
| `bins/run-pi`          | run the `dev-pi` container with host caches/state mounted |
| `bins/run-pa`          | run the `dev-prime-agent` container with host caches/state mounted |
| `bins/run-dsh`         | run the `dev-dsh` container attached to its logs; publishes a free port from 30780–30789, or serves ACP over stdio with `--acp` |
| `dsh/Dockerfile`       | `FROM dev-shared` + dsh CLI (npm) + pnpm + both foreground wrappers |
| `dsh/dsh-web-entry`    | container-side web wrapper: binds the container IP; Ctrl-D/Ctrl-C stops the harness |
| `dsh/dsh-acp-entry`    | container-side ACP wrapper: foreground stdio, quiet profile preflight, execs `dsh --profile acp` |
| `dsh/project-workspace.mjs` | boot plugin: registers the project folder as the session Workspace |
| `dsh/project-workspace.patch.yml` | overlay: points sessions/storages/attachments at `<project>/.dsh` |
| `dsh/searxng-search.mjs` | native `web_search` provider backed by the host SearXNG instance |
| `dsh/searxng-search.patch.yml` | overlay: selects the SearXNG provider for `web_search` |
| `dsh/dev-container.mjs` | image-local policy plugin: container runtime context, access policy + git-write guard |
| `dsh/dev-container.patch.yml` | overlay: loads the dev-container policy plugin |
| `dsh/permission.patch.yml` | overlay: maps `DSH_PERMISSION_MODE` to the default permission preset |
| `dsh/dev-container.test.mjs` | unit tests for the policy parser and prompt text |
| `tests/test_run_dsh.py` | dry-run contract tests for `run-dsh` web/ACP modes |
| `.m2/settings.xml`     | global Maven settings, COPYed into the shared layer |
| `Makefile`             | image build targets (`make all`, `make dsh`, …) plus `make test-dsh` / `make test-run-dsh` |

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
project folder and attaches to its logs; `run-dsh --acp` instead serves ACP
over stdio (see the ACP mode subsection below):

```sh
cd /path/to/project
run-dsh                        # foreground: logs stream; Ctrl-D or Ctrl-C stops it
run-dsh -- --verbose           # flags after -- go to `dsh web` (container side)
run-dsh --shell                # bash in the container instead of the server
run-dsh --dry-run              # print the podman command
```

After the server prints its own `dsh web: …` line, `run-dsh` adds three
lines for the host side: the complete clickable URL with the token, the
published host port, and the token. The last two are raw values intended for
monitoring scripts; the logs continue streaming normally.

```text
http://127.0.0.1:30781/?token=…
30781
…
```

Port: the container serves on 3080; `run-dsh` publishes it on
`127.0.0.1:<port>` with a free port picked from the rare range
**30780–30789**, so up to 10 sessions can run in parallel (different
project folders; each project's data is isolated under its own `.dsh`).
It also passes `--trusted-host`, so the server admits the mapped
`127.0.0.1:<port>` authority. The server's own `dsh web:` line uses the
container-side port **3080**; the three-line block above is the host-ready
form.

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

#### ACP mode: `run-dsh --acp`

`run-dsh --acp` starts the same `dev-dsh` container — same project mount at
the same absolute path, same `~/.dsh`/`~/.agents`/cache mounts, working
directory, environment, and `<project>/.dsh` isolation — but runs
`dsh-acp-entry` → `dsh --profile acp` as a foreground
[Agent Client Protocol](https://agentclientprotocol.com) stdio server:

```sh
cd /path/to/project
run-dsh --acp                  # ACP on stdin/stdout: no port, URL, or token
run-dsh --acp --dry-run        # print the podman/docker command
run-dsh --acp --env DSH_PERMISSION_MODE=workspace-write   # per-run policy
```

The launcher `exec`s podman/docker, so the ACP client owns stdin/stdout, the
client's signals reach the container engine, and the exit status is the
container's. ACP mode forces `-i` and never allocates a pseudo-TTY (`-t`),
even when started from a terminal: terminal echo and CR/LF translation corrupt
ACP framing. It publishes no port, adds no `--trusted-host`, prints no web
banner, and runs no log streaming or URL/port parsing. It accepts the same
`--engine`, `--image`, `--env`, `--no-selinux`, and `--dry-run` options as web
mode, is mutually exclusive with `--shell` and `--exec`, and forwards trailing
arguments after `--` to `dsh --profile acp`.

**stdout is a protocol socket.** Every stdout byte is ACP JSON-RPC (ndjson);
diagnostics, warnings, and first-run profile initialization go to stderr. If
stdin is a terminal, `run-dsh` warns on stderr and continues (dry runs do not
warn) because a human cannot usefully type JSON-RPC there. Do not merge any
other output into stdout.

**agent-shell integration.**
[agent-shell](https://github.com/xenodium/agent-shell) starts the command from
the client buffer's `default-directory`, so start it from the project root:

```emacs-lisp
(setq agent-shell-deepseek-acp-command '("run-dsh" "--acp"))
;; Optional: choose defaults at session start; the ACP profile does not
;; inherit the web profile's provider/model settings.
;; (setq agent-shell-deepseek-default-config-options
;;       '(("model" . "deepseek-v4-flash")
;;         ("reasoning_effort" . "high")))
```

Then `M-x agent-shell-deepseek-start-agent` from a buffer in the project. No
port/token parsing and no environment injection from `acp-make-client` are
needed: `run-dsh` owns the container environment and passes provider API keys
through from Emacs's environment (or `~/.dsh/.credentials.yaml`). Closing the
agent-shell buffer closes stdin; the ACP server exits and `--rm` removes the
container.

**Profiles and models.** The `acp` profile lives in `~/.dsh/profiles/acp`,
separate from `~/.dsh/profiles/web`. Its shipped default is
`provider: deepseek-official`, `model: deepseek-v4-flash`; model and reasoning
effort are chosen by the client at session start. Web UI model/provider
settings and edits to `~/.dsh/profiles/web/cordis.patch.yml` do **not**
automatically carry over. To align them, copy the relevant model/provider rows
(for example `llm-*` and `agent-default-model`) into
`~/.dsh/profiles/acp/cordis.patch.yml`, or set defaults through
`agent-shell-deepseek-default-config-options`.

**Isolation and lifecycle.** `dsh-acp-entry` loads the same overlays as the
web entry in the same order: `project-workspace.patch.yml` (required when
`DSH_PROJECT_HOME` is set), then `searxng-search`, `dev-container`, and
`permission`. Sessions, storages, and attachments therefore stay in
`<project>/.dsh`, and `session/list` does not show another project's sessions.
The dev-container prompt policy, git-write guard, and
`DSH_PERMISSION_MODE` default (`danger-full-access` unless overridden) match
web mode. SearXNG forwarding is identical under podman (`pasta:-T,7331` plus
`SEARXNG_URL`); ACP adds no published port or `--host-lo-to-ns-lo`. Each
invocation is one ephemeral container; the first run initializes
`~/.dsh/profiles/acp` with its output kept off stdout. ACP sessions take their
cwd from the client rather than from the Web UI Workspace registry, so the
`project-workspace` insert can stay pending in this composition (dsh reports
it on stderr); the overlay's persistence/storage/attachment redirects still
apply to `<project>/.dsh`.

Closing the client closes stdin, which is the normal shutdown path. SIGTERM
(what `acp.el` sends first to the process group) reaches podman/docker and
stops the `--rm` container. An uncatchable SIGKILL can leave the container
running after its engine client dies; remove it with
`podman ps` / `podman rm -f` (or the docker equivalents).

Only one dsh mode per project at a time is supported: web and ACP write the
same `<project>/.dsh` store. Do not run both against one project concurrently.
The dry-run command shape is covered by `make test-run-dsh`.

#### Container policy: runtime context and git guard

Both entrypoints load `dsh/dev-container.patch.yml` unconditionally. Its
plugin (`dsh/dev-container.mjs`) adds a runtime-context snapshot to every
session, alongside DSH's own sandbox/approval facts:

- the access policy: the container is a disposable isolation boundary, so the
  agent acts freely and never asks for permission (DSH's file sandbox is off
  and approvals never fire; see the next subsection);
- the project is a host bind mount; the OS is disposable (apt/pip/npm
  installs, `/etc`, `/usr`, `/tmp`, services vanish when the container stops);
- the container runs as root, `sudo` is not installed, so use `apt-get`/`apt`
  directly; there is no systemd;
- `CARGO_TARGET_DIR=/target` is a per-project named volume; the project's
  `./target` is not the build output and may contain host binaries — a
  `GLIBC_… not found` error there is an ABI mismatch, not a bug to debug;
- `.git` is mounted read-only; commits are the operator's job on the host,
  signed with their key. The agent reports a `git diff` plus commit message.

The plugin also installs a `tools.guard` that denies git write commands
(`commit`, `push`, `stash`, `checkout`, …) with that policy message instead
of a raw read-only-filesystem error. The read-only mount enforces local git
state; the guard adds a clear denial and also covers remote writes such as
`push`, which a read-only local `.git` does not necessarily prevent. The
guard is best-effort UX (aliases, heredocs and subprocesses can evade shell
parsing). A failed command that ran a project-relative `target/debug`
or `target/release` binary gets a short `tools/post-execute` hint pointing
at `/target`.

`bins/run-dsh` exports `DSH_DEV_CONTAINER=1` as a launch marker,
`DSH_PERMISSION_MODE=danger-full-access` (the permission policy described
below) and, when the project has a `.git`, `DSH_GIT_READ_ONLY=1`; the plugin
renders the project path, target path and git mode from the environment.
Because this is an image-local deployment policy (not project configuration),
it applies to every project and is not written into the shared `~/.dsh` or the
project's `AGENTS.md`. `dsh` does not read `APPEND_SYSTEM.md`; this plugin is
the dsh equivalent. Put project-specific build/test rules in the project's
`AGENTS.md` instead.

Disable the guard for one run (debugging) with
`run-dsh --env DSH_CONTAINER_GIT_GUARD=0`; the read-only `.git` mount and the
context are unaffected. Rebuild the image after editing the plugin
(`make dsh`) and run its tests with:

```sh
make test-dsh
```

#### Permissions: sandbox, approvals, and the freedom mandate

`run-dsh` starts every session with DSH's permissions wide open. The launcher
exports `DSH_PERMISSION_MODE=danger-full-access`, which the shipped base
profile maps to:

- **file policy `danger-full-access`** — `@deepseek-ai/dsh-bash-sandbox` runs
  commands unwrapped (no bwrap/Landlock) and `@deepseek-ai/dsh-fs-sandbox`
  delegates file mutations unfenced;
- **approval policy `never`** — no approval prompt can appear and the
  fail-closed approval path is out of the picture (there is nothing to
  escalate when the sandbox is already off).

DSH has **no** `IS_SANDBOX` / `--dangerously-skip-permissions` /
`allow-dangerous-actions` switch. Permissions are a **permission preset**: a
sandbox mode plus an approval policy, and every surface writes the same
session events. The knobs, weakest to strongest:

| Where | Knob | Scope |
|-------|------|-------|
| environment | `DSH_PERMISSION_MODE` (`read-only`, `workspace-write`, `danger-full-access`; base default `workspace-write`) | deployment default: `sandbox-policy.mode`, and `approval.policy=never` iff `danger-full-access` |
| `$DSH_HOME/profiles/<name>/cordis.patch.yml` | `sandbox-policy.mode`, `approval.policy`, `permission.presets`, `permission.defaultPreset` | profile/deployment |
| any `dsh --patch` overlay (applied after the profile) | the same rows; `dsh/permission.patch.yml` does this for the container | image-local override |
| Web UI permission selector or `/permission <preset>` | session events `permission/preset`, `sandbox/mode`, `approval/policy` | one live session; survives reload by replay |

`permission.defaultPreset` is what pins a fresh session, and the image-local
`dsh/permission.patch.yml` overlay (loaded after the profile) sets it from
`DSH_PERMISSION_MODE` with a `danger-full-access` fallback. The launcher knob
is therefore authoritative for new sessions, replacing whatever
`defaultPreset` the shared `~/.dsh` profile carries. Per-session switches are
stronger still: the Web UI selector and `/permission <preset>` append
`sandbox/mode`/`approval/policy` events that win over the initial pin.
Because the overlay replaces the whole `permission` row, presets added to
the shared profile do not reach the container; the three standard presets
remain available to `/permission`.

Override one run without editing anything:

```sh
run-dsh --env DSH_PERMISSION_MODE=workspace-write   # or read-only
```

and switch a single session back from the Web UI (or `/permission
danger-full-access`). The dev-container context now states the policy
explicitly: inside the container the agent is meant to act, not ask;
`ask_user_question` is for user-owned decisions, not for permission to run a
command or edit a file.

Only what the container physically cannot do still constrains it:

- **git writes** — `.git` is a read-only bind mount (see above), so commits and
  other ref/index writes fail; the guard turns that into a clear message;
- **read-before-write** — DSH's `fs-observation-policy` row requires a `read`
  of a file before `edit` or an overwriting `write`. It is a clobber guard, not
  a security boundary; remove the row in a patch to allow blind writes;
- **command deadline** — `bash` uses the `bash-sandbox` row's 60 s default
  (capped at 600 s); on expiry the command is promoted to a background job
  rather than killed. Pass `timeoutMs` or `run_in_background` for long builds.

Other environment variables that matter for capability, not permission:
`DSH_WEB_SEARCH_PROVIDER` / `DSH_WEB_FETCH_PROVIDER` (which web backend to
use), `DSH_TOOLS_MODE` (`native` or `ptc`), `DSH_TELEMETRY_DISABLED`, and, for
the SDK profile only, `DSH_CONTEXT_WINDOW` and `DSH_SYSTEM_PROMPT`.

#### Project-local state

`run-dsh` keeps project data inside the project folder, so different
projects cannot see each other's sessions or Workspace registry:

```text
<project>/.dsh/
  sessions/      # session logs (session.v4.jsonl.zstd)
  storages/      # Workspace registry, projection caches
  attachments/   # image/attachment objects
  skills/        # optional project-local skills (DSH already scans this)
```

Add `.dsh/` to the project's `.gitignore` (this repository already does).
DSH's project-data roots are redirected there by
`dsh/project-workspace.patch.yml`; `bins/run-dsh` exports the paths as
`DSH_PROJECT_DIR` / `DSH_PROJECT_HOME` / `DSH_PROJECT_SESSIONS` /
`DSH_PROJECT_STORAGES`, and both entrypoints load the overlay. The
`project-workspace` plugin registers `$PWD` as the one session Workspace at
boot, so the first blank session starts in the project instead of DSH's
first-use `default-workspace`.

`~/.dsh` remains the shared **config** home: profiles, installed plugins,
model/provider settings, `.credentials.yaml`, global `AGENTS.md`/skills,
and startup-failure diagnostics. It no longer receives project sessions,
Workspace registries, or attachments from `run-dsh`. The old
`~/.dsh/sessions` / `storages` / `attachments` trees are left untouched for
non-`run-dsh` use; they are not consulted by a `run-dsh` container.

To start a project with existing history, move that project's old session
directory into the new root before the first run:

```sh
mkdir -p .dsh/sessions
mv ~/.dsh/sessions/--var-home-sem-...-- .dsh/sessions/
```

The next boot bootstraps the project Workspace from those session headers.
Image attachments referenced by migrated sessions must be copied from
`~/.dsh/attachments` separately (new sessions store attachments in
`<project>/.dsh/attachments`).

Rebuild the image after changing the overlay or plugin:

```sh
make dsh
```

## Web search (SearXNG)

The host runs SearXNG 24/7 (quadlet
`~/.config/containers/systemd/searxng.container`, published on host
`127.0.0.1:7331`). The Podman launchers use `--network pasta:-T,7331` to
forward only container `127.0.0.1:7331` to host `127.0.0.1:7331`, and set
`SEARXNG_URL=http://127.0.0.1:7331` in the container. This avoids depending
on the container's dynamic gateway or Podman's `host.containers.internal`
address.

For DSH, `web_search` stays the native model-facing tool: the
`searxng-search` overlay (`dsh/searxng-search.patch.yml`) registers a
SearXNG provider (`dsh/searxng-search.mjs`) and selects it in the `web`
service. The provider queries `$SEARXNG_URL/search?format=json` and returns
normalized titles, URLs and snippets, so no skill, API key or Python kernel
is involved. `web_fetch` remains the anonymous HTTP provider.

Overrides (environment):

- `SEARXNG_URL` — base URL, default `http://127.0.0.1:7331`; an explicit
  `--env SEARXNG_URL=...` still overrides the launcher default.
- `SEARXNG_TIMEOUT_MS` — HTTP timeout, default `30000`.

If `web_search` reports the SearXNG provider unreachable:

- the container must be (re)started by an updated `run-dsh` (the pasta
  flag and `SEARXNG_URL` are part of the launch), or
- the host SearXNG quadlet must be running
  (`systemctl --user status searxng.service`), or
- the instance must allow the JSON API (SearXNG `search.formats` includes
  `json`), or
- under Docker, publish the port and pass
  `--env SEARXNG_URL=http://<host>:7331`.

prime-agent gets the matching `searxng` skill from the host state dir
(`~/.prime/agent/skills/searxng`), not from the image.

Docker has no pasta port-forwarding equivalent: publish the SearXNG port on
a host-reachable address in the quadlet (`PublishPort=`) and run with
`--env SEARXNG_URL=http://<host>:7331`.

## Notes

- Base is Ubuntu 26.04 (emacs-nox ships Emacs 30.x). To share a compiled cache (e.g. cargo `target/`)
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
- dsh containers share the host `~/.dsh` for **config only**: profiles,
  installed plugins, model/provider settings and provider keys. The web and
  acp profiles initialize on first use into that directory — the first
  web-mode `run-dsh` on a machine runs pnpm once (the image installs pnpm 11
  for it; the dsh npm package resolves pnpm from PATH), later runs start
  instantly; the acp profile has no external dependencies to install.
  Project data (sessions, Workspace registry, attachments) lives in
  `<project>/.dsh`; parallel `run-dsh` containers in different project
  folders are isolated. Web and ACP against the *same* project share one
  `.dsh` store and are not a supported shape. dsh rejects wildcard bind
  addresses by design, so web mode's `dsh/dsh-web-entry` binds the container's
  own IP and `run-dsh` publishes the port on `127.0.0.1` only — dsh-emacs and
  the browser are expected on the same machine. ACP mode publishes nothing;
  its client speaks over stdin/stdout.

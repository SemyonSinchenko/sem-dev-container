# syntax=docker/dockerfile:1
#
# Codex layer on top of the shared base (see ../shared/Dockerfile).
# Ships two npm packages: @openai/codex (the `codex` CLI/TUI, a native musl
# binary behind a node shim) and @agentclientprotocol/codex-acp (the Agent
# Client Protocol adapter, `codex-acp`, which drives `codex app-server` and
# speaks ACP over stdio to clients such as Emacs agent-shell/acp.el).
# bins/run-codex is the launcher: the default mode runs the TUI, --acp hands
# stdio to the adapter.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f codex/Dockerfile -t dev-codex .
#
# Or use the Makefile:  make codex
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Pins are bumped together: the adapter depends on @openai/codex ^0.165.0
# (i.e. 0.165.x) and warns that other app-server versions may not be
# compatible. Keeping the CLI on that release lets CODEX_PATH below point the
# adapter at the image's own `codex`, so the TUI and ACP run one binary.
ARG CODEX_VERSION=0.165.0
ARG CODEX_ACP_VERSION=2.5.0

# The adapter's own @openai/codex dependency carries a second copy of the
# native binaries (~390 MB). CODEX_PATH makes it unused, so drop it; the build
# check below proves the adapter still loads.
RUN npm install -g --no-fund --no-audit --loglevel=warn --progress=false \
        "@openai/codex@${CODEX_VERSION}" \
        "@agentclientprotocol/codex-acp@${CODEX_ACP_VERSION}" \
    && rm -rf /usr/lib/node_modules/@agentclientprotocol/codex-acp/node_modules/@openai \
    && npm cache clean --force \
    && test "$(command -v codex)" = /usr/bin/codex \
    && codex --version \
    && codex-acp --version

# CODEX_HOME: spelled out so the bins/run-codex mount target is visible in
# the image (host ~/.codex: auth, config.toml, AGENTS.md, skills, rules).
# CODEX_SQLITE_HOME: Codex's SQLite state (thread index behind session/list,
# logs, memories, goals, queue) lives here instead of in CODEX_HOME; run-codex
# over-mounts it from the project, so the shared home holds no session state.
# CODEX_PATH: the adapter spawns this `codex` (the policy wrapper below)
# instead of its bundled copy (removed above).
# NO_BROWSER: hides the adapter's browser-based ChatGPT login, whose localhost
# callback cannot reach a container; log in with `codex login --device-auth`.
ENV CODEX_HOME=/root/.codex \
    CODEX_SQLITE_HOME=/root/.codex/sqlite \
    CODEX_PATH=/usr/local/bin/codex \
    NO_BROWSER=1

# Image-local container policy, the Codex equivalent of /etc/claude-code:
#   - /usr/local/bin/codex: wrapper ahead of /usr/bin/codex on PATH. Maps
#     CODEX_PERMISSION_MODE to `-c sandbox_mode/approval_policy/
#     approvals_reviewer` overrides for every invocation (TUI, --shell, the
#     adapter's app-server). CLI overrides beat ~/.codex/config.toml and,
#     unlike managed config, do not lock the mode: it can still be switched
#     inside a session.
#   - /etc/codex/managed_config.toml: rendered at container start by
#     codex-dev-entry. Codex layers it over the user config, so its
#     developer_instructions (APPEND_SYSTEM.md plus, for multi-agent runs, a
#     "This run" section) reach every session without touching ~/.codex.
#   - APPEND_SYSTEM.md: the pristine base text of those instructions.
RUN install -d /etc/codex /usr/local/share/codex
COPY APPEND_SYSTEM.md /usr/local/share/codex/APPEND_SYSTEM.md
RUN chmod 0444 /usr/local/share/codex/APPEND_SYSTEM.md

# codex-dev-entry fronts every container command (TUI, ACP, --shell, --exec):
# it renders the managed config and execs its arguments. codex-acp-entry is
# the ACP stdio server behind `run-codex --acp`. Neither writes to stdout.
COPY codex/codex-dev-entry codex/codex-acp-entry /usr/local/bin/
COPY codex/codex-wrapper /usr/local/bin/codex
RUN chmod 0755 /usr/local/bin/codex-dev-entry /usr/local/bin/codex-acp-entry \
              /usr/local/bin/codex \
    && test "$(command -v codex)" = /usr/local/bin/codex \
    && codex --version

WORKDIR /work
ENTRYPOINT ["codex-dev-entry"]
CMD ["codex"]

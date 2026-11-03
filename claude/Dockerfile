# syntax=docker/dockerfile:1
#
# Claude Code layer on top of the shared base (see ../shared/Dockerfile).
# Ships two npm packages: @anthropic-ai/claude-code (the `claude` CLI/TUI) and
# @agentclientprotocol/claude-agent-acp (the Agent Client Protocol adapter,
# `claude-agent-acp`, which drives the same CLI through the Claude Agent SDK
# and speaks ACP over stdio to clients such as Emacs agent-shell/acp.el).
# bins/run-claude is the launcher: the default mode runs the TUI, --acp hands
# stdio to the adapter.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f claude/Dockerfile -t dev-claude .
#
# Or use the Makefile:  make claude
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Pins are bumped together: the adapter depends on an exact
# @anthropic-ai/claude-agent-sdk, which is built against one Claude Code
# release (adapter 0.89.0 -> SDK 0.3.293 -> Claude Code 2.1.293). Keeping the
# CLI on that release lets CLAUDE_CODE_EXECUTABLE below point the adapter at
# the image's own `claude`, so the TUI and ACP run one binary.
ARG CLAUDE_CODE_VERSION=2.1.293
ARG CLAUDE_ACP_VERSION=0.89.0

# --allow-scripts: the shared base ships npm 12, which blocks install scripts
# not covered by an allowScripts policy. claude-code's postinstall links the
# platform-native binary into bin/claude.exe; without it `claude` only prints
# "native binary not installed".
# The adapter's SDK carries its own copy of that native binary (~240 MB) as
# an optional platform dependency. CLAUDE_CODE_EXECUTABLE makes it unused, so
# drop it; the build check below proves the adapter still loads.
RUN npm install -g --no-fund --no-audit --loglevel=warn --progress=false \
        --allow-scripts=@anthropic-ai/claude-code \
        "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
        "@agentclientprotocol/claude-agent-acp@${CLAUDE_ACP_VERSION}" \
    && rm -rf /usr/lib/node_modules/@agentclientprotocol/claude-agent-acp/node_modules/@anthropic-ai/claude-agent-sdk-linux-* \
    && npm cache clean --force \
    && test "$(command -v claude)" = /usr/bin/claude \
    && claude --version \
    && claude-agent-acp --version

# CLAUDE_CONFIG_DIR: spelled out so the bins/run-claude mount target is
# visible in the image. Setting it also moves .claude.json (onboarding state,
# per-project trust/MCP entries) inside the config dir, so the container never
# needs the host's ~/.claude.json; it lands in ~/.claude/.claude.json instead.
# CLAUDE_CODE_EXECUTABLE: the adapter spawns this CLI instead of the SDK's
# bundled copy (removed above).
# IS_SANDBOX: the container runs as root, and both the CLI and the adapter
# refuse bypassPermissions for root unless this is set; the container is the
# isolation boundary (see claude/managed-settings.template.json).
# DISABLE_AUTOUPDATER: the image is the version pin; bump the ARGs instead.
ENV CLAUDE_CONFIG_DIR=/root/.claude \
    CLAUDE_CODE_EXECUTABLE=/usr/bin/claude \
    IS_SANDBOX=1 \
    DISABLE_AUTOUPDATER=1

# Image-local container policy, the Claude Code equivalent of
# dsh/dev-container.mjs: Claude Code loads managed memory and managed settings
# from /etc/claude-code on Linux for every session (TUI and ACP) on top of the
# user/project settings, so the policy never has to be written into the
# host-shared ~/.claude.
#   - CLAUDE.md: the runtime facts pi gets from APPEND_SYSTEM.md (root,
#     disposable OS, CARGO_TARGET_DIR=/target, read-only .git, commit on host).
#   - managed-settings.json: rendered at container start by claude-dev-entry
#     from the template (permission mode from CLAUDE_PERMISSION_MODE, git
#     write deny rules).
#   - CLAUDE.base.md: a pristine copy of CLAUDE.md. For multi-agent runs
#     (AGENT_ROLE / AGENT_MOUNT_MODE / AGENT_OUTBOX set) claude-dev-entry
#     rewrites CLAUDE.md as this base plus a "This run" section; otherwise
#     CLAUDE.md is used exactly as copied here.
RUN install -d /etc/claude-code /usr/local/share/claude
COPY APPEND_SYSTEM.md /etc/claude-code/CLAUDE.md
COPY APPEND_SYSTEM.md /usr/local/share/claude/CLAUDE.base.md
COPY claude/managed-settings.template.json /usr/local/share/claude/
RUN chmod 0444 /etc/claude-code/CLAUDE.md \
              /usr/local/share/claude/CLAUDE.base.md \
              /usr/local/share/claude/managed-settings.template.json

# claude-dev-entry fronts every container command (TUI, ACP, --shell, --exec):
# it renders the managed settings and execs its arguments. claude-acp-entry is
# the ACP stdio server behind `run-claude --acp`. Neither writes to stdout.
COPY claude/claude-dev-entry claude/claude-acp-entry /usr/local/bin/
RUN chmod 0755 /usr/local/bin/claude-dev-entry /usr/local/bin/claude-acp-entry

WORKDIR /work
ENTRYPOINT ["claude-dev-entry"]
CMD ["claude"]

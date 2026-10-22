# syntax=docker/dockerfile:1
#
# DeepSeek Harness (dsh) layer on top of the shared base (see
# ../shared/Dockerfile). dsh (https://github.com/deepseek-ai/deepseek-harness)
# ships as the @deepseek-ai/dsh npm package; its `web` profile serves the
# browser UI plus the RPC surface that clients such as dsh-emacs
# (https://github.com/vritser/dsh-emacs) connect to, and its `acp` profile
# serves the Agent Client Protocol over stdio for clients such as Emacs
# agent-shell/acp.el. bins/run-dsh is the launcher: web mode publishes the
# port on the host and attaches to the logs; ACP mode owns stdio directly.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f dsh/Dockerfile -t dev-dsh .
#
# Or use the Makefile:  make dsh
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Latest stable-track release (npm dist-tag `latest`, 2026-10-19). dsh is a
# developer preview that ships compatibility-breaking changes: bump this ARG
# to upgrade. dsh-emacs 0.5.x targets the dsh 0.1.7 wire protocol and supports
# servers 0.1.5 or newer, so this pin stays client-compatible. This pin also
# ships the ACP packages (@deepseek-ai/dsh-acp and dsh-acp-app) as regular
# dependencies of @deepseek-ai/dsh; the build check below verifies that the
# acp profile can boot.
ARG DSH_VERSION=0.2.1-rc.2

# The web profile auto-initializes on first use into $DSH_HOME/profiles/web
# (default ~/.dsh) and installs its plugin dependencies through pnpm, which
# the npm launch mode resolves from PATH (it has no bundled runtime; only the
# Desktop carrier bundles one). Install pnpm at build time so the very first
# `run-dsh` on a machine can initialize the profile; the major tracks the one
# upstream develops and tests with (pnpm@ in the repo's packageManager field).
# The ACP profile initializes the same way into $DSH_HOME/profiles/acp on the
# first `run-dsh --acp`; it has no external dependencies to install.
# Plain registry installs, so no npm-12 --allow-remote escape hatch is needed
# (unlike prime-agent/Dockerfile, whose deps are pinned to URL tarballs).
RUN npm install -g --no-fund --no-audit --loglevel=warn --progress=false pnpm@11 \
    && npm install -g --no-fund --no-audit --loglevel=warn --progress=false "@deepseek-ai/dsh@${DSH_VERSION}" \
    && npm cache clean --force \
    && dsh --version \
    && pnpm --version

# Fail the build early if the pinned dsh cannot boot the automation-only ACP
# profile. The ACP packages ship as dependencies of @deepseek-ai/dsh, so no
# separate plugin install is required; this check guards a future DSH_VERSION
# bump that drops them. It uses a throwaway DSH_HOME: the image's /root/.dsh
# is either unused (web mode) or shadowed by the host mount (run-dsh), so
# pre-initializing profiles there would not persist anyway. The acp profile is
# initialized at container runtime into the host-mounted $DSH_HOME instead;
# dsh/dsh-acp-entry does that with stdout redirected to /dev/null.
RUN DSH_HOME=/tmp/dsh-acp-check dsh --profile acp --help >/dev/null 2>&1 \
    && rm -rf /tmp/dsh-acp-check

# Foreground wrapper behind bins/run-dsh (image CMD, so `run-dsh --shell`
# can still replace it with plain bash): binds the web profile to this
# container's own address -- dsh rejects wildcard binds by design (binding
# every interface would expose the harness to the network), and podman's
# published port forwards to exactly this address -- and owns the
# attached-terminal contract: the launcher streams the logs, and Ctrl-D
# (stdin EOF) or Ctrl-C (SIGINT, dsh's own graceful shutdown) stops the
# harness and removes the container.
COPY dsh/dsh-web-entry /usr/local/bin/dsh-web-entry
RUN chmod 0755 /usr/local/bin/dsh-web-entry

# ACP entrypoint behind `run-dsh --acp`: the same container, patches and
# policy, but a foreground stdio server with no port, no URL banner, no TTY,
# and no stdin watcher. dsh-web-entry is not reused because it backgrounds
# `dsh web` and expects a URL; ACP owns stdin/stdout directly and exits on
# stdin EOF. See dsh/dsh-acp-entry and README.md.
COPY dsh/dsh-acp-entry /usr/local/bin/dsh-acp-entry
RUN chmod 0755 /usr/local/bin/dsh-acp-entry

# Project-local DSH data lives in <project>/.dsh (sessions, workspace
# registry, attachments); both entrypoints load that overlay only when
# bins/run-dsh exports DSH_PROJECT_HOME. The plugin registers the project
# folder as a session Workspace before the client (browser or ACP) connects,
# so DSH never falls back to its first-use Workspace under
# <Documents>/deepseek-harness.
#
# The searxng-search overlay is always loaded: it registers a native
# web_search provider backed by the host's SearXNG instance and selects it in
# the `web` service, so DSH's web_search tool works without an API key.
#
# The dev-container overlay is always loaded too: it adds the container's
# runtime context (root, disposable OS, CARGO_TARGET_DIR=/target, read-only
# .git) to the system prompt, states the unrestricted access policy, and
# installs the git-write guard. It is an image-local deployment policy, not
# project configuration; bins/run-dsh exports DSH_GIT_READ_ONLY when it
# applies the read-only .git mount and DSH_PERMISSION_MODE=danger-full-access
# so sessions start with the file sandbox and approvals off.
# The permission overlay maps DSH_PERMISSION_MODE to the default permission
# preset, so the launcher's value (danger-full-access unless overridden) wins
# over the shared profile's own defaultPreset for new sessions.
#
# The acp-commands overlay is ACP-only: it wraps @deepseek-ai/dsh-acp's
# transport so agent-shell sees the user-invocable skills and the host slash
# commands, and so `/compact`, `/goal`, etc. run in the host registry instead
# of being sent to the model. It is loaded by dsh-acp-entry, not the web entry.
RUN install -d /usr/local/share/dsh
COPY dsh/project-workspace.mjs dsh/project-workspace.patch.yml \
     dsh/searxng-search.mjs dsh/searxng-search.patch.yml \
     dsh/dev-container.mjs dsh/dev-container.patch.yml \
     dsh/permission.patch.yml \
     dsh/acp-commands.mjs dsh/acp-commands.patch.yml /usr/local/share/dsh/
RUN chmod 0444 /usr/local/share/dsh/project-workspace.mjs \
              /usr/local/share/dsh/project-workspace.patch.yml \
              /usr/local/share/dsh/searxng-search.mjs \
              /usr/local/share/dsh/searxng-search.patch.yml \
              /usr/local/share/dsh/dev-container.mjs \
              /usr/local/share/dsh/dev-container.patch.yml \
              /usr/local/share/dsh/permission.patch.yml \
              /usr/local/share/dsh/acp-commands.mjs \
              /usr/local/share/dsh/acp-commands.patch.yml

# Shared harness config lives under $DSH_HOME (default ~/.dsh, spelled out so
# the bins/run-dsh mount target is visible in the image): profiles, out-of-tree
# plugins, settings including provider API keys, and global instructions.
# bins/run-dsh bind-mounts the host's ~/.dsh here so configuration and the
# first-run profile initialization survive container teardown. Project data
# (sessions, Workspace registry, attachments) is redirected to <project>/.dsh
# by the overlay above; it never lands in this shared home.
ENV DSH_HOME=/root/.dsh

WORKDIR /work
CMD ["dsh-web-entry"]

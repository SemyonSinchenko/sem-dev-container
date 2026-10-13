# syntax=docker/dockerfile:1
#
# DeepSeek Harness (dsh) layer on top of the shared base (see
# ../shared/Dockerfile). dsh (https://github.com/deepseek-ai/deepseek-harness)
# ships as the @deepseek-ai/dsh npm package; its `web` profile serves the
# browser UI plus the RPC surface that clients such as dsh-emacs
# (https://github.com/vritser/dsh-emacs) connect to. bins/run-dsh is the
# launcher: it publishes the port on the host and attaches to the logs.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f dsh/Dockerfile -t dev-dsh .
#
# Or use the Makefile:  make dsh
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Latest stable-track release (npm dist-tag `latest`, 2026-10-13). dsh is a
# developer preview that ships compatibility-breaking changes: bump this ARG
# to upgrade. dsh-emacs 0.5.x targets the dsh 0.1.7 wire protocol and supports
# servers 0.1.5 or newer, so this pin stays client-compatible.
ARG DSH_VERSION=0.2.0-rc.2

# The web profile auto-initializes on first use into $DSH_HOME/profiles/web
# (default ~/.dsh) and installs its plugin dependencies through pnpm, which
# the npm launch mode resolves from PATH (it has no bundled runtime; only the
# Desktop carrier bundles one). Install pnpm at build time so the very first
# `run-dsh` on a machine can initialize the profile; the major tracks the one
# upstream develops and tests with (pnpm@ in the repo's packageManager field).
# Plain registry installs, so no npm-12 --allow-remote escape hatch is needed
# (unlike prime-agent/Dockerfile, whose deps are pinned to URL tarballs).
RUN npm install -g --no-fund --no-audit --loglevel=warn --progress=false pnpm@11 \
    && npm install -g --no-fund --no-audit --loglevel=warn --progress=false "@deepseek-ai/dsh@${DSH_VERSION}" \
    && npm cache clean --force \
    && dsh --version \
    && pnpm --version

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

# Harness state lives under $DSH_HOME (default ~/.dsh, spelled out so the
# bins/run-dsh mount target is visible in the image): profiles, out-of-tree
# plugins, settings including provider API keys, and sessions. bins/run-dsh
# bind-mounts the host's ~/.dsh here, so configuration and the first-run
# profile initialization survive container teardown.
ENV DSH_HOME=/root/.dsh

WORKDIR /work
CMD ["dsh-web-entry"]

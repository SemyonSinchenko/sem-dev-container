# syntax=docker/dockerfile:1
#
# prime-agent harness layer on top of the shared base (see ../shared/Dockerfile).
# Prime Agent (https://github.com/PrimeIntellect-ai/prime-agent) ships as an
# npm tarball from a release bucket; its postinstall bootstraps an IPython
# kernel venv with uv — both Node 22 and uv already come from the shared base.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f prime-agent/Dockerfile -t dev-prime-agent .
#
# Or use the Makefile:  make prime-agent
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Latest stable release (mirrors the official install.sh flow:
#   https://app.primeintellect.ai/prime-agent/install.sh
# resolves <base>/stable -> "v0.8.0" and installs
#   <base>/releases/v0.8.0/prime-agent-0.8.0.tgz  via `npm install -g`).
# Bump this ARG to upgrade.
ARG PRIME_AGENT_VERSION=0.8.0
ARG PRIME_AGENT_DOWNLOAD_BASE_URL=https://pub-728493de92a943e2a9b2d17b4719f318.r2.dev

# The kernel venv is created at build time by the package postinstall
# (ensureKernelPython -> `uv venv --python 3.11 --seed` + ipykernel /
# prime-agent-runtime / dill / default RLM extras). It defaults to
# $HOME/.prime/agent/kernel-venv, which would pin it to /root — under
# `--userns=keep-id` the agent would try to re-bootstrap into the runtime
# user's HOME instead. Point it at a fixed, world-writable path instead
# (chmod'd below) so every runtime UID reuses the prebuilt venv.
ENV PRIME_AGENT_KERNEL_VENV=/opt/prime-agent/kernel-venv

# The daemon supervisor registry defaults to ~/.prime/supervisor-owners, which
# prime-agent treats as "global per user, one machine" authority state for its
# daemons: owner records collide on the socketPath string (/tmp/prime-agent-0/
# daemon.sock looks identical in every container) and stale-detection compares
# pids, which are meaningless across containers' pid namespaces. The run-pa
# script bind-mounts the host ~/.prime at /root/.prime, so two containers
# started in different projects stole each other's registry entries and the
# daemon died with "supervisor generation ... no longer owns its registry
# entry; socket: /tmp/prime-agent-0/daemon.sock; registry:
# /root/.prime/supervisor-owners". Point the registry at a container-private
# path instead: a container lives exactly as long as its daemon, so ephemeral
# storage is the correct scope here (upstream only avoids $TMPDIR on the host
# because a host daemon can outlive macOS's 3-day /tmp cleanup). Setting the
# var also disables the legacy $TMPDIR/prime-agent-<uid>/supervisor-owners
# scan. Sessions/auth/config still live in the shared ~/.prime — only daemon
# ownership arbitration becomes per-container. Keep in sync with bins/run-pa.
ENV PRIME_AGENT_INTERNAL_DAEMON_SUPERVISOR_REGISTRY_DIR=/tmp/prime-agent-supervisor-owners

# PRIME_AGENT_BOOTSTRAP_TOOLS_ON_INSTALL=1  preloads fd/rg — both are already
# system-wide in the shared base (fdfind + ripgrep), so nothing is downloaded.
# PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=1 builds the kernel venv now, at
# build time, instead of on first `prime-agent` run. uv is found on PATH and
# its Python/caches live in the shared /opt/uv and /cache locations.
#
# --allow-remote=all: the shared base ships npm 12, where remote tarball URL
# dependencies default to "none" (supply-chain hardening, EALLOWREMOTE). The
# prime-agent tarball pins its deps (e.g. @earendil-works/pi-agent-core) to
# https://...r2.dev/... URLs, so npm refuses them without an explicit opt-in.
# Scoped to this single global install of a version-pinned tarball fetched
# from the pinned PRIME_AGENT_DOWNLOAD_BASE_URL above.
#
# --dangerously-allow-all-scripts: npm 12 blocks install scripts not
# covered by an allowScripts policy; --allow-scripts=prime-agent did not
# match the tarball's resolved identity, so the postinstall that builds
# the kernel venv was silently skipped (its warning hidden by
# loglevel=error) and /opt/prime-agent was never created. The escape
# hatch is acceptable here: a global install of a version-pinned tarball
# fetched from the pinned PRIME_AGENT_DOWNLOAD_BASE_URL above, inside a
# throwaway build layer. loglevel=warn keeps blocked-script warnings
# visible in build logs.
RUN curl -fsSL "${PRIME_AGENT_DOWNLOAD_BASE_URL}/releases/v${PRIME_AGENT_VERSION}/prime-agent-${PRIME_AGENT_VERSION}.tgz" \
        -o /tmp/prime-agent.tgz \
    && env PRIME_AGENT_BOOTSTRAP_TOOLS_ON_INSTALL=1 \
           PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=1 \
        npm install -g --no-fund --no-audit --loglevel=warn --progress=false \
            --allow-remote=all --dangerously-allow-all-scripts /tmp/prime-agent.tgz \
    && rm -f /tmp/prime-agent.tgz \
    && npm cache clean --force \
    && ( test -x /opt/prime-agent/kernel-venv/bin/python \
         || { echo 'kernel venv bootstrap failed' >&2; exit 1; } ) \
    && chmod -R a+rwX /opt/prime-agent

WORKDIR /work
CMD ["prime-agent"]

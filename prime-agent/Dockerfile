# syntax=docker/dockerfile:1
#
# prime-agent harness layer on top of the shared base (see ../shared/Dockerfile).
# Prime Agent (https://github.com/PrimeIntellect-ai/prime-agent) ships as an
# npm tarball from a release bucket. Since 0.10.0 that package is a small
# "Rust migration bridge" with NO postinstall: on the first `prime-agent` run
# it executes the bundled install-rust.sh, which downloads the real Rust
# payload from the same pinned bucket (SHA256SUMS-verified), installs it under
# $PRIME_AGENT_RUST_PREFIX, and pre-warms the Python kernel venv
# (`prime-agent --prime-agent-bootstrap`, via uv). Node 22 and uv already come
# from the shared base; the build below just runs that migration once at
# build time so the image ships payload + venv prebuilt.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f prime-agent/Dockerfile -t dev-prime-agent .
#
# Or use the Makefile:  make prime-agent
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# Latest stable release (mirrors the official install.sh flow:
#   https://app.primeintellect.ai/prime-agent/install.sh
# resolves <base>/stable -> "v0.10.0" and installs
#   <base>/releases/v0.10.0/prime-agent-0.10.0.tgz  via `npm install -g`).
# Bump this ARG to upgrade.
ARG PRIME_AGENT_VERSION=0.10.0
ARG PRIME_AGENT_DOWNLOAD_BASE_URL=https://pub-728493de92a943e2a9b2d17b4719f318.r2.dev

# The kernel venv (prime-agent-runtime sidecar + RLM extras; the Rust product
# no longer uses ipykernel) is created at build time by --prime-agent-bootstrap.
# It defaults to $HOME/.prime/agent/kernel-venv, which would pin it to /root —
# under `--userns=keep-id` the agent would try to re-bootstrap into the runtime
# user's HOME instead. Point it at a fixed, world-writable path instead
# (chmod'd below) so every runtime UID reuses the prebuilt venv. Its
# bin/python is a symlink into the shared base's uv-managed
# /opt/uv/python/cpython-3.11 tree. Honored by both the 0.9.x TS package and
# the 0.10.0 Rust binary.
ENV PRIME_AGENT_KERNEL_VENV=/opt/prime-agent/kernel-venv

# Where the migration bridge installs (and later finds) the Rust payload:
# $PRIME_AGENT_RUST_PREFIX/share/prime-agent/prime-agent, plus the per-version
# receipt that lets the npm launcher skip the migration and exec the payload
# directly on every start. Without this, the first runtime launch would
# re-run the migration into the invoking user's ~/.local — a network download
# outside the build. Same fixed, world-writable path as the venv so any
# runtime UID works.
ENV PRIME_AGENT_RUST_PREFIX=/opt/prime-agent

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

# npm install stops at the bridge: 0.10.0's package.json declares no
# postinstall, and the 0.9.x-era PRIME_AGENT_BOOTSTRAP_TOOLS_ON_INSTALL /
# PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL knobs no longer exist — installing
# alone leaves /opt/prime-agent empty. So run the bridge once: `prime-agent
# --version` triggers the migration, which downloads
# prime-agent-<v>-linux-x64.tar.gz from the pinned
# PRIME_AGENT_DOWNLOAD_BASE_URL (the ARG is exported to RUN, and
# install-rust.sh inherits it), verifies it, installs it under
# PRIME_AGENT_RUST_PREFIX, and pre-warms the kernel venv at
# PRIME_AGENT_KERNEL_VENV (uv from the shared base; fd/rg are already
# system-wide). A fresh build container has no daemons and no TypeScript
# install, so the installer's takeover steps are no-ops. The guards then pin
# the two artifacts the image must contain; chmod keeps everything usable by
# any runtime UID.
#
# --allow-remote=all: the shared base ships npm 12, where remote tarball URL
# dependencies default to "none" (supply-chain hardening, EALLOWREMOTE). The
# prime-agent tarball pins its deps (e.g. @earendil-works/pi-agent-core) to
# https://...r2.dev/... URLs, so npm refuses them without an explicit opt-in.
# Scoped to this single global install of a version-pinned tarball fetched
# from the pinned PRIME_AGENT_DOWNLOAD_BASE_URL above.
#
# --dangerously-allow-all-scripts: the top-level package has no install
# scripts anymore, but its dependencies do (koffi's native prebuild setup, at
# minimum), and npm 12 blocks scripts not covered by an allowScripts policy.
# Same escape hatch as before: a global install of a version-pinned tarball
# fetched from the pinned PRIME_AGENT_DOWNLOAD_BASE_URL above, inside a
# throwaway build layer. loglevel=warn keeps blocked-script warnings visible.
RUN curl -fsSL "${PRIME_AGENT_DOWNLOAD_BASE_URL}/releases/v${PRIME_AGENT_VERSION}/prime-agent-${PRIME_AGENT_VERSION}.tgz" \
        -o /tmp/prime-agent.tgz \
    && npm install -g --no-fund --no-audit --loglevel=warn --progress=false \
            --allow-remote=all --dangerously-allow-all-scripts /tmp/prime-agent.tgz \
    && rm -f /tmp/prime-agent.tgz \
    && npm cache clean --force \
    && prime-agent --version \
    && test -x /opt/prime-agent/share/prime-agent/prime-agent \
    && test -x /opt/prime-agent/kernel-venv/bin/python \
    && chmod -R a+rwX /opt/prime-agent

WORKDIR /work
CMD ["prime-agent"]

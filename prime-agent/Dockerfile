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
# resolves <base>/stable -> "v0.7.1" and installs
#   <base>/releases/v0.7.1/prime-agent-0.7.1.tgz  via `npm install -g`).
# Bump this ARG to upgrade.
ARG PRIME_AGENT_VERSION=0.7.1
ARG PRIME_AGENT_DOWNLOAD_BASE_URL=https://pub-728493de92a943e2a9b2d17b4719f318.r2.dev

# The kernel venv is created at build time by the package postinstall
# (ensureKernelPython -> `uv venv --python 3.11 --seed` + ipykernel /
# prime-agent-runtime / dill / default RLM extras). It defaults to
# $HOME/.prime/agent/kernel-venv, which would pin it to /root — under
# `--userns=keep-id` the agent would try to re-bootstrap into the runtime
# user's HOME instead. Point it at a fixed, world-writable path instead
# (chmod'd below) so every runtime UID reuses the prebuilt venv.
ENV PRIME_AGENT_KERNEL_VENV=/opt/prime-agent/kernel-venv

# PRIME_AGENT_BOOTSTRAP_TOOLS_ON_INSTALL=1  preloads fd/rg — both are already
# system-wide in the shared base (fdfind + ripgrep), so nothing is downloaded.
# PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=1 builds the kernel venv now, at
# build time, instead of on first `prime-agent` run. uv is found on PATH and
# its Python/caches live in the shared /opt/uv and /cache locations.
RUN curl -fsSL "${PRIME_AGENT_DOWNLOAD_BASE_URL}/releases/v${PRIME_AGENT_VERSION}/prime-agent-${PRIME_AGENT_VERSION}.tgz" \
        -o /tmp/prime-agent.tgz \
    && env PRIME_AGENT_BOOTSTRAP_TOOLS_ON_INSTALL=1 \
           PRIME_AGENT_BOOTSTRAP_KERNEL_ON_INSTALL=1 \
        npm install -g --no-fund --no-audit --loglevel=error --progress=false /tmp/prime-agent.tgz \
    && rm -f /tmp/prime-agent.tgz \
    && npm cache clean --force \
    && chmod -R a+rwX /opt/prime-agent

WORKDIR /work
CMD ["prime-agent"]

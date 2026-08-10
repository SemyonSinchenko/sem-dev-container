# syntax=docker/dockerfile:1
#
# pi harness layer on top of the shared base (see ../shared/Dockerfile).
# Only adds what is specific to the `pi` coding agent: the agent itself plus
# the OpenSpec CLI. Everything heavy (compilers, runtimes, tools) lives in
# the shared base.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f pi/Dockerfile -t dev-pi .
#
# Or use the Makefile:  make pi
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# ---------------------------------------------------------------------------
# The pi coding agent (https://pi.dev) + the OpenSpec CLI
# (https://github.com/Fission-AI/OpenSpec/). Both ship as npm packages, so
# they share one global install (OpenSpec needs Node >= 20.19; Node 22 comes
# from the shared base). Provides the `pi` and `openspec` binaries.
# ---------------------------------------------------------------------------
RUN npm install -g @earendil-works/pi-coding-agent @fission-ai/openspec@latest \
    && npm cache clean --force

WORKDIR /work
CMD ["pi"]

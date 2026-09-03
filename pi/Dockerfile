# syntax=docker/dockerfile:1
#
# pi harness layer on top of the shared base (see ../shared/Dockerfile).
# Only adds what is specific to the `pi` coding agent. Everything heavy
# (compilers, runtimes, tools, the openspec CLI) lives in the shared base.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#   podman build -f pi/Dockerfile -t dev-pi .
#
# Or use the Makefile:  make pi
ARG BASE_IMAGE=dev-shared
FROM ${BASE_IMAGE}

# ---------------------------------------------------------------------------
# The pi coding agent (https://pi.dev). It ships as an npm package; Node 22
# comes from the shared base. Provides the `pi` binary. (The OpenSpec CLI
# used to be installed here too, but it moved down into the shared base —
# step 10 — so prime-agent gets it as well and this layer stays thin.)
# ---------------------------------------------------------------------------
ARG PI_VERSION=0.84.4
RUN npm install -g @earendil-works/pi-coding-agent@${PI_VERSION} \
    && npm cache clean --force

WORKDIR /work
CMD ["pi"]

# Build the layered dev-container images.
#
# Usage:
#   make shared        # build the heavy shared base only
#   make pi            # shared + pi harness (default engine: podman)
#   make prime-agent   # shared + prime-agent harness
#   make all           # both harnesses
#
# Override the engine or tags freely:
#   make ENGINE=docker pi
#   make TAG_SHARED=myreg/dev-shared:1.0 TAG_PI=myreg/dev-pi:1.0 all
#
# All builds use the repo root as context (the shared layer COPYs
# .m2/settings.xml), so run make from the repo root.

ENGINE       ?= podman
TAG_SHARED   ?= dev-shared
TAG_PI       ?= dev-pi
TAG_PRIME    ?= dev-prime-agent
CONTEXT      := .

.PHONY: all shared pi prime-agent

all: pi prime-agent

shared:
	$(ENGINE) build -f shared/Dockerfile -t $(TAG_SHARED) $(CONTEXT)

pi: shared
	$(ENGINE) build -f pi/Dockerfile -t $(TAG_PI) \
		--build-arg BASE_IMAGE=$(TAG_SHARED) $(CONTEXT)

prime-agent: shared
	$(ENGINE) build -f prime-agent/Dockerfile -t $(TAG_PRIME) \
		--build-arg BASE_IMAGE=$(TAG_SHARED) $(CONTEXT)

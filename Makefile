# Build the layered dev-container images.
#
# Usage:
#   make shared        # build the heavy shared base only
#   make pi            # shared + pi harness (default engine: podman)
#   make prime-agent   # shared + prime-agent harness
#   make dsh           # shared + DeepSeek Harness (dsh web + acp profiles)
#   make test-dsh      # dev-container plugin unit tests (Node 22+)
#   make test-run-dsh  # run-dsh web/ACP dry-run contract tests (Python 3.10+)
#   make all           # all three harnesses
#
# Override the engine or tags freely:
#   make ENGINE=docker pi
#   make TAG_SHARED=myreg/dev-shared:1.0 TAG_PI=myreg/dev-pi:1.0 all
#
# All builds use the repo root as context (the shared layer COPYs
# .m2/settings.xml, the dsh layer COPYs dsh/dsh-web-entry and
# dsh/dsh-acp-entry), so run make from the repo root.

ENGINE       ?= podman
TAG_SHARED   ?= dev-shared
TAG_PI       ?= dev-pi
TAG_PRIME    ?= dev-prime-agent
TAG_DSH      ?= dev-dsh
CONTEXT      := .

.PHONY: all shared pi prime-agent dsh test-dsh test-run-dsh

all: pi prime-agent dsh

shared:
	$(ENGINE) build -f shared/Dockerfile -t $(TAG_SHARED) $(CONTEXT)

pi: shared
	$(ENGINE) build -f pi/Dockerfile -t $(TAG_PI) \
		--build-arg BASE_IMAGE=$(TAG_SHARED) $(CONTEXT)

prime-agent: shared
	$(ENGINE) build -f prime-agent/Dockerfile -t $(TAG_PRIME) \
		--build-arg BASE_IMAGE=$(TAG_SHARED) $(CONTEXT)

dsh: shared
	$(ENGINE) build -f dsh/Dockerfile -t $(TAG_DSH) \
		--build-arg BASE_IMAGE=$(TAG_SHARED) $(CONTEXT)

test-dsh:
	node --test dsh/dev-container.test.mjs dsh/acp-commands.test.mjs

test-run-dsh:
	python3 -m unittest discover -s tests -p 'test_*.py'

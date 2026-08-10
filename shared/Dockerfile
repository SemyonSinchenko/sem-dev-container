# syntax=docker/dockerfile:1
#
# Shared "heavy" base for all agentic dev containers in this repo.
# Everything here is harness-agnostic: system packages, compilers, runtimes,
# package managers, and generic CLIs. The per-harness layers (pi/,
# prime-agent/) build on top of this and only add their own agent + CLIs.
#
#   podman build -f shared/Dockerfile -t dev-shared .
#
# Base is Ubuntu 24.04 (noble). If you want to share a compiled cache with your
# host (see the target/ discussion), match this to your host distro/glibc
# instead.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

# ---------------------------------------------------------------------------
# 1. System packages: gcc/g++/make (build-essential) + everything commonly
#    needed to actually build native code, plus protoc.
# ---------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt \
    apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        make \
        pkg-config \
        cmake \
        protobuf-compiler \
        ca-certificates \
        curl \
        wget \
        git \
        openssh-client \
        tzdata \
        gnupg \
        unzip \
        zip \
        xz-utils \
        locales \
        mg \
        # --- headers commonly needed to build Python C-extension wheels ---
        libssl-dev \
        libffi-dev \
        zlib1g-dev \
        libsqlite3-dev \
        libbz2-dev \
        liblzma-dev \
        libreadline-dev \
        libncurses-dev \
        libyaml-dev \
        libxml2-dev \
        libxslt1-dev \
        libjpeg-dev \
        libfreetype-dev \
        libpq-dev \
        # --- gmp/mpfr dev headers: needed to build GDB (step 1b) and any other
        #     native code that links against the GNU multiprecision libs.
        libgmp-dev \
        libmpfr-dev \
        # --- search tools agents shell out to (find/grep). Ship them
        #     system-wide so agents reuse them instead of downloading fd/rg on
        #     every run. `fd-find` installs the binary as `fdfind`, so also
        #     provide the conventional `fd` symlink (pi and prime-agent both
        #     check `cmd --version` on PATH and accept either name).
        #     No version requirement: agents only probe `cmd --version`.
        # ---
        ripgrep \
        fd-find \
    && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 1b. GDB built from source (uses build-essential + the gmp/mpfr dev headers
#     from step 1). The GNU release tarball ships pre-generated .info pages,
#     so we stub MAKEINFO=true to avoid pulling in texinfo purely for docs.
#     Out-of-tree build (recommended by the GDB manual). Installed to
#     /usr/local, which is already on PATH, so `gdb` is immediately usable.
#     Build artifacts are removed to keep the layer small.
# ---------------------------------------------------------------------------
ARG GDB_VERSION=17.2
RUN cd /tmp \
    && curl -fsSL "https://ftp.gnu.org/gnu/gdb/gdb-${GDB_VERSION}.tar.gz" -o gdb.tar.gz \
    && tar xzf gdb.tar.gz \
    && mkdir /tmp/gdb-build && cd /tmp/gdb-build \
    && ../gdb-${GDB_VERSION}/configure --prefix=/usr/local --disable-nls \
    && make -j"$(nproc)" MAKEINFO=true \
    && make install MAKEINFO=true \
    && cd / && rm -rf /tmp/gdb*

# ---------------------------------------------------------------------------
# 2. Java 8 / 11 / 17 / 21 (Eclipse Temurin from Adoptium) + a `use-java` helper.
#    Default is 21; switch at runtime with:  use-java 17
# ---------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt \
    install -d /etc/apt/keyrings \
    && wget -qO- https://packages.adoptium.net/artifactory/api/gpg/key/public \
        | gpg --dearmor > /etc/apt/keyrings/adoptium.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb $(. /etc/os-release; echo $VERSION_CODENAME) main" \
        > /etc/apt/sources.list.d/adoptium.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        temurin-8-jdk \
        temurin-11-jdk \
        temurin-17-jdk \
        temurin-21-jdk \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sfn "$(dirname "$(dirname "$(readlink -f /usr/bin/javac)")")" /usr/lib/jvm/current

# Helper to flip the active JDK: `use-java 8|11|17|21`
RUN cat > /usr/local/bin/use-java <<'EOF' && chmod +x /usr/local/bin/use-java
#!/bin/sh
set -e
v="$1"
d=$(ls -d /usr/lib/jvm/temurin-${v}-jdk-* 2>/dev/null | head -n1)
[ -d "$d" ] || { echo "No JDK $v installed" >&2; exit 1; }
update-alternatives --set java  "$d/bin/java"  >/dev/null 2>&1 || true
update-alternatives --set javac "$d/bin/javac" >/dev/null 2>&1 || true
ln -sfn "$d" /usr/lib/jvm/current
echo "active JDK -> $d  (JAVA_HOME=/usr/lib/jvm/current)"
EOF

ENV JAVA_HOME=/usr/lib/jvm/current
ENV PATH=$JAVA_HOME/bin:$PATH

# ---------------------------------------------------------------------------
# 3. sbt (JVM tarball, arch-independent).
# ---------------------------------------------------------------------------
ARG SBT_VERSION=1.11.7
RUN curl -fsSL "https://github.com/sbt/sbt/releases/download/v${SBT_VERSION}/sbt-${SBT_VERSION}.tgz" \
        | tar xz -C /opt \
    && ln -sf /opt/sbt/bin/sbt /usr/local/bin/sbt

# ---------------------------------------------------------------------------
# 3b. Maven (Apache binary tarball, arch-independent JVM tool).
#     MAVEN_HOME points to the *install* (correct usage); the local repo cache
#     is redirected to /cache/maven via the global settings.xml (see COPY below).
# ---------------------------------------------------------------------------
ARG MAVEN_VERSION=3.9.9
RUN curl -fsSL "https://archive.apache.org/dist/maven/maven-3/${MAVEN_VERSION}/binaries/apache-maven-${MAVEN_VERSION}-bin.tar.gz" \
        | tar xz -C /opt \
    && ln -sfn /opt/apache-maven-${MAVEN_VERSION} /opt/maven \
    && ln -sf /opt/maven/bin/mvn /usr/local/bin/mvn
ENV MAVEN_HOME=/opt/maven
# Redirect the local repo to /cache/maven via the *global* settings.xml
# (HOME-independent, so it applies under any UID incl. keep-id). This
# replaces the old MAVEN_OPTS="-Dmaven.repo.local=..." hack, which
# clobbered any user-supplied JVM/memory flags put into MAVEN_OPTS.
COPY .m2/settings.xml /opt/maven/conf/settings.xml

# ---------------------------------------------------------------------------
# 4. Node.js 22 (needed by every agent harness: pi and prime-agent both ship
#    as npm packages) + buf CLI.
#    buf is pulled as a static release binary (matches host arch via uname).
# ---------------------------------------------------------------------------
RUN --mount=type=cache,target=/var/cache/apt \
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

ARG BUF_VERSION=1.71.0
RUN curl -fsSL "https://github.com/bufbuild/buf/releases/download/v${BUF_VERSION}/buf-$(uname -s)-$(uname -m)" \
        -o /usr/local/bin/buf \
    && chmod +x /usr/local/bin/buf

# ---------------------------------------------------------------------------
# 5. uv (installs system-wide) + poetry + pre-commit as uv tools + Python 3.10-3.13.
#    UV_TOOL_BIN_DIR=/usr/local/bin puts `poetry`/`pre-commit` straight on PATH.
#    Also used by the prime-agent layer to build its IPython kernel venv.
# ---------------------------------------------------------------------------
ENV UV_INSTALL_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv/python \
    UV_TOOL_DIR=/opt/uv/tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    INSTALLER_NO_MODIFY_PATH=1

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ARG PYTHON_DEFAULT=3.13
# Interpreters 3.10-3.13, exposed on PATH as python3.10 ... python3.13.
# A generic `python3`/`python` -> PYTHON_DEFAULT is provided so build scripts,
# pre-commit hooks, etc. that assume `python3` exists actually find one.
RUN uv python install 3.10 3.11 3.12 3.13 \
    && for v in 3.10 3.11 3.12 3.13; do \
         ln -sf "$(uv python find $v)" /usr/local/bin/python$v; \
       done \
    && ln -sf /usr/local/bin/python${PYTHON_DEFAULT} /usr/local/bin/python3 \
    && ln -sf /usr/local/bin/python${PYTHON_DEFAULT} /usr/local/bin/python

# poetry + pre-commit (both requested)
RUN uv tool install poetry \
    && uv tool install pre-commit

# ---------------------------------------------------------------------------
# 6. zig, via `uv tool install ziglang`. The ziglang package installs its
#    binary as `python-zig` (renamed to avoid clashing with a system zig), so
#    we symlink `zig` -> `python-zig`. cargo-zigbuild then finds `zig` on PATH.
# ---------------------------------------------------------------------------
RUN uv tool install ziglang \
    && ln -sf "$UV_TOOL_BIN_DIR/python-zig" /usr/local/bin/zig

# ---------------------------------------------------------------------------
# 7. Rust stable toolchain + cargo-zigbuild. Installed system-wide so any UID
#    (e.g. via `podman run --userns=keep-id`) can use it.
# ---------------------------------------------------------------------------
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN --mount=type=cache,target=/usr/local/cargo/registry \
    --mount=type=cache,target=/usr/local/cargo/git \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
        | sh -s -- -y --no-modify-path --default-toolchain stable --profile default \
    && rustup component add rust-analyzer clippy rustfmt \
    && cargo install cargo-zigbuild \
    # add a couple of common cross targets zigbuild is handy for (optional):
    # && rustup target add x86_64-unknown-linux-musl aarch64-unknown-linux-musl \
    && true

# Make the toolchain dirs writable by any runtime user so `cargo install` /
# `uv tool install` still work when you run as a non-root UID.
RUN chmod -R a+rwX /usr/local/cargo /usr/local/rustup /opt/uv || true

# ---------------------------------------------------------------------------
# 7b. Shared cache locations under /cache. Each tool's *cache* (not its
#     install) is redirected here via its own env var, so at runtime you just
#     bind-mount host dirs onto /cache/* — no per-run -e flags needed.
#     NOTE: cargo is intentionally NOT repointed here — its install lives in
#     /usr/local/cargo (with cargo-zigbuild). Share only the registry subdir
#     at runtime: -v ~/.cargo/registry:/usr/local/cargo/registry
# ---------------------------------------------------------------------------
ENV COURSIER_CACHE=/cache/coursier \
    UV_CACHE_DIR=/cache/uv \
    PIP_CACHE_DIR=/cache/pip \
    POETRY_CACHE_DIR=/cache/poetry \
    SBT_OPTS="-Dsbt.ivy.home=/cache/ivy2"
RUN mkdir -p /cache/coursier /cache/uv /cache/pip /cache/poetry /cache/maven /cache/ivy2 \
    && chmod -R a+rwX /cache

# ---------------------------------------------------------------------------
# 8. hardwood-cli (https://github.com/hardwood-hq/hardwood) — a native GraalVM
#    Parquet CLI, fetched from GitHub Releases (linux x86_64 only). The tarball
#    keeps the binary in bin/ alongside its JNI compression libs (liblz4 /
#    snappy / zstd) in lib/, which the binary resolves relative to itself, so
#    we preserve that layout under /opt and put bin/ on PATH (a symlink into
#    /usr/local/bin would break the lib/ lookup).
# ---------------------------------------------------------------------------
ARG HARDWOOD_VERSION=1.0.0.Final
RUN curl -fsSL "https://github.com/hardwood-hq/hardwood/releases/download/v${HARDWOOD_VERSION}/hardwood-cli-${HARDWOOD_VERSION}-linux-x86_64.tar.gz" \
        | tar xz -C /opt \
    && ln -sfn "/opt/hardwood-cli-${HARDWOOD_VERSION}-linux-x86_64" /opt/hardwood-cli
ENV PATH=/opt/hardwood-cli/bin:$PATH

# ---------------------------------------------------------------------------
# 9. Go toolchain + the open-spdd CLI (https://github.com/gszhangwei/open-spdd).
#     open-spdd ships as a Go module and is installed via `go install`. Go is
#     kept on the image (GOROOT under /opt/go, GOPATH under /opt/gopath) so the
#     `go` command and any future `go install`s work for every runtime UID
#     (dirs are world-writable, matching the cargo/uv pattern). The build and
#     module caches are redirected to /cache for host bind-mount sharing.
# ---------------------------------------------------------------------------
ARG GO_VERSION=1.26.5
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar xz -C /opt
ENV GOROOT=/opt/go \
    GOPATH=/opt/gopath \
    GOCACHE=/cache/go-build \
    GOMODCACHE=/cache/go-mod
ENV PATH=$GOROOT/bin:$GOPATH/bin:$PATH

RUN --mount=type=cache,target=/cache/go-build \
    --mount=type=cache,target=/cache/go-mod \
    mkdir -p /opt/gopath \
    && go install github.com/gszhangwei/open-spdd/cmd/openspdd@latest \
    && chmod -R a+rwX /cache /opt/gopath

# No WORKDIR/CMD here on purpose: each harness layer sets its own.

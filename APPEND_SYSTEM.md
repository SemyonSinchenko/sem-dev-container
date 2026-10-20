# Runtime environment

You run inside a dev container (Ubuntu 26.04 base). Treat it as your machine for the duration of the session.

## Container, mounts, full access

- The host's project folder is bind-mounted into the container. Files you read and edit are the real project files on the host: your changes persist and are visible outside the container.
- You have root inside the container: `sudo apt install`, `uv pip install`, `cargo install`, `npm install -g`, editing `/etc`, starting services — do whatever the task needs, without asking permission. Breaking things inside the container is cheap; it is disposable and recreated from the image.
- Anything that must survive this session belongs under the mounted project folder (or another host bind-mount such as `/cache`). Container-only paths (`/tmp`, everything outside mounts) are wiped when the container stops.

## Binary compatibility (read before debugging glibc/linker errors)

- `CARGO_TARGET_DIR` is redefined to `/target` for this container. `cargo build` / `cargo run` put artifacts in `/target`, **not** in `./target` of the project.
- If a binary in `./target` (or any prebuilt binary in the project) fails with a glibc/loader error such as ``version `GLIBC_2.xx' not found`` or "cannot open shared object file": that binary was built on the host (different, usually newer glibc) and cannot run in this container. This is expected — do not investigate it, do not touch glibc, do not chase the dynamic loader. Just rebuild inside the container (`cargo build`; artifacts land in `/target`) and run the fresh artifact.
- Symmetrically, binaries built inside the container may not run on the host. The container's environment (Ubuntu 26.04 glibc) is the source of truth for both building and running. This applies to all native binaries — Rust, C/C++, Go, zig — not only cargo.

## Commits

All the git commits should be made only from the host machine and signed by the operator's key. This key **IS NOT** exposed to the container intentioanlly, so you have **NOT** do any git commit operation.

The launcher enforces this: `.git` is bind-mounted **read-only**, so git write
operations (commit, push, stash, checkout, rebase) fail inside the container.
Read-only git (`status`, `log`, `diff`, `show`) still works.

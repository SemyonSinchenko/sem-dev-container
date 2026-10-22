// Unit tests for the dev-container plugin's pure policy logic and prompt text.
// Run with: node --test dsh/dev-container.test.mjs

import assert from "node:assert/strict";
import test from "node:test";

import {
  apply,
  classifyGitCommand,
  commandUsesProjectTarget,
  containerFacts,
  renderEnvironment,
  targetHintFor,
} from "./dev-container.mjs";

const gitCases = [
  // Read-only git must stay available.
  ["git status", false],
  ["git log --oneline", false],
  ["git diff --stat", false],
  ["git show HEAD", false],
  ["git blame src/lib.rs", false],
  ["git -C repo status", false],
  ["git -c core.pager=cat log", false],
  ["git --no-pager diff", false],
  ["git --version", false],
  ["git --help", false],
  ["git help commit", false],
  ["git commit --help", false],
  ["git status --help", false],
  // Common write attempts.
  ["git commit -m x", true],
  ["git commit", true],
  ["cd repo && git commit -m x", true],
  ["git -C repo commit", true],
  ["git -Crepo commit", true],
  ["git -c user.name=x commit", true],
  ["git --git-dir=/repo/.git commit", true],
  ["git push origin main", true],
  ["git push --dry-run", true],
  ["git fetch", true],
  ["git pull", true],
  ["git merge topic", true],
  ["git rebase main", true],
  ["git cherry-pick abc", true],
  ["git revert HEAD", true],
  ["git am patch.mbox", true],
  ["git add .", true],
  ["git rm file", true],
  ["git mv a b", true],
  ["git clean -fd", true],
  ["git reset --hard", true],
  ["git checkout -- .", true],
  ["git switch main", true],
  ["git restore .", true],
  ["git init", true],
  ["git gc", true],
  ["git prune", true],
  ["git repack", true],
  ["git update-ref refs/heads/x HEAD", true],
  ["git filter-branch --all", true],
  ["git fast-import", true],
  ["git commit 2>&1", true],
  ["echo hi; git commit", true],
  ["git commit &", true],
  ["git status & git commit", true],
  ["git status | grep x", false],
  ["git clone https://example.invalid/repo.git", false],
  // Prefixes and shells.
  ["FOO=1 git commit", true],
  ["sudo -u root git commit", true],
  ["sudo --user=root git commit", true],
  ["env -i git commit", true],
  ["command git commit", true],
  ["command -v git", false],
  ["command -V git", false],
  ["nice -n 10 git commit", true],
  ["nohup git commit", true],
  ["time -p git commit", true],
  ["xargs -0 git commit", true],
  ["bash -c 'git commit -m x'", true],
  ["sh -lc 'cd repo && git commit'", true],
  ["bash -c 'if true; then git commit; fi'", true],
  ["eval 'git commit'", true],
  ["bash -c 'echo \"git commit\"'", false],
  // Quoted text is data.
  ["echo 'git commit'", false],
  ["echo \"git commit\"", false],
  ["grep -r 'git commit' .", false],
  ["printf '%s' 'git commit'", false],
  ["cat <<EOF\ngit commit\nEOF", false],
  ["# git commit", false],
  // Mixed read/write subcommands.
  ["git config --get user.name", false],
  ["git config --list", false],
  ["git config --global --get-all user.name", false],
  ["git config user.name", false],
  ["git config user.name X", true],
  ["git config --global user.name X", false],
  ["git config --file /tmp/x user.name", false],
  ["git config --file /tmp/x user.name X", true],
  ["git config --global --unset user.name", false],
  ["git config --system user.name X", false],
  ["git config --worktree user.name X", true],
  ["git config --file .git/config user.name X", true],
  ["git branch", false],
  ["git branch --list", false],
  ["git branch -a", false],
  ["git branch --contains HEAD", false],
  ["git branch new", true],
  ["git branch -d old", true],
  ["git branch -D old", true],
  ["git branch -m old new", true],
  ["git branch --set-upstream-to=origin/main", true],
  ["git tag", false],
  ["git tag -l", false],
  ["git tag -n5 v1", false],
  ["git tag --verify v1", false],
  ["git tag v1", true],
  ["git tag -a v1 -m x", true],
  ["git tag -d v1", true],
  ["git remote", false],
  ["git remote -v", false],
  ["git remote show origin", false],
  ["git remote get-url origin", false],
  ["git remote add origin url", true],
  ["git remote prune origin", true],
  ["git stash", true],
  ["git stash list", false],
  ["git stash show -p", false],
  ["git stash pop", true],
  ["git worktree", false],
  ["git worktree list", false],
  ["git worktree add ../wt", true],
  ["git worktree prune", true],
  ["git submodule", false],
  ["git submodule status", false],
  ["git submodule summary", false],
  ["git submodule update --init", true],
  ["git reflog", false],
  ["git reflog show", false],
  ["git reflog expire --all", true],
  ["git reflog delete HEAD@{1}", true],
  ["git notes", false],
  ["git notes list", false],
  ["git notes show HEAD", false],
  ["git notes add -m x", true],
  ["git symbolic-ref HEAD", false],
  ["git symbolic-ref --short HEAD", false],
  ["git symbolic-ref HEAD refs/heads/x", true],
  ["git symbolic-ref -d HEAD", true],
  ["git lfs status", false],
  ["git lfs ls-files", false],
  ["git lfs push origin main", true],
  ["git lfs track '*.bin'", true],
  ["git hash-object file", false],
  ["git hash-object -w file", true],
  ["git apply --check patch.diff", false],
  ["git apply --stat patch.diff", false],
  ["git apply patch.diff", true],
  ["git bisect", false],
  ["git bisect log", false],
  ["git bisect view", false],
  ["git bisect start", true],
  ["git commit-graph", false],
  ["git commit-graph verify", false],
  ["git commit-graph write", true],
  ["git multi-pack-index", false],
  ["git multi-pack-index verify", false],
  ["git multi-pack-index write", true],
  ["git sparse-checkout", false],
  ["git sparse-checkout list", false],
  ["git sparse-checkout set src", true],
  ["git replace -l", false],
  ["git replace A B", true],
  ["git send-email --to x", false],
  ["git archive HEAD", false],
  ["git bundle create out.bundle --all", false],
  ["git format-patch HEAD~1", false],
  ["git fast-export --all", false],
  ["git maintenance run", true],
];

test("classifyGitCommand covers the read/write boundary", () => {
  for (const [command, expected] of gitCases) {
    assert.equal(classifyGitCommand(command), expected, command);
  }
});

test("classifyGitCommand is total on malformed input", () => {
  for (const command of ["", "   ", "git", "sudo", "env", "bash -c", "git -C", '"unterminated']) {
    assert.doesNotThrow(() => classifyGitCommand(command));
    assert.equal(classifyGitCommand(command), false, command);
  }
  assert.equal(classifyGitCommand(undefined), false);
});

test("containerFacts reads the launcher environment with safe fallbacks", () => {
  assert.deepEqual(
    containerFacts({
      DSH_PROJECT_DIR: "/work/proj",
      CARGO_TARGET_DIR: "/target",
      DSH_GIT_READ_ONLY: "1",
    }),
    { project: "/work/proj", target: "/target", gitReadOnly: true },
  );
  assert.deepEqual(containerFacts({}), {
    project: "the session workspace",
    target: "/target",
    gitReadOnly: false,
  });
});

test("renderEnvironment states the mounts, root access and target rule", () => {
  const text = renderEnvironment({
    project: "/work/proj",
    target: "/target",
    gitReadOnly: true,
  });
  assert.match(text, /\/work\/proj/);
  assert.match(text, /CARGO_TARGET_DIR/);
  assert.match(text, /\/target\/debug/);
  assert.match(text, /mounted read-only/);
  assert.match(text, /GPG/);
  assert.match(text, /sudo` is not installed/);
  assert.match(text, /no systemd/);
  assert.match(text, /danger-full-access/);
  assert.match(text, /no action needs prior permission/);
  assert.match(text, /isolation is the operator's responsibility/);
  assert.match(text, /Never ask the user whether to run a command/);
  assert.doesNotMatch(text, /\{\{/);
  assert.doesNotMatch(text, /\r/);

  const noGit = renderEnvironment({ project: "the session workspace", target: "/target", gitReadOnly: false });
  assert.match(noGit, /reserved for the operator/);
});

test("commandUsesProjectTarget distinguishes ./target from /target", () => {
  assert.equal(commandUsesProjectTarget("./target/debug/foo", "/work/proj"), true);
  assert.equal(commandUsesProjectTarget("target/release/foo", "/work/proj"), true);
  assert.equal(commandUsesProjectTarget("target/x86_64-unknown-linux-gnu/debug/foo", "/work/proj"), true);
  assert.equal(commandUsesProjectTarget("/work/proj/target/debug/foo", "/work/proj"), true);
  assert.equal(commandUsesProjectTarget("/target/debug/foo", "/work/proj"), false);
  assert.equal(commandUsesProjectTarget("cargo run", "/work/proj"), false);
});

test("targetHintFor only fires on loader/missing-binary output", () => {
  const loader = {
    content: [{ type: "text", text: "error while loading shared libraries: libc.so.6: version `GLIBC_2.38' not found" }],
  };
  assert.match(targetHintFor(loader, "/target"), /CARGO_TARGET_DIR/);
  assert.match(targetHintFor({ content: [{ type: "text", text: "foo: No such file or directory" }] }, "/t"), /`\/t`/);
  assert.equal(targetHintFor({ content: [{ type: "text", text: "compiled successfully" }] }, "/target"), undefined);
  assert.equal(targetHintFor({}, "/target"), undefined);
});

test("apply registers the context, guard and post-execute hint", async () => {
  const saved = { ...process.env };
  process.env.DSH_PROJECT_DIR = "/work/proj";
  process.env.CARGO_TARGET_DIR = "/target";
  process.env.DSH_GIT_READ_ONLY = "1";
  process.env.DSH_CONTAINER_GIT_GUARD = "1";
  try {
    const contexts = [];
    const guards = [];
    const handlers = new Map();
    const ctx = {
      systemPrompt: {
        getContextOrder: (name) => (name === "APPROVAL_POLICY" ? 115 : undefined),
        context: (value) => contexts.push(value),
      },
      tools: { guard: (value) => guards.push(value) },
      on: (event, handler) => handlers.set(event, handler),
    };

    apply(ctx);
    assert.equal(contexts.length, 1);
    assert.equal(contexts[0].name, "dev-container:environment");
    assert.equal(contexts[0].order, 116);
    assert.equal(contexts[0].required, true);
    assert.equal(contexts[0].interpolate, false);
    assert.match(contexts[0].text(), /\/work\/proj/);

    assert.equal(guards.length, 1);
    const guard = guards[0];
    assert.equal(guard({ name: "read", arguments: { path: "/work/proj/README.md" } }), undefined);
    assert.equal(guard({ name: "bash", arguments: { command: "git status" } }), undefined);
    assert.match(guard({ name: "bash", arguments: { command: "git commit -m x" } }), /Git writes are disabled/);
    assert.equal(guard({ name: "bash", arguments: {} }), undefined);

    const post = handlers.get("tools/post-execute");
    assert.equal(typeof post, "function");
    const result = {
      isError: true,
      content: [{ type: "text", text: "error while loading shared libraries: libc.so.6" }],
    };
    const decision = await post(
      { name: "bash", arguments: { command: "./target/debug/foo" } },
      result,
      async () => ({ kind: "accept", content: result.content }),
    );
    assert.equal(decision.kind, "accept");
    assert.equal(decision.content.length, 2);
    assert.match(decision.content[1].text, /\/target/);

    const untouched = await post(
      { name: "bash", arguments: { command: "cargo build" } },
      result,
      async () => ({ kind: "accept", content: result.content }),
    );
    assert.equal(untouched.content.length, 1);

    const blocked = await post(
      { name: "bash", arguments: { command: "./target/debug/foo" } },
      result,
      async () => ({ kind: "block", feedback: [{ type: "text", text: "no" }] }),
    );
    assert.equal(blocked.kind, "block");
  } finally {
    for (const key of ["DSH_PROJECT_DIR", "CARGO_TARGET_DIR", "DSH_GIT_READ_ONLY", "DSH_CONTAINER_GIT_GUARD"]) {
      if (saved[key] === undefined) delete process.env[key];
      else process.env[key] = saved[key];
    }
  }
});

test("DSH_CONTAINER_GIT_GUARD=0 keeps the context but skips the guard", () => {
  const saved = process.env.DSH_CONTAINER_GIT_GUARD;
  process.env.DSH_CONTAINER_GIT_GUARD = "0";
  try {
    const contexts = [];
    const guards = [];
    const ctx = {
      systemPrompt: {
        getContextOrder: () => 115,
        context: (value) => contexts.push(value),
      },
      tools: { guard: (value) => guards.push(value) },
      on: () => undefined,
    };
    apply(ctx);
    assert.equal(contexts.length, 1);
    assert.equal(guards.length, 0);
  } finally {
    if (saved === undefined) delete process.env.DSH_CONTAINER_GIT_GUARD;
    else process.env.DSH_CONTAINER_GIT_GUARD = saved;
  }
});

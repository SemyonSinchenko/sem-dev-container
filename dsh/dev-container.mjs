// dev-container — DSH deployment policy for the run-dsh container.
//
// Loaded by dsh-web-entry as an image-local row. It contributes two things:
//
//  1. A runtime-context snapshot (the same seam that carries DSH's sandbox and
//     approval policy) describing the container's mounts, root access,
//     CARGO_TARGET_DIR and read-only .git, plus the deployment's access
//     policy: act freely, never ask for permission. Values come from the
//     environment bins/run-dsh exports, so the prose cannot drift from the
//     launcher.
//
//  2. A monotonic tools.guard that turns the common "agent tries to commit"
//     case into a clear policy message instead of a raw read-only-filesystem
//     error, plus a narrow post-execute hint for the ./target vs /target
//     glibc trap.
//
// The guard is a UX layer, not a security boundary: the read-only .git mount
// enforces local git state (it does not necessarily stop a push; the guard
// covers that). Shell aliases, scripts and commands run below the tool layer
// can evade command parsing; do not present this as a sandbox.

export const name = "dev-container";
export const inject = ["systemPrompt", "tools"];

const GIT_DENIAL =
  "Git writes are disabled in this container: `.git` is mounted read-only " +
  "when the project has one, and commits are made by the operator on the " +
  "host, signed with their GPG key. Read-only git (`status`, `log`, `diff`, " +
  "`show`, `blame`) still works. Do not retry with another git write " +
  "command; when the change is ready, report it with `git diff` and a " +
  "proposed commit message instead.";

const TARGET_HINT =
  "Artifacts from this container's builds live in `{target}` " +
  "(CARGO_TARGET_DIR), not the project's `./target`. A GLIBC/loader error " +
  "from `./target` is a host-binary ABI mismatch, not a bug to debug: rebuild " +
  "here and run `{target}/debug/...` or `cargo run`.";

/** Tool names whose first argument is a shell command, across compositions. */
const SHELL_TOOLS = new Set(["bash", "bash_persistent"]);

/** Keywords that can syntactically precede a simple command. */
const SHELL_COMMAND_PREFIXES = new Set(["do", "elif", "else", "if", "then", "until", "while", "!"]);

/** Git subcommands that only write the worktree, index, refs or object store. */
const WRITE_SUBCOMMANDS = new Set([
  "add",
  "am",
  "apply",
  "checkout",
  "checkout-index",
  "cherry-pick",
  "clean",
  "commit",
  "commit-graph",
  "commit-tree",
  "fast-import",
  "fetch",
  "filter-branch",
  "gc",
  "hash-object",
  "index-pack",
  "init",
  "maintenance",
  "merge",
  "mktag",
  "mktree",
  "multi-pack-index",
  "mv",
  "pack-refs",
  "prune",
  "prune-packed",
  "pull",
  "push",
  "read-tree",
  "rebase",
  "repack",
  "replace",
  "reset",
  "restore",
  "revert",
  "rm",
  "sparse-checkout",
  "switch",
  "unpack-objects",
  "update-index",
  "update-ref",
  "write-tree",
]);

/** Git subcommands that are safe to run with a read-only .git. */
const READ_SUBCOMMANDS = new Set([
  "archive",
  "blame",
  "bundle",
  "cat-file",
  "check-attr",
  "check-ignore",
  "check-mailmap",
  "column",
  "count-objects",
  "describe",
  "diff",
  "diff-files",
  "diff-index",
  "diff-tree",
  "fast-export",
  "for-each-ref",
  "format-patch",
  "grep",
  "help",
  "log",
  "ls-files",
  "ls-tree",
  "merge-base",
  "name-rev",
  "patch-id",
  "request-pull",
  "rev-list",
  "rev-parse",
  "send-email",
  "shortlog",
  "show",
  "show-index",
  "show-ref",
  "status",
  "var",
  "verify-pack",
  "version",
  "whatchanged",
]);

const GIT_GLOBAL_VALUE_OPTIONS = new Set([
  "-C",
  "-c",
  "--config-env",
  "--exec-path",
  "--git-dir",
  "--namespace",
  "--super-prefix",
  "--work-tree",
]);

const SUDO_VALUE_OPTIONS = new Set([
  "-C",
  "-g",
  "-h",
  "-p",
  "-r",
  "-t",
  "-T",
  "-U",
  "-u",
  "--close-from",
  "--command-timeout",
  "--group",
  "--host",
  "--other-user",
  "--prompt",
  "--role",
  "--type",
  "--user",
]);

const ENV_VALUE_OPTIONS = new Set(["-S", "-u", "--split-string", "--unset"]);

const EXEC_VALUE_OPTIONS = new Set(["-a"]);
const NICE_VALUE_OPTIONS = new Set(["-n", "--adjustment"]);
const XARGS_VALUE_OPTIONS = new Set([
  "-a",
  "-d",
  "-E",
  "-I",
  "-L",
  "-n",
  "-P",
  "-s",
  "--arg-file",
  "--delimiter",
  "--eof",
  "--max-args",
  "--max-chars",
  "--max-lines",
  "--max-procs",
  "--process-slot-var",
  "--replace",
]);

/**
 * Container facts exported by bins/run-dsh.
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {{ project: string, target: string, gitReadOnly: boolean }}
 */
export function containerFacts(env = process.env) {
  const project = env.DSH_PROJECT_DIR?.trim();
  const target = env.CARGO_TARGET_DIR?.trim();
  return {
    project: project === undefined || project === "" ? "the session workspace" : project,
    target: target === undefined || target === "" ? "/target" : target,
    gitReadOnly: env.DSH_GIT_READ_ONLY === "1",
  };
}

/**
 * Render the always-on container context. Kept short: this is a runtime
 * snapshot, not a tutorial; details that belong to the task go in AGENTS.md.
 * @param {{ project: string, target: string, gitReadOnly: boolean }} facts
 * @returns {string}
 */
export function renderEnvironment(facts) {
  const git = facts.gitReadOnly
    ? "`.git` is mounted read-only. Read-only git (`status`, `log`, `diff`, `show`, `blame`) works. Do not commit, stage, push, stash, checkout, reset, rebase or otherwise write git state: commits are made by the operator on the host, signed with their GPG key. When a change is ready, report it with `git diff` and a proposed commit message instead."
    : "Git writes are reserved for the operator on the host. Do not commit, stage, push, stash, checkout, reset or rebase; report a `git diff` and a proposed commit message instead.";
  return [
    "Container environment: this dsh server runs inside the project's disposable dev container, as root.",
    "",
    "Access policy: this container is a disposable isolation boundary; isolation is the operator's responsibility, not yours. When DSH's file policy above is `danger-full-access` (the run-dsh default), the file sandbox is off and approval is `never`, so no action needs prior permission: run commands, install packages, edit any file including `/etc` and `/usr`, start services, and use the network as the task requires. Never ask the user whether to run a command or modify a file, and never stop short of a required step because it looks risky or privileged; `ask_user_question` is for the user's own decisions (product or design trade-offs), not for permission to act. Only actions the container physically blocks — such as writing the read-only `.git` described below — are off limits.",
    "",
    `The project directory (\`${facts.project}\`) is a host bind mount at the same absolute path: files you edit are real host files and persist. Treat the container OS as disposable: apt/pip/npm installs, \`/etc\`, \`/usr\`, \`/tmp\` and background services vanish when the container stops. The project, \`${facts.target}\`, \`~/.dsh\`, \`~/.agents\` and the mounted caches persist. \`sudo\` is not installed: run \`apt-get\`/\`apt\` directly. There is no systemd; start daemons directly. Container-level changes (package installs, \`/etc\` edits, services) are expected here; they only affect this disposable container.`,
    "",
    `Build artifacts: \`CARGO_TARGET_DIR\` is \`${facts.target}\` (a per-project named volume that survives container restarts). \`cargo build\`/\`cargo run\` write there; the project's \`./target\` is not the build output and may contain host-built binaries. A \`GLIBC_x.y not found\` or loader error from \`./target\` means a host binary from a different libc: do not investigate or modify glibc. Rebuild inside the container and run \`${facts.target}/debug/...\` or \`cargo run\`.`,
    "",
    "Native binaries built inside the container may likewise not run on the host; the container's libc is the reference for this session.",
    "",
    "Git: " + git,
    "",
    "Destructive edits under the project are real host-file changes, and the read-only `.git` means `git checkout` cannot undo them. Verify the resolved path before a recursive delete or move; when a deletion or move is what the task needs, do it.",
  ].join("\n");
}

/**
 * DSH prompt context registration (runtime facts, alongside sandbox policy)
 * plus the tool guard and the target/glibc reminder.
 * @param {import("@deepseek-ai/cordis").Context} ctx
 */
export function apply(ctx) {
  const facts = containerFacts();
  const approvalOrder = ctx.systemPrompt.getContextOrder("APPROVAL_POLICY");
  ctx.systemPrompt.context({
    name: "dev-container:environment",
    order: Number.isFinite(approvalOrder) ? approvalOrder + 1 : 116,
    required: true,
    interpolate: false,
    text: () => renderEnvironment(facts),
  });

  if (process.env.DSH_CONTAINER_GIT_GUARD !== "0") {
    ctx.tools.guard((exec) => {
      try {
        if (!SHELL_TOOLS.has(exec.name)) return undefined;
        const command = exec.arguments?.command;
        if (typeof command !== "string" || command.trim() === "") return undefined;
        return classifyGitCommand(command) ? GIT_DENIAL : undefined;
      } catch {
        // A guard must never break an unrelated tool call.
        return undefined;
      }
    });
  }

  ctx.on("tools/post-execute", async (exec, result, next) => {
    const decision = await next();
    try {
      if (decision.kind !== "accept" || Object.hasOwn(decision, "value")) return decision;
      if (!SHELL_TOOLS.has(exec.name) || !result.isError) return decision;
      const command = exec.arguments?.command;
      if (!commandUsesProjectTarget(command, facts.project)) return decision;
      const hint = targetHintFor(result, facts.target);
      if (hint === undefined) return decision;
      const content = decision.content ?? result.content;
      return {
        ...decision,
        content: [...content, { type: "text", text: hint }],
      };
    } catch {
      return decision;
    }
  });

  ctx.logger?.info?.(
    `dev-container: policy active (target ${facts.target}${facts.gitReadOnly ? ", .git read-only" : ""})`,
  );
}

/**
 * Whether a shell command contains a git invocation that would write state.
 * Best-effort: comments, dynamic eval, and commands below the tool layer can
 * hide a write; the read-only mount remains the enforcement.
 * @param {string} command
 * @returns {boolean}
 */
export function classifyGitCommand(command) {
  if (typeof command !== "string" || command.trim() === "") return false;
  return splitCommands(tokenizeCommand(command)).some((tokens) => commandWritesGit(tokens));
}

/**
 * Whether a command appears to execute a binary from the project-relative
 * `target/` tree (or the absolute host project path), as opposed to the
 * container's CARGO_TARGET_DIR.
 * @param {string} command
 * @param {string} project
 * @returns {boolean}
 */
export function commandUsesProjectTarget(command, project) {
  if (typeof command !== "string" || command === "") return false;
  // ./target/debug/x, target/release/x, target/<triple>/debug/x — but not /target/...
  if (/(^|[\s"'`=:;|&()])(?:\.\/)?target\/(?:[^/\s]+\/)?(?:debug|release)\//.test(command)) return true;
  if (typeof project !== "string" || project === "") return false;
  const root = project.replace(/\/+$/, "");
  return command.includes(`${root}/target/debug/`) || command.includes(`${root}/target/release/`);
}

/**
 * Model-facing hint appended when a `./target` binary fails on a loader or
 * missing-file error. Returns undefined when the output does not look like
 * the host/container ABI trap.
 * @param {{ isError?: boolean, content?: readonly object[] }} result
 * @param {string} target
 * @returns {string | undefined}
 */
export function targetHintFor(result, target = "/target") {
  const text = textOf(result?.content);
  if (
    !/GLIBC_|error while loading shared libraries|cannot open shared object file|version .* not found/i.test(
      text,
    ) &&
    !/No such file or directory|not found/i.test(text)
  ) {
    return undefined;
  }
  return TARGET_HINT.replaceAll("{target}", () => target);
}

// ---------------------------------------------------------------------------
// Small shell tokenizer/segmenter. It intentionally ignores shell semantics
// beyond quoting and the operators that start a new simple command; the goal
// is to recognize common `git ...` invocations, not to be a shell parser.
// ---------------------------------------------------------------------------

const SEPARATORS = new Set([";", "&", "|", "(", ")", "{", "}"]);

/**
 * @param {string} command
 * @returns {Array<{ text: string, quoted: boolean } | { op: string }>}
 */
function tokenizeCommand(command) {
  const tokens = [];
  let text = "";
  let quote = null;
  let escaped = false;
  let quoted = false;

  const flush = () => {
    if (text !== "" || quoted) tokens.push({ text, quoted });
    text = "";
    quoted = false;
  };

  for (let index = 0; index < command.length; index += 1) {
    const char = command[index];
    if (escaped) {
      text += char;
      escaped = false;
      continue;
    }
    if (quote !== null) {
      if (char === quote) {
        quote = null;
        quoted = true;
        continue;
      }
      if (char === "\\" && quote === '"') {
        escaped = true;
        continue;
      }
      text += char;
      continue;
    }
    if (char === "'" || char === '"') {
      quote = char;
      quoted = true;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (SEPARATORS.has(char)) {
      // `2>&1` is a redirection, not a command separator.
      if (char === "&" && text.endsWith(">")) {
        text += char;
        continue;
      }
      flush();
      if ((char === "&" || char === "|") && command[index + 1] === char) index += 1;
      tokens.push({ op: char });
      continue;
    }
    if (/\s/u.test(char)) {
      flush();
      continue;
    }
    text += char;
  }
  flush();
  return tokens;
}

/** @param {Array<{ text: string, quoted: boolean } | { op: string }>} tokens */
function splitCommands(tokens) {
  const commands = [];
  let current = [];
  for (const token of tokens) {
    if ("op" in token) {
      if (current.length > 0) commands.push(current);
      current = [];
    } else {
      current.push(token);
    }
  }
  if (current.length > 0) commands.push(current);
  return commands;
}

/**
 * @param {Array<{ text: string, quoted: boolean }>} tokens
 * @returns {boolean}
 */
function commandWritesGit(tokens) {
  let index = 0;
  while (index < tokens.length && isAssignment(tokens[index].text)) index += 1;
  if (index >= tokens.length) return false;

  for (;;) {
    if (index >= tokens.length) return false;
    const word = basename(tokens[index].text);
    if (SHELL_COMMAND_PREFIXES.has(word)) {
      index += 1;
      continue;
    }
    if (word === "sudo") {
      index = skipOptions(tokens, index + 1, SUDO_VALUE_OPTIONS);
      while (index < tokens.length && isAssignment(tokens[index].text)) index += 1;
      continue;
    }
    if (word === "env") {
      index = skipOptions(tokens, index + 1, ENV_VALUE_OPTIONS);
      while (index < tokens.length && isAssignment(tokens[index].text)) index += 1;
      continue;
    }
    if (word === "command") {
      const after = skipOptions(tokens, index + 1, new Set());
      if (tokens.slice(index + 1, after).some((token) => token.text === "-v" || token.text === "-V")) {
        return false;
      }
      index = after;
      continue;
    }
    if (word === "exec") {
      index = skipOptions(tokens, index + 1, EXEC_VALUE_OPTIONS);
      continue;
    }
    if (word === "nice") {
      index = skipOptions(tokens, index + 1, NICE_VALUE_OPTIONS);
      continue;
    }
    if (word === "nohup" || word === "time") {
      index = skipOptions(tokens, index + 1, new Set());
      continue;
    }
    if (word === "xargs") {
      index = skipOptions(tokens, index + 1, XARGS_VALUE_OPTIONS);
      continue;
    }
    if (word === "sh" || word === "bash" || word === "dash" || word === "zsh" || word === "ksh") {
      const script = shellScriptArgument(tokens, index + 1);
      return script === undefined ? false : classifyGitCommand(script);
    }
    if (word === "eval") {
      return classifyGitCommand(tokens.slice(index + 1).map((token) => token.text).join(" "));
    }
    break;
  }

  if (index >= tokens.length) return false;
  const command = basename(tokens[index].text);
  if (command !== "git" && command !== "git.exe") return false;
  return gitArgumentsWrite(tokens, index + 1);
}

/** @param {string} text */
function isAssignment(text) {
  return /^[A-Za-z_][A-Za-z0-9_]*=/u.test(text);
}

/** @param {string} text */
function basename(text) {
  const slash = text.lastIndexOf("/");
  return slash < 0 ? text : text.slice(slash + 1);
}

/**
 * @param {Array<{ text: string, quoted: boolean }>} tokens
 * @param {number} index
 * @param {Set<string>} valueOptions
 * @returns {number}
 */
function skipOptions(tokens, index, valueOptions) {
  while (index < tokens.length) {
    const text = tokens[index].text;
    if (text === "--") return index + 1;
    if (!text.startsWith("-") || text === "-") return index;
    const name = text.includes("=") ? text.slice(0, text.indexOf("=")) : text;
    index += 1;
    if (valueOptions.has(name) && !text.includes("=")) index += 1;
  }
  return index;
}

/**
 * @param {Array<{ text: string, quoted: boolean }>} tokens
 * @param {number} index
 * @returns {string | undefined}
 */
function shellScriptArgument(tokens, index) {
  for (; index < tokens.length; index += 1) {
    const text = tokens[index].text;
    if (text === "--") return undefined;
    if (text === "-c" || /^-[A-Za-z]*c[A-Za-z]*$/u.test(text)) return tokens[index + 1]?.text;
    if (!text.startsWith("-")) return undefined;
  }
  return undefined;
}

/**
 * @param {Array<{ text: string, quoted: boolean }>} tokens
 * @param {number} index
 * @returns {boolean}
 */
function gitArgumentsWrite(tokens, index) {
  while (index < tokens.length) {
    const text = tokens[index].text;
    if (text === "--") {
      index += 1;
      break;
    }
    if (!text.startsWith("-") || text === "-") break;
    if (
      text === "--version" ||
      text === "--help" ||
      text === "-h" ||
      text === "--html-path" ||
      text === "--man-path" ||
      text === "--info-path"
    ) {
      return false;
    }
    if (
      text === "-p" ||
      text === "--paginate" ||
      text === "--no-pager" ||
      text === "--bare" ||
      text === "--literal-pathspecs" ||
      text === "--no-replace-objects" ||
      text === "--no-optional-locks" ||
      text === "--no-advice"
    ) {
      index += 1;
      continue;
    }
    const name = text.includes("=") ? text.slice(0, text.indexOf("=")) : text;
    index += 1;
    if (GIT_GLOBAL_VALUE_OPTIONS.has(name) && !text.includes("=")) index += 1;
  }
  if (index >= tokens.length) return false;
  const subcommand = tokens[index].text;
  const args = tokens.slice(index + 1).map((token) => token.text);
  return gitSubcommandWrites(subcommand, args);
}

/**
 * @param {string} subcommand
 * @param {string[]} args
 * @returns {boolean}
 */
function gitSubcommandWrites(subcommand, args) {
  if (READ_SUBCOMMANDS.has(subcommand)) return false;
  if (hasAnyFlag(args, ["-h", "--help"])) return false;
  switch (subcommand) {
    case "apply":
      return !hasAnyFlag(args, ["--check", "--stat", "--numstat", "--summary"]);
    case "bisect":
      return args[0] !== undefined && !["log", "view"].includes(args[0]);
    case "branch":
      return branchWrites(args);
    case "commit-graph":
      return args[0] === "write";
    case "config":
      return configWrites(args);
    case "hash-object":
      return hasAnyFlag(args, ["-w", "--write"]);
    case "lfs":
      return lfsWrites(args);
    case "multi-pack-index":
      return args[0] === "write";
    case "notes":
      return notesWrites(args);
    case "reflog":
      return reflogWrites(args);
    case "remote":
      return remoteWrites(args);
    case "replace":
      return !hasAnyFlag(args, ["-l", "--list"]);
    case "sparse-checkout":
      return args[0] !== undefined && args[0] !== "list";
    case "stash":
      return stashWrites(args);
    case "submodule":
      return submoduleWrites(args);
    case "symbolic-ref":
      return symbolicRefWrites(args);
    case "tag":
      return tagWrites(args);
    case "worktree":
      return worktreeWrites(args);
    default:
      return WRITE_SUBCOMMANDS.has(subcommand);
  }
}

/**
 * @param {string[]} args
 * @param {string[]} flags
 * @returns {boolean}
 */
function hasAnyFlag(args, flags) {
  return flags.some((flag) =>
    args.some((arg) => arg === flag || (flag.startsWith("--") && arg.startsWith(`${flag}=`))),
  );
}

/**
 * @param {string[]} args
 * @returns {string | undefined}
 */
function firstPositional(args) {
  return args.find((arg) => !arg.startsWith("-") || arg === "-");
}

/**
 * `git config` reads with --get/--list/key, writes with key value or
 * --add/--unset/--edit. Positional counting skips the value of -f/--file.
 * @param {string[]} args
 * @returns {boolean}
 */
function configWrites(args) {
  // --global/--system write the container-local user/system config, not the
  // repository. Allowing them keeps read-only git usable under Docker's
  // rootful ownership (safe.directory) without touching repo state.
  if (hasAnyFlag(args, ["--global", "--system"])) return false;
  if (
    hasAnyFlag(args, [
      "-l",
      "--get",
      "--get-all",
      "--get-regexp",
      "--list",
      "--name-only",
      "--show-origin",
      "--show-scope",
    ])
  ) {
    return false;
  }
  if (
    hasAnyFlag(args, [
      "-e",
      "--add",
      "--edit",
      "--remove-section",
      "--rename-section",
      "--replace-all",
      "--unset",
      "--unset-all",
    ])
  ) {
    return true;
  }
  const positionals = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--") {
      positionals.push(...args.slice(index + 1).filter((value) => !value.startsWith("-")));
      break;
    }
    if (arg === "-f" || arg === "--file" || arg === "--blob") {
      index += 1;
      continue;
    }
    if (arg.startsWith("--file=") || arg.startsWith("--blob=")) continue;
    if (!arg.startsWith("-")) positionals.push(arg);
  }
  return positionals.length >= 2;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function branchWrites(args) {
  if (
    hasAnyFlag(args, [
      "-a",
      "-l",
      "-r",
      "-v",
      "-vv",
      "--all",
      "--column",
      "--contains",
      "--format",
      "--ignore-case",
      "--list",
      "--merged",
      "--no-column",
      "--no-contains",
      "--no-merged",
      "--points-at",
      "--remotes",
      "--show-current",
      "--sort",
      "--verbose",
    ])
  ) {
    return false;
  }
  if (
    hasAnyFlag(args, [
      "-C",
      "-D",
      "-M",
      "-c",
      "-d",
      "-f",
      "-m",
      "-u",
      "--copy",
      "--delete",
      "--edit-description",
      "--force",
      "--move",
      "--set-upstream-to",
      "--unset-upstream",
    ])
  ) {
    return true;
  }
  return firstPositional(args) !== undefined;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function tagWrites(args) {
  if (args.some((arg) => /^-n\d*$/u.test(arg))) return false;
  if (
    hasAnyFlag(args, [
      "-l",
      "-n",
      "-v",
      "--column",
      "--contains",
      "--format",
      "--merged",
      "--no-column",
      "--no-contains",
      "--no-merged",
      "--points-at",
      "--sort",
      "--verify",
    ])
  ) {
    return false;
  }
  if (
    hasAnyFlag(args, [
      "-F",
      "-a",
      "-d",
      "-f",
      "-m",
      "-s",
      "-u",
      "--annotate",
      "--cleanup",
      "--create-reflog",
      "--delete",
      "--file",
      "--force",
      "--local-user",
      "--message",
      "--sign",
    ])
  ) {
    return true;
  }
  return firstPositional(args) !== undefined;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function remoteWrites(args) {
  const subcommand = firstPositional(args);
  if (subcommand === undefined || subcommand === "show" || subcommand === "get-url") return false;
  return ["add", "prune", "remove", "rename", "rm", "set-branches", "set-head", "set-url", "update"].includes(
    subcommand,
  );
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function stashWrites(args) {
  const subcommand = firstPositional(args);
  if (subcommand === undefined) return true; // bare `git stash` = push
  return !["list", "show"].includes(subcommand);
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function worktreeWrites(args) {
  const subcommand = firstPositional(args);
  return subcommand !== undefined && subcommand !== "list";
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function submoduleWrites(args) {
  const subcommand = firstPositional(args);
  if (subcommand === undefined || subcommand === "status" || subcommand === "summary") return false;
  return true;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function reflogWrites(args) {
  const subcommand = firstPositional(args);
  return subcommand === "expire" || subcommand === "delete";
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function notesWrites(args) {
  const subcommand = firstPositional(args);
  if (subcommand === undefined || subcommand === "list" || subcommand === "show") return false;
  return true;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function symbolicRefWrites(args) {
  if (hasAnyFlag(args, ["-d", "--delete"])) return true;
  return args.filter((arg) => !arg.startsWith("-")).length >= 2;
}

/**
 * @param {string[]} args
 * @returns {boolean}
 */
function lfsWrites(args) {
  const subcommand = firstPositional(args);
  if (subcommand === undefined) return false;
  return !["env", "logs", "ls-files", "status", "version"].includes(subcommand);
}

/**
 * @param {unknown} content
 * @returns {string}
 */
function textOf(content) {
  if (!Array.isArray(content)) return "";
  return content
    .map((block) =>
      block !== null && typeof block === "object" && block.type === "text" && typeof block.text === "string"
        ? block.text
        : "",
    )
    .join("\n");
}

export default { name, inject, apply };

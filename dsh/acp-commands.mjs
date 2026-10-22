// ACP command bridge for the dev-dsh container.
//
// The stock @deepseek-ai/dsh-acp bridge implements the automation-only ACP
// surface: sessions, prompts, model options, MCP mounts and permissions. It
// never emits the standard `available_commands_update` notification and never
// calls `ctx.commands`, so an ACP client such as agent-shell shows no "/"
// commands.
//
// DSH itself already has both halves:
//   - dsh-tool-skill expands a typed `/skill-name` in a user message and
//     injects the skill body at agent/pre-step, so skills work over ACP as
//     soon as they are advertised;
//   - ctx.commands holds host-side slash commands (/compact, /goal, ...),
//     which are normally executed by a UI adapter, not by the model loop.
//
// This module wraps dsh-acp's `config.stream` transport, advertises the
// user-invocable skills and the host commands for each session, and executes
// a host command locally instead of forwarding its line to the model. It is
// an image-local deployment plugin; see acp-commands.patch.yml.

import { createRequire } from "node:module";
import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";
import { Readable, Writable } from "node:stream";

export const name = "acp-commands";

/** Same core services as @deepseek-ai/dsh-acp; skills/commands are read lazily. */
export const inject = ["agents", "llm", "sessionPersistence", "sessions"];

const NEW_SESSION = "session/new";
const RESUME_SESSION = "session/resume";
const PROMPT = "session/prompt";
const CANCEL = "session/cancel";
const CLOSE_SESSION = "session/close";
const SESSION_UPDATE = "session/update";
const PROMPT_UPDATE = "agent_message_chunk";
const COMMANDS_UPDATE = "available_commands_update";

/** The host-command grammar from @deepseek-ai/dsh-commands. */
const COMMAND_RE = /^\/([a-z][a-z0-9_-]*)(?=$|[\t\n\r ])/u;

/**
 * Parse one host command line with the same grammar as dsh-commands.
 * @param {string} line - complete candidate command line.
 * @returns {{ name: string, rawInput: string } | undefined} parsed command.
 */
export function parseCommandLine(line) {
  if (typeof line !== "string") return undefined;
  const match = COMMAND_RE.exec(line);
  if (match === null || match[1] === undefined) return undefined;
  return { name: match[1], rawInput: line.slice(match[0].length) };
}

/**
 * Return the prompt text when a prompt is exactly one text block.
 *
 * A command carrying images or files keeps its normal model path: the tap
 * does not drop content to steal the line. The ACP bridge's own prompt
 * admission remains the authority for those blocks.
 * @param {unknown} prompt - ACP prompt content blocks.
 * @returns {string | undefined} the single text block, if any.
 */
export function commandText(prompt) {
  if (!Array.isArray(prompt) || prompt.length !== 1) return undefined;
  const block = prompt[0];
  if (
    block === null
    || typeof block !== "object"
    || block.type !== "text"
    || typeof block.text !== "string"
  ) return undefined;
  return block.text;
}

/**
 * Merge host commands and user-invocable skills into the ACP command list.
 *
 * Commands win a name collision, matching the agent-scoped precedence of the
 * host command registry. Duplicate names are advertised once.
 * @param {readonly object[]} commands - `ctx.commands.list(agent)` descriptors.
 * @param {readonly object[]} skills - `ctx.skills.list(...)` summaries.
 * @returns {{ name: string, description: string, input?: { hint: string } }[]}
 *   the ACP `availableCommands` payload.
 */
export function buildAvailableCommands(commands, skills) {
  /** @type {{ name: string, description: string, input?: { hint: string } }[]} */
  const available = [];
  const seen = new Set();
  for (const command of commands ?? []) {
    if (command === null || typeof command !== "object") continue;
    const commandName = command.name;
    if (typeof commandName !== "string" || commandName.length === 0 || seen.has(commandName)) continue;
    seen.add(commandName);
    const entry = {
      name: commandName,
      description: typeof command.description === "string" ? command.description : "",
    };
    const hint = command.input?.hint;
    if (typeof hint === "string" && hint.length > 0) entry.input = { hint };
    available.push(entry);
  }
  for (const skill of skills ?? []) {
    if (skill === null || typeof skill !== "object") continue;
    const skillName = skill.name;
    if (typeof skillName !== "string" || skillName.length === 0 || seen.has(skillName)) continue;
    if (skill.invocation?.userInvocable !== true) continue;
    seen.add(skillName);
    const description = [skill.description, skill.whenToUse]
      .filter((value) => typeof value === "string" && value.length > 0)
      .join(" — ");
    available.push({ name: skillName, description });
  }
  return available;
}

/**
 * Wrap dsh-acp's transport with command advertisement and host-command
 * execution.
 *
 * The tap stays out of the bridge's admission and settlement code: skills are
 * forwarded as ordinary prompt text (dsh-tool-skill's agent/pre-step expands
 * them), while a resolved host command is executed locally and answered with
 * the standard `end_turn` stop reason.
 * @param {object} ctx - Cordis context carrying agents and the registries.
 * @param {object} config - the acp row's config, passed through to dsh-acp.
 * @param {object} deps - runtime dependencies.
 * @param {Function} deps.acpApply - @deepseek-ai/dsh-acp's apply.
 * @param {{ readable: ReadableStream, writable: WritableStream }} deps.base -
 *   the underlying ACP message stream.
 * @param {number} [deps.debounceMs] - coalescing delay for registry changes.
 * @returns {unknown} whatever acpApply returns.
 */
export function install(ctx, config, { acpApply, base, debounceMs = 50 } = {}) {
  if (typeof acpApply !== "function") throw new TypeError("acp-commands: acpApply is required");
  if (base === null || typeof base !== "object") throw new TypeError("acp-commands: base stream is required");

  let writeChain = Promise.resolve();
  let closed = false;

  /** Serialize every outbound frame, including the tap's own notifications. */
  const send = (message) => {
    const write = writeChain.then(async () => {
      const writer = base.writable.getWriter();
      try {
        await writer.write(message);
      } finally {
        writer.releaseLock();
      }
    });
    writeChain = write.catch(() => {});
    return write;
  };

  const warn = (detail) => {
    ctx.logger?.warn?.(`acp-commands: ${detail}`);
  };

  /** Session state: the requested cwd; the exact Agent is read from ctx. */
  const sessions = new Map();
  /** In-flight session/new and session/resume requests keyed by JSON-RPC id. */
  const tracked = new Map();
  /** Cancellation controller for a locally executing host command. */
  const activeCommands = new Map();
  const timers = new Map();

  const listCommands = (agent) => {
    const registry = ctx.get?.("commands");
    if (registry === undefined || typeof registry.list !== "function") return [];
    try {
      return registry.list(agent) ?? [];
    } catch (error) {
      warn(`command catalog failed: ${String(error)}`);
      return [];
    }
  };

  const listSkills = async (agent, cwd) => {
    const registry = ctx.get?.("skills");
    if (registry === undefined || typeof registry.list !== "function") return [];
    try {
      return await registry.list({ cwd, scope: agent });
    } catch (error) {
      warn(`skill catalog failed: ${String(error)}`);
      return [];
    }
  };

  const publish = async (sessionId) => {
    if (closed) return;
    const session = sessions.get(sessionId);
    if (session === undefined) return;
    const agent = ctx.agents?.get?.(sessionId);
    if (agent === undefined) return;
    const availableCommands = buildAvailableCommands(
      listCommands(agent),
      await listSkills(agent, session.cwd),
    );
    if (closed || !sessions.has(sessionId)) return;
    await send({
      jsonrpc: "2.0",
      method: SESSION_UPDATE,
      params: {
        sessionId,
        update: {
          sessionUpdate: COMMANDS_UPDATE,
          availableCommands,
        },
      },
    });
  };

  /** Coalesce registry invalidations; the client can receive updates anytime. */
  const schedulePublish = (sessionId) => {
    if (closed) return;
    const prior = timers.get(sessionId);
    if (prior !== undefined) clearTimeout(prior);
    const timer = setTimeout(() => {
      timers.delete(sessionId);
      publish(sessionId).catch((error) => warn(`command advertisement failed: ${String(error)}`));
    }, debounceMs);
    timer.unref?.();
    timers.set(sessionId, timer);
  };

  /** Execute a known host command; the line never reaches the model. */
  const executeCommand = async (message, line, parsed, agent, commands) => {
    const sessionId = message.params.sessionId;
    const controller = new AbortController();
    activeCommands.set(sessionId, controller);
    let result;
    try {
      const execution = await commands.execute(agent, line, [], controller.signal);
      result = execution?.result
        ?? { kind: "error", text: `/${parsed.name} did not run` };
    } catch (error) {
      result = {
        kind: "error",
        text: error instanceof Error ? error.message : String(error),
      };
    } finally {
      if (activeCommands.get(sessionId) === controller) activeCommands.delete(sessionId);
    }
    if (controller.signal.aborted) {
      await send({ jsonrpc: "2.0", id: message.id, result: { stopReason: "cancelled" } });
      return;
    }
    const text = typeof result.text === "string" && result.text.length > 0
      ? result.text
      : result.kind === "success"
        ? `/${parsed.name} completed`
        : `/${parsed.name} failed`;
    await send({
      jsonrpc: "2.0",
      method: SESSION_UPDATE,
      params: {
        sessionId,
        update: {
          sessionUpdate: PROMPT_UPDATE,
          content: { type: "text", text },
        },
      },
    });
    await send({ jsonrpc: "2.0", id: message.id, result: { stopReason: "end_turn" } });
  };

  /**
   * Claim a command prompt only when the exact Agent is idle and the name
   * resolves in the host registry. Everything else keeps dsh-acp's normal
   * path, including skills and the bridge's own in-flight rejection.
   */
  const claim = (message) => {
    const text = commandText(message.params?.prompt);
    if (text === undefined) return false;
    const parsed = parseCommandLine(text);
    if (parsed === undefined) return false;
    const sessionId = message.params?.sessionId;
    if (typeof sessionId !== "string" || !sessions.has(sessionId)) return false;
    const agent = ctx.agents?.get?.(sessionId);
    const commands = ctx.get?.("commands");
    if (agent === undefined || commands === undefined || commands === null) return false;
    if (agent.status !== "idle") return false;
    if (typeof commands.find !== "function" || commands.find(agent, parsed.name) === undefined) return false;
    executeCommand(message, text, parsed, agent, commands).catch((error) => {
      warn(`host command failed: ${String(error)}`);
      void send({
        jsonrpc: "2.0",
        id: message.id,
        result: { stopReason: "end_turn" },
      }).catch(() => {});
    });
    return true;
  };

  const readable = base.readable.pipeThrough(new TransformStream({
    transform(message, controller) {
      if (message === null || typeof message !== "object") {
        controller.enqueue(message);
        return;
      }
      if (message.method === NEW_SESSION || message.method === RESUME_SESSION) {
        tracked.set(message.id, {
          method: message.method,
          cwd: message.params?.cwd,
          sessionId: message.params?.sessionId,
        });
      } else if (message.method === CANCEL) {
        activeCommands.get(message.params?.sessionId)?.abort();
      } else if (message.method === CLOSE_SESSION) {
        activeCommands.get(message.params?.sessionId)?.abort();
        sessions.delete(message.params?.sessionId);
      } else if (message.method === PROMPT && claim(message)) {
        return; // answered locally; do not show dsh-acp a command prompt
      }
      controller.enqueue(message);
    },
  }));

  const writable = new WritableStream({
    async write(message) {
      let publishId;
      if (message !== null && typeof message === "object" && message.id !== undefined) {
        const request = tracked.get(message.id);
        if (request !== undefined) {
          tracked.delete(message.id);
          if (message.error === undefined) {
            const sessionId = request.method === NEW_SESSION
              ? message.result?.sessionId
              : request.sessionId;
            if (typeof sessionId === "string") {
              sessions.set(sessionId, { cwd: request.cwd });
              publishId = sessionId;
            }
          }
        }
      }
      await send(message);
      // The session/new or session/resume result is on the wire before the
      // first advertisement, so the client already knows the session id.
      if (publishId !== undefined) {
        publish(publishId).catch((error) => warn(`command advertisement failed: ${String(error)}`));
      }
    },
  });

  if (typeof ctx.on === "function") {
    const invalidate = () => {
      for (const sessionId of sessions.keys()) schedulePublish(sessionId);
    };
    ctx.on("skills/change", invalidate);
    ctx.on("commands/change", invalidate);
  }

  ctx.effect?.(() => () => {
    closed = true;
    for (const timer of timers.values()) clearTimeout(timer);
    timers.clear();
    for (const controller of activeCommands.values()) controller.abort();
    activeCommands.clear();
    sessions.clear();
    tracked.clear();
  });

  return acpApply(ctx, { ...config, stream: { readable, writable } });
}

/**
 * Resolve a package against the running dsh installation.
 *
 * The plugin lives outside the installation tree, so bare specifiers do not
 * resolve from its own path. `process.argv[1]` is the dsh launcher; resolve
 * its symlink first (/usr/bin/dsh -> the installed bin.js).
 */
function dshAnchor() {
  const entry = process.argv[1];
  if (typeof entry === "string" && entry.length > 0) {
    try {
      const resolved = realpathSync(entry);
      createRequire(resolved).resolve("@deepseek-ai/dsh-acp");
      return resolved;
    } catch {
      // fall through to the explicit error below
    }
  }
  throw new Error("acp-commands: cannot resolve @deepseek-ai/dsh-acp from the dsh launcher");
}

/**
 * Mount the tap around the real ACP bridge.
 * @param {object} ctx - Cordis context.
 * @param {object} config - the acp row's config.
 * @returns {Promise<unknown>} the bridge's apply result.
 */
export async function apply(ctx, config) {
  const require = createRequire(dshAnchor());
  const [acp, sdk] = await Promise.all([
    import(pathToFileURL(require.resolve("@deepseek-ai/dsh-acp")).href),
    import(pathToFileURL(require.resolve("@agentclientprotocol/sdk")).href),
  ]);
  if (typeof acp.apply !== "function") {
    throw new TypeError("acp-commands: @deepseek-ai/dsh-acp no longer exports apply()");
  }
  if (typeof sdk.ndJsonStream !== "function") {
    throw new TypeError("acp-commands: @agentclientprotocol/sdk no longer exports ndJsonStream()");
  }
  const base = sdk.ndJsonStream(
    Writable.toWeb(process.stdout),
    Readable.toWeb(process.stdin),
  );
  return install(ctx, config, { acpApply: acp.apply, base });
}

export default { name, inject, apply };

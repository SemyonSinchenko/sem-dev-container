// Unit and canary tests for the ACP command bridge.
//
// The pure helpers pin the advertisement/command grammar. The stream test
// exercises the tap with a fake @deepseek-ai/dsh-acp, and the canary test
// mounts the installed bridge through the same tap so an upstream change to
// `config.stream` or the `initialize` handler fails here instead of in
// agent-shell.
//
// Run with: node --test dsh/acp-commands.test.mjs

import assert from "node:assert/strict";
import { existsSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { delimiter, join } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";

import {
  buildAvailableCommands,
  commandText,
  install,
  parseCommandLine,
} from "./acp-commands.mjs";

/** A message-level ACP stream plus the client ends the test drives. */
function memoryBase() {
  let inbound;
  let inputClosed = false;
  const out = [];
  const readable = new ReadableStream({
    start(controller) {
      inbound = controller;
    },
    cancel() {
      inputClosed = true;
    },
  });
  const writable = new WritableStream({
    write(message) {
      out.push(message);
    },
  });
  return {
    base: { readable, writable },
    push(message) {
      inbound.enqueue(message);
    },
    close() {
      if (inputClosed) return;
      inputClosed = true;
      try {
        inbound.close();
      } catch {
        // The transform may already have closed the input side.
      }
    },
    out,
  };
}

/** Write one frame without holding the writer across an await. */
async function reply(writable, message) {
  const writer = writable.getWriter();
  try {
    await writer.write(message);
  } finally {
    writer.releaseLock();
  }
}

/** Poll until PREDICATE is true, then fail with LABEL. */
async function waitFor(predicate, label, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail(`timed out waiting for ${label}`);
}

/** Minimal bridge stand-in: answers initialize/session-new, records prompts. */
function fakeAcp(forwarded) {
  return async (_ctx, { stream }) => {
    const reader = stream.readable.getReader();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) return;
        if (value?.method === "initialize") {
          await reply(stream.writable, {
            jsonrpc: "2.0",
            id: value.id,
            result: { protocolVersion: 1 },
          });
        } else if (value?.method === "session/new") {
          await reply(stream.writable, {
            jsonrpc: "2.0",
            id: value.id,
            result: { sessionId: "s1" },
          });
        } else {
          forwarded.push(value);
          if (value?.method === "session/prompt") {
            await reply(stream.writable, {
              jsonrpc: "2.0",
              id: value.id,
              result: { stopReason: "end_turn" },
            });
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  };
}

/** Fake Cordis context carrying the two registries and one idle agent. */
function makeCtx({ commands = [], skills = [], status = "idle" } = {}) {
  const executed = [];
  const commandRegistry = {
    list: () => commands,
    find: (_agent, name) => commands.find((command) => command.name === name),
    execute: async (_agent, line) => {
      executed.push(line);
      return { commandId: "c1", result: { kind: "success", text: `ran ${line}` } };
    },
  };
  const skillRegistry = { list: async () => skills };
  const agent = {
    id: "s1",
    status,
    session: { id: "s1", header: { cwd: "/project" } },
  };
  return {
    ctx: {
      agents: { get: (id) => (id === "s1" ? agent : undefined) },
      get: (service) =>
        service === "commands" ? commandRegistry : service === "skills" ? skillRegistry : undefined,
      logger: { warn: () => {} },
      on: () => {},
      effect: () => {},
    },
    executed,
    agent,
  };
}

/** The installed dsh launcher, or undefined when the test runs without dsh. */
function findDshBin() {
  for (const dir of (process.env.PATH ?? "").split(delimiter)) {
    if (dir.length === 0) continue;
    const candidate = join(dir, "dsh");
    if (existsSync(candidate)) return realpathSync(candidate);
  }
  return undefined;
}

test("buildAvailableCommands: commands first, skills filtered, collisions once", () => {
  const commands = [
    { name: "compact", description: "Compact older history", input: { hint: "<text>" } },
  ];
  const skills = [
    {
      name: "guideme",
      description: "Guide a change",
      whenToUse: "when asked to explain",
      invocation: { userInvocable: true },
    },
    { name: "hidden", description: "Model only", invocation: { userInvocable: false } },
    { name: "compact", description: "Shadowed skill", invocation: { userInvocable: true } },
  ];
  assert.deepEqual(buildAvailableCommands(commands, skills), [
    { name: "compact", description: "Compact older history", input: { hint: "<text>" } },
    { name: "guideme", description: "Guide a change — when asked to explain" },
  ]);
});

test("commandText and parseCommandLine accept only one leading text command", () => {
  assert.deepEqual(parseCommandLine("/compact now"), {
    name: "compact",
    rawInput: " now",
  });
  assert.equal(parseCommandLine(" /compact"), undefined);
  assert.equal(parseCommandLine("/Compact"), undefined);
  assert.equal(parseCommandLine("/compact!"), undefined);
  assert.equal(commandText([{ type: "text", text: "/compact now" }]), "/compact now");
  assert.equal(
    commandText([{ type: "text", text: "/compact" }, { type: "image", data: "" }]),
    undefined,
  );
});

test("tap advertises, executes host commands, and forwards skills", async () => {
  const memory = memoryBase();
  const forwarded = [];
  const { ctx, executed } = makeCtx({
    commands: [{ name: "compact", description: "Compact older history" }],
    skills: [
      { name: "guideme", description: "Guide", invocation: { userInvocable: true } },
      { name: "hidden", description: "Hidden", invocation: { userInvocable: false } },
    ],
  });
  const running = install(
    ctx,
    { provider: "deepseek-official" },
    { acpApply: fakeAcp(forwarded), base: memory.base, debounceMs: 5 },
  );
  try {
    memory.push({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: 1, clientCapabilities: {} },
    });
    memory.push({
      jsonrpc: "2.0",
      id: 2,
      method: "session/new",
      params: { cwd: "/project", mcpServers: [] },
    });

    const isAdvertisement = (message) =>
      message?.method === "session/update"
      && message.params?.update?.sessionUpdate === "available_commands_update";
    await waitFor(
      () => memory.out.some(isAdvertisement),
      "the first available_commands_update",
    );
    const advertisement = memory.out.find(isAdvertisement);
    assert.deepEqual(
      advertisement.params.update.availableCommands.map((command) => command.name),
      ["compact", "guideme"],
    );

    memory.push({
      jsonrpc: "2.0",
      id: 3,
      method: "session/prompt",
      params: { sessionId: "s1", prompt: [{ type: "text", text: "/compact now" }] },
    });
    await waitFor(
      () => memory.out.some((message) => message.id === 3 && message.result?.stopReason === "end_turn"),
      "the host-command response",
    );
    assert.deepEqual(executed, ["/compact now"]);
    assert.equal(forwarded.some((message) => message.id === 3), false);
    assert.equal(
      memory.out.some(
        (message) =>
          message?.method === "session/update"
          && message.params?.update?.sessionUpdate === "agent_message_chunk"
          && message.params.update.content.text === "ran /compact now",
      ),
      true,
    );

    memory.push({
      jsonrpc: "2.0",
      id: 4,
      method: "session/prompt",
      params: { sessionId: "s1", prompt: [{ type: "text", text: "/guideme the change" }] },
    });
    await waitFor(
      () => forwarded.some((message) => message.id === 4),
      "the skill prompt to reach the bridge",
    );
  } finally {
    memory.close();
    await running;
  }
});

test(
  "upstream dsh-acp still accepts the tap's stream override",
  { skip: findDshBin() === undefined ? "dsh is not installed" : false },
  async () => {
    const require = createRequire(findDshBin());
    const acp = await import(
      pathToFileURL(require.resolve("@deepseek-ai/dsh-acp")).href
    );
    assert.equal(typeof acp.apply, "function");

    const memory = memoryBase();
    const ctx = {
      sessionPersistence: {},
      logger: { warn: () => {} },
      get: () => undefined,
      on: () => {},
      effect: () => {},
    };
    const running = install(ctx, {}, { acpApply: acp.apply, base: memory.base });
    try {
      memory.push({
        jsonrpc: "2.0",
        id: 1,
        method: "initialize",
        params: { protocolVersion: 1, clientCapabilities: {} },
      });
      await waitFor(
        () => memory.out.some((message) => message.id === 1 && message.result !== undefined),
        "the real bridge's initialize response",
      );
      assert.equal(memory.out.find((message) => message.id === 1).result.protocolVersion, 1);
    } finally {
      memory.close();
      await Promise.race([
        running,
        new Promise((resolve) => setTimeout(resolve, 250)),
      ]);
    }
  },
);

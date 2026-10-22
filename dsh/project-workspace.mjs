// project-workspace — boot plugin loaded by dsh-web-entry when
// bins/run-dsh sets DSH_PROJECT_DIR.
//
// Registers the container's project folder as a real DSH Workspace before
// the browser connects. Without this, DSH's first-use default Workspace
// (under <Documents>/deepseek-harness/default-workspace) becomes the
// session cwd, so the agent never sees the project.
//
// The registry is idempotent: an existing Workspace for the same canonical
// path is returned unchanged, including its title and session ledger.

import { basename } from "node:path";

export const name = "project-workspace";
export const inject = ["workspaceRegistry"];

export async function apply(ctx) {
  const project = process.env.DSH_PROJECT_DIR;
  if (project === undefined || project === "") return;

  const workspace = await ctx.workspaceRegistry.create(
    project,
    basename(project) || project,
  );
  ctx.logger.info(
    `project-workspace: registered "${workspace.title}" at ${workspace.path}`,
  );
}

export default { name, inject, apply };

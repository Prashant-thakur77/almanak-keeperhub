// Runs inside a KeeperHub checkout (copy this file to tests/unit/ there):
//   ALMANAK_COMPOUNDER_JSON=/path/to/almanak-keeperhub/docs/keeper-workflow.json pnpm vitest run tests/unit/compounder.test.ts
// Structural check with validateWorkflow, then the deep check (action schemas, template references, ABI matching).
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { validateWorkflow } from "@/lib/mcp/validate-workflow";
import { validateWorkflowDeep } from "@/lib/mcp/validate-workflow-deep";

const FILE = process.env.ALMANAK_COMPOUNDER_JSON ?? "../almanak-keeperhub/docs/keeper-workflow.json";

describe("almanak-keeperhub compounder workflow", () => {
  const raw = JSON.parse(readFileSync(FILE, "utf8"));
  const workflow = { id: "compounder", nodes: raw.nodes, edges: raw.edges, inputSchema: null, outputMapping: null, isListed: false, workflowType: "read" as const };

  it("passes the structural validator", () => {
    const result = validateWorkflow(workflow);
    expect(result.errors).toEqual([]);
    expect(result.valid).toBe(true);
  });

  it("passes the deep validator", async () => {
    const result = await validateWorkflowDeep(workflow);
    expect(result.errors).toEqual([]);
  });
});

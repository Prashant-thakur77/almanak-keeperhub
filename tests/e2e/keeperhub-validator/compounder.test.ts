import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
vi.mock("server-only", () => ({}));
import { validateWorkflow } from "@/lib/mcp/validate-workflow";

const FILE = process.env.ALMANAK_COMPOUNDER_JSON ?? "/tmp/claude-1000/-home-prashant-KeeperHub/c4726e2f-2e02-4e56-8a28-8661001ddb16/scratchpad/compounder.json";

describe("almanak-keeperhub compounder workflow", () => {
  it("passes KeeperHub's structural and deep checks", () => {
    const raw = JSON.parse(readFileSync(FILE, "utf8"));
    const result = validateWorkflow(
      { id: "compounder", nodes: raw.nodes, edges: raw.edges, inputSchema: null, outputMapping: null, isListed: false, workflowType: "read" },
      { deepCheck: true }
    );
    console.log(JSON.stringify({ valid: result.valid, errors: result.errors, warnings: result.warnings }));
    expect(result.errors).toEqual([]);
    expect(result.valid).toBe(true);
  });
});

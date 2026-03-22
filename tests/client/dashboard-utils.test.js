import { describe, it, expect } from "vitest";
import { createRequire } from "module";

const require = createRequire(import.meta.url);
const cp = require("../../control_plane/static/dashboard-utils.js");

describe("dashboard-utils (shared with browser)", () => {
  it("modelOptionLabel shows distinct name when it differs from id", () => {
    expect(cp.modelOptionLabel({ id: "m1", name: "Human label" })).toBe("Human label");
  });

  it("modelOptionLabel falls back to id when name matches id", () => {
    expect(cp.modelOptionLabel({ id: "gpt-4", name: "gpt-4" })).toBe("gpt-4");
  });

  it("sessionWorkspacePath uses repo_path when set", () => {
    expect(cp.sessionWorkspacePath({ repo_path: "/tmp/proj" }, "/home/ws")).toBe("/tmp/proj");
  });

  it("sessionWorkspacePath falls back to workspace root or label", () => {
    expect(cp.sessionWorkspacePath({ repo_path: "" }, "/home/ws")).toBe("/home/ws");
    expect(cp.sessionWorkspacePath({ repo_path: null }, "")).toBe("Workspace root");
  });
});

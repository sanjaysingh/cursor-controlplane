/**
 * Pure helpers for the web dashboard (workspace labels, model picker labels).
 * Loaded before app.js; exposed as window.cpDashboard. Node (Vitest) can require this file.
 */
(function (global) {
  const cpDashboard = {
    modelOptionLabel(opt) {
      if (!opt) return "";
      const id = String(opt.id || "").trim();
      const name = String(opt.name || "").trim();
      if (name && name !== id) return name;
      return id;
    },
    /**
     * @param {object|null|undefined} s session-like { repo_path?: string }
     * @param {string} workspaceRoot from GET /api/dashboard-config
     */
    sessionWorkspacePath(s, workspaceRoot) {
      if (!s) return "—";
      const rp = s.repo_path != null ? String(s.repo_path).trim() : "";
      if (rp) return rp;
      const root = (workspaceRoot || "").trim();
      return root || "Workspace root";
    },
  };
  global.cpDashboard = cpDashboard;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = cpDashboard;
  }
})(typeof window !== "undefined" ? window : globalThis);

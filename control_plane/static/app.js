/** Open external links safely after DOMPurify (register once). */
(function setupMarkdownSanitize() {
  if (typeof DOMPurify !== "undefined") {
    DOMPurify.addHook("afterSanitizeAttributes", (node) => {
      if (node.tagName === "A" && node.hasAttribute("href")) {
        node.setAttribute("target", "_blank");
        node.setAttribute("rel", "noopener noreferrer");
      }
    });
  }
})();

function dashboard() {
  return {
    /** Matches server WEB_CHANNEL_KEY (fetched from /api/dashboard-config). */
    webChannelKey: "web:default",
    newRepoPath: "",
    newSessionModel: "",
    /** Persisted default `agent --model` for new sessions (from GET /api/dashboard-config). */
    defaultModelPreference: "",
    defaultModelSaving: false,
    maxSessions: 5,
    sessions: [],
    selectedSessionId: null,
    selectedStatus: "",
    messages: [],
    draft: "",
    streamText: "",
    pendingQuestion: null,
    freeAnswer: "",
    error: "",
    /** True after dispatching a message until the POST completes (agent may still be streaming). */
    awaitingAgentReply: false,
    wsState: "disconnected",
    _ws: null,
    /** From `GET /api/models` — exact `agent --model` ids (label === value). */
    availableModels: [],
    modelsLoadError: "",
    modelsLoading: false,
    /** Resolved server workspace root (from /api/dashboard-config). */
    workspaceRoot: "",
    /** Unified local + GitHub options from GET /api/repo-picker */
    repoPickerItems: [],
    selectedRepoPickId: "",
    repoPickerError: "",
    repoPickerLoading: false,
    repoCloneBusy: false,

    /** Send button — disabled while a message is in flight (draft cleared immediately on send). */
    get canSend() {
      return !!(
        this.selectedSessionId &&
        this.draft.trim() &&
        !this.awaitingAgentReply
      );
    },

    /** Read-only label for active session (model is only set when creating a session). */
    get selectedSessionModelLabel() {
      const s = this.sessions.find((x) => x.id === this.selectedSessionId);
      if (!s) return "—";
      if (s.model == null || !String(s.model).trim()) return "Auto";
      return String(s.model).trim();
    },

    get atSessionLimit() {
      return Array.isArray(this.sessions) && this.sessions.length >= this.maxSessions;
    },

    async init() {
      if (typeof marked !== "undefined") {
        const m = /** @type {any} */ (marked);
        if (typeof m.setOptions === "function") {
          m.setOptions({ gfm: true, breaks: true, headerIds: false, mangle: false });
        }
      }
      this.$watch("streamText", () => {
        this.$nextTick(() => this._scrollMessagesEnd());
      });
      await this.loadDashboardConfig();
      await this.loadRepoPicker();
      await this.refreshSessions();
      this.connectWs();
      await this.fetchModels();
    },

    async loadDashboardConfig() {
      try {
        const r = await fetch("/api/dashboard-config");
        if (r.ok) {
          const d = await r.json();
          if (d && d.web_channel_key) {
            this.webChannelKey = String(d.web_channel_key);
          }
          if (d && d.workspace_root) {
            this.workspaceRoot = String(d.workspace_root);
          }
          if (d && d.default_model !== undefined && d.default_model !== null) {
            this.defaultModelPreference = String(d.default_model);
          }
        }
      } catch {
        /* keep default */
      }
    },

    async saveDefaultModel() {
      this.error = "";
      this.defaultModelSaving = true;
      try {
        const res = await fetch("/api/settings/default-model", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            model: this.defaultModelPreference.trim() || null,
          }),
        });
        const data = await res.json();
        if (!res.ok) {
          this.error = data.error || res.statusText;
          return;
        }
        if (data.default_model !== undefined) {
          this.defaultModelPreference = String(data.default_model || "");
        }
        this.newSessionModel = this.defaultModelPreference;
        await this.$nextTick();
        this.syncModelSelects();
      } catch (e) {
        this.error = String(e);
      } finally {
        this.defaultModelSaving = false;
      }
    },

    async loadRepoPicker() {
      this.repoPickerError = "";
      this.repoPickerLoading = true;
      try {
        const r = await fetch("/api/repo-picker?gh_limit=80");
        const data = await r.json();
        this.repoPickerItems = Array.isArray(data.items) ? data.items : [];
        this.repoPickerError = data.error || "";
      } catch (e) {
        this.repoPickerError = String(e);
        this.repoPickerItems = [];
      } finally {
        this.repoPickerLoading = false;
      }
    },

    async onRepoPickerChange() {
      const id = this.selectedRepoPickId;
      if (!id) {
        this.newRepoPath = "";
        return;
      }
      const it = this.repoPickerItems.find((x) => x.id === id);
      if (!it) {
        this.newRepoPath = "";
        return;
      }
      this.error = "";
      if (it.kind === "local") {
        this.newRepoPath = String(it.path || "");
        return;
      }
      if (it.kind === "github" && it.nameWithOwner) {
        this.repoCloneBusy = true;
        this.newRepoPath = "";
        try {
          const res = await fetch("/api/github/clone", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ nameWithOwner: it.nameWithOwner }),
          });
          const data = await res.json();
          if (!res.ok) {
            this.error = data.error || res.statusText;
            this.selectedRepoPickId = "";
            return;
          }
          if (data.path) this.newRepoPath = String(data.path);
          await this.loadRepoPicker();
          const match = this.repoPickerItems.find(
            (x) => x.kind === "local" && x.path === this.newRepoPath
          );
          if (match) this.selectedRepoPickId = match.id;
        } catch (e) {
          this.error = String(e);
          this.selectedRepoPickId = "";
        } finally {
          this.repoCloneBusy = false;
        }
      }
    },

    /**
     * Option label: normally exact CLI id (same as value). If `name` differs (e.g. stored id not in list), show `name`.
     */
    modelOptionLabel(opt) {
      if (!opt) return "";
      const id = String(opt.id || "").trim();
      const name = String(opt.name || "").trim();
      if (name && name !== id) return name;
      return id;
    },

    async fetchModels() {
      this.modelsLoadError = "";
      this.modelsLoading = true;
      const url = "/api/models";
      console.info("[cp-models] GET", url);
      try {
        const res = await fetch(url);
        const bodyText = await res.text();
        let data;
        try {
          data = JSON.parse(bodyText);
        } catch (parseErr) {
          console.error("[cp-models] JSON parse failed", {
            status: res.status,
            bodyPreview: bodyText.slice(0, 800),
          });
          throw parseErr;
        }
        const rawLen = Array.isArray(data.models) ? data.models.length : -1;
        this.availableModels = Array.isArray(data.models) ? data.models : [];
        this.modelsLoadError = data.error || "";
        if (this.availableModels.length) {
          this.modelsLoadError = "";
        } else if (!this.modelsLoadError) {
          this.modelsLoadError =
            "No models from CLI (`agent models`). Check CURSOR_API_KEY / agent install — see server log GET /models.";
        }
        console.info("[cp-models] response", {
          httpStatus: res.status,
          ok: res.ok,
          modelCount: this.availableModels.length,
          rawModelsFieldLength: rawLen,
          error: data.error || null,
          source: data.source,
        });
      } catch (e) {
        this.availableModels = [];
        this.modelsLoadError = String(e);
        console.error("[cp-models] fetch failed", e);
      }
      this.modelsLoading = false;
      this.newSessionModel = this.defaultModelPreference || "";
      await this.$nextTick();
      await this.$nextTick();
      this.syncModelSelects();
    },

    /**
     * Native <select> cannot reliably use Alpine x-for on <option>.
     * Repopulate options when models load or session changes.
     */
    syncModelSelects() {
      const fill = () => {
        this._fillModelSelect(this.$refs.newSessionModelSelect, this.availableModels, this.newSessionModel, false);
        this._fillModelSelect(this.$refs.defaultModelSelect, this.availableModels, this.defaultModelPreference, true);
      };
      this.$nextTick(() => {
        fill();
        this.$nextTick(fill);
      });
    },

    _fillModelSelect(sel, opts, currentVal, isNewForm) {
      if (!sel || sel.tagName !== "SELECT") {
        console.warn("[cp-models] _fillModelSelect: missing <select> ref", {
          hasEl: !!sel,
          tagName: sel && sel.tagName,
          isNewForm,
        });
        return;
      }
      const defaultLabel = "Auto";
      while (sel.options.length) {
        sel.remove(0);
      }
      const o0 = document.createElement("option");
      o0.value = "";
      o0.textContent = defaultLabel;
      sel.appendChild(o0);
      for (const opt of opts || []) {
        if (!opt || opt.id == null || String(opt.id).trim() === "") continue;
        const o = document.createElement("option");
        o.value = String(opt.id);
        o.textContent = this.modelOptionLabel(opt);
        sel.appendChild(o);
      }
      const want = currentVal != null && currentVal !== undefined ? String(currentVal) : "";
      sel.value = want;
      if (want && sel.value !== want) {
        const o = document.createElement("option");
        o.value = want;
        o.textContent = `${want} (other)`;
        sel.appendChild(o);
        sel.value = want;
      }
      const added = sel.options.length - 1;
      console.info("[cp-models] _fillModelSelect done", {
        isNewForm,
        optionCount: sel.options.length,
        nonDefaultOptions: added,
        sampleIds: (opts || [])
          .slice(0, 3)
          .map((o) => (o && o.id != null ? String(o.id).slice(0, 48) : null)),
      });
    },

    /** Markdown → sanitized HTML for chat bubbles */
    renderMarkdown(raw) {
      if (raw == null) return "";
      const s = String(raw);
      if (!s) return "";
      try {
        let html = s;
        const m = typeof marked !== "undefined" ? /** @type {any} */ (marked) : null;
        const parseFn = m && (typeof m.parse === "function" ? m.parse : typeof m === "function" ? m : null);
        if (parseFn) {
          html = parseFn.call(m, s);
        } else {
          return this._escapeHtml(s);
        }
        if (typeof DOMPurify !== "undefined" && typeof DOMPurify.sanitize === "function") {
          return DOMPurify.sanitize(html);
        }
        return this._escapeHtml(s);
      } catch (e) {
        console.warn("renderMarkdown", e);
        return this._escapeHtml(s);
      }
    },

    _escapeHtml(text) {
      const d = document.createElement("div");
      d.textContent = text;
      return d.innerHTML.replace(/\n/g, "<br>");
    },

    _scrollMessagesEnd() {
      const el = document.getElementById("messages-box");
      if (el) el.scrollTop = el.scrollHeight;
    },

    connectWs() {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws`;
      this._ws = new WebSocket(url);
      this._ws.onopen = () => {
        this.wsState = "connected";
      };
      this._ws.onclose = () => {
        this.wsState = "disconnected";
        setTimeout(() => this.connectWs(), 3000);
      };
      this._ws.onmessage = (ev) => {
        let msg;
        try {
          msg = JSON.parse(ev.data);
        } catch {
          return;
        }
        if (msg.type === "session_removed") {
          const sid = msg.session_id;
          this.sessions = this.sessions.filter((s) => s.id !== sid);
          if (this.selectedSessionId === sid) {
            this.selectedSessionId = null;
            this.selectedStatus = "";
            this.messages = [];
            this.streamText = "";
            this.pendingQuestion = null;
            this.awaitingAgentReply = false;
          }
        }
        if (msg.type === "session_updated" || msg.type === "session_closed") {
          this.mergeSession(msg.session);
          if (
            msg.type === "session_updated" &&
            msg.session.id === this.selectedSessionId &&
            msg.session.activity === "idle"
          ) {
            this.streamText = "";
            this.loadMessages();
          }
          if (msg.type === "session_closed" && msg.session.id === this.selectedSessionId) {
            this.selectedStatus = "closed";
            this.streamText = "";
          }
        }
        if (msg.type === "agent_stream" && msg.session_id === this.selectedSessionId) {
          if (!msg.conversation_id || msg.conversation_id === this.webChannelKey) {
            this.streamText += msg.text || "";
          }
        }
        if (
          msg.type === "question" &&
          msg.session_id === this.selectedSessionId &&
          msg.channel === "web" &&
          msg.conversation_id === this.webChannelKey
        ) {
          this.pendingQuestion = {
            question: msg.question,
            options: msg.options || [],
          };
        }
        if (
          msg.type === "channel_message" &&
          msg.channel === "web" &&
          msg.conversation_id === this.webChannelKey
        ) {
          this.streamText += (msg.text || "") + "\n";
        }
        if (msg.type === "sessions_purged") {
          this.sessions = [];
          this.selectedSessionId = null;
          this.selectedStatus = "";
          this.messages = [];
          this.streamText = "";
        }
      };
    },

    mergeSession(s) {
      const idx = this.sessions.findIndex((x) => x.id === s.id);
      if (idx >= 0) {
        this.sessions[idx] = s;
      } else {
        this.sessions.unshift(s);
      }
      if (this.selectedSessionId === s.id) {
        this.selectedStatus = s.status;
      }
      this.$nextTick(() => this.syncModelSelects());
    },

    async refreshSessions() {
      const res = await fetch("/api/sessions");
      this.sessions = await res.json();
    },

    async createSession() {
      this.error = "";
      const repoPath = String(this.newRepoPath ?? "").trim();
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          // Always a string (never JSON null): older Pydantic `repo_path: str` rejects null with 422.
          repo_path: repoPath,
          title: "",
          model: this.newSessionModel.trim() || null,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 422 && Array.isArray(data.detail)) {
          this.error = data.detail
            .map((d) => `${(d.loc && d.loc.join) ? d.loc.join(".") : ""}: ${d.msg || d.type || ""}`)
            .join("; ")
            .trim() || JSON.stringify(data.detail);
        } else {
          this.error = data.error || res.statusText;
        }
        return;
      }
      this.mergeSession(data);
      this.selectSession(data.id);
      this.newRepoPath = "";
      this.selectedRepoPickId = "";
      this.newSessionModel = "";
      await this.refreshSessions();
      await this.loadRepoPicker();
      await this.$nextTick();
      this.syncModelSelects();
    },

    async selectSession(id) {
      this.selectedSessionId = id;
      this.awaitingAgentReply = false;
      this.pendingQuestion = null;
      this.freeAnswer = "";
      this.streamText = "";
      this.error = "";
      const s = this.sessions.find((x) => x.id === id);
      this.selectedStatus = s ? s.status : "";
      try {
        await fetch(`/api/sessions/${encodeURIComponent(id)}/join`, { method: "POST" });
      } catch (_) {
        /* non-fatal */
      }
      await this.loadMessages();
      await this.$nextTick();
    },

    async loadMessages() {
      if (!this.selectedSessionId) return;
      const res = await fetch(`/api/sessions/${encodeURIComponent(this.selectedSessionId)}/messages`);
      if (!res.ok) return;
      this.messages = await res.json();
      this.$nextTick(() => this._scrollMessagesEnd());
    },

    /**
     * Keep Alpine x-model and the textarea DOM in sync when draft is set from JS
     * (x-model can leave the visible text stale on programmatic updates).
     */
    _setComposerDraft(value) {
      this.draft = value;
      this.$nextTick(() => {
        const el = this.$refs.composerDraft;
        if (!el) return;
        if (el.value !== value) {
          el.value = value;
          el.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
    },

    async sendDraft() {
      if (!this.selectedSessionId || !this.draft.trim() || this.awaitingAgentReply) return;
      const text = this.draft.trim();
      const sessionId = this.selectedSessionId;
      this.error = "";
      this._setComposerDraft("");
      this.streamText = "";
      this.awaitingAgentReply = true;
      try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/message`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await res.json();
        if (!res.ok) {
          this.error = data.error || res.statusText;
          if (this.selectedSessionId === sessionId) {
            this._setComposerDraft(text);
          }
          return;
        }
        if (this.selectedSessionId === sessionId) {
          this.mergeSession(data);
          await this.loadMessages();
        }
      } catch (e) {
        this.error = String(e);
        if (this.selectedSessionId === sessionId) {
          this._setComposerDraft(text);
        }
      } finally {
        if (this.selectedSessionId === sessionId) {
          this.awaitingAgentReply = false;
        }
      }
    },

    async closeSession() {
      if (!this.selectedSessionId) return;
      const id = this.selectedSessionId;
      const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/close`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        this.error = data.error || res.statusText;
        return;
      }
      if (this.selectedSessionId === id) {
        this.selectedSessionId = null;
        this.selectedStatus = "";
        this.messages = [];
        this.streamText = "";
        this.pendingQuestion = null;
        this.awaitingAgentReply = false;
      }
      await this.refreshSessions();
    },

    async closeAllSessions() {
      this.error = "";
      const res = await fetch("/api/sessions/close-all", { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        this.error = data.error || res.statusText;
        return;
      }
      this.sessions = [];
      this.selectedSessionId = null;
      this.selectedStatus = "";
      this.messages = [];
      this.streamText = "";
      this.pendingQuestion = null;
      this.awaitingAgentReply = false;
      await this.refreshSessions();
      await this.loadRepoPicker();
    },

    async sendAnswer(text) {
      if (!text || !this.selectedSessionId) return;
      const res = await fetch(`/api/sessions/${encodeURIComponent(this.selectedSessionId)}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer: text }),
      });
      if (res.ok) {
        this.pendingQuestion = null;
        this.freeAnswer = "";
      }
    },
  };
}


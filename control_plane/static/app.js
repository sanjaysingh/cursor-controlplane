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
    includeClosed: true,
    sessions: [],
    selectedSessionId: null,
    selectedStatus: "",
    messages: [],
    draft: "",
    streamText: "",
    pendingQuestion: null,
    freeAnswer: "",
    error: "",
    sending: false,
    wsState: "disconnected",
    _ws: null,
    /** From `GET /api/models` — exact `agent --model` ids (label === value). */
    availableModels: [],
    modelsLoadError: "",
    modelsLoading: false,

    /** Send button only — composer stays enabled whenever a session is selected */
    get canSend() {
      return !!(this.selectedSessionId && this.draft.trim() && !this.sending);
    },

    /** Read-only label for active session (model is only set when creating a session). */
    get selectedSessionModelLabel() {
      const s = this.sessions.find((x) => x.id === this.selectedSessionId);
      if (!s) return "—";
      if (s.model == null || !String(s.model).trim()) return "Auto";
      return String(s.model).trim();
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
        }
      } catch {
        /* keep default */
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
        this._fillModelSelect(this.$refs.newSessionModelSelect, this.availableModels, this.newSessionModel, true);
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

    q() {
      return `include_closed=${this.includeClosed}`;
    },

    async refreshSessions() {
      const res = await fetch("/api/sessions?" + this.q());
      this.sessions = await res.json();
    },

    async createSession() {
      this.error = "";
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_path: this.newRepoPath,
          title: "",
          model: this.newSessionModel.trim() || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        this.error = data.error || res.statusText;
        return;
      }
      this.mergeSession(data);
      this.selectSession(data.id);
      this.newRepoPath = "";
      this.newSessionModel = "";
      await this.refreshSessions();
      await this.$nextTick();
      this.syncModelSelects();
    },

    async selectSession(id) {
      this.selectedSessionId = id;
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

    async sendDraft() {
      if (!this.canSend) return;
      this.error = "";
      this.sending = true;
      this.streamText = "";
      try {
        const res = await fetch(`/api/sessions/${encodeURIComponent(this.selectedSessionId)}/message`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: this.draft }),
        });
        const data = await res.json();
        if (!res.ok) {
          this.error = data.error || res.statusText;
          return;
        }
        this.mergeSession(data);
        this.draft = "";
        await this.loadMessages();
      } catch (e) {
        this.error = String(e);
      } finally {
        this.sending = false;
      }
    },

    async closeSession() {
      if (!this.selectedSessionId) return;
      await fetch(`/api/sessions/${encodeURIComponent(this.selectedSessionId)}/close`, { method: "POST" });
      this.selectedStatus = "closed";
      await this.refreshSessions();
    },

    async closeAllSessions() {
      this.error = "";
      const res = await fetch("/api/sessions/close-all", { method: "POST" });
      const data = await res.json();
      if (!res.ok) { this.error = data.error || res.statusText; return; }
      await this.refreshSessions();
    },

    async purgeAllSessions() {
      this.error = "";
      const res = await fetch("/api/sessions/purge", { method: "POST" });
      const data = await res.json();
      if (!res.ok) { this.error = data.error || res.statusText; return; }
      this.sessions = [];
      this.selectedSessionId = null;
      this.selectedStatus = "";
      this.messages = [];
      this.streamText = "";
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

(() => {
  const modeRadios = document.getElementById("mode-radios");
  const modeError = document.getElementById("mode-error");
  const statusGrid = document.getElementById("status-grid");
  const logUnit = document.getElementById("log-unit");
  const logConnect = document.getElementById("log-connect");
  const logOutput = document.getElementById("log-output");

  let config = { modes: {}, dashboard_units: [] };
  let statusTimer = null;
  let logWs = null;

  function setModeError(msg) {
    if (!msg) {
      modeError.hidden = true;
      modeError.textContent = "";
      return;
    }
    modeError.hidden = false;
    modeError.textContent = msg;
  }

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) {
      let detail = r.statusText;
      try {
        const j = await r.json();
        if (j.detail) detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return r.json();
  }

  function renderModes() {
    modeRadios.innerHTML = "";
    const names = Object.keys(config.modes || {});
    names.forEach((name) => {
      const label = document.createElement("label");
      label.className = "mode-opt";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = "mode";
      input.value = name;
      input.addEventListener("change", async () => {
        if (!input.checked) return;
        setModeError("");
        try {
          await fetchJson(`/api/mode/${encodeURIComponent(name)}`, { method: "POST" });
          syncModeRadio(name);
          await refreshStatus();
        } catch (e) {
          setModeError(String(e.message || e));
          await refreshStatus();
        }
      });
      const meta = document.createElement("div");
      meta.className = "mode-meta";
      const strong = document.createElement("strong");
      strong.textContent = name;
      const span = document.createElement("span");
      span.textContent = (config.modes[name] && config.modes[name].description) || "";
      meta.appendChild(strong);
      meta.appendChild(span);
      label.appendChild(input);
      label.appendChild(meta);
      modeRadios.appendChild(label);
    });
  }

  function syncModeRadio(mode) {
    if (!mode) return;
    modeRadios.querySelectorAll('input[name="mode"]').forEach((el) => {
      el.checked = el.value === mode;
    });
  }

  function renderStatus(data) {
    statusGrid.innerHTML = "";
    const units = data.units || {};
    if (data.mode) syncModeRadio(data.mode);

    Object.keys(units).forEach((u) => {
      const row = document.createElement("div");
      row.className = "pill";
      const code = document.createElement("code");
      code.textContent = u;
      const st = document.createElement("span");
      const v = (units[u].active || "unknown").toLowerCase();
      st.className = `state ${v === "active" ? "active" : v === "inactive" ? "inactive" : "unknown"}`;
      st.textContent = units[u].active || "unknown";
      row.appendChild(code);
      row.appendChild(st);
      statusGrid.appendChild(row);
    });
  }

  function fillLogUnits() {
    const sel = logUnit;
    const prev = sel.value;
    sel.innerHTML = "";
    (config.dashboard_units || []).forEach((u) => {
      const o = document.createElement("option");
      o.value = u;
      o.textContent = u;
      sel.appendChild(o);
    });
    if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  }

  async function loadConfig() {
    config = await fetchJson("/api/config");
    renderModes();
    fillLogUnits();
  }

  async function refreshStatus() {
    const data = await fetchJson("/api/status");
    renderStatus(data);
  }

  function appendLog(line) {
    logOutput.textContent += (logOutput.textContent ? "\n" : "") + line;
    logOutput.scrollTop = logOutput.scrollHeight;
  }

  function stopLogs() {
    if (logWs) {
      logWs.close();
      logWs = null;
    }
  }

  function startLogs() {
    stopLogs();
    const unit = logUnit.value;
    if (!unit) return;
    logOutput.textContent = "";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/logs`;
    logWs = new WebSocket(wsUrl);
    logWs.addEventListener("open", () => {
      logWs.send(JSON.stringify({ unit }));
    });
    logWs.addEventListener("message", (ev) => {
      const t = ev.data;
      if (t.startsWith("{")) {
        try {
          const j = JSON.parse(t);
          if (j.error) appendLog("[error] " + j.error);
          return;
        } catch {
          /* plain line */
        }
      }
      appendLog(t);
    });
    logWs.addEventListener("close", () => {
      logWs = null;
    });
  }

  logConnect.addEventListener("click", () => {
    startLogs();
  });

  loadConfig()
    .then(() => refreshStatus())
    .catch((e) => setModeError(String(e.message || e)));

  statusTimer = window.setInterval(() => {
    refreshStatus().catch(() => {});
  }, 3000);
})();

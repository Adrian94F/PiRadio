(() => {
  const PAGE_SUBTITLE = {
    mode: "Choose playback mode",
    status: "Services & journal stream",
    settings: "Audio output device",
  };

  const ROUTES = ["mode", "status", "settings"];

  const modeButtons = document.getElementById("mode-buttons");
  const modeError = document.getElementById("mode-error");
  const pageSubtitle = document.getElementById("page-subtitle");
  const statusGrid = document.getElementById("status-grid");
  const logUnit = document.getElementById("log-unit");
  const logConnect = document.getElementById("log-connect");
  const logOutput = document.getElementById("log-output");
  const audioSelect = document.getElementById("audio-select");
  const audioApply = document.getElementById("audio-apply");
  const audioMeta = document.getElementById("audio-meta");
  const audioError = document.getElementById("audio-error");

  let config = { modes: {}, dashboard_units: [] };
  let statusTimer = null;
  let logWs = null;

  function setAudioError(msg) {
    if (!msg) {
      audioError.hidden = true;
      audioError.textContent = "";
      return;
    }
    audioError.hidden = false;
    audioError.textContent = msg;
  }

  function setModeError(msg) {
    if (!msg) {
      modeError.hidden = true;
      modeError.textContent = "";
      return;
    }
    modeError.hidden = false;
    modeError.textContent = msg;
  }

  function getRoute() {
    const raw = (window.location.hash || "#/mode").replace(/^#\/?/, "");
    const base = (raw.split("/")[0] || "mode").toLowerCase();
    return ROUTES.includes(base) ? base : "mode";
  }

  function applyRoute(route) {
    document.querySelectorAll("[data-page]").forEach((el) => {
      const show = el.getAttribute("data-page") === route;
      el.hidden = !show;
      el.setAttribute("aria-hidden", show ? "false" : "true");
    });

    document.querySelectorAll("[data-nav]").forEach((el) => {
      const active = el.getAttribute("data-nav") === route;
      el.classList.toggle("bottom-nav__item--active", active);
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    });

    if (pageSubtitle) {
      pageSubtitle.textContent = PAGE_SUBTITLE[route] || "";
    }

    if (route !== "status") {
      stopLogs();
    }
  }

  function initHash() {
    if (!window.location.hash || window.location.hash === "#") {
      const base = `${window.location.pathname}${window.location.search}`;
      window.history.replaceState(null, "", `${base}#/mode`);
    }
    applyRoute(getRoute());
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

  function highlightMode(mode) {
    if (!modeButtons) return;
    modeButtons.querySelectorAll(".mode-btn").forEach((btn) => {
      const active = btn.dataset.mode === mode;
      btn.classList.toggle("mode-btn--active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function renderModes() {
    if (!modeButtons) return;
    modeButtons.innerHTML = "";
    const names = Object.keys(config.modes || {});
    names.forEach((name) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "mode-btn";
      btn.dataset.mode = name;
      btn.setAttribute("aria-pressed", "false");

      const title = document.createElement("span");
      title.className = "mode-btn__title";
      title.textContent = name;

      const desc = document.createElement("span");
      desc.className = "mode-btn__desc";
      desc.textContent = (config.modes[name] && config.modes[name].description) || "";

      btn.appendChild(title);
      btn.appendChild(desc);

      btn.addEventListener("click", async () => {
        setModeError("");
        try {
          await fetchJson(`/api/mode/${encodeURIComponent(name)}`, { method: "POST" });
          highlightMode(name);
          await refreshStatus();
        } catch (e) {
          setModeError(String(e.message || e));
          await refreshStatus();
        }
      });

      modeButtons.appendChild(btn);
    });
  }

  function renderAudio(audio) {
    if (!audio) return;
    const outputs = audio.outputs || [];
    const current = audio.current || null;
    const prev = audioSelect.value;

    audioSelect.innerHTML = "";
    if (!outputs.length) {
      const o = document.createElement("option");
      o.value = "";
      o.textContent = "No playback devices (alsa-utils / USB?)";
      audioSelect.appendChild(o);
      audioSelect.disabled = true;
      audioApply.disabled = true;
    } else {
      audioSelect.disabled = false;
      audioApply.disabled = false;
      outputs.forEach((o) => {
        const opt = document.createElement("option");
        opt.value = o.id;
        opt.textContent = o.label;
        audioSelect.appendChild(opt);
      });
      const want =
        (current && current.id) ||
        (prev && outputs.some((x) => x.id === prev) ? prev : outputs[0].id);
      if (want && [...audioSelect.options].some((o) => o.value === want)) {
        audioSelect.value = want;
      }
    }

    let meta = "";
    if (current && current.id) {
      meta = `Selected: ${current.label || current.id}`;
      if (current.missing) {
        meta += " — device not listed now (unplugged?).";
      }
    } else {
      meta = "Choose a device and tap Apply.";
    }
    audioMeta.textContent = meta;
  }

  function renderStatus(data) {
    statusGrid.innerHTML = "";
    const units = data.units || {};
    if (data.mode) highlightMode(data.mode);

    renderAudio(data.audio);

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

  audioApply.addEventListener("click", async () => {
    const id = audioSelect.value;
    if (!id) return;
    setAudioError("");
    try {
      const res = await fetchJson("/api/audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      const rr = (res.restarted || []).filter(Boolean);
      if (rr.length) {
        audioMeta.textContent = `Applied ${res.label || id}. Restarted: ${rr.join(", ")}`;
      } else {
        audioMeta.textContent = `Applied ${res.label || id}.`;
      }
      await refreshStatus();
    } catch (e) {
      setAudioError(String(e.message || e));
    }
  });

  window.addEventListener("hashchange", () => applyRoute(getRoute()));

  initHash();

  loadConfig()
    .then(() => refreshStatus())
    .catch((e) => setModeError(String(e.message || e)));

  statusTimer = window.setInterval(() => {
    refreshStatus().catch(() => {});
  }, 3000);
})();

(() => {
  const PAGE_SUBTITLE = {
    mode: "Choose playback mode",
    status: "Services & journal stream",
    settings: "Audio & Bluetooth",
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
  const deviceNameInput = document.getElementById("device-name");
  const deviceNameSave = document.getElementById("device-name-save");
  const deviceNameMeta = document.getElementById("device-name-meta");
  const deviceNameError = document.getElementById("device-name-error");
  const btUnavailable = document.getElementById("bt-unavailable");
  const btAdapterMeta = document.getElementById("bt-adapter-meta");
  const btConnected = document.getElementById("bt-connected");
  const btPaired = document.getElementById("bt-paired");
  const btPairStart = document.getElementById("bt-pair-start");
  const btPairStop = document.getElementById("bt-pair-stop");
  const btPairHint = document.getElementById("bt-pair-hint");
  const btError = document.getElementById("bt-error");
  const btAdapterOff = document.getElementById("bt-adapter-off");
  const btAdapterOffMsg = document.getElementById("bt-adapter-off-msg");
  const btEnable = document.getElementById("bt-enable");
  const autoModeToggle = document.getElementById("auto-mode-toggle");
  const autoModeMeta = document.getElementById("auto-mode-meta");
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

  function setDeviceNameError(msg) {
    if (!msg) {
      deviceNameError.hidden = true;
      deviceNameError.textContent = "";
      return;
    }
    deviceNameError.hidden = false;
    deviceNameError.textContent = msg;
  }

  function setBtError(msg) {
    if (!msg) {
      btError.hidden = true;
      btError.textContent = "";
      return;
    }
    btError.hidden = false;
    btError.textContent = msg;
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

  function renderBluetooth(bt) {
    if (!btUnavailable) return;

    if (!bt || !bt.available) {
      btUnavailable.hidden = false;
      btUnavailable.textContent =
        bt && bt.error ? bt.error : "Bluetooth stack unavailable.";
      btPairStart.disabled = true;
      btPairStop.disabled = true;
      btConnected.innerHTML = "";
      btPaired.innerHTML = "";
      btAdapterMeta.textContent = "";
      btPairHint.textContent = "";
      if (btAdapterOff) btAdapterOff.hidden = true;
      return;
    }

    btUnavailable.hidden = true;

    const ad = bt.adapter || {};
    const ready = bt.adapter_ready === true;

    if (btAdapterOff && btAdapterOffMsg) {
      if (!ready) {
        btAdapterOff.hidden = false;
        if (bt.rfkill_blocked) {
          btAdapterOffMsg.textContent =
            "Bluetooth is disabled (rfkill blocked—often after reboot or flight mode). Tap Enable to unblock and power the adapter.";
        } else if (ad.powered === false) {
          btAdapterOffMsg.textContent =
            "The Bluetooth adapter is powered off. Tap Enable to turn it on.";
        } else {
          btAdapterOffMsg.textContent =
            "Bluetooth is not ready yet. Tap Enable to unblock rfkill (if needed) and power on.";
        }
      } else {
        btAdapterOff.hidden = true;
      }
    }

    btPairStart.disabled = !ready;

    const mac = ad.address || "";
    let metaLine = "";
    if (mac) {
      metaLine = `Adapter ${mac}`;
      if (!ready) {
        metaLine += bt.rfkill_blocked ? " · blocked" : "";
        metaLine += ad.powered === false ? " · powered off" : "";
        metaLine += ad.powered == null ? " · state unknown" : "";
      } else if (ad.pairable === false) {
        metaLine += " · not pairable";
      }
    }
    btAdapterMeta.textContent = metaLine;

    const conn = bt.connected || [];
    btConnected.innerHTML = "";
    if (!conn.length) {
      const empty = document.createElement("div");
      empty.className = "bt-block bt-block--empty";
      empty.textContent = "No device connected.";
      btConnected.appendChild(empty);
    } else {
      conn.forEach((d) => {
        const wrap = document.createElement("div");
        wrap.className = "bt-block bt-connected-card";
        const name = document.createElement("span");
        name.className = "bt-list__name";
        name.textContent = d.name || "(unknown)";
        const macEl = document.createElement("span");
        macEl.className = "bt-list__mac";
        macEl.textContent = d.address || "";
        wrap.appendChild(name);
        wrap.appendChild(macEl);
        btConnected.appendChild(wrap);
      });
    }

    const paired = bt.paired || [];
    btPaired.innerHTML = "";
    if (!paired.length) {
      const li = document.createElement("li");
      li.className = "bt-block--empty";
      li.style.border = "none";
      li.style.background = "transparent";
      li.style.padding = "0.5rem 0";
      li.textContent = "No paired devices yet. Use pairing mode above.";
      btPaired.appendChild(li);
    } else {
      paired.forEach((d) => {
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.className = "bt-list__name";
        name.textContent = d.name || "(unknown)";
        const macEl = document.createElement("span");
        macEl.className = "bt-list__mac";
        macEl.textContent = d.address || "";
        li.appendChild(name);
        li.appendChild(macEl);
        btPaired.appendChild(li);
      });
    }

    const disc = !!ad.discoverable;
    btPairStop.disabled = !ready || !disc;
    if (!ready) {
      btPairHint.textContent = "Enable Bluetooth above, then use pairing.";
    } else {
      btPairHint.textContent = disc
        ? "Adapter is visible — pair from your phone’s Bluetooth settings."
        : "Tap to advertise this Pi so new devices can discover it.";
    }
  }

  function renderStatus(data) {
    statusGrid.innerHTML = "";
    const units = data.units || {};
    if (data.mode) highlightMode(data.mode);

    // Device name
    if (deviceNameInput && document.activeElement !== deviceNameInput) {
      deviceNameInput.value = data.device_name || "";
    }

    // Auto-mode toggle
    if (autoModeToggle) {
      autoModeToggle.checked = !!data.auto_mode;
    }

    renderAudio(data.audio);

    renderBluetooth(data.bluetooth);

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

  if (btEnable) {
    btEnable.addEventListener("click", async () => {
      setBtError("");
      try {
        await fetchJson("/api/bluetooth/enable", { method: "POST" });
        await refreshStatus();
      } catch (e) {
        setBtError(String(e.message || e));
      }
    });
  }

  deviceNameSave.addEventListener("click", async () => {
    const name = (deviceNameInput.value || "").trim();
    if (!name) {
      setDeviceNameError("Enter a device name.");
      return;
    }
    setDeviceNameError("");
    try {
      const res = await fetchJson("/api/device-name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      deviceNameMeta.textContent = `Saved as "${res.name}" for AirPlay, Spotify & Bluetooth.`;
      if (res.errors) {
        setDeviceNameError(res.errors.join("; "));
      }
      await refreshStatus();
    } catch (e) {
      setDeviceNameError(String(e.message || e));
    }
  });

  btPairStart.addEventListener("click", async () => {
    setBtError("");
    try {
      await fetchJson("/api/bluetooth/discoverable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: true, minutes: 5 }),
      });
      await refreshStatus();
    } catch (e) {
      setBtError(String(e.message || e));
    }
  });

  btPairStop.addEventListener("click", async () => {
    setBtError("");
    try {
      await fetchJson("/api/bluetooth/discoverable", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: false }),
      });
      await refreshStatus();
    } catch (e) {
      setBtError(String(e.message || e));
    }
  });

  autoModeToggle.addEventListener("change", async () => {
    const enabled = autoModeToggle.checked;
    autoModeMeta.textContent = "Saving…";
    try {
      await fetchJson("/api/auto-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      autoModeMeta.textContent = enabled
        ? "Auto mode enabled — all services running, dmix active."
        : "Exclusive mode — only active source runs, direct hardware access.";
      await refreshStatus();
    } catch (e) {
      autoModeMeta.textContent = "Error: " + (e.message || e);
      autoModeToggle.checked = !enabled;
    }
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

/* Read-only GDW presentation. All transport destinations are source-owned. */
(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const CALIBRATION = document.body.dataset.console === "calibration";
  const STATUS_PATHS = new Set(["/v1/calibration/status", "/v1/dashboard/state", "/v1/dashboard/alerts"]);
  const RECORD = value => value !== null && typeof value === "object" && !Array.isArray(value);
  const STRING = value => typeof value === "string" && value.trim().length > 0 && value.length <= 2048;
  const COUNT = value => Number.isSafeInteger(value) && value >= 0;
  const RANGE = (value, max) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= max;
  const UNKNOWN = "UNAVAILABLE";
  let bearerToken = "";
  let active = null;
  let sourceActive = null;
  let generation = 0;
  let invoker = null;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function tone(node, value) {
    node.dataset.tone = ["watch", "unsafe", "degraded"].includes(value) ? value : "neutral";
  }
  function empty(id, text) { $(id).replaceChildren(element("div", text, "empty")); }
  function field(name, value, valid = STRING(value)) {
    const node = document.querySelector(`[data-field="${name}"]`);
    if (!node) return false;
    node.textContent = valid ? String(value) : UNKNOWN;
    node.dataset.missing = String(!valid);
    return valid;
  }
  function missingState(message) {
    document.querySelectorAll("[data-field]").forEach(node => {
      node.textContent = UNKNOWN;
      node.dataset.missing = "true";
    });
    $("integrity").textContent = UNKNOWN;
    $("alert-count").textContent = UNKNOWN;
    empty("alerts", message);
    if (CALIBRATION) {
      $("proposal-mix").textContent = message;
      $("reported-boundary").textContent = "The service boundary could not be read. Source-defined authority limits above remain in force.";
    } else {
      $("overall").textContent = UNKNOWN;
      tone($("overall"), "neutral");
      $("drift-status").textContent = UNKNOWN;
      tone($("drift-status"), "neutral");
      renderChain(null);
      empty("drift", message);
    }
  }

  async function jsonRead(path, signal, authenticated = true) {
    if (!(STATUS_PATHS.has(path) || (path === "/build-info" && !authenticated))) throw new Error("Unsupported read path");
    const headers = { Accept: "application/json" };
    if (authenticated && bearerToken) headers.Authorization = `Bearer ${bearerToken}`;
    const response = await fetch(path, { method: "GET", credentials: "omit", cache: "no-store", redirect: "error", signal, headers });
    if (response.status === 401) throw new Error("AUTH_REQUIRED");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    if (!(response.headers.get("content-type") || "").toLowerCase().includes("application/json")) throw new Error("Unexpected response type");
    const cap = 256 * 1024;
    const declared = response.headers.get("content-length");
    if (declared && /^\d+$/.test(declared) && Number(declared) > cap) throw new Error("Response exceeds display bound");
    if (!response.body) throw new Error("Response body unavailable");
    const reader = response.body.getReader();
    const chunks = [];
    let length = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        length += part.value.byteLength;
        if (length > cap) { await reader.cancel(); throw new Error("Response exceeds display bound"); }
        chunks.push(part.value);
      }
    } finally { reader.releaseLock(); }
    const body = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength; }
    const parsed = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    if (!RECORD(parsed)) throw new Error("Invalid status object");
    return parsed;
  }

  function renderAlerts(items, strings = false) {
    if (!Array.isArray(items) || items.length > 1000 || !items.every(item => strings ? STRING(item) : RECORD(item) && STRING(item.family) && STRING(item.recommended_next_step) && STRING(item.truth_status) && STRING(item.severity))) {
      $("alert-count").textContent = UNKNOWN;
      empty("alerts", "Alert data is missing or malformed. No clear-state inference is made.");
      return false;
    }
    $("alert-count").textContent = `${items.length} returned${items.length > 8 ? " · showing 8" : ""}`;
    if (!items.length) { empty("alerts", "No alerts in this response."); return true; }
    $("alerts").replaceChildren(...items.slice(0, 8).map(item => {
      const node = element("div", undefined, "alert");
      tone(node, strings ? "watch" : item.severity);
      node.append(element("strong", strings ? item : item.family));
      if (!strings) node.append(element("p", item.recommended_next_step), element("p", `${item.severity} / ${item.truth_status}`));
      return node;
    }));
    return true;
  }
  function renderChain(counts) {
    let valid = true;
    const labels = [["mutations", "Mutations"], ["simulations", "Simulations"], ["decisions", "Decisions"], ["manifests", "Manifests"], ["acks", "Runtime acks"], ["outcomes", "Outcomes"]];
    $("chain").replaceChildren(...labels.map(([key, label]) => {
      const item = element("li");
      const known = RECORD(counts) && COUNT(counts[key]);
      valid = valid && known;
      const value = element("strong", known ? counts[key] : UNKNOWN);
      value.dataset.missing = String(!known);
      item.append(value, element("span", label));
      return item;
    }));
    return valid;
  }
  function renderDrift(section) {
    if (!RECORD(section) || !STRING(section.status)) {
      empty("drift", "Localization state is unavailable.");
      $("drift-status").textContent = UNKNOWN;
      tone($("drift-status"), "neutral");
      return false;
    }
    $("drift-status").textContent = section.status;
    tone($("drift-status"), section.status);
    if (section.latest === null) { empty("drift", "The response contains no evaluated localization window."); return true; }
    const dimensions = section.latest?.dimensions;
    if (!RECORD(dimensions) || Object.keys(dimensions).length > 128) { empty("drift", "Localization dimensions are unavailable."); return false; }
    const entries = Object.entries(dimensions);
    if (!entries.length) { empty("drift", "No dimensions are present in the supplied window."); return true; }
    let valid = true;
    const nodes = entries.slice(0, 12).map(([name, item]) => {
      const known = name.length <= 128 && RECORD(item) && RANGE(item.severity, 1);
      valid = valid && known;
      const row = element("div", undefined, "drift-row");
      const bar = element("div", undefined, "bar");
      bar.setAttribute("aria-hidden", "true");
      const fill = element("i");
      if (known) fill.style.width = `${item.severity * 100}%`;
      bar.append(fill);
      row.append(element("span", name.length <= 128 ? name : "Dimension label unavailable"), bar, element("output", known ? item.severity.toFixed(2) : "N/A"));
      return row;
    });
    if (entries.length > 12) nodes.push(element("p", `Showing 12 of ${entries.length} supplied dimensions.`, "small muted"));
    $("drift").replaceChildren(...nodes);
    return valid;
  }
  function calibration(data) {
    if (data.service !== "szl-calibration-intelligence-plane" || data.status !== "MODELED") throw new Error("Unexpected calibration identity or truth status");
    const checks = [];
    const runtime = RECORD(data.runtime) ? data.runtime : {};
    checks.push(field("mode", STRING(runtime.active_mode) ? runtime.active_mode.toUpperCase() : null));
    checks.push(field("runtime", runtime.active_runtime_hash, typeof runtime.active_runtime_hash === "string" && /^[a-f0-9]{64}$/.test(runtime.active_runtime_hash)));
    checks.push(field("policy", data.policy_version), field("signing", data.signing_key_source));
    const counts = data.proposal_state_counts;
    const validCounts = RECORD(counts) && Object.keys(counts).length <= 128 && Object.entries(counts).every(([key, value]) => key.length <= 128 && COUNT(value));
    const total = validCounts ? Object.values(counts).reduce((a, b) => a + b, 0) : null;
    checks.push(field("proposal-count", total, validCounts && COUNT(total)));
    $("proposal-mix").textContent = validCounts && COUNT(total) ? Object.entries(counts).map(([key, value]) => `${key}: ${value}`).join(" · ") || "No proposals in the supplied queue." : "Proposal counts are unavailable.";
    checks.push(field("scenarios", data.scenario_count, COUNT(data.scenario_count)), renderAlerts(data.alerts, true));
    const boundary = STRING(data.truth_boundary);
    checks.push(boundary);
    $("reported-boundary").textContent = boundary ? data.truth_boundary : "The response did not supply a truth boundary.";
    $("integrity").textContent = "RESPONSE RECEIVED";
    return checks.every(Boolean);
  }
  function master(data) {
    if (data.truth_status !== "MODELED" || data.regulatory_claim !== "NOT_ASSESSED" || data.execution_authority !== "UNAVAILABLE") throw new Error("Unexpected master-suite authority declaration");
    const checks = [];
    const integrity = data.journal?.integrity_status ?? data.journal?.status;
    $("integrity").textContent = STRING(integrity) ? integrity : UNKNOWN;
    checks.push(STRING(integrity));
    $("overall").textContent = STRING(data.overall_status) ? data.overall_status : UNKNOWN;
    tone($("overall"), data.overall_status);
    checks.push(STRING(data.overall_status));
    const metrics = RECORD(data.metrics) ? data.metrics : {};
    for (const [name, key, max] of [["signed", "runtime_transitions_with_valid_signed_manifest_pct", 100], ["simulated", "proposals_with_simulation_pct", 100], ["rejection", "risk_gate_rejection_rate", 1], ["abstention", "abstention_rate", 1]]) {
      const known = RANGE(metrics[key], max);
      checks.push(field(name, known ? `${(metrics[key] * (max === 1 ? 100 : 1)).toFixed(1)}%` : null, known));
    }
    checks.push(renderChain(data.chain_counts), renderDrift(data.sections?.drift_localization));
    const sections = RECORD(data.sections) ? data.sections : {};
    const strategy = sections.control_isolation?.strategy_write_path;
    const apply = sections.runtime_apply_ack?.paper_shadow_only;
    const kill = sections.kill_switch_readiness?.engaged;
    checks.push(field("strategy", strategy ? "AVAILABLE" : "BLOCKED", typeof strategy === "boolean"));
    checks.push(field("owner", sections.control_isolation?.risk_control_owner));
    checks.push(field("signing", sections.receipt_validity?.signing_key_source));
    checks.push(field("apply", apply ? "MODELED / PAPER–SHADOW" : UNKNOWN, typeof apply === "boolean"));
    checks.push(field("broker", sections.kill_switch_readiness?.broker_enforcement));
    checks.push(field("kill", kill ? "ENGAGED" : "MODELED / IDLE", typeof kill === "boolean"));
    const generated = typeof data.generated_at === "string" ? Date.parse(data.generated_at) : NaN;
    checks.push(Number.isFinite(generated));
    $("state-note").textContent = Number.isFinite(generated)
      ? `Reported state generated ${new Date(generated).toISOString()}. Authority remains MODELED; this read does not authorize execution.`
      : "Reported state generation time is UNAVAILABLE. Authority remains MODELED; this read does not authorize execution.";
    return checks.every(Boolean);
  }

  async function refresh() {
    if (active) return;
    const current = generation;
    const controller = new AbortController();
    active = controller;
    const timeout = setTimeout(() => controller.abort(), 6000);
    $("refresh").disabled = true;
    $("connection").textContent = "Reading status…";
    $("observed-title").closest("section").setAttribute("aria-busy", "true");
    try {
      if (CALIBRATION) {
        const data = await jsonRead("/v1/calibration/status", controller.signal);
        if (current !== generation) return;
        const complete = calibration(data);
        $("connection").textContent = complete ? "Reported state loaded" : "PARTIAL — some fields unavailable";
      } else {
        const [state, alerts] = await Promise.allSettled([jsonRead("/v1/dashboard/state", controller.signal), jsonRead("/v1/dashboard/alerts", controller.signal)]);
        if (current !== generation) return;
        if (state.status !== "fulfilled") throw state.reason;
        const complete = master(state.value);
        const alertComplete = alerts.status === "fulfilled" && alerts.value.truth_status === "MODELED" && renderAlerts(alerts.value.alerts);
        if (!alertComplete) { $("alert-count").textContent = UNKNOWN; empty("alerts", "The alerts response is unavailable. Current alert absence cannot be established."); }
        $("connection").textContent = complete && alertComplete ? "Reported state loaded" : "PARTIAL — some fields unavailable";
      }
      $("updated").textContent = `Read ${new Date().toISOString()} · ${bearerToken ? "page session" : "anonymous request"}`;
      $("refresh").textContent = "Refresh state";
    } catch (error) {
      if (current !== generation) return;
      const auth = error?.message === "AUTH_REQUIRED";
      missingState(auth ? "This deployment requires authentication. Select Connect session to provide a token." : "The current read did not return qualified data. Retry when the source is available.");
      $("connection").textContent = auth ? "Authentication required" : "UNAVAILABLE — read failed";
      $("updated").textContent = auth ? "No credential prompt was opened automatically." : "Values were cleared to avoid presenting an earlier read as current.";
      $("state-note").textContent = "No current operator state is asserted by this page. Source-defined authority limits remain unchanged.";
      $("auto-refresh").checked = false;
      $("refresh").textContent = "Retry state";
    } finally {
      clearTimeout(timeout);
      if (active === controller) {
        active = null;
        $("refresh").disabled = false;
        $("observed-title").closest("section").removeAttribute("aria-busy");
      }
    }
  }

  function clearSession() {
    generation += 1;
    active?.abort();
    bearerToken = "";
    $("session-token").value = "";
    $("disconnect").hidden = true;
    $("auto-refresh").checked = false;
    missingState("Session cleared. Select Read state to make an anonymous request.");
    $("connection").textContent = "Session cleared";
    $("updated").textContent = "The token and displayed operator state have been removed from this page.";
    $("state-note").textContent = "Read-only view. Status reads do not create proposals, issue manifests, change risk controls, or submit orders.";
  }
  function closeSession() {
    $("session-token").value = "";
    $("session-error").textContent = "";
    $("session-dialog").close();
    invoker?.focus();
  }
  $("connect").addEventListener("click", event => {
    invoker = event.currentTarget;
    $("session-dialog").showModal();
    $("session-token").focus();
  });
  $("session-dialog").addEventListener("cancel", event => { event.preventDefault(); closeSession(); });
  $("session-cancel").addEventListener("click", closeSession);
  $("session-dialog").addEventListener("keydown", event => {
    if (event.key !== "Tab") return;
    const controls = [$("session-token"), $("session-cancel"), $("session-form").querySelector('button[type="submit"]')];
    const first = controls[0], last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  $("session-form").addEventListener("submit", event => {
    event.preventDefault();
    const value = $("session-token").value.trim();
    if (value.length < 32 || value.length > 4096 || /[\r\n]/.test(value)) { $("session-error").textContent = "Use a token of 32–4096 characters without line breaks."; return; }
    generation += 1;
    active?.abort();
    active = null;
    bearerToken = value;
    $("disconnect").hidden = false;
    closeSession();
    void refresh();
  });
  $("disconnect").addEventListener("click", clearSession);
  $("refresh").addEventListener("click", () => void refresh());
  $("auto-refresh").addEventListener("change", () => { if ($("auto-refresh").checked) void refresh(); });
  setInterval(() => { if ($("auto-refresh").checked && document.visibilityState === "visible") void refresh(); }, 15000);

  async function readSource() {
    if (sourceActive) return;
    const controller = new AbortController();
    sourceActive = controller;
    const timer = setTimeout(() => controller.abort(), 6000);
    $("source-retry").disabled = true;
    $("source-status").textContent = "Reading the runtime declaration…";
    $("source-link").hidden = true;
    $("source-link").removeAttribute("href");
    try {
      const data = await jsonRead("/build-info", controller.signal, false);
      if (data.service !== "gdw" || data.model_status !== "MODELED") throw new Error("Unexpected build identity");
      const source = typeof data.source_sha === "string" && /^[a-f0-9]{40}$/.test(data.source_sha) && !/^0+$/.test(data.source_sha);
      $("source-revision").textContent = source ? data.source_sha : UNKNOWN;
      $("source-image").textContent = typeof data.image_digest === "string" && /^sha256:[a-f0-9]{64}$/.test(data.image_digest) ? data.image_digest : UNKNOWN;
      $("source-mode").textContent = STRING(data.deployment_mode) ? data.deployment_mode : UNKNOWN;
      $("source-signing").textContent = STRING(data.receipt_signing) ? data.receipt_signing : UNKNOWN;
      if (source) { $("source-link").href = `https://github.com/szl-holdings/gdw-frontier/commit/${data.source_sha}`; $("source-link").hidden = false; }
      $("source-status").textContent = source ? "Declaration received. The listed commit is not independently authenticated by this page." : "Declaration received; immutable source revision UNAVAILABLE.";
    } catch {
      for (const id of ["source-revision", "source-image", "source-mode", "source-signing"]) $(id).textContent = UNKNOWN;
      $("source-status").textContent = "UNAVAILABLE — build declaration could not be read or qualified.";
    } finally { clearTimeout(timer); sourceActive = null; $("source-retry").disabled = false; }
  }
  $("source-details").addEventListener("toggle", () => { if ($("source-details").open) void readSource(); });
  $("source-retry").addEventListener("click", () => void readSource());
  function applyTheme(theme) {
    document.documentElement.dataset.surface = theme;
    const dark = theme === "dark";
    $("theme-toggle").textContent = dark ? "Light theme" : "Dark theme";
    $("theme-toggle").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    $("theme-toggle").setAttribute("aria-pressed", String(dark));
  }
  let savedTheme = null;
  try { savedTheme = localStorage.getItem("gdw-native-theme"); } catch { /* Session storage may be unavailable. */ }
  applyTheme(savedTheme === "dark" ? "dark" : "light");
  $("theme-toggle").addEventListener("click", () => {
    const theme = document.documentElement.dataset.surface === "dark" ? "light" : "dark";
    applyTheme(theme);
    try { localStorage.setItem("gdw-native-theme", theme); } catch { /* Theme remains usable without storage. */ }
  });
  window.addEventListener("pagehide", () => { clearSession(); sourceActive?.abort(); });
})();

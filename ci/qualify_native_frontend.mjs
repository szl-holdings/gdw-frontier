/* Actual installed-package pages, read-only local API and explicit response fixtures. */
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { lstatSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const { chromium } = createRequire(import.meta.url)(process.env.PLAYWRIGHT_MODULE || "playwright");
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PARENT = process.env.GDW_QA_OUT ? resolve(process.env.GDW_QA_OUT) : tmpdir();
mkdirSync(PARENT, { recursive: true, mode: 0o700 });
assert.ok(lstatSync(PARENT).isDirectory(), "observation parent must be a directory");
const OUT = mkdtempSync(join(PARENT, "gdw-native-observations-"));
const QUICK = process.argv.includes("--quick");
const widths = QUICK ? [320, 1440] : [320, 390, 768, 1024, 1440, 2560];
const checks = [], measurements = [], errors = [], denied = [], writes = [], served = [];
const TOKEN = "gdw-native-local-fixture-token-32-characters";
const paths = ["szl-tokens.css", "SOURCE.json", "szl-icon-32.svg", "szl-icon-16.svg", "PROJECTION.json", "gdw-native.css", "gdw-native.js"];
const hash = bytes => createHash("sha256").update(bytes).digest("hex");
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const check = (name, pass, detail) => checks.push({ name, pass: Boolean(pass), ...(pass ? {} : { detail }) });
function git(args) {
  const result = spawnSync("git", args, { cwd: ROOT, encoding: "utf8", maxBuffer: 1024 * 1024 });
  assert.equal(result.status, 0, "Git source observation failed");
  return result.stdout.trim();
}
const revision = git(["rev-parse", "HEAD"]);
const dirty = Boolean(git(["status", "--porcelain", "--untracked-files=all"]));
let browser, server, logs = "";
async function freePort() {
  const listener = createServer();
  await new Promise((resolve, reject) => { listener.once("error", reject); listener.listen(0, "127.0.0.1", resolve); });
  const port = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return port;
}
const calibration = {
  service: "szl-calibration-intelligence-plane", status: "MODELED", policy_version: "fixture-policy/v1", signing_key_source: "ephemeral_process",
  runtime: { active_mode: "paper", active_runtime_hash: "a".repeat(64) },
  proposal_state_counts: { proposed: 12, simulated: 8, acknowledged: 4 }, scenario_count: 4,
  alerts: ["EPHEMERAL_SIGNING_KEY"], truth_boundary: "Local rendered fixture: no broker connectivity, order authority, production signature, or trading-edge claim.",
};
const master = {
  generated_at: "2026-10-11T00:00:00Z", truth_status: "MODELED", regulatory_claim: "NOT_ASSESSED", execution_authority: "UNAVAILABLE", overall_status: "watch", journal: { integrity_status: "VERIFIED" },
  metrics: { runtime_transitions_with_valid_signed_manifest_pct: 75, proposals_with_simulation_pct: 83.3, risk_gate_rejection_rate: 0.125, abstention_rate: 0.25 },
  chain_counts: { mutations: 24, simulations: 20, decisions: 18, manifests: 12, acks: 10, outcomes: 8 },
  sections: {
    control_isolation: { strategy_write_path: false, risk_control_owner: "independent_fixture_operator" },
    receipt_validity: { signing_key_source: "fixture_environment" }, runtime_apply_ack: { paper_shadow_only: true },
    kill_switch_readiness: { engaged: false, broker_enforcement: "UNAVAILABLE" },
    drift_localization: { status: "watch", latest: { dimensions: { input_drift: { severity: 0.35 }, calibration_drift: { severity: 0.2 }, regime_novelty: { severity: 0.6 } } } },
  },
};
const alerts = { truth_status: "MODELED", alerts: [{ family: "insufficient_reference_support", severity: "watch", recommended_next_step: "Review the supplied window before proposing a change.", truth_status: "MODELED" }] };

async function contextFor(origin, options = {}, fixture = { mode: "valid" }) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, ...options });
  fixture.reads = [];
  await context.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== origin) { denied.push({ origin: url.origin, path: url.pathname }); return route.abort(); }
    if (request.method() !== "GET") { writes.push({ path: url.pathname, method: request.method() }); return route.abort(); }
    if (!url.pathname.startsWith("/v1/") && url.pathname !== "/build-info") return route.continue();
    fixture.reads.push({ path: url.pathname, authenticated: Boolean(request.headers().authorization), tokenMatchesFixture: request.headers().authorization === `Bearer ${TOKEN}` });
    if (fixture.mode === "actual") return route.continue();
    if (fixture.mode === "auth" && request.headers().authorization !== `Bearer ${TOKEN}`) return route.fulfill({ status: 401, contentType: "application/json", body: '{"detail":"fixture authentication required"}' });
    if (fixture.mode === "unavailable") return route.fulfill({ status: 503, contentType: "application/json", body: '{"detail":"local unavailable fixture"}' });
    if (fixture.mode === "malformed") return route.fulfill({ contentType: "application/json", body: "{" });
    if (fixture.mode === "oversized") return route.fulfill({ contentType: "application/json", body: JSON.stringify({ value: "x".repeat(300000) }) });
    if (fixture.mode === "wrongtype") return route.fulfill({ contentType: "text/html", body: "<p>Not a status response</p>" });
    if (fixture.mode === "alerts-unavailable" && url.pathname.endsWith("/alerts")) return route.fulfill({ status: 503, contentType: "application/json", body: "{}" });
    let value;
    if (url.pathname === "/build-info") value = { service: "gdw", model_status: "MODELED", source_sha: fixture.source || revision, image_digest: "UNAVAILABLE", deployment_mode: "LOCAL_FIXTURE", receipt_signing: "UNAVAILABLE" };
    else if (url.pathname === "/v1/calibration/status") value = structuredClone(calibration);
    else if (url.pathname === "/v1/dashboard/state") value = structuredClone(master);
    else if (url.pathname === "/v1/dashboard/alerts") value = structuredClone(alerts);
    else { denied.push({ path: url.pathname, reason: "unowned operator endpoint" }); return route.abort(); }
    if (url.pathname === "/v1/dashboard/state") {
      if (fixture.mode === "generation-missing") delete value.generated_at;
      if (fixture.mode === "generation-invalid") value.generated_at = "not a generation timestamp";
    }
    if (fixture.mode === "partial") {
      if (url.pathname.endsWith("/status")) delete value.proposal_state_counts;
      if (url.pathname.endsWith("/state")) { delete value.metrics; delete value.sections.control_isolation; delete value.sections.kill_switch_readiness; }
    }
    if (fixture.mode === "hostile") {
      const hostile = '<img src="https://invalid.test/fixture" onerror="window.pwned=1">';
      if (url.pathname.endsWith("/status")) { value.policy_version = hostile; value.truth_boundary = hostile; }
      if (url.pathname.endsWith("/state")) { value.overall_status = hostile; value.sections.control_isolation.risk_control_owner = hostile; value.sections.drift_localization.latest.dimensions = { [hostile]: { severity: 0.5 } }; }
      if (url.pathname.endsWith("/alerts")) { value.alerts[0].family = hostile; value.alerts[0].severity = hostile; }
    }
    return route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
  });
  context.on("page", page => page.on("pageerror", error => errors.push(error.message)));
  return context;
}

async function settle(page) { await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))); }
async function read(page) { await page.locator("#refresh").click(); await page.waitForFunction(() => !document.getElementById("refresh").disabled); await settle(page); }
async function readSource(page, selector) {
  await Promise.all([
    page.waitForResponse(response => new URL(response.url()).pathname === "/build-info", { timeout: 8000 }),
    page.locator(selector).click(),
  ]);
  await page.waitForFunction(() => !document.getElementById("source-retry").disabled);
}

async function measure(page, name) {
  const result = await page.evaluate(() => {
    const visible = (el) => {
      if (!el.getClientRects().length) return false;
      for (let at = el; at instanceof Element; at = at.parentElement) {
        const s = getComputedStyle(at);
        if (s.display === "none" || s.visibility !== "visible" || Number(s.opacity) === 0)
          return false;
      }
      return true;
    };
    const paint = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    const color = (value) => {
      paint.clearRect(0, 0, 1, 1);
      if (!CSS.supports("color", value)) throw new Error("Unsupported computed color");
      paint.fillStyle = value;
      paint.fillRect(0, 0, 1, 1);
      const x = paint.getImageData(0, 0, 1, 1).data;
      return [x[0], x[1], x[2], x[3] / 255];
    };
    const blend = (a, b) =>
      a
        .slice(0, 3)
        .map((v, i) => v * a[3] + b[i] * (1 - a[3]))
        .concat(1);
    const lum = (c) =>
      c
        .slice(0, 3)
        .map((v) => {
          v /= 255;
          return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
        })
        .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
    const ratio = (a, b) => (Math.max(lum(a), lum(b)) + 0.05) / (Math.min(lum(a), lum(b)) + 0.05);
    const layers = (value) => {
      const out = [];
      let depth = 0,
        start = 0;
      for (let i = 0; i < value.length; i++) {
        if (value[i] === "(") depth++;
        if (value[i] === ")") depth--;
        if (value[i] === "," && depth === 0) {
          out.push(value.slice(start, i));
          start = i + 1;
        }
      }
      out.push(value.slice(start));
      return out;
    };
    const cache = new Map();
    const backgrounds = (el) => {
      if (!el) return [[255, 255, 255, 1]];
      if (cache.has(el)) return cache.get(el);
      const s = getComputedStyle(el);
      let colors = backgrounds(el.parentElement).map((bg) => blend(color(s.backgroundColor), bg));
      for (const layer of layers(s.backgroundImage).reverse()) {
        if (!layer.includes("gradient(")) continue;
        const stops = [...layer.matchAll(/rgba?\([^()]*\)|color\([^()]*\)|#[\da-f]{3,8}\b/gi)].map(
          (m) => color(m[0]),
        );
        if (stops.length) colors = colors.flatMap((bg) => stops.map((stop) => blend(stop, bg)));
        colors = [...new Map(colors.map((c) => [c.map(Math.round).join(), c])).values()].slice(
          0,
          64,
        );
      }
      cache.set(el, colors);
      return colors;
    };
    const badText = [],
      badTargets = [],
      unnamed = [];
    let textPairs = 0;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode,
        el = node.parentElement;
      const text = node.textContent.trim();
      if (
        !text ||
        !el ||
        !(el instanceof HTMLElement) ||
        !visible(el) ||
        el.closest("script,style,option,[disabled],[aria-hidden='true']")
      )
        continue;
      const s = getComputedStyle(el),
        fg = color(s.color);
      const minimum =
        parseFloat(s.fontSize) >= 24 ||
        (parseFloat(s.fontSize) >= 18.66 && Number(s.fontWeight) >= 700)
          ? 3
          : 4.5;
      const actual = Math.min(...backgrounds(el).map((bg) => ratio(blend(fg, bg), bg)));
      textPairs++;
      if (actual + 0.01 < minimum)
        badText.push({
          text: text.slice(0, 100),
          ratio: Number(actual.toFixed(3)),
          minimum,
          element: el.className,
        });
    }
    for (const el of document.querySelectorAll(
      "button,input[type='password'],summary,.site-nav a,.site-footer a,.source-links a",
    )) {
      if (!visible(el)) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width < 43.9 || rect.height < 43.9)
        badTargets.push({
          tag: el.tagName,
          label: (el.getAttribute("aria-label") || el.textContent).trim().slice(0, 60),
          width: rect.width,
          height: rect.height,
        });
      if (
        el.tagName !== "INPUT" &&
        !el.textContent.trim() &&
        !el.getAttribute("aria-label") &&
        !el.getAttribute("aria-labelledby")
      )
        unnamed.push(el.outerHTML.slice(0, 180));
    }
    return {
      overflow: document.documentElement.scrollWidth > innerWidth + 1,
      width: innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      textPairs,
      badText,
      badTargets,
      unnamed,
      theme: document.documentElement.dataset.surface,
    };
  });
  measurements.push({ name, ...result });
  check(`${name}: no document overflow`, !result.overflow, result);
  check(
    `${name}: visible text contrast`,
    result.textPairs > 15 && !result.badText.length,
    result.badText,
  );
  check(`${name}: 44px controls`, !result.badTargets.length, result.badTargets);
  check(`${name}: controls named`, !result.unnamed.length, result.unnamed);
}



try {
  const port = await freePort(), origin = `http://127.0.0.1:${port}`;
  const clean = {
    PATH: process.env.PATH, PYTHONNOUSERSITE: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONUNBUFFERED: "1",
    GDW_DATA_DIR: join(OUT, "data"), GDW_LOG_DIR: join(OUT, "logs"), GDW_BACKUP_DIR: join(OUT, "backups"), GDW_WORKERS: "1",
    GDWX_MASTER_SUITE_DATA_ROOT: join(OUT, "master-suite"), GDW_REQUIRE_AUTH: "1", GDW_API_TOKEN: TOKEN,
    GDW_RECEIPT_SIGNING_REQUIRED: "0", GDW_SOURCE_SHA: dirty ? "UNAVAILABLE" : revision,
    OTEL_SDK_DISABLED: "true", OTEL_TRACES_EXPORTER: "none", OTEL_METRICS_EXPORTER: "none", OTEL_LOGS_EXPORTER: "none",
  };
  if (process.env.GDW_QA_PYTHON_PATH) clean.PYTHONPATH = process.env.GDW_QA_PYTHON_PATH;
  server = spawn(process.env.GDW_QA_PYTHON || "python", ["-c", `import uvicorn; uvicorn.run('app.api:app', host='127.0.0.1', port=${port}, workers=1, log_level='warning')`], { cwd: OUT, env: clean, stdio: ["ignore", "pipe", "pipe"] });
  for (const stream of [server.stdout, server.stderr]) stream.on("data", data => { logs = (logs + data.toString()).slice(-128 * 1024); });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (server.exitCode !== null) throw new Error(`Local package server exited: ${logs}`);
    try { const response = await fetch(origin + "/healthz", { signal: AbortSignal.timeout(1000), redirect: "error" }); if (response.ok) { ready = true; break; } } catch { /* Bounded local startup retry. */ }
    await wait(100);
  }
  assert.ok(ready, `Installed package did not become ready: ${logs}`);
  for (const name of paths) {
    const response = await fetch(origin + "/assets/gdw/" + name, { signal: AbortSignal.timeout(5000), redirect: "error" });
    const bytes = Buffer.from(await response.arrayBuffer());
    const expected = readFileSync(join(ROOT, "app/static/native", name));
    check(`installed asset ${name}`, response.ok && hash(bytes) === hash(expected), response.status);
    served.push({ path: "/assets/gdw/" + name, bytes: bytes.length, sha256: hash(bytes) });
  }
  for (const path of ["/assets/gdw/private.json", "/v1/calibration/status", "/v1/dashboard/state", "/v1/dashboard/alerts"]) {
    const response = await fetch(origin + path, { signal: AbortSignal.timeout(5000), redirect: "error" });
    check(`installed middleware protects ${path}`, response.status === 401, response.status);
    await response.arrayBuffer();
  }
  const unknown = await fetch(origin + "/assets/gdw/private.json", { headers: { Authorization: `Bearer ${TOKEN}` }, signal: AbortSignal.timeout(5000), redirect: "error" });
  check("authenticated neighboring asset remains absent", unknown.status === 404, unknown.status);
  await unknown.arrayBuffer();
  for (const [path, file] of [["/calibration", "calibration-console.html"], ["/master-suite", "master-suite.html"]]) {
    const response = await fetch(origin + path, { signal: AbortSignal.timeout(5000), redirect: "error" });
    const bytes = Buffer.from(await response.arrayBuffer());
    check(`installed HTML ${path}`, response.ok && hash(bytes) === hash(readFileSync(join(ROOT, "app/static", file))), response.status);
  }
  browser = await chromium.launch({ headless: true, ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}), args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  for (const path of ["/master-suite", "/calibration"]) {
    for (const width of widths) for (const theme of ["light", "dark"]) {
      const fixture = { mode: "valid" }, context = await contextFor(origin, { viewport: { width, height: 1100 } }, fixture);
      const page = await context.newPage();
      await page.goto(origin + path);
      if (theme === "dark") await page.locator("#theme-toggle").click();
      await settle(page);
      check(`${path} ${width} ${theme}: passive entry sends no operator/source request`, fixture.reads.length === 0, fixture.reads);
      await measure(page, `${path} ${width} ${theme} entry`);
      await read(page);
      check(`${path} ${width} ${theme}: explicit state rendered`, await page.locator("#connection").textContent() === "Reported state loaded");
      await measure(page, `${path} ${width} ${theme} loaded`);
      if ([320,1440].includes(width)) await page.screenshot({ path: join(OUT, `${path.slice(1)}-${width}-${theme}.png`), fullPage: true });
      await context.close();
    }
    const fixture = { mode: "valid" }, context = await contextFor(origin, {}, fixture), page = await context.newPage();
    await page.goto(origin + path);
    for (const mode of ["unavailable", "malformed", "oversized", "wrongtype", "auth"]) {
      fixture.mode = mode;
      await read(page);
      check(`${path} ${mode}: qualified failure`, /UNAVAILABLE|Authentication required/.test(await page.locator("#connection").textContent()));
      check(`${path} ${mode}: no fabricated zero or empty alert state`, await page.locator('[data-field]').evaluateAll(nodes => nodes.every(node => node.textContent === "UNAVAILABLE")) && await page.locator("#alert-count").textContent() === "UNAVAILABLE");
      check(`${path} ${mode}: no passive auth modal`, !(await page.locator("#session-dialog").evaluate(node => node.open)));
    }
    fixture.mode = "partial";
    await read(page);
    check(`${path}: missing fields stay partial`, (await page.locator("#connection").textContent()).includes("PARTIAL"));
    if (path === "/master-suite") check("missing control booleans never infer BLOCKED/IDLE", await page.locator('[data-field="strategy"]').textContent() === "UNAVAILABLE" && await page.locator('[data-field="kill"]').textContent() === "UNAVAILABLE");
    if (path === "/master-suite") for (const mode of ["generation-missing", "generation-invalid"]) {
      fixture.mode = "valid";
      await read(page);
      check(`${mode}: valid predecessor displays its generation`, (await page.locator("#state-note").textContent()).includes("2026-10-11T00:00:00.000Z"));
      fixture.mode = mode;
      await read(page);
      const note = await page.locator("#state-note").textContent();
      check(`${mode}: later response clears the predecessor generation`, (await page.locator("#connection").textContent()).includes("PARTIAL") && note.includes("generation time is UNAVAILABLE") && note.includes("Authority remains MODELED") && !note.includes("2026-10-11T00:00:00.000Z"));
    }
    fixture.mode = "hostile";
    await read(page);
    check(`${path}: hostile text remains text`, await page.locator("body").evaluate(node => node.textContent.includes("<img src=")) && await page.locator('img[src*="invalid.test"]').count() === 0 && !(await page.evaluate(() => window.pwned)));
    fixture.mode = "auth";
    await page.locator("#connect").click();
    check(`${path}: modal focuses input`, await page.locator("#session-token").evaluate(node => node === document.activeElement));
    await page.keyboard.press("Shift+Tab");
    check(`${path}: reverse modal focus contained`, await page.locator('#session-form button[type="submit"]').evaluate(node => node === document.activeElement));
    await page.keyboard.press("Tab");
    check(`${path}: forward modal focus contained`, await page.locator("#session-token").evaluate(node => node === document.activeElement));
    await page.locator("#session-token").fill(TOKEN);
    await page.locator('#session-form button[type="submit"]').click();
    await page.waitForFunction(() => !document.getElementById("refresh").disabled);
    check(`${path}: session dialog restores invoker focus`, await page.locator("#connect").evaluate(node => node === document.activeElement));
    check(`${path}: explicit session reads scoped endpoint`, fixture.reads.some(item => item.tokenMatchesFixture) && await page.locator("#connection").textContent() === "Reported state loaded");
    check(`${path}: token absent from input/storage/URL`, await page.locator("#session-token").inputValue() === "" && !(await page.evaluate(token => location.href.includes(token) || Object.values(localStorage).includes(token) || Object.values(sessionStorage).includes(token), TOKEN)));
    await page.locator("#disconnect").click();
    check(`${path}: session clear removes displayed state`, await page.locator('[data-field]').evaluateAll(nodes => nodes.every(node => node.textContent === "UNAVAILABLE")));
    fixture.mode = "valid";
    await readSource(page, "#source-details summary");
    check(`${path}: source read never carries bearer`, fixture.reads.filter(item => item.path === "/build-info").every(item => !item.authenticated));
    check(`${path}: declaration uses immutable fixed source link`, await page.locator("#source-link").getAttribute("href") === `https://github.com/szl-holdings/gdw-frontier/commit/${revision}`, await page.locator("#source-status").textContent());
    fixture.source = "0".repeat(40);
    await readSource(page, "#source-retry");
    check(`${path}: placeholder source stays unavailable`, await page.locator("#source-revision").textContent() === "UNAVAILABLE" && await page.locator("#source-link").isHidden());
    fixture.source = "https://invalid.test/source";
    await readSource(page, "#source-retry");
    check(`${path}: arbitrary source cannot become a link`, await page.locator("#source-link").getAttribute("href") === null);
    await page.emulateMedia({ reducedMotion: "reduce" });
    check(`${path}: reduced motion has no running decorative animation`, await page.evaluate(() => document.getAnimations().every(a => a.playState !== "running")));
    await page.emulateMedia({ forcedColors: "active" });
    await measure(page, `${path} forced colors`);
    await page.emulateMedia({ forcedColors: "none", media: "print" });
    await measure(page, `${path} print`);
    await context.close();
    const nojs = await contextFor(origin, { javaScriptEnabled: false }), nojsPage = await nojs.newPage();
    await nojsPage.goto(origin + path);
    check(`${path}: no-JavaScript recovery`, await nojsPage.locator(".no-js").isVisible());
    await nojs.close();
  }
  const actual = { mode: "actual" }, actualContext = await contextFor(origin, {}, actual), actualPage = await actualContext.newPage();
  await actualPage.goto(origin + "/master-suite");
  await read(actualPage);
  check("installed real API still requires operator auth", await actualPage.locator("#connection").textContent() === "Authentication required");
  await actualPage.locator("#connect").click();
  await actualPage.locator("#session-token").fill(TOKEN);
  await actualPage.locator('#session-form button[type="submit"]').click();
  await actualPage.waitForFunction(() => !document.getElementById("refresh").disabled);
  check("installed real local API read qualifies", await actualPage.locator("#connection").textContent() === "Reported state loaded", await actualPage.locator("#connection").textContent());
  check("real modeled empty denominator remains observed zero", await actualPage.locator('[data-field="signed"]').textContent() === "0.0%");
  await actualContext.close();
  check("no browser page errors", errors.length === 0, errors);
  check("no remote browser transport", denied.length === 0, denied);
  check("no browser non-GET operations", writes.length === 0, writes);
} catch (error) { check("qualification completed", false, String(error.stack || error)); }
finally {
  await browser?.close();
  server?.kill("SIGTERM");
  if (server && server.exitCode === null) await Promise.race([new Promise(resolve => server.once("exit", resolve)), wait(3000).then(() => server.kill("SIGKILL"))]);
  writeFileSync(join(OUT, "server.log"), logs, { flag: "wx", mode: 0o600 });
  const report = { kind: "UNSIGNED_RENDERED_PACKAGE_FIXTURE_OBSERVATION", revision, dirty, checks: checks.length, passed: checks.filter(x => x.pass).length, failed: checks.filter(x => !x.pass), measurements, errors, denied, writes, served, limits: ["Actual installed wheel serves both HTML pages and all seven native assets; operator examples are explicit local response fixtures plus one authenticated read of a disposable local service.", "Browser transport counters cover browser-context traffic, not every Python-process socket. Child environment supplies no external endpoint or credential; tracing exporters are disabled.", "No live Space read, upload, production witness, model operation, market authority, signed receipt admission or publication is performed.", "Contrast covers visible HTML text and gradient color envelopes; this is not complete assistive-technology certification."] };
  writeFileSync(join(OUT, "results.json"), JSON.stringify(report, null, 2) + "\n", { flag: "wx", mode: 0o600 });
  console.log(JSON.stringify({ checks: report.checks, passed: report.passed, failed: report.failed, output: OUT }, null, 2));
  process.exitCode = report.failed.length ? 1 : 0;
}

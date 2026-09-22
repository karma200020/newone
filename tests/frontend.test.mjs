import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { webcrypto } from "node:crypto";
import vm from "node:vm";

const source = readFileSync(resolve("src", "ops", "static", "app.js"), "utf8");
const html = readFileSync(resolve("src", "ops", "static", "index.html"), "utf8");
const css = readFileSync(resolve("src", "ops", "static", "app.css"), "utf8");

// A deliberately small DOM double exercises application behavior without a build
// step or network dependencies. Layout still requires a real-browser smoke test.
class Node {
  constructor(tag, document) {
    this.tagName = tag.toUpperCase();
    this.document = document;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.className = "";
    this.id = "";
    this.value = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.open = false;
    this.scrollTop = 0;
    this._text = "";
    this.classList = {
      toggle: (name, force) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (force ?? !names.has(name)) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
  set innerHTML(_) { throw new Error("Unsafe HTML assignment"); }
  append(...nodes) { for (const node of nodes) { node.parent = this; this.children.push(node); } }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  addEventListener(name, fn) { (this.listeners[name] ||= []).push(fn); }
  dispatch(name) { for (const fn of this.listeners[name] || []) fn({ preventDefault() {}, target: this }); }
  click() { if (!this.disabled) this.dispatch("click"); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  focus() { this.document.activeElement = this; }
  scrollIntoView() {}
  remove() { if (this.parent) this.parent.children = this.parent.children.filter((child) => child !== this); }
  contains(node) { return this === node || this.children.some((child) => child.contains(node)); }
  querySelectorAll(selector) {
    const matches = (node) => {
      if (selector.startsWith(".")) return node.className.split(/\s+/).includes(selector.slice(1));
      if (selector.startsWith("[data-")) {
        const key = selector.slice(6, -1).replace(/-([a-z])/g, (_, char) => char.toUpperCase());
        return Object.hasOwn(node.dataset, key);
      }
      if (selector === "details[open]") return node.tagName === "DETAILS" && node.open;
      return node.tagName.toLowerCase() === selector;
    };
    return this.children.flatMap((node) => [...(matches(node) ? [node] : []), ...node.querySelectorAll(selector)]);
  }
}
function documentDouble() {
  const document = {
    createElement(tag) { return new Node(tag, this); },
    getElementById(id) {
      const walk = (node) => node.id === id ? node : node.children.map(walk).find(Boolean);
      return walk(this.body) || null;
    },
    querySelectorAll(selector) { return this.body.querySelectorAll(selector); },
  };
  document.body = document.createElement("body");
  document.activeElement = document.body;
  for (const match of html.matchAll(/<([a-z][a-z0-9-]*)\b([^>]*\bid="([^"]+)"[^>]*)>/gi)) {
    const node = document.createElement(match[1]);
    node.id = match[3];
    node.hidden = /\bhidden\b/.test(match[2]);
    document.body.append(node);
  }
  return document;
}
function harness(fetcher = async () => Response.json([])) {
  const document = documentDouble(), timers = new Map(), calls = [];
  let nextTimer = 0;
  const context = vm.createContext({
    console, URL, DOMException, AbortController, crypto: webcrypto,
    setTimeout: (fn, delay) => { timers.set(++nextTimer, { fn, delay }); return nextTimer; },
    clearTimeout: (id) => timers.delete(id),
    fetch: async (url, options) => { calls.push({ url, options }); return fetcher(url, options); },
  });
  vm.runInContext(`${source}\nglobalThis.app = {
    state, api, login, signOut, loadConfig, refresh, poll, setView, renderRegion,
    duration, percent, money, safeReference, approvalRestriction, errorMessage,
    filterEvidence, renderScenarios, renderInvestigations, renderDetail, renderEvidence,
    renderApprovals, renderEvaluations, createInvestigation, decide, approvalKey,
    selectInvestigation, downloadReport
  };`, context);
  context.document = document;
  return { app: context.app, context, document, timers, calls, $: (id) => document.getElementById(id) };
}
const principal = (role = "operator", subject = `demo-${role}`) => ({ subject, role, tenant_id: "acme", services: ["*"], tools: ["*"] });
const proposal = (digest = "a".repeat(64)) => ({
  investigation_id: "investigation-1", requested_by: "demo-operator", digest,
  action: { id: "action-1", kind: "restart_service", service: "checkout-service", environment: "synthetic", reason: "Evidence indicates failure", evidence_ids: ["ev-1"], parameters: { component: "application" }, risk: "high", status: "proposed", verification: null },
});
const investigation = () => ({
  id: "investigation-1", scenario_id: "INC-001", description: "Investigate checkout service errors",
  status: "diagnosed", severity: "high", created_at: "2026-09-22T10:00:00Z", updated_at: "2026-09-22T10:00:42Z",
  affected_services: ["checkout-service"], actions: [proposal().action], warnings: [],
  usage: { cost_usd: 0 }, report: "# Actual report",
  diagnosis: { root_cause: "Connection exhaustion", confidence: "high", confidence_explanation: "Multiple source signals", supporting_evidence: ["ev-1"], contradictory_evidence: [], uncertainty: ["Synthetic dataset"] },
  evidence: [{ id: "ev-1", source_type: "logs", source_id: "log-1", timestamp: "2026-09-22T10:00:00Z", content: "<script>alert('untrusted')</script>", tool: "logs.search", relevance: 0.9, reference: "acme://logs/log-1", content_hash: "b".repeat(64), attributes: { host: "node-1" }, suspicious: true }],
  hypotheses: [{ id: "hyp-1", title: "Pool exhaustion", score: 0.9, test_summary: "Matched", required_signals: ["pool"], missing_signals: [], supporting_evidence: ["ev-1"], contradictory_evidence: [] }],
  timeline: [{ timestamp: "2026-09-22T10:00:00Z", summary: "Error spike", evidence_ids: ["ev-1"] }],
  tool_calls: [{ id: "call-1", tool: "logs.search", started_at: "2026-09-22T10:00:00Z", duration_ms: 4, attempts: 2, cache_hit: false, evidence_ids: ["ev-1"], error: null, error_code: null }],
  trace: [{ timestamp: "2026-09-22T10:00:00Z", stage: "diagnosis", summary: "Matched supported hypothesis", evidence_ids: ["ev-1"] }],
});
const tick = () => new Promise((done) => setImmediate(done));

test("HTML and CSS are external, local-only, accessible entry points", () => {
  assert.match(html, /src="\/static\/app.js"/);
  assert.match(html, /href="\/static\/app.css"/);
  assert.doesNotMatch(html, /<style\b|\sstyle=|\son[a-z]+\s*=|<script(?![^>]*\bsrc=)/i);
  assert.doesNotMatch(source, /innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage/);
  assert.doesNotMatch(html + css, /https?:\/\/|@import|url\(/i);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
  assert.equal(ids.length, new Set(ids).size);
  for (const match of html.matchAll(/for="([^"]+)"/g)) assert.ok(ids.includes(match[1]));
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /max-width: 720px/);
  assert.match(html, /Skip to workspace/);
});

test("formatters preserve real zeros and do not invent missing metrics", () => {
  const { app } = harness();
  assert.equal(app.percent(0), "0.0%");
  assert.equal(app.percent(undefined), "Not measured");
  assert.equal(app.money(0), "$0.000000");
  assert.equal(app.money(null), "Not measured");
  assert.equal(app.duration(investigation()), "42s");
  assert.equal(app.duration({ ...investigation(), status: "collecting" }, Date.parse("2026-09-22T10:01:01Z")), "1m 1s");
  assert.equal(app.duration({ created_at: "bad" }), "Not available");
});

test("references allow HTTPS only, and evidence search includes provenance", () => {
  const { app } = harness();
  for (const url of ["javascript:alert(1)", "http://example.test", "//example.test", "acme://logs/1", "data:text/html,x", "https://user:pass@example.test", "not a URL"]) assert.equal(app.safeReference(url), null);
  assert.equal(app.safeReference("https://example.test/source"), "https://example.test/source");
  assert.equal(app.filterEvidence(investigation().evidence, "NODE-1").length, 1);
  assert.equal(app.filterEvidence(investigation().evidence, "absent").length, 0);
});

test("JSON errors include validation details; requests are same origin and bearer authenticated", async () => {
  const h = harness(async () => Response.json({ detail: [{ loc: ["body", "description"], msg: "Too short" }] }, { status: 422 }));
  h.app.state.token = "memory-token";
  await assert.rejects(h.app.api("/investigations", { method: "POST", body: { description: "x" } }), /body.description: Too short/);
  assert.equal(h.calls[0].url, "/api/v1/investigations");
  assert.equal(h.calls[0].options.headers.Authorization, "Bearer memory-token");
  assert.equal(h.calls[0].options.credentials, "omit");
  assert.equal(h.calls[0].options.cache, "no-store");
  assert.equal(h.timers.size, 0);
});

test("non-JSON HTTP and network failures surface explicit errors", async () => {
  const h = harness(async () => new Response("unavailable", { status: 503 }));
  h.app.state.token = "token";
  await assert.rejects(h.app.api("/investigations"), /HTTP 503/);
  const offline = harness(async () => { throw new TypeError("network"); });
  offline.app.state.token = "token";
  await assert.rejects(offline.app.api("/investigations"), /Unable to reach the server/);
});

test("demo availability follows config; demo login posts role then verifies /me", async () => {
  const h = harness(async (url) => {
    if (url.endsWith("/config")) return Response.json({ demo_login: true, data_mode: "synthetic" });
    if (url.endsWith("/auth/demo")) return Response.json({ access_token: "demo-token", token_type: "bearer" });
    if (url.endsWith("/me")) return Response.json(principal("approver"));
    return Response.json([]);
  });
  await h.app.loadConfig();
  assert.equal(h.$("demo-login").hidden, false);
  assert.match(h.$("mode-copy").textContent, /SYNTHETIC MODE/);
  await h.app.login("approver");
  await tick();
  const auth = h.calls.find((call) => call.url.endsWith("/auth/demo"));
  assert.deepEqual(JSON.parse(auth.options.body), { role: "approver" });
  assert.equal(auth.options.headers.Authorization, undefined);
  assert.equal(h.calls.find((call) => call.url.endsWith("/me")).options.headers.Authorization, "Bearer demo-token");
  assert.equal(h.app.state.user.role, "approver");
  assert.equal(h.$("create-investigation").disabled, true);
  assert.equal(h.$("login-panel").hidden, true);
  const disabled = harness(async () => Response.json({ demo_login: false, data_mode: "synthetic" }));
  await disabled.app.loadConfig();
  assert.equal(disabled.$("demo-login").hidden, true);
});

test("pasted tokens are cleared from the input and never sent to demo login", async () => {
  const h = harness(async (url) => Response.json(url.endsWith("/me") ? principal() : []));
  h.$("token").value = "Bearer pasted-token";
  await h.app.login();
  await tick();
  assert.equal(h.app.state.token, "pasted-token");
  assert.equal(h.$("token").value, "");
  assert.equal(h.calls[0].url, "/api/v1/me");
  assert.ok(!h.calls.some((call) => call.url.endsWith("/auth/demo")));
});

test("401 clears identity, halts polling and retains in-memory navigation without auto-login", async () => {
  const h = harness(async () => Response.json({ detail: "Expired" }, { status: 401 }));
  Object.assign(h.app.state, { token: "expired", user: principal(), selected: "investigation-1", view: "approvals" });
  await assert.rejects(h.app.api("/approvals"), { name: "AbortError" });
  assert.equal(h.app.state.token, "");
  assert.equal(h.app.state.user, null);
  assert.equal(h.app.state.selected, "investigation-1");
  assert.equal(h.app.state.view, "approvals");
  assert.equal(h.$("login-panel").hidden, false);
  assert.match(h.$("login-message").textContent, /expired/);
  h.app.refresh();
  assert.equal(h.calls.length, 1);
  assert.equal(h.timers.size, 0);
});

test("responses from an old identity cannot populate a new session", async () => {
  let complete;
  const h = harness(() => new Promise((done) => { complete = done; }));
  h.app.state.token = "old-token";
  const pending = h.app.api("/investigations");
  h.app.signOut();
  h.app.state.token = "new-token";
  complete(Response.json([investigation()]));
  await assert.rejects(pending, { name: "AbortError" });
  assert.equal(h.app.state.token, "new-token");
  assert.equal(h.app.state.investigations, null);
});

test("polls do not overlap and preserve incident/search inputs", async () => {
  let complete;
  const h = harness(() => new Promise((done) => { complete = done; }));
  Object.assign(h.app.state, { token: "token", user: principal(), scenarios: [] });
  h.$("incident").value = "Unsubmitted draft";
  h.$("evidence-search").value = "source-filter";
  const first = h.app.poll();
  await h.app.poll();
  h.app.refresh();
  assert.equal(h.calls.length, 1);
  complete(Response.json([]));
  await first;
  assert.equal(h.app.state.polling, false);
  assert.equal(h.$("incident").value, "Unsubmitted draft");
  assert.equal(h.$("evidence-search").value, "source-filter");
  const timer = [...h.timers.values()][0];
  assert.equal(timer.delay, 0);
  h.app.state.refreshPending = false;
  const next = h.app.poll();
  complete(Response.json([]));
  await next;
  assert.ok([...h.timers.values()].some((entry) => entry.delay === 3000));
});

test("unchanged dynamic regions retain their nodes and expanded provenance", () => {
  const h = harness();
  h.app.state.detail = investigation();
  h.app.state.selected = "investigation-1";
  h.app.renderDetail();
  const card = h.$("evidence-ev-1"), provenance = h.$("provenance-ev-1");
  provenance.open = true;
  h.app.renderDetail();
  assert.equal(h.$("evidence-ev-1"), card);
  assert.equal(h.$("provenance-ev-1").open, true);
  h.app.state.detail.evidence[0].attributes.host = "updated-host";
  h.app.renderEvidence();
  assert.equal(h.$("provenance-ev-1").open, true);
});

test("detail renders every contract surface with untrusted text inert", () => {
  const h = harness();
  h.app.state.detail = investigation();
  h.app.state.selected = "investigation-1";
  h.app.renderDetail();
  assert.match(h.$("diagnosis").textContent, /Connection exhaustion/);
  assert.match(h.$("diagnosis").textContent, /not a calibrated probability/);
  assert.match(h.$("hypotheses").textContent, /Pool exhaustion/);
  assert.match(h.$("timeline").textContent, /Error spike/);
  assert.match(h.$("tool-calls").textContent, /logs.search/);
  assert.match(h.$("decision-trace").textContent, /Matched supported hypothesis/);
  assert.match(h.$("actions").textContent, /application/);
  assert.match(h.$("evidence-list").textContent, /<script>alert/);
  assert.equal(h.$("evidence-list").querySelectorAll("script").length, 0);
  assert.equal(h.$("evidence-list").querySelectorAll("a").length, 0);
  assert.match(h.$("evidence-list").textContent, /Suspicious source content/);
  assert.match(h.$("evidence-list").textContent, /bbbbbbbb/);
  assert.equal(h.$("download-report").disabled, false);
});

test("proposal restrictions enforce role, requester, resource and tool scope", () => {
  const { app } = harness(), item = proposal();
  assert.match(app.approvalRestriction(principal(), item), /Approver or admin/);
  assert.match(app.approvalRestriction(principal("admin", "demo-operator"), item), /own action/);
  assert.equal(app.approvalRestriction(principal("approver"), item), "");
  assert.match(app.approvalRestriction({ ...principal("approver"), services: ["other"] }, item), /service scope/);
  assert.match(app.approvalRestriction({ ...principal("approver"), tools: [] }, item), /tool scope/);
});

test("review survives identical polling, but resets when proposal digest changes", () => {
  const h = harness();
  Object.assign(h.app.state, { user: principal("approver"), approvals: [proposal()] });
  h.app.renderApprovals();
  const id = "approval-investigation-1-action-1";
  const checkbox = h.$(`${id}-review`);
  assert.equal(h.$(`${id}-approve`).disabled, true);
  checkbox.checked = true; checkbox.dispatch("change");
  assert.equal(h.$(`${id}-approve`).disabled, false);
  assert.equal(h.$(`${id}-reject`).disabled, false);
  h.app.renderApprovals();
  assert.equal(h.$(`${id}-review`), checkbox);
  h.app.state.approvals = [proposal("c".repeat(64))];
  h.app.renderApprovals();
  assert.equal(h.$(`${id}-review`).checked, false);
  assert.equal(h.$(`${id}-approve`).disabled, true);
  assert.equal(h.app.state.reviewed.size, 0);
});

test("decisions require review and send the exact digest and decision", async () => {
  const h = harness(async (url) => Response.json(url.endsWith("/decision") ? investigation() : []));
  const item = proposal();
  Object.assign(h.app.state, { token: "token", user: principal("approver"), view: "approvals", approvals: [item] });
  await h.app.decide(item, "approve");
  assert.equal(h.calls.length, 0);
  h.app.state.reviewed.add(h.app.approvalKey(item));
  await h.app.decide(item, "reject");
  await tick();
  const call = h.calls.find((entry) => entry.url.endsWith("/decision"));
  assert.equal(call.url, "/api/v1/investigations/investigation-1/actions/action-1/decision");
  assert.deepEqual(JSON.parse(call.options.body), { decision: "reject", expected_digest: item.digest });
  assert.equal(h.app.state.reviewed.size, 0);
  assert.match(h.$("notice").textContent, /Rejection recorded/);
});

test("conflicts require fresh explicit review and display the API error", async () => {
  const item = proposal();
  const h = harness(async (url) => url.endsWith("/decision")
    ? Response.json({ detail: "Proposal changed" }, { status: 409 })
    : Response.json([item]));
  Object.assign(h.app.state, { token: "token", user: principal("approver"), view: "approvals", approvals: [item] });
  h.app.state.reviewed.add(h.app.approvalKey(item));
  await h.app.decide(item, "approve");
  await tick();
  assert.match(h.$("notice").textContent, /Proposal changed/);
  assert.equal(h.app.state.reviewed.size, 0);
  assert.equal(h.$("approval-investigation-1-action-1-approve").disabled, true);
});

test("investigation creation uses UUID idempotency and preserves key on uncertain retries", async () => {
  const h = harness(async () => { throw new TypeError("network failure"); });
  Object.assign(h.app.state, { token: "token", user: principal(), scenarios: [{ id: "INC-001" }] });
  h.$("scenario").value = "INC-001";
  h.$("incident").value = "Investigate checkout service errors";
  const event = { preventDefault() {} };
  await h.app.createInvestigation(event);
  await h.app.createInvestigation(event);
  const first = h.calls[0].options;
  assert.match(first.headers["Idempotency-Key"], /^[0-9a-f-]{36}$/);
  assert.equal(first.headers["Idempotency-Key"], h.calls[1].options.headers["Idempotency-Key"]);
  assert.deepEqual(JSON.parse(first.body), { description: "Investigate checkout service errors", scenario_id: "INC-001" });
  assert.equal(h.$("incident").value, "Investigate checkout service errors");
  h.$("incident").value = "A changed description for another attempt";
  await h.app.createInvestigation(event);
  assert.notEqual(first.headers["Idempotency-Key"], h.calls[2].options.headers["Idempotency-Key"]);
});

test("evaluations use persisted actual metric names, history, zeros, and empty instructions", () => {
  const h = harness();
  h.app.state.evaluations = [];
  h.app.renderEvaluations();
  assert.match(h.$("evaluations-content").textContent, /ops evaluate --persist/);
  assert.equal(h.$("evaluations-content").querySelectorAll("table").length, 0);
  h.app.state.evaluations = [{
    id: "run-1", timestamp: "2026-09-22T10:00:00Z", scenario_count: 12, passed: false,
    metrics: { root_cause_accuracy: .75, evidence_supported_conclusion_rate: .8, tool_success_rate: .9, latency_p95_ms: 42, cost_per_investigation_usd: 0, unauthorized_action_rate: 0, approval_violation_rate: 0 },
    regressions: ["Root cause regression"], limitations: ["Synthetic only"],
  }];
  h.app.renderEvaluations();
  const content = h.$("evaluations-content").textContent;
  for (const value of ["75.0%", "80.0%", "90.0%", "42 ms", "$0.000000", "Root cause regression", "Synthetic only", "approval_violation_rate"]) assert.ok(content.includes(value), value);
  assert.equal(h.$("evaluations-content").querySelectorAll("tbody")[0].children.length, 1);
});

test("report download uses authenticated fetch and an actual server blob", async () => {
  const h = harness(async () => new Response("# Server report", { headers: { "Content-Type": "text/markdown" } }));
  h.app.state.token = "report-token";
  const blob = await h.app.api("/investigations/investigation-1/report", { format: "blob" });
  assert.equal(await blob.text(), "# Server report");
  assert.equal(h.calls[0].options.headers.Authorization, "Bearer report-token");
  assert.equal(h.calls[0].options.headers.Accept, "text/markdown");
});

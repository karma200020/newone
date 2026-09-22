"use strict";

// All credentials and workspace selections live only for the lifetime of this page.
const state = {
  token: "", user: null, epoch: 0, view: "investigations", selected: null,
  config: null, scenarios: null, investigations: null, detail: null,
  approvals: null, evaluations: null, reviewed: new Set(), decisions: new Set(),
  timer: null, polling: false, refreshPending: false, controllers: new Set(),
  authenticating: false, creating: false, downloading: false, submission: null,
};
const signatures = new Map();
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
const text = (value) => value === null || value === undefined ? "Not available" : String(value);
const humanize = (value) => text(value).replaceAll("_", " ");
const segment = (value) => encodeURIComponent(value);
const domId = (prefix, value) => `${prefix}-${encodeURIComponent(value)}`;
const finite = (value) => typeof value === "number" && Number.isFinite(value);

function element(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = text(content);
  return node;
}
function append(parent, ...children) {
  for (const child of children) if (child) parent.append(child);
  return parent;
}
function message(id, content, error = true) {
  const node = $(id);
  node.textContent = content || "";
  node.hidden = !content;
  node.classList.toggle("error", error);
}
function empty(title, description, command) {
  const node = element("div", "empty");
  append(node, element("strong", "", title), element("p", "", description));
  if (command) node.append(element("code", "", command));
  return node;
}
function button(label, className, callback, id) {
  const node = element("button", className, label);
  node.type = "button";
  if (id) node.id = id;
  node.addEventListener("click", callback);
  return node;
}
function badge(value) {
  const good = ["diagnosed", "approved", "succeeded", "pass", "high confidence"];
  const bad = ["failed", "rejected", "fail"];
  const warn = ["proposed", "needs_clarification", "unverified", "high risk", "suspicious"];
  const tone = good.includes(value) ? "good" : bad.includes(value) ? "bad" : warn.includes(value) ? "warn" : "";
  return element("span", `badge ${tone}`, humanize(value));
}
function formatDate(value) {
  if (!value) return "Not available";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Invalid timestamp" : date.toLocaleString(undefined, {
    month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
}
function duration(investigation, now = Date.now()) {
  const terminal = ["diagnosed", "needs_clarification", "failed"].includes(investigation.status);
  const end = terminal ? Date.parse(investigation.updated_at) : now;
  const start = Date.parse(investigation.created_at);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return "Not available";
  const seconds = Math.floor((end - start) / 1000);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}
function percent(value) { return finite(value) ? `${(value * 100).toFixed(1)}%` : "Not measured"; }
function numeric(value, unit = "") { return finite(value) ? `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}${unit}` : "Not measured"; }
function money(value) { return finite(value) ? `$${value.toFixed(6)}` : "Not measured"; }
function safeReference(value) {
  if (typeof value !== "string" || !/^https:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}
function approvalRestriction(user, item) {
  if (!user || !["approver", "admin"].includes(user.role)) return "Approver or admin access is required. Switch identity to the separate demo approver, or paste an approver token.";
  if (user.subject === item.requested_by) return "You requested this investigation and cannot decide on your own action. Switch to a different approver identity.";
  if (!user.services.includes("*") && !user.services.includes(item.action.service)) return "This resource is outside your service scope.";
  if (!user.tools.includes("*") && !user.tools.includes(`actions.${item.action.kind}`)) return "This action is outside your tool scope.";
  return "";
}
function errorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (Array.isArray(payload?.detail)) return payload.detail.map((item) => `${(item.loc || []).join(".")}: ${item.msg || "Invalid input"}`).join("; ");
  if (typeof payload?.message === "string") return payload.message;
  return fallback;
}
class ApiError extends Error {
  constructor(message, status = 0) { super(message); this.name = "ApiError"; this.status = status; }
}
function staleError() { return new DOMException("The identity changed.", "AbortError"); }
function ignored(error) { return error.name === "AbortError"; }

async function api(path, { method = "GET", body, headers = {}, publicRequest = false, format = "json" } = {}) {
  const epoch = state.epoch;
  const token = state.token;
  if (!publicRequest && !token) throw new ApiError("Connect an identity to continue.", 401);
  const controller = new AbortController();
  state.controllers.add(controller);
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    let response;
    try {
      response = await fetch(`${API}${path}`, {
        method, signal: controller.signal, cache: "no-store", credentials: "omit",
        headers: {
          Accept: format === "blob" ? "text/markdown" : "application/json",
          ...(!publicRequest ? { Authorization: `Bearer ${token}` } : {}),
          ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...headers,
        },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
    } catch (error) {
      if (state.epoch !== epoch) throw staleError();
      if (error.name === "AbortError") throw new ApiError("Request timed out. Check the server and retry.");
      throw new ApiError("Unable to reach the server. Check your connection and retry.");
    }
    if (state.epoch !== epoch) throw staleError();
    if (response.status === 401 && !publicRequest) {
      signOut("Your session expired or the token is invalid. Connect again to continue.");
      throw staleError();
    }
    if (!response.ok) {
      let payload = null;
      try { payload = await response.json(); } catch { /* Non-JSON errors use the explicit HTTP status. */ }
      if (state.epoch !== epoch) throw staleError();
      throw new ApiError(errorMessage(payload, `Request failed (HTTP ${response.status}).`), response.status);
    }
    let result;
    try {
      result = format === "blob" ? await response.blob() : response.status === 204 ? null : await response.json();
    } catch {
      if (state.epoch !== epoch) throw staleError();
      throw new ApiError("The server returned an unreadable response. Retry or inspect the API.");
    }
    if (state.epoch !== epoch) throw staleError();
    return result;
  } finally {
    clearTimeout(timeout);
    state.controllers.delete(controller);
  }
}

// Only changed data regions are replaced. Stable forms and search inputs never are.
function renderRegion(id, data, build) {
  const signature = JSON.stringify(data);
  if (signatures.get(id) === signature) return;
  const host = $(id);
  const focused = host.contains(document.activeElement) ? document.activeElement.id : null;
  const opened = new Set(Array.from(host.querySelectorAll("details[open]"), (node) => node.id));
  const scrollTop = host.scrollTop;
  host.replaceChildren(...build());
  for (const node of host.querySelectorAll("details")) if (opened.has(node.id)) node.open = true;
  if (focused && $(focused)) $(focused).focus({ preventScroll: true });
  host.scrollTop = scrollTop;
  signatures.set(id, signature);
}
function field(label, value, mono = false) {
  return append(element("div", "field"), element("span", "field-label", label), element("div", `field-value${mono ? " mono" : ""}`, value));
}
function values(label, items, emptyLabel = "None reported") {
  const node = element("div", "field");
  node.append(element("span", "field-label", label));
  if (!items?.length) node.append(element("p", "help", emptyLabel));
  else {
    const list = element("ul", "value-list");
    for (const item of items) list.append(element("li", "", item));
    node.append(list);
  }
  return node;
}
function evidenceRefs(ids, interactive = true) {
  const node = element("div", "references");
  if (!ids?.length) node.append(element("span", "help", "No evidence references"));
  for (const id of ids || []) {
    if (!interactive) node.append(element("code", "reference", id));
    else node.append(button(id, "reference", () => {
      $("evidence-search").value = "";
      renderEvidence();
      const card = $(domId("evidence", id));
      if (card) { card.scrollIntoView({ block: "center", behavior: "instant" }); card.focus({ preventScroll: true }); }
      else message("notice", `Evidence ${id} is not present in this investigation.`, true);
    }));
  }
  return node;
}
function disclosure(id, title, children) {
  const node = element("details");
  node.id = id;
  append(node, element("summary", "", title), ...children);
  return node;
}
function actionContent(action, interactive = true) {
  const node = element("div", "action-card");
  append(node, append(element("div", "card-top"), element("h4", "", humanize(action.kind)), badge(action.status)));
  append(node, field("Action ID", action.id, true), field("Resource / service", action.service, true),
    field("Environment", action.environment), field("Risk", action.risk), field("Reason", action.reason),
    field("Exact parameters", JSON.stringify(action.parameters, null, 2), true), field("Evidence", ""));
  node.append(evidenceRefs(action.evidence_ids, interactive));
  node.append(field("Verification", action.verification || "Not yet verified"));
  return node;
}

async function loadConfig() {
  $("config-retry").hidden = true;
  try {
    const config = await api("/config", { publicRequest: true });
    if (typeof config?.demo_login !== "boolean" || typeof config?.data_mode !== "string") throw new ApiError("Invalid server configuration response.");
    state.config = config;
    $("demo-login").hidden = !config.demo_login;
    $("mode-copy").textContent = config.data_mode === "synthetic"
      ? "SYNTHETIC MODE - Seeded incident data and isolated actions. No live infrastructure is changed."
      : `Server data mode: ${config.data_mode}. Verify the environment before taking action.`;
  } catch (error) {
    if (ignored(error)) return;
    $("mode-copy").textContent = `Configuration unavailable: ${error.message} Demo login is unavailable until configuration is loaded.`;
    $("demo-login").hidden = true;
    $("config-retry").hidden = false;
  }
}
function setLoginBusy(busy) {
  state.authenticating = busy;
  $("login-submit").disabled = busy;
  $("token").disabled = busy;
  for (const node of document.querySelectorAll("[data-demo-role]")) node.disabled = busy;
  $("login-submit").textContent = busy ? "Connecting..." : "Connect workspace";
}
async function login(role) {
  if (state.authenticating) return;
  message("login-message", "");
  const pasted = $("token").value.trim().replace(/^Bearer\s+/i, "");
  if (!role && !pasted) { message("login-message", "Paste an access token to connect."); return; }
  setLoginBusy(true);
  try {
    if (role) {
      const credentials = await api("/auth/demo", { method: "POST", body: { role }, publicRequest: true });
      if (typeof credentials?.access_token !== "string" || !credentials.access_token) throw new ApiError("Demo login did not return an access token.");
      state.token = credentials.access_token;
    } else state.token = pasted;
    $("token").value = "";
    const user = await api("/me");
    if (!user?.subject || !user.role || !Array.isArray(user.services) || !Array.isArray(user.tools)) throw new ApiError("The server returned an invalid identity.");
    state.user = user;
    $("identity-name").textContent = user.subject;
    $("identity-role").textContent = `${user.role} / ${user.tenant_id}`;
    $("sign-out").hidden = false;
    $("login-panel").hidden = true;
    $("authenticated-workspace").hidden = false;
    updateCreatePermission();
    setView(state.view, true);
  } catch (error) {
    if (!ignored(error)) {
      state.token = "";
      message("login-message", error.message);
    }
  } finally { setLoginBusy(false); }
}
function signOut(reason = "") {
  state.epoch += 1;
  clearTimeout(state.timer);
  state.timer = null;
  for (const controller of state.controllers) controller.abort();
  state.token = "";
  state.user = null;
  state.scenarios = null;
  state.investigations = null;
  state.detail = null;
  state.approvals = null;
  state.evaluations = null;
  state.reviewed.clear();
  state.decisions.clear();
  state.refreshPending = false;
  state.submission = null;
  state.creating = false;
  state.downloading = false;
  signatures.clear();
  $("token").value = "";
  $("incident").value = "";
  $("incident-count").textContent = "0 / 4,000";
  $("evidence-search").value = "";
  $("scenario").replaceChildren(element("option", "", "Loading scenarios..."));
  $("scenario").disabled = true;
  $("scenario-description").textContent = "Scenarios are supplied by the authenticated API.";
  $("download-report").textContent = "Download report";
  for (const region of ["detail-summary", "diagnosis", "actions", "timeline", "hypotheses", "evidence-list", "tool-calls", "decision-trace"]) $(region).replaceChildren();
  $("identity-name").textContent = "Not connected";
  $("identity-role").textContent = "Authenticate to continue";
  $("sign-out").hidden = true;
  $("login-panel").hidden = false;
  $("authenticated-workspace").hidden = true;
  $("investigation-detail").hidden = true;
  $("investigation-list").replaceChildren(empty("Loading investigations", "Waiting for an authenticated update."));
  $("approvals-list").replaceChildren(empty("Loading approvals", "Waiting for an authenticated update."));
  $("evaluations-content").replaceChildren(empty("Loading evaluations", "Waiting for an authenticated update."));
  $("investigation-count").textContent = "Not loaded";
  $("last-sync").textContent = "Waiting for first update";
  for (const id of ["notice", "refresh-error", "create-message"]) message(id, "");
  setConnection("Not connected", false);
  message("login-message", reason);
  $("token").focus();
}
function setConnection(label, online) {
  $("connection-status").textContent = label;
  $("connection-dot").classList.toggle("online", online);
}
function setView(view, focus = false) {
  state.view = view;
  for (const node of document.querySelectorAll("[data-view]")) {
    const active = node.dataset.view === view;
    node.classList.toggle("active", active);
    if (active) node.setAttribute("aria-current", "page"); else node.removeAttribute("aria-current");
  }
  for (const name of ["investigations", "approvals", "evaluations"]) $(`view-${name}`).hidden = name !== view;
  message("refresh-error", "");
  if (state.user) {
    if (focus) $(`${view}-title`).focus({ preventScroll: true });
    refresh();
  }
}
function refresh() {
  clearTimeout(state.timer);
  if (!state.user) return;
  if (state.polling) { state.refreshPending = true; return; }
  void poll();
}
async function poll() {
  if (state.polling || !state.user) return;
  state.polling = true;
  state.refreshPending = false;
  const epoch = state.epoch, view = state.view, selected = state.selected;
  const errors = [];
  const current = () => epoch === state.epoch && state.user && view === state.view;
  async function load(path, apply, failure) {
    try {
      const data = await api(path);
      if (current()) apply(data);
    } catch (error) {
      if (ignored(error) || !current()) return;
      errors.push(error.message);
      if (failure) failure(error);
    }
  }
  const tasks = [];
  if (view === "investigations") {
    if (!state.scenarios) tasks.push(load("/scenarios", (data) => { state.scenarios = requireArray(data); renderScenarios(); }, () => {
      $("scenario-description").textContent = "Scenarios could not be loaded. Retrying automatically; you can also refresh data.";
    }));
    tasks.push(load("/investigations", (data) => { state.investigations = requireArray(data); renderInvestigations(); }, () => {
      if (state.investigations === null) $("investigation-list").replaceChildren(empty("Unable to load investigations", "Use Refresh data to retry."));
    }));
    if (selected) tasks.push(load(`/investigations/${segment(selected)}`, (data) => {
      if (selected !== state.selected) return;
      state.detail = data;
      renderDetail();
    }, (error) => {
      if (selected !== state.selected) return;
      if (!state.detail || [403, 404].includes(error.status)) {
        state.detail = null;
        $("investigation-detail").hidden = true;
      }
    }));
  } else if (view === "approvals") {
    tasks.push(load("/approvals", (data) => { state.approvals = requireArray(data); renderApprovals(); }, () => {
      if (state.approvals === null) $("approvals-list").replaceChildren(empty("Unable to load approvals", "Use Refresh data to retry."));
    }));
  } else {
    tasks.push(load("/evaluations", (data) => { state.evaluations = requireArray(data); renderEvaluations(); }, () => {
      if (state.evaluations === null) $("evaluations-content").replaceChildren(empty("Unable to load evaluations", "Use Refresh data to retry."));
    }));
  }
  try {
    await Promise.all(tasks);
    if (current()) {
      message("refresh-error", errors.length ? `Update failed: ${[...new Set(errors)].join(" ")} Displayed data may be stale. Retrying every 3 seconds.` : "");
      setConnection(errors.length ? "Update failed / retrying" : "Connected / polling", !errors.length);
      if (!errors.length) $("last-sync").textContent = `Last synced ${new Date().toLocaleTimeString()}`;
    }
  } finally {
    state.polling = false;
    if (state.user) state.timer = setTimeout(() => void poll(), state.refreshPending ? 0 : 3000);
  }
}
function requireArray(value) {
  if (!Array.isArray(value)) throw new ApiError("Expected a list from the API.");
  return value;
}
function renderScenarios() {
  const select = $("scenario"), previous = select.value;
  select.replaceChildren();
  const placeholder = element("option", "", state.scenarios.length ? "Choose an incident scenario" : "No scenarios available");
  placeholder.value = "";
  select.append(placeholder);
  for (const item of state.scenarios) {
    const option = element("option", "", `${item.id} / ${item.service}`);
    option.value = item.id;
    select.append(option);
  }
  if (state.scenarios.some((item) => item.id === previous)) select.value = previous;
  select.disabled = !state.scenarios.length;
  scenarioDescription();
  updateCreatePermission();
}
function scenarioDescription() {
  const item = state.scenarios?.find((scenario) => scenario.id === $("scenario").value);
  $("scenario-description").textContent = item?.description || "Choose a scenario to see its supplied incident context.";
}
function updateCreatePermission() {
  const allowed = ["operator", "admin"].includes(state.user?.role);
  $("create-investigation").disabled = !allowed || !state.scenarios?.length || state.creating;
  $("create-investigation").textContent = state.creating ? "Submitting..." : "Start investigation";
  $("create-permission").textContent = allowed
    ? "Queued work is processed by the server worker. Actions require a separate identity's review."
    : "Operator or admin access is required to start investigations. Switch identity to an operator.";
}
async function createInvestigation(event) {
  event.preventDefault();
  if (state.creating || !state.user || !["operator", "admin"].includes(state.user.role)) return;
  const description = $("incident").value.trim(), scenario_id = $("scenario").value;
  if (description.length < 10 || description.length > 4000 || !state.scenarios?.some((item) => item.id === scenario_id)) {
    message("create-message", "Choose a scenario and enter an incident description of 10-4,000 characters."); return;
  }
  if (!globalThis.crypto?.randomUUID) { message("create-message", "A secure browser context with crypto.randomUUID is required. Use HTTPS or localhost."); return; }
  const body = { description, scenario_id }, bodyKey = JSON.stringify(body), epoch = state.epoch;
  // Retrying an unchanged request reuses its key, including after a network timeout.
  if (state.submission?.bodyKey !== bodyKey) state.submission = { bodyKey, key: crypto.randomUUID() };
  state.creating = true;
  updateCreatePermission();
  message("create-message", "");
  try {
    const result = await api("/investigations", { method: "POST", body, headers: { "Idempotency-Key": state.submission.key } });
    if (!result?.id) throw new ApiError("The server did not return an investigation ID.");
    state.submission = null;
    if ($("incident").value.trim() === description && $("scenario").value === scenario_id) {
      $("incident").value = "";
      $("incident-count").textContent = "0 / 4,000";
    }
    message("notice", `Investigation ${result.id} queued. The server worker will update its progress.`, false);
    selectInvestigation(result.id);
  } catch (error) { if (!ignored(error)) message("create-message", error.message); }
  finally { if (epoch === state.epoch) { state.creating = false; updateCreatePermission(); } }
}
function renderInvestigations() {
  $("investigation-count").textContent = `${state.investigations.length} total`;
  renderRegion("investigation-list", [state.investigations, state.selected], () => {
    if (!state.investigations.length) return [empty("No investigations yet", "Choose a scenario and describe an incident to begin.")];
    return state.investigations.map((item) => {
      const node = button("", `queue-item${item.id === state.selected ? " selected" : ""}`, () => selectInvestigation(item.id), domId("investigation", item.id));
      node.setAttribute("aria-pressed", String(item.id === state.selected));
      append(node, append(element("div", "queue-top"), element("span", "queue-id mono", `${item.scenario_id} / ${item.id.slice(0, 8)}`), badge(item.status)),
        element("p", "queue-description", item.description));
      const elapsed = element("span", "", `Duration ${duration(item)}`);
      elapsed.dataset.duration = item.id;
      append(node, append(element("div", "queue-meta"), element("span", "", item.affected_services?.join(", ") || "Service pending"), elapsed, element("span", "", formatDate(item.created_at))));
      const actions = item.actions || [];
      append(node, element("p", "queue-action", actions.length
        ? actions.map((action) => `${humanize(action.kind)} / ${humanize(action.status)}`).join(" | ")
        : "Current action: no proposal"));
      return node;
    });
  });
  for (const node of $("investigation-list").querySelectorAll("[data-duration]")) {
    const item = state.investigations.find((entry) => entry.id === node.dataset.duration);
    node.textContent = `Duration ${duration(item)}`;
  }
}
function selectInvestigation(id) {
  const changed = id !== state.selected;
  state.selected = id;
  if (changed) {
    state.detail = null;
    $("evidence-search").value = "";
    for (const region of ["detail-summary", "diagnosis", "actions", "timeline", "hypotheses", "evidence-list", "tool-calls", "decision-trace"]) {
      signatures.delete(region);
      $(region).replaceChildren(element("p", "empty", "Loading investigation details..."));
    }
  }
  $("investigation-detail").hidden = false;
  $("detail-title").textContent = "Investigation detail";
  $("detail-id").textContent = id;
  $("download-report").disabled = true;
  if (state.investigations) renderInvestigations();
  setView("investigations");
  $("detail-title").focus();
}
function renderDetail() {
  const data = state.detail;
  if (!data || data.id !== state.selected) return;
  $("investigation-detail").hidden = false;
  $("detail-title").textContent = `${data.scenario_id} / Investigation detail`;
  $("detail-id").textContent = data.id;
  $("download-report").disabled = data.report === null || data.report === undefined || state.downloading;
  renderRegion("detail-summary", [data.status, data.description, data.severity, data.affected_services, data.warnings, data.usage, duration(data)], () => {
    const strip = append(element("div", "summary-strip"), badge(data.status),
      element("span", "", `Severity: ${data.severity}`), element("span", "", `Duration: ${duration(data)}`),
      element("span", "", `Services: ${data.affected_services.join(", ") || "Pending"}`),
      element("span", "", `Cost: ${money(data.usage?.cost_usd)}`));
    const nodes = [element("p", "detail-description", data.description), strip];
    if (data.warnings.length) nodes.push(values("Investigation warnings", data.warnings));
    return nodes;
  });
  renderRegion("diagnosis", data.diagnosis, () => {
    const d = data.diagnosis;
    if (!d) return [empty("Diagnosis not available", data.status === "failed" ? "The investigation failed. Inspect its warnings and decision trace." : "Evidence collection and hypothesis testing must finish first.")];
    return [
      badge(`${d.confidence} confidence`), element("h4", "diagnosis-title", d.root_cause),
      element("p", "prose", d.confidence_explanation),
      element("p", "help", "Confidence is qualitative, not a calibrated probability."),
      field("Supporting evidence", ""), evidenceRefs(d.supporting_evidence),
      field("Contradictory evidence", ""), evidenceRefs(d.contradictory_evidence),
      values("Uncertainty", d.uncertainty),
    ];
  });
  renderRegion("actions", data.actions, () => data.actions.length
    ? [...data.actions.map((action) => actionContent(action)), button("Open approval queue", "text-button", () => setView("approvals", true), "detail-approvals")]
    : [empty("No actions proposed", "Proposals will appear here when the investigation produces them.")]);
  renderRegion("timeline", data.timeline, () => data.timeline.length ? data.timeline.map((entry) => {
    const node = element("div", "record"), time = element("time", "", formatDate(entry.timestamp));
    time.dateTime = entry.timestamp;
    return append(node, time, element("p", "", entry.summary), evidenceRefs(entry.evidence_ids));
  }) : [empty("No timeline yet", "Incident events will appear as evidence is collected.")]);
  renderRegion("hypotheses", data.hypotheses, () => data.hypotheses.length ? data.hypotheses.map((item) => disclosure(domId("hypothesis", item.id), item.title, [
    field("Score (not probability)", numeric(item.score)), field("Test summary", item.test_summary || "Not tested yet"),
    values("Required signals", item.required_signals), values("Missing signals", item.missing_signals),
    field("Supporting evidence", ""), evidenceRefs(item.supporting_evidence),
    field("Contradictory evidence", ""), evidenceRefs(item.contradictory_evidence),
  ])) : [empty("No hypotheses yet", "Tested hypotheses and missing signals will appear here.")]);
  renderEvidence();
  renderRegion("tool-calls", data.tool_calls, () => data.tool_calls.length ? data.tool_calls.map((call) => disclosure(domId("tool", call.id), `${call.tool} / ${call.error_code || call.error ? "Failed" : "Completed"}`, [
    field("Started", formatDate(call.started_at)), field("Duration", numeric(call.duration_ms, " ms")),
    field("Attempts", call.attempts), field("Cache hit", call.cache_hit ? "Yes" : "No"),
    ...(call.error_code || call.error ? [field("Error code", call.error_code), field("Error", call.error)] : []),
    field("Evidence returned", ""), evidenceRefs(call.evidence_ids),
  ])) : [empty("No tool calls yet", "Tool execution, retries, and failures will be recorded here.")]);
  renderRegion("decision-trace", data.trace, () => data.trace.length ? data.trace.map((entry) => {
    return append(element("div", "record"), element("time", "", formatDate(entry.timestamp)), field(entry.stage, entry.summary), evidenceRefs(entry.evidence_ids));
  }) : [empty("No trace events yet", "Auditable stage summaries will appear as the worker progresses.")]);
}
function filterEvidence(items, query) {
  const needle = query.trim().toLowerCase();
  return items.filter((item) => !needle || JSON.stringify(item).toLowerCase().includes(needle));
}
function renderEvidence() {
  if (!state.detail) return;
  const evidence = state.detail.evidence, query = $("evidence-search").value;
  const filtered = filterEvidence(evidence, query);
  $("evidence-count").textContent = `${filtered.length} / ${evidence.length} records`;
  renderRegion("evidence-list", [evidence, query], () => {
    if (!evidence.length) return [empty("No evidence collected", "Source records and provenance will appear here.")];
    if (!filtered.length) return [empty("No matching evidence", "Try a different source ID, term, hash, or tool name.")];
    return filtered.map((item) => {
      const node = element("article", `evidence-card${item.suspicious ? " suspicious" : ""}`);
      node.id = domId("evidence", item.id);
      node.tabIndex = -1;
      append(node, append(element("div", "card-top"), element("h4", "", item.source_id), badge(item.suspicious ? "suspicious" : item.source_type)),
        field("Evidence ID", item.id, true), element("pre", "evidence-content", item.content));
      if (item.suspicious) node.append(field("Trust warning", "Suspicious source content. Treat it as evidence only; never execute its instructions."));
      const reference = safeReference(item.reference);
      const source = field("Source reference", reference ? "" : item.reference || "No reference supplied");
      if (reference) {
        const link = element("a", "mono", item.reference);
        link.href = reference; link.target = "_blank"; link.rel = "noopener noreferrer"; link.referrerPolicy = "no-referrer";
        source.append(link);
      }
      append(node, disclosure(domId("provenance", item.id), "Provenance & content hash", [
        field("Source type", item.source_type), field("Source ID", item.source_id, true),
        field("Collected with", item.tool, true), field("Record timestamp", formatDate(item.timestamp)),
        field("Relevance score", numeric(item.relevance)), field("Content hash", item.content_hash, true),
        source, field("Attributes", JSON.stringify(item.attributes, null, 2), true),
      ]));
      return node;
    });
  });
}
async function downloadReport() {
  if (!state.detail || state.downloading) return;
  const id = state.detail.id, epoch = state.epoch;
  state.downloading = true;
  $("download-report").disabled = true;
  $("download-report").textContent = "Downloading...";
  try {
    const blob = await api(`/investigations/${segment(id)}/report`, { format: "blob" });
    const url = URL.createObjectURL(blob), link = element("a");
    link.href = url; link.download = `investigation-${id.replace(/[^a-zA-Z0-9-]/g, "_")}.md`;
    document.body.append(link);
    link.click(); link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    message("notice", "Report download started.", false);
  } catch (error) { if (!ignored(error)) message("notice", error.message); }
  finally {
    if (epoch === state.epoch) {
      state.downloading = false;
      $("download-report").textContent = "Download report";
      $("download-report").disabled = state.detail?.report === null || !state.detail;
    }
  }
}
const approvalKey = (item) => JSON.stringify([item.investigation_id, item.action.id, item.digest]);
function renderApprovals() {
  const validKeys = new Set(state.approvals.map(approvalKey));
  for (const key of state.reviewed) if (!validKeys.has(key)) state.reviewed.delete(key);
  renderRegion("approvals-list", [state.approvals, state.user, [...state.decisions]], () => {
    if (!state.approvals.length) return [empty("All clear. No pending proposals.", "Actions awaiting human review will appear here after an investigation proposes them.")];
    return state.approvals.map((item) => {
      const key = approvalKey(item), restriction = approvalRestriction(state.user, item), pending = state.decisions.has(key);
      const node = element("article", "panel approval-card");
      const prefix = domId("approval", `${item.investigation_id}-${item.action.id}`);
      append(node, append(element("div", "card-top"), element("span", "eyebrow muted", "ACTION REVIEW"), badge(`${item.action.risk} risk`)),
        field("Requested by", item.requested_by), field("Investigation", item.investigation_id, true),
        button("Inspect investigation & evidence", "text-button", () => selectInvestigation(item.investigation_id), `${prefix}-inspect`),
        actionContent(item.action, false), field("Proposal digest (SHA-256)", item.digest, true));
      const review = element("div", "approval-review"), label = element("label", "check-label"), checkbox = element("input");
      checkbox.type = "checkbox"; checkbox.id = `${prefix}-review`;
      checkbox.checked = state.reviewed.has(key); checkbox.disabled = Boolean(restriction) || pending;
      append(label, checkbox, element("span", "", "I reviewed this exact resource, parameters, reason, evidence, and risk. My decision is bound to this proposal digest."));
      review.append(label);
      const approve = button(pending ? "Submitting..." : "Approve action", "button primary", () => decide(item, "approve"), `${prefix}-approve`);
      const reject = button("Reject action", "button danger", () => decide(item, "reject"), `${prefix}-reject`);
      const update = () => { approve.disabled = reject.disabled = Boolean(restriction) || pending || !checkbox.checked; };
      checkbox.addEventListener("change", () => { if (checkbox.checked) state.reviewed.add(key); else state.reviewed.delete(key); update(); });
      update();
      append(node, review, append(element("div", "button-row"), approve, reject));
      if (restriction) node.append(element("p", "approval-restriction", restriction));
      return node;
    });
  });
}
async function decide(item, decision) {
  const key = approvalKey(item), epoch = state.epoch;
  if (state.decisions.has(key) || !state.reviewed.has(key)) return;
  const restriction = approvalRestriction(state.user, item);
  if (restriction) { message("notice", restriction); return; }
  if (!state.approvals?.some((current) => approvalKey(current) === key)) {
    message("notice", "The proposal changed. Review the current proposal before deciding."); return;
  }
  state.decisions.add(key); renderApprovals();
  message("notice", "");
  try {
    await api(`/investigations/${segment(item.investigation_id)}/actions/${segment(item.action.id)}/decision`, {
      method: "POST", body: { decision, expected_digest: item.digest },
    });
    state.reviewed.delete(key);
    state.approvals = state.approvals.filter((current) => approvalKey(current) !== key);
    message("notice", decision === "approve" ? "Approval recorded. Execution and verification are performed by the server worker." : "Rejection recorded. This proposal will not execute.", false);
  } catch (error) {
    if (!ignored(error)) {
      state.reviewed.delete(key);
      message("notice", `${error.message} Review the latest proposal before trying again.`);
    }
  } finally {
    if (epoch === state.epoch) {
      state.decisions.delete(key);
      signatures.delete("approvals-list");
      renderApprovals(); refresh();
    }
  }
}
const metricDefinitions = [
  ["Root cause accuracy", "root_cause_accuracy", percent],
  ["Grounding", "evidence_supported_conclusion_rate", percent],
  ["Tool success", "tool_success_rate", percent],
  ["Latency / p95", "latency_p95_ms", (value) => numeric(value, " ms")],
  ["Cost / investigation", "cost_per_investigation_usd", money],
  ["Safety / unauthorized actions", "unauthorized_action_rate", percent],
];
function renderEvaluations() {
  renderRegion("evaluations-content", state.evaluations, () => {
    if (!state.evaluations.length) return [append(element("div", "panel"), empty("No persisted evaluations", "Run this command on the server, then refresh to inspect actual benchmark metrics and limitations.", "ops evaluate --persist"))];
    const reports = [...state.evaluations].sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp));
    const latest = reports[0], metrics = latest.metrics || {};
    const summary = append(element("div", "summary-strip"), badge(latest.passed ? "pass" : "fail"),
      element("span", "", `Latest run: ${formatDate(latest.timestamp)}`), element("span", "", `Scenarios: ${text(latest.scenario_count)}`));
    const cards = element("div", "metrics-grid");
    for (const [label, key, format] of metricDefinitions) cards.append(append(element("article", "metric"),
      element("span", "metric-label", label), element("strong", "metric-value", format(metrics[key])), element("span", "metric-key", key)));
    const notes = append(element("div", "detail-grid"),
      append(element("section", "panel panel-body"), element("h2", "", "Regressions"), values("Latest run", latest.regressions, "No regressions reported")),
      append(element("section", "panel panel-body"), element("h2", "", "Benchmark limitations"), values("Interpret results in context", latest.limitations, "No limitations supplied")));
    const allMetrics = append(element("section", "panel panel-body evaluation-section"), element("h2", "", "All measured metrics"));
    allMetrics.append(disclosure("all-evaluation-metrics", `${Object.keys(metrics).length} metrics / includes safety & approval compliance`, Object.entries(metrics).map(([key, value]) => field(key, text(value), true))));
    const history = append(element("section", "panel"), append(element("div", "panel-heading"), element("h2", "", "Evaluation history"), element("span", "count", `${reports.length} runs`)));
    const table = element("table");
    const caption = element("caption", "help", "Persisted runs, newest first. All latency values are milliseconds; costs are USD per investigation.");
    const header = element("tr");
    for (const label of ["Timestamp", "Scenarios", "Result", "Root cause", "Grounding", "Tool success", "p95 latency", "Cost", "Unauthorized actions"]) header.append(element("th", "", label));
    for (const th of header.children) th.scope = "col";
    append(table, caption, append(element("thead"), header));
    const tbody = element("tbody");
    for (const report of reports) {
      const row = element("tr"), m = report.metrics || {};
      append(row, element("td", "", formatDate(report.timestamp)), element("td", "", report.scenario_count),
        append(element("td"), badge(report.passed ? "pass" : "fail")));
      for (const [, key, format] of metricDefinitions) row.append(element("td", "mono", format(m[key])));
      tbody.append(row);
    }
    table.append(tbody);
    const scroll = element("div", "table-scroll");
    scroll.tabIndex = 0; scroll.setAttribute("role", "region"); scroll.setAttribute("aria-label", "Evaluation history table");
    history.append(append(scroll, table));
    const details = append(element("section", "panel panel-body evaluation-section"), element("h2", "", "Run details"));
    reports.forEach((report, index) => details.append(disclosure(domId("evaluation", report.id || `${report.timestamp}-${index}`), `${formatDate(report.timestamp)} / ${report.passed ? "Passed" : "Failed"}`, [
      field("Scenario count", report.scenario_count), values("Regressions", report.regressions),
      values("Limitations", report.limitations), field("Raw measured metrics", JSON.stringify(report.metrics, null, 2), true),
    ])));
    return [summary, cards, notes, allMetrics, details, history];
  });
}
function initialize() {
  for (const node of document.querySelectorAll("[data-view]")) node.addEventListener("click", () => setView(node.dataset.view, true));
  for (const node of document.querySelectorAll("[data-demo-role]")) node.addEventListener("click", () => void login(node.dataset.demoRole));
  for (const node of document.querySelectorAll(".refresh-button")) node.addEventListener("click", refresh);
  $("login-form").addEventListener("submit", (event) => { event.preventDefault(); void login(); });
  $("sign-out").addEventListener("click", () => signOut());
  $("config-retry").addEventListener("click", () => void loadConfig());
  $("investigation-form").addEventListener("submit", (event) => void createInvestigation(event));
  $("scenario").addEventListener("change", scenarioDescription);
  $("incident").addEventListener("input", () => { $("incident-count").textContent = `${$("incident").value.length.toLocaleString()} / 4,000`; });
  $("evidence-search").addEventListener("input", renderEvidence);
  $("download-report").addEventListener("click", () => void downloadReport());
  void loadConfig();
}
if (typeof document !== "undefined") initialize();

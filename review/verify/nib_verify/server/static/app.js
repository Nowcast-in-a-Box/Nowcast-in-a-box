/* The review page.
 *
 * No framework and no build step, on purpose: this ships inside a repository
 * whose root has no pyproject and no node_modules, and a reviewer should be able
 * to read the whole interface in one sitting.
 *
 * The page holds no rules of its own. Every constraint it appears to enforce —
 * the ruler cannot be edited, metrics are appended, an empty plan is an error —
 * is enforced by the API, and the page only reports what came back. If this file
 * were deleted the behaviour would be unchanged, just harder to reach.
 */

"use strict";

// ---------------------------------------------------------------- helpers
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

function h(tag, props, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value == null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "value") node.value = value;
    else if (key === "checked") node.checked = Boolean(value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) node.setAttribute(key, "");
    else node.setAttribute(key, value);
  }
  for (const kid of kids.flat(4)) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/// Replace a node's children.
///
/// Flattening is not cosmetic. ``mount(select, xs.map(...))`` is the natural way
/// to write a list, and without it the array is stringified into one text node —
/// which produces an empty dropdown rather than an error.
function mount(node, ...kids) {
  clear(node);
  const flat = kids.flat(4).filter(k => k !== null && k !== undefined && k !== false);
  node.append(...flat.map(k => k instanceof Node ? k : document.createTextNode(String(k))));
  return node;
}
function notice(kind, text, list) {
  return h("div", { class: `notice ${kind || ""}` }, text,
    list && list.length ? h("ul", {}, list.map(x => h("li", { text: x }))) : null);
}
function empty(text) { return h("div", { class: "empty", text }); }
function pill(text, kind) { return h("span", { class: `pill ${kind || ""}`, text }); }
function num(x) { return Number(x).toLocaleString("en-US"); }
function pct(x) { return x == null ? "—" : (100 * x).toFixed(1) + "%"; }

async function api(path, options = {}) {
  const init = { headers: { "Content-Type": "application/json" } };
  if (options.body !== undefined) init.body = JSON.stringify(options.body);
  if (options.method) init.method = options.method;
  const res = await fetch(path, init);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = { detail: text }; }
  if (!res.ok) {
    const message = (data && (data.detail || data.error)) || `${res.status} ${res.statusText}`;
    const error = new Error(message);
    error.payload = data;
    throw error;
  }
  return data;
}

const state = {
  catalog: null,
  metrics: null,
  configs: [],
  selected: null,
  memberRows: [],
  metricGroups: [],
  preview: null,
  metricFamily: null,
  chosenItems: new Set(),
  expand: [],
  lastQuery: null,
  poll: null,
  job: null,
};

// ---------------------------------------------------------------- tabs
function showTab(name) {
  if (state.poll && name !== "configs") { clearTimeout(state.poll); state.poll = null; }
  for (const button of $$("#tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === name));
  }
  for (const section of $$("main > section")) {
    section.hidden = section.id !== `tab-${name}`;
  }
  if (name === "results") refreshResultsSelectors();
}

$$("#tabs button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));

// ==================================================================
// Configs
// ==================================================================
async function loadConfigs() {
  const target = $("#configs-list");
  try {
    const data = await api("/api/configs");
    state.configs = data.configs;
    $("#configs-source").textContent = `${data.configs.length} config(s) in the config directory`;
    mount(target, renderConfigTable(data.configs, data.broken));
    // The results tab offers the same configs, so it has to be told they moved.
    await loadConfigsIntoSelect();
  } catch (error) {
    mount(target, notice("bad", `Could not read the configs: ${error.message}`));
  }
}

function renderConfigTable(configs, broken) {
  if (!configs.length) {
    return h("div", {},
      empty("No configs yet. Use “New config” to make the first one."),
      broken && broken.length ? notice("warn", "Some files could not be read:", broken.map(b => `${b.name}: ${b.error}`)) : null);
  }
  const rows = configs.map(cfg => h("tr", {},
    h("td", {}, h("code", { text: cfg.group_id })),
    h("td", { text: cfg.name }),
    h("td", { text: cfg.ground_truth }),
    h("td", { class: "num", text: cfg.time_step_minutes }),
    h("td", { class: "num", text: cfg.n_members }),
    h("td", { class: "num", text: cfg.n_metrics }),
    h("td", {}, h("code", { class: "tiny", text: cfg.ruler_fingerprint })),
    h("td", {}, h("button", { class: "link", text: "Open", onclick: () => openConfig(cfg.group_id) })),
  ));
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", { text: "id" }), h("th", { text: "name" }), h("th", { text: "truth" }),
      h("th", { class: "num", text: "step" }), h("th", { class: "num", text: "members" }),
      h("th", { class: "num", text: "metrics" }), h("th", { text: "ruler" }), h("th", {}))),
    h("tbody", {}, rows)));
}

async function openConfig(groupId) {
  const host = $("#config-detail");
  host.hidden = false;
  mount(host, h("div", { class: "card" }, empty("Loading…")));
  try {
    const cfg = await api(`/api/configs/${encodeURIComponent(groupId)}`);
    state.selected = cfg;
    mount(host, renderConfigDetail(cfg));
  } catch (error) {
    mount(host, h("div", { class: "card" }, notice("bad", error.message)));
  }
}

function renderConfigDetail(cfg) {
  const ruler = cfg.ruler;

  const rulerCard = h("div", { class: "card" },
    h("h2", {}, "The ruler ", pill("locked", "ok")),
    h("p", { class: "hint" }, "These fields decide the denominator and the mask. To change one, make a new config."),
    h("div", { class: "grid tight" },
      fact("truth source", ruler.ground_truth),
      fact("variable", ruler.variable),
      fact("unit", ruler.unit),
      fact("time step", `${ruler.time_step_minutes} min`),
      fact("extent", `[${ruler.extent.join(", ")}]`),
      fact("resolution", String(ruler.resolution)),
      fact("fingerprint", ruler.fingerprint)),
    cfg.warnings && cfg.warnings.length ? notice("warn", "Worth knowing:", cfg.warnings) : null);

  // ---- roster
  const rosterRows = cfg.members.map(m => h("tr", {},
    h("td", {}, h("code", { text: m.name })),
    h("td", { text: m.leads.join(", ") }),
    h("td", { text: m.active_from || "anytime" }),
    h("td", { text: m.active_to || "ongoing" }),
    h("td", {}, h("button", {
      class: "link danger", text: "Retire",
      onclick: () => retireMember(cfg, m.name),
    })),
  ));
  const rosterCard = h("div", { class: "card" },
    h("h2", {}, "Roster"),
    h("p", { class: "hint" }, "Add a member, or give one a last day. Membership records are never rewritten — a retired member keeps its numbers."),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", { text: "member" }), h("th", { text: "leads (min)" }),
        h("th", { text: "from" }), h("th", { text: "to" }), h("th", {}))),
      h("tbody", {}, rosterRows))),
    h("div", { class: "row", style: "margin-top:10px" },
      h("input", { id: "roster-name", placeholder: "new member id", style: "max-width:200px" }),
      h("input", { id: "roster-leads", placeholder: "10, 30, 60", style: "max-width:160px" }),
      h("button", { class: "act quiet", text: "Add", onclick: () => addMemberTo(cfg) })));

  // ---- metrics
  const metricBlocks = cfg.metrics.map(g => h("div", { class: "groupbox" },
    h("div", { class: "head" },
      h("strong", { text: `${g.family} → ${g.storage_family}` }), h("span", { class: "spacer" }),
      g.levels && g.levels.length ? pill(`levels ${g.levels.join(", ")}`) : null,
      g.half_windows && g.half_windows.length ? pill(`windows ${g.half_windows.join(", ")}`) : null),
    h("div", { class: "chips", style: "margin-top:6px" },
      g.items.map(i => h("span", { class: "chip", text: i })))));

  const metricsCard = h("div", { class: "card" },
    h("h2", {}, "Metrics ", pill("append-only", "ok")),
    h("p", { class: "hint" }, "A metric can be added, never removed: removing it would change what a stored number means. Adding one adds no rows."),
    h("div", {}, metricBlocks),
    renderMetricAdder(cfg, (group) => appendMetrics(cfg, group)));

  const storedCard = h("div", { class: "card" },
    h("h2", {}, "Stored"),
    h("div", { id: "stored-body" }, renderStored(cfg)));

  return h("div", {}, rulerCard, rosterCard, metricsCard, runCard(cfg), storedCard);
}

function renderStored(cfg) {
  const stored = cfg.stored || {};
  const counts = Object.entries(stored.tasks || {});
  return h("div", {},
    counts.length
      ? h("div", { class: "chips" }, counts.map(([state, n]) => pill(`${state}: ${num(n)}`, state === "failed" ? "bad" : state === "done" ? "ok" : "")))
      : empty("Nothing verified under this config yet."),
    (stored.families || []).length
      ? h("p", { class: "tiny muted", style: "margin-top:8px" }, `families populated: ${stored.families.join(", ")}`) : null,
    stored.missing_obs_times ? h("p", { class: "tiny muted" }, `${stored.missing_obs_times} observation time(s) recorded as missing`) : null);
}

/// Refresh only what a run changed.
///
/// Rebuilding the whole panel would be simpler and wrong: it would throw away
/// the verdict the run just produced, so the page would go quiet at exactly the
/// moment it had something to say.
async function refreshStored(groupId) {
  const host = $("#stored-body");
  if (!host) return;
  try {
    mount(host, renderStored(await api(`/api/configs/${encodeURIComponent(groupId)}`)));
  } catch (error) {
    mount(host, notice("bad", error.message));
  }
  await loadConfigs();
}

// ---------------------------------------------------------------- running
//
// A run happens in the background, so the page starts it and then polls. It
// polls rather than using server-sent events because the progress is a handful
// of lines and a run is short: reconnection semantics would be more code and
// more ways to be wrong than simply asking again.
function runCard(cfg) {
  const init = h("input", {
    id: "run-init",
    placeholder: "2026-01-01T00:00:00Z — blank takes them from the runs found",
  });
  const force = h("input", { id: "run-force", type: "checkbox" });
  const log = h("pre", { id: "run-log", class: "log", hidden: true });
  const verdict = h("div", { id: "run-verdict" });
  const status = h("span", { id: "run-status", class: "muted tiny" });

  const button = h("button", {
    id: "btn-run", class: "act", text: "Run",
    onclick: () => startRun(cfg, { init: init.value.trim(), force: force.checked, button, log, verdict, status }),
  });

  return h("div", { class: "card" },
    h("h2", {}, "Run"),
    h("p", { class: "hint" },
      "Verification reads saved prediction and truth, so it never starts inference. " +
      "Only one run happens at a time; a second is turned away rather than queued."),
    h("div", { class: "row" },
      init,
      h("label", { class: "row tiny" }, force, "recompute what is already done"),
      button,
      h("span", { class: "spacer" }),
      status),
    verdict, log);
}

async function startRun(cfg, ui) {
  if (state.poll) { clearTimeout(state.poll); state.poll = null; }
  ui.button.disabled = true;
  ui.log.hidden = false;
  ui.log.textContent = "";
  mount(ui.verdict, "");
  ui.status.textContent = "starting…";

  const body = { config: cfg.group_id, force: ui.force };
  if (ui.init) body.init = ui.init.split(",").map(s => s.trim()).filter(Boolean);

  let started;
  try {
    started = await api("/api/runs", { method: "POST", body });
  } catch (error) {
    mount(ui.verdict, notice("bad", error.message));
    ui.status.textContent = "";
    ui.button.disabled = false;
    return;
  }

  if (!started.started) {
    ui.status.textContent = "";
    ui.button.disabled = false;
    mount(ui.verdict,
      notice(started.reason === "busy" ? "warn" : "",
        started.reason === "busy"
          ? "A run is already in progress."
          : "Nothing to do: every run this config could use was rejected."),
      renderPlan(started.plan));
    return;
  }

  state.job = started.job;
  let cursor = 0;
  const poll = async () => {
    let view;
    try {
      view = await api(`/api/jobs/${encodeURIComponent(started.job)}?after=${cursor}`);
    } catch (error) {
      ui.log.textContent += `\n${error.message}`;
      ui.button.disabled = false;
      return;
    }
    cursor = view.cursor;
    for (const line of view.lines) ui.log.textContent += line + "\n";
    ui.log.scrollTop = ui.log.scrollHeight;
    ui.status.textContent = `${view.state} · ${view.elapsed_seconds}s · ${view.cursor} line(s)`;

    if (view.state === "running") {
      state.poll = setTimeout(poll, 400);
      return;
    }
    ui.button.disabled = false;
    state.job = null;
    if (view.state === "failed") {
      mount(ui.verdict, notice("bad", `The run failed: ${view.error}`));
    } else {
      const stats = (view.result || {}).stats || {};
      mount(ui.verdict, notice(stats.failed ? "warn" : "ok",
        `${num(stats.total || 0)} task(s): ${num(stats.ran || 0)} ran, ` +
        `${num(stats.skipped || 0)} skipped, ${num(stats.unavailable || 0)} unavailable, ` +
        `${num(stats.failed || 0)} failed.`));
      await refreshStored(cfg.group_id);
    }
  };
  state.poll = setTimeout(poll, 200);
}

function fact(label, value) {
  return h("div", { class: "field" },
    h("label", { text: label }),
    h("code", { text: String(value) }));
}

async function retireMember(cfg, name) {
  const on = window.prompt(`Last day ${name} is on the roster (YYYY-MM-DD):`, new Date().toISOString().slice(0, 10));
  if (!on) return;
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/roster/${encodeURIComponent(name)}/retire`,
      { method: "POST", body: { on } });
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
  }
}

async function addMemberTo(cfg) {
  const name = $("#roster-name").value.trim();
  const leads = $("#roster-leads").value.trim();
  if (!name) return window.alert("A member id is required.");
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/roster`,
      { method: "POST", body: { name, leads } });
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
  }
}

async function appendMetrics(cfg, group) {
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/metrics`,
      { method: "POST", body: group });
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
  }
}

// ==================================================================
// New config
// ==================================================================
async function loadCatalog() {
  try {
    state.catalog = await api("/api/catalog");
  } catch (error) {
    state.catalog = null;
  }
  const packages = (state.catalog && state.catalog.packages) || null;
  const truthList = $("#truth-options");
  const stepList = $("#step-options");
  clear(truthList); clear(stepList);

  if (!packages) {
    $("#truth-note").textContent = "No package roots were given to this server.";
    return;
  }
  const seenSteps = new Set();
  for (const pkg of packages.packages || []) {
    if (pkg.kind === "data") {
      truthList.append(h("option", { value: pkg.id }));
      if (pkg.cadence_minutes && !seenSteps.has(pkg.cadence_minutes)) {
        seenSteps.add(pkg.cadence_minutes);
        stepList.append(h("option", { value: pkg.cadence_minutes }));
      }
    }
  }
  $("#truth-note").textContent = `${(packages.packages || []).length} package(s) found`;
}

async function loadMetrics() {
  try {
    state.metrics = await api("/api/metrics");
  } catch (error) {
    state.metrics = { families: [], metrics: [] };
  }
  const select = $("#m-family");
  clear(select);
  for (const family of state.metrics.families) {
    select.append(h("option", { value: family.family, text: family.family }));
  }
  select.value = "categorical";
  renderItemChips();
  select.addEventListener("change", renderItemChips);
}

function paramsForFamily(family) {
  const rows = (state.metrics.metrics || []).filter(m => m.family === family);
  const params = new Set();
  rows.forEach(m => (m.params || []).forEach(p => params.add(p)));
  return params;
}

function renderItemChips() {
  const family = $("#m-family").value;
  const host = "#m-items";
  const items = (state.metrics.metrics || []).filter(m => m.family === family).map(m => m.method);
  state.chosenItems = new Set(items);           // default: the whole family
  const needs = paramsForFamily(family);
  markRequired($("#m-levels"), needs.has("level"));
  markRequired($("#m-windows"), needs.has("half_window"));
  mount($(host), items.map(item => h("button", {
    class: "chip", type: "button", "aria-pressed": "true", text: item,
    onclick: (event) => {
      const button = event.currentTarget;
      const on = button.getAttribute("aria-pressed") !== "true";
      button.setAttribute("aria-pressed", String(on));
      if (on) state.chosenItems.add(item); else state.chosenItems.delete(item);
    },
  })));
}

/// Show that a parameter is required *before* the user asks for a preview.
///
/// The service would refuse either way; finding out at the last step, after
/// filling in five blocks, is the part worth avoiding.
///
/// Tolerates a detached input: the metric adder builds its fields and then
/// refreshes them, so at that instant ``closest`` finds nothing.
function markRequired(input, needed) {
  input.disabled = !needed;
  input.required = needed;
  const field = input.closest(".field");
  const label = field ? field.querySelector("label") : null;
  if (label) label.textContent = label.textContent.replace(/ \*$/, "") + (needed ? " *" : "");
}

function emptyMember() {
  return { name: "", leads: "", active_from: "", active_to: "" };
}

function renderMembers() {
  const host = "#members";
  const models = modelIds();
  const rows = state.memberRows.map((row) => h("div", { class: "grid", style: "margin-bottom:8px" },
    h("div", { class: "field" }, h("label", { text: "member id" }),
      h("input", { value: row.name, placeholder: "mlcast-det", oninput: e => row.name = e.target.value })),
    h("div", { class: "field" }, h("label", { text: "leads (minutes)" }),
      h("input", { value: row.leads, placeholder: "10, 30, 60", oninput: e => row.leads = e.target.value })),
    h("div", { class: "field" }, h("label", { text: "active from" }),
      h("input", { value: row.active_from, placeholder: "optional", oninput: e => row.active_from = e.target.value })),
    h("div", { class: "field" }, h("label", { text: "active to" }),
      h("input", { value: row.active_to, placeholder: "optional", oninput: e => row.active_to = e.target.value })),
    h("div", { class: "field" }, h("label", { text: " " }),
      h("button", { class: "act danger", text: "Remove", onclick: () => {
        state.memberRows = state.memberRows.filter(r => r !== row);
        renderMembers();
      } }))));

  mount($(host),
    rows.length ? h("div", {}, rows) : empty("No members yet."),
    h("p", { class: "tiny muted" }, models.length
      ? `Models the catalog knows about: ${models.join(", ")}`
      : "This server was given no package roots, so no model ids to suggest."));
}

function modelIds() {
  const packages = state.catalog && state.catalog.packages;
  return packages ? (packages.packages || []).filter(p => p.kind === "model").map(p => p.id) : [];
}

function renderMetricAdder(cfg, onAdd) {
  const family = h("select", {}, (state.metrics.families || []).map(f => h("option", { value: f.family, text: f.family })));
  const levels = h("input", { placeholder: "181, 219" });
  const windows = h("input", { placeholder: "2, 4" });
  const chips = h("div", { class: "chips" });
  const chosen = new Set();

  function refresh() {
    const itemRows = (state.metrics.metrics || []).filter(m => m.family === family.value);
    const needs = paramsForFamily(family.value);
    markRequired(levels, needs.has("level"));
    markRequired(windows, needs.has("half_window"));
    chosen.clear();
    mount(chips, itemRows.map(m => h("button", {
      class: "chip", type: "button", "aria-pressed": "false", text: m.method,
      onclick: (event) => {
        const button = event.currentTarget;
        const on = button.getAttribute("aria-pressed") !== "true";
        button.setAttribute("aria-pressed", String(on));
        if (on) chosen.add(m.method); else chosen.delete(m.method);
      },
    })));
  }
  family.addEventListener("change", refresh);
  refresh();

  const needs = paramsForFamily(family.value);
  return h("div", { class: "groupbox", style: "background:#fafbfe" },
    h("div", { class: "grid" },
      h("div", { class: "field" }, h("label", { text: "family" }), family),
      h("div", { class: "field" }, h("label", { text: "levels" }), levels),
      h("div", { class: "field" }, h("label", { text: "half windows" }), windows)),
    h("div", { class: "field", style: "margin-top:10px" }, h("label", { text: "metrics" }), chips),
    h("div", { class: "row end", style: "margin-top:10px" },
      h("button", {
        class: "act ghost", text: "Add this group",
        onclick: () => {
          if (!chosen.size) return window.alert("Pick at least one metric.");
          const needed = paramsForFamily(family.value);
          onAdd({
            family: family.value,
            items: Array.from(chosen),
            // A disabled input keeps its text. Reading it anyway is how a
            // continuous group ends up carrying thresholds it cannot use.
            levels: needed.has("level") ? numbers(levels.value) : [],
            half_windows: needed.has("half_window") ? numbers(windows.value).map(Number) : [],
          });
        },
      })));
}

function numbers(text) {
  return String(text || "").split(",").map(s => s.trim()).filter(Boolean).map(Number);
}

function buildSpec() {
  const id = $("#c-id").value.trim();
  const name = $("#c-name").value.trim();
  if (!id) throw new Error("A group id is required.");
  if (!name) throw new Error("A display name is required.");

  const members = state.memberRows.map(r => ({
    name: r.name.trim(),
    variable: $("#c-variable").value.trim(),
    unit: $("#c-unit").value.trim(),
    leads: numbers(r.leads).map(Number),
    active_from: r.active_from.trim() || null,
    active_to: r.active_to.trim() || null,
  })).filter(m => m.name);

  if (!members.length) throw new Error("At least one member is required.");
  if (!state.metricGroups.length) throw new Error("At least one metric group is required.");

  return {
    group: { id, name },
    notes: $("#c-notes").value.trim(),
    ruler: {
      ground_truth: $("#c-truth").value.trim(),
      variable: $("#c-variable").value.trim(),
      unit: $("#c-unit").value.trim(),
      time_step_minutes: Number($("#c-step").value),
      extent: [Number($("#c-lon0").value), Number($("#c-lat0").value),
               Number($("#c-lon1").value), Number($("#c-lat1").value)],
      resolution: Number($("#c-res").value),
    },
    members,
    metrics: state.metricGroups,
  };
}

async function previewPlan() {
  const host = $("#preview");
  mount(host, empty("Planning…"));
  try {
    const spec = buildSpec();
    const probe = $("#c-probe").checked;
    const plan = await api("/api/plan", { method: "POST", body: { spec, probe } });
    state.preview = plan;
    mount(host, renderPlan(plan));
  } catch (error) {
    mount(host, notice("bad", error.message));
  }
}

function renderPlan(plan) {
  const parts = [];
  if (plan.warnings && plan.warnings.length) {
    parts.push(notice("warn", "The catalog disagrees:", plan.warnings));
  }
  parts.push(h("div", { class: "grid tight" },
    fact("tasks", num(plan.tasks)),
    fact("init times", num(plan.init_times)),
    fact("members", plan.members.join(", ") || "—"),
    fact("leads", plan.leads.join(", ") || "—"),
    fact("runs found", num(plan.runs_discovered)),
    fact("from", plan.span.from || "—"),
    fact("to", plan.span.to || "—")));

  if (!plan.tasks && plan.runs_discovered) {
    parts.push(notice("bad",
      "Every run was rejected, so this config would verify nothing. That is an error, not an empty success."));
  }
  if (plan.rejections && plan.rejections.length) {
    const reasons = Object.entries(plan.reasons || {});
    parts.push(h("table", {},
      h("thead", {}, h("tr", {}, h("th", { text: "reason" }), h("th", { class: "num", text: "runs" }), h("th", { text: "what it means" }))),
      h("tbody", {}, reasons.map(([reason, count]) => h("tr", {},
        h("td", {}, h("code", { text: reason })),
        h("td", { class: "num", text: num(count) }),
        h("td", { class: "muted", text: REASON_TEXT[reason] || "" }))))));
  }
  if (plan.coverage) {
    const c = plan.coverage;
    parts.push(notice("ok", `The provider can serve ${num(c.available)} of ${num(c.expected)} planned frames.`));
  }
  if (plan.notes && plan.notes.length) {
    parts.push(notice("", "Notes:", plan.notes.slice(0, 8)));
  }
  return h("div", {}, parts);
}

const REASON_TEXT = {
  "not-on-roster": "the run's model is not on this config's roster",
  inactive: "the member was not on the roster on that date",
  "no-start-time": "the run is identified by sample_id, so it has no place on a time series",
  "beyond-horizon": "no requested lead falls inside the run's forecast horizon",
  "incompatible-pair": "the data and model packages do not match",
};

async function saveConfig() {
  const host = $("#preview");
  try {
    const spec = buildSpec();
    const strict = $("#c-strict").checked;
    const created = await api("/api/configs", { method: "POST", body: { spec, strict } });
    mount(host, notice("ok", `Created ${created.created}.`),
      created.warnings && created.warnings.length ? notice("warn", "Saved with warnings:", created.warnings) : null);
    await loadConfigs();
    await loadCatalog();
  } catch (error) {
    mount(host, notice("bad", error.message));
  }
}

// ==================================================================
// Results
// ==================================================================
async function refreshResultsSelectors() {
  await loadConfigsIntoSelect();
  const select = $("#q-config");
  if (select.value) await onConfigPicked();
}

async function loadConfigsIntoSelect() {
  const select = $("#q-config");
  const previous = select.value;
  let configs = state.configs;
  if (!configs.length) {
    try { configs = (await api("/api/configs")).configs; state.configs = configs; } catch (_) { configs = []; }
  }
  mount(select, configs.map(c => h("option", { value: c.group_id, text: `${c.group_id} — ${c.name}` })));
  if (previous && configs.some(c => c.group_id === previous)) select.value = previous;
}

async function onConfigPicked() {
  const groupId = $("#q-config").value;
  if (!groupId) return;
  let cfg = state.selected && state.selected.group_id === groupId ? state.selected : null;
  if (!cfg) {
    try { cfg = await api(`/api/configs/${encodeURIComponent(groupId)}`); } catch (_) { return; }
  }
  state.selected = cfg;
  const methods = (cfg.metrics_available || []).map(m => m.method);
  const select = $("#q-method");
  mount(select, methods.map(m => h("option", { value: m, text: m })));
  if (methods.length) await onMethodPicked();
  renderCoverage(cfg);
}

async function onMethodPicked() {
  const method = $("#q-method").value;
  if (!method) return;
  let dimensions = [];
  try { dimensions = (await api(`/api/metrics/${encodeURIComponent(method)}/dimensions`)).dimensions; } catch (_) {}
  state.expand = state.expand.filter(d => dimensions.includes(d));
  renderExpandChips(dimensions);
}

function renderExpandChips(dimensions) {
  mount($("#q-g"),
    dimensions.map(d => {
      const at = state.expand.indexOf(d);
      return h("button", {
        class: "chip", type: "button", "aria-pressed": String(at >= 0),
        text: at >= 0 ? `${at + 1}. ${d}` : d,
        onclick: () => {
          const i = state.expand.indexOf(d);
          if (i >= 0) state.expand.splice(i, 1); else state.expand.push(d);
          onMethodPicked();
        },
      });
    }),
    h("span", { class: "tiny muted" }, state.expand.length
      ? `order: ${state.expand.join(" → ")}` : "nothing selected: one total"));
}

function renderCoverage(cfg) {
  const stored = cfg.stored || {};
  const counts = Object.entries(stored.tasks || {});
  mount($("#coverage"),
    counts.length
      ? h("div", { class: "chips" }, counts.map(([s, n]) => pill(`${s}: ${num(n)}`, s === "failed" ? "bad" : s === "done" ? "ok" : "")))
      : empty("Nothing verified under this config yet."),
    h("p", { class: "tiny muted" }, `families: ${(stored.families || []).join(", ") || "none"}`
      + (stored.missing_obs_times ? ` · ${stored.missing_obs_times} missing observation time(s)` : "")));
}

async function runQuery() {
  const host = $("#query-result");
  mount(host, empty("Querying…"));
  const params = new URLSearchParams();
  params.set("config", $("#q-config").value);
  params.set("method", $("#q-method").value);
  state.expand.forEach(d => params.append("g", d));
  for (const [key, id] of [["lead", "q-lead"], ["level", "q-level"], ["half_window", "q-window"],
                           ["time_from", "q-time-from"], ["time_to", "q-time-to"]]) {
    const value = $(`#${id}`).value.trim();
    if (value) params.set(key, value);
  }
  try {
    const result = await api(`/api/scores?${params.toString()}`);
    state.lastQuery = result;
    mount(host, renderScores(result));
  } catch (error) {
    mount(host, notice("bad", error.message));
  }
}

function renderScores(result) {
  // The store always answers, even when nothing matched: an ungrouped query
  // returns one row of zeroes. That row is a real answer to "was there
  // anything?" and a useless row to show, so it is filtered out — and the
  // count of what was filtered is reported rather than swallowed.
  const usable = result.records.filter(r => r.n_valid > 0);
  const dropped = result.records.length - usable.length;
  if (!usable.length) return empty("No stored quantities match.");

  const dims = result.dims;
  const head = h("tr", {},
    dims.map(d => h("th", { text: d })),
    h("th", { class: "num", text: result.meta.label }),
    h("th", { class: "num", text: "samples" }));
  const body = usable.map(r => h("tr", {},
    dims.map(d => h("td", { class: d === "valid_time" || d === "init_time" ? "mono" : "", text: r[d] == null ? "—" : String(r[d]) })),
    h("td", { class: "num", text: r.value == null ? "—" : Number(r.value).toFixed(4) }),
    h("td", { class: "num", text: num(r.n_valid) })));

  return h("div", {},
    h("div", { class: "row", style: "margin-top:12px" },
      pill(`${usable.length} record(s)`),
      pill(`${num(usable.reduce((a, r) => a + r.n_valid, 0))} valid samples`),
      pill(`ruler ${result.meta.group ? result.meta.group.ruler.fingerprint : "—"}`)),
    dropped ? h("p", { class: "tiny muted" },
      `${dropped} group(s) had no samples and are not shown.`) : null,
    h("div", { class: "scroll", style: "margin-top:10px" },
      h("table", {}, h("thead", {}, head), h("tbody", {}, body))));
}

// ==================================================================
// wiring
// ==================================================================
$("#btn-reload-configs").addEventListener("click", loadConfigs);
$("#btn-add-member").addEventListener("click", () => { state.memberRows.push(emptyMember()); renderMembers(); });
$("#btn-add-metric-group").addEventListener("click", () => {
  const family = $("#m-family").value;
  if (!state.chosenItems.size) return window.alert("Pick at least one metric.");
  const needed = paramsForFamily(family);
  state.metricGroups.push({
    family,
    items: Array.from(state.chosenItems),
    // Only read a parameter the family actually has. ``markRequired`` disables
    // the other field but a disabled input still holds whatever was typed into
    // it before the family changed.
    levels: needed.has("level") ? numbers($("#m-levels").value) : [],
    half_windows: needed.has("half_window")
      ? numbers($("#m-windows").value).map(Number) : [],
  });
  renderMetricGroups();
});
$("#btn-preview").addEventListener("click", previewPlan);
$("#btn-save").addEventListener("click", saveConfig);
$("#btn-query").addEventListener("click", runQuery);
$("#q-config").addEventListener("change", onConfigPicked);
$("#q-method").addEventListener("change", onMethodPicked);

function renderMetricGroups() {
  const host = $("#metric-groups");
  if (!state.metricGroups.length) return mount(host, empty("No metric groups yet."));
  mount(host, state.metricGroups.map((g, i) => h("div", { class: "groupbox" },
    h("div", { class: "head" },
      h("strong", { text: g.family }),
      h("span", { class: "spacer" }),
      h("button", { class: "link danger", text: "remove",
        onclick: () => { state.metricGroups.splice(i, 1); renderMetricGroups(); } })),
    h("div", { class: "chips", style: "margin-top:6px" }, g.items.map(x => h("span", { class: "chip", text: x }))),
    (g.levels.length || g.half_windows.length)
      ? h("p", { class: "tiny muted", style: "margin:6px 0 0" },
          [g.levels.length ? `levels ${g.levels.join(", ")}` : "", g.half_windows.length ? `half windows ${g.half_windows.join(", ")}` : ""].filter(Boolean).join(" · "))
      : null)));
}

async function boot() {
  await Promise.all([loadConfigs(), loadCatalog(), loadMetrics()]);
  renderMembers();
  renderMetricGroups();
  showTab("configs");
}

boot();

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

function clear(node) { if (!node) { return; } while (node.firstChild) node.removeChild(node.firstChild); }

/// Replace a node's children.
///
/// Flattening is not cosmetic. ``mount(select, xs.map(...))`` is the natural way
/// to write a list, and without it the array is stringified into one text node —
/// which produces an empty dropdown rather than an error.
function mount(node, ...kids) {
    if (!node) { return; }   // a panel that no longer exists
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
  expand: [],
  lastQuery: null,
  poll: null,
  job: null,
  chartUrl: null,
  kind: "line",
  hidden: [],            // members the legend has been clicked off
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
  if (name === "results") { renderKindChips(); syncMethodChips(); refreshResultsSelectors(); }
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
    if ($("#configs-source")) {
      $("#configs-source").textContent = `${data.configs.length} config(s) in the config directory`;
    }
    if (target) { mount(target, renderConfigTable(data.configs, data.broken)); }
    // The results tab offers the same configs, so it has to be told they moved.
    await loadConfigsIntoSelect();
  } catch (error) {
    if (target) { mount(target, notice("bad", `Could not read the configs: ${error.message}`)); }
    else { console.warn("Could not read the configs:", error); }
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
    h("td", {},
      h("button", { class: "link", text: "Edit", onclick: () => openConfig(cfg.group_id) }),
      state.removing === cfg.group_id
        ? h("span", { class: "row", style: "display:inline-flex;margin-left:10px" },
            h("span", { class: "tiny muted", text: "remove it?" }),
            h("button", { class: "link danger", text: "Yes",
                          onclick: () => removeConfig(cfg.group_id) }),
            h("button", { class: "link", text: "No",
                          onclick: () => { state.removing = null; loadConfigs(); } }))
        : h("button", { class: "link danger", text: "Remove",
                        style: "margin-left:10px",
                        onclick: () => { state.removing = cfg.group_id; loadConfigs(); } })),
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
    resumeRun(cfg);
  } catch (error) {
    mount(host, h("div", { class: "card" }, notice("bad", error.message)));
  }
}

function renderConfigDetail(cfg) {
  const ruler = cfg.ruler;

  const rulerCard = h("div", { class: "card" },
    h("h2", {}, "Basic set ", pill("locked", "ok")),
    h("p", { class: "hint" }, "These fields decide the denominator and the mask. To change one, make a new config."),
    h("div", { class: "grid tight" },
      fact("truth source", ruler.ground_truth),
      // No `variable`: the ruler has not carried one since it stopped deciding
      // anything. A member's output field is the member's own fact, and it is
      // shown on the roster, not here.
      fact("unit", ruler.unit),
      fact("time step", `${ruler.time_step_minutes} min`),
      fact("extent", `[${ruler.extent.join(", ")}]`),
      fact("resolution", String(ruler.resolution)),
      fact("fingerprint", ruler.fingerprint)),
    cfg.warnings && cfg.warnings.length ? notice("warn", "Worth knowing:", cfg.warnings) : null);

  // ---- roster
  const retired = (m) => Boolean(m.active_to);
  const rosterRows = cfg.members.map(m => h("tr", {},
    h("td", {}, h("code", { text: m.name }),
      retired(m) ? pill(`retired ${m.active_to}`, "") : null),
    h("td", {}, retired(m)
      ? h("span", { class: "muted", text: m.leads.join(", ") })
      : h("input", {
          value: m.leads.join(", "), style: "max-width:220px",
          title: "the leads this member is verified at, in minutes",
          onchange: (e) => saveMemberLeads(cfg, m, e.target.value),
        })),
    h("td", { text: m.active_from || "anytime" }),
    h("td", { text: m.active_to || "ongoing" }),
    h("td", {}, retired(m)
      ? h("span", { class: "tiny muted", text: "off the roster" })
      : state.removingMember === m.name
        ? h("span", { class: "row", style: "display:inline-flex" },
            h("span", { class: "tiny muted", text: "remove?" }),
            h("button", { class: "link danger", text: "Yes",
                          onclick: () => removeMember(cfg, m.name) }),
            h("button", { class: "link", text: "No",
                          onclick: () => { state.removingMember = null; openConfig(cfg.group_id); } }))
        : h("button", {
            class: "link danger", text: "Remove",
            onclick: () => { state.removingMember = m.name; openConfig(cfg.group_id); },
          })),
  ));
  const rosterCard = h("div", { class: "card" },
    h("h2", {}, "Forecasts"),
    h("p", { class: "hint" }, "Leads are editable. Removing a member gives it a last day rather than deleting the record, because the numbers it already contributed have to keep their meaning."),
    h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", { text: "member" }), h("th", { text: "leads (min)" }),
        h("th", { text: "from" }), h("th", { text: "to" }), h("th", {}))),
      h("tbody", {}, rosterRows))),
    h("div", { class: "row", style: "margin-top:10px" },
      rosterPicker(cfg),
      h("input", { id: "roster-leads", placeholder: "10, 30, 60", style: "max-width:160px" }),
      h("button", { class: "act quiet", text: "Add", onclick: () => addMemberTo(cfg) })));

  // ---- metrics
  const metricBlocks = cfg.metrics.map(g => h("div", { class: "groupbox" },
    h("div", { class: "head" },
      h("strong", { text: familyLabel(g.family) }), h("span", { class: "spacer" }),
      g.levels && g.levels.length ? pill(`levels ${g.levels.join(", ")}`) : null,
      g.half_windows && g.half_windows.length ? pill(`windows ${g.half_windows.join(", ")}`) : null),
    h("div", { class: "chips", style: "margin-top:6px" },
      g.items.map(i => h("span", { class: "chip", text: i })))));

  const metricsCard = h("div", { class: "card" },
    h("h2", {}, "Metrics ", pill("append-only", "ok")),
    h("p", { class: "hint" }, "Not editable yet. A group can be appended and never removed — removing one would change what a stored number means — and the form for appending is not built."),
    h("div", {}, metricBlocks));

  const storedCard = h("div", { class: "card" },
    h("h2", {}, "Stored"),
    h("div", { id: "stored-body" }, renderStored(cfg)),
    h("h2", { style: "margin-top:18px" }, "Runs"),
    h("p", { class: "hint" },
      "Every pass that finished, newest first. Kept in the store, so it survives " +
      "a restart — 'when was this last verified' is a question about the record."),
    h("div", { id: "runs-body" }, renderRuns(cfg)));

  return h("div", {}, rulerCard, rosterCard, metricsCard, runCard(cfg), storedCard);
}

/// What has been verified, and when it was last looked at.
///
/// The counts alone cannot answer the question somebody opening this page the
/// next morning actually has, which is whether they need to run it again.
function renderRuns(cfg) {
  const runs = (cfg.stored || {}).runs || [];
  if (!runs.length) return empty("No pass has finished under this config yet.");
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {},
      h("th", { text: "started" }), h("th", { text: "took" }),
      h("th", { text: "ran" }), h("th", { text: "unavailable" }),
      h("th", { text: "failed" }), h("th", { text: "skipped" }),
      h("th", {}, ""))),
    h("tbody", {}, runs.map((r, i) => h("tr", {},
      h("td", {}, h("code", { class: "tiny", text: r.started_at })),
      h("td", { class: "num", text: seconds(r.seconds) }),
      h("td", { class: "num", text: num(r.ran) }),
      h("td", { class: "num", text: num(r.unavailable) }),
      h("td", { class: "num", text: num(r.failed) }),
      h("td", { class: "num", text: num(r.skipped) }),
      h("td", {}, r.failed ? pill("failed", "bad")
        : i === 0 ? pill("latest", "ok") : null),
    )))));
}

function seconds(value) {
  const n = Number(value) || 0;
  return n < 60 ? `${n.toFixed(1)} s` : `${(n / 60).toFixed(1)} min`;
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
    const cfg = await api(`/api/configs/${encodeURIComponent(groupId)}`);
    mount(host, renderStored(cfg));
    const runs = $("#runs-body");
    if (runs) mount(runs, renderRuns(cfg));
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

  watchRun(started.job, cfg, ui, 0);
}

function fact(label, value, note) {
  return h("div", { class: "field" },
    h("label", { text: label }),
    h("code", { text: String(value) }),
    note ? h("span", { class: "note", text: note }) : null);
}

/// Save the leads a member is verified at.
///
/// Leads are the one part of a membership record that can be rewritten: they
/// decide which frames would be verified, not what a stored number means. Every
/// stored row carries its own lead, so shortening this list cannot orphan
/// anything -- it only says what to do next time.
async function saveMemberLeads(cfg, member, text) {
  const leads = numbers(text).map(Number);
  if (!leads.length) {
    window.alert("A member needs at least one lead.");
    return openConfig(cfg.group_id);
  }
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/roster`, {
      method: "POST",
      body: {
        name: member.name, variable: member.variable, unit: member.unit, leads,
        active_from: member.active_from, active_to: member.active_to,
      },
    });
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
    await openConfig(cfg.group_id);
  }
}

/// Take a member off the roster, as of today.
///
/// A last day and not a deletion. The numbers this member contributed up to that
/// day are in the store keyed by its name, and a config that no longer names it
/// leaves them with nothing to be a number *of*.
async function removeMember(cfg, name) {
  const on = new Date().toISOString().slice(0, 10);
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/roster/`
              + `${encodeURIComponent(name)}/retire`, { method: "POST", body: { on } });
    state.removingMember = null;
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
    await openConfig(cfg.group_id);
  }
}

/// The models this config could still put on its roster.
///
/// One unit only: every member has to be in the ruler's unit or the thresholds
/// mean nothing. Typing a name by hand was how a model in another unit got on --
/// and then verified nothing, with the reason somewhere the reader could not see.
function rosterChoices(cfg) {
  const tree = state.catalog && state.catalog.packages;
  const packages = (tree && tree.packages) || [];
  const on = new Set(cfg.members.filter(m => !m.active_to).map(m => m.name));
  return packages
    .filter(p => p.kind === "model" && !on.has(p.id))
    .filter(p => (((p.ruler_defaults || {}).unit) || cfg.ruler.unit) === cfg.ruler.unit)
    .map(p => p.id);
}

function rosterPicker(cfg) {
  const choices = rosterChoices(cfg);
  const pick = h("select", { id: "roster-name", style: "max-width:220px" },
    (choices.length ? choices : [""]).map(id => h("option", {
      value: id,
      text: id || (cfg.members.length
        ? "every model in this unit is on the roster"
        : `no model the catalog found produces ${cfg.ruler.unit}`),
    })));
  pick.disabled = !choices.length;
  return pick;
}

async function addMemberTo(cfg) {
  const name = $("#roster-name").value.trim();
  const leads = numbers($("#roster-leads").value).map(Number);
  if (!name) return window.alert("Pick a model to add.");
  if (!leads.length) return window.alert("A member needs at least one lead.");
  try {
    await api(`/api/configs/${encodeURIComponent(cfg.group_id)}/roster`,
      { method: "POST", body: { name, leads } });
    await loadConfigs();
    await openConfig(cfg.group_id);
  } catch (error) {
    window.alert(error.message);
  }
}

/// Remove a whole config.
///
/// This one does destroy things: the file, and every stored row under it. That is
/// what "remove" means, so it asks first and it is asked twice -- once on the row
/// and once by the second click.
async function removeConfig(groupId) {
  try {
    await api(`/api/configs/${encodeURIComponent(groupId)}`, { method: "DELETE" });
    state.removing = null;
    if (state.selected && state.selected.group_id === groupId) {
      state.selected = null;
      $("#config-detail").hidden = true;
      mount($("#config-detail"), "");
    }
    await loadConfigs();
  } catch (error) {
    window.alert(error.message);
  }
}



// ==================================================================
// New config
// ==================================================================
async function loadCatalog() {
    if (!$("#truth-options")) { return; }   // wizard gone
  try {
    state.catalog = await api("/api/catalog");
  } catch (error) {
    state.catalog = null;
  }
  const packages = (state.catalog && state.catalog.packages) || null;
  const truthList = $("#truth-options");
  clear(truthList);

  if (!packages) {
    $("#truth-note").textContent = "No package roots were given to this server.";
    return;
  }
  for (const pkg of packages.packages || []) {
    if (pkg.kind === "data") truthList.append(h("option", { value: pkg.id }));
  }
  applyGroundTruth();
}

// What the ground truth package says about the ruler, keyed by package id.
function groundTruth(id) {
  const packages = (state.catalog && state.catalog.packages) || null;
  if (!packages) return null;
  for (const pkg of packages.packages || []) {
    if (pkg.id === id) return pkg.ruler_defaults || {};
  }
  return null;
}

// A box the manifest does not answer for is left empty, never filled with a
// guess: the ruler decides what the numbers mean, and an invented extent is a
// silently invented mask.
function fill(selector, value) {
  $(selector).value = value === null || value === undefined ? "" : value;
}

const RULER_BOXES = [["unit", "#c-unit"], ["time_step_minutes", "#c-step"],
                     ["resolution", "#c-res"]];
const EXTENT_BOXES = ["#c-lon0", "#c-lat0", "#c-lon1", "#c-lat1"];

function applyGroundTruth() {
  const id = $("#c-truth").value.trim();
  const derived = id ? groundTruth(id) : null;

  for (const [key, selector] of RULER_BOXES) {
    fill(selector, derived ? derived[key] : null);
  }
  EXTENT_BOXES.forEach((selector, i) => {
    fill(selector, derived && derived.extent ? derived.extent[i] : null);
  });

  if (!id) {
    $("#truth-note").textContent = "Pick one; its unit and its step come with it.";
    $("#extent-note").textContent = "";
    return;
  }
  if (!derived) {
    $("#truth-note").textContent =
      `"${id}" is not a package this server can see, so nothing was filled in.`;
    $("#extent-note").textContent = "";
    return;
  }

  const have = [];
  for (const [key, label] of [["unit", "unit"], ["time_step_minutes", "time step"],
                              ["extent", "extent"], ["resolution", "resolution"]]) {
    if (derived[key] !== null && derived[key] !== undefined) have.push(label);
  }
  renderMemberPicker();
  $("#truth-note").textContent = have.length
    ? `filled from the package: ${have.join(", ")}`
    : "this package declares none of these, so fill them in yourself";
  $("#extent-note").textContent =
    derived.extent == null || derived.resolution == null
      ? "Where the grid is was not declared — a region name is not an extent — so "
        + "the extent and the resolution are empty. They decide the mask, and "
        + "nothing here is going to guess them."
      : "";
}

async function loadMetrics() {
  try {
    state.metrics = await api("/api/metrics");
  } catch (error) {
    state.metrics = { families: [], metrics: [] };
  }
  seedMetricGroups();
}

// The families, their words and their defaults all come from the service. The
// page has no list of its own: a family added on the server shows up here, and a
// family renamed on the server is renamed here.
function familyInfo(family) {
  return (state.metrics.families || []).find(f => f.family === family) || null;
}

function familyLabel(family) {
  const info = familyInfo(family);
  return info ? info.label : family;
}

function defaultFamilies() {
  return (state.metrics.families || []).filter(f => f.added_by_default);
}

function defaultGroup(family) {
  const info = familyInfo(family);
  return { family, items: info ? info.metrics.slice() : [], levels: "", half_windows: "" };
}

// Seeded from what the registry actually offers, so a family the server does not
// have is not added and everything in one it does have is. That is the whole
// point of the section: there is nothing here to tick.
function seedMetricGroups() {
  state.metricGroups = defaultFamilies().map(f => defaultGroup(f.family));
  renderMetricGroups();
}

function paramsForFamily(family) {
  const rows = (state.metrics.metrics || []).filter(m => m.family === family);
  const params = new Set();
  rows.forEach(m => (m.params || []).forEach(p => params.add(p)));
  return params;
}

/// The boxes a family's parameters are typed into, derived from what its metrics
/// say they need. A family that reads no parameter is told so rather than handed
/// two dead boxes to wonder about.
function metricParamFields(group) {
  const needs = paramsForFamily(group.family);
  const fields = [];
  if (needs.has("level")) {
    fields.push(h("div", { class: "field" },
      h("label", { for: `m-levels-${group.family}`, text: "Thresholds *" }),
      h("input", { id: `m-levels-${group.family}`, value: group.levels,
                   placeholder: "16, 74, 133, 181, 219", autocomplete: "off",
                   oninput: e => { group.levels = e.target.value; } }),
      h("span", { class: "note",
                  text: "The levels the scores are computed at. Comma separated." })));
  }
  if (needs.has("half_window")) {
    fields.push(h("div", { class: "field" },
      h("label", { for: `m-windows-${group.family}`, text: "Half windows *" }),
      h("input", { id: `m-windows-${group.family}`, value: group.half_windows,
                   placeholder: "2, 4", autocomplete: "off",
                   oninput: e => { group.half_windows = e.target.value; } }),
      h("span", { class: "note",
                  text: "Neighbourhood radius, in grid cells. Comma separated." })));
  }
  return fields.length
    ? h("div", { class: "grid", style: "margin-top:10px" }, fields)
    : h("p", { class: "tiny muted", style: "margin:10px 0 0" },
        "No parameters — it needs nothing but the truth and the forecast.");
}

/// Show that a parameter is required *before* the user asks for a preview.
///
/// The service would refuse either way; finding out at the last step, after
/// filling in five blocks, is the part worth avoiding.
///
/// Tolerates a detached input: the metric adder builds its fields and then
/// refreshes them, so at that instant ``closest`` finds nothing.

function emptyMember() {
  return { name: "", leads: "" };
}

// The dropdown offers the models this server can see, minus the ones already on
// the roster: adding the same forecast twice is not a thing.
// Only models whose output is in the ruler's unit. A model in another unit
// cannot join this config -- every member has to be in the ruler's unit or the
// thresholds mean nothing -- and declaring a radar model in mm h-1 would produce
// a config that says something untrue about it and then verifies nothing.
function modelChoices() {
  const packages = state.catalog && state.catalog.packages;
  const truth = groundTruth($("#c-truth").value.trim());
  const unit = truth && truth.unit;
  const known = packages
    ? (packages.packages || []).filter(p => p.kind === "model").map(p => p.id)
    : [];
  const chosen = new Set(state.memberRows.map(r => r.name));
  return known
    .filter(id => !chosen.has(id))
    .filter(id => !unit || (groundTruth(id) || {}).unit === unit);
}

// The leads a model's own run configs allow: the horizon it asked for, in the
// one time step this config has. It is a starting point, not a fence -- the box
// stays editable, and the planner clips per run and says which leads it dropped.
function leadsFor(model) {
  const all = state.catalog && state.catalog.leads;
  const leads = all && all.by_model && all.by_model[model];
  return leads && leads.length ? leads.join(", ") : "";
}

function cadenceLabel() {
  const all = state.catalog && state.catalog.leads;
  return all && all.cadence_minutes ? `${all.cadence_minutes} min` : "the source step";
}

function renderMemberPicker() {
  const pick = $("#member-pick");
  clear(pick);
  const choices = modelChoices();
  const truth = groundTruth($("#c-truth").value.trim());
  for (const id of choices.length ? choices : [""]) {
    pick.append(h("option", { value: id, text: id || (state.memberRows.length
      ? "every model in this unit is on the roster"
      : truth && truth.unit
        ? `no model the catalog found produces ${truth.unit}`
        : "pick a ground truth first") }));
  }
  pick.disabled = !choices.length;
}

function renderMembers() {
  renderMemberPicker();

  const rows = state.memberRows.map((row) => h("div", { class: "grid",
    style: "margin-bottom:8px" },
    h("div", { class: "field" }, h("label", { text: "forecast" }),
      h("input", { value: row.name, readonly: true })),
    h("div", { class: "field" }, h("label", { text: "leads (minutes)" }),
      h("input", { value: row.leads, placeholder: "10, 30, 60",
                   oninput: e => row.leads = e.target.value })),
    h("div", { class: "field" }, h("label", { text: " " }),
      h("button", { class: "act danger", text: "Remove", onclick: () => {
        state.memberRows = state.memberRows.filter(r => r !== row);
        renderMembers();
      } }))));

  mount($("#members"), rows.length
    ? h("div", {}, rows)
    : empty("No forecasts on the roster yet."));

  // Kept while there are rows, even once the list is exhausted: it is the only
  // place that says where the numbers in those boxes came from.
  $("#member-hint").textContent = state.memberRows.length
    ? `leads = run horizon x ${cadenceLabel()}, from the model's own run configs`
    : "";
}


function numbers(text) {
  return String(text || "").split(",").map(s => s.trim()).filter(Boolean).map(Number);
}

function buildSpec() {
  const id = $("#c-id").value.trim();
  const name = $("#c-name").value.trim();
  if (!id) throw new Error("A group id is required.");
  if (!name) throw new Error("A display name is required.");

  // The member's own output field, from the model's own package. Falling back to
  // the ruler would let a model be declared in a unit it does not produce: the
  // config would then be a true-looking lie about it, and the run would be turned
  // away for the wrong reason.
  const derived = groundTruth($("#c-truth").value.trim()) || {};
  const members = state.memberRows.filter(r => r.name).map(r => {
    const own = groundTruth(r.name) || {};
    return {
      name: r.name.trim(),
      variable: own.variable || derived.variable || "",
      unit: own.unit || $("#c-unit").value.trim(),
      leads: numbers(r.leads).map(Number),
    };
  });

  if (!members.length) throw new Error("At least one member is required.");
  if (!state.metricGroups.length) throw new Error("At least one metric group is required.");

  return {
    group: { id, name },
    ruler: {
      ground_truth: $("#c-truth").value.trim(),
      unit: $("#c-unit").value.trim(),
      time_step_minutes: Number($("#c-step").value),
      extent: [Number($("#c-lon0").value), Number($("#c-lat0").value),
               Number($("#c-lon1").value), Number($("#c-lat1").value)],
      resolution: Number($("#c-res").value),
    },
    members,
    metrics: state.metricGroups.map(g => {
      const needs = paramsForFamily(g.family);
      // Only carry a parameter the family actually reads. An empty list is a
      // claim about the config, not an absence in it.
      return {
        family: g.family,
        items: g.items,
        levels: needs.has("level") ? numbers(g.levels).map(Number) : [],
        half_windows: needs.has("half_window")
          ? numbers(g.half_windows).map(Number) : [],
      };
    }),
  };
}


function renderPlan(plan) {
  const parts = [];
  const found = Number(plan.runs_discovered) || 0;
  const refusals = groupRejections(plan.rejections || []);
  const rejected = (plan.rejections || []).length;
  const usable = Math.max(0, found - rejected);
  // distinct: one per task would count 72 where there are six
  const inits = [...new Set((plan.tasks_detail || []).map(t => t.init_time))].sort();

  if (plan.warnings && plan.warnings.length) {
    parts.push(notice("warn", "The catalog disagrees:", plan.warnings));
  }

  // A number that is a product of three other numbers should be shown as that
  // product. `tasks: 72` on its own is a total with nothing behind it, and the
  // question it raises -- 72 of what? -- is the one the preview exists to answer.
  const possible = plan.members.length * inits.length * plan.leads.length;
  const maths = !plan.tasks ? ""
    : plan.tasks === possible
      ? `${plan.members.length} members x ${inits.length} init times x ${plan.leads.length} leads`
      : `${num(plan.tasks)} of a possible ${num(possible)}: not every member covers every lead at every init time`;

  parts.push(h("div", { class: "grid tight" },
    fact("tasks", num(plan.tasks), maths),
    fact("runs found", found
      ? `${num(found)} — ${num(usable)} usable, ${num(rejected)} rejected`
      : num(found),
      "every run the platform has, not just this config's"),
    fact("members", plan.members.join(", ") || "—"),
    fact("leads", plan.leads.length ? plan.leads.join(", ") : "—"),
    fact("init times", inits.length
      ? `${num(inits.length)} — ${inits[0]} → ${inits[inits.length - 1]}`
      : "—")));

  if (!plan.tasks && found) {
    parts.push(notice("bad",
      "Every run was rejected, so this config would verify nothing. That is an error, not an empty success."));
  }

  const byMember = planByMember(plan.tasks_detail || []);
  if (byMember.length) {
    parts.push(h("div", { class: "plan-title", text: "What would be verified" }));
    parts.push(h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", { text: "member" }),
        h("th", { text: "leads (min)" }),
        h("th", { class: "num", text: "frames" }),
        h("th", { class: "num", text: "init times" }),
        h("th", { text: "valid times" }))),
      h("tbody", {}, byMember.map(m => h("tr", {},
        h("td", {}, h("code", { text: m.member })),
        h("td", { text: m.leads.join(", ") }),
        h("td", { class: "num", text: num(m.frames) }),
        h("td", { class: "num", text: num(m.inits) }),
        h("td", {}, h("code", { class: "tiny", text: m.span }))))))));
  }

  if (refusals.length) {
    parts.push(h("div", { class: "plan-title", text: "What was left out" }));
    parts.push(h("div", { class: "scroll" }, h("table", {},
      h("thead", {}, h("tr", {},
        h("th", { text: "reason" }),
        h("th", { class: "num", text: "runs" }),
        h("th", { text: "which models" }),
        h("th", { text: "what it means" }))),
      h("tbody", {}, refusals.map(r => h("tr", {},
        h("td", {}, h("code", { text: r.reason })),
        h("td", { class: "num", text: num(r.runs) }),
        h("td", {}, r.models.join(", ")),
        h("td", { class: "muted" }, r.meaning,
          r.details.length === 1
            ? h("div", { class: "tiny", text: r.details[0] }) : null)))))));
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

/// The accepted half of the plan, by member.
///
/// `tasks: 72` is a total with nothing behind it. The detail is already in the
/// response, and a plan is a decision: which frames, for which member, at which
/// lead, over which init times.
function planByMember(detail) {
  const by = new Map();
  for (const t of detail) {
    if (!by.has(t.member)) by.set(t.member, { member: t.member, leads: new Set(), inits: new Set(), valid: [], frames: 0 });
    const g = by.get(t.member);
    g.frames += 1;
    g.leads.add(t.lead_minutes);
    g.inits.add(t.init_time);
    g.valid.push(t.valid_time);
  }
  return [...by.values()].map(g => ({
    member: g.member,
    frames: g.frames,
    leads: [...g.leads].sort((a, b) => a - b),
    inits: g.inits.size,
    span: `${g.valid.sort()[0]} → ${g.valid[g.valid.length - 1]}`,
  })).sort((a, b) => a.member.localeCompare(b.member));
}

/// The rejected half, grouped by reason and with the models named.
///
/// `12 not-on-roster` was the whole report, and it is the shape of report that
/// hides things: the two radar models that had been added to a satellite ruler
/// were in the twelve, and nothing on the page said so. The server had already
/// sent each one's model id and its own sentence about why.
function groupRejections(rejections) {
  const by = new Map();
  for (const r of rejections) {
    if (!by.has(r.reason)) {
      by.set(r.reason, { reason: r.reason, meaning: r.meaning || "",
                         runs: 0, models: new Set(), details: new Set() });
    }
    const g = by.get(r.reason);
    g.runs += 1;
    g.models.add(r.model);
    if (r.detail) g.details.add(r.detail);
  }
  return [...by.values()].map(g => ({
    reason: g.reason, meaning: g.meaning, runs: g.runs,
    models: [...g.models].sort(), details: [...g.details],
  })).sort((a, b) => b.runs - a.runs || a.reason.localeCompare(b.reason));
}

async function saveConfig() {
  const host = $("#create-result");
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

// What a run is called on screen. The platform's own name is an event id glued to
// an ISO stamp -- 20260701@2026-07-01T00:00:00Z -- which is exact and unreadable.
// Who was verified, against what, and from when: the three things that tell two
// runs apart. The id stays on the tooltip for when they are not enough.
function configLabel(c) {
  const who = c.display_name || (c.member_names || []).join(" + ") || "no member";
  const against = c.ground_truth || "";
  const raw = String(c.name || "");
  const when = raw.includes("@") ? raw.split("@").pop() : "";
  const stamp = /^\d{4}-\d{2}-\d{2}T/.test(when)
    ? when.slice(0, 16).replace("T", " ") + " UTC"
    : "";
  if (c.display_name) { return c.display_name; }   // the platform named it
  return [who, against, stamp].filter(Boolean).join(" · ");
}

async function loadConfigsIntoSelect() {
  const select = $("#q-config");
  const previous = select.value;
  let configs = state.configs;
  if (!configs.length) {
    try { configs = (await api("/api/configs")).configs; state.configs = configs; } catch (_) { configs = []; }
  }
  mount(select, configs.map(c => h("option", {
    value: c.group_id,
    // The name first: a run id is a timestamp and a hash, and nobody picks by
    // one. The id stays on the tooltip, which is where to look when the name
    // is not enough to tell two runs apart.
      text: configLabel(c),
      title: `${c.name || c.group_id}` + (c.n_metrics ? ` — ${c.n_metrics} metrics, ${c.n_members} member(s)` : ""),
  })));
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
  // Defaults come off the config, not off the clock. "The last week" read from
  // the wall clock is right for a live system and an empty page for a replayed
  // one, and the page has no way to tell the two apart -- so the window opens on
  // the most recent week this config was actually verified over.
  state.initSpan = (cfg.stored || {}).init_span || null;
  state.leads = cfg.leads || [];
  fillInitDefaults();
  fillLeadDefaults();

  const available = cfg.metrics_available || [];
  const select = $("#q-method");
  const previous = select.value;
  mount(select, available.map(m => h("option", { value: m.method, text: m.method })));
  if (previous && available.some(m => m.method === previous)) select.value = previous;
  if (available.length) await onMethodPicked();
  renderCoverage(cfg);
}

//: The default window: seven days.
const WEEK = 7 * 24 * 3600 * 1000;

//: What the quick picks offer, in days. ``null`` is everything that was stored.
const INIT_PICKS = [[1, "1 d"], [3, "3 d"], [7, "7 d"], [30, "30 d"], [null, "All"]];

function initDays() {
  const from = $("#q-init-from").value, to = $("#q-init-to").value;
  if (!from || !to) return null;
  return (new Date(to) - new Date(from)) / 86400000;
}

/** What the range currently is, on the button that opens the panel. The button
 * is the confirmation: there is no separate line to read, and nothing has to be
 * clicked elsewhere for the choice to have been made. */
function sayInit() {
  const from = $("#q-init-from").value, to = $("#q-init-to").value;
  const days = initDays();
  const span = days !== null && days > 0.05
    ? `  \u00b7  ${days < 10 ? days.toFixed(1) : Math.round(days)} d` : "";
  $("#btn-init-range").textContent =
    (from ? from.replace("T", " ") : "earliest") + "  \u2192  "
    + (to ? to.replace("T", " ") : "now") + span;
}

//: The five dropdowns of one end of the range. Cheaper to use than a calendar
//: and, unlike one, possible to put a button under.
const SEGMENTS = ["year", "month", "day", "hour", "minute"];

function parts(value) {
  const got = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value || "");
  const now = new Date();
  return got ? got.slice(1).map(Number)
             : [now.getFullYear(), now.getMonth() + 1, now.getDate(),
                now.getHours(), now.getMinutes()];
}

function stamp(parts) {
  const pad = value => String(value).padStart(2, "0");
  const days = new Date(parts[0], parts[1], 0).getDate();      // month is 1-based
  return `${parts[0]}-${pad(parts[1])}-${pad(Math.min(parts[2], days))}`
       + `T${pad(Math.min(parts[3], 23))}:${pad(Math.min(parts[4], 59))}`;
}

function range(lo, hi) {
  return Array.from({ length: hi - lo + 1 }, (unused, at) => lo + at);
}

function buildSeg(host, parts) {
  const choices = [
    range(2020, 2100),
    range(1, 12),
    range(1, new Date(parts[0], parts[1], 0).getDate()),
    range(0, 23),
    range(0, 59),
  ];
  mount(host, SEGMENTS.map((label, at) => {
    const pad = value => String(value).padStart(label === "year" ? 4 : 2, "0");
    const pick = h("select", { class: "seg-part", title: label, "aria-label": label });
    for (const value of choices[at]) {
      pick.appendChild(h("option", { value: String(value), text: pad(value) }));
    }
    pick.value = String(parts[at]);
    pick.onchange = () => {
      const now = parts.map((_, index) =>
        Number(host.querySelectorAll("select")[index].value));
      if (at === 1 || at === 0) buildSeg(host, now);      // the day count moved
    };
    return pick;
  }));
}

function panelParts(host) {
  return [...host.querySelectorAll("select")].map(pick => Number(pick.value));
}

function openInitPanel() {
  buildSeg($("#init-from-fields"), parts($("#q-init-from").value));
  buildSeg($("#init-to-fields"), parts($("#q-init-to").value));
  $("#init-panel").hidden = false;
  $("#init-panel").scrollIntoView({ block: "nearest" });
}

function closeInitPanel() { $("#init-panel").hidden = true; }

// How long the config actually spans. "All" means the window covers it, not
// that no window was chosen -- and initDays() answers with a number either way.
function coversEverything(days) {
  const span = state.initSpan;
  if (!span || days === null) { return false; }
  const full = (Date.parse(span[1]) - Date.parse(span[0])) / 86400000;
  return days >= full - 0.5;
}

function renderInitQuick() {
  const days = initDays();
  const near = (one, other) => one !== null && other !== null && Math.abs(one - other) < 0.2;
  mount($("#q-init-quick"), INIT_PICKS.map(([count, label]) => h("button", {
    class: "chip", type: "button", text: label,
    // All is the whole span, and its count is null -- so the old test excluded it
    // from ever looking pressed.
    "aria-pressed": String(count === null ? coversEverything(days) : near(days, count)),
    onclick: () => setInitDays(count),
  })));
}

function setInitDays(count) {
  const span = state.initSpan;
  const end = new Date();
  const start = count === null
    ? (span ? new Date(span[0]) : new Date(0))
    : new Date(end.getTime() - count * 86400000);
  $("#q-init-from").value = localStamp(start);
  $("#q-init-to").value = localStamp(end);
  closeInitPanel();
  sayInit();
  renderInitQuick();
}

/** The button the browser's own picker does not have. It is on the panel, where
 * the choosing happens, and it is the only way a change is taken. */
function applyInit() {
  $("#q-init-from").value = stamp(panelParts($("#init-from-fields")));
  $("#q-init-to").value = stamp(panelParts($("#init-to-fields")));
  closeInitPanel();
  sayInit();
  renderInitQuick();
  if (state.lastQuery) runQuery();
}

function fillInitDefaults() {
  const span = state.initSpan;
  // The window ends now: it follows the clock on a live system. It starts a week
  // before the data ends rather than a week before now, because on a replayed
  // dataset those are months apart and a week before now is an empty page -- and
  // the page cannot tell a replay from a live feed.
  const end = new Date();
  const anchor = span ? new Date(span[1]) : end;
  const start = new Date(Math.max(span ? new Date(span[0]).getTime() : 0,
                                  anchor.getTime() - WEEK));
  $("#q-init-from").value = localStamp(start);
  $("#q-init-to").value = localStamp(end);
  closeInitPanel();
  sayInit();
  renderInitQuick();
}

function fillLeadDefaults() {
  const leads = state.leads.slice().sort((a, b) => a - b);
  if (!leads.length) return;
  $("#q-lead-from").value = leads[0];
  $("#q-lead-to").value = leads[leads.length - 1];
}

function localStamp(date) {
  // `datetime-local` shows local time and has no zone. The store keeps UTC, so
  // the value is shifted to local for the box and shifted back on the way out.
  // Skipping either half reads a time hours off what was asked for, with no
  // error to say so.
  const pad = n => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
    + `T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function utcStamp(local) {
  if (!local) return "";
  const date = new Date(local);        // read as local, which is how it was written
  return isNaN(date.getTime()) ? "" : date.toISOString().replace(/\.\d+Z$/, "Z");
}

async function onMethodPicked() {
  const method = $("#q-method").value;
  const entry = ((state.selected || {}).metrics_available || []).find(m => m.method === method);
  // One value per parameter, not all of them. The control is tiled so adding the
  // rest is one click, but N thresholds against M models is N x M lines, and a
  // chart with ten lines on it says nothing about any of them. The middle value is
  // not more meaningful than the ends; something has to open, and this is the
  // choice that does not claim a meaning it does not have.
  state.hidden = [];
  state.picked = {};
  for (const key of ["level", "half_window"]) {
    const values = (entry && entry[key === "level" ? "levels" : "half_windows"]) || [];
    if (values.length) state.picked[key] = [values[Math.floor((values.length - 1) / 2)]];
  }
  renderParams(entry || null);
}

//: What to call each parameter on the form.
const PARAM_LABEL = { level: "level", half_window: "half window" };

function renderParams(entry) {
  const host = $("#q-params");
  if (!entry) return mount(host);
  const shown = ["level", "half_window"].filter(k => state.picked[k] !== undefined);
  if (!shown.length) {
    return mount(host, h("span", { class: "tiny muted" }, `${entry.family}: no parameters`));
  }
  // Built as one list and mounted once. ``mount`` clears what it is given, so a
  // row mounted per turn of a loop leaves the last row and nothing else -- and a
  // spatial metric has two rows, which is exactly the case that hides it.
  mount(host, shown.map(key => {
    const all = entry[key === "level" ? "levels" : "half_windows"] || [];
    // The row is drawn from the config's whole list, not from what is selected:
    // a chip that can be clicked off has to still be there to click back on.
    return h("div", { class: "param-row" },
      h("span", { class: "who" }, PARAM_LABEL[key] + unitFor(key)),
      h("div", { class: "chips" }, all.slice().sort((a, b) => a - b).map(value => h("button", {
        class: "chip param", type: "button", text: String(value),
        "aria-pressed": String(state.picked[key].includes(value)),
        onclick: () => {
          const at = state.picked[key].indexOf(value);
          if (at >= 0) state.picked[key].splice(at, 1); else state.picked[key].push(value);
          renderParams(entry);
        },
      }))));
  }));
}

//: A threshold is a number in the ruler's unit and means nothing without it: 35
//: is light rain in mm h-1 and nothing at all in dBZ.
function unitFor(key) {
  if (key === "half_window") return " (cells)";
  const unit = ((state.selected || {}).ruler || {}).unit;
  return unit ? ` (${unit})` : "";
}

//: The value of the axis select that asks for two dimensions at once.
const TWO_AXES = "init_time+lead_minutes";

function askedAxes() {
  return $("#q-axis").value.split("+");
}

//: A mesh is not a form to pick. It is what asking for two axes looks like --
//: offering a line chart of a two-dimensional answer would only be offering to
//: throw one of the axes away.
function kindOf() {
  return askedAxes().length > 1 ? "mesh" : state.kind;
}

function pickedParams() {
  const out = {};
  for (const [key, values] of Object.entries(state.picked || {})) {
    const sorted = values.slice().sort((a, b) => a - b);
    if (sorted.length) out[key] = sorted;
  }
  return out;
}

//: A parameter with more than one value is a dimension: it separates the marks
//: and gets expanded. With one value it is only a filter, and expanding by it
//: would put the same number in every row.
function manyParams() {
  const picked = pickedParams();
  return Object.keys(picked).filter(k => picked[k].length > 1);
}

function queryParams() {
  const params = new URLSearchParams();
  params.set("config", $("#q-config").value);
  params.set("method", $("#q-method").value);
  const initFrom = utcStamp($("#q-init-from").value);
  const initTo = utcStamp($("#q-init-to").value);
  if (initFrom) params.set("init_from", initFrom);
  if (initTo) params.set("init_to", initTo);
  const leadFrom = $("#q-lead-from").value.trim();
  const leadTo = $("#q-lead-to").value.trim();
  if (leadFrom) params.set("lead_from", leadFrom);
  if (leadTo) params.set("lead_to", leadTo);
  for (const [key, values] of Object.entries(pickedParams())) {
    params.set(key, values.join(","));
  }
  // Only when something is hidden: sending every member explicitly would be the
  // same query written longer, and the URL is worth keeping readable.
  if (state.hidden.length) shownMembers().forEach(name => params.append("member", name));
  return params;
}

async function runQuery() {
  const host = $("#query-result");
  // Every parameter has to keep at least one value. An empty list is not "no
  // filter" -- it is a question with no answer, and sending nothing would quietly
  // widen the query to every threshold the config ever stored, which is the
  // opposite of what unselecting the last one asks for.
  const none = Object.entries(state.picked || {})
    .filter(([, values]) => !values.length).map(([key]) => PARAM_LABEL[key]);
  if (none.length) {
    mount(host, notice("bad", `Pick at least one ${none.join(" and ")}.`));
    return;
  }
  mount(host, empty("Querying…"));
  const params = queryParams();
  // What the table expands by has to be what the chart draws, or the two would
  // describe one query in two shapes.
  const axes = askedAxes();
  const many = manyParams();
  const dims = axes.length > 1
    ? ["member", ...many, ...axes]
    : ["member", ...axes, ...many];
  dims.forEach(d => params.append("g", d));
  try {
    const result = await api(`/api/scores?${params.toString()}`);
    state.lastQuery = result;
    mount(host, renderScores(result));
    showChart(params);
  } catch (error) {
    mount(host, notice("bad", error.message));
    $("#chart-card").hidden = true;
    $("#chart-empty").hidden = false;
  }
}

//: How wide the chart has to come back, in CSS pixels.
function canvasWidth() {
  const pane = document.querySelector("#tab-results .answer");
  const card = pane ? pane.clientWidth : 0;
  return Math.max(520, Math.min(1400, Math.round(card - 40)));
}

function showChart(params) {
  const kind = kindOf();
  const many = manyParams();
  const chart = new URLSearchParams(params);
  chart.delete("g");
  chart.set("kind", kind);
  // The canvas is drawn at the width it will be shown at. Left at its own default
  // it comes out wider than the column and the browser scales it down, which
  // shrinks every label with it -- the picture is then legible only to whoever
  // opens the SVG on its own.
  chart.set("width", String(canvasWidth()));
  if (kind === "mesh") {
    chart.set("x", "init_time");
    chart.set("series", "lead_minutes");
    // A third dimension becomes further panels rather than further grids.
    for (const extra of many) chart.append("panel", extra);
  } else {
    chart.set("x", $("#q-axis").value);
    // One line per model. A parameter filtered to more than one value becomes a
    // panel each, not another line: three thresholds overlaid read as three ways
    // of scoring one forecast, and side by side they read as one score moving
    // with the threshold.
    chart.set("series", "member");
    for (const extra of many) chart.append("panel", extra);
  }

  state.chartUrl = `/api/chart?${chart.toString()}`;
  // No download link: the picture is for looking at, and what somebody who wants
  // to draw their own can use is the table's CSV.
  $("#chart").src = state.chartUrl;
  $("#chart-card").hidden = false;
  $("#chart-empty").hidden = true;
  // What the picture is of, under the picture rather than across the plot: the
  // canvas is meant to be worth saving on its own.
  const filters = ((state.lastQuery || {}).meta || {}).s || {};
  const said = Object.entries(filters)
    .map(([name, value]) => `${name}=${JSON.stringify(value)}`).join(", ");
  $("#chart-note").textContent = chart.get("method")
    + (said ? ` over ${said}` : " over everything stored");
  renderLegend();
  renderKindChips();
}

//: The models the chart should draw, in roster order.
function shownMembers() {
  const all = ((state.selected || {}).member_names) || [];
  return all.filter(name => !state.hidden.includes(name));
}

function renderLegend() {
  const host = $("#chart-legend");
  const all = ((state.selected || {}).member_names) || [];
  const colours = (state.selected || {}).series_colours || {};
  // A member whose line is drawn with a hole in it. The canvas shows the hole --
  // a line stops and starts again -- but not whether that is a gap in the data or
  // an artefact of the drawing, so the key is where it gets said.
  const gapped = new Set(((state.lastQuery || {}).records || [])
    .filter(r => r.value === null).map(r => r.member));
  if (all.length < 2) return mount(host);      // one model needs no key
  mount(host, all.map(name => {
    const shown = !state.hidden.includes(name);
    return h("button", {
      class: "chip legend", type: "button",
      title: (shown ? `hide ${name}` : `show ${name}`)
        + (gapped.has(name) ? " (its line has gaps)" : ""),
      "aria-pressed": String(shown),
      onclick: () => {
        if (shown && shownMembers().length === 1) {
          // Hiding the last one would send no member filter at all, which is
          // every model -- the opposite of what clearing the last key asks for.
          mount($("#query-result"), notice("bad", "Pick at least one model."));
          return;
        }
        const at = state.hidden.indexOf(name);
        if (at >= 0) state.hidden.splice(at, 1); else state.hidden.push(name);
        if (state.lastQuery) runQuery(); else renderLegend();
      },
    }, h("span", { class: "dot", style: `background:${colours[name] || "#888"}` }),
       name + (gapped.has(name) ? " (gap)" : ""));
  }));
}

function renderKindChips() {
  if (kindOf() === "mesh") {
    mount($("#q-kind"));
    $("#q-kind-note").textContent = "two axes: drawn as a grid";
    return;
  }
  $("#q-kind-note").textContent = "";
  mount($("#q-kind"), ["line", "bar"].map(name => h("button", {
    class: "chip", type: "button", text: name,
    "aria-pressed": String(state.kind === name),
    onclick: () => {
      state.kind = name;
      renderKindChips();
      // The form is a way of looking at the answer, not a different question, so
      // it redraws at once -- the same rule the axis follows. The filters are
      // the ones that wait for the button, because those change what is asked.
      if (state.lastQuery) runQuery();
    },
  })));
}

// ==================================================================
// wiring
// ==================================================================
if ($("#btn-reload-configs")) $("#btn-reload-configs").addEventListener("click", loadConfigs);
if ($("#btn-add-member")) $("#btn-add-member").addEventListener("click", () => {
  const id = $("#member-pick").value;
  if (!id) return;
  // Picking one is the whole action: the leads are already known from its runs.
  state.memberRows.push({ name: id, leads: leadsFor(id) });
  renderMembers();
});
if ($("#btn-save")) $("#btn-save").addEventListener("click", saveConfig);
// Choosing the ground truth is what fills the ruler in, so it is also what has to
// clear it again when the choice changes to a package that declares less.
if ($("#c-truth")) $("#c-truth").addEventListener("input", applyGroundTruth);
if ($("#c-truth")) $("#c-truth").addEventListener("change", applyGroundTruth);
$("#btn-query").addEventListener("click", runQuery);
for (const id of ["#q-init-from", "#q-init-to"]) {
  $(id).addEventListener("change", () => { sayInit(false); renderInitQuick(); });
}
$("#btn-init-range").addEventListener("click", openInitPanel);
$("#btn-init-ok").addEventListener("click", applyInit);
$("#q-config").addEventListener("change", () => {
  setTimeout(syncMethodChips, 60);   // the metrics change with the config
  onConfigPicked();
});
$("#q-method").addEventListener("change", onMethodPicked);
$("#q-axis").addEventListener("change", () => {
  if (state.lastQuery) runQuery(); else renderKindChips();
});

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
      pill(`ruler ${result.meta.group ? result.meta.group.ruler.fingerprint : "—"}`),
      h("span", { class: "spacer" }),
      // Beside the table and not in the chart card: this is the table, saved.
      // Whoever wants their own plot takes these rows and draws them.
      h("button", { class: "act ghost", id: "btn-csv", text: "Save table (CSV)",
        title: "Save the rows below as a CSV file",
        onclick: () => saveCsv(result) })),
    dropped ? h("p", { class: "tiny muted" },
      `${dropped} group(s) had no samples and are not shown.`) : null,
    h("div", { class: "scroll", style: "margin-top:10px" },
      h("table", {}, h("thead", {}, head), h("tbody", {}, body))));
}

//: The table, as a file.
//:
//: Built from the rows already on screen rather than fetched again, so the file
//: cannot disagree with the table it was saved from. A comment header carries
//: what the numbers are: a CSV of bare columns is a file nobody can label later.
function saveCsv(result) {
  const dims = result.dims || [];
  const meta = result.meta || {};
  const label = meta.label || "value";
  const lines = [
    `# ${label} (${meta.family || "?"})`,
    `# config ${(meta.group || {}).id || "?"}, `
      + `ruler ${((meta.group || {}).ruler || {}).fingerprint || "?"}`,
    `# filters ${JSON.stringify(meta.s || {})}`,
    ["method", ...dims, "value", "samples"].join(","),
  ];
  for (const row of result.records) {
    const cells = [meta.method, ...dims.map(d => row[d]), row.value, row.n_valid];
    lines.push(cells.map(cell => {
      const text = cell === null || cell === undefined ? "" : String(cell);
      return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
    }).join(","));
  }
  const blob = new Blob([lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: `${(meta.group || {}).id || "query"}-${meta.method}.csv` });
  document.body.appendChild(link);
  link.click();
  link.remove();
  // Revoked rather than left: an object URL holds its blob for the life of the
  // document, and a page that queries all afternoon would hold every one.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function renderMetricGroups() {
  const groups = state.metricGroups;
  mount($("#metric-groups"), groups.length
    ? groups.map((g, i) => h("div", { class: "groupbox", "data-family": g.family },
        h("div", { class: "head" },
          h("strong", { text: familyLabel(g.family) }),
          h("span", { class: "spacer" }),
          h("button", { class: "link danger", text: "remove",
            onclick: () => { groups.splice(i, 1); renderMetricGroups(); } })),
        h("div", { class: "chips", style: "margin-top:6px" },
          g.items.map(x => h("span", { class: "chip", text: x }))),
        metricParamFields(g)))
    : empty("No families. A config with no metrics computes nothing."));

  // A family dropped by mistake comes back with one click, and nothing else can
  // be added: the three are the three.
  const have = new Set(groups.map(g => g.family));
  mount($("#metric-add"), defaultFamilies().filter(f => !have.has(f.family)).map(f =>
    h("button", { class: "act quiet", text: `+ ${f.label}`,
      onclick: () => { groups.push(defaultGroup(f.family)); renderMetricGroups(); } })));
}

// Every metric as a button, one row per family. The select underneath stays and
// keeps doing the work: a click sets its value and fires change, so nothing
// downstream has to know that what is being clicked is no longer the select.
const FAMILY_ROWS = ["continuous", "yes_or_no", "spatial", "moments"];

function syncMethodChips() {
  const select = $("#q-method");
  if (!select) { return; }
  if (!select.dataset.watched) {
    select.dataset.watched = "1";
    new MutationObserver(() => syncMethodChips()).observe(select, { childList: true });
  }
  let host = $("#q-methods");
  if (!host) {
    const field = select.closest(".field") || select;
    host = h("div", { id: "q-methods" });
    // Outside the row, not inside it: Config and Metric share one flex row,
    // and a panel of buttons dropped between them is squeezed onto one line.
    const outside = field.closest(".row") || field;
    outside.parentNode.insertBefore(host, outside.nextSibling);
    field.style.display = "none";
  }
  const available = ((state.selected || {}).metrics_available) || [];
  let rows = FAMILY_ROWS
    .map(family => ({ family, items: available.filter(m => m.family === family) }))
    .filter(row => row.items.length);
  if (!rows.length && select.options.length) {
    rows = [{ family: "metrics", items: Array.from(select.options).map(o => ({ method: o.value, label: o.textContent })) }];
  }
  const on = "background:#e7eefb;color:#1f5fbf;font-weight:600;border-color:#b9cdf0";
  mount(host, rows.map(row => h("div", { class: "field", style: "margin-bottom:9px" },
    h("label", { text: row.family.replace(/_/g, " ") }),
    h("div", { class: "chips" }, row.items.map(m => h("button", {
      class: "chip", type: "button", text: m.method, title: m.label || m.method,
      style: m.method === select.value ? on : "",
      onclick: () => { select.value = m.method;
        select.dispatchEvent(new Event("change")); syncMethodChips(); },
    }))))));
}


async function boot() {
  await Promise.all([loadConfigs(), loadCatalog(), loadMetrics()]);
  // renderMembers is the roster editor of the removed New config panel.
  renderMetricGroups();
  showTab("results");
}

boot();


/// Follow a run to its end, and refresh what it changed when it gets there.
///
/// Shared by the button and by a page that has just loaded: the run lives in
/// the daemon, and the page does not.
function watchRun(jobId, cfg, ui, cursor) {
  if (state.poll) { clearTimeout(state.poll); state.poll = null; }
  state.job = jobId;

  const poll = async () => {
    let view;
    try {
      view = await api(`/api/jobs/${encodeURIComponent(jobId)}?after=${cursor}`);
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

/// Pick up a run that was already going when this page arrived.
///
/// Without it the page forgets a run the moment it reloads or somebody reopens
/// the config: the work carries on in the daemon, the tab has no idea, and the
/// person who started it clicks Run again and is told the server is busy with
/// their own job.
async function resumeRun(cfg) {
  if (state.job) return;
  let live = null;
  try {
    live = ((await api("/api/jobs")).jobs || []).find(
      j => j.kind === "run" && j.state === "running" && j.group === cfg.group_id);
  } catch (error) {
    return;                  // the Run card still works; it can only fail later
  }
  const ui = {
    log: $("#run-log"), verdict: $("#run-verdict"),
    status: $("#run-status"), button: $("#btn-run"),
  };
  if (!live || !ui.log || !ui.verdict || !ui.status || !ui.button) return;
  ui.log.hidden = false;
  ui.log.textContent = "attached to a run that was already going" + String.fromCharCode(10);
  mount(ui.verdict, notice("warn", "A run under this config is in progress."));
  ui.button.disabled = true;
  watchRun(live.id, cfg, ui, 0);
}

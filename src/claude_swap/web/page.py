"""The dashboard's single page.

One string, no build step, no framework, no CDN: the page has to render on a
cluster login node reached through an SSH tunnel, where fetching anything from
the internet -- a web font included -- is exactly what will not work. The face
is ``Geist`` when the machine has it and the platform sans otherwise; the
identity is carried by geometry, not by a download.

THE SYSTEM IS MONOCHROME, AND THAT IS A DATA DECISION. Quota is a single ratio
against a limit, so each window is a METER: an ink fill on a hairline track, the
track a lighter step of the same ramp so the bar reads as one object at any
level. Severity is not a hue. It is carried by the number's weight and by a
label -- bold at 20% left, and at zero the word "limit reached" beside a glyph,
in the one red the system allows. Red means exactly one thing on this page, so
it is never spent on decoration.

THE FRAMING IS HEADROOM. Every figure reads "x% left" and every meter drains.
The question this page answers is "where should I work next", and the answer is
the account with the most room, so the quantity on screen is the one being
compared, not its complement.

The one rule that is policy rather than taste: an account billing paid credits
gets a solid ink pill and a sentence, never the healthy treatment. It works, so
it stays switchable by hand -- but it is not spare capacity, and it must not
look like any.
"""

from __future__ import annotations

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-switch</title>
<style>
  :root {
    color-scheme: light;
    --canvas:#f5f5f5; --panel:#fafafa; --paper:#ffffff;
    --ink:#0a0a0a; --ink-soft:#171717; --mid:#737373; --hairline:#e5e5e5;
    --track:#e5e5e5; --ember:#e7000b; --on-ink:#ffffff;
    --shadow:0 1px 1px rgba(0,0,0,.025), 0 2px 2px rgba(0,0,0,.02);
  }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) {
      color-scheme: dark;
      --canvas:#0a0a0a; --panel:#111111; --paper:#171717;
      --ink:#fafafa; --ink-soft:#e5e5e5; --mid:#a3a3a3; --hairline:#262626;
      --track:#2a2a2a; --ember:#ff6b6b; --on-ink:#0a0a0a;
      --shadow:none;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --canvas:#0a0a0a; --panel:#111111; --paper:#171717;
    --ink:#fafafa; --ink-soft:#e5e5e5; --mid:#a3a3a3; --hairline:#262626;
    --track:#2a2a2a; --ember:#ff6b6b; --on-ink:#0a0a0a;
    --shadow:none;
  }

  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--canvas); color: var(--ink);
    font: 14px/1.5 "Geist", "Geist Variable", -apple-system, "Segoe UI", system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
  .wrap { max-width: 760px; margin: 0 auto; padding-block: 32px 64px; padding-inline: 20px; }

  /* -- masthead -------------------------------------------------------- */
  header { display: flex; align-items: baseline; flex-wrap: wrap; gap: 12px; margin-bottom: 12px; }
  h1 { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; margin: 0; }
  .stamp { margin-left: auto; font-size: 12px; color: var(--mid); font-variant-numeric: tabular-nums; }

  /* -- KPI row: one tile per provider, its ACTIVE account's headroom ---- */
  .kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .tile {
    background: var(--paper); border: 1px solid var(--hairline); border-radius: 24px;
    box-shadow: var(--shadow); padding: 20px; display: flex; flex-direction: column; gap: 6px;
  }
  .label { font-size: 12px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; color: var(--mid); }
  .value { font-size: 30px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.1; color: var(--ink); }
  .value small { font-size: 14px; font-weight: 400; letter-spacing: 0; color: var(--mid); margin-left: 6px; }
  .value.ember { color: var(--ember); }
  .sub { font-size: 13px; color: var(--mid); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

  /* -- provider panels: a tonal step, not a divider ---------------------- */
  .provider { background: var(--panel); border-radius: 24px; padding: 20px; margin-bottom: 12px; }
  .provider > h2 { font-size: 12px; font-weight: 500; letter-spacing: .05em; text-transform: uppercase; color: var(--mid); margin: 0 0 12px 4px; }
  .provider > h2 span { color: var(--mid); font-weight: 400; letter-spacing: 0; text-transform: none; margin-left: 8px; }

  /* -- account card ------------------------------------------------------ */
  .card {
    background: var(--paper); border: 1px solid var(--hairline); border-radius: 24px;
    box-shadow: var(--shadow); padding: 20px; margin-bottom: 8px;
  }
  .card:last-child { margin-bottom: 0; }
  .top { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .slot { font-size: 13px; color: var(--mid); font-variant-numeric: tabular-nums; flex: none; }
  .name { font-weight: 500; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .grow { flex: 1; }

  /* pills: 18px is the only interactive radius in this system */
  .pill {
    display: inline-flex; align-items: center; height: 24px; padding: 0 10px; border-radius: 18px;
    font-size: 12px; font-weight: 500; line-height: 1; white-space: nowrap; flex: none;
    border: 1px solid var(--hairline); color: var(--mid); background: transparent;
  }
  .pill.solid { background: var(--ink-soft); border-color: var(--ink-soft); color: var(--on-ink); }
  .pill.soft  { background: var(--canvas); border-color: transparent; color: var(--ink); }

  button {
    font: inherit; font-size: 13px; font-weight: 500; height: 32px; padding: 0 14px;
    border-radius: 18px; border: 1px solid var(--hairline); background: transparent; color: var(--ink);
    cursor: pointer; flex: none; white-space: nowrap;
  }
  button.primary { background: var(--ink); border-color: var(--ink); color: var(--on-ink); }
  button.ghost { background: var(--canvas); border-color: transparent; color: var(--mid); cursor: default; }
  button:hover:not(:disabled):not(.ghost) { border-color: var(--ink); }
  button:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  button:disabled { opacity: .6; }
  input, select { font: inherit; color: var(--ink); background: var(--paper); border: 1px solid var(--hairline); border-radius: 10px; min-height: 36px; padding: 6px 10px; max-width: 100%; }
  input:focus-visible, select:focus-visible, summary:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; }
  input[type="checkbox"] { min-height: auto; accent-color: var(--ink); }
  .preferences, .actions, .auto-heading { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .preferences { margin-bottom: 20px; gap: 12px; color: var(--mid); font-size: 12px; }
  .preferences label { display: inline-flex; align-items: center; gap: 6px; }
  .actions { margin-block: 12px; }
  .actions button { min-height: 36px; height: auto; padding-block: 6px; }
  .notice, .hint { color: var(--mid); font-size: 12px; margin: 8px 0; overflow-wrap: anywhere; }
  .notice { padding-inline: 4px; }
  .auto-panel { border-top: 1px solid var(--hairline); padding-top: 14px; margin-top: 16px; }
  .auto-heading h3 { font-size: 13px; font-weight: 500; margin: 0; }
  .threshold-field { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
  .threshold-field input { width: 90px; }
  .events { max-height: 160px; overflow-y: auto; padding-left: 22px; color: var(--mid); font-size: 12px; overflow-wrap: anywhere; }
  .events li { padding-block: 4px; }
  .auto-error { color: var(--ember); font-size: 12px; overflow-wrap: anywhere; }
  summary { cursor: pointer; font-size: 12px; color: var(--mid); }
  dialog { width: min(460px, calc(100% - 32px)); max-height: calc(100% - 32px); overflow: auto; padding: 24px; border: 1px solid var(--hairline); border-radius: 24px; background: var(--paper); color: var(--ink); box-shadow: var(--shadow); }
  dialog::backdrop { background: rgba(0,0,0,.4); }
  dialog h2 { margin: 0 0 12px; font-size: 18px; font-weight: 500; }
  dialog p { font-size: 13px; overflow-wrap: anywhere; }
  .field { display: grid; gap: 6px; margin-block: 14px; font-size: 13px; }
  .check { display: flex; align-items: start; gap: 8px; font-size: 13px; margin-block: 14px; }
  .check input { flex: none; margin-top: 4px; }
  .dialog-actions { justify-content: flex-end; margin-bottom: 0; }
  #dialog-feedback { color: var(--ember); }
  [hidden] { display: none !important; }

  /* -- meters: one per window, ink on a lighter step of the same ramp ---- */
  .meters { margin-top: 14px; display: grid; gap: 10px; }
  .meter { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; align-items: baseline; }
  .meter .label { font-size: 11.5px; }
  .meter .num { font-size: 13px; color: var(--ink); font-variant-numeric: tabular-nums; text-align: right; }
  .meter .num.low { font-weight: 600; }
  .meter .num.out { font-weight: 600; color: var(--ember); }
  .meter .num span { color: var(--mid); font-weight: 400; }
  .meter .track { grid-column: 1 / -1; height: 6px; border-radius: 6px; background: var(--track); overflow: hidden; }
  .meter .fill { height: 100%; border-radius: 6px; background: var(--ink); }
  .meter .fill.out { background: var(--ember); }

  /* -- state line under the meters --------------------------------------- */
  .state { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--hairline); display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: 13px; color: var(--mid); }
  .state.out { color: var(--ember); }
  .state .glyph { font-size: 10px; }

  /* -- the signed-in-but-unmanaged row ----------------------------------- */
  .adopt { background: var(--paper); border: 1px solid var(--hairline); border-radius: 24px; box-shadow: var(--shadow); padding: 16px 20px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .adopt .t { font-size: 13px; color: var(--mid); min-width: 0; }
  .adopt .t b { font-weight: 500; color: var(--ink); }
  .empty { font-size: 13px; color: var(--mid); padding: 4px; }
  code { font-family: ui-monospace, "SF Mono", Menlo, monospace; font-size: 12px; color: var(--ink); background: var(--canvas); padding: 1px 6px; border-radius: 6px; }

  #toast {
    position: sticky; bottom: 16px; margin-top: 20px; padding: 12px 16px; border-radius: 18px;
    background: var(--ink); color: var(--on-ink); font-size: 13px; display: none; box-shadow: var(--shadow);
  }
  #toast.show { display: block; }
  #toast.ember { background: var(--ember); color: #fff; }

  @media (max-width: 480px) {
    .wrap { padding-inline: 16px; }
    .tile, .card, .provider { border-radius: 18px; padding: 16px; }
    .value { font-size: 26px; }
    .top { flex-wrap: wrap; }
  }
  @media (min-width: 1200px) {
    .wrap { max-width: 1440px; }
    .providers { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); align-items: start; gap: 16px; }
  }
  @media (prefers-reduced-motion: no-preference) {
    .meter .fill { transition: width .35s ease; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>agent-switch</h1>
    <span class="stamp" id="stamp">loading…</span>
  </header>

  <div class="preferences">
    <label for="theme">Theme <select id="theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label>
    <label for="watch"><input id="watch" type="checkbox" checked> Live updates</label>
    <span id="watch-status">Every 20 seconds · auto-switch runs independently</span>
  </div>

  <section class="kpis" id="kpis" aria-label="Active accounts"></section>

  <div class="providers">
  <section class="provider" aria-labelledby="claude-heading"><h2 id="claude-heading">Claude Code<span id="claude-count"></span></h2><p class="notice" id="claude-switch-notice">CLI only. Claude desktop, including the Code tab, has a separate sign-in and is not switched here.</p><div id="claude-actions" class="actions"></div><div id="claude"></div><div id="claude-auto" class="auto-panel"></div></section>
  <section class="provider" aria-labelledby="codex-heading"><h2 id="codex-heading">Codex<span id="codex-count"></span></h2><p class="notice" id="codex-switch-notice" hidden></p><div id="codex-actions" class="actions"></div><div id="codex"></div><div id="codex-auto" class="auto-panel"></div></section>
  </div>

  <div id="toast" role="status" aria-live="polite"></div>
</div>
<dialog id="action-dialog" aria-labelledby="dialog-title" aria-describedby="dialog-description">
  <form id="dialog-form">
    <h2 id="dialog-title"></h2>
    <p id="dialog-description"></p>
    <div id="dialog-fields"></div>
    <p id="dialog-feedback" role="alert" hidden></p>
    <div class="actions dialog-actions"><button type="button" id="dialog-cancel">Cancel</button><button type="submit" class="primary" id="dialog-submit" data-action>Confirm</button></div>
  </form>
</dialog>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};
const providers = {claude: "Claude Code", codex: "Codex"};
const providerUI = {};
let state = {}, busy = false, requestVersion = 0, loadingVersion = 0, toastTimer;
let dialogAction = null, dialogOpener = null;

async function api(path, options) {
  const opts = Object.assign({headers: {"X-Auth-Token": TOKEN}}, options || {});
  const response = await fetch(path, opts);
  const body = await response.json();
  return {ok: response.ok, body};
}

function safeMessage(message, secret) {
  let text = String(message);
  for (const value of [TOKEN, secret]) {
    if (value) text = text.split(value).join("[redacted]");
  }
  return text;
}

function toast(message, ember, persistent = false) {
  const n = $("toast");
  clearTimeout(toastTimer);
  n.textContent = safeMessage(message);
  n.className = "show" + (ember ? " ember" : "");
  if (!ember && !persistent) toastTimer = setTimeout(() => { n.className = ""; }, 5000);
}

function duration(seconds) {
  if (!seconds || seconds <= 0) return null;
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
  if (d) return `${d}d ${h}h`;
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

// Headroom on the account's WORST window: that is what limits it.
function headroom(a) {
  const ws = (a.windows || []).filter((w) => typeof w.usedPercent === "number");
  if (ws.length) return Math.max(0, Math.min(...ws.map((w) => 100 - w.usedPercent)));
  return (typeof a.percent === "number") ? Math.max(0, 100 - a.percent) : null;
}
function soonestReset(a) {
  const ws = (a.windows || []).filter((w) => w.resetAfterSeconds > 0);
  return ws.length ? Math.min(...ws.map((w) => w.resetAfterSeconds)) : null;
}

// -- KPI tile: one per provider, its active account --------------------------
function tile(name, data) {
  const t = el("div", "tile");
  t.appendChild(el("div", "label", name + " · active"));
  const active = (data.accounts || []).find((a) => a.active);
  const v = el("div", "value");
  if (!data.available) { v.textContent = "—"; t.appendChild(v); t.appendChild(el("div", "sub", data.error || "unavailable")); return t; }
  if (!active) { v.textContent = "—"; t.appendChild(v); t.appendChild(el("div", "sub", "no active account")); return t; }
  const left = headroom(active);
  if (active.onCredits) {
    v.textContent = "0%"; v.appendChild(el("small", null, "included quota"));
    t.appendChild(v);
    const s = el("div", "sub"); s.appendChild(el("span", "pill solid", "paid credits")); t.appendChild(s);
  } else if (left === null) {
    v.textContent = "?"; t.appendChild(v); t.appendChild(el("div", "sub", active.sentinel || active.error || "usage unknown"));
  } else {
    v.textContent = left + "%"; v.appendChild(el("small", null, "left"));
    if (left <= 0) v.classList.add("ember");
    t.appendChild(v);
    const r = duration(soonestReset(active));
    t.appendChild(el("div", "sub", (active.email || "") + (r ? " · resets " + r : "")));
  }
  return t;
}

// -- meter: one window ---------------------------------------------------------
function meter(w) {
  const left = Math.max(0, 100 - (w.usedPercent ?? 0));
  const m = el("div", "meter");
  m.appendChild(el("span", "label", `${w.label} window`));
  const num = el("span", "num" + (left <= 0 ? " out" : left <= 20 ? " low" : ""));
  num.textContent = `${left}% left`;
  const r = duration(w.resetAfterSeconds);
  if (r) num.appendChild(el("span", null, ` · resets ${r}`));
  m.appendChild(num);
  const track = el("div", "track");
  const fill = el("div", "fill" + (left <= 0 ? " out" : ""));
  fill.style.width = left + "%";
  track.appendChild(fill);
  m.appendChild(track);
  return m;
}

// -- account card --------------------------------------------------------------
function card(provider, a, data) {
  const c = el("div", "card");
  const top = el("div", "top");
  top.appendChild(el("span", "slot", a.number));
  top.appendChild(el("span", "name", a.email || ("account " + a.number)));
  if (a.alias) top.appendChild(el("span", "pill soft", a.alias));
  if (a.plan) top.appendChild(el("span", "pill", a.plan));
  if (a.org && a.org !== "personal") top.appendChild(el("span", "pill", a.org));
  if (a.disabled) top.appendChild(el("span", "pill", "disabled"));
  top.appendChild(el("div", "grow"));
  const b = actionButton(a.active ? "active" : "switch", () => act("/api/switch", {provider, number: a.number}, b, "switching…"));
  b.dataset.focusKey = provider + ":switch:" + a.number;
  b.className = a.active ? "ghost" : "";
  b.setAttribute("aria-label", a.active ? `Account ${a.number} is active` : `Switch to account ${a.number}`);
  block(b, !!a.active || a.switchable === false || !supports(data, "switch"));
  top.appendChild(b);
  c.appendChild(top);

  const ws = a.windows || [];
  if (ws.length) {
    const ms = el("div", "meters");
    ws.forEach((w) => ms.appendChild(meter(w)));
    c.appendChild(ms);
  }

  const left = headroom(a);
  if (a.error || (a.sentinel && a.sentinel !== "api key")) {
    const s = el("div", "state out");
    s.appendChild(el("span", "glyph", "●"));
    s.appendChild(el("span", null, a.error || a.sentinel));
    c.appendChild(s);
  } else if (a.sentinel === "api key") {
    c.appendChild(el("div", "state", "API key · subscription quota is not available for this account."));
  } else if (a.onCredits) {
    const s = el("div", "state");
    s.appendChild(el("span", "pill solid", "paid credits"));
    s.appendChild(el("span", null, "Included quota spent; every request now bills credits. Auto-switch will never choose this account — switch by hand if you mean to pay."));
    c.appendChild(s);
  } else if (left !== null && left <= 0) {
    const s = el("div", "state out");
    s.appendChild(el("span", "glyph", "●"));
    s.appendChild(el("span", null, "limit reached"));
    c.appendChild(s);
  } else if (!ws.length) {
    c.appendChild(el("div", "state", "usage unknown"));
  }
  const actions = el("div", "actions");
  const disable = actionButton(a.disabled ? "Enable" : "Disable", () => act("/api/disabled", {provider, number: a.number, disabled: !a.disabled}, disable, "saving…"));
  disable.dataset.focusKey = provider + ":disable:" + a.number;
  disable.setAttribute("aria-label", `${a.disabled ? "Enable" : "Disable"} account ${a.number} for auto-switch`);
  disable.title = "Disabled accounts are excluded from automatic selection.";
  block(disable, !supports(data, "disable"));
  const remove = actionButton("Remove", () => confirmAction({
    title: `Remove ${providers[provider]} account?`,
    description: `Remove ${a.email || "account"} from slot ${a.number}? This removes the managed credentials, not the provider account.`,
    label: "Remove account",
    opener: remove,
    submit: (button) => act("/api/remove", {provider, number: a.number, confirm: true}, button, "removing…"),
  }));
  remove.dataset.focusKey = provider + ":remove:" + a.number;
  remove.setAttribute("aria-label", `Remove account ${a.number}`);
  block(remove, !supports(data, "remove"));
  actions.append(disable, remove);
  c.appendChild(actions);
  return c;
}

function adoptRow(provider, live) {
  const r = el("div", "adopt");
  const t = el("div", "t");
  t.appendChild(document.createTextNode("Signed in as "));
  t.appendChild(el("b", null, live.email));
  t.appendChild(document.createTextNode(" — not managed yet."));
  r.appendChild(t);
  r.appendChild(el("div", "grow"));
  r.appendChild(el("span", "hint", "Use Add current to manage it."));
  return r;
}

function supports(data, capability) {
  return !!data.available && (data.capabilities || ["switch", "add"]).includes(capability);
}

function actionButton(label, onClick) {
  const button = el("button", null, label);
  button.type = "button";
  button.dataset.action = "";
  button.onclick = onClick;
  return button;
}

function block(button, disabled) {
  button.dataset.blocked = String(disabled);
  button.disabled = busy || disabled;
}

function syncBusy() {
  document.querySelectorAll("[data-action]").forEach((button) => {
    button.disabled = busy || button.dataset.blocked === "true";
  });
  document.querySelectorAll("[data-lock]").forEach((input) => {
    input.disabled = busy || input.dataset.blocked === "true";
  });
  $("dialog-cancel").disabled = busy;
  $("dialog-form").setAttribute("aria-busy", String(busy));
}

function setupProvider(id) {
  const ui = providerUI[id] = {mode: "stopped", dirty: false, buttons: {}};
  const host = $(id + "-actions");
  for (const [capability, label, path, pending] of [
    ["add", "Add current", "/api/add", "adding…"],
    ["switch-best", "Switch best", "/api/switch-best", "choosing…"],
  ]) {
    const button = actionButton(label, () => act(path, {provider: id}, button, pending));
    ui.buttons[capability] = button;
    block(button, true);
    host.appendChild(button);
  }
  ui.buttons.add.title = "Save or refresh the currently signed-in account, including one already managed.";
  const refresh = actionButton("Refresh usage", () => refreshUsage(refresh));
  host.appendChild(refresh);
  if (id === "claude") {
    const token = actionButton("Add token / API key", () => tokenDialog(token));
    ui.buttons.token = token;
    block(token, true);
    host.appendChild(token);
  }
  const auto = $(id + "-auto");
  const heading = el("div", "auto-heading");
  heading.appendChild(el("h3", null, "Auto-switch"));
  ui.status = el("span", "pill", "unavailable");
  heading.appendChild(ui.status);
  auto.appendChild(heading);
  auto.appendChild(el("p", "hint", "Runs only while the local dashboard server is open. Closing this tab or pausing live updates does not stop it. These controls apply to this server session only."));
  const field = el("div", "threshold-field");
  const label = el("label", "hint", "Session threshold (% used)");
  label.htmlFor = id + "-threshold";
  ui.threshold = el("input");
  Object.assign(ui.threshold, {id: id + "-threshold", type: "number", min: "50", max: "99.9", step: "0.1", value: "90", required: true});
  ui.threshold.dataset.lock = "";
  ui.threshold.oninput = () => { ui.dirty = true; };
  ui.apply = actionButton("Apply threshold", () => setAuto(id, ui.mode, ui.apply));
  field.append(label, ui.threshold, ui.apply);
  auto.appendChild(field);
  const actions = el("div", "actions");
  ui.modes = {};
  for (const [mode, text] of [["dry-run", "Dry run"], ["live", "Start live"], ["stopped", "Stop"]]) {
    const button = actionButton(text, () => setAuto(id, mode, button, mode === "stopped"));
    ui.modes[mode] = button;
    actions.appendChild(button);
  }
  auto.appendChild(actions);
  ui.error = el("p", "auto-error");
  ui.error.setAttribute("role", "status");
  auto.appendChild(ui.error);
  ui.reason = el("p", "hint", "No auto-switch events yet.");
  auto.appendChild(ui.reason);
  const details = el("details");
  details.appendChild(el("summary", null, "Recent events (last 20)"));
  ui.events = el("ol", "events");
  ui.events.setAttribute("aria-label", `${providers[id]} auto-switch events`);
  details.appendChild(ui.events);
  auto.appendChild(details);
  $(id).addEventListener("focusout", () => queueMicrotask(() => renderAccounts(id, state[id] || {})));
  updateProvider(id, {});
}

function renderAccounts(id, data, force = false) {
  const host = $(id);
  const focused = host.contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  if (focused && !force) return;
  host.replaceChildren();
  const n = (data.accounts || []).length;
  if (!data.available) { host.appendChild(el("div", "empty", data.error || "unavailable")); return; }
  (data.accounts || []).forEach((a) => host.appendChild(card(id, a, data)));
  const live = data.liveLogin;
  if (live && !live.managed) host.appendChild(adoptRow(id, live));
  else if (!n) {
    const e = el("div", "empty", "Nothing managed and nothing signed in. Run ");
    e.appendChild(el("code", null, `agent-switch ${id === "codex" ? "codex " : ""}add`));
    host.appendChild(e);
  }
  if (focused) {
    const next = Array.from(host.querySelectorAll("[data-action]")).find((button) => button.dataset.focusKey === focused);
    if (next && !next.disabled) next.focus({preventScroll: true});
  }
}

function updateProvider(id, data, force = false) {
  const ui = providerUI[id];
  const n = (data.accounts || []).length;
  $(id + "-count").textContent = data.available && n ? `${n} managed` : "";
  const notice = $(id + "-switch-notice");
  notice.textContent = data.switchNotice || (id === "claude" ? "CLI only. Claude desktop, including the Code tab, has a separate sign-in and is not switched here." : "");
  notice.hidden = !notice.textContent;
  Object.entries(ui.buttons).forEach(([capability, button]) => block(button, !supports(data, capability)));
  const auto = data.auto || {};
  const available = supports(data, "auto");
  ui.mode = ["dry-run", "live", "stopped"].includes(auto.mode) ? auto.mode : "stopped";
  ui.status.textContent = available ? ui.mode : "unavailable";
  ui.status.className = "pill" + (available && ui.mode === "live" ? " solid" : "");
  if (!ui.dirty && document.activeElement !== ui.threshold && typeof auto.threshold === "number") ui.threshold.value = String(auto.threshold);
  ui.threshold.dataset.blocked = String(!available);
  ui.threshold.disabled = busy || !available;
  block(ui.apply, !available);
  Object.entries(ui.modes).forEach(([mode, button]) => {
    block(button, !available || mode === ui.mode);
    button.setAttribute("aria-pressed", String(available && mode === ui.mode));
  });
  ui.error.textContent = auto.error || "";
  ui.error.hidden = !auto.error;
  const events = (auto.events || []).slice(-20);
  ui.reason.textContent = available ? (events.length ? events[events.length - 1].message : "No auto-switch events yet.") : "Auto-switch is not available from this provider/server.";
  ui.events.replaceChildren();
  events.forEach((event) => {
    const item = el("li");
    const date = new Date(typeof event.at === "number" ? event.at * 1000 : event.at);
    const at = event.at != null && !Number.isNaN(date.getTime()) ? date.toLocaleTimeString() + " · " : "";
    item.textContent = `${at}${event.kind || "event"}: ${event.message || ""}`;
    ui.events.appendChild(item);
  });
  renderAccounts(id, data, force);
}

async function load(force = false, silent = false) {
  if (loadingVersion && !force) return false;
  const version = ++requestVersion;
  loadingVersion = version;
  try {
    const {ok, body} = await api("/api/state" + (force ? "?force=1" : ""));
    if (version !== requestVersion) return false;
    if (!ok || body.error) {
      if (!silent) toast(body.message || body.error || "Could not load state.", true);
      return false;
    }
    state = body;
    $("kpis").replaceChildren(tile("Claude Code", body.claude || {}), tile("Codex", body.codex || {}));
    Object.keys(providers).forEach((id) => updateProvider(id, body[id] || {}, force));
    $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
    return true;
  } catch {
    if (version === requestVersion && !silent) toast("Could not reach agent-switch. Check that the local dashboard server is still open.", true);
    return false;
  } finally {
    if (loadingVersion === version) loadingVersion = 0;
  }
}

async function refreshUsage(button) {
  if (busy) return;
  busy = true;
  const was = button.textContent;
  button.textContent = "refreshing…";
  syncBusy();
  try { await load(true); }
  finally { busy = false; button.textContent = was; syncBusy(); }
}

async function act(path, payload, button, pending) {
  if (busy) return false;
  busy = true;
  ++requestVersion;
  const was = button.textContent;
  button.textContent = pending;
  syncBusy();
  try {
    const {ok, body} = await api(path, {
      method: "POST",
      headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"},
      body: JSON.stringify(payload),
    });
    const success = ok && body.ok !== false && !body.error;
    const noSwitch = body.switched === false;
    let message = safeMessage(body.message || body.error || body.reason || (noSwitch ? "No account was switched." : success ? "Request completed." : "Request failed."), payload.token);
    if (path === "/api/auto" && success && payload.threshold !== undefined) providerUI[payload.provider].dirty = false;
    const refreshed = await load(true, true);
    if (!refreshed) message += " State could not be refreshed; check the local dashboard server.";
    toast(message, !success || !!body.restartRequired || !refreshed, noSwitch);
    if ($("action-dialog").open && !success) {
      $("dialog-feedback").textContent = message;
      $("dialog-feedback").hidden = false;
    }
    return success;
  } catch {
    const message = "Could not complete the request. Check the local dashboard server and refresh the state before retrying.";
    toast(message, true);
    if ($("action-dialog").open) {
      $("dialog-feedback").textContent = message;
      $("dialog-feedback").hidden = false;
    }
    return false;
  } finally {
    busy = false;
    button.textContent = was;
    syncBusy();
    if (!button.isConnected && button.dataset.focusKey) {
      const replacement = Array.from(document.querySelectorAll("[data-action]")).find((item) => item.dataset.focusKey === button.dataset.focusKey);
      if (replacement && !replacement.disabled) replacement.focus({preventScroll: true});
    }
  }
}

function confirmAction({title, description, label, opener, submit, fields}) {
  if (busy || $("action-dialog").open) return;
  dialogOpener = opener;
  dialogAction = submit;
  $("dialog-title").textContent = title;
  $("dialog-description").textContent = description;
  $("dialog-submit").textContent = label;
  $("dialog-feedback").hidden = true;
  $("dialog-feedback").textContent = "";
  $("dialog-fields").replaceChildren();
  if (fields) fields($("dialog-fields"));
  $("action-dialog").showModal();
  $("dialog-cancel").focus();
}

function setAuto(id, mode, opener, stop = false) {
  const ui = providerUI[id];
  if (busy || (!stop && !ui.threshold.reportValidity())) return;
  const payload = {provider: id, mode};
  if (!stop) payload.threshold = Number(ui.threshold.value);
  const submit = (button) => act("/api/auto", payload, button, "saving…");
  if (mode === "live") {
    payload.confirm = true;
    confirmAction({title: `Start live auto-switch for ${providers[id]}?`, description: `This can change the active CLI account automatically, using a ${payload.threshold}% used-quota threshold. It runs only while this local dashboard server is open. Closing the tab does not stop it; use Stop.`, label: "Confirm live mode", opener, submit});
  } else {
    submit(opener);
  }
}

function tokenDialog(opener) {
  let credential, email, slot, overwrite;
  confirmAction({
    title: "Add Claude token / API key",
    description: "Save a Claude credential for CLI use. Desktop sign-in, including the Code tab, stays separate. The credential is sent only to this local dashboard server and is never shown in activity messages.",
    label: "Save credential", opener,
    fields: (host) => {
      const field = (id, text, type) => {
        const label = el("label", "field", text);
        label.htmlFor = id;
        const input = el("input");
        Object.assign(input, {id, type, required: true});
        input.dataset.lock = "";
        label.appendChild(input);
        host.appendChild(label);
        return input;
      };
      credential = field("credential", "Token or API key", "password");
      credential.autocomplete = "new-password";
      credential.spellcheck = false;
      credential.setAttribute("autocapitalize", "off");
      email = field("credential-email", "Account email (optional)", "email");
      email.required = false;
      email.autocomplete = "off";
      slot = field("credential-slot", "Slot (optional; blank chooses an available slot)", "number");
      Object.assign(slot, {required: false, min: "1", step: "1"});
      const check = el("label", "check");
      overwrite = el("input");
      overwrite.type = "checkbox";
      overwrite.dataset.lock = "";
      const explanation = el("span");
      check.append(overwrite, explanation);
      check.hidden = true;
      host.appendChild(check);
      const updateOverwrite = () => {
        const existing = (state.claude?.accounts || []).find((account) => account.email && account.email.toLowerCase() === email.value.trim().toLowerCase());
        const replacement = slot.value || (existing && existing.number);
        overwrite.checked = false;
        overwrite.required = !!replacement;
        check.hidden = !replacement;
        explanation.textContent = slot.value ? `I confirm replacing the saved credentials in slot ${slot.value} if it already exists.` : `I confirm refreshing the saved credentials for ${existing?.email || "this account"} in slot ${replacement}.`;
      };
      slot.oninput = updateOverwrite;
      email.oninput = updateOverwrite;
    },
    submit: async (button) => {
      const token = credential.value.trim();
      if (!token) { credential.focus(); return false; }
      const payload = {provider: "claude", token, email: email.value.trim(), slot: slot.value ? Number(slot.value) : null, confirm: overwrite.checked};
      const success = await act("/api/token", payload, button, "saving…");
      credential.value = "";
      return success;
    },
  });
}

$("dialog-form").onsubmit = async (event) => {
  event.preventDefault();
  if (busy || !$("dialog-form").reportValidity() || !dialogAction) return;
  if (await dialogAction($("dialog-submit"))) $("action-dialog").close();
};
$("dialog-cancel").onclick = () => { if (!busy) $("action-dialog").close(); };
$("action-dialog").addEventListener("cancel", (event) => { if (busy) event.preventDefault(); });
$("action-dialog").addEventListener("close", () => {
  $("dialog-fields").replaceChildren();
  dialogAction = null;
  if (dialogOpener && dialogOpener.isConnected) dialogOpener.focus();
  else $("theme").focus();
});

try { $("theme").value = localStorage.getItem("agent-switch-theme") || "system"; } catch {}
function applyTheme() {
  const theme = ["light", "dark"].includes($("theme").value) ? $("theme").value : "system";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("agent-switch-theme", theme); } catch {}
}
$("theme").onchange = applyTheme;
applyTheme();
$("watch").onchange = () => {
  $("watch-status").textContent = $("watch").checked ? "Every 20 seconds · auto-switch runs independently" : "Updates paused · auto-switch is not stopped";
  if ($("watch").checked && !busy && !$("action-dialog").open) load();
};
Object.keys(providers).forEach(setupProvider);
load();
setInterval(() => { if ($("watch").checked && !busy && !$("action-dialog").open) load(); }, 20000);
</script>
</body>
</html>
"""

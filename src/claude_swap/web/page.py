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
  header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 20px; }
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

  <section class="kpis" id="kpis" aria-label="Active accounts"></section>

  <section class="provider"><h2>Claude Code<span id="claude-count"></span></h2><div id="claude"></div></section>
  <section class="provider"><h2>Codex<span id="codex-count"></span></h2><div id="codex"></div></section>

  <div id="toast" role="status" aria-live="polite"></div>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

function api(path, options) {
  const opts = Object.assign({headers: {"X-Auth-Token": TOKEN}}, options || {});
  return fetch(path, opts).then((r) => r.json().then((b) => ({ok: r.ok, body: b})));
}

function toast(message, ember) {
  const n = $("toast");
  n.textContent = message;
  n.className = "show" + (ember ? " ember" : "");
  if (!ember) setTimeout(() => { n.className = ""; }, 5000);
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
function card(provider, a) {
  const c = el("div", "card");
  const top = el("div", "top");
  top.appendChild(el("span", "slot", a.number));
  top.appendChild(el("span", "name", a.email || ("account " + a.number)));
  if (a.alias) top.appendChild(el("span", "pill soft", a.alias));
  if (a.plan) top.appendChild(el("span", "pill", a.plan));
  if (a.org && a.org !== "personal") top.appendChild(el("span", "pill", a.org));
  if (a.disabled) top.appendChild(el("span", "pill", "disabled"));
  top.appendChild(el("div", "grow"));
  const b = el("button", a.active ? "ghost" : "", a.active ? "active" : "switch");
  b.disabled = !!a.active;
  if (!a.active) b.onclick = () => act("/api/switch", {provider, number: a.number}, b, "switching…");
  top.appendChild(b);
  c.appendChild(top);

  const ws = a.windows || [];
  if (ws.length) {
    const ms = el("div", "meters");
    ws.forEach((w) => ms.appendChild(meter(w)));
    c.appendChild(ms);
  }

  const left = headroom(a);
  if (a.error || a.sentinel) {
    const s = el("div", "state out");
    s.appendChild(el("span", "glyph", "●"));
    s.appendChild(el("span", null, a.error || a.sentinel));
    c.appendChild(s);
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
  const b = el("button", "primary", "Manage this account");
  b.onclick = () => act("/api/add", {provider}, b, "adding…");
  r.appendChild(b);
  return r;
}

function render(id, data) {
  const host = $(id);
  host.innerHTML = "";
  const n = (data.accounts || []).length;
  $(id + "-count").textContent = data.available && n ? `${n} managed` : "";
  if (!data.available) { host.appendChild(el("div", "empty", data.error || "unavailable")); return; }
  (data.accounts || []).forEach((a) => host.appendChild(card(id, a)));
  const live = data.liveLogin;
  if (live && !live.managed) host.appendChild(adoptRow(id, live));
  else if (!n) {
    const e = el("div", "empty", "Nothing managed and nothing signed in. Run ");
    e.appendChild(el("code", null, `agent-switch ${id === "codex" ? "codex " : ""}add`));
    host.appendChild(e);
  }
}

function load(force) {
  return api("/api/state" + (force ? "?force=1" : "")).then(({ok, body}) => {
    if (!ok) return toast(body.error || "could not load state", true);
    const k = $("kpis"); k.innerHTML = "";
    k.appendChild(tile("Claude Code", body.claude));
    k.appendChild(tile("Codex", body.codex));
    render("claude", body.claude);
    render("codex", body.codex);
    $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
  }).catch((e) => toast("could not reach agent-switch: " + e, true));
}

function act(path, payload, button, pending) {
  const was = button.textContent;
  button.disabled = true;
  button.textContent = pending;
  api(path, {
    method: "POST",
    headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"},
    body: JSON.stringify(payload),
  }).then(({ok, body}) => {
    // A Codex switch that still needs a restart is not a plain success: it
    // stays up in the one red the page allows, instead of fading.
    toast(body.message || (ok ? "done" : "failed"), !ok || !!body.restartRequired);
    return load(true);
  }).catch((e) => { toast(String(e), true); button.disabled = false; button.textContent = was; });
}

load();
setInterval(() => load(false), 20000);
</script>
</body>
</html>
"""

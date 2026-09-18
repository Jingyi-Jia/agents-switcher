"""The dashboard's single page.

Kept as one string with no build step, no framework and no CDN: the page has to
render on a cluster login node reached through an SSH tunnel, where fetching
anything from the internet is exactly what will not work.

The one piece of visual logic that is policy rather than taste: an account
running on paid credits is drawn in the warning colour and labelled, never in
the healthy colour. It still works, so it is switchable -- but it is not spare
capacity, and making it LOOK like spare capacity would invite reaching for it.
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
    --bg: #fbfbfa; --fg: #1a1a18; --muted: #6b6b66; --line: #e3e3df;
    --card: #ffffff; --ok: #2f7d52; --warn: #b4690e; --bad: #b3261e;
    --bar: #ecece8; --accent: #3a5ccc;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #17171a; --fg: #e8e8e4; --muted: #9a9a94; --line: #2c2c31;
      --card: #1e1e22; --ok: #5fbe86; --warn: #e0a049; --bad: #f2867c;
      --bar: #2a2a30; --accent: #8aa4ff;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--fg);
    font: 14px/1.5 ui-sans-serif, -apple-system, "Segoe UI", system-ui, sans-serif;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 24px 16px 48px; }
  header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }
  h1 { font-size: 18px; margin: 0; font-weight: 600; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 12px; }
  h2 {
    font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
    color: var(--muted); margin: 28px 0 10px; font-weight: 600;
  }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 10px;
    padding: 12px 14px; margin-bottom: 8px;
    display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; align-items: center;
  }
  .card.inactive-credits { border-color: var(--warn); }
  .who { font-weight: 550; display: flex; align-items: center; gap: 8px; }
  .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--line); flex: none; }
  .dot.on { background: var(--ok); }
  .tag {
    font-size: 11px; color: var(--muted); border: 1px solid var(--line);
    border-radius: 999px; padding: 0 6px;
  }
  .detail { grid-column: 1; color: var(--muted); font-size: 12.5px; }
  .detail.warn { color: var(--warn); }
  .detail.bad { color: var(--bad); }
  .bar { grid-column: 1; height: 5px; background: var(--bar); border-radius: 3px; overflow: hidden; }
  .bar > i { display: block; height: 100%; background: var(--ok); }
  .bar > i.warn { background: var(--warn); }
  .bar > i.bad { background: var(--bad); }
  button {
    grid-row: 1 / span 3; font: inherit; font-size: 13px; padding: 6px 14px;
    border-radius: 7px; border: 1px solid var(--line); background: transparent;
    color: var(--fg); cursor: pointer;
  }
  button:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
  button:disabled { opacity: .4; cursor: default; }
  .empty, .err { color: var(--muted); font-size: 13px; padding: 10px 2px; }
  .err { color: var(--bad); }
  #status {
    position: sticky; bottom: 0; margin-top: 16px; padding: 10px 12px;
    background: var(--card); border: 1px solid var(--line); border-radius: 8px;
    font-size: 13px; display: none;
  }
  #status.show { display: block; }
  #status.warn { border-color: var(--warn); color: var(--warn); }
  #status.bad { border-color: var(--bad); color: var(--bad); }
  @media (max-width: 520px) {
    .card { grid-template-columns: 1fr; }
    button { grid-row: auto; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>agent-switch</h1>
    <span class="sub" id="stamp">loading…</span>
  </header>
  <div class="sub">Claude Code and Codex accounts on this machine.</div>

  <h2>Claude Code</h2>
  <div id="claude"></div>

  <h2>Codex</h2>
  <div id="codex"></div>

  <div id="status"></div>
</div>
<script>
const TOKEN = new URLSearchParams(location.search).get("token") || "";
const $ = (id) => document.getElementById(id);

function api(path, options) {
  const opts = Object.assign({headers: {"X-Auth-Token": TOKEN}}, options || {});
  return fetch(path, opts).then((r) => r.json().then((b) => ({ok: r.ok, body: b})));
}

function say(message, kind) {
  const el = $("status");
  el.textContent = message;
  el.className = "show" + (kind ? " " + kind : "");
  if (!kind) setTimeout(() => { el.className = ""; }, 6000);
}

function severity(pct) {
  if (pct === null || pct === undefined) return "";
  if (pct >= 100) return "bad";
  if (pct >= 80) return "warn";
  return "";
}

function card(provider, acct) {
  const el = document.createElement("div");
  el.className = "card";
  const name = acct.email || ("account " + acct.number);
  const alias = acct.alias ? `<span class="tag">${acct.alias}</span>` : "";
  const plan = acct.plan ? `<span class="tag">${acct.plan}</span>` : "";
  const org = acct.org && acct.org !== "personal" ? `<span class="tag">${acct.org}</span>` : "";

  let detail, barClass, width = 0, detailClass = "";
  if (acct.error) {
    detail = acct.error; detailClass = "bad"; barClass = "bad";
  } else if (acct.sentinel) {
    detail = acct.sentinel; detailClass = "warn"; barClass = "warn";
  } else if (acct.percent === null || acct.percent === undefined) {
    detail = "usage unknown"; barClass = "";
  } else {
    width = Math.min(100, acct.percent);
    barClass = severity(acct.percent);
    detail = acct.summary || (acct.percent + "% used");
  }
  // Policy, not decoration: an account billing credits still works, but it is
  // not spare capacity and must not be drawn as though it were.
  if (acct.onCredits) { detailClass = "warn"; barClass = "bad"; el.classList.add("inactive-credits"); }
  if (acct.disabled) { detail += " · disabled"; }

  el.innerHTML = `
    <div class="who"><span class="dot ${acct.active ? "on" : ""}"></span>
      ${acct.number}. ${name} ${alias} ${plan} ${org}</div>
    <div class="detail ${detailClass}">${detail}</div>
    <div class="bar"><i class="${barClass}" style="width:${width}%"></i></div>`;

  const button = document.createElement("button");
  button.textContent = acct.active ? "active" : "switch";
  button.disabled = !!acct.active;
  button.onclick = () => doSwitch(provider, acct.number, button);
  el.appendChild(button);
  return el;
}

function renderProvider(id, data) {
  const host = $(id);
  host.innerHTML = "";
  if (!data.available) {
    host.innerHTML = `<div class="err">${data.error || "unavailable"}</div>`;
    return;
  }
  if (!data.accounts.length) {
    host.innerHTML = `<div class="empty">No accounts managed yet — run
      <code>agent-switch ${id === "codex" ? "codex " : ""}add</code>.</div>`;
    return;
  }
  data.accounts.forEach((a) => host.appendChild(card(id, a)));
}

function load(force) {
  return api("/api/state" + (force ? "?force=1" : "")).then(({ok, body}) => {
    if (!ok) { say(body.error || "could not load state", "bad"); return; }
    renderProvider("claude", body.claude);
    renderProvider("codex", body.codex);
    $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
  }).catch((e) => say("could not reach agent-switch: " + e, "bad"));
}

function doSwitch(provider, number, button) {
  button.disabled = true;
  button.textContent = "switching…";
  api("/api/switch", {
    method: "POST",
    headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"},
    body: JSON.stringify({provider, number}),
  }).then(({ok, body}) => {
    // A Codex switch that needs a restart is NOT a plain success, and the
    // message stays up until the next action rather than fading.
    say(body.message || (ok ? "switched" : "failed"),
        ok ? (body.restartRequired ? "warn" : null) : "bad");
    return load(true);
  }).catch((e) => { say(String(e), "bad"); load(true); });
}

load();
setInterval(() => load(false), 20000);
</script>
</body>
</html>
"""

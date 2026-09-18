"""The dashboard's single page.

One string, no build step, no framework, no CDN: the page has to render on a
cluster login node reached through an SSH tunnel, where fetching anything from
the internet is exactly what will not work.

THE FRAMING IS HEADROOM, NOT UTILISATION. Every bar shows what is LEFT and
drains as it is spent, and every number reads "x% left". The question this page
answers is "where should I work next", and the answer is the account with the
most room -- so the quantity on screen should be the one being compared, not its
complement. A bar that fills up as an account is consumed reads as progress
toward something good, which is backwards.

One bar per WINDOW, because an account is limited by its worst one and an
average would hide that. Both providers are normalised to the same window shape
by the server so there is a single renderer rather than two that drift.

The one piece of visual logic that is policy rather than taste: an account
running on paid credits is never drawn as healthy. It works, so it stays
switchable, but it is not spare capacity, and colouring it like capacity would
invite reaching for it.
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
    --bg:#f7f7f5; --fg:#17171a; --dim:#71716c; --faint:#9b9b95;
    --line:#e4e4df; --card:#fff; --raise:0 1px 2px rgba(0,0,0,.04);
    --ok:#2e7d51; --warn:#b06d10; --bad:#b3261e; --track:#e0e0d9; --accent:#3b5bdb;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg:#161619; --fg:#ececE8; --dim:#9a9a94; --faint:#6e6e68;
      --line:#2b2b30; --card:#1d1d21; --raise:none;
      --ok:#57b881; --warn:#d99a3f; --bad:#ee7f75; --track:#292930; --accent:#8da2ff;
    }
  }
  *{box-sizing:border-box}
  body{
    margin:0;background:var(--bg);color:var(--fg);
    font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",system-ui,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .wrap{max-width:720px;margin:0 auto;padding:28px 16px 56px}
  header{display:flex;align-items:baseline;gap:10px;margin-bottom:22px}
  h1{font-size:15px;margin:0;font-weight:640;letter-spacing:-.01em}
  .stamp{color:var(--faint);font-size:11.5px;margin-left:auto;font-variant-numeric:tabular-nums}
  h2{
    font-size:10.5px;text-transform:uppercase;letter-spacing:.09em;color:var(--faint);
    margin:0 0 8px 2px;font-weight:650;
  }
  section{margin-bottom:26px}

  .row{
    background:var(--card);border:1px solid var(--line);border-left:3px solid var(--line);
    border-radius:9px;padding:11px 13px;margin-bottom:7px;box-shadow:var(--raise);
  }
  .row.active{border-left-color:var(--ok)}
  .row.credits{border-left-color:var(--warn)}
  .row.dead{border-left-color:var(--bad)}

  .top{display:flex;align-items:center;gap:8px}
  .slot{color:var(--faint);font-variant-numeric:tabular-nums;font-size:12.5px}
  .name{font-weight:560;letter-spacing:-.005em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .chip{
    font-size:10.5px;color:var(--dim);border:1px solid var(--line);
    border-radius:999px;padding:1px 7px;flex:none;line-height:1.5;
  }
  .spacer{flex:1}
  button{
    font:inherit;font-size:12.5px;padding:4px 12px;border-radius:6px;
    border:1px solid var(--line);background:transparent;color:var(--dim);
    cursor:pointer;flex:none;white-space:nowrap;
  }
  button:hover:not(:disabled){border-color:var(--accent);color:var(--accent)}
  button:disabled{opacity:.55;cursor:default}
  button.primary{color:var(--accent);border-color:var(--accent)}

  .limits{margin-top:10px;display:flex;flex-direction:column;gap:9px}
  .limit{display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:baseline}
  .limit .what{font-size:11.5px;color:var(--faint)}
  .limit .left{
    font-size:11.5px;color:var(--dim);text-align:right;font-variant-numeric:tabular-nums;
  }
  .limit .left b{font-weight:600;color:var(--fg)}
  .limit .left b.warn{color:var(--warn)} .limit .left b.bad{color:var(--bad)}
  .track{
    grid-column:1/-1;height:4px;background:var(--track);border-radius:3px;overflow:hidden;
  }
  .track > i{display:block;height:100%;background:var(--ok);border-radius:3px}
  .track > i.warn{background:var(--warn)} .track > i.bad{background:var(--bad)}

  .note{
    margin-top:9px;font-size:11.5px;line-height:1.45;color:var(--warn);
    border-top:1px solid var(--line);padding-top:8px;
  }
  .note.bad{color:var(--bad)}
  .note .why{color:var(--dim)}

  .banner{
    background:var(--card);border:1px dashed var(--line);border-radius:9px;
    padding:11px 13px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  }
  .banner .t{font-size:12.5px}
  .banner .t b{font-weight:560}
  .empty{color:var(--faint);font-size:12.5px;padding:2px}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:var(--dim)}

  #toast{
    position:sticky;bottom:12px;margin-top:18px;padding:10px 13px;border-radius:8px;
    background:var(--card);border:1px solid var(--line);box-shadow:var(--raise);
    font-size:12.5px;display:none;
  }
  #toast.show{display:block}
  #toast.warn{border-color:var(--warn);color:var(--warn)}
  #toast.bad{border-color:var(--bad);color:var(--bad)}
  @media(max-width:460px){ .name{max-width:44vw} }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>agent-switch</h1>
    <span class="stamp" id="stamp">loading…</span>
  </header>
  <section><h2>Claude Code</h2><div id="claude"></div></section>
  <section><h2>Codex</h2><div id="codex"></div></section>
  <div id="toast"></div>
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

function toast(message, kind) {
  const n = $("toast");
  n.textContent = message;
  n.className = "show" + (kind ? " " + kind : "");
  if (!kind) setTimeout(() => { n.className = ""; }, 5000);
}

// Severity is on HEADROOM, so it reads the same direction as the bar.
function grade(left) {
  if (left <= 0) return "bad";
  if (left <= 20) return "warn";
  return "";
}

function duration(seconds) {
  if (!seconds || seconds <= 0) return null;
  const d = Math.floor(seconds / 86400), h = Math.floor((seconds % 86400) / 3600);
  if (d) return `${d}d ${h}h`;
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

function limitRow(w) {
  const left = Math.max(0, 100 - (w.usedPercent ?? 0));
  const g = grade(left);
  const node = el("div", "limit");
  node.appendChild(el("span", "what", `${w.label} limit`));
  const right = el("span", "left");
  const strong = el("b", g, `${left}% left`);
  right.appendChild(strong);
  const resets = duration(w.resetAfterSeconds);
  if (resets) right.appendChild(document.createTextNode(` · resets ${resets}`));
  node.appendChild(right);
  const track = el("div", "track");
  const fill = el("i", g);
  fill.style.width = left + "%";
  track.appendChild(fill);
  node.appendChild(track);
  return node;
}

function accountRow(provider, a) {
  const row = el("div", "row");
  if (a.active) row.classList.add("active");
  if (a.onCredits) row.classList.add("credits");
  if (a.error || a.sentinel) row.classList.add("dead");

  const top = el("div", "top");
  top.appendChild(el("span", "slot", a.number + "."));
  top.appendChild(el("span", "name", a.email || ("account " + a.number)));
  if (a.alias) top.appendChild(el("span", "chip", a.alias));
  if (a.plan) top.appendChild(el("span", "chip", a.plan));
  if (a.org && a.org !== "personal") top.appendChild(el("span", "chip", a.org));
  if (a.disabled) top.appendChild(el("span", "chip", "disabled"));
  top.appendChild(el("div", "spacer"));

  const button = el("button", null, a.active ? "active" : "switch");
  button.disabled = !!a.active;
  if (!a.active) button.onclick = () => act("/api/switch", {provider, number: a.number}, button, "switching…");
  top.appendChild(button);
  row.appendChild(top);

  const windows = a.windows || [];
  if (windows.length) {
    const limits = el("div", "limits");
    windows.forEach((w) => limits.appendChild(limitRow(w)));
    row.appendChild(limits);
  }

  if (a.error) {
    row.appendChild(el("div", "note bad", a.error));
  } else if (a.sentinel) {
    row.appendChild(el("div", "note", a.sentinel));
  } else if (a.onCredits) {
    // Policy made visible: it works, but it is not spare capacity.
    const note = el("div", "note", "Included quota spent — now billing paid credits. ");
    note.appendChild(el("span", "why", "Auto-switch will never choose this; switch by hand if you mean to pay."));
    row.appendChild(note);
  } else if (!windows.length) {
    row.appendChild(el("div", "note", "usage unknown"));
  }
  return row;
}

function banner(provider, live) {
  const box = el("div", "banner");
  const text = el("div", "t");
  text.appendChild(document.createTextNode("Signed in as "));
  text.appendChild(el("b", null, live.email));
  text.appendChild(document.createTextNode(" — not managed yet."));
  box.appendChild(text);
  box.appendChild(el("div", "spacer"));
  const button = el("button", "primary", "manage this account");
  button.onclick = () => act("/api/add", {provider}, button, "adding…");
  box.appendChild(button);
  return box;
}

function render(id, data) {
  const host = $(id);
  host.innerHTML = "";
  if (!data.available) {
    host.appendChild(el("div", "empty", data.error || "unavailable"));
    return;
  }
  (data.accounts || []).forEach((a) => host.appendChild(accountRow(id, a)));
  const live = data.liveLogin;
  // A machine can be LOGGED IN to an account this tool does not manage.
  // Saying only "no accounts" there is true and useless.
  if (live && !live.managed) host.appendChild(banner(id, live));
  else if (!(data.accounts || []).length) {
    const e = el("div", "empty", "No accounts managed, and nothing signed in. Run ");
    e.appendChild(el("code", null, `agent-switch ${id === "codex" ? "codex " : ""}add`));
    host.appendChild(e);
  }
}

function load(force) {
  return api("/api/state" + (force ? "?force=1" : "")).then(({ok, body}) => {
    if (!ok) return toast(body.error || "could not load state", "bad");
    render("claude", body.claude);
    render("codex", body.codex);
    $("stamp").textContent = new Date().toLocaleTimeString();
  }).catch((e) => toast("could not reach agent-switch: " + e, "bad"));
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
    // A Codex switch needing a restart is NOT a plain success, so its message
    // stays up instead of fading.
    toast(body.message || (ok ? "done" : "failed"),
          ok ? (body.restartRequired ? "warn" : null) : "bad");
    return load(true);
  }).catch((e) => {
    toast(String(e), "bad");
    button.disabled = false;
    button.textContent = was;
  });
}

load();
setInterval(() => load(false), 20000);
</script>
</body>
</html>
"""

"""The offline, single-document Agent Switch dashboard."""

from __future__ import annotations

from .page_style import PAGE_STYLE
from .page_switch import SWITCH_SCRIPT
from .page_usage import USAGE_SCRIPT

PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>agent-switch</title>
<style>__PAGE_STYLE__</style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand">
      <svg class="brand-mark" viewBox="0 0 16 16" fill="none" shape-rendering="crispEdges" aria-hidden="true"><path fill="#78dce8" d="M10 3h1v1H10zM3 4h9v1H3zM2 5h11v1H2zM2 6h2v1H2zM10 6h2v1H10zM2 7h2v1H2zM10 7h1v1H10z"/><path fill="#b49bf4" d="M5 12h1v1H5zM4 11h9v1H4zM3 10h11v1H3zM12 9h2v1H12zM4 9h2v1H4zM12 8h2v1H12zM5 8h1v1H5z"/></svg>
      <div><h1 id="app-title">Agent Switch</h1><small>A little more headroom</small></div>
    </div>
    <nav class="nav" aria-label="Main navigation">
      <button type="button" id="nav-accounts" aria-controls="view-accounts" aria-current="page"><span class="nav-symbol" aria-hidden="true">▦</span>Accounts</button>
      <button type="button" id="nav-usage" aria-controls="view-usage"><span class="nav-symbol" aria-hidden="true">▥</span>Usage</button>
      <button type="button" id="nav-settings" aria-controls="view-settings"><span class="nav-symbol" aria-hidden="true">⚙</span>Settings</button>
    </nav>
    <div class="sidebar-foot"><span class="local-dot" aria-hidden="true"></span>Local by design<br>Your accounts. Your device.</div>
  </aside>
<main class="wrap">
  <section id="view-accounts" aria-labelledby="accounts-title">
  <header class="page-heading">
    <div><div class="eyebrow">Your workspace, ready</div><h2 id="accounts-title" tabindex="-1">Accounts</h2><p>Find your next bit of breathing room.</p></div>
    <span class="stamp" id="stamp">Connecting to your local service…</span>
  </header>
  <div id="state-error" class="load-error" role="status" hidden></div>
  <div id="guide-slot">
  <section class="guide" id="desktop-guide" aria-labelledby="guide-title" hidden>
    <div class="guide-heading">
      <div><div class="label" id="guide-label">Getting started</div><h2 id="guide-title" tabindex="-1">Start with an existing login</h2></div>
      <button type="button" id="help-close">Hide help</button>
    </div>
    <p class="guide-intro">Keep your Claude Code and Codex accounts in one place. This app includes its own runtime; you don't need to install Python or Node to run it. Claude Code needs its CLI and sign-in. Codex can use an existing file-backed sign-in from its Desktop app or CLI; the Codex CLI is not required for a Desktop login.</p>
    <div class="guide-providers" id="guide-providers"></div>
    <ol class="guide-steps">
      <li><strong>Set up a provider.</strong> Sign in through Claude Code's CLI or Codex's Desktop app or CLI, using its official setup guide. Then return here and choose Check again.</li>
      <li><strong>Save the current login.</strong> Add existing login saves that provider's current local credentials for switching later. It doesn't start a new sign-in. Codex keyring-only and API-key logins aren't supported.</li>
      <li><strong>Add another account when you're ready.</strong> Sign in to a different account in that provider, then add that login here. Switch by hand, or try Dry run before enabling live auto-switch.</li>
    </ol>
    <p class="guide-boundary" id="guide-boundary">The Claude Code and Codex account controls manage local provider credentials. Claude Desktop, including its Code tab, has a separate sign-in and is not switched here. This app doesn't install provider CLIs, start sign-in flows, or change Desktop cookies.</p>
    <p class="hint">Paid-credit accounts remain manual-only; auto-switch never chooses them. After switching, follow any provider restart notice shown below.</p>
    <p class="hint">Closing the app stops its automation. Pausing live updates only pauses this view.</p>
    <div class="guide-footer"><button type="button" id="guide-check" data-action>Check again</button><span class="hint" id="desktop-meta"></span></div>
  </section>
  </div>
  <section class="kpis" id="kpis" aria-label="Active accounts"></section>

  <div class="providers">
  <section class="provider" aria-labelledby="codex-heading"><h2 id="codex-heading">Codex<span id="codex-count"></span></h2><p class="notice" id="codex-switch-notice" hidden></p><div id="codex-actions" class="actions"></div><div id="codex"></div><details id="codex-auto" class="auto-panel"></details></section>
  <div id="claude-group" role="group" aria-label="Claude">
  <section class="provider" aria-labelledby="claude-heading"><h2 id="claude-heading">Claude Code<span id="claude-count"></span></h2><p class="notice" id="claude-switch-notice">CLI only. Claude desktop, including the Code tab, has a separate sign-in and is not switched here.</p><div id="claude-actions" class="actions"></div><div id="claude"></div><details id="claude-auto" class="auto-panel"></details></section>
  <section class="provider" id="claude-desktop-panel" aria-labelledby="claude-desktop-heading" hidden>
    <h2 id="claude-desktop-heading">Profiles <span>Beta · Claude Desktop</span></h2>
    <p class="notice">Separate local spaces for Claude Desktop. Open one, then sign in there.</p>
    <details class="profile-about"><summary>About profiles</summary>
      <p id="claude-desktop-notice"></p>
      <p>Profiles are local app data, not verified accounts. Names are your labels; last opened does not verify an active identity. Confirm the selected account in Claude. Signed-in persistence on Mac and Code/Cowork are not fully verified. Relocated profiles disable local Claude-in-Chrome pairing.</p>
      <p>Fully quit Claude before opening another profile. The Dock normally opens the usual default profile. No CLI token import, cookie copying, forced quit, deletion, or auto-switching is provided.</p>
    </details>
    <p class="notice" id="claude-desktop-status" role="status"></p>
    <div class="actions" id="claude-desktop-actions"></div>
    <div id="claude-desktop-profiles"></div>
  </section>
  </div>
  </div>
  </section>
  <section id="view-usage" aria-labelledby="usage-title" hidden>
    <header class="page-heading"><div><div class="eyebrow">A rhythm of your own</div><h2 id="usage-title" tabindex="-1">Usage</h2><p id="usage-subtitle">Token activity, with the details that matter.</p></div></header>
    <div class="usage-toolbar">
      <label for="usage-provider">Provider<select id="usage-provider"><option value="codex">Codex</option><option value="claude">Claude Code</option></select></label>
      <label id="usage-account-field" for="usage-account">Account<select id="usage-account"><option value="all">All accounts</option></select></label>
      <label for="usage-range">Date range<select id="usage-range"><option value="7">Last 7 days</option><option value="30" selected>Last 30 days</option><option value="90">Last 90 days</option><option value="all">All reported</option></select></label>
      <button type="button" id="usage-refresh" class="push">Refresh activity</button>
    </div>
    <div class="segmented" aria-label="Usage view"><button type="button" id="usage-overview" aria-pressed="true">Overview</button><button type="button" id="usage-models" aria-pressed="false">Models</button></div>
    <p id="usage-context" class="usage-context" role="status" aria-live="polite"></p>
    <div id="usage-content" aria-busy="false"></div>
  </section>
  <section id="view-settings" aria-labelledby="settings-title" hidden>
    <header class="page-heading"><div><div class="eyebrow">Make yourself at home</div><h2 id="settings-title" tabindex="-1">Settings</h2><p>A quieter workspace, your way.</p></div></header>
    <div class="settings-panel">
      <h3>Appearance &amp; updates</h3>
      <div class="setting-row"><div><label for="theme">Appearance</label><p class="hint">Follow your device, or set the mood.</p></div><select id="theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></div>
      <div class="setting-row"><div><label for="watch">Live updates</label><p class="hint" id="watch-status">Every 20 seconds · auto-switch runs independently</p></div><input id="watch" type="checkbox" checked></div>
      <p id="settings-lifecycle">Closing the app stops its automation. Pausing live updates only pauses this view. Use Stop in an account's auto-switch controls to stop automation.</p>
      <p class="hint">Usage history refreshes when you open Usage or choose Refresh activity, not on every live update. Profile readiness is checked while Profiles is visible.</p>
    </div>
    <div class="settings-panel"><h3>Here to help</h3><p>Save existing provider logins, switch deliberately, and keep automatic selection under your control.</p><button type="button" id="help-toggle" aria-controls="desktop-guide" aria-expanded="false" hidden>Account setup &amp; help</button><p>Claude Code accounts and Claude Desktop profiles are separate. Paid-credit accounts stay manual-only; auto-switch never chooses them.</p></div>
    <div id="settings-guide-slot"></div>
  </section>
  <div id="toast" role="status" aria-live="polite"></div>
</main>
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
<dialog id="codex-dialog" aria-labelledby="codex-dialog-title" aria-describedby="codex-dialog-description">
  <div class="eyebrow">A clean handoff</div><h2 id="codex-dialog-title">Switch Codex account</h2>
  <p id="codex-dialog-description"></p>
  <div class="switch-steps" aria-hidden="true"><span id="codex-step-quit">01 · Quit</span><span id="codex-step-switch">02 · Switch</span><span id="codex-step-open">03 · Reopen</span></div>
  <div id="codex-dialog-status" class="switch-status" role="status" aria-live="polite"></div>
  <p>We never stop terminal sessions. A running process's signed-in identity isn't verified here.</p>
  <div class="actions dialog-actions"><button type="button" id="codex-cancel">Cancel</button><button type="button" id="codex-check">Check again</button><button type="button" id="codex-continue" class="primary" disabled>Continue &amp; switch</button><button type="button" id="codex-assist" class="primary" hidden>Quit, switch &amp; reopen</button></div>
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
const helpUI = {};
const setupLinks = {claude: "https://code.claude.com/docs/en/setup", codex: "https://developers.openai.com/codex/app"};
let state = {}, busy = false, requestVersion = 0, loadingVersion = 0, toastTimer;
let dialogAction = null, dialogOpener = null, dialogKind = null;
let helpRequested = false, helpDismissed = false;
let activeView = "accounts", preferenceVersion = 0, preferences = {theme: "system", profileNoticeVersion: 0};
let profileConsentSaved = false;

function isDesktop() {
  return !!state.desktop;
}

function automationLifecycle() {
  return isDesktop()
    ? "Runs only while this app is open. Closing the app stops its automation. Pausing live updates does not stop it; use Stop. These controls apply to this app session only."
    : "Runs only while the local dashboard server is open. Closing this tab or pausing live updates does not stop it. These controls apply to this server session only.";
}

function setupHelp(id) {
  const ui = helpUI[id] = {};
  const host = el("article", "guide-provider");
  host.id = id + "-setup";
  const title = el("h3", null, providers[id] + (id === "claude" ? " CLI" : " Desktop or CLI"));
  title.id = id + "-setup-title";
  host.setAttribute("aria-labelledby", title.id);
  ui.installed = el("span", "pill");
  ui.login = el("p");
  ui.next = el("p", "hint");
  ui.add = actionButton("Add existing login", () => act("/api/add", {provider: id}, ui.add, "adding…"));
  ui.add.title = "Save or refresh this provider's current local login. This does not sign in.";
  block(ui.add, true);
  const link = el("a", null, "Official setup guide ↗");
  link.href = setupLinks[id];
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.setAttribute("aria-label", providers[id] + (id === "claude" ? " CLI" : " Desktop") + " official setup guide (opens in browser)");
  const actions = el("div", "actions");
  actions.append(ui.add, link);
  host.append(title, ui.installed, ui.login, ui.next, actions);
  $("guide-providers").appendChild(host);
}

function renderHelp() {
  const desktop = isDesktop();
  const managed = Object.keys(providers).some((id) => (state[id]?.accounts || []).length);
  const guide = $("desktop-guide");
  $("help-toggle").hidden = !desktop;
  guide.hidden = !desktop || !(helpRequested || (activeView === "accounts" && !managed && !helpDismissed));
  $("help-toggle").setAttribute("aria-expanded", String(!guide.hidden));
  if (guide.hidden && guide.contains(document.activeElement)) $("help-toggle").focus();
  $("app-title").textContent = "Agent Switch";
  document.title = "Agent Switch";
  if (!desktop) return;
  $("guide-boundary").textContent = state.claudeDesktop
    ? "The Claude Code and Codex account controls manage local provider credentials. Claude Desktop, including its Code tab, has a separate sign-in; the experimental profile launcher in Accounts opens separate app profiles without transferring CLI credentials. This app doesn't install provider CLIs, start sign-in flows, or change Desktop cookies."
    : "The Claude Code and Codex account controls manage local provider credentials. Claude Desktop, including its Code tab, has a separate sign-in and is not switched here. This app doesn't install provider CLIs, start sign-in flows, or change Desktop cookies.";
  $("guide-label").textContent = managed ? "Help" : "Getting started";
  $("guide-title").textContent = managed ? "Account switching, step by step" : "Start with an existing login";
  const platform = {darwin: "macOS", win32: "Windows", linux: "Linux"}[state.desktop.platform] || "Desktop";
  $("desktop-meta").textContent = `Agent Switch ${state.desktop.version || ""} · ${platform}`;
  for (const id of Object.keys(providers)) {
    const ui = helpUI[id], data = state[id] || {};
    const installed = state.desktop.providers?.[id]?.installed;
    const live = data.liveLogin;
    const count = (data.accounts || []).length;
    ui.installed.textContent = installed === true ? "CLI detected" : installed === false ? "CLI not detected" : "CLI detection unavailable";
    const source = id === "codex" ? "file-backed" : "CLI";
    ui.login.textContent = live
      ? `Signed in as ${live.email || "an existing account"}${live.managed ? " · already managed" : " · not managed yet"}.`
      : count ? `${count} managed ${count === 1 ? "account" : "accounts"}. No current ${source} login detected.` : data.available ? `No ${source} login detected yet.` : `Current ${source} login could not be checked.`;
    ui.next.textContent = !data.available
      ? "Account access is unavailable. Check the provider's status below, then try Check again."
      : live
        ? live.managed ? "This login is saved. Add existing login can refresh its saved credentials." : "Add existing login saves this account without signing you in again."
        : id === "codex" ? "Sign in through Codex Desktop or CLI using a file-backed login, then choose Check again." : "Sign in through this provider's CLI, then choose Check again.";
    if (id === "codex") ui.next.textContent += " A file-backed Desktop login works without the Codex CLI. Use the official setup guide if needed.";
    else if (installed === false) ui.next.textContent += " If the CLI isn't installed, use the official setup guide. If you just installed it, reopen this app if detection hasn't updated.";
    block(ui.add, !live || !supports(data, "add"));
  }
}

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

function headroom(a) {
  const ws = (a.windows || []).filter((w) => typeof w.usedPercent === "number" && Number.isFinite(w.usedPercent));
  if (ws.length) return Math.max(0, Math.min(...ws.map((w) => 100 - w.usedPercent)));
  return (typeof a.percent === "number" && Number.isFinite(a.percent)) ? Math.max(0, Math.min(100, 100 - a.percent)) : null;
}
function soonestReset(a) {
  const ws = (a.windows || []).filter((w) => w.resetAfterSeconds > 0);
  return ws.length ? Math.min(...ws.map((w) => w.resetAfterSeconds)) : null;
}

function tile(name, data) {
  const t = el("div", "tile");
  t.appendChild(el("div", "label", name + " · active"));
  const active = (data.accounts || []).find((a) => a.active);
  const identity = el("div", "identity", active?.alias || active?.email || "No managed login");
  identity.title = active?.email || identity.textContent;
  t.appendChild(identity);
  const v = el("div", "value");
  if (!data.available) { v.textContent = "—"; t.appendChild(v); t.appendChild(el("div", "sub", data.error || "unavailable")); return t; }
  if (!active) { v.textContent = "—"; t.appendChild(v); t.appendChild(el("div", "sub", "no active account")); return t; }
  const left = headroom(active);
  if (active.onCredits) {
    v.textContent = "0%"; v.appendChild(el("small", null, "included quota"));
    t.appendChild(v);
    const s = el("div", "sub"); s.appendChild(el("span", "pill solid", "paid credits")); t.appendChild(s);
  } else if (left === null) {
    v.textContent = active.error ? "Unavailable" : "Not reported";
    t.appendChild(v);
    const detail = el("div", "sub", active.sentinel || active.error || "The provider has not reported quota for this account.");
    detail.title = detail.textContent;
    t.appendChild(detail);
  } else {
    v.textContent = left + "%"; v.appendChild(el("small", null, "left"));
    if (left <= 0) v.classList.add("ember");
    t.appendChild(v);
    const r = duration(soonestReset(active));
    t.appendChild(el("div", "sub", (active.email || "") + (r ? " · resets " + r : "")));
  }
  return t;
}

function meter(w) {
  const left = typeof w.usedPercent === "number" && Number.isFinite(w.usedPercent) ? Math.max(0, Math.min(100, 100 - w.usedPercent)) : null;
  const m = el("div", "meter" + (left === null ? " unknown" : ""));
  m.appendChild(el("span", "label", `${w.label} window`));
  const num = el("span", "num" + (left === null ? "" : left <= 0 ? " out" : left <= 20 ? " low" : ""));
  num.textContent = left === null ? "Not reported" : `${left}% left`;
  const r = duration(w.resetAfterSeconds);
  if (r) num.appendChild(el("span", null, ` · resets ${r}`));
  m.title = `${w.label} window: ${left === null ? "not reported" : left + "% left"}${r ? " · resets in " + r : ""}`;
  m.setAttribute("aria-label", m.title);
  m.appendChild(num);
  const track = el("div", "track");
  const fill = el("div", "fill" + (left <= 0 ? " out" : ""));
  fill.style.width = (left ?? 0) + "%";
  track.appendChild(fill);
  m.appendChild(track);
  return m;
}

function card(provider, a, data) {
  const c = el("article", "card" + (a.active ? " is-active" : ""));
  const top = el("div", "top");
  const avatar = el("span", "avatar " + provider, String(a.number).padStart(2, "0"));
  avatar.setAttribute("aria-hidden", "true");
  const identity = el("div", "identity-stack"), name = el("span", "name", a.alias || a.email || ("Account " + a.number));
  name.title = a.email || name.textContent;
  identity.append(name, el("div", "account-meta", [a.alias ? a.email : null, a.plan, a.org !== "personal" ? a.org : null, "Slot " + a.number].filter(Boolean).join(" · ")));
  top.append(avatar, identity);
  const b = actionButton(a.active ? "Current" : "Switch", () => provider === "codex" ? requestCodexSwitch(a, b) : act("/api/switch", {provider, number: a.number}, b, "Switching…"));
  b.dataset.focusKey = provider + ":switch:" + a.number;
  b.className = a.active ? "ghost" : "primary";
  b.setAttribute("aria-label", a.active ? `Account ${a.number} is active` : `Switch to account ${a.number}`);
  block(b, !!a.active || a.switchable === false || !supports(data, "switch"));
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
  const footer = el("div", "account-footer");
  footer.append(b, el("span", "hint", a.disabled ? "Excluded from auto-switch" : a.onCredits ? "Manual only" : a.active ? "Current saved login" : ""));
  const menu = el("details", "account-overflow"), summary = el("summary", null, "···");
  menu.dataset.menu = provider + ":menu:" + a.number;
  summary.dataset.control = "";
  summary.dataset.focusKey = menu.dataset.menu;
  summary.setAttribute("aria-label", `Manage account ${a.number}`);
  menu.appendChild(summary);
  const actions = el("div", "actions");
  const disable = actionButton(a.disabled ? "Include in auto-switch" : "Exclude from auto-switch", () => act("/api/disabled", {provider, number: a.number, disabled: !a.disabled}, disable, "saving…"));
  disable.dataset.focusKey = provider + ":disable:" + a.number;
  disable.setAttribute("aria-label", `${a.disabled ? "Include" : "Exclude"} account ${a.number} ${a.disabled ? "in" : "from"} auto-switch`);
  disable.title = "Excluded accounts stay available for manual switching.";
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
  menu.appendChild(actions); footer.appendChild(menu); c.appendChild(footer);
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
  r.appendChild(el("span", "hint", isDesktop() ? "Use Add existing login to manage it." : "Use Add current to manage it."));
  return r;
}

function supports(data, capability) {
  return !!data.available && (data.capabilities || ["switch", "add"]).includes(capability);
}

function actionButton(label, onClick) {
  const button = el("button", null, label);
  button.type = "button";
  button.dataset.action = "";
  button.dataset.control = "";
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
  for (const view of ["accounts", "usage", "settings"]) $("nav-" + view).disabled = busy;
  $("dialog-form").setAttribute("aria-busy", String(busy));
}

const desktopProfileWarning = "Experimental, macOS/Linux only. Signed-in persistence on Mac and Code/Cowork are unverified. Relocated profiles disable local Claude-in-Chrome pairing. Sign in to each profile in Claude once. Fully QUIT Claude before opening another profile. Profile names are user labels, not verified identities; confirm the selected account in Claude.";
const desktopProfileUI = {};

function setupDesktopProfiles() {
  const actions = $("claude-desktop-actions");
  desktopProfileUI.create = actionButton("New profile", () => createDesktopProfile(desktopProfileUI.create));
  desktopProfileUI.default = actionButton("Open usual Claude (default)", () => openDesktopProfile("default", "usual Claude (default)", desktopProfileUI.default));
  desktopProfileUI.refresh = actionButton("Check again", () => refreshUsage(desktopProfileUI.refresh));
  actions.append(desktopProfileUI.create, desktopProfileUI.default, desktopProfileUI.refresh);
}

function renderDesktopProfiles(data) {
  const panel = $("claude-desktop-panel");
  panel.hidden = !data;
  if (!data) return;
  $("claude-desktop-notice").textContent = data.notice || "Experimental Claude Desktop profiles are separate from Claude Code accounts.";
  const unavailable = !data.supported ? "Claude Desktop profiles are supported on macOS and Linux only."
    : !data.installed ? "Claude Desktop is not installed in a supported location."
    : !data.available ? "Opening Claude Desktop profiles is unavailable."
    : "";
  const processStatus = !data.supported || !data.installed ? ""
    : data.running === true ? "Claude is running. Fully quit Claude Desktop (⌘Q on Mac), then choose Check again. Closing its window is not enough."
    : data.running !== false ? "Claude process status is unknown. Opening is blocked until a check confirms it is quit. Choose Check again to retry."
    : data.available ? "Claude is quit; you can open a profile." : "";
  const openBlocked = !data.available || data.running !== false;
  const openReason = [unavailable, data.error, processStatus].filter(Boolean).join(" ");
  const status = $("claude-desktop-status");
  status.className = openBlocked ? "notice launch-blocked" : "notice";
  status.textContent = [openReason,
    data.canCreate && !data.available ? "You can still create empty profiles; creating does not launch Claude." : ""].filter(Boolean).join(" ");
  block(desktopProfileUI.create, !(data.canCreate ?? data.available));
  block(desktopProfileUI.default, openBlocked);
  desktopProfileUI.default.title = openBlocked ? openReason : "";
  desktopProfileUI.default.setAttribute("aria-describedby", "claude-desktop-status");
  const host = $("claude-desktop-profiles");
  const signature = JSON.stringify([data.profiles, openBlocked, openReason]);
  if (desktopProfileUI.signature === signature) return;
  desktopProfileUI.signature = signature;
  const focused = host.contains(document.activeElement) ? document.activeElement.dataset.profileId : null;
  host.replaceChildren();
  const profiles = Array.isArray(data.profiles) ? data.profiles : [];
  if (!profiles.length) host.appendChild(el("div", "empty", "No named profiles yet. Creating one makes an empty profile; sign in through Claude after opening it."));
  profiles.forEach((profile) => {
    const card = el("div", "card");
    const row = el("div", "top");
    row.appendChild(el("span", "name", profile.name));
    row.appendChild(el("span", "grow"));
    const open = actionButton("Open", () => openDesktopProfile(profile.id, profile.name, open));
    open.setAttribute("aria-label", `Open Claude Desktop profile ${profile.name}`);
    open.dataset.profileId = profile.id;
    block(open, openBlocked);
    open.title = openBlocked ? openReason : "";
    open.setAttribute("aria-describedby", "claude-desktop-status");
    row.appendChild(open);
    card.appendChild(row);
    host.appendChild(card);
    if (focused === profile.id && !open.disabled) open.focus({preventScroll: true});
  });
}

function desktopConsent(host) {
  if (preferences.profileNoticeVersion >= 1 || profileConsentSaved) return;
  const check = el("label", "check");
  const input = el("input");
  input.type = "checkbox";
  input.required = true;
  input.dataset.lock = "";
  check.append(input, el("span", null, "I understand these experimental limitations and will verify the account in Claude."));
  host.appendChild(check);
}

function createDesktopProfile(opener) {
  if (!(state.claudeDesktop?.canCreate ?? state.claudeDesktop?.available)) return;
  let name;
  confirmAction({
    title: "Create an empty Claude Desktop profile?", description: desktopProfileWarning + " Creation does not launch Claude or sign you in.",
    label: "Create profile", opener, kind: "profile",
    fields: (host) => {
      const field = el("label", "field", "Profile label (up to 64 characters)");
      name = el("input");
      Object.assign(name, {type: "text", required: true, maxLength: 64, autocomplete: "off"});
      name.dataset.lock = "";
      field.appendChild(name);
      host.append(field);
      desktopConsent(host);
    },
    submit: async (button) => {
      const label = name.value.trim();
      if (!label || label.length > 64 || !(state.claudeDesktop?.canCreate ?? state.claudeDesktop?.available)) return false;
      if (!await saveProfileConsent()) return false;
      const success = await act("/api/claude-desktop/create", {name: label, confirm: true}, button, "creating…");
      if (success) toast("Empty profile created; sign in through Claude after choosing Open on the new profile. Nothing has launched.", false, true);
      return success;
    },
  });
}

function openDesktopProfile(profileId, name, opener) {
  if (!state.claudeDesktop?.available || state.claudeDesktop.running !== false) return;
  confirmAction({
    title: `Open ${name}?`, description: desktopProfileWarning + " This only requests a launch; it does not authenticate or switch an account. The Dock normally opens the usual default profile.",
    label: "Open profile", opener, fields: desktopConsent, kind: "profile",
    submit: async (button) => {
      if (!state.claudeDesktop?.available || state.claudeDesktop.running !== false) return false;
      if (!await saveProfileConsent()) return false;
      if (!state.claudeDesktop?.available || state.claudeDesktop.running !== false) return false;
      const result = await act("/api/claude-desktop/open", {profileId, confirm: true}, button, "requesting…");
      dialogOpener = desktopProfileUI.refresh;
      return result;
    },
  });
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
  const heading = el("summary", "auto-heading");
  heading.appendChild(el("span", null, "Auto-switch"));
  ui.status = el("span", "pill", "unavailable");
  heading.appendChild(ui.status);
  auto.appendChild(heading);
  ui.lifecycle = el("p", "hint", automationLifecycle());
  auto.appendChild(ui.lifecycle);
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
  const signature = JSON.stringify([data.available, data.error, data.accounts, data.liveLogin, data.capabilities, isDesktop()]);
  if (!force && providerUI[id].accountSignature === signature) return;
  providerUI[id].accountSignature = signature;
  const openMenus = new Set(Array.from(host.querySelectorAll("[data-menu]")).filter((menu) => menu.open).map((menu) => menu.dataset.menu));
  host.replaceChildren();
  const n = (data.accounts || []).length;
  if (!data.available) { host.appendChild(el("div", "empty", data.error || "unavailable")); return; }
  (data.accounts || []).forEach((a) => host.appendChild(card(id, a, data)));
  const live = data.liveLogin;
  if (live && !live.managed) host.appendChild(adoptRow(id, live));
  else if (!n) {
    const e = el("div", "empty");
    if (isDesktop()) {
      e.textContent = id === "codex"
        ? "No accounts saved yet. Sign in through Codex Desktop or CLI with a file-backed login, then use Add existing login. The Codex CLI is not required for a Desktop login. Open Settings for setup help."
        : "No accounts saved yet. Sign in through this provider's CLI, then use Add existing login. Open Settings for setup help.";
    } else {
      e.textContent = "Nothing managed and nothing signed in. Run ";
      e.appendChild(el("code", null, `agent-switch ${id === "codex" ? "codex " : ""}add`));
    }
    host.appendChild(e);
  }
  if (focused) {
    host.querySelectorAll("[data-menu]").forEach((menu) => { menu.open = openMenus.has(menu.dataset.menu); });
    const next = Array.from(host.querySelectorAll("[data-control]")).find((button) => button.dataset.focusKey === focused);
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
  ui.buttons.add.textContent = isDesktop() ? "Add existing login" : "Add current";
  ui.lifecycle.textContent = automationLifecycle();
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
  ui.reason.textContent = available ? (events.length ? events[events.length - 1].message : "No auto-switch events yet.") : isDesktop() ? "Auto-switch is not available for this provider in the app." : "Auto-switch is not available from this provider/server.";
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
  const preferenceAtStart = preferenceVersion;
  loadingVersion = version;
  try {
    const {ok, body} = await api("/api/state" + (force ? "?force=1" : ""));
    if (version !== requestVersion) return false;
    if (!ok || body.error) {
      $("state-error").hidden = false;
      $("state-error").textContent = safeMessage(body.message || body.error || "Could not load accounts. Your saved logins have not been changed.");
      if (!silent) toast(body.message || body.error || "Could not load state.", true);
      return false;
    }
    state = body;
    if (preferenceAtStart === preferenceVersion && body.preferences) receivePreferences(body.preferences);
    $("state-error").hidden = true;
    $("settings-lifecycle").textContent = automationLifecycle();
    renderHelp();
    $("kpis").replaceChildren(tile("Codex", body.codex || {}), tile("Claude Code", body.claude || {}));
    Object.keys(providers).forEach((id) => updateProvider(id, body[id] || {}, force));
    renderDesktopProfiles(body.claudeDesktop);
    $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
    return true;
  } catch {
    if (version === requestVersion) {
      $("state-error").hidden = false;
      $("state-error").textContent = "The local service couldn't be reached. Previously loaded accounts may be out of date. Refresh or reopen Agent Switch.";
    }
    if (version === requestVersion && !silent) toast(isDesktop() ? "Could not reach the app's local service. Reopen Agent Switch and try again." : "Could not reach agent-switch. Check that the local dashboard server is still open.", true);
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
    if (success && path === "/api/claude-desktop/open") message = "Launch requested only; confirm the selected account in Claude. Sign in there if this profile is new.";
    if (success && path === "/api/claude-desktop/create") message = "Empty profile created; sign in through Claude after opening it.";
    if (path === "/api/auto" && success && payload.threshold !== undefined) providerUI[payload.provider].dirty = false;
    const refreshed = await load(true, true);
    if (!refreshed) message += isDesktop() ? " State could not be refreshed; try Refresh usage or reopen the app." : " State could not be refreshed; check the local dashboard server.";
    if (success && body.followUp) message += " " + safeMessage(body.followUp, payload.token);
    const actionRequired = body.kind === "action-required" && ["codex-running", "codex-status-unknown"].includes(body.code);
    toast(message, (!success && !actionRequired) || !refreshed, noSwitch || !!body.followUp || !!body.restartRequired || actionRequired);
    if ($("action-dialog").open && !success) {
      $("dialog-feedback").textContent = message;
      $("dialog-feedback").hidden = false;
    }
    return success;
  } catch {
    const message = isDesktop() ? "Could not complete the request. Try Refresh usage or reopen the app before retrying." : "Could not complete the request. Check the local dashboard server and refresh the state before retrying.";
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

function confirmAction({title, description, label, opener, submit, fields, kind = "action"}) {
  if (busy || codexFlow || $("action-dialog").open) return;
  dialogKind = kind;
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
    confirmAction({title: `Start live auto-switch for ${providers[id]}?`, description: `This can change the active CLI account automatically, using a ${payload.threshold}% used-quota threshold. ${automationLifecycle()}`, label: "Confirm live mode", opener, submit});
  } else {
    submit(opener);
  }
}

function tokenDialog(opener) {
  let credential, email, slot, overwrite;
  confirmAction({
    title: "Add Claude token / API key",
    description: `Save a Claude credential for CLI use. Claude Desktop sign-in, including the Code tab, stays separate. The credential is sent only to ${isDesktop() ? "this app's local service" : "this local dashboard server"} and is never shown in activity messages.`,
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

__USAGE_SCRIPT__
__SWITCH_SCRIPT__

let preferenceQueue = Promise.resolve();
function receivePreferences(value) {
  if (["system", "light", "dark"].includes(value?.theme)) {
    preferences.theme = value.theme;
    $("theme").value = value.theme;
    applyTheme();
  }
  if ([0, 1].includes(value?.profileNoticeVersion)) preferences.profileNoticeVersion = profileConsentSaved ? 1 : value.profileNoticeVersion;
}

async function loadPreferences() {
  const version = preferenceVersion;
  try {
    const {ok, body} = await api("/api/preferences");
    if (ok && version === preferenceVersion) receivePreferences(body.preferences || body);
  } catch {}
}

function savePreferences(patch) {
  const request = preferenceQueue.then(() => api("/api/preferences", {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(patch)}));
  preferenceQueue = request.catch(() => {});
  return request;
}

async function saveProfileConsent() {
  if (preferences.profileNoticeVersion >= 1 || profileConsentSaved) return true;
  busy = true; ++preferenceVersion; syncBusy();
  try {
    const {ok, body} = await savePreferences({profileNoticeVersion: 1, confirm: true});
    if (!ok || body.ok === false || body.error) throw new Error("Preference write failed");
    profileConsentSaved = true; preferences.profileNoticeVersion = 1;
    return true;
  } catch {
    $("dialog-feedback").textContent = "The profile acknowledgement couldn't be saved. Nothing was created or opened. Try again.";
    $("dialog-feedback").hidden = false;
    return false;
  } finally { busy = false; syncBusy(); }
}

function navigate(view, updateHash = true) {
  view = ["accounts", "usage", "settings"].includes(view) ? view : "accounts";
  if (busy) { if (location.hash !== "#" + activeView) location.hash = activeView; return; }
  if (codexFlow) cancelCodexSwitch();
  if ($("action-dialog").open) $("action-dialog").close();
  const changed = activeView !== view;
  if (changed && activeView === "usage") { ++analyticsVersion; analyticsLoading = false; }
  activeView = view;
  for (const id of ["accounts", "usage", "settings"]) {
    $("view-" + id).hidden = view !== id;
    $("nav-" + id).setAttribute("aria-current", view === id ? "page" : "false");
  }
  (view === "settings" ? $("settings-guide-slot") : $("guide-slot")).appendChild($("desktop-guide"));
  renderHelp();
  if (updateHash && location.hash !== "#" + view) location.hash = view;
  if (changed) $(view + "-title").focus({preventScroll: true});
  if (view === "usage" && (changed || !analyticsBody)) return loadAnalytics();
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
  dialogAction = null; dialogKind = null;
  if (dialogOpener && dialogOpener.isConnected) dialogOpener.focus();
  else $("theme").focus();
});

try { $("theme").value = localStorage.getItem("agent-switch-theme") || "system"; } catch {}
function applyTheme() {
  const theme = ["light", "dark"].includes($("theme").value) ? $("theme").value : "system";
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("agent-switch-theme", theme); } catch {}
}
$("theme").onchange = async () => {
  const version = ++preferenceVersion;
  applyTheme();
  const theme = document.documentElement.dataset.theme;
  try {
    const {ok, body} = await savePreferences({theme});
    if (version !== preferenceVersion) return;
    if (!ok || body.ok === false || body.error) throw new Error("Preference write failed");
    preferences.theme = theme;
  } catch {
    if (version === preferenceVersion) toast("Appearance changed for this window, but couldn't be saved. Try again in Settings.", true);
  }
};
applyTheme();
$("watch").onchange = () => {
  $("watch-status").textContent = $("watch").checked ? "Every 20 seconds · auto-switch runs independently" : "Updates paused · auto-switch is not stopped";
  if ($("watch").checked && !busy && !$("action-dialog").open) load();
};
$("help-toggle").onclick = () => {
  helpRequested = true;
  renderHelp();
  $("guide-title").focus();
};
$("help-close").onclick = () => {
  helpRequested = false;
  helpDismissed = true;
  renderHelp();
  $("help-toggle").focus();
};
$("guide-check").onclick = () => refreshUsage($("guide-check"));
Object.keys(providers).forEach(setupHelp);
Object.keys(providers).forEach(setupProvider);
setupDesktopProfiles();
for (const view of ["accounts", "usage", "settings"]) $("nav-" + view).onclick = () => navigate(view);
window.addEventListener("hashchange", () => navigate(location.hash.slice(1), false));
loadPreferences();
load();
navigate((location.hash || "").slice(1), false);
setInterval(() => { if ($("watch").checked && !busy && !$("action-dialog").open) load(); }, 20000);
setInterval(() => {
  if (!busy && (dialogKind === "profile" || (activeView === "accounts" && !$("claude-desktop-panel").hidden)) && (!$("action-dialog").open || dialogKind === "profile")) load(false, true);
}, 5000);
</script>
</body>
</html>
""".replace("__PAGE_STYLE__", PAGE_STYLE).replace("__USAGE_SCRIPT__", USAGE_SCRIPT).replace("__SWITCH_SCRIPT__", SWITCH_SCRIPT)

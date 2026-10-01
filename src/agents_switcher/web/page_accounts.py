"""Explicit storage import and isolated Codex sign-in interactions."""

ACCOUNTS_SCRIPT = r"""
let storageState = null, storageVersion = 0, codexEnrollmentFlow = null;

async function loadStorage() {
  const version = ++storageVersion;
  try {
    const {ok, body} = await api("/api/storage");
    if (version !== storageVersion) return;
    if (!ok || body.ok === false || body.error || typeof body.destination !== "string") throw new Error("Storage unavailable");
    storageState = body;
    const sources = Array.isArray(body.sources) ? body.sources : [];
    $("storage-location").textContent = body.destination;
    $("storage-status").textContent = body.imported
      ? "Previous accounts were imported. Their original store was left intact."
      : body.canImport ? "Previous saved accounts are available to copy into this empty store. Nothing has been imported."
        : sources.length ? "Previous storage was found, but import is not available. Existing accounts here will never be overwritten."
          : "No previous saved-account store was found. New accounts are saved only in Agent Switch's own store.";
    $("storage-warnings").replaceChildren();
    for (const warning of body.warnings || []) $("storage-warnings").appendChild(el("li", null, safeMessage(warning)));
    for (const source of sources) {
      if (source.error) $("storage-warnings").appendChild(el("li", null, safeMessage(source.label + ": " + source.error)));
    }
    $("storage-warnings").hidden = !$("storage-warnings").children.length;
    block($("storage-import"), body.canImport !== true);
    $("storage-notice").hidden = body.imported === true || !sources.length;
  } catch {
    if (version !== storageVersion) return;
    storageState = null;
    $("storage-status").textContent = "Storage status could not be checked. Choose Check storage to retry; nothing has been imported.";
    block($("storage-import"), true);
  }
}

function importStorage(opener) {
  if (storageState?.canImport !== true) return;
  const sources = (storageState.sources || []).filter(source => ["legacy", "xdg"].includes(source.id));
  if (!sources.length) return;
  const usable = sources.filter(source => !source.error && (!source.counts || Object.values(source.counts).some(count => Number.isInteger(count) && count > 0)));
  let source, consent;
  confirmAction({
    title: "Copy previous saved accounts?", opener, label: "Import accounts",
    description: "Quit other account switchers and provider clients first, and stop this app's auto-switch and preview modes. This copies saved accounts into Agent Switch's independent store without moving or deleting the original. Desktop profiles and terminal sessions are excluded.",
    fields: host => {
      const label = el("label", "field", "Previous store");
      source = el("select"); source.required = true; source.dataset.lock = "";
      source.id = "import-source"; label.htmlFor = source.id;
      source.setAttribute("aria-label", "Previous store");
      if (sources.length > 1) { const placeholder = el("option", null, "Choose a store…"); placeholder.value = ""; source.appendChild(placeholder); }
      for (const item of sources) {
        const counts = item.counts ? ` · ${item.counts.claude} Claude, ${item.counts.codex} Codex` : "";
        const option = el("option", null, item.label + counts + (item.error ? " · unavailable" : ""));
        option.value = item.id; option.disabled = !usable.includes(item); source.appendChild(option);
      }
      source.value = sources.length === 1 ? sources[0].id : "";
      label.appendChild(source);
      const check = el("label", "check"); consent = el("input");
      Object.assign(consent, {type: "checkbox", required: true, id: "import-consent"}); consent.dataset.lock = "";
      check.append(consent, el("span", null, "Other switchers and provider clients are stopped. Copy their saved credentials into Agent Switch."));
      host.append(label, check, el("p", "hint", "Import cannot repair a revoked login. Use Sign in again on the affected Codex account afterwards."));
    },
    submit: async button => {
      if (!consent.checked || !usable.some(item => item.id === source.value)) return false;
      ++storageVersion;
      const success = await act("/api/storage/import", {source: source.value, confirm: true}, button, "Importing…");
      await loadStorage();
      if (success) await loadPreferences();
      return success;
    },
  });
}

function enrollmentMessage(message, error = false) {
  const text = safeMessage(message);
  if ($("codex-login-status").textContent !== text) $("codex-login-status").textContent = text;
  $("codex-login-status").className = "switch-status" + (error ? " dialog-error" : "");
}

function currentCodexEnrollment(flow) {
  return codexEnrollmentFlow === flow && $("codex-login-dialog").open && flow.phase !== "cancelling";
}

function updateEnrollmentControls(flow) {
  if (codexEnrollmentFlow !== flow) return;
  $("codex-login-next").textContent = {
    consent: "Continue in browser", preparing: "Preparing…", opening: "Opening browser…",
    waiting: flow.openFailed ? "Retry opening browser" : "Reopen browser", exchanging: "Finishing sign-in…",
    saving: "Saving account…", saved: "Use saved login", "save-error": "Retry save",
    "status-error": "Check again", error: "Start again", expired: "Start again", cancelling: "Closing…",
  }[flow.phase];
  block($("codex-login-next"), flow.phase === "exchanging" || (flow.phase === "saved" && (!flow.account?.number || flow.refreshFailed || !supports(state.codex || {}, "switch"))));
  $("codex-login-cancel").textContent = flow.phase === "saved" ? "Close" : "Cancel";
  $("codex-login-refresh").hidden = flow.phase !== "save-error" && !(flow.phase === "saved" && (flow.refreshFailed || !flow.account?.number || !supports(state.codex || {}, "switch")));
  $("codex-login-auto-notice").hidden = state.codex?.auto?.mode !== "live";
}

function stopEnrollmentPolling(flow) {
  clearTimeout(flow.timer); flow.timer = null;
  clearTimeout(flow.pollTimeout); flow.pollTimeout = null;
  const controller = flow.controller; flow.controller = null;
  controller?.abort();
}

function startCodexEnrollment(opener, account = null) {
  if (busy || codexFlow || codexEnrollmentFlow || $("action-dialog").open || !supports(state.codex || {}, "login")) return;
  const flow = codexEnrollmentFlow = {opener, target: account, number: account?.number, sessionId: null, phase: "consent", timer: null, polling: false};
  $("codex-login-title").textContent = account ? "Sign in again to Codex" : "Add a Codex account";
  $("codex-login-description").textContent = account
    ? `Continue to sign in with ChatGPT and automatically replace the saved login for ${account.alias || account.email || "account " + account.number} in slot ${account.number}. Sign in to that exact account; a different identity will be refused. Signing in saves this account; it does not switch accounts.`
    : "Continue to sign in with ChatGPT in your browser and automatically save the account in Agent Switch. Signing in saves this account; it does not switch accounts.";
  enrollmentMessage("Nothing has changed yet. No Codex CLI is needed.");
  updateEnrollmentControls(flow);
  $("codex-login-dialog").showModal(); syncBusy(); $("codex-login-next").focus();
}

function closeCodexEnrollment(flow) {
  if (codexEnrollmentFlow !== flow) return;
  stopEnrollmentPolling(flow);
  codexEnrollmentFlow = null;
  if ($("codex-login-dialog").open) $("codex-login-dialog").close();
  const opener = flow.opener?.isConnected ? flow.opener : providerUI.codex.buttons.login;
  syncBusy();
  queueMicrotask(() => { if (!codexEnrollmentFlow && !codexFlow && opener && !opener.disabled) opener.focus(); });
}

async function enrollmentRequest(action, payload, signal) {
  return api("/api/codex/login/" + action, {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify({...payload, confirm: true}), ...(signal ? {signal} : {})});
}

function scheduleEnrollmentPoll(flow) {
  if (!currentCodexEnrollment(flow) || flow.polling || !["waiting", "exchanging"].includes(flow.phase)) return;
  clearTimeout(flow.timer);
  flow.timer = setTimeout(() => { flow.timer = null; return pollCodexEnrollment(flow); }, 1000);
}

async function showEnrollmentSaved(flow, body) {
  flow.phase = "saved";
  flow.account = body.account || null;
  stopEnrollmentPolling(flow);
  $("codex-login-title").textContent = "Codex account saved";
  $("codex-login-description").textContent = "Signing in saves this account; it does not switch accounts.";
  const account = flow.account?.email || flow.account?.alias || (flow.account?.number ? "account " + flow.account.number : "your account");
  const warning = body.cleanupWarning || (body.warning || body.ok === false ? body.message || "Sign-in cleanup needs attention. Close to retry cleanup; the saved account will remain." : "");
  enrollmentMessage(`Account saved: ${account}. Choose Use saved login when you're ready to quit Codex and switch.` + (warning ? " " + warning : ""), !!warning);
  updateEnrollmentControls(flow);
  flow.refreshFailed = !await load(true, true);
  if (!currentCodexEnrollment(flow)) return;
  if (flow.refreshFailed) toast("Account saved, but Accounts could not be refreshed. Refresh Accounts before switching.", true, true);
  else if (warning) toast("Account saved. " + warning, true, true);
}

async function saveCodexEnrollment(flow) {
  if (!currentCodexEnrollment(flow) || busy || !["waiting", "exchanging", "save-error"].includes(flow.phase)) return;
  flow.phase = "saving"; stopEnrollmentPolling(flow);
  busy = true; ++requestVersion; syncBusy(); updateEnrollmentControls(flow);
  enrollmentMessage("Saving the account. Sign-in does not switch accounts. Keep this window open.");
  try {
    const {ok, body} = await enrollmentRequest("complete", {sessionId: flow.sessionId, activate: false});
    if (!currentCodexEnrollment(flow)) return;
    if (body.activationRequired === true && body.account && body.switched !== true) {
      await showEnrollmentSaved(flow, {...body, warning: !ok || body.ok !== true || !!body.error || body.warning});
      return;
    }
    const terminal = body.code === "wrong-account" || ["error", "expired"].includes(body.status);
    flow.phase = terminal ? "error" : "save-error";
    enrollmentMessage((body.message || body.error || "The save result could not be confirmed.") + (terminal ? " Choose Start again to sign in to the expected account." : " Refresh Accounts or choose Retry save using this same sign-in; do not sign in again."), true);
  } catch {
    if (!currentCodexEnrollment(flow)) return;
    flow.phase = "save-error";
    enrollmentMessage("The save result could not be confirmed. Refresh Accounts or choose Retry save using this same sign-in; do not sign in again.", true);
  } finally {
    busy = false; syncBusy(); updateEnrollmentControls(flow);
  }
}

async function pollCodexEnrollment(flow) {
  if (!currentCodexEnrollment(flow) || flow.polling || !["waiting", "exchanging"].includes(flow.phase)) return;
  clearTimeout(flow.timer); flow.timer = null; flow.polling = true;
  const controller = new AbortController(); flow.controller = controller;
  const timeout = flow.pollTimeout = setTimeout(() => controller.abort(), 10000);
  try {
    const {ok, body} = await enrollmentRequest("status", {sessionId: flow.sessionId}, controller.signal);
    if (!currentCodexEnrollment(flow) || flow.controller !== controller || !["waiting", "exchanging"].includes(flow.phase)) return;
    if (["error", "expired"].includes(body.status) || !ok || body.ok !== true || body.error) {
      flow.phase = body.status === "expired" ? "expired" : "error";
      enrollmentMessage(body.message || body.error || "Sign-in could not finish. Choose Start again to retry.", true);
    } else if (body.status === "ready") {
      await saveCodexEnrollment(flow);
    } else if (body.status === "saved") {
      busy = true; ++requestVersion; syncBusy();
      try { await showEnrollmentSaved(flow, body); }
      finally { busy = false; syncBusy(); }
    } else if (["waiting", "exchanging"].includes(body.status)) {
      flow.phase = body.status;
      if (!flow.openFailed || body.status === "exchanging") enrollmentMessage(body.message || (body.status === "waiting" ? "Waiting for sign-in in your browser. You can cancel at any time." : "Finishing sign-in. You can still cancel."));
    } else {
      flow.phase = "status-error";
      enrollmentMessage("Sign-in status could not be confirmed. Choose Check again to check this same sign-in.", true);
    }
  } catch {
    if (!currentCodexEnrollment(flow) || flow.controller !== controller || !["waiting", "exchanging"].includes(flow.phase)) return;
    flow.phase = "status-error";
    enrollmentMessage("Sign-in status could not be checked. Choose Check again to check this same sign-in.", true);
  } finally {
    clearTimeout(timeout);
    if (flow.pollTimeout === timeout) flow.pollTimeout = null;
    if (flow.controller === controller) flow.controller = null;
    flow.polling = false;
    updateEnrollmentControls(flow); scheduleEnrollmentPoll(flow);
  }
}

async function continueCodexEnrollment() {
  const flow = codexEnrollmentFlow;
  if (!flow || !currentCodexEnrollment(flow) || busy) return;
  if (flow.phase === "save-error") { await saveCodexEnrollment(flow); return; }
  if (flow.phase === "status-error") { flow.phase = "waiting"; await pollCodexEnrollment(flow); return; }
  if (flow.phase === "saved") {
    if (!flow.account?.number || flow.refreshFailed || !supports(state.codex || {}, "switch")) return;
    const account = (state.codex.accounts || []).find(account => account.number === flow.account.number) || flow.account;
    if (await cancelCodexEnrollment()) await requestCodexSwitch({...account, activationRequired: true}, providerUI.codex.buttons.login);
    return;
  }
  if (["error", "expired"].includes(flow.phase) && flow.sessionId) {
    if (!await cancelCodexEnrollment()) return;
    startCodexEnrollment(flow.opener, flow.target);
    await continueCodexEnrollment();
    return;
  }
  if (!["consent", "waiting", "error", "expired"].includes(flow.phase)) return;
  stopEnrollmentPolling(flow);
  busy = true; ++requestVersion; syncBusy();
  try {
    if (!flow.sessionId) {
      flow.phase = "preparing"; updateEnrollmentControls(flow); enrollmentMessage("Preparing browser sign-in…");
      const {ok, body} = await enrollmentRequest("prepare", flow.number ? {number: flow.number} : {});
      if (!currentCodexEnrollment(flow)) return;
      if (typeof body.sessionId === "string" && body.sessionId) flow.sessionId = body.sessionId;
      if (!ok || body.ok !== true || body.error || !flow.sessionId || body.method !== "browser" || !["waiting", "exchanging", "ready"].includes(body.status)) {
        flow.phase = body.status === "expired" ? "expired" : "error";
        enrollmentMessage(body.message || body.error || "Browser sign-in could not be prepared. Choose Start again to retry.", true);
        return;
      }
      if (body.status !== "waiting") {
        flow.phase = "exchanging";
        enrollmentMessage("Resuming your existing browser sign-in. The account will be saved automatically; sign-in does not switch accounts.");
        return;
      }
    }
    flow.phase = "opening"; updateEnrollmentControls(flow); enrollmentMessage("Opening sign-in in your default browser…");
    const {ok, body} = await enrollmentRequest("open", {sessionId: flow.sessionId});
    if (!currentCodexEnrollment(flow)) return;
    flow.phase = "waiting"; flow.openFailed = !ok || body.ok !== true || !!body.error;
    enrollmentMessage(flow.openFailed ? body.message || body.error || "The browser could not be opened. Choose Retry opening browser." : "Waiting for sign-in in your browser. The account will be saved automatically; sign-in does not switch accounts.", flow.openFailed);
  } catch {
    if (!currentCodexEnrollment(flow)) return;
    flow.phase = flow.sessionId ? "waiting" : "error"; flow.openFailed = true;
    enrollmentMessage(flow.sessionId ? "The browser request could not be confirmed. Choose Retry opening browser." : "Browser sign-in could not be prepared. Choose Start again to retry.", true);
  } finally {
    busy = false; syncBusy(); updateEnrollmentControls(flow); scheduleEnrollmentPoll(flow);
  }
}

async function cancelCodexEnrollment() {
  const flow = codexEnrollmentFlow;
  if (!flow || busy) return false;
  flow.phase = "cancelling"; stopEnrollmentPolling(flow);
  if (!flow.sessionId) { closeCodexEnrollment(flow); return true; }
  busy = true; syncBusy(); updateEnrollmentControls(flow);
  try {
    const {ok, body} = await enrollmentRequest("cancel", {sessionId: flow.sessionId});
    if (!ok || body.ok !== true || body.error) {
      closeCodexEnrollment(flow);
      toast((body.message || body.error || "Sign-in cleanup was not confirmed.") + " Quit Agent Switch to retry cleanup. Saved accounts are not removed.", true, true);
      return false;
    }
    closeCodexEnrollment(flow);
    return true;
  } catch {
    closeCodexEnrollment(flow);
    toast("The local service couldn't confirm sign-in cleanup. Quit Agent Switch to retry cleanup. Saved accounts are not removed.", true, true);
    return false;
  } finally { busy = false; syncBusy(); }
}

$("storage-review").onclick = () => { navigate("settings"); $("storage-import").focus(); };
$("storage-import").onclick = () => importStorage($("storage-import"));
$("storage-refresh").onclick = () => loadStorage();
$("codex-login-next").onclick = continueCodexEnrollment;
$("codex-login-cancel").onclick = cancelCodexEnrollment;
$("codex-login-dialog").addEventListener("cancel", event => { event.preventDefault(); cancelCodexEnrollment(); });
$("codex-login-dialog").addEventListener("close", () => {
  if (codexEnrollmentFlow && !$("codex-login-dialog").open) {
    if (busy) $("codex-login-dialog").showModal();
    else cancelCodexEnrollment();
  }
});
$("codex-login-refresh").onclick = async () => {
  const flow = codexEnrollmentFlow;
  if (!flow || busy || !["save-error", "saved"].includes(flow.phase)) return;
  busy = true; syncBusy();
  try {
    flow.refreshFailed = !await load(true, true);
    toast(flow.refreshFailed ? flow.phase === "saved" ? "Accounts could not be refreshed. The account is saved; try refreshing again." : "Accounts could not be refreshed. Your sign-in is still available for Retry save." : "Accounts refreshed. No switch was requested.", flow.refreshFailed, true);
  } finally { busy = false; syncBusy(); updateEnrollmentControls(flow); }
};
"""

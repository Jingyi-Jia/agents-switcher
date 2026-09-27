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
  $("codex-login-status").textContent = safeMessage(message);
  $("codex-login-status").className = "switch-status" + (error ? " dialog-error" : "");
}

function startCodexEnrollment(opener, account = null) {
  if (busy || codexFlow || codexEnrollmentFlow || $("action-dialog").open || !supports(state.codex || {}, "login")) return;
  codexEnrollmentFlow = {opener, number: account?.number, sessionId: null};
  $("codex-login-title").textContent = account ? "Sign in again to Codex" : "Add a Codex account";
  $("codex-login-description").textContent = account
    ? `Repair ${account.alias || account.email || "account " + account.number} in slot ${account.number}. Sign in to that exact account; a different identity will be refused. Other saved accounts stay intact.`
    : "Sign in to another account without signing out the one you already saved. Your current login stays unchanged until you choose Save & switch.";
  $("codex-login-terminal").hidden = true;
  $("codex-login-command").value = "";
  $("codex-login-next").textContent = "Prepare sign-in";
  $("codex-login-next").hidden = false;
  $("codex-login-cancel").textContent = "Cancel";
  for (const stage of ["prepare", "signin", "save"]) $("login-step-" + stage).className = stage === "prepare" ? "current" : "";
  enrollmentMessage("Nothing has changed yet. Prepare sign-in creates an empty private folder and a command for your terminal.");
  $("codex-login-dialog").showModal(); $("codex-login-cancel").focus(); syncBusy();
}

function closeCodexEnrollment(flow) {
  if (codexEnrollmentFlow !== flow) return;
  codexEnrollmentFlow = null;
  $("codex-login-command").value = "";
  if ($("codex-login-dialog").open) $("codex-login-dialog").close();
  const opener = flow.opener?.isConnected ? flow.opener : providerUI.codex.buttons.login;
  syncBusy();
  queueMicrotask(() => { if (opener && !opener.disabled) opener.focus(); });
}

async function continueCodexEnrollment() {
  const flow = codexEnrollmentFlow;
  if (!flow || flow.completed || busy) return;
  const completing = !!flow.sessionId;
  busy = true; ++requestVersion; syncBusy();
  $("codex-login-next").textContent = completing ? "Saving & switching…" : "Preparing…";
  enrollmentMessage(completing ? "Checking that Codex is closed, then saving and switching. Keep this window open." : "Preparing a fresh private sign-in folder…");
  try {
    const path = completing ? "/api/codex/login/complete" : "/api/codex/login/prepare";
    const payload = completing ? {sessionId: flow.sessionId, confirm: true} : {confirm: true, ...(flow.number ? {number: flow.number} : {})};
    const {ok, body} = await api(path, {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify(payload)});
    if (!ok || body.ok === false || body.error) {
      if (body.activationRequired === true && body.account) {
        flow.saved = true;
        $("codex-login-terminal").hidden = true;
        $("codex-login-command").value = "";
        $("login-step-signin").className = ""; $("login-step-save").className = "current";
      }
      enrollmentMessage((body.message || body.error || "The sign-in request failed. Check the terminal result before retrying.") + (flow.saved ? " Choose Retry switch after fixing the problem; do not sign in again." : ""), body.kind !== "action-required");
      return;
    }
    if (!completing) {
      if (typeof body.sessionId !== "string" || !body.sessionId || typeof body.command !== "string" || !body.command) {
        enrollmentMessage("The service did not return a sign-in command. Close this dialog and try again.", true);
        return;
      }
      flow.sessionId = body.sessionId;
      $("codex-login-command").value = body.command;
      $("codex-login-shell").textContent = body.shell === "powershell" ? "PowerShell command" : "Terminal command";
      $("codex-login-terminal").hidden = false;
      $("login-step-prepare").className = ""; $("login-step-signin").className = "current";
      enrollmentMessage("Ready for your terminal. No sign-in has been launched and your current login is unchanged.");
      $("codex-login-command").focus();
    } else {
      $("login-step-signin").className = ""; $("login-step-save").className = "current";
      const refreshed = await load(true, true);
      toast((body.message || "Codex account saved and switched.") + " Open Codex when you're ready and confirm the account there." + (refreshed ? "" : " Refresh Accounts to check the result."), !refreshed, true);
      if (body.warning) {
        flow.completed = true;
        $("codex-login-terminal").hidden = true;
        $("codex-login-command").value = "";
        $("codex-login-next").hidden = true;
        $("codex-login-cancel").textContent = "Close & clean up";
        enrollmentMessage("The account was saved and switched, but temporary sign-in cleanup needs attention. Stop the terminal sign-in, then choose Close & clean up. " + (body.cleanupWarning || ""), true);
      } else closeCodexEnrollment(flow);
    }
  } catch {
    enrollmentMessage(completing
      ? "The save & switch result could not be confirmed. Close this dialog and refresh Accounts before retrying; don't repeat the terminal sign-in command."
      : "The local service couldn't prepare sign-in. Close this dialog and try again.", true);
  } finally {
    busy = false;
    $("codex-login-next").textContent = flow.saved ? "Retry switch" : flow.sessionId ? "Save & switch" : "Prepare sign-in";
    syncBusy();
  }
}

async function cancelCodexEnrollment() {
  const flow = codexEnrollmentFlow;
  if (!flow || busy) return false;
  if (!flow.sessionId) { closeCodexEnrollment(flow); return true; }
  busy = true; syncBusy();
  try {
    const {ok, body} = await api("/api/codex/login/cancel", {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify({sessionId: flow.sessionId, confirm: true})});
    if (!ok || body.ok === false || body.error) {
      closeCodexEnrollment(flow);
      toast((body.message || body.error || "Temporary sign-in cleanup was not confirmed.") + " Stop the terminal sign-in, then quit Agent Switch to retry cleanup. Saved accounts are not removed.", true, true);
      return false;
    }
    closeCodexEnrollment(flow);
    return true;
  } catch {
    closeCodexEnrollment(flow);
    toast("The local service couldn't confirm cleanup. Stop the terminal sign-in, then quit Agent Switch to retry cleanup of its temporary state. Saved accounts are not removed.", true, true);
    return false;
  } finally { busy = false; syncBusy(); }
}

$("storage-review").onclick = () => { navigate("settings"); $("storage-import").focus(); };
$("storage-import").onclick = () => importStorage($("storage-import"));
$("storage-refresh").onclick = () => loadStorage();
$("codex-login-next").onclick = continueCodexEnrollment;
$("codex-login-cancel").onclick = cancelCodexEnrollment;
$("codex-login-dialog").addEventListener("cancel", event => { event.preventDefault(); cancelCodexEnrollment(); });
$("codex-login-copy").onclick = async () => {
  const command = $("codex-login-command");
  if (!codexEnrollmentFlow?.sessionId || busy) return;
  try {
    if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
    await navigator.clipboard.writeText(command.value);
    enrollmentMessage("Command copied. Run it in your terminal, finish sign-in, then fully quit Codex before saving.");
  } catch {
    command.focus(); command.select?.();
    enrollmentMessage("Select and copy the command above, then run it in your terminal. Clipboard access wasn't available.");
  }
};
"""

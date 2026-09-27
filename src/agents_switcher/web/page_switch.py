"""Cancellable, quit-first Codex guidance embedded into the dashboard."""

SWITCH_SCRIPT = r"""
let codexFlow = null, codexGeneration = 0, codexWaitTimer;

function currentCodexFlow(flow) {
  return !!flow && codexFlow === flow && flow.generation === codexGeneration && $("codex-dialog").open && activeView === "accounts";
}

function codexStopped(status) {
  return status?.available === true && status.running === false && status.terminalCount === 0 && status.backgroundCount === 0 && status.desktopRunning === false;
}

function codexAssistAllowed(status) {
  return status?.available === true && status.running === true && status.canAssist === true && status.desktopRunning === true && status.terminalCount === 0 && status.backgroundCount === 0;
}

function codexMessage(message, error = false) {
  $("codex-dialog-status").textContent = safeMessage(message);
  $("codex-dialog-status").className = "switch-status" + (error ? " dialog-error" : "");
}

function updateCodexControls(flow) {
  const committed = flow?.phase === "switching" || flow?.phase === "opening";
  const waiting = ["checking", "quitting", "waiting"].includes(flow?.phase);
  $("codex-cancel").disabled = committed;
  $("codex-check").disabled = waiting || committed;
  $("codex-continue").disabled = waiting || committed || !codexStopped(flow?.status);
  $("codex-assist").hidden = !codexAssistAllowed(flow?.status);
  $("codex-assist").disabled = waiting || committed;
  $("codex-check").hidden = committed;
  for (const [id, phases] of [["quit", ["checking", "quitting", "waiting", "ready"]], ["switch", ["switching"]], ["open", ["opening"]]]) {
    $("codex-step-" + id).className = phases.includes(flow?.phase) ? "current" : "";
  }
}

function cancelCodexSwitch() {
  if (!codexFlow || ["switching", "opening"].includes(codexFlow.phase)) return false;
  const opener = codexFlow.opener;
  codexFlow.controller?.abort();
  ++codexGeneration; clearTimeout(codexWaitTimer); codexFlow = null;
  if ($("codex-dialog").open) $("codex-dialog").close();
  if (opener?.isConnected) opener.focus();
  return true;
}

async function readCodexStatus(flow) {
  const controller = new AbortController();
  flow.controller = controller;
  let timeout;
  const unavailable = {available: false, running: null, message: "Codex process status could not be checked. Your current login is unchanged. Try Check again."};
  const expired = new Promise((resolve) => {
    controller.signal.addEventListener("abort", () => resolve(unavailable), {once: true});
    timeout = setTimeout(() => controller.abort(), 5000);
  });
  try {
    const request = api("/api/codex/status", {signal: controller.signal}).then(({ok, body}) => ok && typeof body.running === "boolean" ? body : {...body, running: null}).catch(() => unavailable);
    return await Promise.race([request, expired]);
  } finally { clearTimeout(timeout); if (flow.controller === controller) flow.controller = null; }
}

async function checkCodexFlow(flow) {
  if (!currentCodexFlow(flow)) return;
  flow.phase = "checking"; updateCodexControls(flow); codexMessage("Checking that Codex is fully quit. Your current login is unchanged.");
  const status = await readCodexStatus(flow);
  if (!currentCodexFlow(flow)) return;
  flow.status = status; flow.phase = "ready";
  const detail = codexStopped(status)
    ? "Codex is fully quit. Choose Continue & switch when you're ready. Nothing has switched yet."
    : status.running == null || !status.available
      ? "Process status is unknown. Switching stays blocked until a check confirms Codex is stopped."
      : status.terminalCount > 0 || status.backgroundCount > 0
        ? `Close your Codex terminal sessions and background processes first (${status.terminalCount || 0} terminal, ${status.backgroundCount || 0} background). We never stop these for you.`
        : codexAssistAllowed(status)
          ? "Codex Desktop is running. Save your work, then quit it yourself or choose Quit, switch & reopen. Your current login is unchanged."
          : "Fully quit Codex Desktop and all Codex CLI sessions, then choose Check again. Closing a window may not quit the app. Your current login is unchanged.";
  codexMessage([detail, status.message].filter(Boolean).join(" "));
  updateCodexControls(flow);
}

async function requestCodexSwitch(account, opener) {
  if (busy || codexFlow || $("action-dialog").open || activeView !== "accounts") return;
  const flow = codexFlow = {generation: ++codexGeneration, number: account.number, label: account.alias || account.email || "account " + account.number, opener, phase: "checking", status: null, attempts: 0};
  $("codex-dialog-title").textContent = "Switch to " + flow.label + "?";
  $("codex-dialog-description").textContent = "Codex must be fully quit before its saved login can change. This prepares the next launch; it doesn't change the identity of a running app.";
  $("codex-dialog").showModal(); $("codex-cancel").focus();
  await checkCodexFlow(flow);
}

async function performCodexSwitch(flow, reopen) {
  if (!currentCodexFlow(flow) || busy) return;
  flow.phase = "checking"; updateCodexControls(flow);
  const status = await readCodexStatus(flow);
  if (!currentCodexFlow(flow)) return;
  flow.status = status;
  if (!codexStopped(status)) {
    flow.phase = "ready"; updateCodexControls(flow);
    codexMessage("Codex is no longer confirmed stopped. Nothing was switched. Close any remaining sessions and choose Check again.");
    return;
  }
  busy = true; ++requestVersion; flow.phase = "switching"; syncBusy(); updateCodexControls(flow);
  codexMessage("Switching the saved login. Please keep this window open.");
  try {
    const {ok, body} = await api("/api/switch", {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify({provider: "codex", number: flow.number})});
    if (!ok || body.ok === false || body.error || body.switched === false) {
      const expected = body.kind === "action-required" && ["codex-running", "codex-status-unknown"].includes(body.code);
      flow.status = null; flow.phase = "ready";
      codexMessage(body.message || body.error || body.reason || "The account could not be switched.", !expected);
      return;
    }
    let message = body.message || "Codex login switched.";
    if (body.followUp) message += " " + body.followUp;
    let launchFailed = false;
    if (reopen) {
      flow.phase = "opening"; updateCodexControls(flow); codexMessage("Login switched. Requesting a new Codex launch…");
      try {
        const opened = await api("/api/codex/open", {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify({confirm: true})});
        launchFailed = !opened.ok || opened.body.ok === false || !!opened.body.error;
        message += launchFailed ? " Codex did not reopen. Your login was switched; open Codex yourself. " + (opened.body.message || "") : " Launch requested. Confirm the selected account in Codex.";
      } catch { launchFailed = true; message += " Login switched, but the launch could not be requested. Open Codex yourself."; }
    } else message += " Open Codex when you're ready and confirm the selected account there.";
    const refreshed = await load(true, true);
    if (!refreshed) message += " State could not be refreshed. Check Accounts before switching again.";
    toast(message, launchFailed || !refreshed, true);
    codexFlow = null; ++codexGeneration; $("codex-dialog").close();
  } catch {
    flow.status = null; flow.phase = "ready";
    codexMessage("The switch request could not be confirmed. Refresh Accounts and check the saved login before retrying. Codex has not been reopened.", true);
  } finally {
    busy = false; syncBusy(); updateCodexControls(codexFlow);
  }
}

async function pollCodexQuit(flow) {
  if (!currentCodexFlow(flow)) return;
  const status = await readCodexStatus(flow);
  if (!currentCodexFlow(flow)) return;
  flow.status = status;
  if (codexStopped(status)) { await performCodexSwitch(flow, true); return; }
  if (status.terminalCount > 0 || status.backgroundCount > 0 || ++flow.attempts >= 20) {
    flow.phase = "ready"; updateCodexControls(flow);
    codexMessage("Codex has not been confirmed stopped. Nothing was switched. Close remaining sessions, then Check again. Continuing will require another click.");
    return;
  }
  flow.phase = "waiting"; updateCodexControls(flow);
  codexMessage(`Waiting for Codex to quit (${flow.attempts}/20)… You can still cancel; no switch has started.${status.running == null ? " Process status is currently unknown." : ""}`);
  codexWaitTimer = setTimeout(() => pollCodexQuit(flow), 1000);
}

async function assistCodexSwitch() {
  const flow = codexFlow;
  if (!currentCodexFlow(flow) || flow.phase !== "ready" || !codexAssistAllowed(flow.status)) return;
  flow.phase = "quitting"; updateCodexControls(flow);
  codexMessage("Requesting a normal Codex quit. Cancel stops the pending switch, but cannot undo a quit already requested.");
  try {
    const result = await api("/api/codex/quit", {method: "POST", headers: {"X-Auth-Token": TOKEN, "Content-Type": "application/json"}, body: JSON.stringify({confirm: true})});
    if (!currentCodexFlow(flow)) return;
    if (!result.ok || result.body.ok === false || result.body.error) {
      flow.phase = "ready"; flow.status = null; updateCodexControls(flow);
      codexMessage(result.body.message || result.body.error || "Codex could not be asked to quit. Quit it yourself and choose Check again.", result.body.kind !== "action-required");
      return;
    }
    await pollCodexQuit(flow);
  } catch {
    if (!currentCodexFlow(flow)) return;
    flow.phase = "ready"; flow.status = null; updateCodexControls(flow);
    codexMessage("The quit request could not be confirmed. Nothing was switched. Quit Codex yourself and choose Check again.", true);
  }
}

$("codex-check").onclick = () => codexFlow && checkCodexFlow(codexFlow);
$("codex-continue").onclick = () => codexFlow?.phase === "ready" && codexStopped(codexFlow.status) && performCodexSwitch(codexFlow, false);
$("codex-assist").onclick = assistCodexSwitch;
$("codex-cancel").onclick = cancelCodexSwitch;
$("codex-dialog").addEventListener("cancel", (event) => { event.preventDefault(); cancelCodexSwitch(); });
$("codex-dialog").addEventListener("close", () => { if (codexFlow) cancelCodexSwitch(); });
"""

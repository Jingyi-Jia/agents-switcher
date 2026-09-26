"""User-triggered update controls for the native desktop bridge."""

UPDATES_SCRIPT = r"""
const updateBridge = window.agentSwitchUpdater;
let updateState = null, updateRevision = 0;
const updateRequests = new Set();
const updateStatuses = new Set(["unsupported", "idle", "checking", "available", "not-available", "downloading", "cancelling", "downloaded", "installing", "error"]);
const manualUpdateStatuses = new Set(["idle", "checking", "available", "not-available", "error"]);

function updateButton(action) {
  return $(action === "viewRelease" ? "update-view-release" : "update-" + action);
}

function renderUpdateControls() {
  const status = updateState?.status;
  const mode = updateState?.mode;
  for (const [action, allowed] of Object.entries({
    check: ["idle", "available", "not-available", "error"],
    viewRelease: ["available"],
    download: ["available"], cancel: ["downloading"], install: ["downloaded"],
  })) {
    const button = updateButton(action);
    const supportedMode = action === "check" ? ["manual", "install"].includes(mode)
      : action === "viewRelease" ? mode === "manual" : mode === "install";
    const pending = updateRequests.size > 0 && !(action === "cancel" && updateRequests.size === 1 && updateRequests.has("download"));
    button.hidden = action !== "check" && (!supportedMode || !allowed.includes(status));
    block(button, !updateState?.supported || !supportedMode || !allowed.includes(status) || pending);
  }
}

function receiveUpdate(value) {
  ++updateRevision;
  if (value?.schemaVersion !== 1 || typeof value.supported !== "boolean" || !updateStatuses.has(value.status)
    || !["manual", "install", "unsupported"].includes(value.mode)
    || (value.mode === "manual" && !manualUpdateStatuses.has(value.status))) {
    updateState = {supported: false, mode: "unsupported", status: "unsupported", message: "Update information is unavailable. Reopen the app to try again."};
  } else {
    updateState = value;
  }
  const versions = [];
  if (typeof updateState.currentVersion === "string") versions.push("Installed version " + updateState.currentVersion);
  if (typeof updateState.availableVersion === "string") versions.push("Available version " + updateState.availableVersion);
  $("update-version").textContent = versions.join(" · ");
  $("update-kind").textContent = {manual: "Community release · manual updates", install: "Verified in-app updates", unsupported: "Manual downloads available"}[updateState.mode];
  $("update-policy").textContent = updateState.mode === "manual"
    ? "Check for a newer stable release, then choose View release to open GitHub in your browser. Download and install it yourself; this app never downloads or installs community updates. Your saved accounts stay in their existing private storage."
    : updateState.mode === "install"
      ? "Updates come from Agent Switch's stable GitHub releases. Downloads start only when you choose them. Installing asks for confirmation and restarts this app; its automatic switching stops. Your provider apps and saved accounts are not updated or removed."
      : "This build does not offer in-app update checks. Release notes and manual downloads are available on GitHub.";
  $("update-status").textContent = safeMessage(updateState.message || "Update information is unavailable.");
  const progress = updateState.progress;
  const downloading = updateState.mode === "install" && updateState.status === "downloading" && typeof progress?.percent === "number" && Number.isFinite(progress.percent);
  $("update-progress").hidden = $("update-transfer").hidden = !downloading;
  if (downloading) {
    const percent = Math.max(0, Math.min(100, progress.percent));
    $("update-progress").value = percent;
    $("update-transfer").textContent = Math.floor(percent) + "% downloaded";
    if (Number.isFinite(progress.transferred) && progress.transferred >= 0 && Number.isFinite(progress.total) && progress.total > 0) {
      $("update-transfer").textContent += ` · ${(progress.transferred / 1000000).toFixed(1)} of ${(progress.total / 1000000).toFixed(1)} MB`;
    }
  }
  renderUpdateControls();
}

async function requestUpdate(action) {
  if (busy || updateButton(action).disabled) return;
  const revision = updateRevision;
  updateRequests.add(action);
  renderUpdateControls();
  try {
    const result = await updateBridge[action]();
    if (revision === updateRevision) receiveUpdate(result);
  } catch {
    if (revision === updateRevision) receiveUpdate({schemaVersion: 1, supported: false, mode: "unsupported", status: "unsupported", currentVersion: updateState?.currentVersion,
      message: "The app's update service couldn't be reached. Reopen the app and check its version before trying again."});
  } finally {
    updateRequests.delete(action);
    renderUpdateControls();
  }
}

async function setupUpdates() {
  if (!updateBridge) return;
  $("app-updates").hidden = false;
  if (!["getState", "check", "viewRelease", "download", "cancel", "install", "onState"].every(name => typeof updateBridge[name] === "function")) {
    receiveUpdate(null);
    return;
  }
  for (const action of ["check", "viewRelease", "download", "cancel", "install"]) updateButton(action).onclick = () => requestUpdate(action);
  const revision = updateRevision;
  try {
    const unsubscribe = updateBridge.onState(receiveUpdate);
    window.addEventListener("pagehide", () => { if (typeof unsubscribe === "function") unsubscribe(); });
    const result = await updateBridge.getState();
    if (revision === updateRevision) receiveUpdate(result);
  } catch {
    if (revision === updateRevision) receiveUpdate(null);
  }
}
"""

"""Analytics presentation only; embedded into the dashboard's single script."""

USAGE_SCRIPT = r"""
let analyticsVersion = 0, analyticsBody = null, analyticsLoading = false, analyticsError = "";
let analyticsProvider = "codex", analyticsAccount = "all", analyticsRange = "30", analyticsTab = "overview";
const knownNumber = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const sumKnown = (values) => {
  const known = values.filter(knownNumber);
  return known.length ? known.reduce((total, value) => total + value, 0) : null;
};
const numberText = (value) => knownNumber(value) ? value.toLocaleString() : "Not reported";
const shortNumber = (value) => {
  if (!knownNumber(value)) return "—";
  if (value >= 1000000000) return (value / 1000000000).toFixed(1) + "B";
  if (value >= 1000000) return (value / 1000000).toFixed(1) + "M";
  if (value >= 1000) return (value / 1000).toFixed(1) + "K";
  return String(value);
};
const dayKey = (value) => {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}/.test(value)) return null;
  const key = value.slice(0, 10), date = new Date(key + "T00:00:00Z");
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === key ? key : null;
};
const dayShift = (key, amount) => new Date(new Date(key + "T00:00:00Z").getTime() + amount * 86400000).toISOString().slice(0, 10);
const timeText = (value) => {
  if (value == null) return "not reported";
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "not reported" : date.toLocaleString();
};

function usageAccounts() {
  const accounts = Array.isArray(analyticsBody?.accounts) ? analyticsBody.accounts : [];
  return analyticsProvider === "claude" || analyticsAccount === "all" ? accounts : accounts.filter((a) => String(a.number) === analyticsAccount);
}

function periodDays(accounts) {
  const reported = accounts.flatMap((a) => (a.daily || []).map((d) => dayKey(d.date))).filter(Boolean).sort();
  const today = new Date().toISOString().slice(0, 10);
  const last = analyticsRange === "all" ? reported.at(-1) : today;
  if (!last) return [];
  const first = analyticsRange === "all" ? reported[0] : dayShift(last, 1 - Number(analyticsRange));
  const maps = accounts.map((a) => new Map((a.daily || []).filter((d) => dayKey(d.date)).map((d) => [dayKey(d.date), d])));
  const days = [];
  for (let date = first; date <= last; date = dayShift(date, 1)) {
    const rows = maps.map((map, i) => {
      const through = dayKey(accounts[i].dataThrough);
      return through && date > through ? null : map.get(date);
    });
    const count = rows.filter((r) => knownNumber(r?.tokens)).length;
    days.push({date, tokens: sumKnown(rows.map((r) => r?.tokens)), messages: sumKnown(rows.map((r) => r?.messages)), sessions: sumKnown(rows.map((r) => r?.sessions)), count, messagesCount: rows.filter((r) => knownNumber(r?.messages)).length, sessionsCount: rows.filter((r) => knownNumber(r?.sessions)).length, total: accounts.length});
  }
  return days;
}

function usageEmpty(title, detail) {
  const host = el("div", "usage-empty");
  host.append(el("div", "pixel-orbit", "· ◇ ·"), el("h3", null, title), el("p", null, detail));
  return host;
}

function usageTable(headers, rows, label) {
  const scroll = el("div", "table-scroll"), table = el("table");
  table.setAttribute("aria-label", label);
  const head = el("thead"), heading = el("tr");
  headers.forEach((name) => { const th = el("th", null, name); th.setAttribute("scope", "col"); heading.appendChild(th); });
  head.appendChild(heading);
  const body = el("tbody");
  rows.forEach((row) => {
    const tr = el("tr");
    row.forEach((value, index) => {
      const cell = el(index === 0 ? "th" : "td");
      if (index === 0) cell.setAttribute("scope", "row");
      if (value && typeof value === "object" && value.tagName) cell.appendChild(value);
      else cell.textContent = value;
      tr.appendChild(cell);
    });
    body.appendChild(tr);
  });
  table.append(head, body); scroll.appendChild(table);
  return scroll;
}

function usageHeading(title, detail) {
  const heading = el("div", "section-heading");
  heading.append(el("h3", null, title), el("span", null, detail));
  return heading;
}

function renderUsageSummary(host, days) {
  const metrics = el("div", "usage-summary");
  const reported = days.filter((d) => knownNumber(d.tokens));
  const values = [
    ["Tokens", sumKnown(days.map((d) => d.tokens)), "Reported in this period", "count"],
    ["Peak day", reported.length ? Math.max(...reported.map((d) => d.tokens)) : null, "Tokens · this period", "count"],
    ["Sessions", sumKnown(days.map((d) => d.sessions)), "Reported in this period", "sessionsCount"],
    ["Messages", sumKnown(days.map((d) => d.messages)), "Reported in this period", "messagesCount"],
  ];
  values.forEach(([label, value, detail, coverage]) => {
    const metric = el("div", "metric"), number = el("strong", null, shortNumber(value));
    number.title = numberText(value);
    number.setAttribute("aria-label", label + ": " + numberText(value));
    metric.append(el("div", "label", label), number, el("small", null, knownNumber(value) ? detail + (days.some((d) => d[coverage] < d.total) ? " · partial" : "") : "Not reported"));
    metrics.appendChild(metric);
  });
  host.appendChild(metrics);
}

function dailyDescription(day) {
  return `${day.date}: ${knownNumber(day.tokens) ? numberText(day.tokens) + " tokens" : "not reported"}${day.count && day.count < day.total ? ` · partial, ${day.count} of ${day.total} accounts` : ""}`;
}

function renderDailyChart(host, days) {
  if (!days.length) return;
  const panel = el("section", "chart-panel");
  panel.appendChild(usageHeading("Daily activity", "Tokens · selected period"));
  const scroll = el("div", "chart-scroll"), chart = el("div", "daily-chart"), tooltip = el("p", "chart-tooltip", "Select a day to see its reported total.");
  chart.style.minWidth = (days.length * 5) + "px";
  chart.setAttribute("aria-label", "Daily token activity. Each bar has a numerical label; the full data table follows.");
  const maximum = Math.max(1, ...days.map((d) => knownNumber(d.tokens) ? d.tokens : 0));
  days.forEach((day) => {
    const column = el("div", "day-column" + (!knownNumber(day.tokens) ? " missing" : day.tokens === 0 ? " zero" : day.count < day.total ? " partial" : ""));
    const bar = el("button", "day-bar");
    bar.type = "button"; bar.style.height = (knownNumber(day.tokens) ? Math.max(.8, day.tokens / maximum * 100) : 100) + "%";
    bar.title = dailyDescription(day); bar.setAttribute("aria-label", bar.title);
    bar.onclick = () => { tooltip.textContent = bar.title; };
    bar.onfocus = bar.onclick;
    column.appendChild(bar); chart.appendChild(column);
  });
  const axis = el("div", "chart-axis");
  axis.append(el("span", null, days[0].date), el("span", null, "Peak " + shortNumber(maximum === 1 && !days.some((d) => d.tokens > 0) ? 0 : maximum)), el("span", null, days.at(-1).date));
  const details = el("details", "data-table");
  details.append(el("summary", null, "View daily numbers"), usageTable(["Day", "Tokens", "Messages", "Sessions", "Coverage"], days.map((d) => [d.date, numberText(d.tokens), numberText(d.messages), numberText(d.sessions), d.count ? `${d.count} / ${d.total} accounts` : "Not reported"]), "Daily activity numerical data"));
  scroll.appendChild(chart); panel.append(scroll, axis, tooltip, details); host.appendChild(panel);
}

function renderHeatmap(host, days) {
  if (!days.length) return;
  const panel = el("section", "chart-panel");
  panel.appendChild(usageHeading("A little progress, day by day", "Mon → Sun · one pixel per day"));
  const scroll = el("div", "heatmap-scroll"), grid = el("div", "heatmap");
  const offset = (new Date(days[0].date + "T00:00:00Z").getUTCDay() + 6) % 7;
  for (let i = 0; i < offset; i++) grid.appendChild(el("span", "heat-spacer"));
  const maximum = Math.max(1, ...days.map((d) => knownNumber(d.tokens) ? d.tokens : 0));
  const detail = el("p", "chart-tooltip", "Gaps mean not reported. An empty solid square means a reported zero.");
  days.forEach((day) => {
    const pixel = el("button", "heat" + (!knownNumber(day.tokens) ? " missing" : day.count < day.total ? " partial" : ""));
    pixel.type = "button";
    pixel.dataset.level = String(!knownNumber(day.tokens) || day.tokens === 0 ? 0 : Math.min(4, Math.ceil(day.tokens / maximum * 4)));
    pixel.title = dailyDescription(day); pixel.setAttribute("aria-label", pixel.title);
    pixel.onclick = () => { detail.textContent = pixel.title; }; pixel.onfocus = pixel.onclick;
    grid.appendChild(pixel);
  });
  const legend = el("div", "chart-legend");
  [["", "Reported activity"], ["gap", "Not reported"], ["partial", "Partial coverage"]].forEach(([style, text]) => {
    const item = el("span"); item.append(el("span", "legend-square " + style), document.createTextNode(text)); legend.appendChild(item);
  });
  scroll.appendChild(grid); panel.append(scroll, legend, detail); host.appendChild(panel);
}

function renderComparison(host, accounts) {
  if (analyticsProvider !== "codex") return;
  const panel = el("section", "comparison");
  panel.appendChild(usageHeading("Account comparison", "Selected period · reported totals"));
  panel.appendChild(usageTable(["Account", "Tokens", "Sessions", "Data through", "Status"], accounts.map((a) => {
    const days = periodDays([a]);
    const label = el("span", null, a.label || "Account " + a.number);
    if (a.error) label.appendChild(el("small", null, safeMessage(a.error)));
    return [label, numberText(sumKnown(days.map((d) => d.tokens))), numberText(sumKnown(days.map((d) => d.sessions))), dayKey(a.dataThrough) || "Not reported", a.stale ? "Cached · stale" : !a.available ? "Unavailable" : "Available"];
  }), "Codex account comparison"));
  host.appendChild(panel);
}

function renderLifetime(host, accounts) {
  const panel = el("section", "comparison");
  panel.appendChild(usageHeading("Beyond this period", "All reported history · not filtered by date"));
  panel.appendChild(usageTable([analyticsProvider === "claude" ? "Scope" : "Account", "Lifetime tokens", "Peak daily tokens", "Current streak", "Longest streak", "Sessions", "Messages", "Threads"], accounts.map((a) => {
    const s = a.summary || {};
    return [analyticsProvider === "claude" ? "This device" : a.label || "Account " + a.number, numberText(s.lifetimeTokens), numberText(s.peakDailyTokens), knownNumber(s.currentStreakDays) ? s.currentStreakDays + " days" : "Not reported", knownNumber(s.longestStreakDays) ? s.longestStreakDays + " days" : "Not reported", numberText(s.totalSessions), numberText(s.totalMessages), numberText(s.totalThreads)];
  }), "All reported history"));
  host.appendChild(panel);
}

function renderModels(host, accounts, days) {
  const period = el("section", "chart-panel");
  period.appendChild(usageHeading("Models in this period", "Reported daily token totals"));
  const selectedDates = new Set(days.map((d) => d.date)), totals = new Map();
  accounts.forEach((a) => (a.dailyModels || []).forEach((day) => {
    const date = dayKey(day.date), through = dayKey(a.dataThrough);
    if (!selectedDates.has(date) || (through && date > through)) return;
    Object.entries(day.tokensByModel || {}).forEach(([name, count]) => {
      if (knownNumber(count)) totals.set(name, (totals.get(name) || 0) + count);
    });
  }));
  if (totals.size) {
    const maximum = Math.max(1, ...totals.values());
    period.appendChild(usageTable(["Model", "Tokens"], [...totals].sort((a, b) => b[1] - a[1]).map(([name, count]) => {
      const amount = el("div", null, numberText(count)), track = el("div", "model-bar"), fill = el("span");
      fill.style.width = count / maximum * 100 + "%"; track.appendChild(fill); amount.appendChild(track);
      return [name, amount];
    }), "Model activity for selected period"));
    period.appendChild(el("p", "hint", "Totals include only reported daily model data; coverage may differ from the daily activity chart."));
  } else period.appendChild(el("p", "hint", "Daily model breakdowns are not reported for this period. No totals are inferred."));
  host.appendChild(period);
  const components = new Map();
  accounts.forEach((a) => (a.models || []).forEach((model) => {
    if (!components.has(model.name)) components.set(model.name, []);
    components.get(model.name).push(model);
  }));
  const panel = el("section", "comparison");
  panel.appendChild(usageHeading("Token components", "All reported history · not filtered by date"));
  panel.appendChild(el("p", "hint", "Input, output, cache read, and cache write components are shown separately as reported. " + (analyticsProvider === "claude" ? "When complete, these four components sum to Claude Code's lifetime token total. Daily token buckets already aggregate them. " : "Daily model totals are used as reported, without adding components again. ") + "Missing values are not zero. Only available reports are included; coverage can differ by model and account."));
  if (components.size) panel.appendChild(usageTable(["Model", "Input", "Output", "Cache read", "Cache write"], [...components].map(([name, rows]) => [name, ...["inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens"].map((key) => {
    const total = sumKnown(rows.map((r) => r[key]));
    return numberText(total) + (knownNumber(total) && rows.some((r) => !knownNumber(r[key])) ? " · partial" : "");
  })]), "Model token components"));
  else panel.appendChild(el("p", "hint", "Token components are not reported by this source."));
  host.appendChild(panel);
  if (analyticsProvider === "codex") accounts.forEach((a) => {
    const insights = a.insights || {};
    if (!knownNumber(insights.fastModePercent) && !insights.topReasoningEffort && !(insights.topInvocations || []).length) return;
    const details = el("section", "comparison");
    details.appendChild(usageHeading("How you work · " + (a.label || "Account " + a.number), "All reported history"));
    details.append(el("p", "hint", `Fast mode: ${knownNumber(insights.fastModePercent) ? insights.fastModePercent + "%" : "Not reported"} · Top reasoning effort: ${insights.topReasoningEffort || "Not reported"}`));
    if ((insights.topInvocations || []).length) details.appendChild(usageTable(["Kind", "Invocation", "Uses"], insights.topInvocations.map((i) => [i.kind || "Not reported", i.name, numberText(i.usageCount)]), "Top local invocations"));
    host.appendChild(details);
  });
}

function renderUsage() {
  $("usage-subtitle").textContent = analyticsProvider === "claude" ? "Claude Code activity · This device" : "Codex activity · saved accounts";
  $("usage-account-field").hidden = analyticsProvider !== "codex";
  $("usage-content").setAttribute("aria-busy", String(analyticsLoading));
  $("usage-refresh").disabled = analyticsLoading;
  $("usage-refresh").textContent = analyticsLoading ? "Loading activity…" : "Refresh activity";
  const host = $("usage-content"); host.replaceChildren();
  if (analyticsLoading) {
    $("usage-context").textContent = "Reading activity. Your account logins are not changed by this view.";
    host.appendChild(usageEmpty("Gathering the details", "Activity is fetched only when you open this view or refresh it. Larger histories can take a moment."));
    return;
  }
  const accounts = usageAccounts();
  if (analyticsError) {
    const error = el("div", "load-error", safeMessage(analyticsError)); error.setAttribute("role", "status"); host.appendChild(error);
  }
  if (!accounts.length) {
    $("usage-context").textContent = "No activity data is available for this selection.";
    host.appendChild(usageEmpty(analyticsError ? "Activity couldn't be loaded" : "A fresh page", analyticsProvider === "claude" ? "Open /usage in Claude Code, then choose Refresh activity here to read its local activity cache. This is device activity, not a saved-account history; we don't reconstruct transcripts." : analyticsError ? "Your saved accounts are unchanged. Try Refresh activity when you're ready." : "Add an existing Codex login in Accounts, then refresh. We only show reported activity, never estimated usage."));
    return;
  }
  const days = periodDays(accounts), complete = days.filter((d) => d.count === d.total).length;
  const available = accounts.filter((a) => a.available).length;
  const through = accounts.map((a) => dayKey(a.dataThrough)).filter(Boolean).sort();
  const coverage = `${available} of ${accounts.length} ${analyticsProvider === "claude" ? "local sources" : "accounts"} available · ${complete} of ${days.length} days with complete token coverage`;
  $("usage-context").textContent = `${analyticsBody.source || "Provider activity"} · Fetched ${timeText(analyticsBody.fetchedAt)}. Data through ${through.length ? through[0] + (through[0] !== through.at(-1) ? "–" + through.at(-1) : "") : "not reported"}. ${coverage}.${available < accounts.length || complete < days.length ? " Partial coverage: totals include reported values only." : ""}${accounts.some((a) => a.stale) ? " Cached data is stale." : ""} Gaps are not zero.`;
  accounts.filter((a) => a.error).forEach((a) => host.appendChild(el("p", "load-error", (analyticsProvider === "claude" ? "This device" : a.label || "Account " + a.number) + ": " + safeMessage(a.error))));
  if (analyticsProvider === "claude" && accounts.some((a) => a.stale || !a.available)) host.appendChild(el("p", "hint", "Open /usage in Claude Code, then choose Refresh activity here. This view reads the local activity cache; it doesn't reconstruct transcripts or attribute activity to saved accounts."));
  if (analyticsTab === "models") renderModels(host, accounts, days);
  else {
    renderUsageSummary(host, days);
    if (!days.some((d) => knownNumber(d.tokens))) host.appendChild(usageEmpty("No reported tokens in this period", "Try a wider date range. Missing days are left as gaps, not filled with zeroes."));
    renderDailyChart(host, days); renderHeatmap(host, days); renderComparison(host, accounts); renderLifetime(host, accounts);
  }
}

async function loadAnalytics(force = false) {
  if (activeView !== "usage") return;
  const version = ++analyticsVersion, provider = analyticsProvider;
  analyticsLoading = true; analyticsError = ""; renderUsage();
  try {
    const {ok, body} = await api(`/api/analytics?provider=${provider}&force=${force ? "1" : "0"}`);
    if (version !== analyticsVersion || provider !== analyticsProvider || activeView !== "usage") return;
    if (!ok || body.provider !== provider) {
      analyticsBody = null; analyticsError = body.message || body.error || "The activity source is unavailable.";
    } else {
      analyticsBody = body; analyticsError = body.error || "";
      const select = $("usage-account"); select.replaceChildren();
      const all = el("option", null, "All accounts"); all.value = "all"; select.appendChild(all);
      (body.accounts || []).forEach((a) => {
        if (a.number == null) return;
        const option = el("option", null, a.label || "Account " + a.number); option.value = String(a.number); select.appendChild(option);
      });
      if (!(body.accounts || []).some((a) => String(a.number) === analyticsAccount)) analyticsAccount = "all";
      select.value = analyticsAccount;
    }
  } catch {
    if (version !== analyticsVersion || activeView !== "usage") return;
    analyticsBody = null; analyticsError = "Could not reach the local activity service. Try Refresh activity.";
  } finally {
    if (version === analyticsVersion && activeView === "usage") { analyticsLoading = false; renderUsage(); }
  }
}

$("usage-provider").onchange = () => {
  analyticsProvider = $("usage-provider").value === "claude" ? "claude" : "codex";
  analyticsAccount = "all"; analyticsBody = null; loadAnalytics();
};
$("usage-account").onchange = () => { analyticsAccount = $("usage-account").value; renderUsage(); };
$("usage-range").value = "30";
$("usage-range").onchange = () => { analyticsRange = ["7", "30", "90", "all"].includes($("usage-range").value) ? $("usage-range").value : "30"; renderUsage(); };
$("usage-refresh").onclick = () => loadAnalytics(true);
for (const tab of ["overview", "models"]) $("usage-" + tab).onclick = () => {
  analyticsTab = tab;
  for (const other of ["overview", "models"]) $("usage-" + other).setAttribute("aria-pressed", String(other === tab));
  renderUsage();
};
"""

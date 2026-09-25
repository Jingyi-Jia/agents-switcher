"""Offline dashboard styles, inlined by page.py."""

PAGE_STYLE = r"""
:root {
  color-scheme: light;
  --canvas:#edf1f7; --panel:#f5f7fb; --paper:#ffffff; --ink:#20283a;
  --ink-soft:#36415a; --mid:#667188; --hairline:#dce3ee; --track:#e8edf5;
  --accent:#007a8b; --accent-soft:#e1f6f8; --violet:#7759c2; --violet-soft:#f0eafa;
  --active-border:#009da9; --active-glow:#20cfd526;
  --ember:#bb354e; --on-ink:#ffffff; --shadow:0 5px 20px #27365306;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme:dark; --canvas:#14171f; --panel:#1a1e28; --paper:#202531;
    --ink:#ecf0f8; --ink-soft:#c2ccdc; --mid:#9ba8bd; --hairline:#323b4d;
    --track:#323b4d; --accent:#79e5e0; --accent-soft:#203e44;
    --violet:#bcabf4; --violet-soft:#322d47; --ember:#ff91a1; --on-ink:#14171f;
    --active-border:#52f3e3; --active-glow:#39e9df28;
    --shadow:0 6px 24px #080b101a;
  }
}
:root[data-theme="dark"] {
  color-scheme:dark; --canvas:#14171f; --panel:#1a1e28; --paper:#202531;
  --ink:#ecf0f8; --ink-soft:#c2ccdc; --mid:#9ba8bd; --hairline:#323b4d;
  --track:#323b4d; --accent:#79e5e0; --accent-soft:#203e44;
  --violet:#bcabf4; --violet-soft:#322d47; --ember:#ff91a1; --on-ink:#14171f;
  --active-border:#52f3e3; --active-glow:#39e9df28;
  --shadow:0 6px 24px #080b101a;
}
* { box-sizing:border-box; }
[hidden] { display:none !important; }
body { margin:0; background:var(--canvas); color:var(--ink); font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
button,input,select { font:inherit; }
button,a,input,select,summary { -webkit-tap-highlight-color:transparent; }
button { min-height:36px; padding:7px 13px; border:1px solid var(--hairline); border-radius:7px; background:var(--paper); color:var(--ink); cursor:pointer; font-weight:550; font-size:12px; }
button:hover:not(:disabled) { border-color:var(--accent); background:var(--accent-soft); }
button:disabled { opacity:.48; cursor:not-allowed; }
button.primary { background:var(--accent); color:var(--canvas); border-color:var(--accent); }
button.primary:hover:not(:disabled) { background:var(--accent); filter:brightness(1.08); }
button.danger { color:var(--ember); }
button.primary.danger,button.primary.danger:hover:not(:disabled) { background:var(--ember); border-color:var(--ember); color:var(--canvas); }
button.ghost { border-color:transparent; background:var(--accent-soft); color:var(--accent); }
button.ghost:disabled { opacity:1; }
:focus-visible { outline:2px solid var(--accent); outline-offset:4px; }
input,select { color:var(--ink); background:var(--paper); border:1px solid var(--hairline); border-radius:6px; min-height:38px; padding:7px 10px; max-width:100%; }
input[type="checkbox"] { min-height:auto; accent-color:var(--accent); }
a { color:var(--accent); text-underline-offset:3px; }
summary { cursor:pointer; color:var(--mid); }
h1,h2,h3,p { margin-top:0; }
h1,h2,h3 { line-height:1.25; }
.shell { display:grid; grid-template-columns:204px minmax(0,1fr); min-height:100vh; }
.sidebar { position:sticky; top:0; height:100vh; display:flex; flex-direction:column; border-right:1px solid var(--hairline); padding:30px 20px; background:var(--panel); }
.brand { display:flex; align-items:center; gap:11px; margin:0 0 38px; }
.brand-mark { width:32px; height:32px; flex:none; color:var(--accent); }
.brand h1 { margin:0; font-size:15px; letter-spacing:-.03em; }
.brand small { display:block; font-size:10px; font-weight:500; letter-spacing:.1em; text-transform:uppercase; color:var(--mid); margin-top:3px; }
.nav { display:grid; gap:6px; }
.nav button { text-align:left; padding:11px 13px; border-color:transparent; background:transparent; color:var(--mid); font-size:13px; }
.nav button[aria-current="page"] { color:var(--accent); background:var(--accent-soft); box-shadow:inset 3px 0 var(--accent); }
.nav-symbol { display:inline-block; width:24px; font-size:17px; vertical-align:-1px; }
.sidebar-foot { margin-top:auto; color:var(--mid); font-size:11px; line-height:1.8; padding:32px 9px 0; }
.local-dot { display:inline-block; width:6px; height:6px; background:var(--accent); margin-right:7px; }
.wrap { max-width:1440px; width:100%; margin:0 auto; padding:40px clamp(20px,4vw,58px) 36px; min-width:0; }
.page-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:20px; margin-bottom:28px; }
.eyebrow,.label { font-size:10px; font-weight:650; letter-spacing:.1em; text-transform:uppercase; color:var(--mid); }
.eyebrow { color:var(--accent); margin-bottom:9px; }
.page-heading h2 { font-size:30px; font-weight:620; letter-spacing:-.045em; margin:0 0 7px; }
.page-heading p { color:var(--mid); margin:0; font-size:13px; }
.stamp { color:var(--mid); font-size:11px; white-space:nowrap; padding-top:9px; font-variant-numeric:tabular-nums; }
.kpis { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; margin-bottom:28px; }
.tile { display:grid; grid-template-columns:1fr auto; gap:6px 12px; border:1px solid var(--hairline); padding:16px 18px; border-radius:10px; background:var(--panel); min-width:0; }
.tile .label { grid-column:1/-1; }
.tile .identity { font-size:14px; font-weight:600; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.value { font-size:14px; font-weight:600; font-variant-numeric:tabular-nums; }
.value small { font-size:11px; color:var(--mid); margin-left:4px; font-weight:400; }
.value.ember { color:var(--ember); }
.sub { font-size:11px; color:var(--mid); grid-column:1/-1; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.providers { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); align-items:start; gap:24px; }
.provider,#claude-group { min-width:0; }
.provider > h2 { font-size:17px; font-weight:620; letter-spacing:-.02em; margin:0; display:flex; align-items:center; gap:9px; }
.provider > h2::before { content:""; width:8px; height:8px; background:var(--accent); flex:none; }
#claude-heading::before { background:var(--violet); }
.provider > h2 span { font-size:10px; font-weight:500; color:var(--mid); letter-spacing:0; margin-left:auto; }
.notice,.hint { font-size:11px; color:var(--mid); margin:10px 0; overflow-wrap:anywhere; }
.notice { line-height:1.65; }
.actions { display:flex; align-items:center; gap:7px; flex-wrap:wrap; margin:13px 0; }
.provider > .actions { margin-bottom:17px; }
.provider > .actions button { background:transparent; font-size:11px; padding:5px 9px; min-height:31px; }
.provider > .actions button:first-child { background:var(--paper); }
.card { border:1px solid var(--hairline); border-radius:10px; padding:18px; background:var(--paper); box-shadow:var(--shadow); margin-bottom:10px; position:relative; }
.card.is-active { border-color:var(--active-border); box-shadow:0 0 0 1px var(--active-border),0 0 20px var(--active-glow),var(--shadow); }
.top { display:flex; align-items:center; gap:10px; min-width:0; }
.avatar { width:34px; height:34px; flex:none; display:grid; place-items:center; background:var(--accent-soft); color:var(--accent); border-radius:4px; font:650 14px ui-monospace,monospace; }
.avatar.claude { background:var(--violet-soft); color:var(--violet); }
.identity-stack { min-width:0; flex:1; }
.name { display:block; font-size:13px; font-weight:620; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.account-meta { color:var(--mid); font-size:10px; margin-top:3px; overflow-wrap:anywhere; }
.grow { flex:1; }
.slot { font-variant-numeric:tabular-nums; }
.pill { display:inline-flex; align-items:center; flex:none; white-space:nowrap; font-size:10px; font-weight:550; padding:2px 6px; border:1px solid var(--hairline); border-radius:4px; color:var(--mid); }
.pill.solid { color:var(--violet); background:var(--violet-soft); border-color:transparent; }
.pill.soft { color:var(--accent); background:var(--accent-soft); border-color:transparent; }
.meters { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 18px; margin:18px 0 0; }
.meter { display:grid; grid-template-columns:auto 1fr; align-items:center; gap:6px; min-width:0; }
.meter .label { font-size:9px; letter-spacing:.03em; }
.num { text-align:right; font-size:10px; color:var(--mid); font-variant-numeric:tabular-nums; }
.num.low { color:var(--ink); font-weight:650; }
.num.out,.state.out { color:var(--ember); }
.quota-reset,.quota-pace { grid-column:1/-1; font-size:10px; color:var(--mid); line-height:1.6; overflow-wrap:anywhere; }
.quota-reset { margin-top:3px; }
.quota-reset time { color:var(--ink-soft); }
.quota-pace { width:fit-content; border-bottom:1px dotted var(--hairline); cursor:help; }
.reset-passed { display:block; }
.track { grid-column:1/-1; height:4px; background:var(--track); overflow:hidden; border-radius:1px; }
.fill { height:100%; background:var(--accent); }
#claude .fill { background:var(--violet); }
.meter.unknown .track { background:repeating-linear-gradient(90deg,var(--track) 0 3px,transparent 3px 6px); }
.state { display:flex; align-items:baseline; gap:6px; font-size:11px; color:var(--mid); margin-top:13px; overflow-wrap:anywhere; }
.glyph { font-size:7px; }
.account-footer { display:flex; justify-content:space-between; align-items:center; gap:8px; border-top:1px solid var(--hairline); margin-top:16px; padding-top:12px; }
.account-footer button { min-height:30px; padding:4px 11px; }
.account-overflow { position:relative; }
.account-overflow > summary { list-style:none; padding:3px 9px; font-size:18px; line-height:1; border:1px solid transparent; border-radius:4px; }
.account-overflow > summary::-webkit-details-marker { display:none; }
.account-overflow[open] > summary { background:var(--panel); border-color:var(--hairline); }
.account-overflow .actions { position:absolute; right:0; z-index:3; top:20px; display:grid; min-width:200px; background:var(--paper); border:1px solid var(--hairline); border-radius:8px; padding:7px; box-shadow:0 6px 24px #0002; }
.account-overflow button { text-align:left; border-color:transparent; }
.auto-panel { border-top:1px solid var(--hairline); padding-top:16px; margin-top:20px; }
.auto-panel > summary { display:flex; align-items:center; gap:10px; font-size:12px; color:var(--ink-soft); }
.auto-panel > summary::before { content:"+"; font:16px ui-monospace,monospace; color:var(--mid); }
.auto-panel[open] > summary::before { content:"−"; }
.auto-heading { display:flex; align-items:center; gap:9px; }
.auto-heading h3 { font-size:12px; margin:0; font-weight:550; }
.threshold-field { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.threshold-field input { width:80px; }
.auto-purpose { font-size:12px; color:var(--ink-soft); margin:14px 0 10px; }
.auto-lifecycle { line-height:1.65; margin-bottom:14px; }
.auto-actions button:first-child { min-width:86px; }
.auto-preview { border-top:1px solid var(--hairline); padding-top:10px; margin-top:14px; }
.auto-preview > summary { font-size:11px; }
.auto-preview .hint { margin-bottom:10px; }
.events { max-height:160px; overflow-y:auto; padding-left:22px; font-size:11px; color:var(--mid); overflow-wrap:anywhere; }
.events li { padding:4px 0; }
.auto-error { color:var(--ember); font-size:12px; }
.adopt,.empty { border:1px dashed var(--hairline); border-radius:10px; padding:24px 18px; color:var(--mid); background:var(--panel); font-size:12px; overflow-wrap:anywhere; }
.empty::before { content:"◇"; display:block; color:var(--violet); font-size:27px; margin-bottom:9px; }
.empty strong { display:block; color:var(--ink); font-size:14px; margin-bottom:6px; }
.adopt { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.adopt b { color:var(--ink); font-weight:500; }
code { font:11px ui-monospace,monospace; color:var(--ink); padding:2px 5px; background:var(--canvas); border-radius:3px; }
#claude-desktop-panel { margin-top:30px; padding-top:24px; border-top:1px solid var(--hairline); }
#claude-desktop-panel h2::before { background:var(--violet); }
.profile-about { font-size:11px; color:var(--mid); margin-bottom:15px; }
.profile-about summary { color:var(--violet); font-size:11px; }
.profile-about p { margin:10px 0; }
#claude-desktop-status.launch-blocked { padding:11px 13px; border-left:2px solid var(--violet); background:var(--violet-soft); color:var(--ink-soft); }
.profile-card { padding:14px 16px; }
.profile-details { flex:1; min-width:0; text-align:left; border:0; padding:3px 0; background:transparent; }
.profile-details:hover:not(:disabled) { background:transparent; }
.profile-details:hover:not(:disabled) .name { text-decoration:underline; text-underline-offset:3px; text-decoration-color:var(--violet); }
.profile-details .account-meta { display:block; font-weight:400; }
.profile-create { display:flex; align-items:center; gap:10px; width:100%; min-height:48px; padding:12px 16px; border-style:dashed; border-radius:10px; background:transparent; color:var(--mid); text-align:left; }
.profile-create::before { content:"+"; color:var(--violet); font:20px/1 ui-monospace,monospace; }
.profile-danger { margin-top:24px; padding-top:17px; border-top:1px solid var(--hairline); }
.profile-danger .hint { margin-top:0; }
.settings-panel,.chart-panel,.metric,.comparison,.guide { background:var(--paper); border:1px solid var(--hairline); border-radius:10px; box-shadow:var(--shadow); }
.settings-panel { max-width:780px; padding:24px; margin-bottom:16px; }
.settings-panel h3 { font-size:15px; margin-bottom:5px; }
.settings-panel p { font-size:12px; color:var(--mid); }
.setting-row { display:flex; gap:20px; align-items:center; justify-content:space-between; padding:18px 0; }
.setting-row + .setting-row { border-top:1px solid var(--hairline); }
.setting-row label { font-size:13px; font-weight:550; }
.setting-row .hint { margin:5px 0 0; font-weight:400; }
.guide { padding:23px; margin-bottom:24px; }
.guide-heading { display:flex; align-items:start; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.guide h2 { margin:6px 0 14px; font-size:20px; letter-spacing:-.03em; }
.guide h3 { margin:0; font-size:14px; }
.guide p,.guide li { font-size:12px; overflow-wrap:anywhere; }
.guide-intro,.guide-boundary { color:var(--mid); }
.guide-providers { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(240px,100%),1fr)); gap:14px; }
.guide-provider { background:var(--panel); padding:16px; border-radius:7px; min-width:0; }
.guide-provider .pill { margin-top:10px; }
.guide-provider .actions { margin-bottom:0; }
.guide a { font-size:11px; }
.guide-steps { padding-left:20px; }
.guide-steps li { padding:5px 0; }
.guide-footer { display:flex; align-items:center; flex-wrap:wrap; gap:12px; }
.usage-toolbar { display:flex; gap:12px; flex-wrap:wrap; align-items:end; margin-bottom:20px; }
.usage-toolbar label { display:grid; gap:5px; font-size:10px; color:var(--mid); }
.usage-toolbar select { min-width:130px; font-size:12px; }
.usage-toolbar #usage-account { max-width:240px; }
.usage-toolbar .push { margin-left:auto; }
.segmented { display:flex; gap:3px; border-bottom:1px solid var(--hairline); margin-bottom:20px; }
.segmented button { border:0; background:transparent; border-radius:0; padding:10px 18px; color:var(--mid); }
.segmented button[aria-pressed="true"] { color:var(--accent); box-shadow:inset 0 -2px var(--accent); }
.usage-context { margin:0 0 20px; font-size:11px; color:var(--mid); line-height:1.8; }
.usage-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:18px; }
.metric { padding:18px; }
.metric strong { display:block; font-size:26px; font-weight:600; letter-spacing:-.045em; margin:8px 0 2px; font-variant-numeric:tabular-nums; }
.metric small { color:var(--mid); font-size:10px; }
.chart-panel { padding:22px; margin-bottom:18px; min-width:0; }
.section-heading { display:flex; gap:12px; justify-content:space-between; align-items:baseline; margin-bottom:18px; }
.section-heading h3 { margin:0; font-size:14px; font-weight:600; letter-spacing:-.015em; }
.section-heading span { font-size:10px; color:var(--mid); }
.daily-chart { display:flex; align-items:stretch; gap:4px; height:182px; padding-top:12px; border-bottom:1px solid var(--hairline); }
.day-column { flex:1; min-width:3px; display:flex; align-items:end; position:relative; }
.day-bar { width:100%; min-width:0; min-height:2px; border:0; padding:0; border-radius:2px 2px 0 0; background:var(--accent); opacity:.8; }
.day-bar:hover:not(:disabled),.day-bar:focus-visible { opacity:1; background:var(--accent); }
.day-column.partial .day-bar { background:var(--violet); }
.day-column.missing { background:repeating-linear-gradient(135deg,transparent 0 4px,var(--track) 4px 5px); }
.day-column.missing .day-bar { background:transparent; height:100% !important; }
.day-column.zero .day-bar { background:var(--mid); }
.chart-axis { display:flex; justify-content:space-between; color:var(--mid); font-size:10px; margin-top:8px; }
.chart-tooltip { min-height:18px; font-size:11px; color:var(--ink-soft); margin:10px 0 0; }
.heatmap-scroll,.table-scroll { overflow-x:auto; }
.chart-scroll { overflow-x:auto; }
.heatmap { display:grid; grid-auto-flow:column; grid-template-rows:repeat(7,11px); grid-auto-columns:11px; gap:4px; width:max-content; margin:8px 0 16px; }
.heat { min-height:0; width:11px; height:11px; padding:0; border-radius:1px; border:1px solid var(--hairline); background:var(--track); }
.heat[data-level="1"] { background:var(--accent); opacity:.25; }
.heat[data-level="2"] { background:var(--accent); opacity:.48; }
.heat[data-level="3"] { background:var(--accent); opacity:.72; }
.heat[data-level="4"] { background:var(--accent); opacity:1; }
.heat.missing { background:transparent; border-style:dashed; opacity:.8; }
.heat.partial { border-color:var(--violet); box-shadow:0 0 0 1px var(--violet); }
.heat-spacer { width:11px; height:11px; }
.chart-legend { display:flex; align-items:center; flex-wrap:wrap; gap:14px; color:var(--mid); font-size:10px; }
.legend-square { display:inline-block; height:8px; width:8px; background:var(--accent); margin-right:5px; }
.legend-square.gap { background:transparent; border:1px dashed var(--mid); }
.legend-square.partial { background:var(--violet); }
.comparison { padding:22px; margin-bottom:18px; min-width:0; }
table { width:100%; border-collapse:collapse; text-align:left; font-size:11px; white-space:nowrap; }
th { color:var(--mid); font-weight:500; font-size:10px; }
th,td { padding:11px 12px; border-bottom:1px solid var(--hairline); }
th:first-child,td:first-child { padding-left:0; }
td { font-variant-numeric:tabular-nums; }
td small { display:block; font-size:10px; color:var(--mid); }
.data-table summary { font-size:11px; margin:16px 0 6px; }
.model-bar { width:100%; height:4px; background:var(--track); margin-top:6px; }
.model-bar span { display:block; height:100%; background:var(--violet); }
.usage-empty { padding:52px 28px; text-align:center; border:1px dashed var(--hairline); border-radius:10px; background:var(--panel); }
.usage-empty .pixel-orbit { font:36px ui-monospace,monospace; color:var(--violet); letter-spacing:8px; margin-bottom:15px; }
.usage-empty h3 { font-size:18px; letter-spacing:-.025em; margin-bottom:10px; }
.usage-empty p { color:var(--mid); font-size:12px; max-width:460px; margin:0 auto; }
.load-error { color:var(--ember); border-left:2px solid var(--ember); padding:12px 15px; background:var(--paper); margin-bottom:18px; font-size:12px; overflow-wrap:anywhere; }
dialog { width:min(480px,calc(100% - 32px)); max-height:calc(100% - 32px); overflow:auto; padding:28px; background:var(--paper); color:var(--ink); border:1px solid var(--hairline); border-radius:12px; box-shadow:0 24px 100px #0006; }
dialog::backdrop { background:#090e1db3; }
dialog h2 { font-size:21px; font-weight:600; letter-spacing:-.03em; margin:7px 0 13px; overflow-wrap:anywhere; }
dialog p { font-size:12px; color:var(--mid); overflow-wrap:anywhere; }
.field { display:grid; gap:7px; margin:17px 0; font-size:12px; }
.check { display:flex; gap:9px; align-items:start; font-size:12px; margin:18px 0; }
.check input { margin-top:4px; flex:none; }
.dialog-actions { justify-content:flex-end; margin-bottom:0; }
#dialog-feedback,.dialog-error { color:var(--ember); }
.switch-status { padding:14px; border:1px solid var(--hairline); border-radius:7px; background:var(--panel); min-height:60px; font-size:12px; }
.switch-steps { display:flex; gap:6px; margin:22px 0; color:var(--mid); font-size:10px; }
.switch-steps span { flex:1; padding-top:8px; border-top:2px solid var(--track); }
.switch-steps span.current { color:var(--accent); border-color:var(--accent); }
#toast { position:fixed; bottom:22px; left:calc(50% + 102px); transform:translateX(-50%); width:max-content; max-width:min(670px,calc(100% - 36px)); z-index:8; display:none; padding:13px 19px; border:1px solid var(--accent); border-radius:8px; background:var(--paper); color:var(--ink); box-shadow:0 8px 35px #0003; font-size:12px; overflow-wrap:anywhere; }
#toast.show { display:flex; align-items:center; gap:14px; }
#toast .dismiss-toast { min-width:30px; min-height:30px; padding:0; flex:none; border-color:transparent; color:inherit; background:transparent; }
#toast .dismiss-toast::before { content:"×"; font-size:21px; }
#toast.ember { border-color:var(--ember); color:var(--ember); }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; }
@media (max-width:1180px) { .shell { grid-template-columns:174px minmax(0,1fr); } .sidebar { padding-inline:15px; } .wrap { padding-inline:25px; } .providers { gap:18px; } .top { gap:8px; } .card { padding:15px; } #toast { left:calc(50% + 87px); } }
@media (max-width:960px) { .providers { grid-template-columns:1fr; gap:28px; } .usage-summary { grid-template-columns:repeat(2,minmax(0,1fr)); } .wrap { max-width:860px; } }
@media (max-width:640px) { .shell { display:block; } .sidebar { position:relative; height:auto; padding:17px 18px 0; border-right:0; border-bottom:1px solid var(--hairline); } .brand { margin-bottom:15px; } .brand small { display:none; } .brand-mark { width:26px; height:26px; } .nav { display:flex; gap:8px; } .nav button { flex:1; text-align:center; font-size:12px; border-radius:6px 6px 0 0; padding:10px 4px; } .nav button[aria-current="page"] { box-shadow:inset 0 -2px var(--accent); } .nav-symbol { width:21px; font-size:15px; } .sidebar-foot { display:none; } .wrap { padding:25px 18px; } .page-heading { gap:10px; margin-bottom:22px; } .page-heading h2 { font-size:27px; } .page-heading .stamp { display:none; } .kpis { gap:10px; } .tile { padding:13px; display:block; } .tile .identity { margin:6px 0 3px; font-size:12px; } .tile .sub { margin-top:3px; } .value { font-size:12px; } .chart-panel,.comparison,.settings-panel { padding:17px; } .usage-toolbar { gap:9px; } .usage-toolbar label { flex:1; min-width:0; } .usage-toolbar select { min-width:0; width:100%; } .usage-toolbar #usage-account { max-width:none; } .usage-toolbar .push { margin-left:0; } .metric { padding:14px; } .metric strong { font-size:24px; } .daily-chart { gap:2px; height:155px; } .setting-row { gap:12px; } #toast { left:50%; bottom:14px; } }
@media (max-width:640px) { .usage-toolbar { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); } .usage-toolbar .push { width:100%; min-height:38px; } }
@media (prefers-reduced-motion: no-preference) { .meter .fill { transition:width .3s ease; } button { transition:background .12s ease,border-color .12s ease; } }
"""

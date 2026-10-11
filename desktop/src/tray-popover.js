'use strict';

const overview = document.getElementById('overview');
const bridge = window.agentSwitchTray;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function render(model) {
  if (['light', 'dark'].includes(model.theme)) document.documentElement.dataset.theme = model.theme;
  else delete document.documentElement.dataset.theme;
  const scroll = overview.scrollTop;
  const nodes = [];
  if (model.status !== 'ready') {
    const notice = element('p', 'notice', model.status === 'loading' ? 'Loading account usage…' : 'Account usage unavailable. Open the app to reconnect.');
    notice.setAttribute('role', 'status');
    nodes.push(notice);
  }
  for (const provider of model.providers) {
    const section = element('section', provider.id);
    const heading = element('h2', '', provider.name);
    if (provider.available) heading.append(element('span', 'count', `${provider.accounts.length} saved`));
    section.append(heading);
    if (!provider.available || !provider.accounts.length) {
      section.append(element('p', 'notice', provider.available ? 'No saved accounts' : 'Provider unavailable'));
    }
    for (const account of provider.accounts) {
      const card = element('article', `${account.active ? 'active ' : ''}${account.status === 'last-good' ? 'last-good' : ''}`);
      card.append(element('h3', '', account.name));
      const badges = element('div', 'badges');
      for (const [show, style, text] of [[account.active, 'active', 'Active'], [account.excluded, '', 'Excluded from auto'], [account.pending, 'pending', 'Saved login pending']]) {
        if (show) badges.append(element('span', `badge ${style}`, text));
      }
      if (badges.childNodes.length) card.append(badges);
      const messages = { login: 'Sign-in required', unavailable: 'Usage unavailable', credits: 'On paid credits · not subscription availability', metered: 'API key · metered usage', failed: 'Update failed · no quota report available', 'last-good': 'Update failed · last successful report' };
      if (messages[account.status]) card.append(element('p', `notice ${['login', 'unavailable', 'failed', 'last-good'].includes(account.status) ? 'warning' : ''}`, messages[account.status]));
      if (['ready', 'last-good'].includes(account.status) && !account.windows.length) card.append(element('p', 'notice', 'Quota unknown'));
      const meters = element('div', 'quotas');
      for (const quota of account.windows) {
        const row = element('div', `quota${quota.model ? ' model' : ''}${quota.remaining !== null && quota.remaining <= 20 ? ' low' : ''}`);
        const top = element('div', 'quota-top');
        top.append(element('span', 'window-name', `${quota.name}${quota.model ? ' · model limit' : ''}`));
        const percentage = element('span', 'percentage', quota.remaining === null ? 'Unknown' : `${Math.round(quota.remaining)}%`);
        if (quota.remaining !== null) percentage.append(element('small', '', ' left'));
        top.append(percentage);
        row.append(top);
        if (quota.remaining !== null) {
          const bar = element('progress');
          bar.max = 100;
          bar.value = quota.remaining;
          bar.setAttribute('aria-label', `${quota.name}${quota.model ? ' model limit' : ''}: ${quota.remaining}% remaining${account.status === 'last-good' ? ', last successful report' : ''}`);
          row.append(bar);
        }
        row.append(element('p', 'reset', quota.reset || 'Reset time unknown'));
        meters.append(row);
      }
      if (meters.childNodes.length) card.append(meters);
      section.append(card);
    }
    nodes.push(section);
  }
  overview.replaceChildren(...nodes);
  overview.scrollTop = scroll;
}

bridge?.onModel(render);
for (const button of document.querySelectorAll('[data-view]')) button.addEventListener('click', () => bridge?.navigate(button.dataset.view));
document.getElementById('close').addEventListener('click', () => bridge?.navigate('dismiss'));
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') { event.preventDefault(); bridge?.navigate('dismiss'); }
});
overview.focus();

'use strict';

const http = require('node:http');

function label(value, fallback) {
  return typeof value === 'string' && value.trim()
    ? value.replace(/[\x00-\x1f\x7f]/g, ' ').slice(0, 320) : fallback;
}

function remaining(value) {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? `${Math.round(Math.max(0, 100 - value))}% left` : 'Quota unknown';
}

function resetLabel(window, now) {
  const { resetAt, observedAt, windowSeconds } = window;
  if (![resetAt, observedAt, windowSeconds].every(value => typeof value === 'number' && Number.isFinite(value))
    || observedAt <= 0 || observedAt > now || windowSeconds <= 0 || resetAt <= now
    || resetAt - observedAt > windowSeconds) return '';
  const minutes = Math.ceil((resetAt - now) / 60);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  const duration = days ? `${days}d ${hours % 24}h` : hours ? `${hours}h ${minutes % 60}m` : `${minutes}m`;
  return ` · resets ${duration}`;
}

function usageItems(state, now = Date.now() / 1000) {
  if (!state) return [{ label: 'Loading account usage…', enabled: false }];
  const items = [{ label: 'Account quota · remaining', enabled: false }];
  for (const [key, name] of [['claude', 'Claude Code'], ['codex', 'Codex']]) {
    const provider = state[key];
    items.push({ type: 'separator' }, { label: name, enabled: false });
    if (!provider?.available) {
      items.push({ label: '  Unavailable', enabled: false });
      continue;
    }
    const accounts = Array.isArray(provider.accounts) ? provider.accounts : [];
    if (!accounts.length) items.push({ label: '  No saved accounts', enabled: false });
    for (const account of accounts) {
      const status = [account.active ? 'active' : '', account.disabled ? 'excluded from auto' : '',
        account.activationRequired ? 'saved login pending' : ''].filter(Boolean).join(' · ');
      items.push({ label: `  ${label(account.alias, label(account.email, `Account ${account.number}`))}${status ? ` · ${status}` : ''}`, enabled: false });
      if (account.error || account.loginRequired) {
        items.push({ label: account.loginRequired ? '    Sign-in required' : '    Usage unavailable', enabled: false });
        continue;
      }
      if (account.sentinel === 'api key' || account.onCredits) {
        items.push({ label: account.onCredits ? '    On paid credits' : '    API key · metered usage', enabled: false });
        continue;
      }
      if (account.usageFailed || account.sentinel) items.push({ label: '    Usage update failed · last report', enabled: false });
      const windows = Array.isArray(account.windows) ? account.windows : [];
      if (!windows.length) items.push({ label: '    Quota unknown', enabled: false });
      for (const window of windows) {
        const scope = window.scope === 'model' ? ' · model limit' : '';
        items.push({ label: `    ${label(window.label, 'Window')}: ${remaining(window.usedPercent)}${scope}${resetLabel(window, now)}`, enabled: false });
      }
    }
  }
  return items;
}

function quotaModel(state, now = Date.now() / 1000, status = 'ready') {
  return {
    status: state ? 'ready' : status === 'unavailable' ? 'unavailable' : 'loading',
    theme: ['light', 'dark'].includes(state?.preferences?.theme) ? state.preferences.theme : 'system',
    providers: state ? [['claude', 'Claude Code'], ['codex', 'Codex']].map(([id, name]) => {
      const provider = state[id];
      const available = provider?.available === true;
      const accounts = available && Array.isArray(provider.accounts) ? provider.accounts : [];
      return { id, name, available, accounts: accounts.filter(account => account && typeof account === 'object').map(account => {
        const hasReport = Array.isArray(account.windows) && account.windows.some(window =>
          typeof window?.usedPercent === 'number' && Number.isFinite(window.usedPercent)
          && window.usedPercent >= 0 && window.usedPercent <= 100);
        const status = account.loginRequired ? 'login' : account.error ? 'unavailable'
          : account.onCredits ? 'credits' : account.sentinel === 'api key' ? 'metered'
            : account.usageFailed || account.sentinel ? hasReport ? 'last-good' : 'failed' : 'ready';
        const showWindows = status === 'ready' || status === 'last-good';
        const number = Number.isSafeInteger(account.number) && account.number > 0 ? String(account.number)
          : typeof account.number === 'string' && /^[1-9]\d{0,8}$/.test(account.number) ? account.number : null;
        return {
          name: label(account.alias, label(account.email, number ? `Account ${number}` : 'Saved account')),
          active: account.active === true,
          excluded: account.disabled === true,
          pending: account.activationRequired === true,
          status,
          windows: showWindows && Array.isArray(account.windows) ? account.windows
            .filter(window => window && typeof window === 'object')
            .map(window => {
              const used = window.usedPercent;
              const left = typeof used === 'number' && Number.isFinite(used) && used >= 0 && used <= 100
                ? Number((100 - used).toFixed(10)) : null;
              return { name: label(window.label, 'Window'), model: window.scope === 'model', remaining: left,
                reset: resetLabel(window, now).replace(/^ · /, '') };
            }) : [],
        };
      }) };
    }) : [],
  };
}

function readState(address, signal) {
  return new Promise((resolve, reject) => {
    const url = new URL(address);
    const token = url.searchParams.get('token');
    if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || url.username || url.password
      || !/^[a-f0-9]{64}$/.test(token || '')) return reject(new Error('Invalid local dashboard'));
    const request = http.get(new URL('/api/state', url.origin), {
      headers: { 'X-Auth-Token': token }, signal,
    }, response => {
      if (response.statusCode !== 200) {
        response.destroy();
        reject(new Error('Local usage unavailable'));
        return;
      }
      const chunks = [];
      let bytes = 0;
      response.on('data', chunk => {
        bytes += chunk.length;
        if (bytes > 1024 * 1024) request.destroy(new Error('Local response too large'));
        else chunks.push(chunk);
      });
      response.on('error', reject);
      response.on('aborted', () => reject(new Error('Local usage interrupted')));
      response.on('end', () => {
        try {
          const state = JSON.parse(Buffer.concat(chunks).toString('utf8'));
          if (!state || typeof state !== 'object' || Array.isArray(state)) throw new Error();
          resolve(state);
        } catch { reject(new Error('Invalid local usage response')); }
      });
    });
    const timer = setTimeout(() => request.destroy(new Error('Local usage timed out')), 30000);
    timer.unref();
    request.once('close', () => clearTimeout(timer));
    request.on('error', reject);
  });
}

class TrayDashboard {
  constructor(render, read = readState) {
    this.render = render;
    this.read = read;
    this.controller = null;
    this.timer = null;
  }

  start(address) {
    this.stop();
    const controller = new AbortController();
    this.controller = controller;
    this.render(usageItems(null), quotaModel(null));
    const poll = async () => {
      let items;
      let model;
      try {
        const state = await this.read(address, controller.signal);
        const now = Date.now() / 1000;
        items = usageItems(state, now);
        model = quotaModel(state, now);
      }
      catch {
        items = [{ label: 'Account usage unavailable · open the app to retry', enabled: false }];
        model = quotaModel(null, undefined, 'unavailable');
      }
      if (controller.signal.aborted) return;
      this.render(items, model);
      this.timer = setTimeout(poll, 20000);
      this.timer.unref();
    };
    void poll();
  }

  stop() {
    this.controller?.abort();
    this.controller = null;
    clearTimeout(this.timer);
    this.timer = null;
  }
}

module.exports = { usageItems, quotaModel, readState, TrayDashboard };

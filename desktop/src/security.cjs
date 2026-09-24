'use strict';

const HELP_LINKS = Object.freeze({
  claude: 'https://code.claude.com/docs/en/setup',
  codex: 'https://developers.openai.com/codex/cli',
  project: 'https://github.com/Jingyi-Jia/agents-switcher',
  releases: 'https://github.com/Jingyi-Jia/agents-switcher/releases',
});

function parseURL(value) {
  try {
    return new URL(value);
  } catch {
    return null;
  }
}

function isOwnedURL(value, origin) {
  const url = parseURL(value);
  return Boolean(url && url.protocol === 'http:' && url.hostname === '127.0.0.1'
    && url.origin === origin && !url.username && !url.password);
}

function isNavigationAllowed(value, origin) {
  return isOwnedURL(value, origin) && new URL(value).pathname === '/';
}

function externalHelpURL(value) {
  const url = parseURL(value);
  if (!url || url.protocol !== 'https:' || url.username || url.password || url.port) return null;
  const scopes = {
    'code.claude.com': '/docs',
    'docs.anthropic.com': '/en/docs/claude-code',
    'developers.openai.com': '/codex',
    'github.com': '/Jingyi-Jia/agents-switcher',
  };
  const prefix = scopes[url.hostname];
  if (!prefix || (url.pathname !== prefix && !url.pathname.startsWith(`${prefix}/`))) return null;
  if (/%|\\/.test(url.pathname)) return null;
  url.search = '';
  url.hash = '';
  return url.href;
}

function dashboardURL(port, token) {
  if (!Number.isInteger(port) || port < 1 || port > 65535
      || typeof token !== 'string' || !/^[a-f0-9]{64}$/.test(token)) {
    throw new Error('Invalid desktop connection');
  }
  const url = new URL(`http://127.0.0.1:${port}/`);
  url.searchParams.set('token', token);
  url.searchParams.set('desktop', '1');
  return url.href;
}

function secureSession(session, origin) {
  session.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  session.setPermissionCheckHandler(() => false);
  session.setDevicePermissionHandler(() => false);
  session.on('will-download', (event) => event.preventDefault());
  session.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    callback({ cancel: !isOwnedURL(details.url, origin) });
  });
}

function secureWebContents(contents, origin, openExternal) {
  const openHelp = (value) => {
    const url = externalHelpURL(value);
    if (url) Promise.resolve(openExternal(url)).catch(() => {});
  };
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.on('will-frame-navigate', (event) => {
    if (event.isMainFrame && isNavigationAllowed(event.url, origin)) return;
    event.preventDefault();
    if (event.isMainFrame) openHelp(event.url);
  });
  contents.on('will-redirect', (event) => {
    if (!event.isMainFrame || !isNavigationAllowed(event.url, origin)) event.preventDefault();
  });
  contents.setWindowOpenHandler(({ url }) => {
    openHelp(url);
    return { action: 'deny' };
  });
}

module.exports = {
  HELP_LINKS, dashboardURL, externalHelpURL, isOwnedURL, isNavigationAllowed,
  secureSession, secureWebContents,
};

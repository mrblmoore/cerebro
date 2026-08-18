import { DEFAULTS, getConfig, setConfig } from './config.js';
import { DETECTORS } from './detectors.js';

const $ = (id) => document.getElementById(id);

async function renderStatus() {
  const pill = $('status');
  const reply = await chrome.runtime.sendMessage({ type: 'PING' }).catch(() => null);
  const online = reply && reply.online;
  pill.className = `pill ${online ? 'ok' : 'err'}`;
  pill.innerHTML = `<span class="dot"></span> ${online ? 'connected' : 'offline'}`;
}

async function renderQueue() {
  const { pendingEvents = [] } = await chrome.storage.local.get('pendingEvents');
  $('queue').textContent = pendingEvents.length
    ? `${pendingEvents.length} event${pendingEvents.length === 1 ? '' : 's'} waiting to be sent — `
      + 'they go out automatically once Cerebro is reachable.'
    : 'Nothing queued. Every event has been delivered.';
}

function renderDetectors(enabled) {
  $('detectors').innerHTML = DETECTORS.map((detector) => `
    <label class="check">
      <input type="checkbox" data-detector="${detector.id}"
             ${enabled.includes(detector.id) ? 'checked' : ''}>
      <span class="name">${detector.label}</span>
    </label>`).join('');
}

(async function init() {
  const config = await getConfig();

  $('apiUrl').value = config.apiUrl;
  $('enabled').checked = config.enabled;
  $('reportCases').checked = config.reportCases;
  $('reportNavigation').checked = config.reportNavigation;
  renderDetectors(config.enabledDetectors);
  renderStatus();
  renderQueue();

  $('test').onclick = async () => {
    const url = $('apiUrl').value.trim().replace(/\/$/, '') || DEFAULTS.apiUrl;
    $('test-result').textContent = 'Testing…';
    try {
      const response = await fetch(`${url}/api/system/info`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const info = await response.json();
      $('test-result').textContent = `Connected to ${info.name} v${info.version}.`;
    } catch (error) {
      $('test-result').textContent =
        `Could not reach Cerebro at ${url} — is it running? (${error.message})`;
    }
  };

  $('retry').onclick = async () => {
    await chrome.runtime.sendMessage({ type: 'PING' }).catch(() => null);
    setTimeout(renderQueue, 600);
  };

  $('clear').onclick = async () => {
    await chrome.storage.local.set({ pendingEvents: [] });
    renderQueue();
  };

  /**
   * Non-local Cerebro instances need an explicit host permission, which Chrome
   * will only grant from a user gesture — so ask for it as part of Save.
   */
  async function ensureHostAccess(apiUrl) {
    try {
      const { origin, hostname } = new URL(apiUrl);
      if (['localhost', '127.0.0.1', '[::1]'].includes(hostname)) return true;
      const pattern = `${origin}/*`;
      if (await chrome.permissions.contains({ origins: [pattern] })) return true;
      const granted = await chrome.permissions.request({ origins: [pattern] });
      if (!granted) {
        $('test-result').textContent =
          `Chrome needs permission to reach ${origin}. Save again and accept the prompt.`;
      }
      return granted;
    } catch {
      return true;  // malformed URL — the connection test will surface it
    }
  }

  $('save').onclick = async () => {
    const apiUrl = $('apiUrl').value.trim().replace(/\/$/, '') || DEFAULTS.apiUrl;
    await ensureHostAccess(apiUrl);
    await setConfig({
      apiUrl,
      enabled: $('enabled').checked,
      reportCases: $('reportCases').checked,
      reportNavigation: $('reportNavigation').checked,
      enabledDetectors: [...document.querySelectorAll('[data-detector]')]
        .filter((input) => input.checked)
        .map((input) => input.dataset.detector),
    });
    $('saved').textContent = 'Saved.';
    setTimeout(() => { $('saved').textContent = ''; }, 2500);
    renderStatus();
  };
})();

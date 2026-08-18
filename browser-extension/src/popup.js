import { getConfig, setConfig } from './config.js';
import { detect } from './detectors.js';

const $ = (id) => document.getElementById(id);

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function row(label, value, muted = false) {
  return `<div class="row">
    <span class="label">${esc(label)}</span>
    <span class="value ${muted ? 'muted' : ''}">${esc(value)}</span>
  </div>`;
}

async function renderStatus(config) {
  const pill = $('status');
  const reply = await chrome.runtime.sendMessage({ type: 'PING' }).catch(() => null);

  if (!config.enabled) {
    pill.className = 'pill warn';
    pill.innerHTML = '<span class="dot"></span> paused';
  } else if (reply && reply.online) {
    pill.className = 'pill ok';
    pill.innerHTML = '<span class="dot"></span> connected';
  } else {
    pill.className = 'pill err';
    pill.innerHTML = '<span class="dot"></span> offline';
  }
  return reply;
}

async function renderContext(config, reply) {
  const host = $('context');
  if (!reply || !reply.online) {
    host.innerHTML = `<p class="hint">Cerebro is not running at
      <strong>${esc(config.apiUrl)}</strong>.<br>Start it, then reopen this popup.</p>`;
    return;
  }
  try {
    const response = await fetch(`${config.apiUrl}/api/context/current`, { cache: 'no-store' });
    const context = await response.json();
    host.innerHTML =
      row('Case', context.crm_case || 'none', !context.crm_case) +
      row('Customer', context.customer || 'unknown', !context.customer) +
      row('State', [
        context.call_active ? 'on a call' : null,
        context.remote_session_active ? 'remote session' : null,
      ].filter(Boolean).join(' · ') || 'idle', !context.call_active && !context.remote_session_active);
  } catch (error) {
    host.innerHTML = `<p class="hint">${esc(error.message)}</p>`;
  }
}

async function renderPage(config) {
  const host = $('page');
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    host.innerHTML = '<p class="hint">No page in view.</p>';
    return;
  }

  const match = detect(tab.url, tab.title || '');
  if (!match) {
    host.innerHTML = '<p class="hint">Not a recognised CRM case page. '
      + 'Open a Salesforce, ServiceNow or Zendesk case and Cerebro picks it up.</p>';
    return;
  }

  host.innerHTML =
    row('System', match.system) +
    row('Case', match.case_id) +
    row('Customer', match.customer || 'unknown', !match.customer) +
    `<div class="actions" style="margin-top:8px">
       <button id="resend">Send to Cerebro</button></div>`;

  $('resend').onclick = async () => {
    $('resend').disabled = true;
    $('resend').textContent = 'Sending…';
    try {
      await fetch(`${config.apiUrl}/api/events/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_type: 'CRM_CASE_OPENED',
          source: 'browser_extension',
          case_id: match.case_id,
          data: {
            system: match.system, case_id: match.case_id,
            customer: match.customer, url: match.url, title: match.title,
          },
        }),
      });
      $('resend').textContent = 'Sent ✓';
      renderContext(config, { online: true });
    } catch {
      $('resend').textContent = 'Failed — is Cerebro running?';
    }
  };
}

(async function init() {
  const config = await getConfig();

  $('toggle').textContent = config.enabled ? 'Pause' : 'Resume';
  $('toggle').onclick = async () => {
    await setConfig({ enabled: !config.enabled });
    window.location.reload();
  };
  $('dashboard').onclick = () => chrome.tabs.create({ url: config.apiUrl });
  $('options').onclick = (event) => {
    event.preventDefault();
    chrome.runtime.openOptionsPage();
  };

  const reply = await renderStatus(config);
  renderContext(config, reply);
  renderPage(config);
})();

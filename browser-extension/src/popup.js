import { getConfig, setConfig } from './config.js';
import { detect, detectDocument } from './detectors.js';
import { isExcluded } from './config.js';

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

  if (isExcluded(tab.url, config.excludedDomains)) {
    host.innerHTML = '<p class="hint">This site is on your excluded list — '
      + 'nothing here is read or reported.</p>';
    return;
  }

  const document_ = detectDocument(tab.url, tab.title || '');
  const match = detect(tab.url, tab.title || '');

  if (!match && document_) {
    host.innerHTML =
      row('Document', document_.filename) +
      row('Source', document_.source) +
      `<div class="actions" style="margin-top:8px">
         <button id="open-doc">Open in Cerebro</button></div>
       <p class="hint" style="margin-top:8px">Cerebro finds the synced copy on this
         machine, so it can read and edit the file.</p>`;

    $('open-doc').onclick = async () => {
      const button = $('open-doc');
      button.disabled = true;
      button.textContent = 'Opening…';
      try {
        const response = await fetch(`${config.apiUrl}/api/documents/observe`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ web_url: document_.web_url, discovered_by: 'browser' }),
        });
        const data = await response.json();
        button.textContent = response.ok
          ? `Reading ${data.kind || 'document'} ✓`
          : 'Not synced to this machine';
        if (!response.ok) {
          host.insertAdjacentHTML('beforeend',
            `<p class="hint">${esc(data.detail || '')}</p>`);
        }
      } catch {
        button.textContent = 'Failed — is Cerebro running?';
      }
    };
    return;
  }

  if (!match) {
    host.innerHTML = '<p class="hint">Nothing recognised here. Open a Salesforce, '
      + 'ServiceNow or Zendesk case, or a SharePoint document, and Cerebro picks it up.</p>';
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

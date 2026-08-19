/* Shared helpers for the Cerebro web screens. */

const API = {
  async request(method, path, body) {
    const options = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) options.body = JSON.stringify(body);

    const response = await fetch(path, options);
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }

    if (!response.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail || response.statusText));
    }
    return data;
  },
  get(path) { return this.request('GET', path); },
  post(path, body) { return this.request('POST', path, body); },
  put(path, body) { return this.request('PUT', path, body); },
  del(path) { return this.request('DELETE', path); },
};

function toast(message, kind = 'ok', ms = 4000) {
  let host = document.getElementById('toasts');
  if (!host) {
    host = document.createElement('div');
    host.id = 'toasts';
    document.body.appendChild(host);
  }
  const el = document.createElement('div');
  el.className = `toast ${kind}`;
  el.textContent = message;
  host.appendChild(el);
  setTimeout(() => el.remove(), ms);
}

/** Escape untrusted strings before inserting them into markup. */
function esc(value) {
  if (value === null || value === undefined) return '';
  return String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function relativeTime(iso) {
  if (!iso) return '';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

/**
 * Poll `fn` on an interval, pausing while the tab is hidden.
 *
 * A generation counter retires the previous chain instead of relying on
 * clearTimeout: when the tab becomes visible again the pending tick may already
 * be running, and cancelling its timer would not stop it from scheduling — so
 * each switch would leave an extra chain behind, doubling the poll rate.
 */
function poll(fn, ms) {
  let timer = null;
  let generation = 0;

  const start = () => {
    const mine = ++generation;
    const tick = async () => {
      if (mine !== generation) return;         // a newer chain took over
      if (!document.hidden) {
        try { await fn(); } catch (error) { console.warn('poll failed', error); }
      }
      if (mine !== generation) return;
      timer = setTimeout(tick, ms);
    };
    clearTimeout(timer);
    tick();
  };

  start();
  document.addEventListener('visibilitychange', () => { if (!document.hidden) start(); });
  return () => { generation += 1; clearTimeout(timer); };
}

/** Render the shared top bar so every screen stays consistent. */
/**
 * Settings-group icons.
 *
 * These replace per-platform emoji, which rendered in a different style and
 * colour on every OS and never matched the rest of the interface. One grid,
 * one stroke weight, and currentColor so they follow the theme and the
 * active-tab colour on their own.
 */
const GROUP_ICONS = {
  'general': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>`,
  'database': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 8c4.42 0 8-1.34 8-3s-3.58-3-8-3-8 1.34-8 3 3.58 3 8 3Z"/><path d="M20 5v6c0 1.66-3.58 3-8 3s-8-1.34-8-3V5"/><path d="M20 11v6c0 1.66-3.58 3-8 3s-8-1.34-8-3v-6"/></svg>`,
  'ai': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18"/><path d="M9 5.5A3 3 0 0 0 6 8.5a3 3 0 0 0-2 5.3A3 3 0 0 0 6.5 19a3 3 0 0 0 5.5.9"/><path d="M15 5.5a3 3 0 0 1 3 3 3 3 0 0 1 2 5.3A3 3 0 0 1 17.5 19a3 3 0 0 1-5.5.9"/><path d="M12 3a2.5 2.5 0 0 0-3 2.5"/><path d="M12 3a2.5 2.5 0 0 1 3 2.5"/></svg>`,
  'knowledge': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></svg>`,
  'enterprise': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="m22 7-10 6L2 7"/></svg>`,
  'documents': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M9 13h6"/><path d="M9 17h6"/></svg>`,
  'brain': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v18"/><path d="M9 5.5A3 3 0 0 0 6 8.5a3 3 0 0 0-2 5.3A3 3 0 0 0 6.5 19a3 3 0 0 0 5.5.9"/><path d="M15 5.5a3 3 0 0 1 3 3 3 3 0 0 1 2 5.3A3 3 0 0 1 17.5 19a3 3 0 0 1-5.5.9"/><path d="M8 11h3"/><path d="M13 15h3"/></svg>`,
  'secretary': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2v4"/><path d="M16 2v4"/><path d="M3 10h18"/><path d="M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/></svg>`,
  'copilot': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2a5 5 0 0 1 5 5v1a4 4 0 0 1 0 8v1a5 5 0 0 1-10 0v-1a4 4 0 0 1 0-8V7a5 5 0 0 1 5-5Z"/><path d="M12 8v8"/><path d="M9 11h6"/></svg>`,
  'capture': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/></svg>`,
  'desktop': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4h18a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/><path d="M8 21h8"/><path d="M12 17v4"/></svg>`,
  'logging': `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 4h10l6 6v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z"/><path d="M14 4v6h6"/><path d="M8 14h6"/><path d="M8 18h4"/></svg>`,
};

/** Icon markup for a settings group, falling back to whatever the API sent. */
function groupIcon(group) {
  return GROUP_ICONS[group.id] ||
    `<span style="font-size:15px">${group.icon || ''}</span>`;
}
function topbar(active) {
  const links = [
    ['/', 'Dashboard'],
    ['/settings', 'Settings'],
    ['/setup', 'Setup'],
    ['/docs', 'API'],
  ];
  return `
    <div class="topbar">
      <a class="brand" href="/" style="color:inherit">
        <svg class="mark" viewBox="0 0 256 256" width="26" height="26" aria-hidden="true"><defs><linearGradient id="cbm" x1="24" y1="28" x2="232" y2="228" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="#8B7CF6"/><stop offset=".55" stop-color="#6366F1"/><stop offset="1" stop-color="#22D3EE"/></linearGradient></defs><g fill="url(#cbm)"><path d="M128 30C106 30 88 41 80 59 60 58 44 71 42 90 27 98 20 116 26 133 17 149 22 170 38 179 41 199 58 212 78 209 90 221 110 227 128 222Z"/><path d="M128 30C106 30 88 41 80 59 60 58 44 71 42 90 27 98 20 116 26 133 17 149 22 170 38 179 41 199 58 212 78 209 90 221 110 227 128 222Z" transform="translate(256,0) scale(-1,1)"/></g><g stroke="#fff" stroke-width="18" stroke-linecap="round" fill="none"><path d="M128 56v144"/><path d="M128 104H84"/><path d="M128 152h44"/></g><g fill="#fff"><circle cx="84" cy="104" r="20"/><circle cx="172" cy="152" r="20"/></g></svg>
        <span>Cerebro <small id="version-badge"></small></span>
      </a>
      <nav class="nav">
        ${links.map(([href, label]) => `
          <a href="${href}"${href === active ? ' aria-current="page"' : ''}>${label}</a>`).join('')}
      </nav>
      <div class="spacer"></div>
      <span class="pill" id="conn-pill"><span class="dot"></span> checking…</span>
    </div>`;
}

async function refreshConnectionPill() {
  const pill = document.getElementById('conn-pill');
  if (!pill) return null;
  try {
    const info = await API.get('/api/system/info');
    pill.className = 'pill ok';
    pill.innerHTML = `<span class="dot"></span> connected`;
    const badge = document.getElementById('version-badge');
    if (badge) badge.textContent = `v${info.version}`;
    return info;
  } catch {
    pill.className = 'pill err';
    pill.innerHTML = `<span class="dot"></span> offline`;
    return null;
  }
}

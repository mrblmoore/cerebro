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
        <span class="mark">C</span>
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

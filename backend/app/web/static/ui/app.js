// Cerebro app: Ask, Activity, Sources and Connect, for the desktop shell and /app.
import { brainSVG, setBrainState, stateLabel } from './brain.js';
import { escapeHTML as esc, renderMarkdown } from './markdown.js';

// ------------------------------------------------------------------ helpers
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const api = {
  async request(method, path, body) {
    const response = await fetch(path, {
      method, headers: body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {},
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!response.ok) throw new Error((data && (data.detail?.message || data.detail)) || `HTTP ${response.status}`);
    return data;
  },
  get: path => api.request('GET', path),
  post: (path, body) => api.request('POST', path, body || {}),
  del: path => api.request('DELETE', path),
};

const shell = () => window.pywebview?.api || null;

function toast(text, kind = '') {
  const node = document.createElement('div');
  node.className = `toast ${kind}`;
  node.textContent = text;
  $('#toasts').append(node);
  setTimeout(() => node.remove(), 3600);
}

function relTime(iso) {
  if (!iso) return '';
  const then = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : `${iso}Z`);
  const seconds = Math.round((Date.now() - then) / 1000);
  if (seconds < 45) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return then.toLocaleDateString();
}

function openExternal(url) {
  if (!url) return;
  if (shell()?.open_external) shell().open_external(url);
  else window.open(url, '_blank', 'noopener');
}

const ICONS = {
  check: '<svg class="check" viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="var(--ok)" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 8.5l3 3 6-7"/></svg>',
  warn: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="var(--warn)" stroke-width="2" stroke-linecap="round"><path d="M8 4v5"/><circle cx="8" cy="12" r=".6" fill="var(--warn)"/></svg>',
  error: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" stroke="var(--err)" stroke-width="2" stroke-linecap="round"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"/></svg>',
  case: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
  user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>',
  call: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2Z"/></svg>',
  doc: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/></svg>',
  app: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/></svg>',
};

// -------------------------------------------------------------------- state
const state = {
  history: [],
  busy: false,
  image: null,          // {name, preview}
  context: {},
  activity: { state: 'idle', detail: 'Idle', active: [] },
  aiEnabled: null,
  tab: 'ask',
  sources: new Map(),   // ref -> source, for citation popovers
};

// ------------------------------------------------------------------- brains
$('#brand-brain').innerHTML = brainSVG(38);

// --------------------------------------------------------------------- tabs
function selectTab(name) {
  state.tab = name;
  $$('.tab').forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.tab === name)));
  $$('.panel').forEach(panel => panel.classList.toggle('active', panel.id === `panel-${name}`));
  moveIndicator();
  if (name === 'activity') loadActivity();
  if (name === 'sources') loadSources();
  if (name === 'connect') loadConnect();
  if (name === 'ask') $('#input').focus();
}

function moveIndicator() {
  const active = $('.tab[aria-selected="true"]');
  const indicator = $('#tab-indicator');
  if (!active) return;
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.transform = `translateX(${active.offsetLeft - 4}px)`;
}

$$('.tab').forEach(tab => tab.addEventListener('click', () => selectTab(tab.dataset.tab)));
addEventListener('resize', moveIndicator);

// -------------------------------------------------------------------- theme
$('#theme-btn').addEventListener('click', () => {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('cerebro.theme', next); } catch { /* ignore */ }
});

$('#menu-btn').addEventListener('click', event => {
  showMenu(event.currentTarget, [
    ['Open dashboard', () => openExternal(`${location.origin}/`)],
    ['Settings', () => openExternal(`${location.origin}/settings`)],
    ['Clear conversation view', () => { state.history = []; renderThread(); }],
  ]);
});

function showMenu(anchor, items) {
  closePopover();
  const rect = anchor.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.className = 'popover';
  menu.style.width = '210px';
  menu.style.padding = '6px';
  menu.innerHTML = items.map(([label], i) =>
    `<button class="btn ghost" data-i="${i}" style="width:100%;justify-content:flex-start">${esc(label)}</button>`).join('');
  menu.style.top = `${rect.bottom + 6}px`;
  menu.style.left = `${Math.max(8, rect.right - 210)}px`;
  menu.addEventListener('click', e => {
    const button = e.target.closest('[data-i]');
    if (button) { closePopover(); items[Number(button.dataset.i)][1](); }
  });
  document.body.append(menu);
  currentPopover = menu;
}

let currentPopover = null;
function closePopover() { currentPopover?.remove(); currentPopover = null; }
document.addEventListener('click', e => {
  if (currentPopover && !currentPopover.contains(e.target) &&
      !e.target.closest('.cite, #menu-btn, .source-pill')) closePopover();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closePopover(); });

// ---------------------------------------------------------- shell controls
function initShell() {
  document.body.classList.add('in-shell');
  $('#hide-btn').addEventListener('click', () => shell()?.hide());
  $('#compact-btn').addEventListener('click', async () => {
    const compact = !document.body.classList.contains('compact');
    document.body.classList.toggle('compact', compact);
    await shell()?.set_compact(compact);
  });
  $('#pin-btn').addEventListener('click', async e => {
    const pinned = await shell()?.toggle_on_top();
    e.currentTarget.setAttribute('aria-pressed', String(Boolean(pinned)));
  });
  $('.titlebar').addEventListener('dblclick', e => {
    if (!e.target.closest('button')) $('#compact-btn').click();
  });
}
if (window.pywebview?.api) initShell();
else window.addEventListener('pywebviewready', initShell);
if (document.documentElement.classList.contains('shell')) document.body.classList.add('in-shell');

// ---------------------------------------------------------- live activity
function paintActivity() {
  const { state: current, detail, label } = state.activity;
  setBrainState(document.body, current);
  const busy = !['idle', 'awaiting_approval', 'error', 'offline'].includes(current);
  const dot = $('#status-dot');
  dot.dataset.state = current;
  dot.dataset.busy = String(busy);
  $('#status-text').textContent = current === 'idle'
    ? (state.context.crm_case ? `Ready · ${state.context.crm_case}` : 'Ready')
    : (detail || label || stateLabel(current));
  const pending = state.activity.pending_approvals || 0;
  $('#approval-count').textContent = pending ? String(pending) : '';
  if (state.tab === 'activity') paintNow();
}

function connectActivity() {
  let failures = 0;
  const open = () => {
    const source = new EventSource('/api/system/activity/stream');
    source.onmessage = event => {
      failures = 0;
      state.activity = JSON.parse(event.data);
      paintActivity();
    };
    source.onerror = () => {
      source.close();
      failures += 1;
      if (failures > 1) { state.activity = { state: 'offline', detail: 'Cerebro is not reachable' }; paintActivity(); }
      setTimeout(open, Math.min(15000, 1000 * failures));
    };
  };
  open();
}

// --------------------------------------------------------------- context
async function loadContext() {
  try {
    state.context = await api.get('/api/context/current') || {};
  } catch { state.context = {}; }
  const c = state.context;
  const chips = [];
  if (c.crm_case) chips.push(`<span class="chip case">${ICONS.case}${esc(c.crm_case)}</span>`);
  if (c.customer) chips.push(`<span class="chip">${ICONS.user}${esc(c.customer)}</span>`);
  if (c.call_active) chips.push(`<span class="chip live">${ICONS.call}On a call</span>`);
  if (c.active_application) chips.push(`<span class="chip">${ICONS.app}${esc(c.active_application)}</span>`);
  if (c.window_title && c.active_application === 'Document') chips.push(`<span class="chip">${ICONS.doc}${esc(c.window_title)}</span>`);
  $('#context-strip').innerHTML = chips.join('');
  paintActivity();
}

async function loadInfo() {
  try {
    const info = await api.get('/api/system/info');
    state.aiEnabled = info.ai_enabled;
    $('#ai-hint').innerHTML = info.ai_enabled ? '' :
      `<a href="#" id="ai-setup">Connect an AI provider</a>`;
    $('#ai-setup')?.addEventListener('click', e => { e.preventDefault(); openExternal(`${location.origin}/settings#ai`); });
  } catch { /* offline: the activity stream reports it */ }
}

// ===================================================================== Ask
const thread = $('#thread');
const scroller = $('#ask-scroll');
const input = $('#input');

function scrollToEnd(smooth = true) {
  requestAnimationFrame(() => scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' }));
}

async function loadHistory() {
  try {
    const data = await api.get('/api/chat/history?limit=60');
    state.history = data.messages || [];
  } catch { state.history = []; }
  renderThread();
  scrollToEnd(false);
}

const SUGGESTIONS = [
  ['Catch me up', 'What needs my attention in Outlook and Teams?'],
  ['This case', 'Summarise the case I have open and suggest next steps'],
  ['Knowledge', 'Search the knowledge base for a fix to error 0x80040115'],
  ['Draft', 'Draft a reply to the most recent customer email'],
];

function renderEmpty() {
  thread.innerHTML = `
    <div class="hero">
      <div class="brain-host">${brainSVG(112)}</div>
      <h2>How can I help?</h2>
      <p>Ask a question, look something up across your sources, or tell me what to do. I'll ask before changing anything.</p>
      <div class="suggestions">
        ${SUGGESTIONS.map(([title, text]) => `
          <button class="suggestion" data-text="${esc(text)}"><b>${esc(title)}</b><span>${esc(text)}</span></button>`).join('')}
      </div>
    </div>`;
  setBrainState(thread, state.activity.state);
  $$('.suggestion', thread).forEach(button => button.addEventListener('click', () => {
    input.value = button.dataset.text;
    send();
  }));
}

function renderThread() {
  if (!state.history.length) return renderEmpty();
  thread.innerHTML = '';
  state.history.forEach(message => thread.append(messageNode(message)));
  setBrainState(thread, state.activity.state);
}

function messageNode(message) {
  const node = document.createElement('div');
  const meta = message.meta || {};
  node.className = `msg ${message.role === 'user' ? 'user' : 'assistant'}`;
  if (message.role === 'user') {
    const image = message.image_path ? `<img class="attached" src="/api/chat/image/${encodeURIComponent(message.image_path)}" alt="">` : '';
    node.innerHTML = `<div class="bubble">${image}${esc(message.content).replace(/\n/g, '<br>')}</div>`;
    return node;
  }
  (meta.sources || []).forEach(source => source.ref && state.sources.set(source.ref, source));
  const short = (message.content || '').length < 60 && !(meta.cards || []).some(c => c.type !== 'progress' && c.type !== 'completion');
  if (short) node.classList.add('short');
  node.innerHTML = `
    <div class="avatar">${brainSVG(28)}</div>
    <div class="bubble glass">
      ${message.steps ? stepsHTML(message.steps) : ''}
      <div class="md">${renderMarkdown(message.content || '')}</div>
      ${imagesHTML(meta.images)}
      ${sourcesHTML(meta.sources)}
      <div class="cards"></div>
    </div>`;
  const cards = $('.cards', node);
  (meta.cards || []).forEach(card => {
    const element = actionCard(card, meta);
    if (element) cards.append(element);
  });
  wireCitations(node);
  return node;
}

function stepsHTML(steps) {
  if (!steps.length) return '';
  return `<div class="steps">${steps.map(step => `
    <div class="step"><span class="ico">${step.status === 'running' ? '<span class="spinner"></span>'
      : step.status === 'warning' ? ICONS.warn : step.status === 'error' ? ICONS.error : ICONS.check}</span>
      <span>${esc(step.title)}</span>${step.detail ? `<span class="detail">· ${esc(step.detail)}</span>` : ''}</div>`).join('')}</div>`;
}

function imagesHTML(images) {
  if (!images?.length) return '';
  return `<div class="sources">${images.map(item => {
    const name = typeof item === 'string' ? item : item.image;
    return name ? `<img class="attached" src="/api/chat/image/${encodeURIComponent(name)}" alt="${esc(item.caption || '')}">` : '';
  }).join('')}</div>`;
}

function sourcesHTML(sources) {
  if (!sources?.length) return '';
  return `<div class="sources">${sources.map(source => `
    <button class="source-pill" data-ref="${esc(source.ref)}" title="${esc(source.title || '')}">
      <span class="ref">${esc(source.ref)}</span><span class="title">${esc(source.title || 'Source')}</span>
    </button>`).join('')}</div>`;
}

function wireCitations(root) {
  $$('.cite, .source-pill', root).forEach(button => button.addEventListener('click', event => {
    event.stopPropagation();
    const source = state.sources.get(button.dataset.ref);
    if (!source) return;
    if (currentPopover?.dataset.ref === button.dataset.ref) return closePopover();
    showSource(button, source);
  }));
}

function showSource(anchor, source) {
  closePopover();
  const pop = document.createElement('div');
  pop.className = 'popover';
  pop.dataset.ref = source.ref;
  const link = source.uri && /^https?:/.test(source.uri);
  pop.innerHTML = `
    <h5>${esc(source.title || 'Source')}</h5>
    <div class="meta">${esc(source.ref)} · ${esc(source.kind || 'source')}${source.locator ? ` · ${esc(source.locator)}` : ''}</div>
    <div class="excerpt">${esc((source.excerpt || '').slice(0, 900))}</div>
    ${link ? '<div class="btn-row"><button class="btn sm" data-open>Open</button></div>' : ''}`;
  document.body.append(pop);
  const rect = anchor.getBoundingClientRect();
  const top = rect.bottom + 8 + pop.offsetHeight > innerHeight ? rect.top - pop.offsetHeight - 8 : rect.bottom + 8;
  pop.style.top = `${Math.max(8, top)}px`;
  pop.style.left = `${Math.min(Math.max(8, rect.left), innerWidth - pop.offsetWidth - 8)}px`;
  $('[data-open]', pop)?.addEventListener('click', () => openExternal(source.uri));
  currentPopover = pop;
}

// ---------------------------------------------------------- action cards
function actionCard(card) {
  if (card.type === 'approval') return changeCard(card);
  if (card.type === 'draft') return draftCard(card);
  if (card.type === 'signin') return signinCard(card);
  return null;
}

const INTEGRATION_LABEL = { dynamics: 'Dynamics 365', rightanswers: 'RightAnswers', sharepoint: 'SharePoint' };

function changeCard(card) {
  const node = document.createElement('div');
  const status = card.status || 'awaiting_approval';
  node.className = `action-card ${status === 'awaiting_approval' ? 'awaiting' : status}`;
  const fields = card.preview?.fields || [];
  node.innerHTML = `
    <div class="action-head">
      <span class="title">${esc(card.title)}</span>
      <span class="badge accent">${esc(INTEGRATION_LABEL[card.integration] || card.integration || 'Change')}</span>
      ${statusBadge(status)}
    </div>
    <div class="diff">${fields.map(diffField).join('')}</div>
    ${status === 'awaiting_approval' ? `
      <div class="btn-row">
        <button class="btn primary" data-approve>${esc(card.approve_label || 'Approve')}</button>
        <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button>
        ${card.preview?.url ? '<button class="btn ghost" data-open>Open record</button>' : ''}
      </div>` : ''}`;
  $('[data-approve]', node)?.addEventListener('click', () => decide(node, `/api/chat/changes/${card.action_id}/approve`));
  $('[data-discard]', node)?.addEventListener('click', () => decide(node, `/api/chat/changes/${card.action_id}/discard`));
  $('[data-open]', node)?.addEventListener('click', () => openExternal(card.preview.url));
  return node;
}

function statusBadge(status) {
  const map = {
    awaiting_approval: ['warn', 'Needs approval'], done: ['ok', 'Done'], running: ['accent', 'Running'],
    failed: ['err', 'Failed'], discarded: ['', 'Discarded'], queued: ['ok', 'Queued'],
  };
  const [kind, label] = map[status] || ['', status];
  return `<span class="badge ${kind}">${esc(label)}</span>`;
}

function diffField(field) {
  const before = field.before == null ? '' : String(field.before);
  const after = field.after == null ? '' : String(field.after);
  const short = before.length < 60 && after.length < 60 && !before.includes('\n') && !after.includes('\n');
  if (short && before) {
    return `<div class="diff-field"><div class="name">${esc(field.name)}</div>
      <div class="diff-inline"><span class="before">${esc(before)}</span><span>→</span><span class="after">${esc(after)}</span></div></div>`;
  }
  return `<div class="diff-field"><div class="name">${esc(field.name)}</div>
    <div class="diff-box">${before ? wordDiff(before, after) : esc(after)}</div></div>`;
}

/** Word-level diff (LCS) rendered with <del>/<ins>; falls back for long texts. */
function wordDiff(a, b) {
  const x = a.split(/(\s+)/), y = b.split(/(\s+)/);
  if (x.length * y.length > 400000) return `<del>${esc(a)}</del>\n<ins>${esc(b)}</ins>`;
  const table = Array.from({ length: x.length + 1 }, () => new Uint16Array(y.length + 1));
  for (let i = x.length - 1; i >= 0; i--)
    for (let j = y.length - 1; j >= 0; j--)
      table[i][j] = x[i] === y[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
  let i = 0, j = 0;
  const ops = [];
  while (i < x.length && j < y.length) {
    if (x[i] === y[j]) { ops.push(['=', x[i]]); i++; j++; }
    else if (table[i + 1][j] >= table[i][j + 1]) ops.push(['-', x[i++]]);
    else ops.push(['+', y[j++]]);
  }
  while (i < x.length) ops.push(['-', x[i++]]);
  while (j < y.length) ops.push(['+', y[j++]]);

  // Group each run of changes into one deletion followed by one insertion —
  // whitespace that is merely shared between changed words joins the run —
  // so a rewritten phrase reads as a phrase, not a patchwork of words.
  let out = '', dels = '', inss = '';
  const flush = () => {
    if (dels) out += `<del>${esc(dels)}</del>`;
    if (inss) out += `<ins>${esc(inss)}</ins>`;
    dels = inss = '';
  };
  ops.forEach(([op, text], k) => {
    const changedAround = (dels || inss) && ops.slice(k + 1).find(o => !/^\s*$/.test(o[1]))?.[0] !== '=';
    if (op === '=' && /^\s+$/.test(text) && changedAround) { dels += text; inss += text; return; }
    if (op === '=') { flush(); out += esc(text); return; }
    if (op === '-') dels += text; else inss += text;
  });
  flush();
  return out;
}

function draftCard(card) {
  const node = document.createElement('div');
  const awaiting = card.status === 'awaiting_approval';
  node.className = `action-card ${awaiting ? 'awaiting' : 'done'}`;
  const to = card.chat_or_channel || (card.to || []).join(', ');
  node.innerHTML = `
    <div class="action-head"><span class="title">${esc(card.title || 'Draft')}</span>
      <span class="badge accent">Power Automate</span>${statusBadge(card.status || 'awaiting_approval')}</div>
    <div class="meta" style="font-size:11.5px;color:var(--text-faint);margin-bottom:6px">${to ? `To ${esc(to)}` : ''}${card.subject ? ` · ${esc(card.subject)}` : ''}</div>
    <div class="draft-body">${esc(card.body || '')}</div>
    ${awaiting ? `<div class="btn-row">
      <button class="btn primary" data-approve>${esc(card.approve_label || 'Approve and send')}</button>
      <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button></div>` : ''}`;
  $('[data-approve]', node)?.addEventListener('click', () => decide(node, `/api/chat/actions/${card.action_id}/approve`));
  $('[data-discard]', node)?.addEventListener('click', () => decide(node, `/api/chat/actions/${card.action_id}/discard`));
  return node;
}

function signinCard(card) {
  const node = document.createElement('div');
  node.className = 'action-card awaiting';
  node.innerHTML = `
    <div class="action-head"><span class="title">${esc(card.title)}</span><span class="badge warn">Sign-in needed</span></div>
    <p style="margin:0;color:var(--text-dim);font-size:12.5px">${esc(card.detail || '')}</p>
    <div class="btn-row"><button class="btn primary" data-signin>Sign in</button></div>`;
  $('[data-signin]', node).addEventListener('click', e => signIn(card.integration, e.currentTarget));
  return node;
}

async function decide(node, path) {
  $$('button', node).forEach(button => { button.disabled = true; });
  try {
    const result = await api.post(path);
    toast(result.reply || 'Done', result.cards?.some(c => c.status === 'error') ? 'err' : 'ok');
  } catch (error) {
    toast(error.message, 'err');
  }
  await loadHistory();
}

// ------------------------------------------------------------- composer
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  $('#send').disabled = state.busy || (!input.value.trim() && !state.image);
});
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});
$('#composer').addEventListener('submit', event => { event.preventDefault(); send(); });
$('#attach-btn').addEventListener('click', () => $('#file-input').click());
$('#file-input').addEventListener('change', e => e.target.files[0] && attach(e.target.files[0]));
input.addEventListener('paste', event => {
  const file = [...(event.clipboardData?.files || [])].find(f => f.type.startsWith('image/'));
  if (file) { event.preventDefault(); attach(file); }
});
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', event => {
  event.preventDefault();
  const file = [...(event.dataTransfer?.files || [])].find(f => f.type.startsWith('image/'));
  if (file) { selectTab('ask'); attach(file); }
});

async function attach(file) {
  const form = new FormData();
  form.append('file', file, file.name || 'pasted.png');
  try {
    const { image } = await api.post('/api/chat/upload-image', form);
    state.image = { name: image, preview: URL.createObjectURL(file) };
    $('#attachment').innerHTML = `<div class="attachment glass"><img src="${state.image.preview}" alt="">
      <button class="btn sm ghost" id="drop-image">Remove</button></div>`;
    $('#drop-image').addEventListener('click', clearImage);
    $('#send').disabled = state.busy;
    input.focus();
  } catch (error) {
    toast(`Couldn't attach that image: ${error.message}`, 'err');
  }
}

function clearImage() {
  state.image = null;
  $('#attachment').innerHTML = '';
  $('#send').disabled = state.busy || !input.value.trim();
}

async function send() {
  const text = input.value.trim();
  if (state.busy || (!text && !state.image)) return;
  const image = state.image?.name || null;
  state.busy = true;
  input.value = '';
  input.style.height = 'auto';
  $('#send').disabled = true;
  if (!state.history.length) thread.innerHTML = '';

  thread.append(messageNode({ role: 'user', content: text || '(image)', image_path: image }));
  clearImage();
  const pending = document.createElement('div');
  pending.className = 'msg assistant short';
  pending.innerHTML = `<div class="avatar">${brainSVG(28)}</div>
    <div class="bubble glass"><div class="steps"></div><div class="thinking"><i></i><i></i><i></i></div></div>`;
  setBrainState(pending, 'thinking');
  thread.append(pending);
  scrollToEnd();

  const steps = [];
  const paintSteps = () => {
    $('.steps', pending).outerHTML = stepsHTML(steps) || '<div class="steps"></div>';
    scrollToEnd();
  };

  try {
    const final = await streamChat({ message: text, image }, card => {
      if (card.type !== 'progress') return;
      const running = steps.findLast?.(s => s.title === card.title && s.status === 'running')
        ?? [...steps].reverse().find(s => s.title === card.title && s.status === 'running');
      if (running && card.status !== 'running') Object.assign(running, card);
      else steps.push({ ...card });
      paintSteps();
    });
    pending.remove();
    const message = {
      role: 'assistant', content: final.reply, kind: final.kind, steps: steps.filter(s => s.status !== 'running'),
      meta: { sources: final.sources, cards: final.cards, images: final.images },
    };
    state.history.push({ role: 'user', content: text, image_path: image }, message);
    thread.append(messageNode(message));
  } catch (error) {
    pending.remove();
    thread.append(messageNode({ role: 'assistant', content: `I couldn't do that: ${error.message}` }));
  } finally {
    state.busy = false;
    $('#send').disabled = !input.value.trim();
    scrollToEnd();
  }
}

/** POST /api/chat/stream and parse its Server-Sent Events. */
async function streamChat(body, onCard) {
  const response = await fetch('/api/chat/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const text = await response.text();
    throw new Error(text || `HTTP ${response.status}`);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let index;
    while ((index = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      let event = 'message', data = '';
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7);
        else if (line.startsWith('data: ')) data += line.slice(6);
      }
      if (!data) continue;
      const payload = JSON.parse(data);
      if (event === 'card') onCard(payload);
      else if (event === 'message') return payload;
      else if (event === 'error') throw new Error(payload.detail || 'Something went wrong');
    }
  }
  throw new Error('The connection closed before an answer arrived.');
}

// ================================================================ Activity
function paintNow() {
  const host = $('#now-card');
  if (!host) return;
  const a = state.activity;
  $('.now-title', host).textContent = stateLabel(a.state);
  $('.now-detail', host).textContent = a.state === 'idle'
    ? 'Nothing running. Ask away.' : (a.detail || '');
  setBrainState(host, a.state);
  $('.now-list', host).innerHTML = (a.active || [])
    .filter(item => item.state !== 'awaiting_approval' && item.detail !== a.detail).slice(0, 4).map(item =>
    `<div class="step"><span class="ico">${item.state === 'error' ? ICONS.error : '<span class="spinner"></span>'}</span>
     <span>${esc(item.detail)}</span><span class="detail">· ${Math.round(item.elapsed_s || 0)}s</span></div>`).join('');
}

async function loadActivity() {
  const body = $('#activity-body');
  if (!$('#now-card')) {
    body.innerHTML = `
      <div class="now glass" id="now-card"><div class="brain-host">${brainSVG(64)}</div>
        <div style="min-width:0;flex:1"><h3 class="now-title"></h3><div class="now-detail detail"></div><div class="now-list"></div></div></div>
      <div id="activity-lists"></div>`;
  }
  paintNow();
  const [changes, nudges, tasks, events] = await Promise.all([
    api.get('/api/chat/changes?limit=20').catch(() => ({ changes: [] })),
    api.get('/api/tasks/nudges?limit=10').catch(() => ({ nudges: [] })),
    api.get('/api/tasks?limit=10').catch(() => ({ tasks: [] })),
    api.get('/api/events/?limit=8').catch(() => []),
  ]);
  const lists = $('#activity-lists');
  lists.innerHTML = '';
  const pending = (changes.changes || []).filter(c => c.status === 'draft');
  const recent = (changes.changes || []).filter(c => c.status !== 'draft').slice(0, 5);

  lists.insertAdjacentHTML('beforeend', `<div class="section-title">Waiting for you<span class="grow"></span></div>`);
  if (!pending.length && !(nudges.nudges || []).length) {
    lists.insertAdjacentHTML('beforeend', '<div class="empty-note">Nothing needs your approval.</div>');
  }
  pending.forEach(change => lists.append(changeCard({
    type: 'approval', action_id: change.id, tool: change.tool, integration: change.integration,
    title: change.title, preview: change.preview, status: 'awaiting_approval',
  })));
  (nudges.nudges || []).forEach(nudge => {
    const card = document.createElement('div');
    card.className = 'card glass';
    card.innerHTML = `<div class="list-row"><div class="grow"><h4>${esc(nudge.title)}</h4>
      <p>${esc(nudge.body || '')}</p></div></div>
      <div class="btn-row">${nudge.action ? '<button class="btn sm primary" data-act>Yes, do it</button>' : ''}
      <button class="btn sm ghost" data-dismiss>Dismiss</button></div>`;
    $('[data-act]', card)?.addEventListener('click', async () => {
      await api.post(`/api/tasks/nudges/${nudge.id}/act`).then(r => toast(r.detail || 'Done', 'ok')).catch(e => toast(e.message, 'err'));
      loadActivity();
    });
    $('[data-dismiss]', card).addEventListener('click', async () => {
      await api.post(`/api/tasks/nudges/${nudge.id}/dismiss`).catch(() => {});
      loadActivity();
    });
    lists.append(card);
  });

  if (recent.length) {
    lists.insertAdjacentHTML('beforeend', `<div class="section-title">Recent changes<span class="grow"></span></div>`);
    recent.forEach(change => lists.insertAdjacentHTML('beforeend', `
      <div class="card glass"><div class="list-row"><div class="grow"><div class="title">${esc(change.title)}</div>
      <div class="meta">${esc(INTEGRATION_LABEL[change.integration] || change.integration)} · ${relTime(change.created_at)}${change.error ? ` · ${esc(change.error)}` : ''}</div></div>
      ${statusBadge(change.status)}</div></div>`));
  }

  lists.insertAdjacentHTML('beforeend', `<div class="section-title">Tasks<span class="grow"></span></div>`);
  const taskRows = (tasks.tasks || []).filter(t => ['active', 'needs_review'].includes(t.status));
  if (!taskRows.length) lists.insertAdjacentHTML('beforeend', '<div class="empty-note">No scheduled tasks. Ask Cerebro to “remind me…” or “every morning…”.</div>');
  taskRows.forEach(task => lists.insertAdjacentHTML('beforeend', `
    <div class="card glass"><div class="list-row"><div class="grow"><div class="title">${esc(task.title)}</div>
    <div class="meta">${esc(task.schedule || '')}${task.next_run ? ` · next ${relTime(task.next_run).replace(' ago', '')}` : ''}</div></div>
    ${task.status === 'needs_review' ? statusBadge('awaiting_approval') : ''}</div></div>`));

  const eventRows = Array.isArray(events) ? events : events.events || [];
  if (eventRows.length) {
    lists.insertAdjacentHTML('beforeend', `<div class="section-title">Recent events<span class="grow"></span></div>`);
    eventRows.forEach(event => lists.insertAdjacentHTML('beforeend', `
      <div class="card glass"><div class="list-row"><div class="grow"><div class="title">${esc(event.event_type.replace(/_/g, ' ').toLowerCase())}</div>
      <div class="meta">${esc(event.case_id || event.source || '')} · ${relTime(event.created_at)}</div></div></div></div>`));
  }
}

// ================================================================= Sources
async function loadSources() {
  const data = await api.get('/api/sources?limit=40').catch(() => ({ sources: [] }));
  const body = $('#sources-body');
  body.innerHTML = `<div class="section-title">What Cerebro can read<span class="grow"></span></div>`;
  if (!(data.sources || []).length) {
    body.insertAdjacentHTML('beforeend', '<div class="empty-note">Open a document or use “Read this page” in the browser extension, and it appears here.</div>');
  }
  (data.sources || []).forEach(source => {
    const card = document.createElement('div');
    card.className = 'card glass';
    card.innerHTML = `<div class="list-row">
      <div class="grow"><div class="title">${esc(source.title || 'Untitled')}</div>
      <div class="meta">${esc(source.kind)} · ${source.readable ? `${(source.characters || 0).toLocaleString()} characters` : esc(source.error || 'not readable')} · ${relTime(source.last_seen)}</div></div>
      <label class="switch" title="Use in answers"><input type="checkbox" ${source.active && !source.excluded ? 'checked' : ''}><span></span></label></div>`;
    $('input', card).addEventListener('change', async e => {
      await api.post(`/api/sources/${source.id}/active`, { active: e.target.checked }).catch(err => toast(err.message, 'err'));
    });
    body.append(card);
  });
}

let searchTimer = null;
$('#kb-search').addEventListener('input', e => {
  clearTimeout(searchTimer);
  const query = e.target.value.trim();
  searchTimer = setTimeout(async () => {
    const host = $('#kb-results');
    if (query.length < 2) { host.innerHTML = ''; return; }
    const data = await api.get(`/api/knowledge/search?query=${encodeURIComponent(query)}&limit=6`).catch(() => ({ results: [] }));
    host.innerHTML = (data.results || []).length ? (data.results || []).map(hit => `
      <div class="card glass"><h4>${esc(hit.title)}</h4><div class="meta">${esc(hit.source || '')} · ${esc(hit.locator || '')}</div>
      <p>${esc((hit.excerpt || '').slice(0, 220))}…</p></div>`).join('')
      : '<div class="empty-note">Nothing indexed matches that.</div>';
  }, 260);
});

// ================================================================= Connect
const LOGO = { dynamics: 'D365', rightanswers: 'RA', sharepoint: 'SP' };

async function loadConnect() {
  const body = $('#connect-body');
  const data = await api.get('/api/integrations').catch(error => ({ error: error.message }));
  if (data.error) { body.innerHTML = `<div class="empty-note">${esc(data.error)}</div>`; return; }
  const b = data.browser || {};
  body.innerHTML = `
    <div class="section-title">Hidden browser<span class="grow"></span></div>
    <div class="card glass integration">
      <div class="logo browser">${b.running ? '●' : '○'}</div>
      <div><h4>${b.enabled ? (b.running ? `Running · ${esc(b.channel || '')} · ${esc(b.mode || '')}` : 'Ready — starts when needed') : 'Switched off'}</h4>
        <div class="meta">${b.installed ? 'Uses your own sign-ins in a separate, private browser profile.' : 'Install backend/requirements-browser.txt to enable.'}</div></div>
      <button class="btn sm" data-settings>Settings</button>
    </div>
    <div class="section-title">Systems<span class="grow"></span></div>
    <div id="integration-list"></div>`;
  $('[data-settings]', body).addEventListener('click', () => openExternal(`${location.origin}/settings#integrations`));
  const list = $('#integration-list');
  (data.integrations || []).forEach(item => list.append(integrationCard(item)));
}

function integrationCard(item) {
  const card = document.createElement('div');
  card.className = 'card glass integration';
  const signedIn = item.signed_in === true;
  const waiting = item.sign_in?.status === 'waiting';
  const badge = !item.enabled ? '<span class="badge">Off</span>'
    : waiting ? '<span class="badge warn">Signing in…</span>'
    : signedIn ? '<span class="badge ok">Signed in</span>'
    : item.signed_in === false ? '<span class="badge err">Sign-in needed</span>'
    : '<span class="badge">Not checked</span>';
  card.innerHTML = `
    <div class="logo ${item.name}">${LOGO[item.name] || item.label[0]}</div>
    <div style="min-width:0"><h4>${esc(item.label)} ${badge}</h4>
      <div class="meta">${item.account ? `Signed in as ${esc(item.account)} · ` : ''}${item.url ? esc(item.url) : 'Add its address in Settings'}${item.checked_at ? ` · checked ${relTime(new Date(item.checked_at * 1000).toISOString())}` : ''}</div>
      ${item.sign_in?.detail && item.sign_in.status !== 'idle' ? `<p>${esc(item.sign_in.detail)}</p>` : ''}</div>
    <div class="btn-row" style="margin:0;flex-direction:column">
      ${item.enabled ? `<button class="btn sm ${signedIn ? '' : 'primary'}" data-signin>${signedIn ? 'Sign in again' : 'Sign in'}</button>
      <button class="btn sm ghost" data-check>Check</button>
      ${item.name === 'rightanswers' && signedIn ? '<button class="btn sm ghost" data-teach>Teach</button>' : ''}`
      : item.configured ? '<button class="btn sm primary" data-connect>Connect</button>'
      : '<button class="btn sm" data-settings>Set up</button>'}
    </div>`;
  $('[data-signin]', card)?.addEventListener('click', e => signIn(item.name, e.currentTarget));
  // One click: switch the system (and the hidden browser) on, then sign in.
  $('[data-connect]', card)?.addEventListener('click', async e => {
    const button = e.currentTarget;
    button.disabled = true;
    try {
      await api.post(`/api/integrations/${item.name}/enable`);
      await signIn(item.name, button);
    } catch (error) {
      toast(error.message, 'err');
      button.disabled = false;
    }
  });
  $('[data-teach]', card)?.addEventListener('click', e => teach(item.name, e.currentTarget));
  $('[data-check]', card)?.addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    const result = await api.post(`/api/integrations/${item.name}/check`).catch(err => ({ detail: err.message }));
    toast(result.detail, result.ok ? 'ok' : 'err');
    loadConnect();
  });
  $('[data-settings]', card)?.addEventListener('click', () => openExternal(`${location.origin}/settings#integrations`));
  return card;
}

/** Teach Cerebro the RightAnswers portal's layout, by watching one search. */
async function teach(name, button) {
  if (button) button.disabled = true;
  const started = await api.post(`/api/integrations/${name}/teach/start`).catch(err => ({ ok: false, detail: err.message }));
  if (!started.ok) { toast(started.detail || 'Teaching could not start', 'err'); if (button) button.disabled = false; return; }
  toast(started.detail || 'Follow the steps in the window that opened.');
  const poll = setInterval(async () => {
    const status = await api.get(`/api/integrations/${name}/teach/status`).catch(() => null);
    if (!status || ['waiting', 'searching', 'opening', 'editing'].includes(status.status)) return;
    clearInterval(poll);
    toast(status.detail || 'Done', status.status === 'learned' ? 'ok' : 'err');
    if (state.tab === 'connect') loadConnect();
    if (button) button.disabled = false;
  }, 2000);
}

async function signIn(name, button) {
  if (button) button.disabled = true;
  const started = await api.post(`/api/integrations/${name}/auth/start`).catch(err => ({ ok: false, detail: err.message }));
  if (!started.ok) { toast(started.detail || 'Sign-in could not start', 'err'); if (button) button.disabled = false; return; }
  toast('A sign-in window opened. Sign in there — it closes by itself.');
  const poll = setInterval(async () => {
    const status = await api.get(`/api/integrations/${name}/auth/status`).catch(() => null);
    if (!status || status.status === 'waiting') return;
    clearInterval(poll);
    toast(status.detail || 'Done', status.status === 'connected' ? 'ok' : 'err');
    if (state.tab === 'connect') loadConnect();
    if (button) button.disabled = false;
  }, 2000);
}

// ==================================================================== boot
// The desktop shell and tray open a specific tab ("Ask Cerebro…", approvals).
window.cerebroSelectTab = name => selectTab(['ask', 'activity', 'sources', 'connect'].includes(name) ? name : 'ask');
selectTab('ask');
connectActivity();
loadInfo();
loadContext();
loadHistory();
setInterval(loadContext, 6000);
setInterval(() => { if (state.tab === 'activity') loadActivity(); }, 15000);
setInterval(loadInfo, 60000);

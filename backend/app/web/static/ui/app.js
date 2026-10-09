// Cerebro app: Ask, Activity, Sources and Connect, for the desktop shell and /app.
import { mascot, setBrainState, setPose, stateLabel } from './brain.js';
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
  patch: (path, body) => api.request('PATCH', path, body || {}),
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

/** "in 20 min", "today 16:00", "tomorrow 08:45", "Mon 08:45" — for times ahead. */
function untilTime(iso) {
  if (!iso) return '';
  const then = new Date(iso);            // task times are local, without a zone
  const minutes = Math.round((then - Date.now()) / 60000);
  if (minutes <= 1) return 'starting';
  if (minutes < 60) return `in ${minutes} min`;
  const time = then.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const today = new Date();
  const days = Math.round((new Date(then.toDateString()) - new Date(today.toDateString())) / 86400000);
  if (days === 0) return `today ${time}`;
  if (days === 1) return `tomorrow ${time}`;
  return `${then.toLocaleDateString([], { weekday: 'short' })} ${time}`;
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
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17v5"/><path d="M9 10.8V4h6v6.8l3 3.2v2H6v-2Z"/></svg>',
  play: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13l10.5-6.5Z"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
};

// -------------------------------------------------------------------- state
const state = {
  history: [],
  chatId: null,         // the chat on screen
  chat: null,           // its record: title, instructions, working…
  chats: [],
  chatTasks: [],
  showArchived: false,
  sending: new Set(),   // chats with an answer on its way from this window
  image: null,          // {name, preview}
  context: {},
  activity: { state: 'idle', detail: 'Idle', active: [] },
  aiEnabled: null,
  tab: 'ask',
  sources: new Map(),   // ref -> source, for citation popovers
};

// ------------------------------------------------------------------- brains
$('#brand-brain').innerHTML = mascot(46);

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
  const items = [
    ['Open dashboard', () => openExternal(`${location.origin}/`)],
    ['Settings', () => openExternal(`${location.origin}/settings`)],
    ['New chat', () => { selectTab('ask'); newChat(); }],
  ];
  if (shell()) {
    items.push(['Window size: small', () => setWindowSize('small')],
               ['Window size: medium', () => setWindowSize('medium')],
               ['Window size: large', () => setWindowSize('large')],
               ['Window size: full height', () => setWindowSize('tall')]);
  }
  items.push(['Text size: larger  (Ctrl +)', () => setZoom(zoom + 0.1)],
             ['Text size: smaller  (Ctrl −)', () => setZoom(zoom - 0.1)],
             ['Text size: reset  (Ctrl 0)', () => setZoom(1)]);
  showMenu(event.currentTarget, items);
});

// -------------------------------------------------------- text size
// Ctrl + / − / 0, remembered. Everything is laid out in CSS pixels, so zoom
// scales the whole interface rather than just the text.
let zoom = 1;
try { zoom = Number(localStorage.getItem('cerebro.zoom')) || 1; } catch { /* ignore */ }
function setZoom(value) {
  zoom = Math.min(1.6, Math.max(0.8, Math.round(value * 10) / 10));
  document.documentElement.style.zoom = zoom === 1 ? '' : String(zoom);
  try { localStorage.setItem('cerebro.zoom', String(zoom)); } catch { /* ignore */ }
  moveIndicator();
}
setZoom(zoom);
document.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return;
  if (event.key === '=' || event.key === '+') { event.preventDefault(); setZoom(zoom + 0.1); }
  else if (event.key === '-') { event.preventDefault(); setZoom(zoom - 0.1); }
  else if (event.key === '0') { event.preventDefault(); setZoom(1); }
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
      !e.target.closest('.cite, #menu-btn, #chat-menu-btn, [data-chat-menu], .source-pill')) closePopover();
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
  $('#max-btn').addEventListener('click', toggleMaximize);
  // Double-click the title bar to expand or restore, like any Windows app.
  $('.titlebar').addEventListener('dblclick', e => {
    if (!e.target.closest('button')) toggleMaximize();
  });
  $$('[data-edge]').forEach(handle => handle.addEventListener('pointerdown', startResize));
  fitToScreen();
  // Brought back from the tray, or the screen changed (a monitor unplugged).
  document.addEventListener('visibilitychange', () => { if (!document.hidden) fitToScreen(); });
  addEventListener('resize', debounce(fitToScreen, 400));
}

function workArea() {
  const s = window.screen;
  return { left: s.availLeft ?? 0, top: s.availTop ?? 0, width: s.availWidth, height: s.availHeight };
}

function fitToScreen() {
  if (document.body.classList.contains('compact') || document.body.classList.contains('maximized')) return;
  shell()?.fit(workArea(), { x: window.screenX, y: window.screenY,
    width: window.outerWidth, height: window.outerHeight });
}

async function toggleMaximize() {
  const maximized = await shell()?.toggle_maximize();
  document.body.classList.toggle('maximized', Boolean(maximized));
  document.body.classList.remove('compact');
  $('#max-btn').setAttribute('aria-pressed', String(Boolean(maximized)));
  $('#max-btn').title = maximized ? 'Restore' : 'Expand';
}

async function setWindowSize(preset) {
  document.body.classList.remove('compact', 'maximized');
  $('#max-btn')?.setAttribute('aria-pressed', 'false');
  await shell()?.set_size(preset, workArea());
}

/** Drag an edge or the corner grip to resize the frameless window. */
function startResize(event) {
  if (!shell() || event.button !== 0 || document.body.classList.contains('compact')) return;
  event.preventDefault();
  const handle = event.currentTarget;
  const edge = handle.dataset.edge;
  handle.setPointerCapture(event.pointerId);
  const start = { px: event.screenX, py: event.screenY, x: window.screenX, y: window.screenY,
    w: window.outerWidth, h: window.outerHeight };
  const area = workArea();
  let pending = null;
  let frame = 0;
  const move = e => {
    const dx = e.screenX - start.px;
    const dy = e.screenY - start.py;
    let { x, y, w, h } = start;
    if (edge.includes('right')) w = start.w + dx;
    if (edge.includes('bottom')) h = start.h + dy;
    if (edge.includes('left')) { w = start.w - dx; x = start.x + dx; }
    w = Math.max(360, Math.min(w, area.width));
    h = Math.max(420, Math.min(h, area.height));
    if (edge.includes('left')) x = start.x + start.w - w;
    pending = [Math.round(x), Math.round(y), Math.round(w), Math.round(h)];
    if (!frame) frame = requestAnimationFrame(() => {
      frame = 0;
      if (pending) shell().set_bounds(...pending);
    });
  };
  const end = () => {
    handle.removeEventListener('pointermove', move);
    handle.removeEventListener('pointerup', end);
    handle.removeEventListener('pointercancel', end);
    document.body.classList.remove('resizing');
    document.body.classList.remove('maximized');
    $('#max-btn')?.setAttribute('aria-pressed', 'false');
  };
  document.body.classList.add('resizing');
  handle.addEventListener('pointermove', move);
  handle.addEventListener('pointerup', end);
  handle.addEventListener('pointercancel', end);
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
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

const busy = () => state.sending.has(state.chatId);

async function loadHistory() {
  const query = state.chatId ? `&conversation_id=${state.chatId}` : '';
  let data;
  try {
    data = await api.get(`/api/chat/history?limit=60${query}`);
  } catch {
    // The remembered chat is gone (deleted elsewhere): fall back to the latest.
    if (state.chatId) { state.chatId = null; return loadHistory(); }
    data = { messages: [] };
  }
  state.history = data.messages || [];
  if (data.conversation) {
    state.chat = data.conversation;
    state.chatId = data.conversation.id;
    try { localStorage.setItem('cerebro.chat', String(state.chatId)); } catch { /* ignore */ }
  }
  renderThread();
  paintChatBar();
  loadChatTasks();
  if (state.chat?.working || busy()) showStillWorking();
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
      <div class="brain-host">${mascot(168)}</div>
      <h2>How can I help?</h2>
      <p>Ask a question, look something up across your sources, or tell me what to do. I'll ask before changing anything.</p>
      <div class="suggestions">
        ${SUGGESTIONS.map(([title, text]) => `
          <button class="suggestion" data-text="${esc(text)}"><b>${esc(title)}</b><span>${esc(text)}</span></button>`).join('')}
      </div>
      <div class="hero-actions">
        <button class="btn ghost sm" data-hero="instructions">${state.chat?.instructions ? 'Edit' : 'Set'} this chat's instructions</button>
        <button class="btn ghost sm" data-hero="task">Assign this chat a task</button>
      </div>
    </div>`;
  setBrainState(thread, state.activity.state);
  $('[data-hero="instructions"]', thread).addEventListener('click', editInstructions);
  $('[data-hero="task"]', thread).addEventListener('click', assignTask);
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
  const fromTask = message.kind === 'task_result' && meta.task;
  node.innerHTML = `
    <div class="avatar">${mascot(34, 'thinking').replace('class="mascot"', 'class="mascot still"')}</div>
    <div class="bubble glass">
      ${fromTask ? `<div class="task-tag">${ICONS.clock}<span>${esc(meta.task.title)}</span>
        <span class="meta">${message.created_at ? relTime(message.created_at) : ''}</span></div>` : ''}
      ${meta.incoming ? `<div class="incoming">
        <div class="incoming-head"><span class="logo sm ${esc(meta.incoming.source)}">${LOGO[meta.incoming.source] || '✉'}</span>
          <b>${esc(meta.incoming.sender || '')}</b>${meta.incoming.chat ? `<span class="meta">${esc(meta.incoming.chat)}</span>` : ''}
          <span class="meta">#${esc(meta.incoming.id)}</span></div>
        ${meta.incoming.subject ? `<div class="incoming-subject">${esc(meta.incoming.subject)}</div>` : ''}
        <div class="incoming-preview">${esc((meta.incoming.preview || '').slice(0, 280))}</div></div>` : ''}
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
      ${card.automatic && status !== 'undone' ? '<span class="badge ok">Applied automatically</span>' : statusBadge(status)}
    </div>
    <div class="diff">${fields.map(diffField).join('')}</div>
    ${card.error && status === 'failed' ? `<div class="card-error">${esc(card.error)}</div>` : ''}
    ${status === 'awaiting_approval' ? `
      <div class="btn-row">
        <button class="btn primary" data-approve>${esc(card.approve_label || 'Approve')}</button>
        <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button>
        ${card.preview?.url ? '<button class="btn ghost" data-open>Open record</button>' : ''}
      </div>` : card.can_undo || card.preview?.url ? `
      <div class="btn-row">
        ${card.can_undo ? '<button class="btn sm" data-undo>Undo</button>' : ''}
        ${card.preview?.url ? '<button class="btn sm ghost" data-open>Open</button>' : ''}
      </div>` : ''}`;
  $('[data-approve]', node)?.addEventListener('click', () => decide(node, `/api/chat/changes/${card.action_id}/approve`));
  $('[data-discard]', node)?.addEventListener('click', () => decide(node, `/api/chat/changes/${card.action_id}/discard`));
  $('[data-undo]', node)?.addEventListener('click', () => decide(node, `/api/chat/changes/${card.action_id}/undo`));
  $('[data-open]', node)?.addEventListener('click', () => openExternal(card.preview.url));
  return node;
}

function statusBadge(status) {
  const map = {
    awaiting_approval: ['warn', 'Needs approval'], done: ['ok', 'Done'], running: ['accent', 'Running'],
    failed: ['err', 'Failed'], discarded: ['', 'Discarded'], queued: ['ok', 'Queued'],
    undone: ['', 'Undone'], sent: ['ok', 'Sent'],
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
  node.className = `action-card ${awaiting ? 'awaiting' : card.status === 'failed' ? 'failed' : 'done'}`;
  const to = card.chat_or_channel || (card.to || []).join(', ');
  const via = card.via === 'browser' ? (card.source === 'teams' ? 'Teams' : 'Outlook') : 'Power Automate';
  node.innerHTML = `
    <div class="action-head"><span class="title">${esc(card.title || 'Draft')}</span>
      <span class="badge accent">${esc(via)}</span>${card.automatic && card.status === 'sent'
        ? '<span class="badge ok">Sent automatically</span>' : statusBadge(card.status || 'awaiting_approval')}</div>
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
    const result = await api.post(`${path}?conversation_id=${state.chatId || ''}`);
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
  $('#send').disabled = busy() || (!input.value.trim() && !state.image);
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
    $('#send').disabled = busy();
    input.focus();
  } catch (error) {
    toast(`Couldn't attach that image: ${error.message}`, 'err');
  }
}

function clearImage() {
  state.image = null;
  $('#attachment').innerHTML = '';
  $('#send').disabled = busy() || !input.value.trim();
}

async function send() {
  const text = input.value.trim();
  if (busy() || (!text && !state.image)) return;
  const image = state.image?.name || null;
  const chatId = state.chatId;
  state.sending.add(chatId);
  paintChatList();
  input.value = '';
  input.style.height = 'auto';
  $('#send').disabled = true;
  if (!state.history.length) thread.innerHTML = '';

  thread.append(messageNode({ role: 'user', content: text || '(image)', image_path: image }));
  clearImage();
  const pending = document.createElement('div');
  pending.className = 'msg assistant short';
  pending.innerHTML = `<div class="avatar"></div>
    <div class="bubble glass"><div class="steps"></div>
      <div class="pending-mascot">${mascot(96, 'thinking')}<span class="pending-caption">Thinking…</span></div></div>`;
  thread.append(pending);
  scrollToEnd();

  const steps = [];
  const paintSteps = () => {
    $('.steps', pending).outerHTML = stepsHTML(steps) || '<div class="steps"></div>';
    scrollToEnd();
  };

  try {
    const final = await streamChat({ message: text, image, conversation_id: chatId }, card => {
      if (card.type !== 'progress') return;
      const running = steps.findLast?.(s => s.title === card.title && s.status === 'running')
        ?? [...steps].reverse().find(s => s.title === card.title && s.status === 'running');
      if (running && card.status !== 'running') Object.assign(running, card);
      else steps.push({ ...card });
      paintSteps();
      // Research reads, work types: the pose follows what Cerebro is doing now.
      const researching = /search|read|recall|open|check|look|find/i.test(card.title || '');
      setPose(pending, card.status === 'running' && researching ? 'studying' : 'working');
      const caption = $('.pending-caption', pending);
      if (caption) caption.textContent = card.status === 'running' ? `${card.title}…` : 'Thinking…';
    });
    state.sending.delete(chatId);
    if (pending.isConnected) {
      pending.remove();
      const message = {
        role: 'assistant', content: final.reply, kind: final.kind, steps: steps.filter(s => s.status !== 'running'),
        meta: { sources: final.sources, cards: final.cards, images: final.images },
      };
      state.history.push({ role: 'user', content: text, image_path: image }, message);
      thread.append(messageNode(message));
      if (final.conversation) { state.chat = { ...state.chat, ...final.conversation }; paintChatBar(); }
      if (final.task) loadChatTasks();
    } else if (state.chatId === chatId) {
      await loadHistory();       // switched away and back while it worked
    } else {
      toast(`Answer ready in “${final.conversation?.title || 'another chat'}”`, 'ok');
    }
  } catch (error) {
    state.sending.delete(chatId);
    if (pending.isConnected) {
      pending.remove();
      thread.append(messageNode({ role: 'assistant', content: `I couldn't do that: ${error.message}` }));
    }
  } finally {
    $('#send').disabled = busy() || !input.value.trim();
    loadChats();
    scrollToEnd();
  }
}

// ================================================================== chats
// Separate conversations, like Copilot or Grok: each has its own thread and
// memory, optional standing instructions, and tasks that post back into it.
const chatsPanel = $('#chats');
const wide = () => matchMedia('(min-width: 760px)').matches;

function openChatList(open = true) {
  chatsPanel.classList.toggle('open', open);
  $('#chats-scrim').classList.toggle('open', open && !wide());
  if (open) { loadChats(); if (!wide()) setTimeout(() => $('#chats-search').focus(), 60); }
}

async function loadChats() {
  const params = new URLSearchParams();
  if (state.showArchived) params.set('archived', 'true');
  const q = $('#chats-search').value.trim();
  if (q) params.set('q', q);
  try {
    state.chats = (await api.get(`/api/chat/conversations?${params}`)).conversations || [];
  } catch { state.chats = []; }
  paintChatList();
}

function paintChatList() {
  const list = $('#chats-list');
  if (!state.chats.length) {
    list.innerHTML = `<div class="empty-note">${state.showArchived ? 'No archived chats.' : 'No chats yet.'}</div>`;
    return;
  }
  const pinned = state.chats.filter(c => c.pinned);
  const rest = state.chats.filter(c => !c.pinned);
  const row = chat => `
    <div class="chat-row${chat.id === state.chatId ? ' current' : ''}" data-chat="${chat.id}" tabindex="0" role="button">
      <div class="line">
        ${(chat.working || state.sending.has(chat.id)) ? '<span class="working-dot on"></span>' : ''}
        <span class="name">${esc(chat.title)}</span>
        <span class="when">${relTime(chat.last_message_at || chat.created_at).replace(' ago', '')}</span>
      </div>
      <div class="line">
        <span class="preview">${esc(chat.preview || (chat.instructions ? `Instructions: ${chat.instructions}` : 'Empty'))}</span>
        ${chat.has_tasks ? `<span class="flag" title="Has scheduled tasks">${ICONS.clock}</span>` : ''}
      </div>
      <button class="icon-btn row-menu" data-chat-menu="${chat.id}" title="Options" aria-label="Chat options">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/></svg></button>
    </div>`;
  list.innerHTML = (pinned.length ? `<div class="chats-group">Pinned</div>${pinned.map(row).join('')}` : '')
    + (rest.length ? `${pinned.length ? '<div class="chats-group">Recent</div>' : ''}${rest.map(row).join('')}` : '');
}

$('#chats-list').addEventListener('click', event => {
  const menu = event.target.closest('[data-chat-menu]');
  if (menu) {
    event.stopPropagation();
    const chat = state.chats.find(c => c.id === Number(menu.dataset.chatMenu));
    return chatMenu(menu, chat);
  }
  const row = event.target.closest('[data-chat]');
  if (row) openChat(Number(row.dataset.chat));
});
$('#chats-list').addEventListener('keydown', event => {
  const row = event.target.closest('[data-chat]');
  if (row && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); openChat(Number(row.dataset.chat)); }
});

async function openChat(id) {
  if (!wide()) openChatList(false);
  if (id === state.chatId && state.history.length) return;
  state.chatId = id;
  clearImage();
  await loadHistory();
  paintChatList();
  input.focus();
}

async function newChat() {
  // An empty chat is reused rather than piling up blank ones.
  if (state.chatId && !state.history.length && !state.chat?.archived && !busy()) {
    if (!wide()) openChatList(false);
    input.focus();
    return;
  }
  try {
    const chat = await api.post('/api/chat/conversations');
    state.showArchived = false;
    $('#chats-archived').setAttribute('aria-pressed', 'false');
    $('#chats-archived').textContent = 'Show archived';
    await openChat(chat.id);
    loadChats();
  } catch (error) { toast(error.message, 'err'); }
}

function paintChatBar() {
  const chat = state.chat || {};
  $('#chat-title-text').textContent = chat.title || 'New chat';
  $('#chat-working').classList.toggle('on', Boolean(chat.working) || busy());
  $('#chat-title').classList.toggle('archived', Boolean(chat.archived));
  paintChatExtras();
}

function paintChatExtras() {
  const chat = state.chat || {};
  const host = $('#chat-extras');
  const parts = [];
  if (chat.archived) {
    parts.push(`<div class="extra-chip warn">This chat was ended. <button class="link" data-extra="restore">Restore it</button> or <button class="link" data-extra="new">start a new chat</button>.</div>`);
  }
  if (chat.instructions) {
    parts.push(`<button class="extra-chip" data-extra="instructions" title="Edit this chat's instructions">
      <b>Instructions</b><span>${esc(chat.instructions)}</span></button>`);
  }
  state.chatTasks.forEach(task => {
    const when = task.status === 'needs_review' ? 'needs your review'
      : task.status === 'failed' ? 'last run failed'
      : task.next_run ? `next ${untilTime(task.next_run)}`.replace('next starting', 'starting')
      : task.schedule === 'manual' ? 'when you run it' : (task.run_count ? 'done' : '');
    parts.push(`<div class="extra-chip task" data-task="${task.id}">${ICONS.clock}
      <span class="grow"><b>${esc(task.title)}</b><span>${esc(SCHEDULE_LABEL[task.schedule] || task.schedule)}${task.at_time ? ` ${esc(task.at_time)}` : ''} · ${esc(when)}</span></span>
      <button class="icon-btn" data-task-run="${task.id}" title="Run now">${ICONS.play}</button>
      <button class="icon-btn" data-task-stop="${task.id}" title="Stop this task">${ICONS.stop}</button></div>`);
  });
  host.innerHTML = parts.join('');
  host.hidden = !parts.length;
}

const SCHEDULE_LABEL = { once: 'Once', hourly: 'Hourly', daily: 'Daily', weekdays: 'Weekdays',
  weekly: 'Weekly', manual: 'On demand' };

$('#chat-extras').addEventListener('click', async event => {
  const action = event.target.closest('[data-extra]')?.dataset.extra;
  if (action === 'instructions') return editInstructions();
  if (action === 'new') return newChat();
  if (action === 'restore') return updateChat(state.chatId, { archived: false }, 'Chat restored');
  const run = event.target.closest('[data-task-run]')?.dataset.taskRun;
  if (run) {
    try {
      await api.post(`/api/chat/conversations/${state.chatId}/tasks/${run}/run`);
      toast('Running — the result will appear here', 'ok');
      state.chat = { ...state.chat, working: true };
      paintChatBar();
      showStillWorking();
    } catch (error) { toast(error.message, 'err'); }
    return;
  }
  const stop = event.target.closest('[data-task-stop]')?.dataset.taskStop;
  if (stop) {
    try {
      await api.post(`/api/tasks/${stop}/cancel`);
      toast('Task stopped', 'ok');
      loadChatTasks();
      loadChats();
    } catch (error) { toast(error.message, 'err'); }
  }
});

async function loadChatTasks() {
  if (!state.chatId) return;
  try {
    state.chatTasks = (await api.get(`/api/chat/conversations/${state.chatId}/tasks`)).tasks || [];
  } catch { state.chatTasks = []; }
  paintChatExtras();
}

/** A task or an answer started elsewhere is still running in this chat. */
let watchTimer = null;
function showStillWorking() {
  if (!$('.msg.still-working', thread)) {
    if (!state.history.length) thread.innerHTML = '';
    const node = document.createElement('div');
    node.className = 'msg assistant short still-working';
    node.innerHTML = `<div class="avatar"></div><div class="bubble glass"><div class="pending-mascot">
      ${mascot(96, 'thinking')}<span class="pending-caption">Working on it…</span></div></div>`;
    thread.append(node);
    setPose(node, 'working');
    scrollToEnd();
  }
  clearInterval(watchTimer);
  const watching = state.chatId;
  watchTimer = setInterval(async () => {
    if (state.chatId !== watching) return clearInterval(watchTimer);
    const chat = await api.get(`/api/chat/conversations/${watching}`).catch(() => null);
    if (chat && !chat.working) {
      clearInterval(watchTimer);
      await loadHistory();
      loadChats();
    }
  }, 2500);
}

async function updateChat(id, fields, done) {
  try {
    const chat = await api.patch(`/api/chat/conversations/${id}`, fields);
    if (id === state.chatId) { state.chat = { ...state.chat, ...chat }; paintChatBar(); }
    if (done) toast(done, 'ok');
    loadChats();
    return chat;
  } catch (error) { toast(error.message, 'err'); return null; }
}

function chatMenu(anchor, chat = state.chat) {
  if (!chat) return;
  const id = chat.id;
  const items = [
    ['Rename…', () => renameChat(chat)],
    [chat.instructions ? 'Edit instructions…' : 'Set instructions…', () => editInstructions(chat)],
    ['Assign a task…', () => assignTask(chat)],
    [chat.pinned ? 'Unpin' : 'Pin to top', () => updateChat(id, { pinned: !chat.pinned })],
  ];
  if (chat.archived) items.push(['Restore', () => updateChat(id, { archived: false }, 'Chat restored')]);
  else items.push(['End conversation', () => endChat(chat)]);
  items.push(['Delete…', () => deleteChat(chat)]);
  showMenu(anchor, items);
}

async function renameChat(chat = state.chat) {
  const values = await sheet({
    title: 'Rename chat', ok: 'Rename',
    body: `<label class="field"><span>Name</span><input name="title" maxlength="120" value="${esc(chat.title || '')}" required></label>`,
  });
  if (values) updateChat(chat.id, { title: values.title }, 'Renamed');
}

async function editInstructions(chat = state.chat) {
  if (!chat || chat instanceof Event) chat = state.chat;
  const values = await sheet({
    title: 'Instructions for this chat', ok: 'Save',
    body: `<p class="sheet-note">Cerebro follows these for every message in “${esc(chat.title || 'New chat')}” — e.g. “Answer in Spanish”, “This chat is about case CAS-01234 for Contoso”, “Keep replies to three bullet points”.</p>
      <label class="field"><span>Instructions</span><textarea name="instructions" rows="5" placeholder="Leave empty for none">${esc(chat.instructions || '')}</textarea></label>`,
  });
  if (values) updateChat(chat.id, { instructions: values.instructions }, values.instructions.trim() ? 'Instructions saved' : 'Instructions cleared');
}

async function assignTask(chat = state.chat) {
  if (!chat || chat instanceof Event) chat = state.chat;
  const values = await sheet({
    title: 'Assign a task to this chat', ok: 'Assign',
    body: `<p class="sheet-note">Cerebro does this with the same tools as Ask — your knowledge base, Dynamics, RightAnswers, SharePoint and inbox — and posts the result in this chat. Anything it would change waits here for your approval.</p>
      <label class="field"><span>What should it do?</span><textarea name="instruction" rows="3" required placeholder="List new Dynamics cases assigned to me and flag any that mention data loss"></textarea></label>
      <div class="field-row">
        <label class="field"><span>When</span><select name="when">
          <option value="now">Now, once</option>
          <option value="once">Once, later today</option>
          <option value="daily">Every day</option>
          <option value="weekdays">Every weekday</option>
          <option value="weekly">Every week</option>
          <option value="hourly">Every hour</option>
          <option value="manual">Only when I run it</option>
        </select></label>
        <label class="field" data-time><span>At</span><input name="at_time" type="time" value="09:00"></label>
      </div>
      <label class="check-row"><input type="checkbox" name="run_now"> Also run it once right now</label>`,
    setup: form => {
      const when = $('[name="when"]', form);
      const sync = () => {
        $('[data-time]', form).hidden = ['now', 'hourly', 'manual'].includes(when.value);
        $('.check-row', form).hidden = when.value === 'now';
      };
      when.addEventListener('change', sync);
      sync();
    },
  });
  if (!values) return;
  const now = values.when === 'now';
  try {
    const result = await api.post(`/api/chat/conversations/${chat.id}/tasks`, {
      instruction: values.instruction,
      schedule: now ? 'once' : values.when,
      at_time: ['now', 'hourly', 'manual'].includes(values.when) ? null : values.at_time || null,
      run_now: now || values.run_now === 'on',
    });
    toast(result.confirmation || 'Task assigned', 'ok');
    if (chat.id !== state.chatId) await openChat(chat.id);
    loadChatTasks();
    loadChats();
    if (now || values.run_now === 'on') {
      state.chat = { ...state.chat, working: true };
      paintChatBar();
      showStillWorking();
    }
  } catch (error) { toast(error.message, 'err'); }
}

async function endChat(chat = state.chat) {
  const scheduled = chat.id === state.chatId ? state.chatTasks.some(t => t.status === 'active') : chat.has_tasks;
  if (scheduled && !await sheet({
    title: 'End this conversation?', ok: 'End conversation',
    body: `<p class="sheet-note">“${esc(chat.title || 'New chat')}” moves to Archived, where you can still read or restore it. Its scheduled tasks will stop.</p>`,
  })) return;
  try {
    const result = await api.post(`/api/chat/conversations/${chat.id}/end`);
    toast(`Ended “${result.ended.title}” — it's under Archived`
      + (result.tasks_stopped ? ` and its ${result.tasks_stopped} task(s) stopped` : ''), 'ok');
    if (chat.id === state.chatId) await openChat(result.conversation.id);
    loadChats();
  } catch (error) { toast(error.message, 'err'); }
}

async function deleteChat(chat = state.chat) {
  const values = await sheet({
    title: 'Delete this chat?', ok: 'Delete', danger: true,
    body: `<p class="sheet-note">“${esc(chat.title || 'New chat')}” and its messages will be deleted, and its tasks stopped. This can't be undone — <b>End conversation</b> keeps it in Archived instead.</p>`,
  });
  if (!values) return;
  try {
    await api.del(`/api/chat/conversations/${chat.id}`);
    toast('Chat deleted', 'ok');
    if (chat.id === state.chatId) { state.chatId = null; await loadHistory(); }
    loadChats();
  } catch (error) { toast(error.message, 'err'); }
}

/** A small modal form. Resolves to its values, or null when cancelled. */
function sheet({ title, body, ok = 'Save', danger = false, setup }) {
  const host = $('#sheet-host');
  const form = $('#sheet');
  $('#sheet-title').textContent = title;
  $('#sheet-body').innerHTML = body;
  const okButton = $('#sheet-ok');
  okButton.textContent = ok;
  okButton.classList.toggle('danger-fill', danger);
  host.hidden = false;
  setup?.(form);
  setTimeout(() => $('input, textarea, select', $('#sheet-body'))?.focus(), 30);
  return new Promise(resolve => {
    const close = value => {
      host.hidden = true;
      form.removeEventListener('submit', submit);
      $('#sheet-cancel').removeEventListener('click', cancel);
      host.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', escape, true);
      resolve(value);
    };
    const submit = event => { event.preventDefault(); close(Object.fromEntries(new FormData(form))); };
    const cancel = () => close(null);
    const outside = event => { if (event.target === host) close(null); };
    const escape = event => { if (event.key === 'Escape') { event.stopPropagation(); close(null); } };
    form.addEventListener('submit', submit);
    $('#sheet-cancel').addEventListener('click', cancel);
    host.addEventListener('mousedown', outside);
    document.addEventListener('keydown', escape, true);
  });
}

$('#chats-btn').addEventListener('click', () => openChatList(!chatsPanel.classList.contains('open')));
$('#chats-close').addEventListener('click', () => openChatList(false));
$('#chats-scrim').addEventListener('click', () => openChatList(false));
$('#chats-new').addEventListener('click', newChat);
$('#new-chat-btn').addEventListener('click', newChat);
$('#chat-title').addEventListener('click', () => state.chat && renameChat());
$('#chat-menu-btn').addEventListener('click', event => chatMenu(event.currentTarget));
let chatSearchTimer = null;
$('#chats-search').addEventListener('input', () => {
  clearTimeout(chatSearchTimer);
  chatSearchTimer = setTimeout(loadChats, 200);
});
$('#chats-archived').addEventListener('click', event => {
  state.showArchived = !state.showArchived;
  event.currentTarget.setAttribute('aria-pressed', String(state.showArchived));
  event.currentTarget.textContent = state.showArchived ? 'Back to chats' : 'Show archived';
  loadChats();
});
document.addEventListener('keydown', event => {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || !$('#sheet-host').hidden) return;
  const key = event.key.toLowerCase();
  if (key === 'n') { event.preventDefault(); selectTab('ask'); newChat(); }
  if (key === 'k') {
    event.preventDefault();
    selectTab('ask');
    if (wide()) $('#chats-search').focus(); else openChatList(!chatsPanel.classList.contains('open'));
  }
  if (event.key === 'Escape') openChatList(false);
});
addEventListener('resize', () => { if (wide()) $('#chats-scrim').classList.remove('open'); });

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
      <div class="now glass" id="now-card"><div class="brain-host">${mascot(84)}</div>
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
  const messages = await api.get('/api/enterprise/messages?unhandled_only=true&limit=8')
    .catch(() => ({ messages: [] }));
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

  if ((messages.messages || []).length) {
    lists.insertAdjacentHTML('beforeend', `<div class="section-title">New messages<span class="grow"></span></div>`);
    messages.messages.forEach(message => {
      const row = document.createElement('div');
      row.className = 'card glass message-row';
      const flags = [
        message.urgency === 'high' ? '<span class="badge err">Urgent</span>' : '',
        message.mentioned ? '<span class="badge accent">@you</span>' : '',
        message.direct && !message.mentioned ? '<span class="badge">To you</span>' : '',
        message.case_id ? `<span class="badge">${esc(message.case_id)}</span>` : '',
      ].join('');
      row.innerHTML = `<div class="list-row">
        <div class="logo sm ${esc(message.source)}">${LOGO[message.source] || '✉'}</div>
        <div class="grow"><div class="title">${esc(message.sender_name || message.sender || 'Someone')}${message.chat_or_channel ? ` · ${esc(message.chat_or_channel)}` : ''}</div>
          <div class="meta">${esc(message.subject || message.preview || '')}</div></div>
        <button class="btn sm" data-look>Look into it</button></div>
        ${flags ? `<div class="flags">${flags}</div>` : ''}`;
      $('[data-look]', row).addEventListener('click', async () => {
        selectTab('ask');
        const inbox = state.chats.find(c => c.title === 'Inbox' && !c.archived);
        if (inbox) await openChat(inbox.id);
        input.value = `Look into message #${message.id} from ${message.sender_name || message.sender || 'them'} and suggest a reply.`;
        send();
      });
      lists.append(row);
    });
  }

  if (recent.length) {
    lists.insertAdjacentHTML('beforeend', `<div class="section-title">Recent changes<span class="grow"></span></div>`);
    recent.forEach(change => {
      const row = document.createElement('div');
      row.className = 'card glass';
      row.innerHTML = `<div class="list-row"><div class="grow"><div class="title">${esc(change.title)}</div>
        <div class="meta">${esc(INTEGRATION_LABEL[change.integration] || change.integration)} · ${relTime(change.created_at)}${change.automatic ? ' · applied automatically' : ''}${change.error ? ` · ${esc(change.error)}` : ''}</div></div>
        ${change.can_undo ? '<button class="btn sm" data-undo>Undo</button>' : ''}${statusBadge(change.status)}</div>`;
      $('[data-undo]', row)?.addEventListener('click', async event => {
        event.currentTarget.disabled = true;
        const result = await api.post(`/api/chat/changes/${change.id}/undo`).catch(e => ({ reply: e.message, cards: [{ status: 'error' }] }));
        toast(result.reply || 'Done', result.cards?.some(c => c.status === 'error') ? 'err' : 'ok');
        loadActivity();
      });
      lists.append(row);
    });
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
const LOGO = { dynamics: 'D365', rightanswers: 'RA', sharepoint: 'SP', outlook: 'OL', teams: 'T', beyondtrust: 'BT', genesys: 'GC' };
const MESSAGING = new Set(['outlook', 'teams']);

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
  state.monitor = data.monitor || state.monitor;
  (data.integrations || []).forEach(item => list.append(integrationCard(item)));
}

function integrationCard(item) {
  const card = document.createElement('div');
  card.className = 'card glass integration';
  const signedIn = item.signed_in === true;
  const waiting = item.sign_in?.status === 'waiting';
  const badge = !item.enabled ? '<span class="badge">Off</span>'
    : waiting ? '<span class="badge warn">Signing in…</span>'
    : signedIn ? `<span class="badge ok">${item.kind === 'api' ? 'Connected' : 'Signed in'}</span>`
    : item.signed_in === false ? `<span class="badge err">${item.kind === 'api' ? 'Check failed' : 'Sign-in needed'}</span>`
    : '<span class="badge">Not checked</span>';
  card.innerHTML = `
    <div class="logo ${item.name}">${LOGO[item.name] || item.label[0]}</div>
    <div style="min-width:0"><h4>${esc(item.label)} ${badge}</h4>
      <div class="meta">${item.account ? `Signed in as ${esc(item.account)} · ` : ''}${item.url ? esc(item.url) : 'Add its address in Settings'}${item.checked_at ? ` · checked ${relTime(new Date(item.checked_at * 1000).toISOString())}` : ''}</div>
      ${item.sign_in?.detail && item.sign_in.status !== 'idle' ? `<p>${esc(item.sign_in.detail)}</p>` : ''}
      ${item.enabled && item.can_auto_apply ? (MESSAGING.has(item.name) ? `
        <label class="auto-apply" title="Off: every message waits for your approval. On: replies in an existing thread go straight away; a new message to someone still asks.">
          <span class="switch"><input type="checkbox" data-auto ${item.auto_apply ? 'checked' : ''}><span></span></span>
          Send replies without asking</label>` : `
        <label class="auto-apply" title="Off: every change waits for your approval. On: changes are made straight away and can be undone from their card.">
          <span class="switch"><input type="checkbox" data-auto ${item.auto_apply ? 'checked' : ''}><span></span></span>
          Apply changes automatically</label>`) : ''}
      ${item.enabled && MESSAGING.has(item.name) ? `
        <label class="auto-apply" title="Check for new messages in the background and tell you about the important ones.">
          <span class="switch"><input type="checkbox" data-monitor ${state.monitor?.enabled ? 'checked' : ''}><span></span></span>
          Watch for new messages</label>` : ''}</div>
    <div class="btn-row" style="margin:0;flex-direction:column">
      ${item.enabled ? `<button class="btn sm ${signedIn ? '' : 'primary'}" data-signin>${item.kind === 'api' ? 'Test connection' : signedIn ? 'Sign in again' : 'Sign in'}</button>
      ${item.kind === 'api' ? '<button class="btn sm ghost" data-settings>Settings</button>' : '<button class="btn sm ghost" data-check>Check</button>'}
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
  $('[data-monitor]', card)?.addEventListener('change', async e => {
    const enabled = e.currentTarget.checked;
    try {
      await api.post('/api/integrations/monitor', { enabled });
      state.monitor = { ...(state.monitor || {}), enabled };
      toast(enabled ? 'Watching Outlook and Teams for new messages' : 'Stopped watching Outlook and Teams', 'ok');
      loadConnect();
    } catch (error) {
      e.currentTarget.checked = !enabled;
      toast(error.message, 'err');
    }
  });
  $('[data-auto]', card)?.addEventListener('change', async e => {
    const enabled = e.currentTarget.checked;
    try {
      await api.post(`/api/integrations/${item.name}/auto-apply`, { enabled });
      toast(MESSAGING.has(item.name)
        ? (enabled ? `${item.label} replies will be sent without asking; new conversations still ask`
          : `Every ${item.label} message will wait for your approval`)
        : enabled ? `${item.label} changes will be made straight away — each can be undone`
        : `${item.label} changes will wait for your approval`, 'ok');
    } catch (error) {
      e.currentTarget.checked = !enabled;
      toast(error.message, 'err');
    }
  });
  $('[data-check]', card)?.addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    const result = await api.post(`/api/integrations/${item.name}/check`).catch(err => ({ detail: err.message }));
    toast(result.detail, result.ok ? 'ok' : 'err');
    loadConnect();
  });
  $('[data-settings]', card)?.addEventListener('click', () => openExternal(`${location.origin}/settings#${item.kind === 'api' ? 'systems' : 'integrations'}`));
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
  // API systems (BeyondTrust, Genesys) have no window: the credentials were checked right now.
  if (started.status === 'connected') {
    toast(started.detail || 'Connected', 'ok');
    if (state.tab === 'connect') loadConnect();
    if (button) button.disabled = false;
    return;
  }
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
// A notification's "Open" (the tray) lands in the chat it is about.
window.cerebroOpenChat = id => { selectTab('ask'); openChat(Number(id)); };
selectTab('ask');
connectActivity();
loadInfo();
loadContext();
try { state.chatId = Number(localStorage.getItem('cerebro.chat')) || null; } catch { /* ignore */ }
loadHistory().then(loadChats);
setInterval(loadContext, 6000);
// Scheduled tasks post into chats on their own; keep the list (and an idle
// open chat) current.
setInterval(async () => {
  if (document.hidden) return;
  const before = state.chats.find(c => c.id === state.chatId)?.last_message_at;
  await loadChats();
  const after = state.chats.find(c => c.id === state.chatId)?.last_message_at;
  if (after && before && after !== before && !busy() && state.tab === 'ask') loadHistory();
}, 20000);
setInterval(() => { if (state.tab === 'activity') loadActivity(); }, 15000);
setInterval(loadInfo, 60000);

// Cerebro app: Ask, Activity, Sources and Connect, for the desktop shell and /app.
import { mascot, setBrainState, setPose, stateLabel } from './brain.js';
import { escapeHTML as esc, renderMarkdown } from './markdown.js';
import { animeSVG, createFace, faceAvatar, lipSync, PALETTE_NAMES, HAIR_NAMES, HAIRSTYLES, EXPRESSIONS, MODEL_SLOTS } from './face.js';

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
  agents: [],           // the user's agents (shared memory, own chats and jobs)
  agentId: null,        // the agent on screen
  groups: {},           // tool-scope groups, for the agent editor
  templates: {},
  stageFace: null,      // the agent's face above its chat
  home: null,
};
const currentAgent = () => state.agents.find(a => a.id === state.agentId) || state.agents[0] || null;

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
  if (name === 'home') loadHome();
  if (name === 'work') loadWork();
  if (name === 'ask') $('#input').focus();
  openSidebar(false);
}

function moveIndicator() {
  const active = $('.tab[aria-selected="true"]');
  const indicator = $('#tab-indicator');
  if (!active || !indicator || getComputedStyle(indicator).display === 'none') return;
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
      !e.target.closest('.cite, #menu-btn, #chat-menu-btn, [data-chat-menu], .source-pill, #addon-add, #addon-menu, #agent-new, #home-new-agent, .agent-row')) closePopover();
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
  paintStage();
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
      ${currentAgent()?.face === 'brain' || !currentAgent() ? `<div class="brain-host">${mascot(168)}</div>` : ''}
      <h2>${currentAgent() && !currentAgent().is_default ? `${esc(currentAgent().name)} — how can I help?` : 'How can I help?'}</h2>
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
    const files = (meta.attachments || []).length ? `<div class="file-chips">${meta.attachments.map(f => fileChip(f)).join('')}</div>` : '';
    node.innerHTML = `<div class="bubble">${image}${files}${esc(message.content).replace(/\n/g, '<br>')}</div>`;
    return node;
  }
  (meta.sources || []).forEach(source => source.ref && state.sources.set(source.ref, source));
  const short = (message.content || '').length < 60 && !(meta.cards || []).some(c => c.type !== 'progress' && c.type !== 'completion');
  if (short) node.classList.add('short');
  const fromTask = message.kind === 'task_result' && meta.task;
  node.innerHTML = `
    <div class="avatar">${faceAvatar(currentAgent(), 34)}</div>
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
  if (card.type === 'addon_signin') return addonSigninCard(card);
  if (card.type === 'plan') return planCard(card);
  if (card.type === 'keep_updated') return keepCard(card);
  if (card.type === 'todo') return todoCard(card);
  return null;
}

const KEEP_KIND = { file: 'Document', site: 'Website', web: 'Website', sharepoint: 'SharePoint', app: 'Desktop app', addon: 'Add-on' };
function keepCard(card) {
  const node = document.createElement('div');
  node.className = `action-card keep ${card.status === 'paused' ? 'paused' : ''}`;
  const paint = c => {
    const paused = c.status === 'paused', stopped = c.status === 'cancelled';
    node.innerHTML = `
      <div class="action-head"><span class="title">Keeping ${esc(c.label || 'it')} updated</span>
        <span class="badge accent">${esc(KEEP_KIND[c.kind] || 'Update')}</span>
        ${stopped ? '<span class="badge">Stopped</span>' : paused ? '<span class="badge">Paused</span>'
          : c.status === 'waiting_approval' ? '<span class="badge warn">Needs approval</span>'
          : c.status === 'running' ? '<span class="badge accent">Updating…</span>' : '<span class="badge ok">On</span>'}</div>
      <div class="keep-grid">
        <span class="meta">How often</span><span>${esc(c.when || '')}</span>
        ${c.source ? `<span class="meta">From</span><span class="keep-path" title="${esc(c.source)}">${esc(c.source.split(/[\\/]/).filter(Boolean).pop() || c.source)}</span>` : ''}
        <span class="meta">Last run</span><span>${c.last_run ? relTime(c.last_run) : 'Not yet'}</span>
        <span class="meta">Next run</span><span>${c.next_run ? relTime(c.next_run) : (c.source ? 'When the source changes' : '—')}</span>
        ${c.learned ? '<span class="meta">Steps</span><span>Saved as a skill — later runs follow them</span>' : ''}
      </div>
      ${c.offer_auto ? `<div class="keep-offer">It worked. <button class="btn sm primary" data-auto-on>Let it update by itself from now on</button></div>` : ''}
      ${c.can_auto_apply ? `<label class="switch-row"><input type="checkbox" data-auto ${c.auto_apply ? 'checked' : ''}>
        <span>Apply changes automatically</span><span class="meta">${c.auto_apply ? 'Saved straight away, logged with screenshots' : 'Each change waits for your approval'}</span></label>` : ''}
      ${stopped ? '' : `<div class="btn-row">
        <button class="btn sm" data-run>Run now</button>
        <button class="btn sm ghost" data-pause>${paused ? 'Resume' : 'Pause'}</button>
        <button class="btn sm ghost" data-stop>Stop</button></div>`}`;
    const act = async (action, label) => {
      try { await api.post(`/api/jobs/${c.task_id}/${action}`); toast(label, 'ok'); refresh(); }
      catch (error) { toast(error.message, 'err'); }
    };
    $('[data-run]', node)?.addEventListener('click', () => act('run', 'Updating now'));
    $('[data-pause]', node)?.addEventListener('click', () => act(paused ? 'resume' : 'pause', paused ? 'Resumed' : 'Paused'));
    $('[data-stop]', node)?.addEventListener('click', () => act('cancel', 'Stopped'));
    const setAuto = async enabled => {
      try { paint(await api.post(`/api/updates/${c.task_id}/auto-apply`, { enabled })); toast(enabled ? 'It updates by itself now' : 'Changes wait for approval', 'ok'); }
      catch (error) { toast(error.message, 'err'); }
    };
    $('[data-auto]', node)?.addEventListener('change', e => setAuto(e.target.checked));
    $('[data-auto-on]', node)?.addEventListener('click', () => setAuto(true));
  };
  const refresh = async () => { try { paint(await api.get(`/api/updates/${card.task_id}`)); } catch { /* gone */ } };
  paint(card);
  return node;
}

function todoCard(card) {
  const item = card.item || {};
  const node = document.createElement('div');
  node.className = 'action-card';
  node.innerHTML = `<div class="action-head"><span class="title">${esc(item.what || '')}</span>
    <span class="badge ok">Added to your to-dos</span></div>
    ${item.due_at ? `<div class="meta">Due ${esc(new Date(item.due_at).toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }))}</div>` : ''}`;
  return node;
}

const INTEGRATION_LABEL = { dynamics: 'Dynamics 365', rightanswers: 'RightAnswers', sharepoint: 'SharePoint', beyondtrust: 'BeyondTrust', genesys: 'Genesys Cloud', addons: 'Add-ons', browser: 'Website', files: 'Files', computer: 'This PC', documents: 'Word & Excel', calendar: 'Calendar' };
const integrationLabel = name => INTEGRATION_LABEL[name] || (/^(mcp|app):/.test(String(name || '')) ? String(name).slice(4) : name) || 'Change';

function planCard(card) {
  const node = document.createElement('div');
  node.className = 'action-card plan';
  node.innerHTML = `<div class="action-head"><span class="title">${esc(card.title || 'Plan')}</span><span class="badge accent">Plan</span></div>
    <ol class="plan-steps">${(card.steps || []).map(step => `<li>${esc(step)}</li>`).join('')}</ol>`;
  return node;
}

function addonSigninCard(card) {
  const node = document.createElement('div');
  node.className = 'action-card awaiting';
  node.innerHTML = `
    <div class="action-head"><span class="title">${esc(card.title)}</span><span class="badge warn">Sign-in needed</span></div>
    <p style="margin:0;color:var(--text-dim);font-size:12.5px">${esc(card.detail || '')}</p>
    <div class="btn-row"><button class="btn primary" data-signin>Sign in</button></div>`;
  $('[data-signin]', node).addEventListener('click', e => addonSignIn(card.name, e.currentTarget));
  return node;
}

function changeCard(card) {
  const node = document.createElement('div');
  const status = card.status || 'awaiting_approval';
  node.className = `action-card ${status === 'awaiting_approval' ? 'awaiting' : status}`;
  const fields = card.preview?.fields || [];
  node.innerHTML = `
    <div class="action-head">
      <span class="title">${esc(card.title)}</span>
      <span class="badge accent">${esc(integrationLabel(card.integration))}</span>
      ${card.automatic && status !== 'undone' ? '<span class="badge ok">Applied automatically</span>' : statusBadge(status)}
    </div>
    ${card.unconfirmed ? '<div class="card-error">It may not have gone through — the page or window didn\'t change. Check it before trying again.</div>' : ''}
    ${card.preview?.image || card.after_image ? `<div class="card-shots">
      ${card.preview?.image ? `<figure><img class="card-shot" src="/api/chat/image/${encodeURIComponent(card.preview.image)}" alt="Before"><figcaption>Before</figcaption></figure>` : ''}
      ${card.after_image ? `<figure><img class="card-shot" src="/api/chat/image/${encodeURIComponent(card.after_image)}" alt="After"><figcaption>After</figcaption></figure>` : ''}
    </div>` : ''}
    <div class="diff">${fields.map(diffField).join('')}</div>
    ${card.error && status === 'failed' ? `<div class="card-error">${esc(card.error)}</div>` : ''}
    ${status === 'awaiting_approval' ? `
      <div class="btn-row">
        <button class="btn primary" data-approve>${esc(card.approve_label || 'Approve')}</button>
        <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button>
        ${card.preview?.url ? '<button class="btn ghost" data-open>Open record</button>' : ''}
      </div>` : status === 'failed' ? `
      <div class="btn-row"><button class="btn primary" data-approve>Retry</button>
        <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button></div>` : card.can_undo || card.preview?.url ? `
      <div class="btn-row">
        ${card.can_undo ? `<button class="btn sm" data-undo>${esc(card.undo_label || (card.tool === 'capability_added' ? 'Remove' : 'Undo'))}</button>` : ''}
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
  const failed = card.status === 'failed';
  node.className = `action-card ${awaiting ? 'awaiting' : failed ? 'failed' : 'done'}`;
  const to = card.chat_or_channel || (card.to || []).join(', ');
  const via = card.via === 'browser' ? (card.source === 'teams' ? 'Teams' : 'Outlook') : 'Power Automate';
  node.innerHTML = `
    <div class="action-head"><span class="title">${esc(card.title || 'Draft')}</span>
      <span class="badge accent">${esc(via)}</span>${card.automatic && card.status === 'sent'
        ? '<span class="badge ok">Sent automatically</span>' : statusBadge(card.status || 'awaiting_approval')}</div>
    <div class="meta" style="font-size:11.5px;color:var(--text-faint);margin-bottom:6px">${to ? `To ${esc(to)}` : ''}${card.subject ? ` · ${esc(card.subject)}` : ''}</div>
    <div class="draft-body">${esc(card.body || '')}</div>
    ${failed ? `<div class="card-error">${esc(card.detail_status || 'It was not sent.')}</div>
      <div class="btn-row"><input class="input" data-to value="${esc(to)}" placeholder="Who to send it to (name or email)" style="flex:1;min-width:0">
      <button class="btn primary" data-approve>Retry</button>
      <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button></div>` : ''}
    ${awaiting ? `<div class="btn-row">
      <button class="btn primary" data-approve>${esc(card.approve_label || 'Approve and send')}</button>
      <button class="btn ghost" data-discard>${esc(card.discard_label || 'Discard')}</button></div>` : ''}`;
  $('[data-approve]', node)?.addEventListener('click', () => {
    const edited = ($('[data-to]', node)?.value || '').trim();
    const changed = failed && edited && edited !== to;
    decide(node, `/api/chat/actions/${card.action_id}/approve`, changed ? `&to=${encodeURIComponent(edited)}` : '');
  });
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

async function decide(node, path, extra = '') {
  $$('button', node).forEach(button => { button.disabled = true; });
  try {
    const result = await api.post(`${path}?conversation_id=${state.chatId || ''}${extra}`);
    const failed = result.cards?.some(c => c.status === 'error' || c.status === 'failed');
    toast(result.reply || 'Done', failed ? 'err' : 'ok');
    if (/\/approve$/.test(path)) react(failed ? 'sorry' : 'smug');
    else if (/\/discard$/.test(path)) react('pout', 1800);
  } catch (error) {
    toast(error.message, 'err');
    react('sorry');
  }
  await loadHistory();
}

/** The face reacts to what just happened — on the stage, and on the desktop buddy. */
function react(emotion, ms = 2600) {
  if (!emotion) return;
  state.stageFace?.react?.(emotion, ms);
  api.post('/api/faces/react', { emotion }).catch(() => {});
}

/** How the face should react to a reply (and what was said to get it). */
function reactionFor(final, said = '') {
  const cards = final?.cards || [];
  const reply = final?.reply || '';
  if (/\b(thank(s| you)|ty|great job|well done|good job|you'?re (the best|amazing)|love (it|you))\b/i.test(said)) return 'love';
  if (cards.some(c => c.status === 'error' || c.status === 'failed') || /^I couldn'?t\b/i.test(reply)) return 'sorry';
  if (cards.some(c => c.status === 'awaiting_approval' || (c.type === 'draft' && c.status !== 'sent'))) return 'surprised';
  if (cards.some(c => (c.type === 'completion' && c.status !== 'error') || c.status === 'done' || c.automatic
      || c.type === 'keep_updated' || c.type === 'todo')) {
    return Math.random() < 0.34 ? 'wink' : 'happy';
  }
  return null;
}

// ------------------------------------------------------------- composer
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  $('#send').disabled = busy() || (!input.value.trim() && !state.image && !(state.files || []).length);
});
input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    send();
  }
});
$('#composer').addEventListener('submit', event => { event.preventDefault(); send(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') voice.audio?.pause(); });
$('#attach-btn').addEventListener('click', () => $('#file-input').click());
$('#mic-btn').addEventListener('click', toggleMic);
$('#file-input').addEventListener('change', e => { [...e.target.files].forEach(attach); e.target.value = ''; });
input.addEventListener('paste', event => {
  const files = [...(event.clipboardData?.files || [])];
  if (files.length) { event.preventDefault(); files.forEach(attach); }
});
document.addEventListener('dragover', e => e.preventDefault());
document.addEventListener('drop', event => {
  event.preventDefault();
  const files = [...(event.dataTransfer?.files || [])];
  if (files.length) { selectTab('ask'); files.forEach(attach); }
});

const FILE_ICON = { docx: 'W', doc: 'W', xlsx: 'X', xls: 'X', csv: 'X', tsv: 'X', pptx: 'P', pdf: 'PDF', zip: 'ZIP' };
function fileChip(file, removable = false) {
  const kind = (file.kind || file.name.split('.').pop() || '').toLowerCase();
  const size = file.size ? ` · ${file.size > 1048576 ? `${(file.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`}` : '';
  return `<span class="file-chip k-${esc(kind)}" ${file.id ? `data-file="${esc(file.id)}"` : ''}>
    <span class="file-ico">${esc(FILE_ICON[kind] || kind.toUpperCase().slice(0, 3) || 'F')}</span>
    ${file.id && !removable ? `<a href="/api/chat/file/${encodeURIComponent(file.id)}" target="_blank" rel="noopener">${esc(file.name)}</a>` : `<span>${esc(file.name)}</span>`}
    <span class="meta">${size}</span>${removable ? `<button type="button" class="chip-x" data-drop-file="${esc(file.id)}" aria-label="Remove">×</button>` : ''}</span>`;
}

function paintAttachments() {
  const image = state.image ? `<div class="attachment glass"><img src="${state.image.preview}" alt="">
      <button class="btn sm ghost" id="drop-image">Remove</button></div>` : '';
  const files = (state.files || []).length ? `<div class="file-chips">${state.files.map(f => fileChip(f, true)).join('')}</div>` : '';
  $('#attachment').innerHTML = image + files;
  $('#drop-image')?.addEventListener('click', clearImage);
  $$('[data-drop-file]', $('#attachment')).forEach(button => button.addEventListener('click', () => {
    state.files = state.files.filter(f => f.id !== button.dataset.dropFile);
    paintAttachments();
  }));
  $('#send').disabled = busy() || (!input.value.trim() && !state.image && !(state.files || []).length);
}

async function attach(file) {
  const form = new FormData();
  form.append('file', file, file.name || 'pasted.png');
  try {
    if ((file.type || '').startsWith('image/')) {
      const { image } = await api.post('/api/chat/upload-image', form);
      state.image = { name: image, preview: URL.createObjectURL(file) };
    } else {
      const { file: stored } = await api.post('/api/chat/upload-file', form);
      state.files = [...(state.files || []), stored];
    }
    paintAttachments();
    input.focus();
  } catch (error) {
    toast(`Couldn't attach ${file.name || 'that'}: ${error.message}`, 'err');
  }
}

function clearImage() {
  state.image = null;
  paintAttachments();
}

function clearAttachments() {
  state.image = null;
  state.files = [];
  paintAttachments();
}

// ------------------------------------------------------------------ voice
// The mic: click to start, click again to stop. The words are turned into
// text on this PC, then either put in the box to check or sent straight away
// (Settings → Voice). A request sent by voice gets a short spoken answer.
const voice = { recorder: null, chunks: [], status: null, audio: null };

async function voiceStatus() {
  if (!voice.status) voice.status = await api.get('/api/voice/status').catch(() => ({ enabled: false }));
  return voice.status;
}

async function toggleMic() {
  const button = $('#mic-btn');
  if (voice.recorder?.state === 'recording') { voice.recorder.stop(); return; }
  voice.audio?.pause();
  const status = await voiceStatus();
  if (!status.enabled) { toast('Voice is switched off in Settings → Voice.', 'err'); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (error) {
    toast(`The microphone isn't available: ${error.message}`, 'err');
    return;
  }
  voice.chunks = [];
  voice.recorder = new MediaRecorder(stream);
  voice.recorder.ondataavailable = e => e.data.size && voice.chunks.push(e.data);
  voice.recorder.onstop = async () => {
    stream.getTracks().forEach(track => track.stop());
    button.classList.remove('recording');
    button.classList.add('busy');
    input.placeholder = 'Understanding…';
    try {
      const form = new FormData();
      form.append('audio', new Blob(voice.chunks, { type: voice.recorder.mimeType || 'audio/webm' }), 'speech.webm');
      const { text } = await api.post('/api/voice/transcribe', form);
      if (!text) { toast("I didn't catch that.", 'err'); return; }
      input.value = (input.value ? input.value + ' ' : '') + text;
      input.dispatchEvent(new Event('input'));
      if (status.mic_mode === 'send') send({ spoken: true });
      else input.focus();
    } catch (error) {
      toast(error.message, 'err');
    } finally {
      button.classList.remove('busy');
      input.placeholder = 'Ask anything — or tell Cerebro what to do…';
    }
  };
  voice.recorder.start();
  button.classList.add('recording');
  input.placeholder = 'Listening… click the mic again when you\'re done';
}

async function speak(text) {
  const status = await voiceStatus();
  if (!status.speak_replies || !text) return;
  try {
    const response = await fetch('/api/voice/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, agent_id: state.agentId }) });
    if (!response.ok) return;
    voice.audio?.pause();
    voice.audio = new Audio(URL.createObjectURL(await response.blob()));
    // The agent's mouth follows its voice (vowel shapes for VRM and Live2D models).
    lipSync(voice.audio, (level, bands) => state.stageFace?.setMouth(level, bands));
    voice.audio.play();
  } catch { /* speaking is a nicety */ }
}

// -------------------------------------------------------------- Talk live
// A live conversation with Amazon Nova Sonic: the mic streams to the backend
// as 16 kHz PCM, Nova Sonic's voice streams back and plays as it arrives, and
// talking over it interrupts. Real work goes to the agent (approvals as usual).
const live = { socket: null, ctx: null, out: null, node: null, stream: null, playAt: 0, sources: [], level: 0 };

async function liveStatus() {
  const status = await voiceStatus();
  const button = $('#live-btn');
  if (button) button.hidden = !(status?.enabled && status?.voice?.live);
}

function liveStrip(text) {
  const strip = $('#live-strip');
  if (!text) { strip.hidden = true; strip.innerHTML = ''; return; }
  strip.hidden = false;
  strip.innerHTML = `<div class="live-strip"><span class="badge accent">Live</span><span class="grow">${esc(text)}</span>
    <button type="button" class="btn sm ghost" id="live-stop">End</button></div>`;
  $('#live-stop').addEventListener('click', stopLive);
}

function stopPlayback() {
  live.sources.forEach(src => { try { src.stop(); } catch { /* already done */ } });
  live.sources = [];
  live.playAt = 0;
  state.stageFace?.setMouth(0);
}

function playPCM(base64) {
  if (!live.out) return;
  const bytes = Uint8Array.from(atob(base64), c => c.charCodeAt(0));
  const samples = new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
  if (!samples.length) return;
  const buffer = live.out.createBuffer(1, samples.length, 24000);
  const channel = buffer.getChannelData(0);
  let sum = 0;
  for (let i = 0; i < samples.length; i++) { channel[i] = samples[i] / 0x8000; sum += channel[i] ** 2; }
  const source = live.out.createBufferSource();
  source.buffer = buffer;
  source.connect(live.out.destination);
  const start = Math.max(live.out.currentTime + 0.03, live.playAt);
  source.start(start);
  live.playAt = start + buffer.duration;
  live.sources.push(source);
  source.onended = () => { live.sources = live.sources.filter(s => s !== source); if (!live.sources.length) state.stageFace?.setMouth(0); };
  // The mouth follows each chunk's loudness while it plays.
  const level = Math.min(1, Math.sqrt(sum / samples.length) * 6);
  setTimeout(() => state.stageFace?.setMouth(level), Math.max(0, (start - live.out.currentTime) * 1000));
}

async function startLive() {
  if (live.socket) return stopLive();
  const button = $('#live-btn');
  try {
    live.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (error) {
    toast(`The microphone isn't available: ${error.message}`, 'err');
    return;
  }
  live.ctx = new AudioContext({ sampleRate: 16000 });
  live.out = new AudioContext({ sampleRate: 24000 });
  await live.ctx.audioWorklet.addModule('/static/ui/pcm-worklet.js');
  const source = live.ctx.createMediaStreamSource(live.stream);
  live.node = new AudioWorkletNode(live.ctx, 'pcm-capture');
  source.connect(live.node);
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const params = new URLSearchParams({ agent_id: state.agentId || '', conversation_id: state.chatId || '' });
  live.socket = new WebSocket(`${proto}://${location.host}/api/voice/live?${params}`);
  live.socket.binaryType = 'arraybuffer';
  live.node.port.onmessage = event => { if (live.socket?.readyState === 1) live.socket.send(event.data); };
  button.classList.add('on');
  liveStrip('Connecting…');
  state.stageFace?.setState('listening');
  live.socket.onmessage = event => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    if (data.type === 'ready') liveStrip(`Talking with ${data.agent} — just speak. Talk over the reply to interrupt.`);
    else if (data.type === 'audio') playPCM(data.data);
    else if (data.type === 'interrupted') stopPlayback();
    else if (data.type === 'transcript') {
      liveStrip(`${data.role === 'user' ? 'You' : (currentAgent()?.name || 'Cerebro')}: ${data.text}`);
      if (data.role === 'assistant' && /\b(yay|hehe|haha|got it|done)\b/i.test(data.text)) state.stageFace?.react?.('happy', 1800);
    } else if (data.type === 'working') { liveStrip(`Working on: ${data.request}`); state.stageFace?.setState('thinking'); }
    else if (data.type === 'result') {
      state.stageFace?.setState('listening');
      react(reactionFor({ reply: data.reply, cards: data.cards }));
      loadHistory();
    } else if (data.type === 'error') { toast(data.detail, 'err'); stopLive(); }
    else if (data.type === 'ended') stopLive();
  };
  live.socket.onclose = () => stopLive(true);
}

function stopLive(fromSocket = false) {
  if (!live.socket && !live.stream) return;
  try { if (!fromSocket && live.socket?.readyState === 1) live.socket.send(JSON.stringify({ type: 'stop' })); } catch { /* closing */ }
  try { live.socket?.close(); } catch { /* closed */ }
  live.socket = null;
  stopPlayback();
  live.stream?.getTracks().forEach(track => track.stop());
  live.stream = null;
  live.node?.disconnect();
  live.ctx?.close?.();
  live.out?.close?.();
  live.ctx = live.out = live.node = null;
  $('#live-btn')?.classList.remove('on');
  liveStrip(null);
  paintStage();
}

$('#live-btn')?.addEventListener('click', startLive);
liveStatus();

async function send({ spoken = false } = {}) {
  const text = input.value.trim();
  const files = [...(state.files || [])];
  if (busy() || (!text && !state.image && !files.length)) return;
  const image = state.image?.name || null;
  const chatId = state.chatId;
  state.sending.add(chatId);
  paintChatList();
  input.value = '';
  input.style.height = 'auto';
  $('#send').disabled = true;
  if (!state.history.length) thread.innerHTML = '';

  thread.append(messageNode({ role: 'user', content: text || (files.length ? '' : '(image)'), image_path: image,
    meta: { attachments: files } }));
  clearAttachments();
  const pending = document.createElement('div');
  pending.className = 'msg assistant short';
  pending.innerHTML = `<div class="avatar"></div>
    <div class="bubble glass"><div class="steps"></div>
      <div class="pending-mascot">${pendingFace()}<span class="pending-caption">Thinking…</span></div></div>`;
  thread.append(pending);
  scrollToEnd();

  const steps = [];
  const paintSteps = () => {
    $('.steps', pending).outerHTML = stepsHTML(steps) || '<div class="steps"></div>';
    scrollToEnd();
  };

  try {
    const final = await streamChat({ message: text, image, conversation_id: chatId, voice: spoken,
      attachments: files.map(f => f.id) }, card => {
      if (card.type === 'plan') { steps.push({ type: 'progress', status: 'complete', title: card.title || 'Plan', detail: (card.steps || []).join(' → ') }); paintSteps(); return; }
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
      state.history.push({ role: 'user', content: text, image_path: image, meta: { attachments: files } }, message);
      thread.append(messageNode(message));
      if (spoken) speak(final.speech_text || final.reply);
      react(reactionFor(final, text));
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
  if (state.agentId) params.set('agent_id', String(state.agentId));
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
    const chat = await api.post('/api/chat/conversations', { agent_id: state.agentId });
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
      <span class="grow"><b>${esc(task.title)}</b><span>${esc(task.source === 'keep' && task.schedule === 'manual' ? 'When its source changes' : (SCHEDULE_LABEL[task.schedule] || task.schedule))}${task.at_time ? ` ${esc(task.at_time)}` : ''} · ${esc(when)}</span></span>
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
      ${pendingFace()}<span class="pending-caption">Working on it…</span></div></div>`;
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
      <div class="btn-row" style="margin:0;flex-direction:column">
        <button class="btn sm" data-settings>Settings</button>
        ${b.enabled ? `<button class="btn sm ghost" data-control title="Show the hidden browser at the page Cerebro has open, to finish something by hand">${state.controlling ? 'Done' : 'Take control'}</button>` : ''}
      </div>
    </div>
    <div class="section-title">Systems<span class="grow"></span></div>
    <div id="integration-list"></div>
    <div class="section-title">Add-ons<span class="grow"></span>
      <button class="btn sm primary" id="addon-add">+ Add</button>
      <button class="icon-btn" id="addon-menu" title="Import or export add-ons" aria-label="More">${'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/></svg>'}</button></div>
    <p class="section-note">Anything Cerebro should reach: an MCP server, any website you sign in to, a Windows program, or a skill you teach it. Just ask in chat (“connect to Jira”, “keep this page updated”) and Cerebro adds what it needs itself.</p>
    <div id="addon-list"></div>`;
  $('[data-settings]', body).addEventListener('click', () => openExternal(`${location.origin}/settings#integrations`));
  $('[data-control]', body)?.addEventListener('click', toggleControl);
  $('#addon-add').addEventListener('click', e => showMenu(e.currentTarget, [
    ['MCP server…', addMcpServer],
    ['Website…', addWebsite],
    ['Desktop app…', addDesktopApp],
    ['Skill…', () => editSkill()],
  ]));
  $('#addon-menu').addEventListener('click', e => showMenu(e.currentTarget, [
    ['Import skills (.zip or SKILL.md)…', () => pickFile('.zip,.md', file => upload('/api/addons/skills/import', file))],
    ['Import add-ons file…', () => pickFile('.json', file => upload('/api/addons/import', file))],
    { label: 'Export add-ons', action: () => openExternal(`${location.origin}/api/addons/export`) },
  ]));
  const list = $('#integration-list');
  state.monitor = data.monitor || state.monitor;
  (data.integrations || []).forEach(item => list.append(integrationCard(item)));
  loadAddons();
}

// ================================================================ Add-ons
async function loadAddons() {
  const host = $('#addon-list');
  if (!host) return;
  const data = await api.get('/api/addons').catch(error => ({ error: error.message }));
  if (data.error) { host.innerHTML = `<div class="empty-note">${esc(data.error)}</div>`; return; }
  const items = data.addons || [];
  if (!items.length) { host.innerHTML = '<div class="empty-note">Nothing added yet.</div>'; return; }
  host.innerHTML = '';
  items.forEach(item => host.append(addonCard(item)));
}

const ADDON_KIND = { mcp: 'MCP', site: 'Website', skill: 'Skill', app: 'Desktop app' };

function addonCard(item) {
  const card = document.createElement('div');
  card.className = 'card glass integration addon';
  const status = !item.enabled ? '<span class="badge">Off</span>'
    : item.status === 'ready' ? '<span class="badge ok">Ready</span>'
    : item.status === 'needs_sign_in' ? '<span class="badge warn">Sign-in needed</span>'
    : item.status === 'error' ? '<span class="badge err">Not working</span>'
    : `<span class="badge">${esc(item.status || '')}</span>`;
  const where = item.kind === 'mcp' ? (item.url || item.command || '') : item.kind === 'skill' ? (item.description || '')
    : item.kind === 'app' ? (item.path || item.window || '') : '';
  card.innerHTML = `
    <div class="logo addon-${item.kind}">${esc(ADDON_KIND[item.kind]?.[0] || '+')}</div>
    <div style="min-width:0"><h4>${esc(item.label)} ${status}</h4>
      <div class="meta">${esc(ADDON_KIND[item.kind])}${item.kind === 'app' ? (item.available ? '' : ' · works on Windows') : item.kind !== 'skill' ? ` · ${item.tool_count} tools` : ''} · added by ${item.added_by === 'cerebro' ? 'Cerebro' : 'you'}${item.last_used_at ? ` · used ${relTime(item.last_used_at)}` : ''}</div>
      ${where ? `<div class="meta mono">${esc(where.slice(0, 140))}</div>` : ''}
      ${item.error && item.status === 'error' ? `<p class="card-error">${esc(item.error)}</p>` : ''}
      ${item.kind === 'mcp' && item.sign_in?.status === 'failed' ? `<p class="card-error">${esc(item.sign_in.detail || '')}</p>` : ''}
      <label class="auto-apply"><span class="switch"><input type="checkbox" data-enabled ${item.enabled ? 'checked' : ''}><span></span></span>On</label>
      ${item.kind === 'mcp' ? `<label class="auto-apply" title="Off: anything that changes something waits for your approval. On: its tools run straight away.">
        <span class="switch"><input type="checkbox" data-auto ${item.auto_approve ? 'checked' : ''}><span></span></span>Approve its actions automatically</label>` : ''}
      ${item.kind === 'site' || item.kind === 'app' ? `<label class="auto-apply" title="Off: every save, send or submit waits for your approval. On: it's made straight away and logged with before and after screenshots.">
        <span class="switch"><input type="checkbox" data-apply ${item.auto_apply ? 'checked' : ''}><span></span></span>Apply changes automatically</label>` : ''}</div>
    <div class="btn-row" style="margin:0;flex-direction:column">
      ${item.kind === 'mcp' && item.status === 'needs_sign_in' ? '<button class="btn sm primary" data-signin>Sign in</button>' : ''}
      ${item.kind === 'mcp' ? '<button class="btn sm ghost" data-refresh>Reconnect</button><button class="btn sm ghost" data-keys>Keys…</button>' : ''}
      ${item.kind === 'skill' ? '<button class="btn sm ghost" data-edit>Edit</button>' : ''}
      <button class="btn sm ghost danger" data-remove>Remove</button>
    </div>`;
  const kind = item.kind, name = encodeURIComponent(item.name);
  $('[data-enabled]', card).addEventListener('change', async e => {
    await api.post(`/api/addons/${kind}/${name}/enabled`, { enabled: e.currentTarget.checked }).catch(err => toast(err.message, 'err'));
    loadAddons();
  });
  $('[data-auto]', card)?.addEventListener('change', async e => {
    const enabled = e.currentTarget.checked;
    await api.post(`/api/addons/mcp/${name}/auto-approve`, { enabled }).catch(err => toast(err.message, 'err'));
    toast(enabled ? `${item.label}'s actions will run without asking` : `${item.label}'s actions will wait for your approval`, 'ok');
  });
  $('[data-apply]', card)?.addEventListener('change', async e => {
    const enabled = e.currentTarget.checked;
    await api.post(`/api/addons/${kind}/${name}/auto-apply`, { enabled }).catch(err => toast(err.message, 'err'));
    toast(enabled ? `${item.label}: changes are made straight away and logged` : `${item.label}: changes wait for your approval`, 'ok');
  });
  $('[data-signin]', card)?.addEventListener('click', e => addonSignIn(item.name, e.currentTarget));
  $('[data-refresh]', card)?.addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    const result = await api.post(`/api/addons/mcp/${name}/refresh`).catch(err => ({ detail: err.message }));
    toast(result.ok ? `${item.label}: ${result.count} tools` : (result.detail || 'It didn\'t connect'), result.ok ? 'ok' : 'err');
    loadAddons();
  });
  $('[data-keys]', card)?.addEventListener('click', async () => {
    const values = await sheet({ title: `Keys for ${item.label}`, ok: 'Save and reconnect',
      body: `<p class="sheet-note">${item.transport === 'stdio' ? 'Environment variables' : 'Request headers'} the server needs, one per line as NAME=value. They're kept encrypted on this PC.</p>
        <textarea class="input" name="keys" rows="4" placeholder="${item.transport === 'stdio' ? 'API_TOKEN=…' : 'Authorization=Bearer …'}"></textarea>` });
    if (!values?.keys?.trim()) return;
    const keys = Object.fromEntries(values.keys.split('\n').filter(l => l.includes('=')).map(l => [l.split('=')[0].trim(), l.slice(l.indexOf('=') + 1).trim()]));
    const result = await api.post(`/api/addons/mcp/${name}/keys`, keys).catch(err => ({ detail: err.message }));
    toast(result.ok ? `${item.label} connected` : (result.detail || 'Saved'), result.ok ? 'ok' : 'err');
    loadAddons();
  });
  $('[data-edit]', card)?.addEventListener('click', () => editSkill(item));
  $('[data-remove]', card).addEventListener('click', async () => {
    if (!confirm(`Remove ${item.label}? Its tools go too.`)) return;
    const result = await api.del(`/api/addons/${kind}/${name}`).catch(err => ({ detail: err.message }));
    toast(result.detail || 'Removed', result.ok ? 'ok' : 'err');
    loadConnect();
  });
  return card;
}

async function addDesktopApp() {
  const values = await sheet({ title: 'Add a desktop app', ok: 'Add',
    body: `<p class="sheet-note">A Windows program Cerebro may open and work in — reading its windows, typing and clicking like you would. Saving, sending or confirming waits for your approval.</p>
      <label class="field"><span>Name</span><input class="input" name="label" placeholder="e.g. Billing tool" required></label>
      <label class="field"><span>Program (path or command)</span><input class="input" name="path" placeholder="C:\\Program Files\\Billing\\billing.exe  ·  notepad"></label>
      <label class="field"><span>…or text in its window title</span><input class="input" name="window" placeholder="Billing"></label>` });
  if (!values?.label?.trim()) return;
  const result = await api.post('/api/addons/apps', values).catch(err => ({ detail: err.message }));
  toast(result.ok ? `Added ${result.label}${result.available ? '' : ' (it can be used on Windows)'}` : (result.detail || 'Not added'), result.ok ? 'ok' : 'err');
  loadAddons();
}

async function addMcpServer() {
  const values = await sheet({ title: 'Add an MCP server', ok: 'Add',
    body: `<label class="field"><span>Search the MCP Registry</span><input class="input" name="q" placeholder="e.g. jira, github, servicenow"></label>
      <div id="registry-results" class="registry-results"></div>
      <label class="field"><span>…or its web address, or the command that runs it</span>
        <input class="input" name="target" placeholder="https://example.com/mcp  ·  uvx mcp-server-time"></label>
      <label class="field"><span>Name</span><input class="input" name="label" placeholder="Optional"></label>
      <label class="field"><span>Keys it needs (NAME=value, one per line)</span><textarea class="input" name="keys" rows="2"></textarea></label>
      <input type="hidden" name="registry_name">`,
    setup: form => {
      let timer;
      $('[name=q]', form).addEventListener('input', e => {
        clearTimeout(timer);
        const q = e.target.value.trim();
        timer = setTimeout(async () => {
          const host = $('#registry-results', form);
          if (q.length < 2) { host.innerHTML = ''; return; }
          host.innerHTML = '<div class="meta">Searching…</div>';
          const data = await api.get(`/api/addons/registry?q=${encodeURIComponent(q)}`).catch(err => ({ error: err.message }));
          host.innerHTML = data.error ? `<div class="meta">${esc(data.error)}</div>` : (data.servers || []).map((s, i) => `
            <button type="button" class="registry-hit" data-i="${i}"><b>${esc(s.label)}</b> <span class="meta">${esc(s.registry_name)}</span>
              <span class="meta">${esc(s.description)}</span>${s.needs.length ? `<span class="meta">Needs: ${esc(s.needs.map(n => n.name).join(', '))}</span>` : ''}</button>`).join('') || '<div class="meta">Nothing found — try the website instead.</div>';
          $$('.registry-hit', host).forEach(button => button.addEventListener('click', () => {
            const server = data.servers[Number(button.dataset.i)];
            $$('.registry-hit', host).forEach(b => b.classList.toggle('chosen', b === button));
            form.registry_name.value = server.registry_name;
            form.target.value = server.config.url || [server.config.command, ...(server.config.args || [])].join(' ');
            form.label.value = server.label;
            if (server.needs.length && !form.keys.value) form.keys.value = server.needs.map(n => `${n.name}=`).join('\n');
          }));
        }, 300);
      });
    } });
  if (!values || !(values.target || values.registry_name)) return;
  const keys = Object.fromEntries((values.keys || '').split('\n').filter(l => l.includes('=') && l.split('=')[1].trim()).map(l => [l.split('=')[0].trim(), l.slice(l.indexOf('=') + 1).trim()]));
  const target = (values.target || '').trim();
  const body = values.registry_name ? { registry_name: values.registry_name, label: values.label || null, keys }
    : /^https?:\/\//.test(target) ? { url: target, label: values.label || null, keys } : { command: null, url: target, label: values.label || null, keys };
  toast('Connecting…');
  const result = await api.post('/api/addons/mcp', body).catch(err => ({ detail: err.message }));
  if (result.status === 'needs_sign_in') { toast(`${result.label} added — sign in to use it`); loadAddons(); addonSignIn(result.name); return; }
  toast(result.ok ? `Added ${result.label} — ${result.count} tools` : (result.detail || 'It didn\'t connect'), result.ok ? 'ok' : 'err');
  loadAddons();
}

async function addWebsite() {
  const values = await sheet({ title: 'Add a website', ok: 'Add',
    body: `<p class="sheet-note">Cerebro works in it through its hidden browser, signed in as you, and learns where its search is.</p>
      <label class="field"><span>Address</span><input class="input" name="url" required placeholder="https://portal.example.com"></label>
      <label class="field"><span>Name</span><input class="input" name="name" placeholder="e.g. Vendor portal"></label>
      <label class="field"><span>What's on it</span><input class="input" name="description" placeholder="Optional — helps Cerebro know when to use it"></label>` });
  if (!values?.url) return;
  const result = await api.post('/api/addons/sites', values).catch(err => ({ detail: err.message }));
  if (!result.ok) { toast(result.detail || 'Couldn\'t add it', 'err'); return; }
  toast(`Added ${result.label} — sign in on its card`, 'ok');
  loadConnect();
}

async function editSkill(item) {
  const values = await sheet({ title: item ? `Skill: ${item.label}` : 'Teach Cerebro a skill', ok: 'Save',
    body: `<label class="field"><span>Name</span><input class="input" name="name" required value="${esc(item?.name || '')}" placeholder="e.g. close-ticket"></label>
      <label class="field"><span>When to use it</span><input class="input" name="description" value="${esc(item?.description || '')}" placeholder="One line"></label>
      <label class="field"><span>Steps</span><textarea class="input" name="instructions" rows="9" required placeholder="1. …">${esc(item?.instructions || '')}</textarea></label>` });
  if (!values) return;
  const result = await api.post('/api/addons/skills', values).catch(err => ({ detail: err.message }));
  toast(result.ok ? `Saved ${result.name}` : result.detail, result.ok ? 'ok' : 'err');
  loadAddons();
}

function pickFile(accept, then) {
  const picker = document.createElement('input');
  picker.type = 'file';
  picker.accept = accept;
  picker.addEventListener('change', () => picker.files[0] && then(picker.files[0]));
  picker.click();
}

async function upload(path, file) {
  const form = new FormData();
  form.append('file', file, file.name);
  const result = await api.post(path, form).catch(err => ({ detail: err.message }));
  const names = result.imported || result.added || [];
  toast(result.ok ? `Imported ${names.length}: ${names.join(', ')}` : result.detail, result.ok ? 'ok' : 'err');
  loadConnect();
}

async function addonSignIn(name, button) {
  if (button) button.disabled = true;
  const started = await api.post(`/api/addons/mcp/${encodeURIComponent(name)}/sign-in`).catch(err => ({ ok: false, detail: err.message }));
  if (!started.ok) { toast(started.detail || 'Sign-in could not start', 'err'); if (button) button.disabled = false; return; }
  toast('Sign in in the browser tab that opened.');
  const poll = setInterval(async () => {
    const status = await api.get(`/api/addons/mcp/${encodeURIComponent(name)}/sign-in`).catch(() => null);
    if (!status || status.status === 'waiting') return;
    clearInterval(poll);
    toast(status.detail || 'Done', status.status === 'connected' ? 'ok' : 'err');
    if (state.tab === 'connect') loadAddons();
    if (button) button.disabled = false;
  }, 2000);
}

async function toggleControl() {
  const path = state.controlling ? '/api/addons/browser/release' : '/api/addons/browser/take-control';
  const result = await api.post(path).catch(err => ({ ok: false, detail: err.message }));
  if (result.ok) state.controlling = !state.controlling;
  toast(result.detail, result.ok ? 'ok' : 'err');
  loadConnect();
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
window.cerebroSelectTab = name => selectTab(['home', 'ask', 'work', 'activity', 'sources', 'connect'].includes(name) ? name : 'ask');
window.cerebroOpenAgent = id => { selectTab('ask'); selectAgent(Number(id)); };
// A notification's "Open" (the tray) lands in the chat it is about.
window.cerebroOpenChat = id => { selectTab('ask'); openChat(Number(id)); };
selectTab('ask');
connectActivity();
loadInfo();
loadContext();
try { state.chatId = Number(localStorage.getItem('cerebro.chat')) || null; } catch { /* ignore */ }
try { state.agentId = Number(localStorage.getItem('cerebro.agent')) || null; } catch { /* ignore */ }
loadAgents().then(() => loadHistory()).then(() => {
  // The chat on screen decides which agent is on screen.
  if (state.chat?.agent_id && state.chat.agent_id !== state.agentId) {
    state.agentId = state.chat.agent_id;
    paintAgents();
    paintStage(true);
    if (!state.history.length) renderEmpty();
  }
  loadChats();
});
setInterval(() => { if (!document.hidden) loadAgents(); }, 15000);
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


// ================================================================= agents
// Several named agents share one memory and the same tools; each has its own
// chats, jobs, face and voice. The sidebar lists them; the stage above the
// chat shows the one on screen.
async function loadAgents() {
  try {
    const data = await api.get('/api/agents');
    state.agents = data.agents || [];
    state.groups = data.groups || {};
    state.templates = data.templates || {};
  } catch { state.agents = state.agents || []; }
  if (!state.agents.some(a => a.id === state.agentId)) state.agentId = state.agents[0]?.id || null;
  paintAgents();
  paintStage();
  const waiting = state.agents.reduce((n, a) => n + (a.status?.counts?.waiting_input || 0) + (a.status?.counts?.waiting_approval || 0), 0);
  $('#work-count').textContent = waiting ? String(waiting) : '';
}

function agentStatusText(agent) {
  const st = agent.status || {};
  const c = st.counts || {};
  if (st.state === 'needs_input') return `Has a question for you`;
  if (st.state === 'needs_approval') return `${c.waiting_approval} job(s) waiting for approval`;
  if (st.state === 'working') return st.current ? `Working on: ${st.current.title}` : `${(c.running || 0) + (c.queued || 0)} job(s) on the go`;
  return c.ideas ? `${c.ideas} idea(s) for you` : 'Ready';
}

function paintAgents() {
  const list = $('#agent-list');
  list.innerHTML = '';
  for (const agent of state.agents) {
    const row = document.createElement('button');
    row.className = 'agent-row';
    row.dataset.agent = agent.id;
    row.dataset.state = agent.status?.state || 'idle';
    row.setAttribute('aria-current', String(agent.id === state.agentId));
    row.style.setProperty('--agent', agent.color || '#8b7cf6');
    const needs = (agent.status?.counts?.waiting_input || 0) + (agent.status?.counts?.waiting_approval || 0);
    row.title = `${agent.name} — ${agentStatusText(agent)}`;
    row.innerHTML = `<span class="agent-av">${faceAvatar(agent, 42)}</span>
      <span class="agent-text"><div class="agent-name">${esc(agent.name)}</div><div class="agent-role">${esc(agentStatusText(agent))}</div></span>
      <span class="agent-badge">${needs || ''}</span>`;
    row.addEventListener('click', () => { selectAgent(agent.id); if (!['ask', 'work'].includes(state.tab)) selectTab('ask'); });
    row.addEventListener('contextmenu', event => { event.preventDefault(); agentMenu(row, agent); });
    list.append(row);
  }
}

function agentMenu(anchor, agent) {
  const items = [['Edit agent…', () => editAgent(agent)], ['New job…', () => newJob(agent)]];
  if (!agent.is_default) items.push(['Remove agent', () => removeAgent(agent)]);
  showMenu(anchor, items);
}

async function selectAgent(id) {
  if (id === state.agentId && state.chat?.agent_id === id) return;
  state.agentId = id;
  try { localStorage.setItem('cerebro.agent', String(id)); } catch { /* ignore */ }
  paintAgents();
  paintStage(true);
  if (state.tab === 'work') loadWork();
  await loadChats();
  const open = state.chats.find(c => !c.archived);
  if (open) await openChat(open.id);
  else {
    const chat = await api.post('/api/chat/conversations', { agent_id: id }).catch(() => null);
    if (chat) { await openChat(chat.id); loadChats(); }
  }
}

// ----------------------------------------------------------------- stage
function stageStateFor(agent) {
  const mine = state.sending.has(state.chatId);
  if (mine || (agent?.is_default && !['idle', 'offline'].includes(state.activity.state))) return state.activity.state;
  const st = agent?.status?.state;
  return st === 'working' ? 'thinking' : st === 'needs_input' || st === 'needs_approval' ? 'awaiting_approval'
    : state.activity.state === 'offline' ? 'offline' : 'idle';
}

function paintStage(rebuild = false) {
  const agent = currentAgent();
  const host = $('#stage-face');
  if (!host || !agent) return;
  const key = `${agent.id}:${agent.face}:${JSON.stringify(agent.face_style || {})}`;
  if (rebuild || host.dataset.key !== key) {
    state.stageFace?.destroy();
    host.innerHTML = '';
    state.stageFace = createFace(host, agent, 110);
    host.dataset.key = key;
  }
  $('#stage-name').textContent = agent.name;
  $('#stage-role').textContent = agent.role || '';
  const status = $('#stage-status');
  status.textContent = agentStatusText(agent);
  status.dataset.state = agent.status?.state || 'idle';
  state.stageFace?.setState(stageStateFor(agent));
}

$('#stage-edit').addEventListener('click', () => currentAgent() && editAgent(currentAgent()));
$('#stage-toggle').addEventListener('click', () => {
  const stage = $('#agent-stage');
  stage.classList.toggle('small');
  try { localStorage.setItem('cerebro.stage', stage.classList.contains('small') ? 'small' : 'big'); } catch { /* ignore */ }
});
try { if (localStorage.getItem('cerebro.stage') === 'small') $('#agent-stage').classList.add('small'); } catch { /* ignore */ }

function pendingFace() {
  const agent = currentAgent();
  if (!agent || agent.face === 'brain') return mascot(96, 'thinking');
  return `<span class="face-avatar pending-anime" style="width:96px;height:96px">${faceAvatar(agent, 96).replace(/^<span[^>]*>|<\/span>$/g, '')}</span>`;
}

// ------------------------------------------------------------ sidebar drawer
function openSidebar(open = true) {
  $('#sidebar').classList.toggle('open', open);
  $('#side-scrim').classList.toggle('open', open);
}
$('#agents-btn').addEventListener('click', () => openSidebar(!$('#sidebar').classList.contains('open')));
$('#side-scrim').addEventListener('click', () => openSidebar(false));
$('#agent-new').addEventListener('click', event => showMenu(event.currentTarget,
  Object.entries(state.templates).map(([key, t]) => [t.name === 'New agent' ? 'Blank agent…' : `${t.name}…`, () => editAgent(null, key)])));

// ------------------------------------------------------------- the editor
const PERSONALITIES = {
  sweetheart: 'Sweetheart — cute, cheerful, teasing', warm: 'Warm & natural', energetic: 'Energetic & upbeat',
  calm: 'Calm & soft', professional: 'Professional', custom: 'Custom',
};
const SLOT_LABEL = { calm: 'Ready', happy: 'Done (happy)', wink: 'Done (wink)', love: 'Thanked', thinking: 'Thinking',
  focused: 'Working', listening: 'Listening', surprised: 'Needs approval', sorry: 'Failed', pout: 'Kept waiting',
  smug: 'Approved', dizzy: 'Error', asleep: 'Offline' };
const GESTURE_LABEL = { greeting: 'When it appears', done: 'Task done', approved: 'You approve',
  thinking: 'Starts thinking', attention: 'Needs you', idle: 'Every few minutes' };
const PREVIEW_LINE = 'Hehe, found it! [giggle] The fix was a typo — [tease]you owe me a coffee~[/tease] [whisper]I won\'t tell anyone.[/whisper]';

async function editAgent(agent, template = 'blank') {
  const base = agent || { ...(state.templates[template] || {}), face: 'anime',
    face_style: { palette: PALETTE_NAMES[state.agents.length % PALETTE_NAMES.length], hair: HAIR_NAMES[state.agents.length % HAIR_NAMES.length] },
    tool_scope: [], personality: { preset: 'sweetheart' } };
  const faces = (await api.get('/api/faces').catch(() => ({ faces: [] }))).faces || [];
  const vstatus = await voiceStatus();
  let face = base.face || 'anime';
  let style = { ...(base.face_style || {}) };
  let vstyle = { ...(base.voice_style || {}) };
  let persona = { preset: 'sweetheart', ...(base.personality || {}) };
  let preview = null;
  const scope = new Set(base.tool_scope || []);
  const num = (v, d) => (v === undefined || v === null || v === '' ? d : v);
  const values = await sheet({
    title: agent ? `Edit ${agent.name}` : 'New agent', ok: agent ? 'Save' : 'Create',
    body: `
      <label class="field"><span>Name</span><input class="input" name="name" required value="${esc(base.name || '')}"></label>
      <label class="field"><span>Role</span><input class="input" name="role" value="${esc(base.role || '')}" placeholder="What it's for, in a few words"></label>
      <label class="field"><span>Standing instructions</span><textarea class="input" name="instructions" rows="3" placeholder="How it should work">${esc(base.instructions || '')}</textarea></label>
      <div class="field"><span>Face</span>
        <div class="work-tabs" data-faces>
          ${[['brain', 'Brain'], ['anime', 'Anime'], ['live2d', 'Live2D model'], ['vrm', 'VRM 3D model']].map(([k, label]) =>
            `<button type="button" class="btn sm" data-face="${k}" aria-pressed="${face === k}">${label}</button>`).join('')}
        </div>
        <div class="face-editor">
          <div class="face-preview-wrap"><div class="face-preview" data-preview></div>
            <div class="expr-row" data-exprs></div></div>
          <div class="face-options">
            <div class="mini-label" data-palette-label>Colour</div>
            <div class="palette-row" data-palettes></div>
            <div class="mini-label" data-hair-label>Hairstyle</div>
            <div class="palette-row" data-hairs></div>
            <div data-models></div>
          </div>
        </div>
      </div>
      <div class="field"><span>Voice &amp; personality</span>
        <div class="voice-grid">
          <label>Personality<select class="input" data-persona>${Object.entries(PERSONALITIES).map(([k, l]) =>
            `<option value="${k}" ${persona.preset === k ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></label>
          <label>Voice<select class="input" data-provider>
            ${[['', 'Settings default'], ['polly', 'Amazon Polly'], ['sonic', 'Amazon Nova Sonic'], ['edge', 'Edge online voices'], ['windows', 'Windows']]
              .map(([k, l]) => `<option value="${k}" ${(vstyle.provider || '') === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
          <label>Voice name<input class="input" data-voice list="voice-names" value="${esc(vstyle.voice || base.voice_name || '')}" placeholder="Ruth, tiffany, en-US-AnaNeural…"><datalist id="voice-names"></datalist></label>
          <label data-engine-wrap>Polly engine<select class="input" data-engine>
            ${[['', 'Settings default'], ['generative', 'Generative (most natural)'], ['neural', 'Neural'], ['standard', 'Standard (effects: whisper, pitch, breaths)'], ['long-form', 'Long-form']]
              .map(([k, l]) => `<option value="${k}" ${(vstyle.engine || '') === k ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
        </div>
        <div class="slider-grid">
          ${[['playfulness', 'Playful & teasing', 0, 1, 0.05, num(persona.playfulness, 0.75)],
             ['expressiveness', 'Expressive (giggles, gasps, whispers)', 0, 1, 0.05, num(persona.expressiveness, 0.75)],
             ['formality', 'Formal', 0, 1, 0.05, num(persona.formality, 0.2)]].map(([k, l, a, b, st, v]) =>
            `<label>${l}<input type="range" data-persona-slider="${k}" min="${a}" max="${b}" step="${st}" value="${v}"></label>`).join('')}
          ${[['rate', 'Speed', 0.6, 1.6, 0.02, num(vstyle.rate, base.voice_rate || 1)],
             ['pitch', 'Pitch (Standard, Edge, Windows)', -20, 20, 1, num(vstyle.pitch, 0)],
             ['volume', 'Volume', -6, 6, 1, num(vstyle.volume, 0)],
             ['timbre', 'Timbre (Polly Standard)', -20, 20, 1, num(vstyle.timbre, 0)]].map(([k, l, a, b, st, v]) =>
            `<label>${l}<input type="range" data-voice-slider="${k}" min="${a}" max="${b}" step="${st}" value="${v}"></label>`).join('')}
        </div>
        <div class="check-row">
          <label><input type="checkbox" data-voice-flag="cues" ${vstyle.cues !== false ? 'checked' : ''}> Sound cues</label>
          <label><input type="checkbox" data-voice-flag="breaths" ${vstyle.breaths ? 'checked' : ''}> Breaths (Polly Standard)</label>
          <label><input type="checkbox" data-voice-flag="drc" ${vstyle.drc ? 'checked' : ''}> Clearer on small speakers</label>
        </div>
        <textarea class="input" data-custom rows="2" placeholder="Anything else about its personality (optional)">${esc(persona.custom || '')}</textarea>
        <div class="btn-row" style="margin-top:6px"><button type="button" class="btn sm" data-try>▶ Preview voice</button>
          <span class="meta" data-try-note>${vstatus?.voice?.sonic ? '' : esc(vstatus?.voice?.sonic_detail || '')}</span></div>
      </div>
      <div class="field"><span>What it may use (nothing ticked: everything)</span>
        <div class="scope-grid">${Object.entries(state.groups).map(([key, label]) =>
          `<label><input type="checkbox" data-scope="${key}" ${scope.has(key) ? 'checked' : ''}> ${esc(label)}</label>`).join('')}</div></div>`,
    setup: form => {
      const rebuildPreview = () => {
        preview?.destroy();
        const host = $('[data-preview]', form);
        host.innerHTML = '';
        preview = createFace(host, { face, face_style: style }, 150);
        preview.setState('idle');
        paintExprs();
        if (face === 'live2d' || face === 'vrm') preview.ready?.then(() => paintModelPanels());
      };
      const paintExprs = () => {
        const list = face === 'brain' ? [] : EXPRESSIONS.filter(e => e !== 'asleep');
        $('[data-exprs]', form).innerHTML = list.map(e => `<button type="button" class="chip" data-expr="${e}">${e}</button>`).join('');
        $$('[data-expr]', form).forEach(b => b.addEventListener('click', () => preview?.react?.(b.dataset.expr, 2200)));
      };
      const paintLooks = () => {
        const anime = face !== 'brain';
        $('[data-palettes]', form).innerHTML = PALETTE_NAMES.map(p =>
          `<button type="button" data-palette="${p}" title="${p}" aria-pressed="${(style.palette || 'pink') === p}">${faceAvatar({ face: 'anime', face_style: { palette: p, hair: style.hair } }, 46)}</button>`).join('');
        $('[data-hairs]', form).innerHTML = HAIR_NAMES.map(h =>
          `<button type="button" data-hair="${h}" title="${HAIRSTYLES[h]}" aria-pressed="${(style.hair || 'long') === h}"><span class="face-avatar hair-thumb">${animeSVG({ palette: style.palette, hair: h }).replace('viewBox="0 0 200 200"', 'viewBox="8 2 184 184"')}</span></button>`).join('');
        $('[data-palettes]', form).style.display = anime ? '' : 'none';
        $('[data-hairs]', form).style.display = face === 'anime' ? '' : 'none';
        $('[data-palette-label]', form).style.display = anime ? '' : 'none';
        $('[data-palette-label]', form).textContent = face === 'anime' ? 'Colour' : 'Stand-in colour (while the model loads)';
        $('[data-hair-label]', form).style.display = face === 'anime' ? '' : 'none';
        $$('[data-palette]', form).forEach(b => b.addEventListener('click', () => { style = { ...style, palette: b.dataset.palette }; paintLooks(); rebuildPreview(); }));
        $$('[data-hair]', form).forEach(b => b.addEventListener('click', () => { style = { ...style, hair: b.dataset.hair }; paintLooks(); rebuildPreview(); }));
      };
      const paintModels = () => {
        const host = $('[data-models]', form);
        if (face !== 'live2d' && face !== 'vrm') { host.innerHTML = ''; return; }
        const mine = faces.filter(f => f.kind === face);
        const fr = { zoom: 1, y: 0, mirror: false, ...(style.framing || {}) };
        host.innerHTML = `<div class="meta" style="margin:6px 0">${face === 'live2d'
          ? 'A .zip of a Cubism 3/4 model (with its .model3.json). Live2D needs Live2D\'s own runtime, which Cerebro downloads with your agreement.'
          : 'A .vrm file — make one free in VRoid Studio.'} Until it loads, the anime face stands in.</div>
          <div class="work-tabs">${mine.map(f => `<button type="button" class="btn sm" data-model="${f.id}" data-entry="${esc(f.entry)}" aria-pressed="${style.model === f.id}">${esc(f.name)}</button>`).join('')}
          <button type="button" class="btn sm primary" data-upload>Load a model…</button></div>
          ${style.model ? `<div class="slider-grid" style="margin-top:8px">
            <label>Zoom<input type="range" data-frame="zoom" min="0.5" max="2.5" step="0.05" value="${fr.zoom}"></label>
            <label>Height<input type="range" data-frame="y" min="-0.6" max="0.6" step="0.02" value="${fr.y}"></label>
            ${face === 'live2d' ? `<label class="check"><input type="checkbox" data-frame="mirror" ${fr.mirror ? 'checked' : ''}> Mirror</label>` : ''}
          </div><div data-mapping class="mapping"><div class="meta">Loading the model's expressions…</div></div>
          ${face === 'vrm' ? '<div data-gestures class="mapping"></div>' : ''}` : ''}`;
        $$('[data-model]', host).forEach(b => b.addEventListener('click', () => {
          style = { ...style, model: b.dataset.model, entry: b.dataset.entry, expressions: {}, gestures: {} };
          paintModels(); rebuildPreview();
        }));
        $('[data-upload]', host).addEventListener('click', () => pickFile(face === 'vrm' ? '.vrm' : '.zip', async file => {
          const form2 = new FormData();
          form2.append('file', file, file.name);
          try {
            const meta = await api.post('/api/faces', form2);
            faces.push(meta);
            style = { ...style, model: meta.id, entry: meta.entry, expressions: {}, gestures: {} };
            if (meta.kind === 'live2d') await ensureLive2D();
            paintModels(); rebuildPreview();
          } catch (error) { toast(error.message, 'err'); }
        }));
        let frameTimer = 0;
        $$('[data-frame]', host).forEach(input => input.addEventListener('input', () => {
          const framing = { ...(style.framing || {}) };
          framing[input.dataset.frame] = input.type === 'checkbox' ? input.checked : Number(input.value);
          style = { ...style, framing };
          clearTimeout(frameTimer);
          frameTimer = setTimeout(rebuildPreview, 250);
        }));
      };
      const paintModelPanels = () => {
        const mapHost = $('[data-mapping]', form);
        if (!mapHost || !preview?.listExpressions) return;
        const names = preview.listExpressions();
        const guess = preview.guesses?.() || {};
        const chosen = style.expressions || {};
        if (!names.length) { mapHost.innerHTML = '<div class="meta">This model has no expressions Cerebro can use; it still blinks, breathes, looks around and lip-syncs.</div>'; return; }
        mapHost.innerHTML = `<div class="mini-label">Expressions — what it shows for each moment
            <button type="button" class="btn sm ghost" data-auto>Auto-match</button></div>
          ${MODEL_SLOTS.map(slot => {
            const pick = chosen[slot];
            const current = pick ? pick.name : (guess[slot] || '');
            return `<div class="map-row"><span>${esc(SLOT_LABEL[slot] || slot)}</span>
              <select class="input sm" data-map="${slot}"><option value="">— nothing —</option>${names.map(n =>
                `<option value="${esc(n)}" ${n === current ? 'selected' : ''}>${esc(n)}${!pick && n === guess[slot] ? ' (auto)' : ''}</option>`).join('')}</select>
              <input type="range" data-weight="${slot}" min="0.1" max="1" step="0.05" value="${pick?.weight ?? (slot === 'calm' ? 0.35 : 1)}" title="Strength">
              <button type="button" class="icon-btn" data-play="${slot}" title="Preview">▶</button></div>`;
          }).join('')}`;
        const save = slot => {
          const name = $(`[data-map="${slot}"]`, mapHost).value;
          const weight = Number($(`[data-weight="${slot}"]`, mapHost).value);
          style = { ...style, expressions: { ...(style.expressions || {}), [slot]: { name, weight } } };
        };
        $$('[data-map]', mapHost).forEach(sel => sel.addEventListener('change', () => save(sel.dataset.map)));
        $$('[data-weight]', mapHost).forEach(r => r.addEventListener('change', () => save(r.dataset.weight)));
        $$('[data-play]', mapHost).forEach(b => b.addEventListener('click', () => {
          const name = $(`[data-map="${b.dataset.play}"]`, mapHost).value;
          if (name) preview.preview?.(name);
        }));
        $('[data-auto]', mapHost).addEventListener('click', () => { style = { ...style, expressions: {} }; paintModelPanels(); });
        const gHost = $('[data-gestures]', form);
        if (gHost) {
          const meta = faces.find(f => f.id === style.model) || {};
          const files = meta.gestures || [];
          const gestures = style.gestures || {};
          gHost.innerHTML = `<div class="mini-label">Gestures (.vrma animations)
              <button type="button" class="btn sm ghost" data-add-gesture>Add gesture…</button></div>
            ${files.length ? Object.entries(GESTURE_LABEL).map(([event, label]) => `<div class="map-row"><span>${label}</span>
              <select class="input sm" data-gesture="${event}"><option value="">—</option>${files.map(f =>
                `<option value="${esc(f)}" ${gestures[event] === f ? 'selected' : ''}>${esc(f.replace('gestures/', '').replace('.vrma', ''))}</option>`).join('')}</select></div>`).join('')
              : '<div class="meta">Add a wave, a nod or a cheer as a .vrma file (VRM Animation), then pick when it plays.</div>'}`;
          $$('[data-gesture]', gHost).forEach(sel => sel.addEventListener('change', () => {
            style = { ...style, gestures: { ...(style.gestures || {}), [sel.dataset.gesture]: sel.value } };
          }));
          $('[data-add-gesture]', gHost).addEventListener('click', () => pickFile('.vrma', async file => {
            const form2 = new FormData();
            form2.append('file', file, file.name);
            try {
              const out = await api.post(`/api/faces/${encodeURIComponent(style.model)}/gestures`, form2);
              const index = faces.findIndex(f => f.id === style.model);
              if (index >= 0) faces[index] = out.face;
              paintModelPanels();
            } catch (error) { toast(error.message, 'err'); }
          }));
        }
      };
      $$('[data-face]', form).forEach(b => b.addEventListener('click', async () => {
        face = b.dataset.face;
        $$('[data-face]', form).forEach(x => x.setAttribute('aria-pressed', String(x === b)));
        if (face === 'live2d') await ensureLive2D();
        paintLooks(); paintModels(); rebuildPreview();
      }));
      // Voice & personality.
      const fillVoices = async () => {
        const provider = $('[data-provider]', form).value || vstatus?.voice?.provider || 'auto';
        $('[data-engine-wrap]', form).style.display = ['', 'auto', 'polly'].includes(provider) ? '' : 'none';
        const list = await api.get(`/api/voice/voices?provider=${encodeURIComponent(provider === 'auto' ? 'polly' : provider)}`).catch(() => ({ voices: [] }));
        $('#voice-names', form).innerHTML = (list.voices || []).map(v => `<option value="${esc(v.id)}">${esc(v.label || (v.engines || []).join(', '))}</option>`).join('');
      };
      $('[data-provider]', form).addEventListener('change', fillVoices);
      $('[data-persona]', form).addEventListener('change', e => {
        persona = { ...persona, preset: e.target.value };
        const defaults = { sweetheart: [0.75, 0.75, 0.2], warm: [0.35, 0.45, 0.4], energetic: [0.55, 0.85, 0.25],
          calm: [0.15, 0.3, 0.45], professional: [0, 0.1, 0.85], custom: [0.5, 0.5, 0.4] }[persona.preset];
        ['playfulness', 'expressiveness', 'formality'].forEach((k, i) => {
          $(`[data-persona-slider="${k}"]`, form).value = defaults[i];
          persona[k] = defaults[i];
        });
      });
      const collectVoice = () => {
        const out = { provider: $('[data-provider]', form).value, engine: $('[data-engine]', form).value,
          voice: $('[data-voice]', form).value.trim() };
        $$('[data-voice-slider]', form).forEach(r => { out[r.dataset.voiceSlider] = Number(r.value); });
        $$('[data-voice-flag]', form).forEach(c => { out[c.dataset.voiceFlag] = c.checked; });
        return out;
      };
      const collectPersona = () => {
        const out = { ...persona, preset: $('[data-persona]', form).value, custom: $('[data-custom]', form).value.trim() };
        $$('[data-persona-slider]', form).forEach(r => { out[r.dataset.personaSlider] = Number(r.value); });
        return out;
      };
      form._collect = () => ({ vstyle: collectVoice(), persona: collectPersona() });
      $('[data-try]', form).addEventListener('click', async e => {
        const button = e.currentTarget;
        button.disabled = true;
        $('[data-try-note]', form).textContent = 'Speaking…';
        try {
          const response = await fetch('/api/voice/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: PREVIEW_LINE, agent_id: agent?.id || null, style: collectVoice(), personality: collectPersona() }) });
          if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'No voice could speak');
          const audio = new Audio(URL.createObjectURL(await response.blob()));
          lipSync(audio, (level, bands) => preview?.setMouth(level, bands));
          preview?.react?.('happy', 1800);
          audio.play();
          $('[data-try-note]', form).textContent = `Spoken by ${response.headers.get('X-Voice-Provider') || 'the voice'}`;
        } catch (error) {
          $('[data-try-note]', form).textContent = error.message;
        } finally { button.disabled = false; }
      });
      paintLooks(); paintModels(); rebuildPreview(); fillVoices();
    },
  });
  const form = $('#sheet');
  const collected = form?._collect ? form._collect() : { vstyle, persona };
  if (form) form._collect = null;
  preview?.destroy();
  if (!values) return;
  const chosen = $$('[data-scope]', form).filter(b => b.checked).map(b => b.dataset.scope);
  const body = { name: values.name, role: values.role, instructions: values.instructions,
    face, face_style: style, voice_name: collected.vstyle.voice || '', voice_rate: collected.vstyle.rate,
    voice_style: collected.vstyle, personality: collected.persona, tool_scope: chosen };
  try {
    const saved = agent ? await api.patch(`/api/agents/${agent.id}`, body)
      : await api.post('/api/agents', { template, ...body });
    toast(agent ? 'Saved' : `${saved.name} is ready`, 'ok');
    await loadAgents();
    if (!agent) selectAgent(saved.id);
    else paintStage(true);
  } catch (error) { toast(error.message, 'err'); }
}

async function ensureLive2D() {
  const info = await api.get('/api/faces/live2d/runtime').catch(() => ({ installed: false }));
  if (info.installed) return true;
  if (!confirm(`${info.terms || 'Live2D models need Live2D\'s Cubism Core.'}\n\nDownload it from Live2D now?`)) return false;
  try {
    await api.post('/api/faces/live2d/runtime', { agree: true });
    toast('Live2D runtime installed', 'ok');
    return true;
  } catch (error) { toast(error.message, 'err'); return false; }
}

async function removeAgent(agent) {
  if (!confirm(`Remove ${agent.name}? Its chats and jobs stay readable.`)) return;
  await api.del(`/api/agents/${agent.id}`).catch(error => toast(error.message, 'err'));
  if (state.agentId === agent.id) state.agentId = null;
  await loadAgents();
  selectAgent(state.agentId || state.agents[0]?.id);
}

// ------------------------------------------------------------------ jobs
const JOB_STATUS = {
  queued: ['accent', 'Queued'], running: ['accent', 'Working'], active: ['', 'Scheduled'],
  waiting_input: ['warn', 'Needs your answer'], waiting_approval: ['warn', 'Needs approval'],
  paused: ['', 'Paused'], needs_review: ['warn', 'To review'], done: ['ok', 'Done'],
  failed: ['err', 'Failed'], cancelled: ['', 'Cancelled'],
};

function jobRow(job, { open = false } = {}) {
  const node = document.createElement('div');
  node.className = 'job-row';
  const plan = job.plan || [];
  const done = plan.filter(p => p.status === 'done').length;
  const [kind, label] = JOB_STATUS[job.status] || ['', job.status];
  const controls = [];
  if (['queued', 'running', 'active', 'waiting_input', 'waiting_approval'].includes(job.status)) controls.push(['pause', 'Pause']);
  if (job.status === 'paused') controls.push(['resume', 'Resume']);
  if (['failed', 'cancelled', 'done', 'needs_review'].includes(job.status)) controls.push(['retry', job.status === 'done' ? 'Run again' : 'Retry']);
  if (!['done', 'cancelled'].includes(job.status)) controls.push(['cancel', 'Cancel']);
  node.innerHTML = `
    <div class="job-top"><span class="job-title" title="${esc(job.instruction || job.title)}">${esc(job.title)}</span><span class="badge ${kind}">${esc(label)}</span></div>
    ${plan.length ? `<div class="progress"><span style="width:${Math.round(100 * done / plan.length)}%"></span></div>` : ''}
    ${job.question && job.status === 'waiting_input' ? `<div class="question">${esc(job.question)}</div>
      <div class="answer-row"><input class="input" data-answer placeholder="Your answer"><button class="btn sm primary" data-send>Answer</button></div>` : ''}
    ${job.error && job.status === 'failed' ? `<div class="card-error">${esc(job.error)}</div>` : ''}
    <div class="job-detail" ${open ? '' : 'hidden'}></div>
    <div class="btn-row" style="margin:0">${controls.map(([a, l]) => `<button class="btn sm ghost" data-act="${a}">${l}</button>`).join('')}
      ${job.conversation_id ? '<button class="btn sm ghost" data-chat>Open chat</button>' : ''}</div>`;
  const detail = $('.job-detail', node);
  const showDetail = async () => {
    const full = await api.get(`/api/jobs/${job.id}`).catch(() => null);
    if (!full) return;
    detail.innerHTML = `${(full.plan || []).length ? `<ul class="plan-list">${full.plan.map(p => `<li data-status="${esc(p.status)}">${esc(p.title)}</li>`).join('')}</ul>` : ''}
      <ul class="event-log">${(full.events || []).slice(-40).map(e => `<li><time>${esc(relTime(e.created_at))}</time><span><b>${esc(e.title)}</b>${e.detail ? ` — ${esc(e.detail.slice(0, 220))}` : ''}</span></li>`).join('')}</ul>`;
  };
  $('.job-title', node).addEventListener('click', () => { detail.hidden = !detail.hidden; if (!detail.hidden) showDetail(); });
  if (open) showDetail();
  $$('[data-act]', node).forEach(b => b.addEventListener('click', async () => {
    b.disabled = true;
    const result = await api.post(`/api/jobs/${job.id}/${b.dataset.act}`).catch(e => ({ detail: e.message }));
    if (result.ok === false || result.detail) toast(result.detail || 'Done', result.ok === false ? 'err' : 'ok');
    refreshWorkViews();
  }));
  $('[data-send]', node)?.addEventListener('click', async () => {
    const answer = $('[data-answer]', node).value.trim();
    if (!answer) return;
    await api.post(`/api/jobs/${job.id}/answer`, { answer }).catch(e => toast(e.message, 'err'));
    toast('Thanks — it carries on', 'ok');
    refreshWorkViews();
  });
  $('[data-chat]', node)?.addEventListener('click', () => {
    if (job.agent_id && job.agent_id !== state.agentId) { state.agentId = job.agent_id; paintAgents(); paintStage(true); loadChats(); }
    selectTab('ask'); openChat(job.conversation_id);
  });
  return node;
}

function ideaRow(idea) {
  const node = document.createElement('div');
  node.className = 'idea-row';
  node.innerHTML = `<b>${esc(idea.title)}</b><span>${esc(idea.reason || '')}</span>
    ${(idea.evidence || []).length ? `<div class="evidence">${idea.evidence.slice(0, 3).map(e => `• ${esc(e.title)}${e.excerpt ? ` — ${esc(e.excerpt.slice(0, 120))}` : ''}`).join('<br>')}</div>` : ''}
    <div class="btn-row" style="margin:0"><button class="btn sm primary" data-accept>Do it</button>
      <button class="btn sm ghost" data-edit>Edit first…</button><button class="btn sm ghost" data-dismiss>Dismiss</button></div>`;
  const decide = async (action, prompt) => {
    const result = await api.post(`/api/ideas/${idea.id}/${action}`, prompt ? { prompt } : {}).catch(e => ({ ok: false, detail: e.message }));
    toast(result.ok ? (action === 'accept' ? 'Started as a job' : 'Dismissed') : result.detail, result.ok ? 'ok' : 'err');
    refreshWorkViews();
  };
  $('[data-accept]', node).addEventListener('click', () => decide('accept'));
  $('[data-dismiss]', node).addEventListener('click', () => decide('dismiss'));
  $('[data-edit]', node).addEventListener('click', async () => {
    const values = await sheet({ title: idea.title, ok: 'Do it', body: `<label class="field"><span>What to do</span><textarea class="input" name="prompt" rows="5">${esc(idea.prompt)}</textarea></label>` });
    if (values?.prompt) decide('accept', values.prompt);
  });
  return node;
}

async function newJob(agent = currentAgent(), project = null) {
  const values = await sheet({
    title: `New job for ${agent?.name || 'Cerebro'}`, ok: 'Start',
    body: `<label class="field"><span>What should it do?</span><textarea class="input" name="instruction" rows="4" required placeholder="e.g. Go through yesterday's escalations and draft a summary for the team"></textarea></label>
      <label class="field"><span>When</span><select class="input" name="schedule">
        ${[['once', 'Now, once'], ['hourly', 'Every hour'], ['daily', 'Every day'], ['weekdays', 'Weekdays'], ['weekly', 'Every week'], ['manual', 'Only when I run it']].map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}
      </select></label>
      <label class="field"><span>At (for daily/weekly)</span><input class="input" name="at_time" placeholder="09:00"></label>`,
  });
  if (!values?.instruction) return;
  const job = await api.post('/api/jobs', { instruction: values.instruction, agent_id: agent?.id, schedule: values.schedule,
    at_time: values.at_time || null, project_id: project?.id || null }).catch(e => { toast(e.message, 'err'); return null; });
  if (job) { toast(`${agent?.name || 'Cerebro'} is on it`, 'ok'); refreshWorkViews(); }
}

function refreshWorkViews() {
  loadAgents();
  if (state.tab === 'home') loadHome();
  if (state.tab === 'work') loadWork();
}

// ------------------------------------------------------------------ Home
async function loadHome() {
  const body = $('#home-body');
  const [data, today] = await Promise.all([
    api.get('/api/home').catch(error => ({ error: error.message })),
    api.get('/api/today').catch(() => null)]);
  if (data.error) { body.innerHTML = `<div class="empty-note">${esc(data.error)}</div>`; return; }
  body.innerHTML = `
    <div class="section-title">Today<span class="grow"></span>
      <span class="meta">${esc(new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }))}</span></div>
    <div class="today" id="today"></div>
    <div id="home-updates"></div>
    <div class="section-title">Your agents<span class="grow"></span>
      <button class="btn sm" id="ideas-refresh">Look for ideas</button><button class="btn sm primary" id="home-new-agent">+ Agent</button></div>
    <div class="board" id="board"></div>
    <div id="home-fired"></div>`;
  const board = $('#board');
  for (const agent of data.agents || []) {
    const card = document.createElement('div');
    card.className = 'agent-card glass';
    card.style.setProperty('--agent', agent.color || '#8b7cf6');
    card.innerHTML = `
      <div class="agent-card-head"><span class="agent-av">${faceAvatar(agent, 48)}</span>
        <div style="min-width:0;flex:1"><h4>${esc(agent.name)}</h4><div class="meta">${esc(agent.role || 'General')} · ${esc(agentStatusText(agent))}</div></div>
        <button class="btn sm" data-chat>Chat</button><button class="btn sm ghost" data-job>+ Job</button></div>
      <div data-jobs></div><div data-ideas></div>`;
    const jobsHost = $('[data-jobs]', card);
    (agent.jobs || []).slice(0, 4).forEach(job => jobsHost.append(jobRow(job)));
    if (!(agent.jobs || []).length) jobsHost.innerHTML = '<div class="meta">No jobs on the go.</div>';
    const ideasHost = $('[data-ideas]', card);
    (agent.ideas || []).forEach(idea => ideasHost.append(ideaRow(idea)));
    $('[data-chat]', card).addEventListener('click', () => { selectTab('ask'); selectAgent(agent.id); });
    $('[data-job]', card).addEventListener('click', () => newJob(agent));
    board.append(card);
  }
  if (today) paintToday($('#today'), today);
  const updates = data.updates || [];
  if (updates.length) {
    $('#home-updates').innerHTML = '<div class="section-title">Kept updated<span class="grow"></span></div><div class="updates-grid"></div>';
    updates.forEach(u => $('#home-updates .updates-grid').append(keepCard(u)));
  }
  const fired = data.watches_fired || [];
  if (fired.length) {
    $('#home-fired').innerHTML = `<div class="section-title">Watches that fired<span class="grow"></span></div>
      ${fired.map(w => `<div class="job-row"><div class="job-top"><span class="job-title">${esc(w.title)}</span><span class="meta">${esc(relTime(w.last_alert_at))}</span></div>
        <span class="meta">${esc(w.target)}</span></div>`).join('')}`;
  }
  $('#home-new-agent').addEventListener('click', e => showMenu(e.currentTarget,
    Object.entries(state.templates).map(([key, t]) => [t.name === 'New agent' ? 'Blank agent…' : `${t.name}…`, () => editAgent(null, key)])));
  $('#ideas-refresh').addEventListener('click', async () => {
    const result = await api.post('/api/ideas/refresh').catch(() => ({ made: 0 }));
    toast(result.made ? `${result.made} new idea(s)` : 'No new ideas right now', 'ok');
    loadHome();
  });
}

const TODO_KIND = { waiting_on: ['Waiting on', 'warn'], promised: ['You promised', 'accent'], asked: ['Asked of you', 'accent'], todo: ['To do', ''] };

function paintToday(host, today) {
  const agenda = today.agenda;
  const now = Date.now();
  const agendaHTML = !today.calendar_connected
    ? '<div class="meta">Connect Outlook (Connect → Outlook) to see your meetings here.</div>'
    : today.calendar_error ? `<div class="card-error">${esc(today.calendar_error)}</div>`
    : !(agenda || []).length ? '<div class="meta">No meetings today.</div>'
    : agenda.map(e => {
        const start = new Date(e.start), end = new Date(e.end);
        const state = end < now ? 'past' : start <= now ? 'now' : '';
        const time = e.all_day ? 'All day' : start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        const people = (e.attendees || []).slice(0, 4).map(a => a.name || a.email).join(', ');
        return `<div class="agenda-row ${state}"><span class="agenda-time">${esc(time)}</span>
          <div style="min-width:0"><div class="agenda-title">${esc(e.subject)}</div>
          <div class="meta">${esc([e.location, people].filter(Boolean).join(' · '))}</div></div>
          ${e.online_url && state !== 'past' ? `<button class="btn sm ghost" data-join="${esc(e.online_url)}">Join</button>` : ''}</div>`;
      }).join('');
  const todos = today.todos || [];
  const todoHTML = todos.length ? todos.slice(0, 12).map(t => {
    const [label, tone] = TODO_KIND[t.kind] || TODO_KIND.todo;
    const due = t.due_at ? new Date(t.due_at) : null;
    const late = due && due < now;
    return `<div class="todo-row" data-id="${t.id}"><input type="checkbox" data-done title="Done">
      <div style="min-width:0;flex:1"><div class="todo-what">${esc(t.what)}</div>
        <div class="meta"><span class="badge ${tone}">${esc(label)}${t.who && t.kind !== 'todo' ? ` · ${esc(t.who)}` : ''}</span>
          ${due ? `<span class="${late ? 'late' : ''}">${late ? 'overdue · ' : 'due '}${esc(due.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' }))}</span>` : ''}
          ${t.evidence ? `<span title="${esc(t.evidence)}">· why?</span>` : ''}</div></div>
      ${t.chase ? '<button class="btn sm" data-chase>Chase</button>' : ''}
      <button class="icon-btn" data-snooze title="Snooze a day" aria-label="Snooze">${ICONS.clock}</button></div>`;
  }).join('') : '<div class="meta">Nothing to chase or deliver. Ask “remind me to…” or say it in a meeting.</div>';
  const brief = today.briefing;
  const waiting = today.waiting || [];
  host.innerHTML = `
    <div class="today-card glass"><div class="today-head"><h4>Agenda</h4><span class="grow"></span>
      ${today.calendar_connected ? '<button class="btn sm ghost" data-ask="Find me 30 minutes free this week">Find time</button>' : ''}</div>${agendaHTML}</div>
    <div class="today-card glass"><div class="today-head"><h4>To-dos & follow-ups</h4><span class="badge">${todos.length}</span><span class="grow"></span>
      <button class="btn sm ghost" data-add-todo>+ Add</button></div>${todoHTML}</div>
    <div class="today-card glass"><div class="today-head"><h4>${esc(brief ? brief.title : 'Briefing')}</h4><span class="grow"></span>
      ${brief ? '<button class="icon-btn" data-speak title="Read it to me" aria-label="Read it to me">▶</button>' : ''}
      <button class="btn sm ghost" data-brief>${brief ? 'Refresh' : 'Brief me'}</button></div>
      ${brief ? `<div class="md brief">${renderMarkdown(brief.content || '')}</div>` : '<div class="meta">Your morning briefing appears here on weekdays (Settings → Secretary).</div>'}</div>
    ${waiting.length || today.approvals ? `<div class="today-card glass"><div class="today-head"><h4>Waiting on you</h4><span class="grow"></span></div>
      ${today.approvals ? `<div class="meta">${today.approvals} change${today.approvals === 1 ? '' : 's'} to approve — open the chat it's in, or Activity.</div>` : ''}
      ${waiting.map(j => `<div class="todo-row"><div style="flex:1;min-width:0"><div class="todo-what">${esc(j.title)}</div>
        <div class="meta">${j.status === 'waiting_input' ? esc(j.question || 'Has a question') : 'Waiting for approval'}</div></div>
        ${j.conversation_id ? `<button class="btn sm" data-open-chat="${j.conversation_id}">Open</button>` : ''}</div>`).join('')}</div>` : ''}`;
  $$('[data-join]', host).forEach(b => b.addEventListener('click', () => openExternal(b.dataset.join)));
  $$('[data-ask]', host).forEach(b => b.addEventListener('click', () => { selectTab('ask'); input.value = b.dataset.ask; input.dispatchEvent(new Event('input')); send(); }));
  $$('[data-open-chat]', host).forEach(b => b.addEventListener('click', () => { selectTab('ask'); openChat(Number(b.dataset.openChat)); }));
  $$('.todo-row[data-id]', host).forEach(row => {
    const id = row.dataset.id;
    $('[data-done]', row)?.addEventListener('change', async () => { await api.post(`/api/followups/${id}/done`).catch(() => {}); row.classList.add('done'); setTimeout(loadHome, 400); });
    $('[data-snooze]', row)?.addEventListener('click', async () => { await api.post(`/api/followups/${id}/snooze`, { days: 1 }).catch(() => {}); toast('Snoozed until tomorrow', 'ok'); loadHome(); });
    $('[data-chase]', row)?.addEventListener('click', async e => {
      e.currentTarget.disabled = true;
      try {
        const { card } = await api.post(`/api/followups/${id}/chase`);
        const holder = document.createElement('div');
        holder.className = 'chase-draft';
        const element = actionCard(card);
        if (element) { holder.append(element); row.after(holder); }
      } catch (error) { toast(error.message, 'err'); }
    });
  });
  $('[data-add-todo]', host).addEventListener('click', async () => {
    const values = await sheet({ title: 'Add a to-do', ok: 'Add',
      body: `<label class="field"><span>What</span><input class="input" name="what" required></label>
        <label class="field"><span>Due (optional)</span><input class="input" name="due" placeholder="tomorrow, friday, 12/11"></label>` });
    if (!values?.what?.trim()) return;
    await api.post('/api/followups', { what: values.what, due: values.due || null }).catch(err => toast(err.message, 'err'));
    loadHome();
  });
  $('[data-brief]', host).addEventListener('click', async e => {
    e.currentTarget.disabled = true;
    await api.post('/api/briefings/morning').catch(err => toast(err.message, 'err'));
    loadHome();
  });
  $('[data-speak]', host)?.addEventListener('click', () => speakText(brief?.data?.spoken || brief?.content || ''));
}

async function speakText(text) {
  if (!text) return;
  try {
    const response = await fetch('/api/voice/speak', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, agent_id: state.agentId }) });
    if (!response.ok) { toast('Voice replies are off (Settings → Voice).', 'err'); return; }
    voice.audio?.pause();
    voice.audio = new Audio(URL.createObjectURL(await response.blob()));
    voice.audio.play();
  } catch { toast("Couldn't speak it.", 'err'); }
}

// ------------------------------------------------------------------ Work
async function newUpdate(agent) {
  const values = await sheet({ title: 'Keep something updated', ok: 'Start',
    body: `<p class="sheet-note">A link (SharePoint, an internal site or wiki), a Word/Excel file, or a program. Cerebro connects it, makes the job, and does the first update now — changes wait for your approval until you let it update by itself.</p>
      <label class="field"><span>What to keep updated</span><input class="input" name="target" placeholder="https://…  ·  C:\\Reports\\Status.xlsx  ·  Billing tool" required></label>
      <label class="field"><span>What to change each time</span><textarea class="input" name="instructions" rows="2" required></textarea></label>
      <label class="field"><span>From (optional)</span><input class="input" name="source" placeholder="A link or file the updates come from"></label>
      <label class="field"><span>How often</span><input class="input" name="when" placeholder="daily at 9 · weekdays at 17:00 · when the source changes"></label>` });
  if (!values?.target?.trim() || !values?.instructions?.trim()) return;
  selectTab('ask');
  selectAgent(agent.id);
  input.value = `Keep ${values.target} updated: ${values.instructions}${values.source ? ` (from ${values.source})` : ''}${values.when ? `, ${values.when}` : ''}`;
  input.dispatchEvent(new Event('input'));
  send();
}

let workView = 'jobs';
async function loadWork() {
  const agent = currentAgent();
  const body = $('#work-body');
  if (!agent) { body.innerHTML = ''; return; }
  body.innerHTML = `
    <div class="section-title">${esc(agent.name)}'s work<span class="grow"></span></div>
    <div class="work-tabs">${[['jobs', 'Jobs'], ['updates', 'Updates'], ['projects', 'Projects'], ['watches', 'Watches'], ['ideas', 'Ideas']].map(([k, l]) =>
      `<button class="btn sm" data-view="${k}" aria-pressed="${workView === k}">${l}</button>`).join('')}
      <span class="grow" style="flex:1"></span><button class="btn sm primary" data-add></button></div>
    <div id="work-list"></div>`;
  $$('[data-view]', body).forEach(b => b.addEventListener('click', () => { workView = b.dataset.view; loadWork(); }));
  const add = $('[data-add]', body);
  add.textContent = { jobs: '+ Job', updates: '+ Keep updated', projects: '+ Project', watches: '+ Watch', ideas: 'Look for ideas' }[workView];
  add.addEventListener('click', () => ({ jobs: () => newJob(agent), updates: () => newUpdate(agent), projects: () => newProject(agent),
    watches: () => newWatch(agent), ideas: async () => { await api.post('/api/ideas/refresh'); loadWork(); } })[workView]());
  const list = $('#work-list');
  if (workView === 'jobs') {
    const { jobs } = await api.get(`/api/jobs?agent_id=${agent.id}&status=`).catch(() => ({ jobs: [] }));
    if (!jobs.length) list.innerHTML = '<div class="empty-note">No jobs yet. Give it one with + Job, or ask in chat.</div>';
    const open = jobs.filter(j => !['done', 'cancelled'].includes(j.status));
    const closed = jobs.filter(j => ['done', 'cancelled'].includes(j.status)).slice(0, 15);
    open.forEach(j => list.append(jobRow(j)));
    if (closed.length) {
      const head = document.createElement('div');
      head.className = 'section-title';
      head.innerHTML = 'Finished<span class="grow"></span>';
      list.append(head);
      closed.forEach(j => list.append(jobRow(j)));
    }
  } else if (workView === 'updates') {
    const { updates } = await api.get(`/api/updates?agent_id=${agent.id}`).catch(() => ({ updates: [] }));
    if (!updates.length) list.innerHTML = '<div class="empty-note">Nothing kept updated yet. Ask: “keep this page updated from the export every morning”, with the link or file.</div>';
    else {
      const grid = document.createElement('div');
      grid.className = 'updates-grid';
      updates.forEach(u => grid.append(keepCard(u)));
      list.append(grid);
    }
  } else if (workView === 'projects') {
    const { projects } = await api.get(`/api/projects?agent_id=${agent.id}`).catch(() => ({ projects: [] }));
    if (!projects.length) list.innerHTML = '<div class="empty-note">No projects yet.</div>';
    for (const project of projects) {
      const card = document.createElement('div');
      card.className = 'agent-card glass';
      card.innerHTML = `<div class="agent-card-head"><div style="flex:1;min-width:0"><h4>${esc(project.title)}</h4>
          <div class="meta">${esc(project.description || '')}</div></div><span class="badge ${project.status === 'done' ? 'ok' : ''}">${esc(project.status)}</span>
          <button class="btn sm ghost" data-job>+ Job</button></div>
        <div class="progress"><span style="width:${Math.round(100 * project.progress)}%"></span></div>
        <ul class="milestones">${project.milestones.map(m => `<li><label><input type="checkbox" data-m="${esc(m.id)}" ${m.done ? 'checked' : ''}> ${esc(m.title)}</label></li>`).join('')}</ul>
        <div data-jobs></div>`;
      $$('[data-m]', card).forEach(box => box.addEventListener('change', async () => {
        const milestones = project.milestones.map(m => m.id === box.dataset.m ? { ...m, done: box.checked } : m);
        await api.patch(`/api/projects/${project.id}`, { milestones, status: milestones.every(m => m.done) ? 'done' : 'active' });
        loadWork();
      }));
      $('[data-job]', card).addEventListener('click', () => newJob(agent, project));
      project.jobs.forEach(j => $('[data-jobs]', card).append(jobRow(j)));
      list.append(card);
    }
  } else if (workView === 'watches') {
    const { watches } = await api.get(`/api/watches?agent_id=${agent.id}`).catch(() => ({ watches: [] }));
    if (!watches.length) list.innerHTML = '<div class="empty-note">Not watching anything. + Watch, or ask: “tell me when this page changes”.</div>';
    for (const w of watches) {
      const row = document.createElement('div');
      row.className = 'job-row';
      row.innerHTML = `<div class="job-top"><span class="job-title">${esc(w.title)}</span><span class="badge ${w.status === 'active' ? 'ok' : ''}">${esc(w.status)}</span></div>
        <span class="meta">${esc(w.describe)} · every ${w.interval_minutes} min${w.last_checked_at ? ` · checked ${esc(relTime(w.last_checked_at))}` : ''}</span>
        ${w.last_error ? `<div class="card-error">${esc(w.last_error)}</div>` : ''}
        <div class="btn-row" style="margin:0"><button class="btn sm ghost" data-w="check">Check now</button>
          <button class="btn sm ghost" data-w="${w.status === 'active' ? 'pause' : 'resume'}">${w.status === 'active' ? 'Pause' : 'Resume'}</button>
          <button class="btn sm ghost" data-w="stop">Stop</button></div>`;
      $$('[data-w]', row).forEach(b => b.addEventListener('click', async () => {
        const result = await api.post(`/api/watches/${w.id}/${b.dataset.w}`).catch(e => ({ detail: e.message }));
        if (b.dataset.w === 'check') toast(result.fired ? `It fired: ${result.detail}` : (result.detail || result.error || 'Checked'), result.ok === false ? 'err' : 'ok');
        loadWork();
      }));
      list.append(row);
    }
  } else {
    const { ideas } = await api.get(`/api/ideas?agent_id=${agent.id}`).catch(() => ({ ideas: [] }));
    if (!ideas.length) list.innerHTML = '<div class="empty-note">No ideas right now.</div>';
    ideas.forEach(i => list.append(ideaRow(i)));
  }
}

async function newProject(agent) {
  const values = await sheet({ title: 'New project', ok: 'Create', body: `
    <label class="field"><span>Name</span><input class="input" name="title" required></label>
    <label class="field"><span>What it's for</span><input class="input" name="description"></label>
    <label class="field"><span>Milestones (one per line)</span><textarea class="input" name="milestones" rows="4"></textarea></label>` });
  if (!values?.title) return;
  await api.post('/api/projects', { title: values.title, description: values.description, agent_id: agent.id,
    milestones: (values.milestones || '').split('\n').map(s => s.trim()).filter(Boolean) }).catch(e => toast(e.message, 'err'));
  loadWork();
}

async function newWatch(agent) {
  const values = await sheet({ title: 'Watch a page', ok: 'Watch', body: `
    <label class="field"><span>Page address</span><input class="input" name="url" required placeholder="https://…"></label>
    <label class="field"><span>Name</span><input class="input" name="title" placeholder="e.g. CAS-01234 status"></label>
    <label class="field"><span>Tell me when</span><select class="input" name="condition">
      <option value="change">it changes</option><option value="contains">this text appears</option>
      <option value="absent">this text is gone</option><option value="below">the number goes below</option><option value="above">the number goes above</option></select></label>
    <label class="field"><span>Text or number</span><input class="input" name="value"></label>
    <label class="field"><span>Check every (minutes)</span><input class="input" name="interval_minutes" value="60"></label>
    <label class="field"><span>Then (optional job)</span><input class="input" name="follow_up" placeholder="e.g. Draft an update to the customer"></label>` });
  if (!values?.url) return;
  await api.post('/api/watches', { ...values, interval_minutes: Number(values.interval_minutes) || 60, agent_id: agent.id })
    .then(() => toast('Watching it', 'ok')).catch(e => toast(e.message, 'err'));
  workView = 'watches';
  loadWork();
}

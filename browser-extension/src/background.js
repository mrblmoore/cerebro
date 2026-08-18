/**
 * Cerebro background service worker.
 *
 * Watches browser activity and reports three things to the local Cerebro API:
 * CRM cases, documents opened from SharePoint/OneDrive, and — only if asked —
 * ordinary tab visits.
 *
 * Behaviour that keeps it well-mannered:
 *  - excluded domains are dropped before anything is sent or even read;
 *  - repeats are de-duplicated, so revisiting a page does not spam events;
 *  - failed posts are queued and retried instead of dropped;
 *  - the toolbar badge always shows whether Cerebro is reachable.
 */

import { DEFAULTS, getConfig, isExcluded, isInternalUrl } from './config.js';
import { detect, detectDocument } from './detectors.js';

const QUEUE_KEY = 'pendingEvents';
const MAX_QUEUE = 50;
//: How long before the same page is worth reporting again.
const REPEAT_WINDOW_MS = 5 * 60 * 1000;

let lastCaseKey = null;
let online = false;
const recentlySent = new Map();   // key -> timestamp

// ------------------------------------------------------------------ badge
const BADGES = {
  online: { text: '', colour: '#0f9d58', title: 'Cerebro — connected' },
  offline: { text: '!', colour: '#d93025', title: 'Cerebro — API not reachable' },
  disabled: { text: '‖', colour: '#8b93a1', title: 'Cerebro — paused' },
  sent: { text: '✓', colour: '#4f46e5', title: 'Cerebro — reported' },
};

function setBadge(state) {
  const badge = BADGES[state] || BADGES.offline;
  chrome.action.setBadgeText({ text: badge.text });
  chrome.action.setBadgeBackgroundColor({ color: badge.colour });
  chrome.action.setTitle({ title: badge.title });
}

/** True the first time a key is seen, or once the repeat window has passed. */
function shouldSend(key) {
  const now = Date.now();
  const previous = recentlySent.get(key);
  if (previous && now - previous < REPEAT_WINDOW_MS) return false;
  recentlySent.set(key, now);
  if (recentlySent.size > 200) {
    for (const [k, t] of recentlySent) {
      if (now - t > REPEAT_WINDOW_MS) recentlySent.delete(k);
    }
  }
  return true;
}

// -------------------------------------------------------------- transport
async function post(path, body) {
  const { apiUrl } = await getConfig();
  const response = await fetch(`${apiUrl}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return response.json().catch(() => ({}));
}

async function queueEvent(event) {
  const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
  queue.push({ ...event, queuedAt: Date.now() });
  await chrome.storage.local.set({ [QUEUE_KEY]: queue.slice(-MAX_QUEUE) });
}

async function flushQueue() {
  const { [QUEUE_KEY]: queue = [] } = await chrome.storage.local.get(QUEUE_KEY);
  if (!queue.length) return;

  const remaining = [];
  for (const event of queue) {
    try {
      const { queuedAt, ...payload } = event;
      await post('/api/events/', payload);
    } catch {
      remaining.push(event);
    }
  }
  await chrome.storage.local.set({ [QUEUE_KEY]: remaining });
}

async function sendEvent(event) {
  try {
    await post('/api/events/', event);
    online = true;
    setBadge('sent');
    setTimeout(() => setBadge(online ? 'online' : 'offline'), 1600);
    await flushQueue();
    return true;
  } catch (error) {
    online = false;
    setBadge('offline');
    await queueEvent(event);
    console.warn('Cerebro: queued event —', error.message);
    return false;
  }
}

/**
 * Tell Cerebro about a document open in a tab.
 *
 * Cerebro resolves the SharePoint URL to the locally synced file. A 404 here is
 * normal and not worth queueing: it means the library is not synced to this
 * machine, and retrying will not change that.
 */
async function sendDocument(document) {
  try {
    const result = await post('/api/documents/observe', {
      web_url: document.web_url,
      discovered_by: 'browser',
    });
    online = true;
    setBadge('sent');
    setTimeout(() => setBadge('online'), 1600);
    return result;
  } catch (error) {
    console.info('Cerebro: document not resolved locally —', error.message);
    return null;
  }
}

// ------------------------------------------------------------- detection
async function handleTab(tab) {
  if (!tab || !tab.url || isInternalUrl(tab.url)) return;

  const config = await getConfig();
  if (!config.enabled) {
    setBadge('disabled');
    return;
  }
  if (isExcluded(tab.url, config.excludedDomains)) return;

  const title = tab.title || '';

  // 1. A CRM case is the most specific thing a page can be.
  const match = detect(tab.url, title);
  if (match && config.reportCases && config.enabledDetectors.includes(match.detector)) {
    const key = `${match.detector}:${match.case_id}`;
    if (key === lastCaseKey) return;
    lastCaseKey = key;

    await sendEvent({
      event_type: 'CRM_CASE_OPENED',
      source: 'browser_extension',
      case_id: match.case_id,
      data: {
        system: match.system, case_id: match.case_id, customer: match.customer,
        url: match.url, title: match.title,
      },
    });
    return;
  }

  // 2. A document open in a tab — SharePoint, OneDrive, Office online.
  if (config.reportDocuments) {
    const document = detectDocument(tab.url, title);
    if (document && shouldSend(`doc:${document.web_url}`)) {
      await sendDocument(document);
      await sendEvent({
        event_type: 'DOCUMENT_OPENED',
        source: 'browser_extension',
        data: {
          filename: document.filename, url: document.web_url,
          origin: document.source, title,
        },
      });
      return;
    }
  }

  // 3. Ordinary browsing, only when the user has asked for it.
  if (config.reportTabs && shouldSend(`tab:${tab.url.split('#')[0]}`)) {
    await sendEvent({
      event_type: 'APPLICATION_CHANGED',
      source: 'browser_extension',
      data: { application: 'Browser', url: tab.url, title },
    });
  }
}

// --------------------------------------------------------------- wiring
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.status === 'complete' || changeInfo.title) handleTab(tab);
});

chrome.tabs.onActivated.addListener(async ({ tabId }) => {
  try { handleTab(await chrome.tabs.get(tabId)); } catch { /* tab closed */ }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // The content script reports SPA navigations that fire no tab update.
  if (message.type === 'URL_CHANGED' && sender.tab) {
    handleTab({ ...sender.tab, url: message.url, title: message.title });
  }

  // Readable page text, captured only where the user turned it on.
  if (message.type === 'PAGE_TEXT' && sender.tab) {
    handlePageText(message, sender.tab);
  }

  if (message.type === 'PING') {
    checkConnection().then(sendResponse);
    return true;   // keep the channel open for the async reply
  }
  return false;
});

async function handlePageText(message, tab) {
  const config = await getConfig();
  if (!config.enabled || !config.capturePageText) return;
  if (isExcluded(tab.url, config.excludedDomains)) return;
  if (!message.text || message.text.length < 200) return;
  if (!shouldSend(`text:${tab.url.split('#')[0]}`)) return;

  await sendEvent({
    event_type: 'PAGE_CAPTURED',
    source: 'browser_extension',
    data: {
      url: tab.url, title: tab.title || '',
      text: message.text.slice(0, 20000),
      characters: message.text.length,
    },
  });
}

async function checkConnection() {
  const { apiUrl, enabled } = await getConfig();
  if (!enabled) {
    setBadge('disabled');
    return { online: false, enabled: false };
  }
  try {
    const response = await fetch(`${apiUrl}/api/system/info`, { cache: 'no-store' });
    if (!response.ok) throw new Error(String(response.status));
    online = true;
    setBadge('online');
    await flushQueue();
    return { online: true, enabled: true, info: await response.json() };
  } catch (error) {
    online = false;
    setBadge('offline');
    return { online: false, enabled: true, error: error.message };
  }
}

// A periodic ping keeps the badge honest and drains the retry queue.
chrome.alarms.create('cerebro-heartbeat', { periodInMinutes: 1 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'cerebro-heartbeat') checkConnection();
});

chrome.runtime.onInstalled.addListener(() => {
  checkConnection();
  chrome.runtime.openOptionsPage();
});
chrome.runtime.onStartup.addListener(checkConnection);
checkConnection();

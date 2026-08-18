/**
 * Cerebro background service worker.
 *
 * Watches tab navigation for CRM case pages and reports them to the local
 * Cerebro API. Three things make it well-behaved:
 *
 *  - it de-duplicates, so re-visiting the same case does not spam events;
 *  - failed posts are queued and retried instead of being dropped;
 *  - the toolbar badge always reflects whether Cerebro is reachable.
 */

import { getConfig } from './config.js';
import { detect } from './detectors.js';

const QUEUE_KEY = 'pendingEvents';
const MAX_QUEUE = 50;

let lastCaseKey = null;
let online = false;

// ------------------------------------------------------------------ badge
const BADGES = {
  online: { text: '', colour: '#0f9d58', title: 'Cerebro — connected' },
  offline: { text: '!', colour: '#d93025', title: 'Cerebro — API not reachable' },
  disabled: { text: '‖', colour: '#8b93a1', title: 'Cerebro — paused' },
  sent: { text: '✓', colour: '#4f46e5', title: 'Cerebro — case reported' },
};

function setBadge(state) {
  const badge = BADGES[state] || BADGES.offline;
  chrome.action.setBadgeText({ text: badge.text });
  chrome.action.setBadgeBackgroundColor({ color: badge.colour });
  chrome.action.setTitle({ title: badge.title });
}

// -------------------------------------------------------------- transport
async function postEvent(event) {
  const { apiUrl } = await getConfig();
  const response = await fetch(`${apiUrl}/api/events/`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(event),
  });
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return response.json();
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
      await postEvent(payload);
    } catch {
      remaining.push(event);
    }
  }
  await chrome.storage.local.set({ [QUEUE_KEY]: remaining });
}

async function send(event) {
  try {
    await postEvent(event);
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

// ------------------------------------------------------------- detection
async function handleTab(tab) {
  if (!tab || !tab.url || !/^https?:/.test(tab.url)) return;

  const config = await getConfig();
  if (!config.enabled) {
    setBadge('disabled');
    return;
  }

  const match = detect(tab.url, tab.title || '');
  if (match && config.reportCases && config.enabledDetectors.includes(match.detector)) {
    const key = `${match.detector}:${match.case_id}`;
    if (key === lastCaseKey) return;   // already reported this case
    lastCaseKey = key;

    await send({
      event_type: 'CRM_CASE_OPENED',
      source: 'browser_extension',
      case_id: match.case_id,
      data: {
        system: match.system,
        case_id: match.case_id,
        customer: match.customer,
        url: match.url,
        title: match.title,
      },
    });
    return;
  }

  if (!match && config.reportNavigation) {
    await send({
      event_type: 'APPLICATION_CHANGED',
      source: 'browser_extension',
      data: { application: 'Browser', url: tab.url, title: tab.title || '' },
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

// The content script reports single-page-app navigations that fire no tab update.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === 'URL_CHANGED' && sender.tab) {
    handleTab({ ...sender.tab, url: message.url, title: message.title });
  }
  if (message.type === 'PING') {
    checkConnection().then(sendResponse);
    return true;   // keep the channel open for the async reply
  }
  return false;
});

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

/** Extension settings, stored in chrome.storage.sync. */

export const DEFAULTS = {
  apiUrl: 'http://127.0.0.1:8000',
  enabled: true,
  reportCases: true,
  reportDocuments: true,    // SharePoint/OneDrive files you open
  reportTabs: false,        // every page you visit — off, it is noisy
  capturePageText: false,   // send readable page text with CRM/document pages
  enabledDetectors: ['salesforce', 'servicenow', 'zendesk'],
  excludedDomains: [],      // never reported, whatever the settings above say
};

export async function getConfig() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

export async function setConfig(changes) {
  await chrome.storage.sync.set(changes);
}

/**
 * True when a URL must never leave the browser.
 *
 * Checked before anything is sent — an excluded domain is not reported as a
 * case, a document, or a tab visit, and its text is never captured.
 */
export function isExcluded(url, excludedDomains = []) {
  let host;
  try {
    host = new URL(url).hostname.toLowerCase();
  } catch {
    return true;   // unparseable: refuse rather than guess
  }
  return excludedDomains
    .map((d) => String(d).trim().toLowerCase())
    .filter(Boolean)
    .some((domain) => host === domain || host.endsWith(`.${domain}`));
}

/**
 * True for anything that is not an ordinary web page.
 *
 * Allow-list rather than deny-list: an exclusion list has to enumerate
 * `chrome:`, `about:`, `file:`, `data:`, `ftp:`, `blob:` and whatever comes
 * next, and the one it forgets gets reported. Cerebro only cares about http(s),
 * so anything else is out.
 */
export function isInternalUrl(url) {
  try {
    return !/^https?:$/.test(new URL(String(url || '')).protocol);
  } catch {
    return true;   // unparseable: refuse rather than guess
  }
}

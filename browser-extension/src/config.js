/** Extension settings, stored in chrome.storage.sync. */

export const DEFAULTS = {
  apiUrl: 'http://127.0.0.1:8000',
  enabled: true,
  reportCases: true,
  reportNavigation: false,   // noisy; off unless the user asks for it
  enabledDetectors: ['salesforce', 'servicenow', 'zendesk'],
};

export async function getConfig() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

export async function setConfig(changes) {
  await chrome.storage.sync.set(changes);
}

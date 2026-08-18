/**
 * Browser Extension for Cerebrus
 * Detects CRM pages and extracts context information
 */

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    const url = details.url;
    const tabId = details.tabId;

    // Detect Salesforce
    if (url.includes('salesforce.com') || url.includes('lightning.force.com')) {
      chrome.tabs.get(tabId, (tab) => {
        const caseMatch = url.match(/\/lightning\/r\/Case\/([a-zA-Z0-9]+)/);
        if (caseMatch) {
          const caseId = caseMatch[1];
          
          // Send event to background script
          chrome.runtime.sendMessage({
            type: 'CRM_CASE_DETECTED',
            system: 'Salesforce',
            case_id: caseId,
            url: url,
            title: tab.title
          });
        }
      });
    }
  },
  { urls: ['*://*.salesforce.com/*', '*://*.lightning.force.com/*'] }
);

// Listen for messages from content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === 'URL_CHANGED') {
    // Send to backend API
    fetch('http://localhost:8000/api/events/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        event_type: 'APPLICATION_CHANGED',
        source: 'browser_extension',
        data: {
          url: request.url,
          title: request.title,
          application: 'Browser'
        }
      })
    });
  }
});

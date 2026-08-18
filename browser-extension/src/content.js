/**
 * Content script: reports single-page-app navigation.
 *
 * Salesforce Lightning, ServiceNow and Zendesk all change the case in view
 * without a page load, so chrome.tabs.onUpdated never fires. Watching history
 * and the title element covers both patterns.
 */

(() => {
  let lastUrl = location.href;

  const report = () => {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    try {
      chrome.runtime.sendMessage({
        type: 'URL_CHANGED',
        url: location.href,
        title: document.title,
      });
    } catch {
      // Extension reloaded or disabled — nothing to do.
    }
  };

  for (const method of ['pushState', 'replaceState']) {
    const original = history[method];
    history[method] = function patched(...args) {
      const result = original.apply(this, args);
      setTimeout(report, 150);
      return result;
    };
  }

  window.addEventListener('popstate', () => setTimeout(report, 150));

  // The title often settles after the URL changes; watch it as a second signal.
  const titleNode = document.querySelector('title');
  if (titleNode) {
    new MutationObserver(() => setTimeout(report, 100))
      .observe(titleNode, { childList: true });
  }
})();

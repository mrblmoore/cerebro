/**
 * Content script: reports SPA navigation and, when asked, readable page text.
 *
 * Salesforce Lightning, ServiceNow, Zendesk and SharePoint all change what is on
 * screen without a page load, so chrome.tabs.onUpdated never fires. Watching
 * history and the title element covers both patterns.
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
      return;   // extension reloaded or disabled
    }
    scheduleTextCapture();
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

  // ------------------------------------------------------------ page text
  /**
   * Extract what a person would actually read.
   *
   * Navigation, scripts and chrome are stripped, and a main/article container is
   * preferred when the page offers one. The background worker decides whether
   * this is allowed to leave the browser — capture is off unless enabled, and
   * excluded domains are dropped there.
   */
  function readablePageText() {
    const container =
      document.querySelector('main, article, [role="main"]') || document.body;
    if (!container) return '';

    const clone = container.cloneNode(true);
    clone.querySelectorAll(
      'script, style, noscript, svg, nav, header, footer, aside, [aria-hidden="true"]'
    ).forEach((node) => node.remove());

    return (clone.innerText || clone.textContent || '')
      .replace(/[ \t]+/g, ' ')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  let captureTimer = null;
  function scheduleTextCapture() {
    clearTimeout(captureTimer);
    // Let the page finish rendering; a Lightning record page fills in late.
    captureTimer = setTimeout(() => {
      const text = readablePageText();
      if (text.length < 200) return;
      try {
        chrome.runtime.sendMessage({ type: 'PAGE_TEXT', text, url: location.href });
      } catch { /* extension gone */ }
    }, 1800);
  }

  if (document.readyState === 'complete') scheduleTextCapture();
  else window.addEventListener('load', scheduleTextCapture, { once: true });
})();

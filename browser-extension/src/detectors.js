/**
 * CRM page detectors.
 *
 * Each detector turns a URL + page title into a Cerebro event. Adding support
 * for another CRM means adding one entry here — nothing else changes.
 */

/** Pull the customer out of a pipe-separated CRM page title. */
function customerFromTitle(title) {
  const parts = String(title || '').split('|').map((part) => part.trim()).filter(Boolean);
  return parts.length >= 3 ? parts[parts.length - 2] : null;
}

export const DETECTORS = [
  {
    id: 'salesforce',
    label: 'Salesforce',
    match: (url) => /salesforce\.com|\.force\.com/.test(url),
    // Classic (/Case/500...) and Lightning (/lightning/r/Case/500.../view)
    caseId: (url) => {
      const lightning = url.match(/\/lightning\/r\/Case\/([a-zA-Z0-9]{15,18})/);
      if (lightning) return lightning[1];
      const classic = url.match(/\/(500[a-zA-Z0-9]{12,15})(?:[/?#]|$)/);
      return classic ? classic[1] : null;
    },
    // Lightning titles look like "Case 00001234 | Contoso Ltd | Salesforce".
    // Two parts means "record | product" with no customer in it — guessing there
    // would file the case number as the customer name.
    customer: customerFromTitle,
  },
  {
    id: 'servicenow',
    label: 'ServiceNow',
    match: (url) => /\.service-now\.com/.test(url),
    caseId: (url) => {
      // ServiceNow nests the real query inside nav_to.do, so the separators may
      // be percent-encoded (`%3F`, `%3D`). Match the record number either way.
      const query = url.match(/number(?:%3D|=)([A-Z]{2,5}\d{5,})/i);
      if (query) return query[1].toUpperCase();
      const path = url.match(/\/((?:INC|CS|RITM|SCTASK|CHG)\d{5,})/i);
      return path ? path[1].toUpperCase() : null;
    },
    customer: customerFromTitle,
  },
  {
    id: 'zendesk',
    label: 'Zendesk',
    match: (url) => /\.zendesk\.com/.test(url),
    caseId: (url) => {
      const ticket = url.match(/\/agent\/tickets\/(\d+)/);
      return ticket ? ticket[1] : null;
    },
    customer: customerFromTitle,
  },
];

/** Identify the CRM case on a page, or null when it is not a case page. */
export function detect(url, title = '') {
  for (const detector of DETECTORS) {
    if (!detector.match(url)) continue;
    const caseId = detector.caseId(url);
    if (!caseId) return null;
    return {
      system: detector.label,
      detector: detector.id,
      case_id: caseId,
      customer: detector.customer(title) || null,
      url,
      title,
    };
  }
  return null;
}

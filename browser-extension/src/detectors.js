/**
 * Page detectors — what Cerebro recognises in a browser tab.
 *
 * Two families:
 *  - CRM detectors turn a case page into a CRM_CASE_OPENED event;
 *  - document detectors turn a SharePoint/OneDrive/Office-online link into a
 *    document Cerebro can open from the locally synced copy.
 *
 * Adding another system means adding one entry — nothing else changes.
 */

/**
 * decodeURIComponent throws on a stray `%`, and real URLs contain those — a
 * literal percent in a filename, a truncated escape. An exception here would
 * abort tab handling entirely, so a page that cannot be decoded is simply
 * treated as its raw text.
 */
function safeDecode(value) {
  const text = String(value ?? '');
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

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

// ------------------------------------------------------------------ documents
const DOCUMENT_EXTENSIONS =
  /\.(docx?|xlsx?|pptx?|pdf|csv|txt|md)(?:$|[?#])/i;

const SHAREPOINT_HOST = /(^|\.)sharepoint\.com$|(^|\.)onedrive\.live\.com$/i;
const OFFICE_ONLINE_HOST = /(^|\.)officeapps\.live\.com$|(^|\.)office\.com$/i;

/**
 * Recognise a document open in a browser tab.
 *
 * SharePoint serves documents through several shapes: a direct path, a viewer
 * with `?file=`, and Doc.aspx with `sourcedoc={guid}`. The filename is what
 * matters — Cerebro finds the real file in the OneDrive-synced folder, so it can
 * read and edit it without any Microsoft API access.
 */
export function detectDocument(url, title = '') {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }

  const host = parsed.hostname || '';
  const isSharePoint = SHAREPOINT_HOST.test(host);
  const isOfficeOnline = OFFICE_ONLINE_HOST.test(host);

  const candidates = [
    parsed.searchParams.get('file'),
    parsed.searchParams.get('filename'),
    parsed.searchParams.get('id'),
    safeDecode(parsed.pathname || ''),
  ].filter(Boolean);

  let filename = null;
  for (const candidate of candidates) {
    // searchParams values are already decoded; decoding again is harmless for
    // ordinary names and safeDecode absorbs the malformed ones.
    const last = safeDecode(candidate).split('/').pop().trim();
    if (DOCUMENT_EXTENSIONS.test(last)) { filename = last; break; }
  }

  // Doc.aspx?sourcedoc={guid} carries no filename — the tab title is the only
  // place the document name appears, so fall back to it.
  if (!filename && (isSharePoint || isOfficeOnline)) {
    const fromTitle = String(title || '').split(/[|\u2013\u2014-]/)[0].trim();
    if (fromTitle && DOCUMENT_EXTENSIONS.test(fromTitle)) filename = fromTitle;
  }

  if (!filename) return null;
  if (!isSharePoint && !isOfficeOnline && !DOCUMENT_EXTENSIONS.test(parsed.pathname)) {
    return null;
  }

  return {
    filename,
    web_url: url,
    source: isSharePoint ? 'sharepoint' : (isOfficeOnline ? 'office' : 'web'),
    title,
  };
}

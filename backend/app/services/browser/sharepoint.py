"""
SharePoint through the hidden browser.

People paste SharePoint links — a document in a library, a short sharing
link, a ``Doc.aspx?sourcedoc=…`` link from Office Online, or a site page —
and Ask should be able to read what is there and, with approval, change it.

Like the Dynamics connector, this one does not click through pages. It calls
SharePoint's own REST API (``/_api/…``) **from inside the signed-in browser
page**, so the requests carry the user's session exactly as SharePoint's own
web parts do. There is no app registration or stored credential, and it can
only do what the user's own permissions allow.

* Documents are downloaded, read with Cerebro's document readers, and — for
  Word and Excel — edited with its document editors and uploaded back.
* Modern site pages are read from their canvas, and edited by text
  replacement, then checked out, saved and republished.
"""

import base64
import hashlib
import html as html_lib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, unquote, urlparse

from app.core.config import settings
from app.core.paths import DATA_DIR
from app.services.browser import register
from app.services.browser.connector import BrowserConnector, SignInRequired

CACHE_DIR = DATA_DIR / "sharepoint_browser"
#: What a change replaced, kept so it can be undone: the file as it was, or
#: the page's content. Kept for UNDO_DAYS.
UNDO_DIR = DATA_DIR / "sharepoint_undo"
UNDO_DAYS = 30
JSON_HEADERS = {"Accept": "application/json;odata=nometadata"}
GUID_RE = re.compile(r"\{?([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\}?")
SITE_RE = re.compile(r"^(/(?:sites|teams|personal)/[^/]+)", re.IGNORECASE)
LINK_RE = re.compile(r"https?://[A-Za-z0-9.-]+\.sharepoint\.com/[^\s<>\"')\]]+", re.IGNORECASE)
DIGEST_SECONDS = 20 * 60

#: Binary-safe request made by the page; bodies and responses travel as base64.
_FETCH_BYTES_JS = """
async ({url, method, headers, body64}) => {
  const init = {method, credentials: 'include', headers: headers || {}};
  if (body64) {
    const raw = atob(body64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    init.body = bytes;
  }
  const response = await fetch(url, init);
  const buffer = new Uint8Array(await response.arrayBuffer());
  let binary = '';
  for (let i = 0; i < buffer.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, buffer.subarray(i, i + 0x8000));
  }
  return {status: response.status, ok: response.ok, url: response.url,
          type: response.headers.get('content-type') || '', body64: btoa(binary)};
}
"""


class SharePointError(RuntimeError):
    pass


def find_links(text: str) -> List[str]:
    """SharePoint links in a message, in order, without duplicates."""
    seen, links = set(), []
    for match in LINK_RE.finditer(text or ""):
        link = match.group(0).rstrip(".,;:")
        if link not in seen:
            seen.add(link)
            links.append(link)
    return links


def _text_hash(markup: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", _html_to_text(markup)).strip()
                          .encode("utf-8")).hexdigest()


def _html_to_text(markup: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", markup or "", flags=re.S | re.I)
    text = re.sub(r"<(br|/p|/div|/h\d|/li|/tr)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def replace_in_html_text(markup: str, find: str, replace: str) -> Tuple[str, int]:
    """Replace text inside an HTML fragment without touching tags or attributes."""
    if not find:
        return markup or "", 0
    escaped_find = html_lib.escape(find, quote=False)
    escaped_replace = html_lib.escape(replace, quote=False)
    count = 0
    parts = re.split(r"(<[^>]*>)", markup or "")
    for index, part in enumerate(parts):
        if part.startswith("<"):
            continue
        hits = part.count(escaped_find)
        if hits:
            parts[index] = part.replace(escaped_find, escaped_replace)
            count += hits
    return "".join(parts), count


class SharePointConnector(BrowserConnector):
    name = "sharepoint"
    label = "SharePoint"
    enabled_setting = "SHAREPOINT_BROWSER_ENABLED"
    url_setting = "SHAREPOINT_SITE_URL"
    auto_apply_setting = "SHAREPOINT_AUTO_APPLY"
    default_selectors: Dict[str, Any] = {}

    def __init__(self):
        super().__init__()
        self._digests: Dict[str, Tuple[str, float]] = {}

    # ------------------------------------------------------------ hosts
    @property
    def tenant_hosts(self) -> Tuple[str, ...]:
        """The tenant's SharePoint host and its OneDrive ("-my") host."""
        host = (urlparse(self.base_url).hostname or "").lower()
        if not host:
            return ()
        prefix = host.split(".", 1)[0]
        return host, host.replace(prefix, f"{prefix}-my", 1)

    def check_link(self, link: str) -> str:
        link = (link or "").strip().strip("<>")
        host = (urlparse(link).hostname or "").lower()
        if not host:
            raise SharePointError("That doesn't look like a SharePoint link.")
        if host not in self.tenant_hosts:
            raise SharePointError(
                f"Cerebro only opens links on {self.tenant_hosts[0] if self.tenant_hosts else 'the company SharePoint'}"
                f" (and its OneDrive); this one is on {host}.")
        return link

    def is_signed_in(self, page) -> bool:
        return self.on_own_site(page.url) and not self.looks_like_login(page.url)

    def account_name(self, page) -> Optional[str]:
        user = self._json(page, f"{self.base_url}/_api/web/currentuser?$select=Title,Email")
        return user.get("Title") or user.get("Email")

    # ---------------------------------------------------------- plumbing
    def _ensure_origin(self, page, url: str) -> None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if page.url.lower().startswith(origin.lower()) and not self.looks_like_login(page.url):
            return
        page.goto(f"{origin}/_api/web?$select=Title", wait_until="domcontentloaded")
        if self.looks_like_login(page.url) or not page.url.lower().startswith(origin.lower()):
            raise SignInRequired(self)

    def _call(self, page, url: str, method: str = "GET", headers: Dict[str, str] = None,
              body: bytes = None) -> Dict[str, Any]:
        self._ensure_origin(page, url)
        result = page.evaluate(_FETCH_BYTES_JS, {
            "url": url, "method": method, "headers": headers or {},
            "body64": base64.b64encode(body).decode("ascii") if body is not None else None})
        if result.get("status") in (401, 403) and self.looks_like_login(result.get("url") or ""):
            raise SignInRequired(self)
        data = base64.b64decode(result.get("body64") or "")
        if not result.get("ok"):
            message = data.decode("utf-8", errors="replace")[:600]
            found = re.search(r'"(?:value|message)"\s*:\s*"([^"]+)"', message)
            if result.get("status") == 403:
                raise SharePointError("SharePoint says you don't have permission for that.")
            raise SharePointError(f"SharePoint refused the request: "
                                  f"{found.group(1) if found else message or result.get('status')}")
        return {"status": result.get("status"), "type": result.get("type"), "body": data,
                "url": result.get("url")}

    def _json(self, page, url: str, method: str = "GET", payload: Any = None,
              site: str = None) -> Dict[str, Any]:
        import json

        headers = dict(JSON_HEADERS)
        body = None
        if method != "GET":
            headers["X-RequestDigest"] = self._digest(page, site or self._site_of(url))
            headers["Content-Type"] = "application/json;odata=nometadata"
            body = json.dumps(payload or {}).encode("utf-8")
        result = self._call(page, url, method, headers, body)
        if not result["body"]:
            return {}
        try:
            return json.loads(result["body"].decode("utf-8"))
        except ValueError:
            return {}

    def _digest(self, page, site: str) -> str:
        """The form digest SharePoint requires on every change, per site."""
        cached = self._digests.get(site)
        if cached and time.time() - cached[1] < DIGEST_SECONDS:
            return cached[0]
        import json

        result = self._call(page, f"{site}/_api/contextinfo", "POST", dict(JSON_HEADERS), b"")
        value = json.loads(result["body"].decode("utf-8")).get("FormDigestValue")
        if not value:
            raise SharePointError("SharePoint did not issue a change token (form digest).")
        self._digests[site] = (value, time.time())
        return value

    @staticmethod
    def _site_of(url: str) -> str:
        parsed = urlparse(url)
        match = SITE_RE.match(parsed.path)
        return f"{parsed.scheme}://{parsed.netloc}{match.group(1) if match else ''}"

    @staticmethod
    def _path_arg(server_relative: str) -> str:
        return quote(server_relative.replace("'", "''"), safe="/")

    # -------------------------------------------------------- resolving
    def _resolve(self, page, link: str) -> Dict[str, Any]:
        """Any SharePoint link → what it points at (file or page) and where."""
        link = self.check_link(link)
        parsed = urlparse(link)
        query = parse_qs(parsed.query)
        path = unquote(parsed.path)

        # Short sharing links (/:w:/s/Site/Eab…) only reveal the file by redirecting.
        if re.match(r"^/:[a-z]:/", path, re.IGNORECASE):
            self._ensure_origin(page, link)
            page.goto(link, wait_until="domcontentloaded")
            if self.looks_like_login(page.url):
                raise SignInRequired(self)
            if page.url == link:
                raise SharePointError("That sharing link didn't lead to a document.")
            return self._resolve(page, page.url)

        site = self._site_of(link)
        sourcedoc = (query.get("sourcedoc") or query.get("sourceDoc") or [None])[0]
        if sourcedoc and GUID_RE.search(sourcedoc):
            guid = GUID_RE.search(sourcedoc).group(1)
            info = self._json(page, f"{site}/_api/web/GetFileById('{guid}')"
                                    "?$select=Name,ServerRelativeUrl,Length,TimeLastModified,ETag")
            return self._file_info(site, info)

        if "/_layouts/" in path.lower():
            raise SharePointError("That link opens a SharePoint screen, not a document or page.")

        info = self._json(page, f"{site}/_api/web/GetFileByServerRelativePath(decodedurl='"
                                f"{self._path_arg(path)}')?$select=Name,ServerRelativeUrl,Length,"
                                "TimeLastModified,ETag")
        return self._file_info(site, info)

    def _file_info(self, site: str, info: Dict[str, Any]) -> Dict[str, Any]:
        relative = info.get("ServerRelativeUrl") or ""
        origin = f"{urlparse(site).scheme}://{urlparse(site).netloc}"
        is_page = "/sitepages/" in relative.lower() and relative.lower().endswith(".aspx")
        return {"kind": "page" if is_page else "file", "site": site,
                "path": relative, "name": info.get("Name") or relative.rsplit("/", 1)[-1],
                "url": origin + quote(relative, safe="/"), "size": int(info.get("Length") or 0),
                "modified": info.get("TimeLastModified"), "etag": info.get("ETag")}

    # ------------------------------------------------------------ reads
    def _download(self, page, item: Dict[str, Any]) -> Path:
        limit = int(float(settings.DOCUMENT_MAX_MB or 25) * 1024 * 1024)
        if item.get("size") and item["size"] > limit:
            raise SharePointError(f"{item['name']} is larger than the {settings.DOCUMENT_MAX_MB} MB "
                                  "Cerebro reads.")
        result = self._call(page, f"{item['site']}/_api/web/GetFileByServerRelativePath(decodedurl='"
                                  f"{self._path_arg(item['path'])}')/$value")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(item["path"].encode("utf-8")).hexdigest()[:10]
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", item["name"])[:150]
        target = CACHE_DIR / f"{digest}-{safe}"
        target.write_bytes(result["body"])
        return target

    def _page_item(self, page, item: Dict[str, Any]) -> Dict[str, Any]:
        return self._json(page, f"{item['site']}/_api/web/GetFileByServerRelativePath(decodedurl='"
                                f"{self._path_arg(item['path'])}')/ListItemAllFields"
                                "?$select=Id,Title,CanvasContent1")

    def read(self, link: str) -> Dict[str, Any]:
        """Open a SharePoint link and return its text (and a local copy, for files)."""
        from app.services import document_readers

        def work(page):
            item = self._resolve(page, link)
            if item["kind"] == "page":
                fields = self._page_item(page, item)
                text = _html_to_text(fields.get("CanvasContent1") or "")
                if not text:
                    page.goto(item["url"], wait_until="domcontentloaded")
                    self.settle(page)
                    text = self.readable_text(page)
                return {**item, "title": fields.get("Title") or item["name"], "text": text}
            local = self._download(page, item)
            try:
                content = document_readers.read(local)
            except document_readers.DocumentError as exc:
                raise SharePointError(f"Downloaded {item['name']}, but couldn't read it: {exc}")
            return {**item, "title": item["name"], "text": content["text"],
                    "outline": content.get("outline"), "local_path": str(local),
                    "doc_kind": content.get("kind")}

        return self.run(work, f"Opening {link[:60]}")

    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        def work(page):
            text = query.replace("'", "''")
            data = self._json(page, f"{self.base_url}/_api/search/query?querytext='{quote(text)}'"
                                    f"&rowlimit={max(1, min(limit, 25))}&selectproperties="
                                    "'Title,Path,HitHighlightedSummary,FileType,LastModifiedTime'")
            rows = (((data.get("PrimaryQueryResult") or {}).get("RelevantResults") or {})
                    .get("Table") or {}).get("Rows") or []
            results = []
            for row in rows:
                cells = {cell.get("Key"): cell.get("Value") for cell in row.get("Cells") or []}
                summary = re.sub(r"</?c0>|<ddd/>", "", cells.get("HitHighlightedSummary") or "")
                results.append({"title": cells.get("Title") or cells.get("Path"),
                                "url": cells.get("Path"), "type": cells.get("FileType"),
                                "modified": cells.get("LastModifiedTime"), "snippet": summary})
            return results

        return self.run(work, f"Searching SharePoint for {query[:40]}")

    # ----------------------------------------------------------- writes
    # Proposals (preview) run immediately; the changes themselves run only
    # from approved AgentActions (see app.services.agent). Every change keeps
    # what it replaced, so it can be undone.
    @staticmethod
    def _keep_for_undo(name: str, data: bytes) -> Path:
        UNDO_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - UNDO_DAYS * 86400
        for old in UNDO_DIR.iterdir():
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass
        safe = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)[:120]
        target = UNDO_DIR / f"{int(time.time() * 1000)}-{safe}"
        target.write_bytes(data)
        return target

    @staticmethod
    def _undo_file(path: str) -> Path:
        target = Path(path or "")
        if not path or target.resolve().parent != UNDO_DIR.resolve() or not target.is_file():
            raise SharePointError("The copy needed to undo this is gone (undo is kept for "
                                  f"{UNDO_DAYS} days). Use the file's Version history in "
                                  "SharePoint instead.")
        return target

    def _etag(self, page, item: Dict[str, Any]) -> Optional[str]:
        info = self._json(page, f"{item['site']}/_api/web/GetFileByServerRelativePath(decodedurl='"
                                f"{self._path_arg(item['path'])}')?$select=ETag")
        return info.get("ETag")

    def preview_document_edit(self, link: str, operations: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply the edits to a scratch copy and return the text before and after."""
        import shutil

        from app.services import document_editors, document_readers

        opened = self.read(link)
        if opened["kind"] != "file":
            raise SharePointError("That link is a page; use the page tool to change it.")
        original = Path(opened["local_path"])
        scratch = original.with_name(f"preview-{original.name}")
        shutil.copy2(original, scratch)
        try:
            document_editors.apply(scratch, operations, keep_backup=False)
            after = document_readers.read(scratch)["text"]
        except document_readers.DocumentError as exc:
            raise SharePointError(str(exc))
        finally:
            scratch.unlink(missing_ok=True)
        return {"item": opened, "before": opened["text"], "after": after}

    def update_document(self, link: str, operations: List[Dict[str, Any]],
                        expected_etag: str = None) -> Dict[str, Any]:
        from app.services import document_editors, document_readers

        def work(page):
            item = self._resolve(page, link)
            if expected_etag and item.get("etag") and item["etag"] != expected_etag:
                raise SharePointError(f"{item['name']} changed in SharePoint after the preview. "
                                      "Ask again so the change is based on the current version.")
            local = self._download(page, item)
            original = self._keep_for_undo(item["name"], local.read_bytes())
            try:
                document_editors.apply(local, operations, keep_backup=False)
            except document_readers.DocumentError as exc:
                original.unlink(missing_ok=True)
                raise SharePointError(str(exc))
            self._upload(page, item, local.read_bytes())
            undo = {"link": link, "original": str(original), "etag": self._etag(page, item)}
            return {"detail": f"{item['name']} updated in SharePoint", "url": item["url"],
                    "undo": undo}

        return self.run(work, "Updating a SharePoint document")

    def restore_document(self, undo: Dict[str, Any]) -> Dict[str, Any]:
        """Put a file back as it was before Cerebro changed it."""
        original = self._undo_file(undo.get("original"))

        def work(page):
            item = self._resolve(page, undo["link"])
            if undo.get("etag") and item.get("etag") and item["etag"] != undo["etag"]:
                raise SharePointError(f"{item['name']} has been edited since Cerebro changed it, "
                                      "so undoing would lose that edit. Use the file's Version "
                                      "history in SharePoint instead.")
            self._upload(page, item, original.read_bytes())
            original.unlink(missing_ok=True)
            return {"detail": f"{item['name']} is back as it was", "url": item["url"]}

        return self.run(work, "Undoing a SharePoint document change")

    def _upload(self, page, item: Dict[str, Any], data: bytes) -> None:
        file_api = (f"{item['site']}/_api/web/GetFileByServerRelativePath(decodedurl='"
                    f"{self._path_arg(item['path'])}')")

        def put():
            headers = {**JSON_HEADERS, "X-HTTP-Method": "PUT",
                       "X-RequestDigest": self._digest(page, item["site"])}
            self._call(page, f"{file_api}/$value", "POST", headers, data)

        try:
            put()
        except SharePointError as exc:
            if "check" not in str(exc).lower():
                raise
            # Libraries that require check-out: check out, write, check in.
            self._json(page, f"{file_api}/CheckOut()", "POST", site=item["site"])
            put()
            self._json(page, f"{file_api}/CheckIn(comment='Updated by Cerebro',checkintype=0)",
                       "POST", site=item["site"])

    def preview_page_edit(self, link: str, replacements: List[Dict[str, str]]) -> Dict[str, Any]:
        def work(page):
            item = self._resolve(page, link)
            if item["kind"] != "page":
                raise SharePointError("That link is a document; use the document tool to change it.")
            fields = self._page_item(page, item)
            canvas = fields.get("CanvasContent1") or ""
            updated, total = canvas, 0
            for change in replacements:
                updated, count = replace_in_html_text(updated, change.get("find", ""),
                                                      change.get("replace", ""))
                if not count:
                    raise SharePointError(f"“{change.get('find')}” isn't on that page.")
                total += count
            return {"item": {**item, "title": fields.get("Title") or item["name"],
                             "page_id": fields.get("Id")},
                    "before": _html_to_text(canvas), "after": _html_to_text(updated),
                    "changes": total}

        return self.run(work, "Preparing a SharePoint page change")

    def update_page(self, link: str, replacements: List[Dict[str, str]]) -> Dict[str, Any]:
        def work(page):
            item = self._resolve(page, link)
            fields = self._page_item(page, item)
            canvas = fields.get("CanvasContent1") or ""
            for change in replacements:
                canvas, count = replace_in_html_text(canvas, change.get("find", ""),
                                                     change.get("replace", ""))
                if not count:
                    raise SharePointError(f"“{change.get('find')}” is no longer on that page.")
            original = self._keep_for_undo(
                f"{item['name']}.json",
                json.dumps({"canvas": fields.get("CanvasContent1") or ""}).encode("utf-8"))
            self._publish_page(page, item, fields.get("Id"), canvas)
            undo = {"link": link, "original": str(original),
                    "text": _text_hash(canvas)}
            return {"detail": f"Page “{fields.get('Title') or item['name']}” updated and published",
                    "url": item["url"], "undo": undo}

        return self.run(work, "Updating a SharePoint page")

    def _publish_page(self, page, item: Dict[str, Any], page_id, canvas: str) -> None:
        pages_api = f"{item['site']}/_api/sitepages/pages({page_id})"
        self._json(page, f"{pages_api}/checkoutpage", "POST", site=item["site"])
        self._json(page, f"{pages_api}/savepage", "POST", {"CanvasContent1": canvas},
                   site=item["site"])
        self._json(page, f"{pages_api}/publish", "POST", site=item["site"])

    def restore_page(self, undo: Dict[str, Any]) -> Dict[str, Any]:
        """Republish a page with the content it had before Cerebro changed it."""
        saved = json.loads(self._undo_file(undo.get("original")).read_text(encoding="utf-8"))

        def work(page):
            item = self._resolve(page, undo["link"])
            fields = self._page_item(page, item)
            title = fields.get("Title") or item["name"]
            # Compared as text: SharePoint may tidy the markup when it saves.
            if undo.get("text") and _text_hash(fields.get("CanvasContent1") or "") != undo["text"]:
                raise SharePointError(f"“{title}” has been edited since Cerebro changed it, so "
                                      "undoing would lose that edit. Use the page's Version "
                                      "history in SharePoint instead.")
            self._publish_page(page, item, fields.get("Id"), saved["canvas"])
            self._undo_file(undo["original"]).unlink(missing_ok=True)
            return {"detail": f"Page “{title}” is back as it was", "url": item["url"]}

        return self.run(work, "Undoing a SharePoint page change")


connector: Optional[SharePointConnector] = register(SharePointConnector())

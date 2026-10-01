"""
RightAnswers (Upland) knowledge base through the hidden browser.

RightAnswers portals are configured per company, so this connector is driven
by selectors rather than hard-coded page structure. The defaults below match
the common portal layout; anything different on a given tenant is corrected
in ``DATA_DIR/connectors/rightanswers.json`` — the same keys, only the ones
that differ — with no code change. (Record them once with
``python -m playwright codegen <your RightAnswers address>``.)

URL templates may use ``{base}``, ``{query}`` (URL-encoded) and ``{id}``.
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from app.services.browser import register
from app.services.browser.connector import BrowserConnector

DEFAULT_SELECTORS: Dict[str, Any] = {
    # Search
    "search_url": "{base}/portal/ss/?searchText={query}",
    "search_input": "input[type='search'], input[name='searchText'], #searchText",
    "result_item": ("[data-solution-id], .search-result, .result-item, .solution-result, "
                    "li.result"),
    "result_link": "a[href*='solution'], a",
    "result_title": "[data-role='title'], .title, h3, h4, a",
    "result_snippet": "[data-role='snippet'], .snippet, .summary, .description, p",
    # Articles
    "article_url": "{base}/portal/app/portlets/results/viewsolution.jsp?solutionid={id}",
    "article_id_pattern": r"(?:solutionid|solutionId|id)=([A-Za-z0-9_.-]+)",
    "article_title": "[data-role='article-title'], h1, .solution-title, .title",
    "article_body": "[data-role='article-body'], .solution-body, .solution-content, article, #content",
    "article_meta": "[data-role='article-meta'], .solution-meta, .metadata",
    # Editing (author permissions required in RightAnswers)
    "edit_url": "",
    "edit_button": ("[data-role='edit'], button:has-text('Edit'), a:has-text('Edit'), "
                    "input[value='Edit']"),
    "editor_title": "[data-role='editor-title'], input[name='title'], #title",
    "editor_body": ("[data-role='editor-body'], iframe.cke_wysiwyg_frame, "
                    "[contenteditable='true'], textarea[name='body'], textarea"),
    "save_button": ("[data-role='save'], button:has-text('Save'), input[value='Save'], "
                    "button:has-text('Publish'), button:has-text('Submit')"),
    "create_url": "",
    "saved_marker": "",
    # Marker that only appears when signed in (optional).
    "signed_in": "",
}


class RightAnswersError(RuntimeError):
    pass


#: Links that look like knowledge articles, for portals whose result markup
#: the selectors don't match (yet). Returns [{href, title}] in page order.
_ARTICLE_LINKS_JS = r"""
(pattern) => {
  const re = new RegExp(pattern, 'i');
  const seen = new Set();
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    const title = (a.innerText || a.title || '').trim();
    if (!re.test(href) || seen.has(href) || title.length < 4) continue;
    seen.add(href);
    const box = a.closest('li, tr, article, [class*="result"], [class*="item"]') || a.parentElement;
    const text = (box && box.innerText || '').replace(title, '').trim();
    out.push({href, title: title.slice(0, 200), snippet: text.slice(0, 300)});
  }
  return out;
}
"""
ARTICLE_LINK_PATTERN = r"solution|article|kb[-_/]?\d|[?&](?:id|docid|contentid)="


class RightAnswersConnector(BrowserConnector):
    name = "rightanswers"
    label = "RightAnswers"
    enabled_setting = "RIGHTANSWERS_ENABLED"
    url_setting = "RIGHTANSWERS_URL"
    default_selectors = DEFAULT_SELECTORS

    def __init__(self):
        super().__init__()
        from app.services.browser.teach import Teacher

        #: Learns this company's portal layout by watching one search.
        self.teacher = Teacher(self)

    # ------------------------------------------------------------ helpers
    def _template(self, key: str, **values) -> str:
        template = self.selectors().get(key) or ""
        if not template:
            return ""
        return template.format(base=self.base_url, **values)

    def article_id_from(self, url: str) -> Optional[str]:
        match = re.search(self.selectors()["article_id_pattern"], url or "")
        return match.group(1) if match else None

    @staticmethod
    def _first_text(scope, selector: str) -> str:
        if not selector:
            return ""
        try:
            locator = scope.locator(selector).first
            if locator.count() == 0:
                return ""
            return (locator.inner_text(timeout=2000) or "").strip()
        except Exception:  # noqa: BLE001 - optional field
            return ""

    # --------------------------------------------------------------- reads
    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        selectors = self.selectors()

        def work(page):
            url = self._template("search_url", query=quote_plus(query))
            if url:
                self.goto(page, url)
            else:
                self.goto(page)
                page.fill(selectors["search_input"], query)
                page.keyboard.press("Enter")
                self.settle(page)
            results = []
            items = page.locator(selectors["result_item"])
            for index in range(min(items.count(), limit * 2)):
                item = items.nth(index)
                link = item.locator(selectors["result_link"]).first
                href = ""
                try:
                    href = link.get_attribute("href", timeout=1000) or ""
                except Exception:  # noqa: BLE001
                    pass
                article_id = (item.get_attribute("data-solution-id")
                              or self.article_id_from(href))
                title = self._first_text(item, selectors["result_title"])
                if not title and not article_id:
                    continue
                results.append({
                    "id": article_id, "title": title or f"Article {article_id}",
                    "snippet": self._first_text(item, selectors["result_snippet"])[:400],
                    "url": page.url if not href else self.url(href) if not href.startswith(
                        "http") else href,
                })
                if len(results) >= limit:
                    break
            if not results:
                # The result markup isn't what the selectors expect: fall back
                # to any link that looks like an article.
                for link in page.evaluate(_ARTICLE_LINKS_JS, ARTICLE_LINK_PATTERN)[:limit]:
                    results.append({"id": self.article_id_from(link["href"]),
                                    "title": link["title"], "snippet": link["snippet"],
                                    "url": link["href"]})
            return results

        return self.run(work, f"Searching {self.label}")

    def _open_article(self, page, article: str) -> str:
        """Navigate to an article given its ID or URL; returns the article ID."""
        if article.startswith(("http://", "https://")):
            self.goto(page, article)
            return self.article_id_from(article) or article
        url = self._template("article_url", id=quote_plus(article))
        if not url:
            raise RightAnswersError("No article address pattern is configured.")
        self.goto(page, url)
        return article

    def get_article(self, article: str) -> Dict[str, Any]:
        selectors = self.selectors()

        def work(page):
            article_id = self._open_article(page, article)
            title = self._first_text(page, selectors["article_title"])
            body_area = page.locator(selectors["article_body"]).first
            body = self.readable_text_of(body_area) if body_area.count() else ""
            if not body:
                body = self.readable_text(page)
            if not title and not body:
                raise RightAnswersError(f"Article {article} could not be read.")
            return {"id": article_id, "title": title or f"Article {article_id}",
                    "body": body, "meta": self._first_text(page, selectors["article_meta"]),
                    "url": page.url}

        return self.run(work, f"Reading {self.label} article {article}")

    # -------------------------------------------------------------- writes
    # Called only by approved AgentActions (see app.services.agent).
    def _fill_body(self, page, text: str) -> None:
        selector = self.selectors()["editor_body"]
        target = page.locator(selector).first
        tag = (target.evaluate("el => el.tagName") or "").lower()
        html = "".join(f"<p>{line}</p>" if line.strip() else "<p><br></p>"
                       for line in _escape(text).split("\n"))
        if tag == "iframe":
            frame = target.content_frame
            frame.locator("body").evaluate("(el, html) => { el.innerHTML = html; }", html)
        elif tag in ("textarea", "input"):
            target.fill(text)
        else:
            target.evaluate("(el, html) => { el.innerHTML = html; "
                            "el.dispatchEvent(new Event('input', {bubbles: true})); }", html)

    def _save(self, page) -> None:
        selectors = self.selectors()
        page.locator(selectors["save_button"]).first.click()
        self.settle(page)
        marker = selectors.get("saved_marker")
        if marker:
            page.locator(marker).first.wait_for(timeout=15000)

    def update_article(self, article: str, body: str = None, title: str = None) -> Dict[str, Any]:
        selectors = self.selectors()

        def work(page):
            edit_url = self._template("edit_url", id=quote_plus(article))
            if edit_url:
                self.goto(page, edit_url)
                article_id = article
            else:
                article_id = self._open_article(page, article)
                edit = page.locator(selectors["edit_button"]).first
                if not edit.count():
                    raise RightAnswersError(
                        "Cerebro couldn't find this portal's Edit button. Open Cerebro → "
                        "Connect → RightAnswers → Teach, and include the Edit step.")
                edit.click()
                self.settle(page)
            if title is not None:
                page.locator(selectors["editor_title"]).first.fill(title)
            if body is not None:
                self._fill_body(page, body)
            self._save(page)
            return {"id": article_id, "url": page.url,
                    "detail": f"{self.label} article {article_id} updated"}

        return self.run(work, f"Updating {self.label} article {article}")

    def create_article(self, title: str, body: str) -> Dict[str, Any]:
        selectors = self.selectors()

        def work(page):
            create_url = self._template("create_url")
            if not create_url:
                raise RightAnswersError(
                    "Creating articles needs the “create_url” address for your RightAnswers "
                    "portal in connectors/rightanswers.json.")
            self.goto(page, create_url)
            page.locator(selectors["editor_title"]).first.fill(title)
            self._fill_body(page, body)
            self._save(page)
            article_id = self.article_id_from(page.url)
            return {"id": article_id, "url": page.url,
                    "detail": f"{self.label} article “{title}” created"}

        return self.run(work, f"Creating a {self.label} article")


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


connector: Optional[RightAnswersConnector] = register(RightAnswersConnector())

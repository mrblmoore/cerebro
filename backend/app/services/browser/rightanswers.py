"""
RightAnswers (Upland) knowledge base through the hidden browser.

Work starts from the agent workspace (DEXIS: SolutionManager's
``/solutionmanger/controller/workspace/``), which is where articles are
reached; the site's front page doesn't link to them. SolutionManager is a
single-page workspace that may put its search and articles in frames, so
every selector is looked for in each frame, not just the top page.

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
    # Search. No address by default: the workspace's own search box is used
    # until Teach learns this portal's search address; a portal without one
    # gets the classic self-service search address instead.
    "search_url": "",
    "portal_search_url": "{base}/portal/ss/?searchText={query}",
    "search_input": ("input[type='search'], input[name='searchText'], #searchText, "
                     "input[name='search'], input[name='query'], input[id*='search' i], "
                     "input[placeholder*='search' i]"),
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
    # The results pager. Cerebro follows it so one search can return far more
    # than the first page of articles.
    "next_page": ("a[rel='next'], a[aria-label*='next' i], button[aria-label*='next' i], "
                  "a:has-text('Next'), button:has-text('Next'), li.next a, "
                  ".pagination .next a, [class*='pager'] [class*='next']"),
}

#: Articles one search returns by default, the most it may be asked for, and how
#: many result pages it follows to get them.
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 60
MAX_RESULT_PAGES = 6
#: Research: phrasings tried, and articles read in full.
MAX_QUERIES = 5
DEFAULT_READ_COUNT = 5
MAX_READ_COUNT = 8


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
ARTICLE_LINK_PATTERN = r"solution|article|kb[-_/]?\d|[?&](?:id|docid|contentid|solutionid)="

#: SolutionManager's path is spelled both ways in the wild ("solutionmanger"
#: is what DEXIS gave); if one 404s the other is tried and remembered.
_SPELLINGS = ("solutionmanger", "solutionmanager")


class RightAnswersConnector(BrowserConnector):
    name = "rightanswers"
    label = "RightAnswers"
    keep_path = True
    enabled_setting = "RIGHTANSWERS_ENABLED"
    url_setting = "RIGHTANSWERS_URL"
    default_selectors = DEFAULT_SELECTORS

    def __init__(self):
        super().__init__()
        from app.services.browser.teach import Teacher

        #: Learns this company's portal layout by watching one search.
        self.teacher = Teacher(self)

    # ------------------------------------------------------------ helpers
    def open_start(self, page):
        """Open the workspace; if that address errors, try the other spelling."""
        response = super().open_start(page)
        if not _page_missing(page, response):
            return response
        for alternative in _alternatives(self.start_url):
            retry = page.goto(alternative, wait_until="domcontentloaded")
            self.settle(page)
            if not _page_missing(page, retry):
                self.remember(start_url=alternative)
                return retry
        return response

    @staticmethod
    def _find(page, selector: str):
        """The first match for ``selector`` in the page or any of its frames."""
        if not selector:
            return None
        for frame in [page.main_frame] + [f for f in page.frames if f != page.main_frame]:
            try:
                locator = frame.locator(selector)
                if locator.count():
                    return locator
            except Exception:  # noqa: BLE001 - a frame that navigated away
                continue
        return None

    def _locate(self, page, selector: str):
        """``_find``, falling back to the top page (so waits and errors read well)."""
        found = self._find(page, selector)
        return (found if found is not None else page.locator(selector)).first

    def _template(self, key: str, **values) -> str:
        template = self.selectors().get(key) or ""
        if not template:
            return ""
        return template.format(base=self.base_url, **values)

    def article_id_from(self, url: str) -> Optional[str]:
        match = re.search(self.selectors()["article_id_pattern"], url or "")
        return match.group(1) if match else None

    @classmethod
    def _first_text(cls, scope, selector: str) -> str:
        if not selector:
            return ""
        try:
            if hasattr(scope, "main_frame"):          # a page: look in its frames too
                found = cls._find(scope, selector)
                locator = found.first if found is not None else None
            else:
                locator = scope.locator(selector).first
            if locator is None or locator.count() == 0:
                return ""
            return (locator.inner_text(timeout=2000) or "").strip()
        except Exception:  # noqa: BLE001 - optional field
            return ""

    # --------------------------------------------------------------- reads
    def search(self, query: str, limit: int = DEFAULT_SEARCH_LIMIT) -> List[Dict[str, Any]]:
        """Search the knowledge base, following result pages until ``limit``
        articles are found (or the results run out)."""
        limit = max(1, min(int(limit or DEFAULT_SEARCH_LIMIT), MAX_SEARCH_LIMIT))
        return self.run(lambda page: self._search_on(page, query, limit),
                        f"Searching {self.label}")

    def _submit_search(self, page, query: str) -> None:
        selectors = self.selectors()
        url = self._template("search_url", query=quote_plus(query))
        if url:
            self.goto(page, url)
            return
        self.goto(page)
        box = self._find(page, selectors["search_input"])
        if box is not None:
            box.first.fill(query)
            box.first.press("Enter")
            self.settle(page)
            page.wait_for_timeout(800)        # results render after the request
        elif self._template("portal_search_url", query=quote_plus(query)):
            self.goto(page, self._template("portal_search_url", query=quote_plus(query)))
        else:
            raise RightAnswersError(
                "Cerebro couldn't find the search box in the RightAnswers workspace. "
                "Open Cerebro → Connect → RightAnswers → Teach so it can learn it.")

    def _results_on_page(self, page, limit: int) -> List[Dict[str, Any]]:
        """The articles listed on the current results page."""
        selectors = self.selectors()
        results = []
        found = self._find(page, selectors["result_item"])
        items = found if found is not None else page.locator(selectors["result_item"])
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
            # to any link that looks like an article, in any frame.
            for frame in page.frames:
                try:
                    links = frame.evaluate(_ARTICLE_LINKS_JS, ARTICLE_LINK_PATTERN)
                except Exception:  # noqa: BLE001
                    continue
                for link in links:
                    results.append({"id": self.article_id_from(link["href"]),
                                    "title": link["title"], "snippet": link["snippet"],
                                    "url": link["href"]})
                if len(results) >= limit:
                    break
            results = results[:limit]
        return results

    def _next_page(self, page) -> bool:
        """Move to the next page of results; False when there isn't one."""
        selector = self.selectors().get("next_page")
        found = self._find(page, selector) if selector else None
        if found is None:
            return False
        try:
            button = found.first
            if not button.is_visible(timeout=1000) or not button.is_enabled(timeout=1000):
                return False
            if (button.get_attribute("aria-disabled") or "").lower() == "true":
                return False
            button.click(timeout=3000)
        except Exception:  # noqa: BLE001 - no usable next button
            return False
        self.settle(page)
        page.wait_for_timeout(600)
        return True

    def _search_on(self, page, query: str, limit: int) -> List[Dict[str, Any]]:
        """Run one search on ``page`` and gather up to ``limit`` articles."""
        self._submit_search(page, query)
        collected: List[Dict[str, Any]] = []
        seen = set()
        for _ in range(MAX_RESULT_PAGES):
            fresh = 0
            for item in self._results_on_page(page, limit):
                key = item.get("id") or item.get("url") or item["title"]
                if key in seen:
                    continue
                seen.add(key)
                collected.append(item)
                fresh += 1
            # No new articles means the "next" button did nothing useful.
            if len(collected) >= limit or not fresh or not self._next_page(page):
                break
        return collected[:limit]

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

    def _read_on(self, page, article: str) -> Dict[str, Any]:
        selectors = self.selectors()
        article_id = self._open_article(page, article)
        title = self._first_text(page, selectors["article_title"])
        found = self._find(page, selectors["article_body"])
        body_area = found.first if found is not None else None
        body = self.readable_text_of(body_area) if body_area is not None else ""
        if not body:
            body = self.readable_text(page)
        if not title and not body:
            raise RightAnswersError(f"Article {article} could not be read.")
        return {"id": article_id, "title": title or f"Article {article_id}",
                "body": body, "meta": self._first_text(page, selectors["article_meta"]),
                "url": page.url}

    def get_article(self, article: str) -> Dict[str, Any]:
        return self.run(lambda page: self._read_on(page, article),
                        f"Reading {self.label} article {article}")

    def research(self, queries: List[str], per_query: int = DEFAULT_SEARCH_LIMIT,
                 read: int = DEFAULT_READ_COUNT) -> Dict[str, Any]:
        """Search several phrasings, then read the best articles in full.

        One browser job does all of it, so the model gets articles to answer
        from instead of a list of titles to follow up one at a time. Articles
        that several phrasings find rank first.
        """
        queries = [q.strip() for q in queries if q and q.strip()][:MAX_QUERIES]
        per_query = max(1, min(int(per_query or DEFAULT_SEARCH_LIMIT), MAX_SEARCH_LIMIT))
        read = max(0, min(int(read if read is not None else DEFAULT_READ_COUNT), MAX_READ_COUNT))

        def work(page):
            merged: Dict[str, Dict[str, Any]] = {}
            failures: List[str] = []
            for query in queries:
                try:
                    hits = self._search_on(page, query, per_query)
                except RightAnswersError:
                    raise
                except Exception as exc:  # noqa: BLE001 - keep the other phrasings
                    failures.append(f"{query}: {exc}")
                    continue
                for rank, hit in enumerate(hits):
                    key = hit.get("id") or hit.get("url") or hit["title"]
                    entry = merged.setdefault(key, {**hit, "queries": 0, "score": 0.0})
                    entry["queries"] += 1
                    entry["score"] += 1.0 / (rank + 1)
            ranked = sorted(merged.values(), key=lambda e: (-e["queries"], -e["score"]))
            articles = []
            for hit in ranked[:read]:
                try:
                    articles.append(self._read_on(page, hit.get("url") or hit["id"]))
                except Exception as exc:  # noqa: BLE001 - one bad article must not lose the rest
                    failures.append(f"{hit['title']}: {exc}")
            return {"queries": queries, "results": ranked, "articles": articles,
                    "failures": failures}

        return self.run(work, f"Researching {self.label}")

    # -------------------------------------------------------------- writes
    # Called only by approved AgentActions (see app.services.agent).
    def _fill_body(self, page, text: str) -> None:
        selector = self.selectors()["editor_body"]
        target = self._locate(page, selector)
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
        self._locate(page, selectors["save_button"]).click()
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
                edit = self._locate(page, selectors["edit_button"])
                if not edit.count():
                    raise RightAnswersError(
                        "Cerebro couldn't find this portal's Edit button. Open Cerebro → "
                        "Connect → RightAnswers → Teach, and include the Edit step.")
                edit.click()
                self.settle(page)
            if title is not None:
                self._locate(page, selectors["editor_title"]).fill(title)
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
            self._locate(page, selectors["editor_title"]).fill(title)
            self._fill_body(page, body)
            self._save(page)
            article_id = self.article_id_from(page.url)
            return {"id": article_id, "url": page.url,
                    "detail": f"{self.label} article “{title}” created"}

        return self.run(work, f"Creating a {self.label} article")


def _page_missing(page, response) -> bool:
    """A "not found" or error page, rather than the workspace."""
    if response is not None and getattr(response, "status", 200) >= 400:
        return True
    try:
        title = (page.title() or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return any(marker in title for marker in ("404", "not found", "error"))


def _alternatives(url: str):
    for wrong, right in (_SPELLINGS, tuple(reversed(_SPELLINGS))):
        if f"/{wrong}/" in url:
            yield url.replace(f"/{wrong}/", f"/{right}/", 1)


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


connector: Optional[RightAnswersConnector] = register(RightAnswersConnector())

"""
Teach Cerebro a RightAnswers portal by watching the user use it once.

Every company's RightAnswers portal is laid out a little differently, and
Cerebro can't see DEXIS's from here. So instead of guessing, Cerebro opens its
browser visibly and asks the user to do three ordinary things:

1. search for a given word,
2. open one of the results,
3. optionally, click **Edit** on that article.

From what the browser sees it works out the portal's search address, its
article address, which elements are results, where an article's text is, and
(if step 3 happened) the editor and Save button. It writes those as selector
overrides to ``DATA_DIR/connectors/rightanswers.json`` — the file the
connector already reads — and saves a diagnostic bundle (addresses and page
markup with hidden form values removed; no cookies) beside it, in case
something needs adjusting by hand.
"""

import json
import re
import threading
import time
import zipfile
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote_plus, unquote_plus, urlparse

from app.core import logger
from app.core.paths import CONNECTORS_DIR
from app.services.browser.engine import engine

#: The word the user is asked to search for. Common enough to return results
#: in any support knowledge base.
TEACH_WORD = "password"
#: How long each step waits for the user.
STEP_TIMEOUT_SECONDS = 600
EDIT_STEP_SECONDS = 120
POLL_SECONDS = 1.5

#: Finds what the page shows for one result link: the repeated container
#: around it, and a selector for that container.
#: Every input's typed value, with a selector for it — how an in-page search
#: (a workspace whose address doesn't change) is recognised.
_INPUTS_JS = r"""
() => [...document.querySelectorAll('input:not([type=hidden]):not([type=password])')]
  .filter(el => el.value)
  .map(el => ({
    value: el.value,
    selector: el.id ? `#${CSS.escape(el.id)}`
      : el.name ? `input[name='${el.name}']`
      : el.placeholder ? `input[placeholder='${el.placeholder.replace(/'/g, "\\'")}']`
      : 'input[type=search], input[type=text]',
  }))
"""

_RESULT_SHAPE_JS = r"""
(hrefPart) => {
  const link = [...document.querySelectorAll('a[href]')].find(a => a.href.includes(hrefPart));
  if (!link) return null;
  const sig = el => el.tagName.toLowerCase() + [...el.classList].sort().map(c => '.' + CSS.escape(c)).join('');
  let node = link.parentElement, item = null;
  while (node && node !== document.body) {
    const parent = node.parentElement;
    if (parent) {
      const same = [...parent.children].filter(c => sig(c) === sig(node));
      if (same.length >= 2) { item = node; break; }
    }
    node = node.parentElement;
  }
  // Only one result on the page: no repetition to go by, so take the nearest
  // block that looks like a result row.
  if (!item) {
    item = link.closest('li, tr, article, [class*="result" i], [class*="hit" i], [class*="item" i]');
    if (item && !item.classList.length && !['LI', 'TR', 'ARTICLE'].includes(item.tagName)) item = null;
  }
  const linkSel = 'a[href*="' + hrefPart.split('=')[0] + '="]';
  return {
    item: item ? sig(item) : null,
    link: linkSel,
    title: (link.innerText || '').trim().slice(0, 200),
  };
}
"""

#: The element holding most of an article's text, and the page title.
_ARTICLE_SHAPE_JS = r"""
() => {
  const skip = new Set(['HTML', 'BODY', 'SCRIPT', 'STYLE', 'NAV', 'HEADER', 'FOOTER']);
  const all = [...document.querySelectorAll('main, article, section, div, td')]
    .filter(el => !skip.has(el.tagName));
  const total = el => (el.innerText || '').trim().length;
  const best = all.reduce((acc, el) => (total(el) > 80 && (!acc || total(el) > total(acc)) ? el : acc), null);
  if (!best) return null;
  // The deepest element that still holds most of the text: the body itself,
  // not the page frame around it.
  let body = best;
  for (;;) {
    const child = [...body.children].find(c => total(c) >= total(body) * 0.85 && !skip.has(c.tagName));
    if (!child) break;
    body = child;
  }
  const sel = el => el.id ? '#' + CSS.escape(el.id)
    : el.tagName.toLowerCase() + [...el.classList].map(c => '.' + CSS.escape(c)).join('');
  const heading = document.querySelector('h1, h2, [class*="title"]');
  return { body: sel(body), title: heading ? sel(heading) : null,
           titleText: heading ? heading.innerText.trim().slice(0, 200) : document.title };
}
"""

#: The article editor, if the page is one, and its Save button.
_EDITOR_SHAPE_JS = r"""
() => {
  const sel = el => {
    if (el.id) return '#' + CSS.escape(el.id);
    const base = el.tagName.toLowerCase() + (el.name ? '[name="' + el.name + '"]' : '')
      + [...el.classList].slice(0, 2).map(c => '.' + CSS.escape(c)).join('');
    const text = (el.innerText || el.value || '').trim();
    // A bare "button" would match the first button on the page; pin it by its label.
    return (el.name || el.classList.length || !text) ? base : base + ':has-text("' + text.slice(0, 40) + '")';
  };
  const editor = document.querySelector('iframe.cke_wysiwyg_frame, iframe[title*="editor" i], [contenteditable="true"], textarea');
  if (!editor) return null;
  const save = [...document.querySelectorAll('button, input[type=submit], input[type=button], a')]
    .find(b => /^(save|publish|submit|update)\b/i.test((b.innerText || b.value || '').trim()));
  const title = [...document.querySelectorAll('input[type=text], input:not([type])')]
    .find(i => (i.value || '').length > 3);
  return { body: sel(editor), save: save ? sel(save) : null, title: title ? sel(title) : null };
}
"""


def _strip_hidden_values(html: str) -> str:
    """Remove hidden form values (view state, anti-forgery tokens) from a capture."""
    return re.sub(r'(<input[^>]*type=["\']?hidden["\']?[^>]*value=)(["\']).*?\2',
                  r'\1\2\2', html or "", flags=re.IGNORECASE)


def _addresses(snap: Dict[str, Any]) -> set:
    """The page's address and every frame's."""
    return {snap["url"]} | {frame["url"] for frame in snap.get("frames", [])}


class Teacher:
    """Runs one teaching session; status is read by the API while it runs."""

    def __init__(self, connector):
        self.connector = connector
        self._lock = threading.Lock()
        self._state: Dict[str, Any] = {"status": "idle"}

    # ------------------------------------------------------------ state
    def state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **values) -> None:
        with self._lock:
            self._state.update(values)

    # ------------------------------------------------------------ start
    def start(self, mode: str = "visible") -> Dict[str, Any]:
        """Open the browser and start watching. ``mode`` is for tests."""
        connector = self.connector
        try:
            connector.require_enabled()
        except Exception as exc:  # noqa: BLE001 - shown to the user
            return {"ok": False, "status": "failed", "detail": str(exc)}
        if self.state().get("status") in ("waiting", "opening", "editing"):
            return {"ok": True, **self.state()}

        def open_window(eng):
            eng.hold_visible = mode == "visible"
            eng.close_context()
            page = eng.page(connector.name, mode=mode)
            page.goto(connector.sign_in_url(), wait_until="domcontentloaded")
            return page.url

        try:
            engine().submit(open_window, "Opening RightAnswers to learn its layout")
        except Exception as exc:  # noqa: BLE001
            engine().hold_visible = False
            return {"ok": False, "status": "failed", "detail": str(exc)}

        self._set(status="waiting", word=TEACH_WORD, started=time.time(), learned={},
                  detail=f"In the window that opened, search RightAnswers for “{TEACH_WORD}”, "
                         "then open any result. Optionally click Edit on it too.")
        threading.Thread(target=self._watch, daemon=True, name="cerebro-teach").start()
        return {"ok": True, **self.state()}

    # ------------------------------------------------------------ watch
    def _snapshot(self) -> Optional[Dict[str, Any]]:
        """The newest page the user is looking at: its URL and markup."""
        def job(eng):
            context = eng.context()
            pages = [p for p in context.pages if not p.is_closed()]
            if not pages:
                return None
            page = pages[-1]          # a result may open in a new tab
            try:
                page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:  # noqa: BLE001
                pass
            # Workspaces like SolutionManager keep the top address and work in
            # frames, so each frame's address, markup and typed inputs count.
            frames = []
            for frame in page.frames:
                try:
                    frames.append({"url": frame.url, "html": frame.content(),
                                   "inputs": frame.evaluate(_INPUTS_JS)})
                except Exception:  # noqa: BLE001 - a frame mid-navigation
                    continue
            return {"url": page.url, "html": page.content(), "page": len(pages) - 1,
                    "frames": frames}

        try:
            return engine().submit(job, "Watching RightAnswers", timeout=30)
        except Exception as exc:  # noqa: BLE001 - window closed, page navigating…
            logger.info("browser", "Teach snapshot skipped", {"error": str(exc)[:160]})
            return None

    def _evaluate(self, snap: Dict[str, Any], script: str, arg=None):
        """Run an analysis script against a *captured* page.

        The user has usually moved on by the time a page is analysed (the
        result they clicked replaced the search page), so the capture is
        loaded into a scratch tab rather than reading the live one.
        """
        def job(eng):
            scratch = eng.context().new_page()
            try:
                scratch.set_content(snap.get("html") or "", wait_until="domcontentloaded")
                return scratch.evaluate(script, arg) if arg is not None else scratch.evaluate(script)
            finally:
                scratch.close()
        return engine().submit(job, "Learning RightAnswers layout", timeout=30)

    def _watch(self) -> None:
        captures: Dict[str, Dict[str, Any]] = {}
        learned: Dict[str, Any] = {}
        deadline = time.time() + STEP_TIMEOUT_SECONDS
        word = TEACH_WORD
        try:
            # Step 1 — the search results page: its address (or a frame's)
            # contains the word, or the word was typed into a search box.
            while time.time() < deadline:
                time.sleep(POLL_SECONDS)
                snap = self._snapshot()
                if snap is None:
                    continue
                found = self._search_in(snap, word)
                if found:
                    captures["search"] = found["snap"]
                    learned.update(found["learned"])
                    self._set(status="opening", learned=dict(learned),
                              detail="Got the search page. Now open any result.")
                    break
            else:
                return self._finish("failed", "No search was seen within 10 minutes.", captures, learned)

            # Step 2 — an article: a new address (top or frame) that isn't the search page.
            seen = _addresses(captures["search"])
            while time.time() < deadline:
                time.sleep(POLL_SECONDS)
                snap = self._snapshot()
                if snap is None:
                    continue
                new = [url for url in _addresses(snap) if url not in seen]
                if not new:
                    continue
                if any(word in unquote_plus(url).lower() for url in new):
                    captures["search"] = snap        # a refined search; keep watching
                    seen = _addresses(snap)
                    continue
                if snap["url"] in seen:
                    # The article opened inside a frame: analyse that frame.
                    frame = next(f for f in snap.get("frames", []) if f["url"] in new)
                    snap = {**snap, "url": frame["url"], "html": frame["html"]}
                captures["article"] = snap
                learned.update(self._learn_article(snap, captures["search"]))
                self._set(status="editing", learned=dict(learned),
                          detail="Got the article. Optionally click Edit on it now, "
                                 "so Cerebro learns the editor too.")
                break
            else:
                return self._finish("failed", "No article was opened within 10 minutes.",
                                    captures, learned)

            # Step 3 (optional) — the editor.
            edit_deadline = time.time() + EDIT_STEP_SECONDS
            while time.time() < edit_deadline:
                time.sleep(POLL_SECONDS)
                snap = self._snapshot()
                if snap is None or snap["url"] == captures["article"]["url"]:
                    continue
                shape = self._evaluate(snap, _EDITOR_SHAPE_JS)
                if shape:
                    captures["editor"] = snap
                    learned.update(self._learn_editor(snap["url"], shape, learned))
                    break
            self._finish("learned", None, captures, learned)
        except Exception as exc:  # noqa: BLE001 - reported, with whatever was captured
            logger.error("browser", "Teaching failed", {"error": str(exc)})
            self._finish("failed", f"Teaching stopped: {exc}", captures, learned)

    # ---------------------------------------------------------- learning
    def _search_in(self, snap: Dict[str, Any], word: str) -> Optional[Dict[str, Any]]:
        """Where the teaching search shows up in ``snap``, and what it teaches."""
        if word in unquote_plus(snap["url"]).lower():
            return {"snap": snap, "learned": self._learn_search(snap["url"])}
        for frame in snap.get("frames", []):
            if word in unquote_plus(frame["url"]).lower():
                return {"snap": {**snap, "url": frame["url"], "html": frame["html"]},
                        "learned": self._learn_search(frame["url"])}
        for frame in snap.get("frames", []):
            for item in frame.get("inputs") or []:
                if word in (item.get("value") or "").lower():
                    # An in-page search: the address doesn't change, so the
                    # search box is what is learned. Results are analysed in
                    # whichever frame now shows the most links.
                    best = max(snap.get("frames") or [frame],
                               key=lambda f: f["html"].count("<a "))
                    return {"snap": {**snap, "html": best["html"]},
                            "learned": {"search_input": item["selector"], "search_url": ""}}
        return None

    def _template(self, url: str, value: str, placeholder: str) -> str:
        """``url`` with ``value`` replaced by ``{placeholder}`` and the host by ``{base}``."""
        base = self.connector.base_url
        template = url.replace(base, "{base}", 1) if url.startswith(base) else url
        for form in (quote_plus(value), value, value.replace(" ", "%20")):
            if form and form in template:
                return template.replace(form, "{" + placeholder + "}", 1)
        return template

    def _learn_search(self, url: str) -> Dict[str, Any]:
        return {"search_url": self._template(url, TEACH_WORD, "query").replace(
            TEACH_WORD.capitalize(), "{query}")}

    def _learn_article(self, article: Dict[str, Any], search: Dict[str, Any]) -> Dict[str, Any]:
        learned: Dict[str, Any] = {}
        url = article["url"]
        # The article's ID is the query value that also appears in a link on
        # the search page (the link the user clicked).
        for key, value in parse_qsl(urlparse(url).query):
            if value and len(value) >= 2 and f"{key}={value}" in search["html"].replace("&amp;", "&"):
                learned["article_url"] = self._template(url.split("#")[0], value, "id")
                learned["article_id_pattern"] = re.escape(key) + r"=([^&#]+)"
                shape = self._evaluate(search, _RESULT_SHAPE_JS, f"{key}={value}")
                if shape:
                    if shape.get("item"):
                        learned["result_item"] = shape["item"]
                    learned["result_link"] = shape["link"]
                    learned["result_title"] = shape["link"]
                break
        shape = self._evaluate(article, _ARTICLE_SHAPE_JS)
        if shape:
            learned["article_body"] = shape["body"]
            if shape.get("title"):
                learned["article_title"] = shape["title"]
        return learned

    def _learn_editor(self, url: str, shape: Dict[str, Any], learned: Dict[str, Any]) -> Dict[str, Any]:
        found: Dict[str, Any] = {"editor_body": shape["body"]}
        if shape.get("save"):
            found["save_button"] = shape["save"]
        if shape.get("title"):
            found["editor_title"] = shape["title"]
        pattern = learned.get("article_id_pattern")
        match = re.search(pattern, url) if pattern else None
        if match:
            found["edit_url"] = self._template(url.split("#")[0], match.group(1), "id")
        return found

    # ------------------------------------------------------------ finish
    def _finish(self, status: str, detail: Optional[str], captures: Dict[str, Any],
                learned: Dict[str, Any]) -> None:
        CONNECTORS_DIR.mkdir(parents=True, exist_ok=True)
        bundle = CONNECTORS_DIR / "rightanswers-capture.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("learned.json", json.dumps(learned, indent=2))
            archive.writestr("urls.json", json.dumps(
                {name: snap["url"] for name, snap in captures.items()}, indent=2))
            for name, snap in captures.items():
                archive.writestr(f"{name}.html", _strip_hidden_values(snap.get("html", "")))

        if learned.get("search_url") or learned.get("search_input"):
            path = CONNECTORS_DIR / "rightanswers.json"
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                existing = {}
            existing.update(learned)
            path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

        def close(eng):
            eng.hold_visible = False
            eng.close_context()

        try:
            engine().submit(close, "Finishing RightAnswers teaching", timeout=30)
        except Exception:  # noqa: BLE001
            engine().hold_visible = False

        if status == "learned":
            parts = ["search", "articles"] + (["editing"] if "editor_body" in learned else [])
            detail = (f"Learned RightAnswers {', '.join(parts)}. Try “Search RightAnswers for …” "
                      "in Ask." + ("" if "editor_body" in learned else
                                   " (Editing wasn't shown; run Teach again and click Edit "
                                   "to enable article updates.)"))
        self._set(status=status, detail=detail, learned=dict(learned),
                  capture=str(bundle), finished=time.time())
        logger.info("browser", "Teaching finished", {"status": status, "learned": list(learned)})

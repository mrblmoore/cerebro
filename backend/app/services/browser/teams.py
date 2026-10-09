"""
Microsoft Teams on the web (``teams.cloud.microsoft``) through the hidden browser.

Messages are read from the chat service JSON the Teams page fetches for
itself (conversations with their latest message, and each chat's messages),
with the chat pane as a fallback. Sending types into the chat's own compose
box, so the message comes from the user, signed in as them.

A chat is reached by its id when Cerebro has seen it, by a Teams deep link
to the people in it (``/l/chat/0/0?users=…``), or by its name in the chat list.
"""

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode, unquote

from app.services.browser import register
from app.services.browser.messaging import (M365_HOSTS, MessagingConnector, MessagingError, _unique,
                                            iso, mentions_me, strip_html, walk)

#: Most chats opened per check to read what's new in them.
CHATS_PER_CHECK = 3

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

DEFAULT_SELECTORS: Dict[str, Any] = {
    # Addresses.
    "chat_url": "{base}/l/chat/{chat}/0",
    "new_chat_url": "{base}/l/chat/0/0?{query}",
    # The launcher Teams shows for /l/ links ("open in the app or the web?").
    "use_web_app": ("button:has-text('Use the web app instead'), a:has-text('Use the web app instead'), "
                    "button:has-text('Continue on this browser')"),
    # Chat list and chat pane (fallback when no JSON was seen).
    "chat_list_item": ("[data-tid^='chat-list-item'], [role='treeitem'][data-tid*='chat'], "
                       "[data-inp='chat-list-item']"),
    "message": "[data-tid='chat-pane-message'], [data-tid='chat-pane-item']",
    "message_author": "[data-tid='message-author-name']",
    "message_body": "[id^='content-'], [data-tid='message-body']",
    "search_box": "input[data-tid='searchInputField'], input[placeholder*='Search' i]",
    "search_results": "[data-tid='search-results'], [role='main']",
    "search_suggestion": ("[data-tid*='suggestion' i], [data-tid*='search-result' i], "
                          "[role='option'], [role='listitem'][data-tid*='people' i]"),
    # Writing.
    "compose": ("[data-tid='ckeditor'][contenteditable='true'], [data-tid='ckeditor'] [contenteditable='true'], "
                "div[role='textbox'][contenteditable='true']"),
    "send_button": ("button[data-tid='newMessageCommands-send'], button[name='send'], "
                    "button[aria-label^='Send']"),
    # The signed-in user (the avatar button, whose label carries their name).
    "me": "button[data-tid='me-control-avatar-trigger'], [data-tid='me-control-avatar']",
}


class TeamsConnector(MessagingConnector):
    name = "teams"
    label = "Teams"
    enabled_setting = "TEAMS_BROWSER_ENABLED"
    url_setting = "TEAMS_URL"
    auto_apply_setting = "TEAMS_AUTO_SEND"
    default_selectors = DEFAULT_SELECTORS
    capture_pattern = r"/api/chatsvc/|/v1/users/ME/|/api/csa/|chatsvcagg|/api/mt/"
    app_hosts = M365_HOSTS

    def __init__(self):
        super().__init__()
        #: Chat id -> its latest message id as last seen in the chat list.
        self._latest: Dict[str, str] = {}
        self._read_up_to: Dict[str, str] = {}

    def _url(self, key: str, **values) -> str:
        return (self.selectors().get(key) or "").format(base=self.base_url, **values)

    def collect(self, quiet: bool = True) -> List[Dict[str, Any]]:
        """New messages: the chat list shows each chat's latest message only,
        so chats with new activity are opened to read what else arrived."""
        def work(page):
            self.ensure_open(page)
            page.wait_for_timeout(500)
            found = self.drain()
            changed = [chat for chat, latest in self._latest.items()
                       if self._read_up_to.get(chat) != latest][:CHATS_PER_CHECK]
            for chat in changed:
                try:
                    self._open_chat(page, chat)
                    page.wait_for_timeout(1200)
                    found.extend(self.drain())
                    self._read_up_to[chat] = self._latest[chat]
                except Exception:  # noqa: BLE001 - one chat failing doesn't stop the rest
                    continue
            if changed:                                  # back to the main view for next time
                self.goto(page)
                self._opened_at = time.time()
            return _unique(found) or self.scrape(page)

        return self.run(work, f"Checking {self.label}", quiet=quiet)

    def account_name(self, page) -> Optional[str]:
        if self.me.get("name"):
            return self.me["name"]
        found = self.locate(page, "me")
        if found is not None:
            label = (found.get_attribute("aria-label") or found.get_attribute("title") or "").strip()
            # "Profile picture of Sam Agent." / "Your profile, Sam Agent, Available"
            name = re.sub(r"^(profile( picture)?( of)?|your profile)[,:]?\s*", "", label,
                          flags=re.IGNORECASE).split(",")[0].strip(" .")
            if name:
                self.me["name"] = name
                return name
        return None

    def learn_identity(self, data: Any) -> None:
        for node in walk(data):
            details = node.get("userDetails")
            if isinstance(details, str) and details.startswith("{"):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = None
            if isinstance(details, dict):
                self.me.setdefault("name", details.get("name"))
                self.me.setdefault("email", details.get("upn"))
            if node.get("skypeid") or node.get("userMri"):
                self.me.setdefault("id", node.get("userMri") or f"8:{node.get('skypeid')}")

    # ------------------------------------------------------------- parse
    def parse(self, url: str, data: Any) -> List[Dict[str, Any]]:
        found = []
        nodes = list(walk(data))
        for node in nodes:                                 # chat names first
            chat = node.get("id")
            props = node.get("threadProperties")
            if isinstance(chat, str) and isinstance(props, dict):
                topic = props.get("topic") or props.get("spaceThreadTopic")
                if topic:
                    self.names[chat] = topic
                last = node.get("lastMessage")
                if isinstance(last, dict) and (last.get("id") or last.get("composetime")):
                    self._latest[chat] = str(last.get("id") or last.get("composetime"))
        for node in nodes:
            message = teams_message(node, self.me, self.names)
            if message:
                found.append(message)
        return found

    def scrape(self, page) -> List[Dict[str, Any]]:
        """Messages showing in the open chat pane."""
        selectors = self.selectors()
        found: List[Dict[str, Any]] = []
        try:
            chat_id = _chat_from_url(page.url)
            items = page.locator(selectors["message"])
            for index in range(max(0, items.count() - 30), items.count()):
                item = items.nth(index)
                author = item.locator(selectors["message_author"]).first
                body = item.locator(selectors["message_body"]).first
                sender = (author.inner_text(timeout=800) or "").strip() if author.count() else ""
                text = (body.inner_text(timeout=800) or "").strip() if body.count() else \
                    (item.inner_text(timeout=800) or "").strip()
                if not text or (sender and sender == self.me.get("name")):
                    continue
                key = item.get_attribute("data-mid") or item.get_attribute("id") or str(hash(text))
                found.append({"source": "teams", "type": "message", "id": f"{chat_id}:{key}",
                              "conversationId": chat_id, "sender_name": sender or None,
                              "body": text, "chat": self.names.get(chat_id),
                              "mentioned": mentions_me(text, self.me)})
        except Exception:  # noqa: BLE001 - no chat open
            pass
        return found

    # ------------------------------------------------------------ reading
    def read_chat(self, chat: str, limit: int = 20) -> Dict[str, Any]:
        """A chat's recent messages, by its id or its name in the chat list."""
        def work(page):
            self.ensure_open(page)
            self.drain()
            chat_id = self._open_chat(page, chat)
            page.wait_for_timeout(1500)
            messages = [m for m in self.drain() if not chat_id or m.get("conversationId") == chat_id] \
                or self.scrape(page)
            messages.sort(key=lambda m: m.get("timestamp") or "")
            return {"chat": self.names.get(chat_id) or chat, "id": chat_id,
                    "messages": messages[-limit:]}

        return self.run(work, f"Reading the Teams chat {chat[:40]}")

    def search(self, query: str, limit: int = 10) -> Dict[str, Any]:
        def work(page):
            self.ensure_open(page)
            self.drain()
            box = self.require(page, "search_box", "the Teams search box")
            box.click()
            box.fill(query)
            box.press("Enter")
            self.settle(page)
            page.wait_for_timeout(1500)
            words = [w for w in query.lower().split() if len(w) > 2]
            matches = [m for m in self.drain()
                       if any(w in (m.get("body") or "").lower() for w in words)][:limit]
            region = self.locate(page, "search_results")
            text = self.readable_text_of(region) if region is not None else ""
            return {"messages": matches, "text": text[:4000]}

        return self.run(work, f"Searching Teams for {query[:40]}")

    # ------------------------------------------------------------ writing
    # Called for approved (or auto-send) EnterpriseActions only.
    def send(self, body: str, chat: str = None, users: List[str] = None) -> Dict[str, Any]:
        """Post ``body`` in a chat (id or name), or to people by email address."""
        users = users or EMAIL_RE.findall(chat or "")
        if not (body or "").strip():
            raise MessagingError("There's nothing to send.")
        if not chat and not users:
            raise MessagingError("A Teams message needs a chat or a person to send to.")

        def work(page):
            if users and not (chat or "").startswith(("19:", "48:")):
                query = urlencode({"users": ",".join(users), "message": body}, quote_via=quote)
                self.goto(page, self._url("new_chat_url", query=query))
                self._skip_launcher(page)
            else:
                self.ensure_open(page)
                self._open_chat(page, chat)
            compose = self.require(page, "compose", "the message box", timeout=20000)
            typed = (compose.inner_text(timeout=2000) or "").strip()
            if body.strip()[:20] not in typed:
                self.type_text(compose, body)
            send = self.locate(page, "send_button")
            if send is not None:
                send.click()
            else:
                compose.press("Enter")
            page.wait_for_timeout(1200)
            if body.strip()[:20] in (compose.inner_text(timeout=2000) or ""):
                self.capture(page, "send")
                raise MessagingError("Teams didn't send the message — it is still in the box.")
            target = self.names.get(_chat_from_url(page.url)) or chat or ", ".join(users)
            return {"detail": f"Teams message sent to {target}"}

        return self.run(work, "Sending a Teams message")

    # ------------------------------------------------------------ helpers
    def _skip_launcher(self, page) -> None:
        button = self.locate(page, "use_web_app", timeout=4000)
        if button is not None:
            button.click()
            self.settle(page)

    def _open_chat(self, page, chat: str) -> Optional[str]:
        if not chat:
            return None
        if chat.startswith(("19:", "48:")):
            self.goto(page, self._url("chat_url", chat=quote(chat, safe="")))
            self._skip_launcher(page)
            return chat
        pattern = name_pattern(chat)
        items = page.locator(self.selectors()["chat_list_item"]).filter(has_text=pattern)
        if not items.count() and self._search_person(page, chat, pattern):
            return _chat_from_url(page.url)
        if not items.count():
            self.capture(page, "chat_list_item")
            raise MessagingError(f"I couldn't find anyone or any chat matching “{chat}” in Teams. "
                                 "Check the spelling, or give me their email address.")
        items.first.click()
        self.settle(page)
        return _chat_from_url(page.url)

    def _search_person(self, page, name: str, pattern) -> bool:
        """Find a person (or chat) through Teams' own search and open it."""
        box = self.locate(page, "search_box", timeout=4000)
        if box is None:
            return False
        try:
            box.click()
            box.fill(name)
            page.wait_for_timeout(1500)
            options = page.locator(self.selectors()["search_suggestion"]).filter(has_text=pattern)
            if not options.count():
                box.press("Escape")
                return False
            options.first.click()
            self.settle(page)
            page.wait_for_timeout(1000)
            return True
        except Exception:  # noqa: BLE001 - fall back to the "not found" message
            return False


def name_pattern(name: str):
    """A regex matching text that contains every word of ``name``, in any order."""
    words = re.findall(r"[^\W_]+", name or "", flags=re.UNICODE)
    return re.compile("".join(rf"(?=.*\b{re.escape(w)})" for w in words) or ".", re.I | re.S)


# ----------------------------------------------------------- JSON shapes
def _chat_from_url(url: str) -> Optional[str]:
    match = re.search(r"(?:conversations/|/chat/|/l/chat/)((?:19|48):[^/?#]+)", unquote(url or ""))
    return match.group(1) if match else None


def teams_message(node: Dict[str, Any], me: Dict[str, str],
                  names: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """A chat message if ``node`` is shaped like one from the Teams chat service."""
    if "messagetype" not in node or "content" not in node:
        return None
    kind = str(node.get("messagetype") or "")
    if not (kind.startswith("RichText") or kind == "Text"):
        return None                                      # calls, joins, edits, system events
    chat = (node.get("conversationid") or node.get("conversationId")
            or _chat_from_url(node.get("conversationLink") or ""))
    message_id = node.get("id") or node.get("clientmessageid")
    if not chat or not message_id:
        return None
    sender_id = str(node.get("from") or "").rsplit("/", 1)[-1]
    sender_name = node.get("imdisplayname") or node.get("fromDisplayNameInToken")
    if (me.get("id") and sender_id == me["id"]) or (me.get("name") and sender_name == me["name"]):
        return None                                      # the user's own message
    content = str(node.get("content") or "")
    body = strip_html(content)
    if not body:
        return None
    mentioned = False
    properties = node.get("properties") if isinstance(node.get("properties"), dict) else {}
    raw_mentions = properties.get("mentions")
    try:
        mentions = json.loads(raw_mentions) if isinstance(raw_mentions, str) else raw_mentions or []
    except ValueError:
        mentions = []
    for mention in mentions if isinstance(mentions, list) else []:
        if isinstance(mention, dict) and (
                (me.get("id") and mention.get("mri") == me["id"])
                or (me.get("name") and mention.get("displayName") == me["name"])):
            mentioned = True
    if not mentioned and "schema.skype.com/Mention" in content:
        mentioned = mentions_me("@" + " @".join(re.findall(r"Mention[^>]*>([^<]+)<", content)), me)
    direct = str(chat).endswith("@unq.gbl.spaces")
    return {
        "source": "teams", "type": "mention" if mentioned else "message",
        "id": f"{chat}:{message_id}", "conversationId": chat,
        "chat": names.get(chat) or (f"Chat with {sender_name}" if direct and sender_name else None),
        "sender": sender_id or None, "sender_name": sender_name, "body": body,
        "timestamp": iso(node.get("originalarrivaltime") or node.get("composetime")),
        "importance": "high" if str(properties.get("importance") or "").lower() in ("high", "urgent")
        else "normal",
        "direct": direct, "mentioned": mentioned,
    }


connector: Optional[TeamsConnector] = register(TeamsConnector())

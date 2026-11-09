"""
Outlook on the web (``outlook.cloud.microsoft/mail/``) through the hidden browser.

Reads the inbox and searches the mailbox from the JSON Outlook's own page
fetches (``service.svc`` FindItem / FindConversation / GetItem and search
results), and falls back to the message list on the page. Sending goes
through the page the way a person would — Outlook's own compose and reply
screens — so mail leaves from the user's mailbox, signed in as them, and
lands in Sent Items like any other.

Nothing is sent without the user's approval unless they switched on
"Send without asking", and even then only replies in an existing thread
(see :mod:`app.services.inbox_monitor` and the Ask tools).
"""

import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

from app.services.browser import register
from app.services.browser.messaging import (M365_HOSTS, MessagingConnector, MessagingError, fingerprint,
                                            iso, mentions_me, strip_html, walk)

DEFAULT_SELECTORS: Dict[str, Any] = {
    # Addresses ({mail} is the configured address, ending in "/mail/").
    "read_url": "{mail}deeplink/read/{id}",
    "compose_url": "{mail}deeplink/compose?{query}",
    # The message list and reading pane (fallback when no JSON was seen).
    "list_item": "[role='option'][data-convid], div[role='listbox'] [role='option']",
    "reading_pane": ("div[aria-label='Message body'], div[role='document'], "
                     "#ReadingPaneContainerId, [data-app-section='ReadingPane']"),
    "search_box": ("input#topSearchInput, input[aria-label='Search'], "
                   "input[placeholder*='Search' i]"),
    # Writing.
    "reply_button": ("button[aria-label='Reply'], button[title='Reply'], "
                     "[role='menuitem'][aria-label='Reply']"),
    "reply_all_button": "button[aria-label='Reply all'], button[title='Reply all']",
    "compose_body": ("div[aria-label='Message body'][contenteditable='true'], "
                     "div[contenteditable='true'][role='textbox'], div[contenteditable='true']"),
    "send_button": "button[aria-label='Send'], button[title='Send'], button:has-text('Send')",
    # Who is signed in (the account control in the top-right corner).
    "account": "#mectrl_currentAccount_secondary, #O365_MainLink_Me [aria-label]",
    # The calendar ({calendar} is the mail address with "mail/" → "calendar/").
    "calendar_url": "{calendar}view/week?startdt={start}",
    "event_compose_url": "{calendar}deeplink/compose?{query}",
    "event_url": "{calendar}deeplink/read/{id}",
    "calendar_event": "[data-calitemid], div[role='button'][aria-label*=' to ']",
    "event_send_button": ("button[aria-label='Send'], button[title='Send'], button:has-text('Send'), "
                          "button[aria-label='Save'], button:has-text('Save')"),
    "event_edit_button": "button[aria-label='Edit'], button[title='Edit'], button:has-text('Edit')",
    "event_start_date": "input[aria-label*='Start date' i]",
    "event_start_time": "input[aria-label*='Start time' i]",
    "event_end_date": "input[aria-label*='End date' i]",
    "event_end_time": "input[aria-label*='End time' i]",
    "event_cancel_button": ("button[aria-label*='Cancel meeting' i], button:has-text('Cancel meeting'), "
                            "button[aria-label='Delete'], button:has-text('Delete')"),
    "cancel_message": "div[contenteditable='true'][role='textbox'], textarea",
    "cancel_send_button": "button:has-text('Send cancellation'), button:has-text('Send'), button:has-text('Delete')",
}


class OutlookConnector(MessagingConnector):
    name = "outlook"
    label = "Outlook"
    enabled_setting = "OUTLOOK_BROWSER_ENABLED"
    url_setting = "OUTLOOK_URL"
    auto_apply_setting = "OUTLOOK_AUTO_SEND"
    default_selectors = DEFAULT_SELECTORS
    capture_pattern = (r"service\.svc|/search/api/|/owa/startupdata|/api/v2\.0/me|/api/beta/me|"
                       r"calendarview|/calendar/")
    app_hosts = M365_HOSTS

    # ------------------------------------------------------------ helpers
    @property
    def mail_url(self) -> str:
        start = self.start_url
        return start if start.endswith("/") else start + "/"

    def _url(self, key: str, **values) -> str:
        return (self.selectors().get(key) or "").format(mail=self.mail_url, **values)

    def account_name(self, page) -> Optional[str]:
        if self.me.get("email") or self.me.get("name"):
            return self.me.get("email") or self.me.get("name")
        found = self.locate(page, "account")
        if found is not None:
            text = (found.inner_text(timeout=1500) or found.get_attribute("aria-label") or "").strip()
            if "@" in text:
                self.me["email"] = text
            return text or None
        return None

    def learn_identity(self, data: Any) -> None:
        for node in walk(data):
            for key in ("UserEmailAddress", "LogonEmailAddress", "PrimarySmtpAddress"):
                value = node.get(key)
                if isinstance(value, str) and "@" in value and not self.me.get("email"):
                    self.me["email"] = value
            name = node.get("UserDisplayName")
            if isinstance(name, str) and name and not self.me.get("name"):
                self.me["name"] = name

    # ------------------------------------------------------------- parse
    def parse(self, url: str, data: Any) -> List[Dict[str, Any]]:
        from app.services.calendar import event_from

        events = getattr(self, "_events", None)
        if events is None:
            events = self._events = {}
        for node in walk(data):
            event = event_from(node)
            if event:
                events[event["id"]] = event
        items = [item for item in (outlook_item(node, self.me) for node in walk(data)) if item]
        # A conversation summary is only kept when its messages weren't seen.
        seen = {item.get("conversationId") for item in items if not item.get("summary")}
        return [item for item in items if not item.get("summary") or item.get("conversationId") not in seen]

    def scrape(self, page) -> List[Dict[str, Any]]:
        selector = self.selectors().get("list_item")
        found: List[Dict[str, Any]] = []
        try:
            items = page.locator(selector)
            for index in range(min(items.count(), 25)):
                item = items.nth(index)
                label = item.get_attribute("aria-label") or ""
                text = (item.inner_text(timeout=1500) or "").strip()
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                if not lines:
                    continue
                conversation = item.get_attribute("data-convid") or ""
                found.append({
                    "source": "outlook", "type": "email",
                    "id": f"dom:{conversation or fingerprint(text)}",
                    "conversationId": conversation or None,
                    "sender_name": lines[0], "subject": lines[1] if len(lines) > 1 else None,
                    "body": " ".join(lines[2:])[:1000] or None,
                    "read": not label.lower().startswith("unread"),
                    "summary": True,
                })
        except Exception:  # noqa: BLE001 - the list isn't showing; nothing to add
            pass
        return found

    # ------------------------------------------------------------ reading
    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        def work(page):
            self.ensure_open(page)
            self.drain()                                   # only results from now on
            box = self.require(page, "search_box", "the mailbox search box")
            box.click()
            box.fill(query)
            box.press("Enter")
            self.settle(page)
            page.wait_for_timeout(1500)
            results = [m for m in self.drain() if not m.get("summary")] or self.scrape(page)
            return results[:limit]

        return self.run(work, f"Searching Outlook for {query[:40]}")

    def read(self, message: str) -> Dict[str, Any]:
        """A message by its id (as Cerebro stored it) or its Outlook link."""
        item_id = _item_id(message)

        def work(page):
            self.drain()
            target = message if message.startswith(("http://", "https://")) \
                else self._url("read_url", id=quote(item_id, safe=""))
            self.goto(page, target)
            page.wait_for_timeout(1200)
            for item in self.drain():
                if item.get("id") == item_id or item_id in (item.get("reply_id") or ""):
                    return item
            pane = self.locate(page, "reading_pane", timeout=8000)
            body = self.readable_text_of(pane) if pane is not None else self.readable_text(page)
            return {"source": "outlook", "id": item_id, "body": body,
                    "subject": (page.title() or "").split(" - ")[0]}

        return self.run(work, "Reading an Outlook message")

    # ----------------------------------------------------------- calendar
    @property
    def calendar_base(self) -> str:
        base = self.mail_url
        return base[:-5] + "calendar/" if base.endswith("/mail/") else base + "calendar/"

    def _cal_url(self, key: str, **values) -> str:
        return (self.selectors().get(key) or "").format(calendar=self.calendar_base, **values)

    def calendar(self, start, end) -> List[Dict[str, Any]]:
        """Events between ``start`` and ``end``, from the calendar page's own data."""
        from datetime import timedelta

        def work(page):
            self.hook(page)
            self._events = {}
            self.drain()
            week = start
            while week < end:
                self.goto(page, self._cal_url("calendar_url", start=quote(week.strftime("%Y-%m-%d"))))
                self.settle(page)
                page.wait_for_timeout(1500)
                self.drain()
                week += timedelta(days=7)
            found = list((self._events or {}).values())
            return found or self._scrape_calendar(page)

        return self.run(work, "Reading your calendar", quiet=True) or []

    def _scrape_calendar(self, page) -> List[Dict[str, Any]]:
        """Events drawn on the page, from their labels ("Sync, 9:00 AM to 9:30 AM, Monday, …")."""
        from datetime import datetime

        found = []
        try:
            items = page.locator(self.selectors().get("calendar_event"))
            for index in range(min(items.count(), 80)):
                item = items.nth(index)
                label = item.get_attribute("aria-label") or ""
                match = re.search(r"^(.*?),\s*(\d{1,2}:\d{2}\s*[AP]M)\s*to\s*(\d{1,2}:\d{2}\s*[AP]M),"
                                  r"\s*\w+,\s*(\w+ \d{1,2},\s*\d{4})", label)
                if not match:
                    continue
                day = datetime.strptime(match.group(4).replace(",", ""), "%B %d %Y")
                times = [datetime.strptime(t.replace(" ", ""), "%I:%M%p") for t in match.group(2, 3)]
                start = day.replace(hour=times[0].hour, minute=times[0].minute)
                end = day.replace(hour=times[1].hour, minute=times[1].minute)
                found.append({"id": item.get_attribute("data-calitemid") or fingerprint(label),
                              "subject": match.group(1), "start": start, "end": end, "location": "",
                              "organizer": {"name": "", "email": ""}, "attendees": [],
                              "all_day": False, "cancelled": False, "show_as": "busy", "body": "",
                              "online_url": None})
        except Exception:  # noqa: BLE001 - nothing drawn yet
            pass
        return found

    def create_event(self, subject: str, start, end, attendees: List[str], body: str = "",
                     location: str = "") -> Dict[str, Any]:
        """Book an event (an invitation when there are attendees) from Outlook's own form."""
        def work(page):
            query = urlencode({"subject": subject, "startdt": start.isoformat(),
                               "enddt": end.isoformat(), "body": body or "", "location": location or "",
                               **({"to": ";".join(attendees)} if attendees else {})}, quote_via=quote)
            self.goto(page, self._cal_url("event_compose_url", query=query))
            self.settle(page)
            self._press(page, "event_send_button", "the Send/Save button")
            return {"detail": f"Booked “{subject}” {start:%a %d %b %H:%M}"
                              + (f" with {', '.join(attendees)}" if attendees else "")}

        return self.run(work, "Booking a meeting")

    def move_event(self, event_id: str, start, end) -> Dict[str, Any]:
        def work(page):
            self.goto(page, self._cal_url("event_url", id=quote(event_id, safe="")))
            self.settle(page)
            edit = self.locate(page, "event_edit_button", 5000)
            if edit is not None:
                edit.click()
            for key, value in (("event_start_date", start.strftime("%d/%m/%Y")),
                               ("event_start_time", start.strftime("%H:%M")),
                               ("event_end_date", end.strftime("%d/%m/%Y")),
                               ("event_end_time", end.strftime("%H:%M"))):
                field = self.locate(page, key, 3000)
                if field is not None:
                    field.fill(value)
                    field.press("Enter")
            self._press(page, "event_send_button", "the Send/Save button")
            return {"detail": f"Moved to {start:%a %d %b %H:%M}"}

        return self.run(work, "Moving a meeting")

    def cancel_event(self, event_id: str, message: str = "") -> Dict[str, Any]:
        def work(page):
            self.goto(page, self._cal_url("event_url", id=quote(event_id, safe="")))
            self.settle(page)
            self.require(page, "event_cancel_button", "the Cancel meeting button").click()
            if message:
                box = self.locate(page, "cancel_message", 3000)
                if box is not None:
                    self.type_text(box, message)
            self._press(page, "cancel_send_button", "the Send cancellation button")
            return {"detail": "Cancelled"}

        return self.run(work, "Cancelling a meeting")

    def _press(self, page, key: str, what: str) -> None:
        button = self.require(page, key, what)
        button.click()
        try:
            button.wait_for(state="detached", timeout=15000)
        except Exception:  # noqa: BLE001 - it may still have worked: never retried
            self.capture(page, key)
            raise MessagingError("Outlook didn't confirm it. Check your calendar before trying again.")

    # ------------------------------------------------------------ writing
    # Called for approved (or auto-send) EnterpriseActions only.
    def send(self, to: List[str], subject: str, body: str, cc: List[str] = None) -> Dict[str, Any]:
        """A new email, from the user's own mailbox."""
        if not to:
            raise MessagingError("An email needs at least one recipient.")

        def work(page):
            query = urlencode({"to": ";".join(to), "subject": subject or "", "body": body or "",
                               **({"cc": ";".join(cc)} if cc else {})}, quote_via=quote)
            self.goto(page, self._url("compose_url", query=query))
            editor = self.require(page, "compose_body", "the new message's text box")
            # Some Outlook versions ignore the body in the address; type it then.
            if (body or "").strip()[:20] not in (editor.inner_text(timeout=2000) or ""):
                self.type_text(editor, body)
            self._press_send(page)
            return {"detail": f"Email sent to {', '.join(to)}", "subject": subject}

        return self.run(work, "Sending an email")

    def reply(self, message: str, body: str, reply_all: bool = False) -> Dict[str, Any]:
        """Reply in the thread of a message Cerebro has stored (or a link)."""
        item_id = _item_id(message)

        def work(page):
            target = message if message.startswith(("http://", "https://")) \
                else self._url("read_url", id=quote(item_id, safe=""))
            self.goto(page, target)
            key = "reply_all_button" if reply_all else "reply_button"
            self.require(page, key, "the Reply button").click()
            editor = self.require(page, "compose_body", "the reply's text box")
            self.type_text(editor, body)
            self._press_send(page)
            return {"detail": "Reply sent" + (" to everyone" if reply_all else "")}

        return self.run(work, "Sending a reply")

    def _press_send(self, page) -> None:
        button = self.require(page, "send_button", "the Send button")
        button.click()
        try:
            button.wait_for(state="detached", timeout=15000)
        except Exception:  # noqa: BLE001
            try:
                if not button.is_visible():
                    return
            except Exception:  # noqa: BLE001
                return
            self.capture(page, "send")
            raise MessagingError("Outlook didn't confirm the message was sent. It may still be "
                                 "open as a draft — check Drafts in Outlook.")


# ----------------------------------------------------------- JSON shapes
def _mailbox(value: Any):
    """(name, address) from OWA's {Mailbox: {Name, EmailAddress}} and Graph's shapes."""
    if not isinstance(value, dict):
        return None, None
    box = value.get("Mailbox") or value.get("emailAddress") or value.get("EmailAddress") or value
    if not isinstance(box, dict):
        return value.get("Name"), box if isinstance(box, str) else None
    name = box.get("Name") or box.get("name")
    address = box.get("EmailAddress") or box.get("address") or box.get("Address")
    return name, address


def _id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("Id") or value.get("id")
    return value if isinstance(value, str) else None


def _item_id(message: str) -> str:
    """The Outlook item id inside one of Cerebro's message ids."""
    text = str(message or "")
    for prefix in ("outlook:", "dom:"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text


def outlook_item(node: Dict[str, Any], me: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """A message (or conversation summary) if ``node`` is shaped like one."""
    # FindConversation: a conversation row in the list.
    if "ConversationTopic" in node and isinstance(node.get("ConversationId"), dict):
        conversation = _id(node["ConversationId"])
        when = node.get("LastDeliveryTime") or node.get("LastDeliveryOrRenewTime")
        senders = node.get("UniqueSenders") or []
        item_ids = node.get("ItemIds") or node.get("GlobalItemIds") or []
        return {
            "source": "outlook", "type": "email", "summary": True,
            "id": f"conv:{conversation}:{when}", "conversationId": conversation,
            "reply_id": _id(item_ids[0]) if item_ids else None,
            "subject": node.get("ConversationTopic"), "body": node.get("Preview"),
            "sender_name": senders[0] if senders and isinstance(senders[0], str) else None,
            "timestamp": iso(when), "importance": str(node.get("Importance") or "normal").lower(),
            "read": not node.get("UnreadCount"),
        }

    item_id = _id(node.get("ItemId"))
    when = (node.get("DateTimeReceived") or node.get("ReceivedDateTime")
            or node.get("receivedDateTime") or node.get("DateTimeSent"))
    if not item_id and isinstance(node.get("Id"), str) and when:
        item_id = node["Id"]
    if not item_id or not when or node.get("IsDraft") or node.get("isDraft"):
        return None
    if not any(key in node for key in ("Subject", "subject", "Preview", "bodyPreview")):
        return None

    name, address = _mailbox(node.get("From") or node.get("Sender") or node.get("from")
                             or node.get("sender"))
    if address and me.get("email") and address.lower() == me["email"].lower():
        return None                                         # the user's own sent mail
    recipients = [_mailbox(r) for r in (node.get("ToRecipients") or node.get("toRecipients") or [])]
    copies = [_mailbox(r) for r in (node.get("CcRecipients") or node.get("ccRecipients") or [])]
    body_block = node.get("UniqueBody") or node.get("Body") or node.get("body") or {}
    body = (body_block.get("Value") or body_block.get("content") if isinstance(body_block, dict)
            else body_block) or node.get("Preview") or node.get("bodyPreview") or ""
    if "<" in body:
        body = strip_html(body)
    mine = (me.get("email") or "").lower()
    direct = bool(mine) and any((a or "").lower() == mine for _, a in recipients)
    conversation = _id(node.get("ConversationId")) or node.get("conversationId")
    return {
        "source": "outlook", "type": "email", "id": item_id, "reply_id": item_id,
        "conversationId": conversation, "subject": node.get("Subject") or node.get("subject"),
        "body": body, "sender": address, "sender_name": name,
        "to": [a for _, a in recipients if a], "cc": [a for _, a in copies if a],
        "timestamp": iso(when),
        "importance": str(node.get("Importance") or node.get("importance") or "normal").lower(),
        "read": bool(node.get("IsRead") if "IsRead" in node else node.get("isRead", True)),
        "direct": direct,
        "mentioned": bool(node.get("MentionedMe") or node.get("IsMentioned")) or mentions_me(body, me),
    }


connector: Optional[OutlookConnector] = register(OutlookConnector())

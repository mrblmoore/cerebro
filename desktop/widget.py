#!/usr/bin/env python3
"""
Cerebro desktop widget.

An always-on-top panel that shows the live support context, suggests the next
action and searches your knowledge base — without stealing focus from the tools
you are actually working in.

Built on Tkinter, which ships with Python, so there is nothing extra to install.

    python cerebro.py widget          (recommended)
    python desktop/widget.py --api http://localhost:8000
"""

import argparse
import json
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import widget_config  # noqa: E402
import win_integration as win  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# --------------------------------------------------------------------- themes
THEMES = {
    "dark": {
        "bg": "#12161d", "surface": "#1a1f28", "surface2": "#222834",
        "border": "#2b323f", "text": "#e7eaf0", "dim": "#9aa4b2", "faint": "#6b7484",
        "accent": "#7c74f5", "accent_text": "#ffffff",
        "ok": "#49c07d", "warn": "#e0a536", "err": "#f0705f",
        "title_bg": "#161b23",
    },
    "light": {
        "bg": "#ffffff", "surface": "#f7f8fa", "surface2": "#eef0f4",
        "border": "#dfe3e9", "text": "#151a21", "dim": "#5c6472", "faint": "#8b93a1",
        "accent": "#4f46e5", "accent_text": "#ffffff",
        "ok": "#0f9d58", "warn": "#b7791f", "err": "#d93025",
        "title_bg": "#f2f4f7",
    },
}

TABS = [("ask", "Ask"), ("context", "Context"), ("inbox", "Inbox"),
        ("docs", "Docs"), ("search", "Search")]

PRIORITY_ICON = {"high": "●", "medium": "●", "low": "○"}


# ----------------------------------------------------------------- api client
class ApiClient:
    """Small JSON client. Every call is made off the UI thread."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, path: str, method: str = "GET", payload=None, timeout: float = 6.0):
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url, data=data, method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else None

    def poll(self) -> dict:
        """One round of everything the widget displays."""
        snapshot = {"online": False}
        try:
            snapshot["context"] = self.request("/api/context/current", timeout=4)
            snapshot["online"] = True
        except Exception as exc:
            snapshot["error"] = _friendly_error(exc)
            return snapshot

        for key, path in (
            ("recommendations", "/api/context/recommendations"),
            ("events", "/api/events/?limit=8"),
            ("info", "/api/system/info"),
            ("inbox", "/api/enterprise/messages?limit=8"),
            ("bridge", "/api/enterprise/status"),
            ("documents", "/api/documents?limit=8"),
            ("nudges", "/api/tasks/nudges?limit=8"),
        ):
            try:
                snapshot[key] = self.request(path, timeout=4)
            except Exception:
                snapshot[key] = None
        return snapshot

    def search(self, query: str, limit: int = 6):
        encoded = urllib.parse.urlencode({"query": query, "limit": limit})
        return self.request(f"/api/knowledge/search?{encoded}", timeout=12)

    def reset_context(self):
        return self.request("/api/context/reset", method="POST")


def _shorten(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"API returned {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        return "Cerebro is not running"
    if isinstance(exc, TimeoutError):
        return "API timed out"
    return str(exc)[:80]


# --------------------------------------------------------------- the widget
class CerebroWidget:
    def __init__(self, config: dict):
        self.config = config
        self.api = ApiClient(config["api_url"])
        self.results = queue.Queue()

        self.snapshot = {}
        self.previous_context = {}
        self.online = False
        self.status_message = "connecting…"
        self.active_tab = config.get("active_tab", "context")
        self.search_results = None
        self.search_query = ""
        self.searching = False
        self.ask_reply = None
        self.expanded = False
        self._normal_size = None
        self._stop = threading.Event()
        self._drag = None
        self._menu_vars = {}

        win.enable_dpi_awareness()
        self._build_window()
        self._build_ui()
        self._start_polling()
        self.root.after(120, self._drain)

    # ------------------------------------------------------------- theming
    @property
    def theme(self) -> dict:
        return THEMES.get(self.config.get("theme", "dark"), THEMES["dark"])

    def font(self, size: int, weight: str = "normal") -> tuple:
        # Resolved once and cached: branding.load_fonts registers the bundled
        # Inter with the OS, which is not something to redo on every label.
        family = getattr(self, "_font_family", None)
        if family is None:
            try:
                import branding

                family = branding.load_fonts()
            except Exception:  # noqa: BLE001 - a missing font is not fatal
                family = "Segoe UI" if sys.platform == "win32" else (
                    "SF Pro Text" if sys.platform == "darwin" else "DejaVu Sans")
            self._font_family = family
        return (family, max(7, int(round(size * self.config.get("font_scale", 1.0)))), weight)

    # -------------------------------------------------------------- window
    def _build_window(self):
        self.root = tk.Tk()
        self.root.title("Cerebro")
        self.root.overrideredirect(True)          # frameless — we draw our own bar
        self.root.attributes("-topmost", bool(self.config["always_on_top"]))
        try:
            self.root.attributes("-alpha", float(self.config["opacity"]))
        except tk.TclError:
            pass

        width = int(self.config["width"])
        height = int(self.config["height"])
        x, y = self.config.get("x"), self.config.get("y")
        if x is None or y is None:
            # Default to the top-right corner, clear of most taskbars.
            x = self.root.winfo_screenwidth() - width - 24
            y = 64
        x, y = self._clamp(int(x), int(y), width, height)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.configure(bg=self.theme["border"])
        self.root.minsize(280, 220)

        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.bind("<Escape>", lambda _event: self.toggle_compact())
        self.root.bind("<Control-r>", lambda _event: self.refresh_now())
        self.root.bind("<Control-f>", lambda _event: self.show_tab("search"))
        self.root.bind("<Control-q>", lambda _event: self.quit())

        # Applied after the window is mapped, when a handle exists.
        self.root.after(220, lambda: (win.make_tool_window(self.root),
                                      win.round_corners(self.root)))

    def _clamp(self, x: int, y: int, width: int, height: int) -> tuple:
        """Keep the widget on screen even if the display layout changed."""
        max_x = max(0, self.root.winfo_screenwidth() - width)
        max_y = max(0, self.root.winfo_screenheight() - height)
        return max(0, min(x, max_x)), max(0, min(y, max_y))

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        theme = self.theme
        # 1px outer border via the root background showing through.
        self.shell = tk.Frame(self.root, bg=theme["bg"])
        self.shell.pack(fill="both", expand=True, padx=1, pady=1)

        self._build_titlebar()
        self._build_status_strip()

        self.body = tk.Frame(self.shell, bg=theme["bg"])
        self.body.pack(fill="both", expand=True)

        self._build_tabs()

        self.content = tk.Frame(self.body, bg=theme["bg"])
        self.content.pack(fill="both", expand=True)

        self._build_resize_grip()

        if self.config.get("compact"):
            self.config["compact"] = False
            self.toggle_compact()
        else:
            self.render()

        self._fade_in()

    def _build_titlebar(self):
        theme = self.theme
        bar = tk.Frame(self.shell, bg=theme["title_bg"], height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        self.titlebar = bar

        # The logo image if it loaded, otherwise the old emoji so the bar is
        # never empty. The reference is kept on self - Tk drops loose images.
        try:
            import branding

            self._mark_image = branding.logo_image(20)
        except Exception:  # noqa: BLE001
            self._mark_image = None
        if self._mark_image is not None:
            mark = tk.Label(bar, image=self._mark_image, bg=theme["title_bg"])
        else:
            mark = tk.Label(bar, text="\U0001F9E0", bg=theme["title_bg"],
                            fg=theme["accent"], font=self.font(11))
        mark.pack(side="left", padx=(9, 6))
        self._titlebar_mark = mark

        self.title_label = tk.Label(bar, text="Cerebro", bg=theme["title_bg"],
                                    fg=theme["text"], font=self.font(10, "bold"),
                                    anchor="w")
        self.title_label.pack(side="left", padx=(0, 8))

        for symbol, command, tip in (
            ("✕", self.quit, "Close (Ctrl+Q)"),
            ("—", self.toggle_compact, "Collapse (Esc)"),
            ("☰", self.open_menu, "Menu"),
        ):
            self._icon_button(bar, symbol, command, tip).pack(side="right", padx=(0, 4))

        for target in (bar, mark, self.title_label):
            target.bind("<Button-1>", self._drag_start)
            target.bind("<B1-Motion>", self._drag_move)
            target.bind("<ButtonRelease-1>", self._drag_end)
            target.bind("<Double-Button-1>", lambda _e: self.toggle_compact())
            target.bind("<Button-3>", lambda event: self.open_menu(event))

    def _icon_button(self, parent, symbol, command, tooltip=""):
        theme = self.theme
        button = tk.Label(parent, text=symbol, bg=theme["title_bg"], fg=theme["faint"],
                          font=self.font(10), padx=6, pady=4, cursor="hand2")
        button.bind("<Button-1>", lambda _event: command())
        button.bind("<Enter>", lambda _event: button.configure(fg=theme["text"]))
        button.bind("<Leave>", lambda _event: button.configure(fg=theme["faint"]))
        if tooltip:
            Tooltip(button, tooltip, self)
        return button

    def _build_status_strip(self):
        theme = self.theme
        strip = tk.Frame(self.shell, bg=theme["surface"], height=24)
        strip.pack(fill="x")
        strip.pack_propagate(False)
        self.status_strip = strip

        self.status_dot = tk.Label(strip, text="●", bg=theme["surface"], fg=theme["faint"],
                                   font=self.font(9))
        self.status_dot.pack(side="left", padx=(9, 5))
        self.status_text = tk.Label(strip, text="connecting…", bg=theme["surface"],
                                    fg=theme["dim"], font=self.font(8),
                                    anchor="w")
        self.status_text.pack(side="left", fill="x", expand=True)

        self.refresh_button = tk.Label(strip, text="⟳", bg=theme["surface"], fg=theme["faint"],
                                       font=self.font(9), padx=8, cursor="hand2")
        self.refresh_button.pack(side="right")
        self.refresh_button.bind("<Button-1>", lambda _event: self.refresh_now())
        Tooltip(self.refresh_button, "Refresh now (Ctrl+R)", self)

    def _build_tabs(self):
        theme = self.theme
        holder = tk.Frame(self.body, bg=theme["bg"])
        holder.pack(fill="x", padx=6, pady=(8, 0))
        self.tab_buttons = {}

        # Tabs share the row evenly rather than sitting at their natural widths,
        # so the last one is never clipped when the widget is narrow.
        for column, (key, label) in enumerate(TABS):
            button = tk.Label(holder, text=label, bg=theme["bg"], fg=theme["dim"],
                              font=self.font(9, "bold"), pady=5, cursor="hand2")
            button.grid(row=0, column=column, sticky="ew", padx=1)
            holder.grid_columnconfigure(column, weight=1)
            button.bind("<Button-1>", lambda _event, k=key: self.show_tab(k))
            self.tab_buttons[key] = button

    def _build_resize_grip(self):
        theme = self.theme
        grip = tk.Label(self.shell, text="◢", bg=theme["bg"], fg=theme["faint"],
                        font=self.font(8), cursor="bottom_right_corner")
        grip.place(relx=1.0, rely=1.0, anchor="se")
        grip.bind("<Button-1>", self._resize_start)
        grip.bind("<B1-Motion>", self._resize_move)
        grip.bind("<ButtonRelease-1>", lambda _event: self._persist_geometry())
        self.grip = grip

    # -------------------------------------------------------- drag & resize
    def _drag_start(self, event):
        self._drag = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())

    def _drag_move(self, event):
        if not self._drag:
            return
        offset_x, offset_y = self._drag
        self.root.geometry(f"+{event.x_root - offset_x}+{event.y_root - offset_y}")

    def _drag_end(self, _event):
        self._drag = None
        if self.config.get("snap_to_edges"):
            self._snap()
        self._persist_geometry()

    def _snap(self, margin: int = 22):
        """Pull the widget flush to a screen edge when released nearby."""
        x, y = self.root.winfo_x(), self.root.winfo_y()
        width, height = self.root.winfo_width(), self.root.winfo_height()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()

        if x < margin:
            x = 0
        elif screen_w - (x + width) < margin:
            x = screen_w - width
        if y < margin:
            y = 0
        elif screen_h - (y + height) < margin:
            y = screen_h - height

        self.root.geometry(f"+{x}+{y}")

    def _resize_start(self, event):
        self._resize_origin = (event.x_root, event.y_root,
                               self.root.winfo_width(), self.root.winfo_height())

    def _resize_move(self, event):
        start_x, start_y, start_w, start_h = self._resize_origin
        width = max(280, start_w + (event.x_root - start_x))
        height = max(220, start_h + (event.y_root - start_y))
        self.root.geometry(f"{width}x{height}")

    def _persist_geometry(self):
        self.config["x"] = self.root.winfo_x()
        self.config["y"] = self.root.winfo_y()
        if not self.config.get("compact"):
            self.config["width"] = self.root.winfo_width()
            self.config["height"] = self.root.winfo_height()
        widget_config.save(self.config)

    # ----------------------------------------------------------- rendering
    def show_tab(self, key: str):
        self.active_tab = key
        self.config["active_tab"] = key
        if self.config.get("compact"):
            self.toggle_compact()
        # Leaving the Ask tab collapses any expansion it caused.
        if key != "ask" and self.expanded:
            self.restore_size()
        self.render()

    def render(self):
        theme = self.theme
        for key, button in self.tab_buttons.items():
            selected = key == self.active_tab
            button.configure(
                bg=theme["surface"] if selected else theme["bg"],
                fg=theme["text"] if selected else theme["dim"],
            )

        for child in self.content.winfo_children():
            child.destroy()

        renderer = {
            "ask": self._render_ask,
            "context": self._render_context,
            "inbox": self._render_inbox,
            "docs": self._render_docs,
            "search": self._render_search,
        }[self.active_tab]
        renderer(ScrollArea(self.content, self))

    def _render_context(self, area):
        context = self.snapshot.get("context") or {}
        theme = self.theme

        if not self.online:
            self._render_offline(area)
            return

        case = context.get("crm_case")
        self._row(area.inner, "Case", case or "No case open",
                  accent=bool(case), sub=context.get("crm_system") if case else None)
        self._row(area.inner, "Customer", context.get("customer") or "—")

        badges = []
        if context.get("call_active"):
            badges.append(("Call active", theme["ok"]))
        if context.get("remote_session_active"):
            host = context.get("remote_host")
            badges.append((f"Remote{f' · {host}' if host else ''}", theme["warn"]))
        if badges:
            strip = tk.Frame(area.inner, bg=theme["bg"])
            strip.pack(fill="x", padx=12, pady=(4, 8))
            for label, colour in badges:
                self._badge(strip, label, colour).pack(side="left", padx=(0, 6))

        application = context.get("active_application")
        if application:
            self._row(area.inner, "Application", application,
                      sub=(context.get("window_title") or "")[:70] or None)

        events = self.snapshot.get("events") or []
        if events:
            self._heading(area.inner, "Recent activity")
            for event in events[:5]:
                data = event.get("data") or {}
                detail = data.get("case_id") or data.get("title") or data.get("application") \
                    or event.get("source") or ""
                self._line(area.inner,
                           event["event_type"].replace("_", " ").title(),
                           _shorten(str(detail), 24))

    def _render_assist(self, area):
        if not self.online:
            self._render_offline(area)
            return

        theme = self.theme
        payload = self.snapshot.get("recommendations") or {}
        recommendations = payload.get("recommendations") or []

        if not recommendations:
            self._empty(area.inner, "🤝", "Nothing to suggest yet.\nOpen a case to get started.")
            return

        for recommendation in recommendations:
            priority = recommendation.get("priority", "medium")
            colour = {"high": theme["accent"], "medium": theme["dim"],
                      "low": theme["faint"]}.get(priority, theme["dim"])

            card = tk.Frame(area.inner, bg=theme["surface"], highlightthickness=0)
            card.pack(fill="x", padx=10, pady=(0, 7))

            header = tk.Frame(card, bg=theme["surface"])
            header.pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(header, text=PRIORITY_ICON.get(priority, "●"), bg=theme["surface"],
                     fg=colour, font=self.font(8)).pack(side="left", padx=(0, 6))
            tk.Label(header, text=priority.upper(), bg=theme["surface"], fg=colour,
                     font=self.font(7, "bold")).pack(side="left")

            tk.Label(card, text=recommendation.get("message", ""), bg=theme["surface"],
                     fg=theme["text"], font=self.font(9), justify="left", anchor="w",
                     wraplength=self.root.winfo_width() - 60).pack(
                fill="x", padx=10, pady=(3, 9))

            action = recommendation.get("action")
            kind = recommendation.get("type")
            if kind == "retrieve_docs" and action:
                self._link(card, f"Search “{action[:28]}”",
                           lambda a=action: self._search_for(a)).pack(
                    anchor="w", padx=10, pady=(0, 9))
            elif kind == "configure_ai":
                self._link(card, "Open settings",
                           lambda: self.open_url("/settings")).pack(
                    anchor="w", padx=10, pady=(0, 9))

    def _render_ask(self, area):
        """Type an instruction; see Cerebro's reply and its open nudges."""
        theme = self.theme

        box = tk.Frame(area.inner, bg=theme["bg"])
        box.pack(fill="x", padx=10, pady=(2, 6))
        self.ask_entry = tk.Entry(
            box, bg=theme["surface2"], fg=theme["text"], font=self.font(9),
            insertbackground=theme["text"], relief="flat", highlightthickness=1,
            highlightbackground=theme["border"], highlightcolor=theme["accent"])
        self.ask_entry.pack(fill="x", ipady=6, ipadx=6)
        self.ask_entry.bind("<Return>", lambda _e: self._send_instruction())
        placeholder = ("Tell me what to do — “remind me…”, "
                       "“keep this doc updated daily under my name”…")
        tk.Label(area.inner, text=placeholder, bg=theme["bg"], fg=theme["faint"],
                 font=self.font(8), anchor="w", justify="left",
                 wraplength=self.root.winfo_width() - 30).pack(fill="x", padx=12, pady=(0, 8))

        if getattr(self, "ask_reply", None):
            card = tk.Frame(area.inner, bg=theme["accent_soft"] if "accent_soft" in theme
                            else theme["surface"])
            card.pack(fill="x", padx=10, pady=(0, 8))
            tk.Label(card, text=self.ask_reply, bg=card["bg"], fg=theme["text"],
                     font=self.font(9), justify="left", anchor="w",
                     wraplength=self.root.winfo_width() - 50).pack(
                fill="x", padx=10, pady=8)

        nudges = (self.snapshot.get("nudges") or {}).get("nudges") or []
        if nudges:
            self._heading(area.inner, "Nudges")
            for nudge in nudges:
                self._nudge_card(area.inner, nudge)
        elif not getattr(self, "ask_reply", None):
            self._empty(area.inner, "💬",
                        "Ask me to do something,\nor I'll raise things here\nwhen they need you.")

    def _nudge_card(self, parent, nudge):
        theme = self.theme
        colour = {"high": theme["err"], "medium": theme["warn"]}.get(
            nudge.get("priority"), theme["faint"])
        card = tk.Frame(parent, bg=theme["surface"])
        card.pack(fill="x", padx=10, pady=(0, 6))

        header = tk.Frame(card, bg=theme["surface"])
        header.pack(fill="x", padx=10, pady=(8, 0))
        tk.Label(header, text="●", bg=theme["surface"], fg=colour,
                 font=self.font(8)).pack(side="left", padx=(0, 6))
        tk.Label(header, text=nudge.get("title", ""), bg=theme["surface"],
                 fg=theme["text"], font=self.font(9, "bold"), anchor="w").pack(side="left")

        tk.Label(card, text=nudge.get("body", ""), bg=theme["surface"], fg=theme["dim"],
                 font=self.font(9), justify="left", anchor="w",
                 wraplength=self.root.winfo_width() - 60).pack(fill="x", padx=10, pady=(3, 6))

        buttons = tk.Frame(card, bg=theme["surface"])
        buttons.pack(fill="x", padx=10, pady=(0, 9))
        action = nudge.get("action")
        if action:
            self._link(buttons, "Yes, do it",
                       lambda n=nudge: self._act_nudge(n)).pack(side="left", padx=(0, 12))
        self._link(buttons, "Dismiss",
                   lambda n=nudge: self._dismiss_nudge(n)).pack(side="left")

    def _send_instruction(self):
        instruction = self.ask_entry.get().strip()
        if len(instruction) < 3:
            return
        self.ask_reply = "Working on it…"
        self.render()

        def worker():
            try:
                result = self.api.request("/api/tasks/instruct", method="POST",
                                          payload={"instruction": instruction})
                self.results.put(("ask", result.get("message", "Done.")))
            except Exception as exc:
                self.results.put(("ask", f"Couldn't do that: {_friendly_error(exc)}"))

        threading.Thread(target=worker, daemon=True).start()

    def _act_nudge(self, nudge):
        def worker():
            try:
                self.api.request(f"/api/tasks/nudges/{nudge['id']}/act", method="POST")
                self.results.put(("status", "On it."))
            except Exception as exc:
                self.results.put(("status", _friendly_error(exc)))
            self.results.put(("poll", self.api.poll()))
        threading.Thread(target=worker, daemon=True).start()

    def _dismiss_nudge(self, nudge):
        def worker():
            try:
                self.api.request(f"/api/tasks/nudges/{nudge['id']}/dismiss", method="POST")
            except Exception:
                pass
            self.results.put(("poll", self.api.poll()))
        threading.Thread(target=worker, daemon=True).start()

    def _render_inbox(self, area):
        """Outlook and Teams messages, most urgent first."""
        if not self.online:
            self._render_offline(area)
            return

        theme = self.theme
        bridge = self.snapshot.get("bridge") or {}
        if not bridge.get("enabled"):
            self._empty(area.inner, "📨",
                        "The Outlook and Teams bridge\nis switched off.")
            self._link(area.inner, "Open settings",
                       lambda: self.open_url("/settings")).pack(pady=(6, 0))
            return

        messages = (self.snapshot.get("inbox") or {}).get("messages") or []
        if not messages:
            self._empty(area.inner, "📭", "Nothing waiting.\nInbox is clear.")
            return

        rank = {"high": 0, "medium": 1, "normal": 2}
        for message in sorted(messages, key=lambda m: rank.get(m.get("urgency"), 3)):
            urgency = message.get("urgency", "normal")
            colour = {"high": theme["err"], "medium": theme["warn"]}.get(urgency, theme["faint"])

            card = tk.Frame(area.inner, bg=theme["surface"])
            card.pack(fill="x", padx=10, pady=(0, 6))

            header = tk.Frame(card, bg=theme["surface"])
            header.pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(header, text="✉" if message.get("source") == "outlook" else "💬",
                     bg=theme["surface"], fg=theme["dim"], font=self.font(8)).pack(side="left")
            tk.Label(header, text=_shorten(message.get("sender_name")
                                           or message.get("sender") or "unknown", 24),
                     bg=theme["surface"], fg=theme["dim"],
                     font=self.font(8)).pack(side="left", padx=(5, 0))
            if urgency != "normal":
                tk.Label(header, text=urgency.upper(), bg=theme["surface"], fg=colour,
                         font=self.font(7, "bold")).pack(side="right")

            tk.Label(card, text=message.get("subject") or message.get("preview") or "",
                     bg=theme["surface"], fg=theme["text"], font=self.font(9),
                     justify="left", anchor="w",
                     wraplength=self.root.winfo_width() - 60).pack(
                fill="x", padx=10, pady=(2, 0))

            footer = []
            if message.get("case_id"):
                footer.append(f"case {message['case_id']}")
            if message.get("chat_or_channel"):
                footer.append(message["chat_or_channel"])
            if message.get("urgency_reason"):
                footer.append(message["urgency_reason"])
            if footer:
                tk.Label(card, text=_shorten(" · ".join(footer), 46), bg=theme["surface"],
                         fg=theme["faint"], font=self.font(7),
                         anchor="w").pack(fill="x", padx=10)

            tk.Frame(card, bg=theme["surface"], height=8).pack()

    def show_media(self, image_path: str, caption: str = "") -> None:
        """
        Pull a screenshot into the widget itself, expanding to fit it.

        This is the "if it can, it pulls the reference up in the widget" piece:
        rather than opening another window, the panel grows, shows the image
        inline, and shrinks back when closed.
        """
        try:
            from tkinter import PhotoImage

            image = PhotoImage(file=image_path)
        except Exception:
            webbrowser.open(image_path)
            return

        self.expand_for(image.height() + 60)
        theme = self.theme
        overlay = tk.Frame(self.content, bg=theme["bg"])
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)

        bar = tk.Frame(overlay, bg=theme["title_bg"])
        bar.pack(fill="x")
        tk.Label(bar, text=caption or "Reference", bg=theme["title_bg"],
                 fg=theme["dim"], font=self.font(9, "bold")).pack(side="left", padx=10, pady=6)

        def close():
            overlay.destroy()
            self.restore_size()
        tk.Label(bar, text="✕", bg=theme["title_bg"], fg=theme["faint"],
                 font=self.font(10), cursor="hand2", padx=10).pack(side="right")
        bar.winfo_children()[-1].bind("<Button-1>", lambda _e: close())

        label = tk.Label(overlay, image=image, bg=theme["bg"])
        label.image = image  # keep a reference so it is not garbage-collected
        label.pack(pady=10)

    def _render_docs(self, area):
        """Documents Cerebro is currently reading."""
        if not self.online:
            self._render_offline(area)
            return

        theme = self.theme
        documents = (self.snapshot.get("documents") or {}).get("documents") or []
        if not documents:
            self._empty(area.inner, "📄",
                        "No documents in play.\nOpen a Word or Excel file\nand it appears here.")
            return

        icons = {"docx": "📝", "xlsx": "📊", "pptx": "📽", "pdf": "📕",
                 "csv": "📈", "text": "📃"}

        for document in documents:
            card = tk.Frame(area.inner, bg=theme["surface"])
            card.pack(fill="x", padx=10, pady=(0, 6))

            header = tk.Frame(card, bg=theme["surface"])
            header.pack(fill="x", padx=10, pady=(8, 0))
            tk.Label(header, text=icons.get(document.get("kind"), "📄"),
                     bg=theme["surface"], fg=theme["dim"],
                     font=self.font(9)).pack(side="left", padx=(0, 6))
            tk.Label(header, text=_shorten(document.get("name", ""), 30),
                     bg=theme["surface"], fg=theme["text"],
                     font=self.font(9, "bold"), anchor="w").pack(side="left", fill="x", expand=True)

            meta = [document.get("discovered_by") or ""]
            if document.get("case_id"):
                meta.append(f"case {document['case_id']}")
            if document.get("indexed"):
                meta.append("indexed")
            tk.Label(card, text=_shorten(" · ".join(m for m in meta if m), 44),
                     bg=theme["surface"], fg=theme["faint"], font=self.font(7),
                     anchor="w").pack(fill="x", padx=10)

            if document.get("read_error"):
                tk.Label(card, text=_shorten(document["read_error"], 60),
                         bg=theme["surface"], fg=theme["warn"], font=self.font(7),
                         justify="left", anchor="w",
                         wraplength=self.root.winfo_width() - 60).pack(fill="x", padx=10)
            elif document.get("summary"):
                tk.Label(card, text=_shorten(document["summary"], 140),
                         bg=theme["surface"], fg=theme["dim"], font=self.font(8),
                         justify="left", anchor="w",
                         wraplength=self.root.winfo_width() - 60).pack(fill="x", padx=10, pady=(3, 0))

            tk.Frame(card, bg=theme["surface"], height=8).pack()

    def _render_search(self, area):
        theme = self.theme
        box = tk.Frame(area.inner, bg=theme["bg"])
        box.pack(fill="x", padx=10, pady=(2, 8))

        self.search_entry = tk.Entry(
            box, bg=theme["surface2"], fg=theme["text"], font=self.font(9),
            insertbackground=theme["text"], relief="flat", highlightthickness=1,
            highlightbackground=theme["border"], highlightcolor=theme["accent"],
        )
        self.search_entry.pack(fill="x", ipady=6, ipadx=6)
        self.search_entry.bind("<Return>", lambda _event: self._run_search())
        # render() rebuilds this widget, so restore the query the user typed.
        if self.search_query:
            self.search_entry.insert(0, self.search_query)
        self.search_entry.focus_set()

        tk.Label(area.inner, text="Press Enter to search your indexed documents",
                 bg=theme["bg"], fg=theme["faint"], font=self.font(8),
                 anchor="w").pack(fill="x", padx=12, pady=(0, 8))

        if self.searching:
            self._empty(area.inner, "⏳", "Searching…")
            return

        if self.search_results is None:
            self._empty(area.inner, "🔎",
                        "Search runbooks, KB articles\nand anything else you indexed.")
            return

        results = self.search_results.get("results", [])
        if not results:
            self._empty(area.inner, "🗂️",
                        "No matches.\nIndex documents from the dashboard.")
            return

        for result in results:
            card = tk.Frame(area.inner, bg=theme["surface"])
            card.pack(fill="x", padx=10, pady=(0, 6))

            title = tk.Label(card, text=result.get("title", "Untitled"), bg=theme["surface"],
                             fg=theme["accent"] if result.get("url") else theme["text"],
                             font=self.font(9, "bold"), justify="left", anchor="w",
                             wraplength=self.root.winfo_width() - 60,
                             cursor="hand2" if result.get("url") else "")
            title.pack(fill="x", padx=10, pady=(8, 1))
            if result.get("url"):
                title.bind("<Button-1>",
                           lambda _event, u=result["url"]: webbrowser.open(u))

            score = result.get("score")
            meta = result.get("source") or "unknown source"
            if score is not None:
                meta += f" · {round(score * 100)}% match"
            tk.Label(card, text=meta, bg=theme["surface"], fg=theme["faint"],
                     font=self.font(7)).pack(anchor="w", padx=10)
            tk.Label(card, text=(result.get("excerpt") or "")[:180], bg=theme["surface"],
                     fg=theme["dim"], font=self.font(8), justify="left", anchor="w",
                     wraplength=self.root.winfo_width() - 60).pack(
                fill="x", padx=10, pady=(3, 9))

    def _render_offline(self, area):
        theme = self.theme
        self._empty(area.inner, "🔌",
                    f"{self.status_message}.\n\nCerebro's API is not answering at\n"
                    f"{self.api.base_url}")
        actions = tk.Frame(area.inner, bg=theme["bg"])
        actions.pack(pady=(4, 0))
        self._link(actions, "Retry now", self.refresh_now).pack(side="left", padx=6)
        self._link(actions, "Change API URL", self.open_settings).pack(side="left", padx=6)

    # ------------------------------------------------------- small builders
    def _row(self, parent, label, value, sub=None, accent=False):
        theme = self.theme
        row = tk.Frame(parent, bg=theme["bg"])
        row.pack(fill="x", padx=12, pady=(6, 0))
        tk.Label(row, text=label.upper(), bg=theme["bg"], fg=theme["faint"],
                 font=self.font(7, "bold"), anchor="w").pack(fill="x")
        tk.Label(row, text=value, bg=theme["bg"],
                 fg=theme["accent"] if accent else theme["text"],
                 font=self.font(11 if accent else 10, "bold" if accent else "normal"),
                 anchor="w", justify="left",
                 wraplength=self.root.winfo_width() - 40).pack(fill="x")
        if sub:
            tk.Label(row, text=sub, bg=theme["bg"], fg=theme["faint"],
                     font=self.font(8), anchor="w").pack(fill="x")

    def _badge(self, parent, text, colour):
        return tk.Label(parent, text=f" {text} ", bg=colour, fg="#0b0e13",
                        font=self.font(7, "bold"), padx=4, pady=2)

    def _heading(self, parent, text):
        theme = self.theme
        tk.Label(parent, text=text.upper(), bg=theme["bg"], fg=theme["faint"],
                 font=self.font(7, "bold"), anchor="w").pack(
            fill="x", padx=12, pady=(14, 4))

    def _line(self, parent, title, detail):
        theme = self.theme
        row = tk.Frame(parent, bg=theme["bg"])
        row.pack(fill="x", padx=12, pady=1)
        tk.Label(row, text=title, bg=theme["bg"], fg=theme["dim"],
                 font=self.font(8), anchor="w").pack(side="left", padx=(0, 10))
        tk.Label(row, text=detail, bg=theme["bg"], fg=theme["faint"],
                 font=self.font(8), anchor="e").pack(side="right")

    def _empty(self, parent, icon, message):
        theme = self.theme
        holder = tk.Frame(parent, bg=theme["bg"])
        holder.pack(fill="both", expand=True, pady=(26, 10))
        tk.Label(holder, text=icon, bg=theme["bg"], fg=theme["faint"],
                 font=self.font(20)).pack()
        tk.Label(holder, text=message, bg=theme["bg"], fg=theme["faint"],
                 font=self.font(9), justify="center").pack(pady=(6, 0))

    def _link(self, parent, text, command):
        theme = self.theme
        link = tk.Label(parent, text=text, bg=parent["bg"], fg=theme["accent"],
                        font=self.font(8, "bold"), cursor="hand2")
        link.bind("<Button-1>", lambda _event: command())
        return link

    # ---------------------------------------------------------- behaviours
    def toggle_compact(self):
        theme = self.theme
        compact = not self.config.get("compact", False)
        self.config["compact"] = compact

        if compact:
            self.config["height"] = self.root.winfo_height()
            self.config["width"] = self.root.winfo_width()
            self.body.pack_forget()
            self.status_strip.pack_forget()
            self.grip.place_forget()
            self.root.geometry(f"{self.root.winfo_width()}x36")
            self.title_label.configure(text=self._compact_title())
        else:
            self.status_strip.pack(fill="x", after=self.titlebar)
            self.body.pack(fill="both", expand=True)
            self.grip.place(relx=1.0, rely=1.0, anchor="se")
            self.root.geometry(f"{self.config['width']}x{self.config['height']}")
            self.title_label.configure(text="Cerebro")
            self.render()

        widget_config.save(self.config)

    def _compact_title(self) -> str:
        """In compact mode the title bar carries the headline state."""
        context = self.snapshot.get("context") or {}
        if not self.online:
            return "Cerebro · offline"
        parts = []
        if context.get("crm_case"):
            parts.append(f"#{context['crm_case']}")
        if context.get("customer"):
            parts.append(context["customer"][:18])
        if context.get("call_active"):
            parts.append("☎")
        if context.get("remote_session_active"):
            parts.append("🔗")

        urgent = sum(1 for m in ((self.snapshot.get("inbox") or {}).get("messages") or [])
                     if m.get("urgency") == "high" and not m.get("handled"))
        if urgent:
            parts.append(f"✉{urgent}")

        nudges = len((self.snapshot.get("nudges") or {}).get("nudges") or [])
        if nudges:
            parts.append(f"💬{nudges}")

        return _shorten(" · ".join(parts), 30) if parts else "Cerebro"

    def open_menu(self, event=None):
        theme = self.theme
        menu = tk.Menu(self.root, tearoff=0, bg=theme["surface"], fg=theme["text"],
                       activebackground=theme["accent"], activeforeground=theme["accent_text"],
                       bd=0, font=self.font(9))

        menu.add_command(label="Refresh now", command=self.refresh_now, accelerator="Ctrl+R")
        menu.add_command(label="Reset context", command=self.reset_context)
        menu.add_separator()

        menu.add_command(label="Open dashboard", command=lambda: self.open_url("/"))
        menu.add_command(label="Open settings", command=lambda: self.open_url("/settings"))
        menu.add_separator()

        view = tk.Menu(menu, tearoff=0, bg=theme["surface"], fg=theme["text"],
                       activebackground=theme["accent"], activeforeground=theme["accent_text"],
                       font=self.font(9))
        view.add_command(
            label=f"Theme: {'Dark' if self.config['theme'] == 'dark' else 'Light'}",
            command=self.toggle_theme)
        view.add_command(label="Collapse", command=self.toggle_compact, accelerator="Esc")
        view.add_separator()
        for label, value in (("Smaller text", 0.9), ("Normal text", 1.0), ("Larger text", 1.2)):
            view.add_command(label=label, command=lambda v=value: self.set_font_scale(v))
        view.add_separator()
        for label, value in (("Opacity 100%", 1.0), ("Opacity 90%", 0.9), ("Opacity 75%", 0.75)):
            view.add_command(label=label, command=lambda v=value: self.set_opacity(v))
        menu.add_cascade(label="Appearance", menu=view)

        position = tk.Menu(menu, tearoff=0, bg=theme["surface"], fg=theme["text"],
                           activebackground=theme["accent"], activeforeground=theme["accent_text"],
                           font=self.font(9))
        for label, corner in (("Top left", "tl"), ("Top right", "tr"),
                              ("Bottom left", "bl"), ("Bottom right", "br")):
            position.add_command(label=label, command=lambda c=corner: self.dock(c))
        menu.add_cascade(label="Move to", menu=position)

        menu.add_separator()
        # Tk holds only a weak link to a checkbutton's variable, so these must be
        # kept alive on the instance or the ticks vanish as soon as they are GC'd.
        self._menu_vars = {
            "on_top": tk.IntVar(value=int(self.config["always_on_top"])),
            "snap": tk.IntVar(value=int(self.config["snap_to_edges"])),
            "startup": tk.IntVar(value=int(win.startup_enabled())),
        }
        menu.add_checkbutton(label="Always on top", command=self.toggle_on_top,
                             onvalue=1, offvalue=0, variable=self._menu_vars["on_top"])
        menu.add_checkbutton(label="Snap to screen edges", command=self.toggle_snap,
                             onvalue=1, offvalue=0, variable=self._menu_vars["snap"])
        if win.IS_WINDOWS:
            menu.add_checkbutton(label="Start with Windows", command=self.toggle_startup,
                                 onvalue=1, offvalue=0, variable=self._menu_vars["startup"])

        menu.add_separator()
        menu.add_command(label="Widget preferences…", command=self.open_settings)
        menu.add_command(label="Quit", command=self.quit, accelerator="Ctrl+Q")

        try:
            if event is not None and getattr(event, "x_root", None):
                menu.tk_popup(event.x_root, event.y_root)
            else:
                menu.tk_popup(self.root.winfo_rootx() + self.root.winfo_width() - 30,
                              self.root.winfo_rooty() + 32)
        finally:
            menu.grab_release()

    def dock(self, corner: str, margin: int = 16):
        width, height = self.root.winfo_width(), self.root.winfo_height()
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = margin if corner[1] == "l" else screen_w - width - margin
        y = margin if corner[0] == "t" else screen_h - height - margin - 48
        self.root.geometry(f"+{max(0, x)}+{max(0, y)}")
        self._persist_geometry()

    def toggle_theme(self):
        self.config["theme"] = "light" if self.config["theme"] == "dark" else "dark"
        widget_config.save(self.config)
        self.rebuild()

    def set_font_scale(self, value: float):
        self.config["font_scale"] = value
        widget_config.save(self.config)
        self.rebuild()

    def set_opacity(self, value: float):
        self.config["opacity"] = value
        try:
            self.root.attributes("-alpha", value)
        except tk.TclError:
            pass
        widget_config.save(self.config)

    def toggle_on_top(self):
        self.config["always_on_top"] = not self.config["always_on_top"]
        self.root.attributes("-topmost", bool(self.config["always_on_top"]))
        widget_config.save(self.config)

    def toggle_snap(self):
        self.config["snap_to_edges"] = not self.config["snap_to_edges"]
        widget_config.save(self.config)

    def toggle_startup(self):
        wanted = not win.startup_enabled()
        if not win.set_startup(wanted, PROJECT_ROOT):
            self._flash_status("Could not change the startup setting", error=True)
        else:
            self._flash_status("Starts with Windows" if wanted else "Removed from startup")
        # Reflect what actually happened, not what was requested.
        if "startup" in self._menu_vars:
            self._menu_vars["startup"].set(int(win.startup_enabled()))

    def rebuild(self):
        """Recreate the interface after a theme or font change."""
        compact = self.config.get("compact", False)
        for child in self.shell.winfo_children():
            child.destroy()
        self.shell.destroy()
        self.root.configure(bg=self.theme["border"])
        self.config["compact"] = False
        self._build_ui()
        self._paint_status()
        if compact:
            self.toggle_compact()

    def open_url(self, path: str):
        webbrowser.open(f"{self.api.base_url}{path}")

    def _search_for(self, query: str):
        self.search_query = query
        self.show_tab("search")
        self._run_search()

    def _run_search(self):
        query = self.search_entry.get().strip()
        if len(query) < 2:
            return
        self.search_query = query
        self.searching = True
        self.render()

        def worker():
            try:
                results = self.api.search(query)
            except Exception as exc:
                results = {"results": [], "error": _friendly_error(exc)}
            self.results.put(("search", results))

        threading.Thread(target=worker, daemon=True).start()

    def reset_context(self):
        def worker():
            try:
                self.api.reset_context()
                self.results.put(("status", "Context cleared"))
            except Exception as exc:
                self.results.put(("status", f"Could not reset: {_friendly_error(exc)}"))
            self.results.put(("poll", self.api.poll()))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_now(self):
        threading.Thread(target=lambda: self.results.put(("poll", self.api.poll())),
                         daemon=True).start()

    # -------------------------------------------------------------- expand
    def expand_for(self, extra_height: int = 240) -> None:
        """
        Grow the widget to show a screenshot or a long answer it is referencing.

        The current size is remembered so the widget can shrink back to its
        normal footprint once the reference is dismissed — the "swells to fit the
        media, then collapses onto its small self" behaviour.
        """
        if self.expanded or self.config.get("compact"):
            return
        self._normal_size = (self.root.winfo_width(), self.root.winfo_height())
        width = max(self.root.winfo_width(), 420)
        height = min(self.root.winfo_screenheight() - 80,
                     self.root.winfo_height() + extra_height)
        self.expanded = True
        self._animate_to(width, height)

    def restore_size(self) -> None:
        """Shrink back to the size the widget had before it expanded."""
        if not self.expanded or not self._normal_size:
            self.expanded = False
            return
        width, height = self._normal_size
        self.expanded = False
        self._animate_to(width, height)
        self._normal_size = None

    # --------------------------------------------------------------- motion
    def _fade_in(self, duration_ms: int = 220) -> None:
        """
        Ease the window up to its configured opacity on launch.

        The widget is always-on-top and frameless, so without this it simply
        appears over whatever the user is doing. A short fade reads as arriving
        rather than interrupting.
        """
        try:
            target = float(self.config.get("opacity", 1.0))
        except (TypeError, ValueError):
            target = 1.0

        steps = 12
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            return          # some window managers do not support alpha at all

        def frame(step: int) -> None:
            if not self.root.winfo_exists():
                return
            ratio = min(1.0, step / steps)
            try:
                # Ease-out, so it settles rather than stopping dead.
                self.root.attributes("-alpha", target * (1 - (1 - ratio) ** 3))
            except tk.TclError:
                return
            if ratio < 1.0:
                self.root.after(max(8, duration_ms // steps), lambda: frame(step + 1))

        frame(0)

    def _pulse_dot(self, label, colour: str, cycles: int = 3) -> None:
        """
        Breathe a status dot between its colour and the background.

        Used when something changes on its own - a nudge arriving, a case being
        detected - so a passive glance catches it without a sound or a popup.
        """
        theme = self.theme

        def blend(ratio: float) -> str:
            start, end = theme["surface"], colour
            return "#" + "".join(
                f"{round(int(start[i:i+2], 16) * (1 - ratio) + int(end[i:i+2], 16) * ratio):02x}"
                for i in (1, 3, 5))

        frames = [blend(value / 10) for value in (10, 7, 4, 7, 10)]

        def step(index: int) -> None:
            if index >= len(frames) * cycles or not label.winfo_exists():
                if label.winfo_exists():
                    label.configure(fg=colour)
                return
            label.configure(fg=frames[index % len(frames)])
            self.root.after(110, lambda: step(index + 1))

        step(0)

    def _animate_to(self, width: int, height: int, steps: int = 8) -> None:
        """A short size animation so the change reads as the panel breathing."""
        start_w, start_h = self.root.winfo_width(), self.root.winfo_height()
        dw = (width - start_w) / steps
        dh = (height - start_h) / steps

        def frame(step: int):
            if step > steps:
                self.root.geometry(f"{width}x{height}")
                self._persist_geometry()
                return
            self.root.geometry(
                f"{int(start_w + dw * step)}x{int(start_h + dh * step)}")
            self.root.after(16, lambda: frame(step + 1))

        frame(1)

    # ------------------------------------------------------------ settings
    def open_settings(self):
        SettingsDialog(self)

    def apply_settings(self, values: dict):
        restart_poll = values["api_url"] != self.config["api_url"]
        self.config.update(values)
        widget_config.save(self.config)
        self.api = ApiClient(self.config["api_url"])
        try:
            self.root.attributes("-alpha", float(self.config["opacity"]))
        except tk.TclError:
            pass
        self.root.attributes("-topmost", bool(self.config["always_on_top"]))
        self.rebuild()
        if restart_poll:
            self.refresh_now()

    # ------------------------------------------------------------ polling
    def _start_polling(self):
        def loop():
            while not self._stop.is_set():
                self.results.put(("poll", self.api.poll()))
                self._stop.wait(max(2, int(self.config.get("poll_seconds", 4))))

        threading.Thread(target=loop, daemon=True).start()

    def _drain(self):
        """Apply queued background results on the UI thread."""
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "poll":
                    self._apply_snapshot(payload)
                elif kind == "search":
                    self.searching = False
                    self.search_results = payload
                    if self.active_tab == "search":
                        self.render()
                elif kind == "ask":
                    self.ask_reply = payload
                    # A substantial reply is worth more room; a short "on it" is not.
                    if len(payload) > 160:
                        self.expand_for(180)
                    if self.active_tab == "ask":
                        self.render()
                elif kind == "status":
                    self._flash_status(payload)
        except queue.Empty:
            pass
        except tk.TclError:
            return  # window is going away
        self.root.after(200, self._drain)

    def _apply_snapshot(self, snapshot: dict):
        was_online = self.online
        self.online = snapshot.get("online", False)
        previous = self.snapshot.get("context") or {}
        self.snapshot = snapshot

        if self.online:
            context = snapshot.get("context") or {}
            self.status_message = self._describe(context)
            if self.config.get("notify_on_change") and was_online:
                self._notify_changes(previous, context)
        else:
            self.status_message = snapshot.get("error", "offline")

        self._paint_status()

        if self.config.get("compact"):
            self.title_label.configure(text=self._compact_title())
        elif self.active_tab in ("ask", "context", "inbox", "docs"):
            self.render()

    @staticmethod
    def _describe(context: dict) -> str:
        if context.get("call_active"):
            return "on a call"
        if context.get("remote_session_active"):
            return "remote session active"
        if context.get("crm_case"):
            return f"case {context['crm_case']}"
        return "connected · idle"

    def _notify_changes(self, previous: dict, current: dict):
        """Flash the widget when something meaningful changed."""
        interesting = ("crm_case", "call_active", "remote_session_active")
        if any(previous.get(key) != current.get(key) for key in interesting):
            win.flash(self.root)

    def _paint_status(self):
        theme = self.theme
        colour = theme["ok"] if self.online else theme["err"]
        try:
            self.status_dot.configure(fg=colour)
            self.status_text.configure(text=self.status_message)
        except tk.TclError:
            pass

    def _flash_status(self, message: str, error: bool = False):
        try:
            self.status_text.configure(text=message,
                                       fg=self.theme["err"] if error else self.theme["ok"])
            self.root.after(2600, lambda: (
                self.status_text.configure(text=self.status_message, fg=self.theme["dim"])
            ))
        except tk.TclError:
            pass

    # --------------------------------------------------------------- exit
    def quit(self):
        self._stop.set()
        self._persist_geometry()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self):
        self.root.mainloop()


# ------------------------------------------------------------------ helpers
class ScrollArea:
    """A vertically scrollable region that keeps the widget usable when small."""

    def __init__(self, parent, widget: CerebroWidget):
        theme = widget.theme
        self.canvas = tk.Canvas(parent, bg=theme["bg"], highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = tk.Frame(self.canvas, bg=theme["bg"])
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind_all(sequence, self._on_wheel)

    def _on_inner(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _on_wheel(self, event):
        try:
            if event.num == 4:
                delta = -1
            elif event.num == 5:
                delta = 1
            else:
                delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass


class Tooltip:
    """Minimal hover tooltip — Tkinter has none built in."""

    def __init__(self, target, text: str, widget: CerebroWidget):
        self.target = target
        self.text = text
        self.widget = widget
        self.window = None
        target.bind("<Enter>", self._show, add="+")
        target.bind("<Leave>", self._hide, add="+")

    def _show(self, _event=None):
        if self.window:
            return
        theme = self.widget.theme
        x = self.target.winfo_rootx()
        y = self.target.winfo_rooty() + self.target.winfo_height() + 4
        self.window = tk.Toplevel(self.target)
        self.window.overrideredirect(True)
        self.window.geometry(f"+{x}+{y}")
        tk.Label(self.window, text=self.text, bg=theme["surface2"], fg=theme["dim"],
                 font=self.widget.font(8), padx=6, pady=3,
                 highlightthickness=1, highlightbackground=theme["border"]).pack()

    def _hide(self, _event=None):
        if self.window:
            self.window.destroy()
            self.window = None


class SettingsDialog:
    """Widget preferences — separate from the server settings at /settings."""

    def __init__(self, widget: CerebroWidget):
        self.widget = widget
        theme = widget.theme

        self.top = tk.Toplevel(widget.root)
        self.top.title("Widget preferences")
        self.top.configure(bg=theme["bg"])
        self.top.attributes("-topmost", True)
        self.top.resizable(False, False)
        self.top.geometry(
            f"+{widget.root.winfo_rootx() + 20}+{widget.root.winfo_rooty() + 40}")

        tk.Label(self.top, text="Widget preferences", bg=theme["bg"], fg=theme["text"],
                 font=widget.font(11, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(self.top, text="These only affect this widget. Server settings live "
                                "in the dashboard.",
                 bg=theme["bg"], fg=theme["faint"], font=widget.font(8),
                 justify="left").pack(anchor="w", padx=16, pady=(0, 10))

        self.api_url = self._entry("Cerebro API URL", widget.config["api_url"])
        self.poll = self._entry("Refresh every (seconds)", str(widget.config["poll_seconds"]))

        self.on_top = tk.IntVar(value=int(widget.config["always_on_top"]))
        self.snap = tk.IntVar(value=int(widget.config["snap_to_edges"]))
        self.notify = tk.IntVar(value=int(widget.config["notify_on_change"]))
        for label, variable in (("Always on top", self.on_top),
                                ("Snap to screen edges", self.snap),
                                ("Flash when the context changes", self.notify)):
            tk.Checkbutton(self.top, text=label, variable=variable, bg=theme["bg"],
                           fg=theme["text"], selectcolor=theme["surface2"],
                           activebackground=theme["bg"], activeforeground=theme["text"],
                           font=widget.font(9), highlightthickness=0, bd=0,
                           anchor="w").pack(fill="x", padx=14)

        tk.Label(self.top, text="Opacity", bg=theme["bg"], fg=theme["dim"],
                 font=widget.font(8)).pack(anchor="w", padx=16, pady=(10, 0))
        self.opacity = tk.Scale(self.top, from_=0.4, to=1.0, resolution=0.05,
                                orient="horizontal", bg=theme["bg"], fg=theme["text"],
                                troughcolor=theme["surface2"], highlightthickness=0,
                                bd=0, font=widget.font(7), length=250,
                                activebackground=theme["accent"])
        self.opacity.set(widget.config["opacity"])
        self.opacity.pack(padx=14, fill="x")

        buttons = tk.Frame(self.top, bg=theme["bg"])
        buttons.pack(fill="x", padx=14, pady=14)
        tk.Button(buttons, text="Cancel", command=self.top.destroy, bg=theme["surface"],
                  fg=theme["text"], font=widget.font(9), relief="flat", bd=0,
                  padx=14, pady=5, cursor="hand2").pack(side="right", padx=(6, 0))
        tk.Button(buttons, text="Save", command=self._save, bg=theme["accent"],
                  fg=theme["accent_text"], font=widget.font(9, "bold"), relief="flat",
                  bd=0, padx=16, pady=5, cursor="hand2").pack(side="right")

    def _entry(self, label: str, value: str) -> tk.Entry:
        theme = self.widget.theme
        tk.Label(self.top, text=label, bg=theme["bg"], fg=theme["dim"],
                 font=self.widget.font(8)).pack(anchor="w", padx=16, pady=(8, 2))
        entry = tk.Entry(self.top, bg=theme["surface2"], fg=theme["text"],
                         font=self.widget.font(9), relief="flat", insertbackground=theme["text"],
                         highlightthickness=1, highlightbackground=theme["border"],
                         highlightcolor=theme["accent"], width=34)
        entry.insert(0, value)
        entry.pack(padx=14, ipady=4, fill="x")
        return entry

    def _save(self):
        try:
            poll_seconds = max(2, int(float(self.poll.get())))
        except ValueError:
            poll_seconds = self.widget.config["poll_seconds"]

        self.widget.apply_settings({
            "api_url": self.api_url.get().strip().rstrip("/") or "http://127.0.0.1:8000",
            "poll_seconds": poll_seconds,
            "always_on_top": bool(self.on_top.get()),
            "snap_to_edges": bool(self.snap.get()),
            "notify_on_change": bool(self.notify.get()),
            "opacity": float(self.opacity.get()),
        })
        self.top.destroy()


# --------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description="Cerebro desktop widget")
    parser.add_argument("--api", help="Cerebro API base URL")
    parser.add_argument("--reset", action="store_true",
                        help="forget saved position and preferences")
    arguments = parser.parse_args()

    if arguments.reset:
        try:
            widget_config.CONFIG_PATH.unlink(missing_ok=True)
        except OSError:
            pass

    config = widget_config.load()
    if arguments.api:
        config["api_url"] = arguments.api.rstrip("/")

    try:
        widget = CerebroWidget(config)
    except tk.TclError as exc:
        print("The desktop widget needs a graphical display and Tkinter.")
        print(f"Details: {exc}")
        if sys.platform.startswith("linux"):
            print("On Debian/Ubuntu: sudo apt install python3-tk")
        return 1

    widget.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

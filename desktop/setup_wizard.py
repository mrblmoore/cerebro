#!/usr/bin/env python3
"""
Cerebro's setup wizard — the one the Windows installer runs.

Installing used to finish by opening a browser at a configuration page, which
meant "install complete" and "ready to use" were two different moments and the
second one was easy to skip. This runs as the last step of the installer
instead, so when it closes the install genuinely is finished.

Every step proves itself before it lets you past: the database is opened, the AI
provider is actually called, the Microsoft 365 folders are written to and read
back. A step that cannot pass can be skipped deliberately, but never silently.

Run standalone with ``python desktop/setup_wizard.py``; ``--first-run`` is what
the installer passes.
"""

import argparse
import os
import sys
import threading
import queue
import webbrowser
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog
except ImportError:  # pragma: no cover - depends on the OS Python build
    sys.stderr.write(
        "This wizard needs Tkinter, which is missing from this Python.\n"
        "On Debian/Ubuntu: sudo apt install python3-tk\n"
        "You can configure Cerebro in a browser instead: start Cerebro and "
        "open http://localhost:8000/setup\n"
    )
    raise SystemExit(1)


# The backend is a sibling directory in a source checkout and bundled at the
# top level in the frozen build.
_HERE = Path(__file__).resolve().parent
for _candidate in (_HERE.parent / "backend", _HERE, _HERE.parent):
    if (_candidate / "app").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
        break


import branding

#: Shared with the widget and the web UI, so all three feel like one product.
T = branding.DARK

#: Resolved once Tk exists — see :func:`Wizard._init_branding`.
FONT = "Segoe UI" if os.name == "nt" else "DejaVu Sans"


class Wizard(tk.Tk):
    """A five-step wizard that configures and verifies an install."""

    STEPS = [
        ("welcome", "Welcome", "Checking your install"),
        ("database", "Storage", "Where your work is kept"),
        ("ai", "AI model", "Optional — adds summaries and drafts"),
        ("microsoft", "Microsoft 365", "Optional — Outlook, Teams and Copilot"),
        ("done", "Finish", "You are ready to go"),
    ]

    def __init__(self, first_run: bool = False):
        super().__init__()
        self.first_run = first_run
        self.index = 0
        self.pending = {}          # settings staged by this wizard
        self.passed = set()        # steps whose test has gone green
        self.models = {}           # live model lists per provider
        self.custom_model = set()  # model fields switched to free text
        self.busy = False
        self._results = queue.Queue()

        self.title("Cerebro Setup")
        self.configure(bg=T["bg"])
        self.geometry("880x620")
        self.minsize(820, 580)
        self._centre()

        self._init_branding()
        self._style_ttk()
        self._build_frame()
        self._render()
        self.after(80, self._drain_results)

    # ------------------------------------------------------------- chrome
    def _init_branding(self):
        """Register the bundled font and set the window icon, before any widget."""
        global FONT
        try:
            FONT = branding.load_fonts()
        except Exception:  # noqa: BLE001 - the default font is fine
            pass
        branding.apply_window_icon(self)
        # Held on the instance because Tk garbage-collects images it cannot see
        # a reference to, which shows up as a silently blank label.
        self.logo = branding.logo_image(48)

    def _style_ttk(self):
        """ttk ships light grey; without this the dropdowns look pasted on."""
        style = ttk.Style(self)
        try:
            style.theme_use("clam")   # the only stock theme that honours colours
        except tk.TclError:
            pass
        style.configure(
            "Cerebro.TCombobox", fieldbackground=T["surface2"], background=T["surface2"],
            foreground=T["text"], arrowcolor=T["dim"], bordercolor=T["border"],
            lightcolor=T["surface2"], darkcolor=T["surface2"], padding=6,
        )
        style.map(
            "Cerebro.TCombobox",
            fieldbackground=[("readonly", T["surface2"])],
            foreground=[("readonly", T["text"])],
            selectbackground=[("readonly", T["surface2"])],
            selectforeground=[("readonly", T["text"])],
            bordercolor=[("focus", T["accent"])],
        )
        # The popup list is a classic Tk listbox, reachable only this way.
        self.option_add("*TCombobox*Listbox.background", T["surface2"])
        self.option_add("*TCombobox*Listbox.foreground", T["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", T["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", T["accent_text"])

    def _centre(self):
        self.update_idletasks()
        width, height = 880, 620
        x = (self.winfo_screenwidth() - width) // 2
        y = (self.winfo_screenheight() - height) // 3
        self.geometry(f"{width}x{height}+{max(x, 0)}+{max(y, 0)}")

    def _build_frame(self):
        outer = tk.Frame(self, bg=T["bg"])
        outer.pack(fill="both", expand=True)

        # Sidebar: the whole journey visible at once, so nobody wonders how
        # much is left.
        self.sidebar = tk.Frame(outer, bg=T["surface"], width=228)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=T["surface"])
        brand.pack(fill="x", padx=22, pady=(26, 22))
        if self.logo is not None:
            tk.Label(brand, image=self.logo, bg=T["surface"]).pack(side="left", padx=(0, 11))
        words = tk.Frame(brand, bg=T["surface"])
        words.pack(side="left")
        tk.Label(words, text="Cerebro", bg=T["surface"], fg=T["text"],
                 font=(FONT, 18, "bold")).pack(anchor="w")
        tk.Label(words, text="Setup", bg=T["surface"], fg=T["faint"],
                 font=(FONT, 10)).pack(anchor="w")
        self.step_labels = []
        for _key, title, _sub in self.STEPS:
            label = tk.Label(self.sidebar, text=title, bg=T["surface"], fg=T["faint"],
                             font=(FONT, 11), anchor="w")
            label.pack(fill="x", padx=22, pady=5)
            self.step_labels.append(label)

        right = tk.Frame(outer, bg=T["bg"])
        right.pack(side="left", fill="both", expand=True)

        header = tk.Frame(right, bg=T["bg"])
        header.pack(fill="x", padx=34, pady=(30, 6))
        self.title_label = tk.Label(header, text="", bg=T["bg"], fg=T["text"],
                                    font=(FONT, 21, "bold"), anchor="w")
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(header, text="", bg=T["bg"], fg=T["dim"],
                                       font=(FONT, 11), anchor="w", justify="left")
        self.subtitle_label.pack(fill="x", pady=(3, 0))

        # Footer first so it is pinned to the bottom whatever the body does.
        footer = tk.Frame(right, bg=T["bg"])
        footer.pack(side="bottom", fill="x", padx=34, pady=(0, 24))

        # The body scrolls. A provider step with credentials plus a model picker
        # is taller than a small laptop screen, and a Continue button below the
        # fold is a trap rather than a layout quirk.
        middle = tk.Frame(right, bg=T["bg"])
        middle.pack(fill="both", expand=True)
        canvas = tk.Canvas(middle, bg=T["bg"], highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True, padx=(34, 0), pady=(14, 8))
        bar = tk.Scrollbar(middle, orient="vertical", command=canvas.yview,
                           bg=T["surface"], troughcolor=T["bg"], bd=0,
                           highlightthickness=0, relief="flat", width=10,
                           activebackground=T["border"])
        bar.pack(side="right", fill="y", padx=(2, 8), pady=(14, 8))
        canvas.configure(yscrollcommand=bar.set)

        self.body = tk.Frame(canvas, bg=T["bg"])
        window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>",
                    lambda event: canvas.itemconfig(window, width=event.width - 10))
        self.body.bind("<Configure>",
                       lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        for sequence, delta in (("<Button-4>", -1), ("<Button-5>", 1)):
            canvas.bind_all(sequence, lambda _e, d=delta: canvas.yview_scroll(d, "units"))
        canvas.bind_all("<MouseWheel>",
                        lambda event: canvas.yview_scroll(-(event.delta // 120), "units"))
        self.status = tk.Label(footer, text="", bg=T["bg"], fg=T["dim"],
                               font=(FONT, 10), anchor="w", justify="left",
                               wraplength=430)
        self.status.pack(side="left", fill="x", expand=True)

        self.next_button = self._button(footer, "Continue", self._next, primary=True)
        self.next_button.pack(side="right")
        self.skip_button = self._button(footer, "Skip", self._skip)
        self.skip_button.pack(side="right", padx=(0, 8))
        self.back_button = self._button(footer, "Back", self._back)
        self.back_button.pack(side="right", padx=(0, 8))

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent, text=text, command=command, font=(FONT, 10, "bold" if primary else "normal"),
            bg=T["accent"] if primary else T["surface2"],
            fg=T["accent_text"] if primary else T["text"],
            activebackground=T["accent"] if primary else T["border"],
            activeforeground=T["accent_text"] if primary else T["text"],
            relief="flat", bd=0, padx=20, pady=9, cursor="hand2",
            highlightthickness=0,
        )

    # -------------------------------------------------------- small parts
    def _card(self, parent):
        card = tk.Frame(parent, bg=T["surface"], highlightbackground=T["border"],
                        highlightthickness=1)
        card.pack(fill="x", pady=(0, 12))
        return card

    def _text(self, parent, text, *, colour=None, size=10, bold=False, pad=(14, 10)):
        label = tk.Label(parent, text=text, bg=parent["bg"], fg=colour or T["dim"],
                         font=(FONT, size, "bold" if bold else "normal"),
                         anchor="w", justify="left", wraplength=520)
        label.pack(fill="x", padx=pad[0], pady=pad[1])
        return label

    def _field(self, parent, key, label, *, secret=False, placeholder="", initial=None):
        """
        A labelled entry bound straight into ``self.pending``.

        ``initial`` overrides the saved value, for the case where showing what
        is stored would be actively misleading — a SQLite path sitting in a box
        labelled "PostgreSQL connection string", for instance.
        """
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=14, pady=(2, 10))
        tk.Label(row, text=label, bg=row["bg"], fg=T["text"], font=(FONT, 10),
                 anchor="w").pack(fill="x")
        value = self._value(key) if initial is None else initial
        variable = tk.StringVar(value=str(value or ""))
        entry = tk.Entry(row, textvariable=variable, font=(FONT, 10),
                         bg=T["surface2"], fg=T["text"], insertbackground=T["text"],
                         relief="flat", bd=0, show="•" if secret else "")
        entry.pack(fill="x", ipady=7, pady=(4, 0))
        if placeholder:
            tk.Label(row, text=placeholder, bg=row["bg"], fg=T["faint"],
                     font=(FONT, 9), anchor="w").pack(fill="x", pady=(3, 0))
        variable.trace_add("write", lambda *_: self.pending.__setitem__(key, variable.get()))
        return entry

    def _value(self, key):
        """Current value, preferring an unsaved edit from this wizard."""
        if key in self.pending:
            return self.pending[key]
        from app.core.config import settings
        return getattr(settings, key, "")

    def _set_status(self, message, kind="dim"):
        self.status.configure(text=message, fg=T.get(kind, T["dim"]))

    # --------------------------------------------------------------- motion
    def _start_spinner(self, message):
        """
        A braille spinner in the status line.

        Tk has no animation primitive, so this is an after() loop. Every check
        the wizard runs talks to something slow — a database, an AI provider, a
        synced folder — and a frozen window is the difference between "working"
        and "hung".
        """
        self._spin_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spin_index = 0
        self._spin_message = message
        self._spin_active = True

        def tick():
            if not self._spin_active:
                return
            frame = self._spin_frames[self._spin_index % len(self._spin_frames)]
            self._spin_index += 1
            self.status.configure(text=f"{frame}  {self._spin_message}", fg=T["dim"])
            self._spin_job = self.after(80, tick)

        tick()

    def _stop_spinner(self):
        self._spin_active = False
        job = getattr(self, "_spin_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001 - already fired
                pass
            self._spin_job = None

    def _flash(self, widget, colour_key="ok"):
        """Briefly tint a widget, to confirm something just succeeded."""
        try:
            original = widget.cget("fg")
        except Exception:  # noqa: BLE001
            return
        widget.configure(fg=T[colour_key])
        self.after(900, lambda: widget.winfo_exists() and widget.configure(fg=original))

    def _animate_step_in(self):
        """
        Fade the window's step content in.

        Tk cannot fade a frame, so this steps the *text* colours from the
        background toward their final value, which reads as the panel resolving.
        Cheap, and it makes step changes feel deliberate rather than abrupt.
        """
        steps = 6

        def blend(start, end, ratio):
            return "#" + "".join(
                f"{round(int(start[i:i + 2], 16) * (1 - ratio) + int(end[i:i + 2], 16) * ratio):02x}"
                for i in (1, 3, 5))

        widgets = []
        def collect(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Label):
                    try:
                        widgets.append((child, child.cget("fg")))
                    except Exception:  # noqa: BLE001
                        pass
                collect(child)
        collect(self.body)

        def frame(index):
            if index > steps:
                for widget, final in widgets:
                    if widget.winfo_exists():
                        widget.configure(fg=final)
                return
            ratio = index / steps
            for widget, final in widgets:
                if widget.winfo_exists():
                    try:
                        widget.configure(fg=blend(T["bg"], final, ratio))
                    except Exception:  # noqa: BLE001 - named colours, skip
                        pass
            self.after(18, lambda: frame(index + 1))

        frame(0)

    # ------------------------------------------------------------ routing
    def _render(self):
        for index, label in enumerate(self.step_labels):
            key = self.STEPS[index][0]
            done = key in self.passed
            label.configure(
                fg=T["text"] if index == self.index else (T["ok"] if done else T["faint"]),
                text=("✓ " if done and index != self.index else "") + self.STEPS[index][1],
                font=(FONT, 11, "bold" if index == self.index else "normal"),
            )

        key, title, subtitle = self.STEPS[self.index]
        self.title_label.configure(text=title)
        self.subtitle_label.configure(text=subtitle)
        for child in self.body.winfo_children():
            child.destroy()
        self._set_status("")

        self.back_button.configure(state="normal" if self.index > 0 else "disabled")
        # Re-pack all three every render. Hiding and re-showing Skip otherwise
        # appends it to the end of the pack order, which silently reshuffles the
        # row into "Skip Back Continue".
        for button in (self.next_button, self.skip_button, self.back_button):
            button.pack_forget()
        self.next_button.pack(side="right")
        # Optional steps can be passed over; the required ones cannot.
        if key not in ("welcome", "done"):
            self.skip_button.pack(side="right", padx=(0, 8))
        self.back_button.pack(side="right", padx=(0, 8))
        self.next_button.configure(text="Finish" if key == "done" else "Continue")

        getattr(self, f"_step_{key}")()
        self._animate_step_in()

    def _next(self):
        if self.busy:
            return
        key = self.STEPS[self.index][0]
        if key == "done":
            self._finish()
            return
        if not self._save():
            return
        self.index = min(self.index + 1, len(self.STEPS) - 1)
        self._render()

    def _skip(self):
        if self.busy:
            return
        self.index = min(self.index + 1, len(self.STEPS) - 1)
        self._render()

    def _back(self):
        if self.busy:
            return
        self.index = max(self.index - 1, 0)
        self._render()

    def _save(self) -> bool:
        """Persist staged settings; refuse to advance if they are rejected."""
        if not self.pending:
            return True
        from app.core import settings_store
        try:
            result = settings_store.update(dict(self.pending))
        except Exception as exc:  # noqa: BLE001 - shown to the user
            self._set_status(f"Could not save: {exc}", "err")
            return False
        if not result.get("ok"):
            errors = result.get("errors") or {}
            first = "; ".join(f"{k}: {v}" for k, v in list(errors.items())[:2])
            self._set_status(first or "Those settings were rejected.", "err")
            return False
        self.pending.clear()
        return True

    # ------------------------------------------------ background testing
    def _run_async(self, work, on_done, message="Working…"):
        """Run a slow check off the UI thread so the window never freezes."""
        self.busy = True
        self.next_button.configure(state="disabled")
        self._start_spinner(message)

        def worker():
            try:
                self._results.put((on_done, work(), None))
            except Exception as exc:  # noqa: BLE001 - reported in the UI
                self._results.put((on_done, None, exc))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_results(self):
        try:
            while True:
                callback, value, error = self._results.get_nowait()
                self.busy = False
                self._stop_spinner()
                self.next_button.configure(state="normal")
                callback(value, error)
        except queue.Empty:
            pass
        self.after(80, self._drain_results)

    # -------------------------------------------------------------- steps
    def _step_welcome(self):
        from app.core import setup_checks

        self._text(self.body,
                   "Cerebro keeps everything on this computer. This takes about a "
                   "minute, and every answer can be changed later.",
                   colour=T["dim"], pad=(0, (0, 14)))

        report = setup_checks.preflight()
        card = self._card(self.body)
        self._text(card, "What is installed", colour=T["text"], bold=True, pad=(14, (12, 4)))

        broken = []
        for check in report["checks"]:
            if check["ok"]:
                mark, colour = "✓", T["ok"]
                detail = check["label"]
            else:
                broken.append(check)
                mark = "✗" if check["required"] else "○"
                colour = T["err"] if check["required"] else T["warn"]
                detail = f"{check['label']} — {check['why']}"
            row = tk.Frame(card, bg=T["surface"])
            row.pack(fill="x", padx=14, pady=1)
            tk.Label(row, text=mark, bg=T["surface"], fg=colour,
                     font=(FONT, 11, "bold"), width=2).pack(side="left")
            tk.Label(row, text=detail, bg=T["surface"], fg=T["dim"], font=(FONT, 10),
                     anchor="w", justify="left", wraplength=470).pack(side="left", fill="x")
        tk.Frame(card, bg=T["surface"], height=10).pack()

        repairable = [c for c in broken if c["repairable"]]
        if repairable:
            fix_card = self._card(self.body)
            self._text(fix_card,
                       "Some optional pieces are missing. Cerebro can install them "
                       "now, into its own environment — which is the part that is "
                       "easy to get wrong by hand.",
                       colour=T["dim"], pad=(14, (12, 8)))
            button = self._button(fix_card, "Install what's missing",
                                  lambda: self._repair(repairable))
            button.pack(anchor="w", padx=14, pady=(0, 14))
        elif broken:
            self._text(self.body,
                       "The missing pieces cannot be installed automatically in this "
                       "build. Cerebro still runs; the features above stay off.",
                       colour=T["warn"], pad=(0, (2, 0)))
        else:
            self._set_status("Everything is installed and ready.", "ok")
            self.passed.add("welcome")

    def _repair(self, checks):
        from app.core import setup_checks

        names = [c["component"] for c in checks]

        def work():
            return [setup_checks.repair(name) for name in names]

        def done(results, error):
            if error:
                self._set_status(f"Could not install: {error}", "err")
                return
            failed = [r for r in results if not r.get("ok")]
            if failed:
                self._set_status(failed[0].get("detail", "Install failed."), "err")
            else:
                self._set_status("Installed. Re-checking…", "ok")
            self._render()

        self._run_async(work, done, "Installing — this can take a minute…")

    # ---------------------------------------------------------- database
    def _step_database(self):
        self._text(self.body,
                   "Cerebro stores your cases, documents and memory in a database "
                   "on this computer. The built-in one needs nothing installed and "
                   "suits almost everyone.",
                   pad=(0, (0, 14)))

        self.db_choice = tk.StringVar(value="sqlite" if self._is_sqlite() else "postgres")
        card = self._card(self.body)
        for value, title, detail in (
            ("sqlite", "Built-in database (recommended)",
             "A single file in your Cerebro folder. Nothing to install or run."),
            ("postgres", "PostgreSQL",
             "For sharing one Cerebro across a team. You supply the server."),
        ):
            row = tk.Frame(card, bg=T["surface"])
            row.pack(fill="x", padx=14, pady=(10, 2))
            tk.Radiobutton(row, text=title, value=value, variable=self.db_choice,
                           command=self._render_db_detail, bg=T["surface"], fg=T["text"],
                           selectcolor=T["surface2"], activebackground=T["surface"],
                           activeforeground=T["text"], font=(FONT, 10, "bold"),
                           anchor="w", highlightthickness=0, bd=0).pack(fill="x")
            tk.Label(row, text=detail, bg=T["surface"], fg=T["faint"], font=(FONT, 9),
                     anchor="w", wraplength=470, justify="left").pack(fill="x", padx=24)
        tk.Frame(card, bg=T["surface"], height=10).pack()

        self.db_detail = tk.Frame(self.body, bg=T["bg"])
        self.db_detail.pack(fill="x")
        self._render_db_detail()

        self._button(self.body, "Test the database", self._test_database).pack(anchor="w", pady=(4, 0))

    def _is_sqlite(self):
        return str(self._value("DATABASE_URL") or "sqlite").startswith("sqlite")

    def _render_db_detail(self):
        for child in self.db_detail.winfo_children():
            child.destroy()
        if self.db_choice.get() == "postgres":
            from app.core import setup_checks

            card = self._card(self.db_detail)
            # Without the driver, SQLAlchemy fails while *building* the engine,
            # which is early enough to stop Cerebro starting. Offer the fix here
            # rather than letting them save a setting that breaks the app.
            absent = setup_checks.missing_modules("postgres")
            if absent:
                self._text(card,
                           "PostgreSQL needs a driver that is not installed yet. "
                           "Cerebro will keep using its built-in database until it is.",
                           colour=T["warn"], pad=(14, (12, 6)))
                self._button(card, "Install the PostgreSQL driver",
                             lambda: self._repair([setup_checks.component_status("postgres")])
                             ).pack(anchor="w", padx=14, pady=(0, 12))
            # Start blank rather than showing the built-in database's own path,
            # which reads like a suggestion to keep it.
            current = str(self._value("DATABASE_URL") or "")
            self._field(card, "DATABASE_URL", "Connection string",
                        initial="" if current.startswith("sqlite") else current,
                        placeholder="postgresql+psycopg://cerebro:cerebro@localhost:5432/cerebro")
        else:
            # Switching back must actually clear a Postgres URL, or "built-in"
            # would be a lie the next test exposes.
            if not self._is_sqlite():
                self.pending["DATABASE_URL"] = ""

    def _test_database(self):
        if not self._save():
            return

        def work():
            from app.core import check_database, init_db
            init_db()          # create the file and tables if this is a first run
            return check_database()

        def done(result, error):
            if error:
                self._set_status(f"Could not open the database: {error}", "err")
                return
            if result.get("ok"):
                self.passed.add("database")
                self._set_status(result.get("detail") or "Database is ready.", "ok")
                self._render()
            else:
                self._set_status(
                    (result.get("detail") or "The database did not answer.") +
                    "  If this is PostgreSQL, check the server is running and the "
                    "connection string is right.", "err")

        self._run_async(work, done, "Opening the database…")

    # ---------------------------------------------------------------- ai
    def _step_ai(self):
        self._text(self.body,
                   "Connect a model and Cerebro can summarise cases, suggest next "
                   "steps and draft replies. Skip this and everything else still "
                   "works — you can add one later in Settings.",
                   pad=(0, (0, 12)))

        self.provider = tk.StringVar(value=str(self._value("LLM_PROVIDER") or "none"))
        card = self._card(self.body)
        for value, title, detail in (
            ("none", "Not now", "Cerebro runs without AI."),
            ("ollama", "Ollama — runs on this computer",
             "Free and private. Needs Ollama installed separately."),
            ("openai", "OpenAI", "Paste an API key."),
            ("bedrock", "Amazon Bedrock", "Uses your AWS account."),
        ):
            row = tk.Frame(card, bg=T["surface"])
            row.pack(fill="x", padx=14, pady=(8, 0))
            tk.Radiobutton(row, text=title, value=value, variable=self.provider,
                           command=self._on_provider, bg=T["surface"], fg=T["text"],
                           selectcolor=T["surface2"], activebackground=T["surface"],
                           activeforeground=T["text"], font=(FONT, 10, "bold"),
                           anchor="w", highlightthickness=0, bd=0).pack(fill="x")
            tk.Label(row, text=detail, bg=T["surface"], fg=T["faint"], font=(FONT, 9),
                     anchor="w").pack(fill="x", padx=24)
        tk.Frame(card, bg=T["surface"], height=10).pack()

        self.ai_detail = tk.Frame(self.body, bg=T["bg"])
        self.ai_detail.pack(fill="both", expand=True)
        self._render_ai_detail()

    def _on_provider(self):
        self.pending["LLM_PROVIDER"] = self.provider.get()
        self._render_ai_detail()

    def _render_ai_detail(self):
        for child in self.ai_detail.winfo_children():
            child.destroy()
        provider = self.provider.get()
        if provider == "none":
            self._set_status("")
            return

        from app.core import setup_checks

        # A provider whose SDK is absent is a dead end unless we offer the fix
        # here, in the interpreter that actually matters.
        component = setup_checks.component_for_provider(provider)
        if component and setup_checks.missing_modules(component):
            card = self._card(self.ai_detail)
            self._text(card,
                       f"This provider needs a package that is not installed "
                       f"({', '.join(setup_checks.missing_modules(component))}).",
                       colour=T["warn"], pad=(14, (12, 6)))
            self._button(card, "Install it now",
                         lambda: self._repair([setup_checks.component_status(component)])
                         ).pack(anchor="w", padx=14, pady=(0, 14))
            return

        card = self._card(self.ai_detail)
        if provider == "openai":
            self._field(card, "OPENAI_API_KEY", "API key", secret=True,
                        placeholder="Starts with sk-")
        elif provider == "ollama":
            self._field(card, "OLLAMA_URL", "Ollama address",
                        placeholder="http://localhost:11434 unless you changed it")
        elif provider == "bedrock":
            self._field(card, "BEDROCK_REGION", "AWS Region", placeholder="for example us-east-1")
            self._bedrock_auth(card)

        self._model_picker(card, provider)
        self._button(self.ai_detail, "Test the connection", self._test_ai).pack(anchor="w", pady=(4, 0))

    def _bedrock_auth(self, parent):
        """AWS signs requests with an identity; make the choice legible."""
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=14, pady=(2, 6))
        tk.Label(row, text="How to sign in to AWS", bg=row["bg"], fg=T["text"],
                 font=(FONT, 10), anchor="w").pack(fill="x")

        modes = [
            ("default", "Use the AWS sign-in already on this computer"),
            ("api_key", "Bedrock API key — paste a single key"),
            ("profile", "Named AWS profile"),
            ("keys", "AWS access key ID and secret"),
        ]
        self.bedrock_mode = tk.StringVar(value=str(self._value("BEDROCK_AUTH_MODE") or "default"))
        box = ttk.Combobox(row, state="readonly", font=(FONT, 10),
                           style="Cerebro.TCombobox",
                           values=[label for _v, label in modes])
        current = next((i for i, (v, _l) in enumerate(modes)
                        if v == self.bedrock_mode.get()), 0)
        box.current(current)
        box.pack(fill="x", pady=(4, 0), ipady=3)

        def changed(_event=None):
            value = modes[box.current()][0]
            self.bedrock_mode.set(value)
            self.pending["BEDROCK_AUTH_MODE"] = value
            self._render_ai_detail()

        box.bind("<<ComboboxSelected>>", changed)

        mode = self.bedrock_mode.get()
        if mode == "api_key":
            self._field(parent, "BEDROCK_API_KEY", "Bedrock API key", secret=True,
                        placeholder="Create one in the Bedrock console under API keys")
        elif mode == "profile":
            self._field(parent, "BEDROCK_AWS_PROFILE", "Profile name", placeholder="default")
        elif mode == "keys":
            self._field(parent, "BEDROCK_AWS_ACCESS_KEY_ID", "Access key ID", secret=True)
            self._field(parent, "BEDROCK_AWS_SECRET_ACCESS_KEY", "Secret access key", secret=True)
            self._field(parent, "BEDROCK_AWS_SESSION_TOKEN", "Session token (temporary keys only)",
                        secret=True)

    def _model_picker(self, parent, provider):
        """Pick from a list rather than typing an ID from memory."""
        from app.core import model_catalog

        key = model_catalog.MODEL_KEY.get(provider, "")
        if not key:
            return
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=14, pady=(2, 12))
        tk.Label(row, text="Model", bg=row["bg"], fg=T["text"], font=(FONT, 10),
                 anchor="w").pack(fill="x")

        live = self.models.get(provider)
        options = live["models"] if live else model_catalog.options(provider, str(self._value(key) or ""))
        labels = [f"{m.get('label') or m['id']}" + (f" — {m['note']}" if m.get("note") else "")
                  for m in options]

        line = tk.Frame(row, bg=row["bg"])
        line.pack(fill="x", pady=(4, 0))
        box = ttk.Combobox(line, state="readonly", font=(FONT, 10),
                           style="Cerebro.TCombobox", values=labels)
        value = str(self._value(key) or "")
        selected = next((i for i, m in enumerate(options) if m["id"] == value), 0)
        box.current(selected)
        box.pack(side="left", fill="x", expand=True, ipady=3)

        def changed(_event=None):
            chosen = options[box.current()]
            if chosen["id"] == model_catalog.CUSTOM:
                self.custom_model.add(key)
            else:
                self.custom_model.discard(key)
                self.pending[key] = chosen["id"]
            self._render_ai_detail()

        box.bind("<<ComboboxSelected>>", changed)
        self._button(line, "Refresh", lambda: self._refresh_models(provider)
                     ).pack(side="left", padx=(8, 0))

        if key in self.custom_model:
            self._field(parent, key, "Model ID")

        if live:
            note = ("Listed from your account — every option here works."
                    if live.get("live") else live.get("error", ""))
            tk.Label(row, text=note, bg=row["bg"],
                     fg=T["ok"] if live.get("live") else T["warn"],
                     font=(FONT, 9), anchor="w", wraplength=470,
                     justify="left").pack(fill="x", pady=(4, 0))

    def _refresh_models(self, provider):
        if not self._save():
            return

        def work():
            from app.core.model_catalog import CUSTOM
            from app.services import model_discovery
            models, error = model_discovery.discover(provider)
            return {"models": list(models) + [{"id": CUSTOM, "label": "Custom model ID…"}],
                    "live": not error, "error": error}

        def done(result, error):
            if error:
                self._set_status(f"Could not list models: {error}", "err")
                return
            self.models[provider] = result
            self._set_status("Model list updated." if result["live"] else result["error"],
                             "ok" if result["live"] else "warn")
            self._render_ai_detail()

        self._run_async(work, done, "Asking the provider which models you can use…")

    def _test_ai(self):
        if not self._save():
            return

        def work():
            from app.services.llm_service import LLMService
            return LLMService().test_connection()

        def done(result, error):
            if error:
                self._set_status(f"Test failed: {error}", "err")
                return
            if result.get("ok"):
                self.passed.add("ai")
                self._set_status(result.get("detail") or "The model answered.", "ok")
                self._render()
            else:
                self._set_status(result.get("detail") or "The model did not answer.", "err")

        self._run_async(work, done, "Asking the model to answer a test question…")

    # --------------------------------------------------------- microsoft
    def _step_microsoft(self):
        self._text(self.body,
                   "Cerebro talks to Outlook, Teams and a Copilot Studio agent "
                   "through folders on this computer — no passwords, and nothing "
                   "leaves your machine except what you already sync.",
                   pad=(0, (0, 12)))

        card = self._card(self.body)
        self._text(card, "Outlook and Teams (Power Automate)", colour=T["text"],
                   bold=True, pad=(14, (12, 2)))
        self._text(card,
                   "Two Power Automate flows drop messages into a folder. Leave "
                   "blank if you are not using them yet.",
                   pad=(14, (0, 6)))
        self._folder_field(card, "ENTERPRISE_INBOX_DIR", "Inbound folder")

        card2 = self._card(self.body)
        self._text(card2, "Copilot Studio agent", colour=T["text"], bold=True,
                   pad=(14, (12, 2)))
        self._text(card2,
                   "A folder inside OneDrive that Cerebro and your agent both read. "
                   "Cerebro writes what it knows; the agent writes back what it "
                   "wants done.",
                   pad=(14, (0, 6)))
        self._folder_field(card2, "COPILOT_BRIDGE_DIR", "Shared OneDrive folder")

        self._button(self.body, "Test these folders", self._test_microsoft).pack(anchor="w", pady=(4, 0))

    def _folder_field(self, parent, key, label):
        row = tk.Frame(parent, bg=parent["bg"])
        row.pack(fill="x", padx=14, pady=(2, 12))
        tk.Label(row, text=label, bg=row["bg"], fg=T["text"], font=(FONT, 10),
                 anchor="w").pack(fill="x")
        line = tk.Frame(row, bg=row["bg"])
        line.pack(fill="x", pady=(4, 0))
        variable = tk.StringVar(value=str(self._value(key) or ""))
        entry = tk.Entry(line, textvariable=variable, font=(FONT, 10), bg=T["surface2"],
                         fg=T["text"], insertbackground=T["text"], relief="flat", bd=0)
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        variable.trace_add("write", lambda *_: self.pending.__setitem__(key, variable.get()))

        def browse():
            chosen = filedialog.askdirectory(title=label)
            if chosen:
                variable.set(chosen)

        self._button(line, "Browse…", browse).pack(side="left", padx=(8, 0))

    def _test_microsoft(self):
        if not self._save():
            return

        def work():
            from app.core.config import settings
            from app.core.database import SessionLocal
            from app.services import enterprise_service
            out = []
            if (settings.ENTERPRISE_INBOX_DIR or "").strip():
                out.append(("Outlook and Teams", enterprise_service.status()))
            if (settings.COPILOT_BRIDGE_DIR or "").strip():
                db = SessionLocal()
                try:
                    from app.api.copilot import test_bridge
                    out.append(("Copilot Studio", test_bridge(db)))
                finally:
                    db.close()
            return out

        def done(results, error):
            if error:
                self._set_status(f"Test failed: {error}", "err")
                return
            if not results:
                self._set_status("Nothing to test — both folders are blank. "
                                 "That is fine; you can add them later.", "warn")
                return
            bad = [(name, r) for name, r in results if not r.get("ok")]
            if bad:
                name, result = bad[0]
                self._set_status(f"{name}: {result.get('detail') or 'did not work'}", "err")
            else:
                self.passed.add("microsoft")
                self._set_status(" · ".join(
                    f"{name}: {r.get('detail') or 'OK'}" for name, r in results), "ok")
                self._render()

        self._run_async(work, done, "Writing and reading the folders…")

    # -------------------------------------------------------------- done
    def _step_done(self):
        summary = self._summary()
        self._text(self.body,
                   "Cerebro is configured. Everything below was tested, not assumed.",
                   colour=T["dim"], pad=(0, (0, 14)))

        card = self._card(self.body)
        for label, value, good in summary:
            row = tk.Frame(card, bg=T["surface"])
            row.pack(fill="x", padx=14, pady=3)
            tk.Label(row, text="✓" if good else "○", bg=T["surface"],
                     fg=T["ok"] if good else T["faint"], font=(FONT, 11, "bold"),
                     width=2).pack(side="left")
            tk.Label(row, text=f"{label}: ", bg=T["surface"], fg=T["text"],
                     font=(FONT, 10, "bold")).pack(side="left")
            tk.Label(row, text=value, bg=T["surface"], fg=T["dim"], font=(FONT, 10),
                     anchor="w", wraplength=400, justify="left").pack(side="left", fill="x")
        tk.Frame(card, bg=T["surface"], height=10).pack()

        self.start_now = tk.BooleanVar(value=True)
        tk.Checkbutton(self.body, text="Start Cerebro and its widget now",
                       variable=self.start_now, bg=T["bg"], fg=T["text"],
                       selectcolor=T["surface2"], activebackground=T["bg"],
                       activeforeground=T["text"], font=(FONT, 10),
                       highlightthickness=0, bd=0, anchor="w").pack(fill="x", pady=(6, 0))

    def _summary(self):
        from app.core.config import settings
        provider = (settings.LLM_PROVIDER or "none").lower()
        names = {"none": "Not connected", "openai": "OpenAI", "ollama": "Ollama",
                 "qwen": "Qwen", "bedrock": "Amazon Bedrock"}
        rows = [
            ("Storage",
             "Built-in database" if settings.using_sqlite else "PostgreSQL",
             "database" in self.passed),
            ("AI model",
             names.get(provider, provider) +
             (f" · {settings.llm_model}" if provider != "none" and settings.llm_model else ""),
             "ai" in self.passed),
        ]
        microsoft = []
        if (settings.ENTERPRISE_INBOX_DIR or "").strip():
            microsoft.append("Outlook and Teams")
        if (settings.COPILOT_BRIDGE_DIR or "").strip():
            microsoft.append("Copilot Studio")
        rows.append(("Microsoft 365",
                     " and ".join(microsoft) if microsoft else "Not connected",
                     "microsoft" in self.passed))
        return rows

    def _finish(self):
        from app.core import settings_store
        try:
            settings_store.mark_setup_complete()
        except Exception:  # noqa: BLE001 - never block closing on this
            pass
        if getattr(self, "start_now", None) and self.start_now.get():
            self._launch()
        self.destroy()

    def _launch(self):
        """Start the installed Cerebro, or fall back to the dashboard."""
        import subprocess
        widget = Path(sys.executable).with_name("CerebroWidget.exe")
        if widget.exists():
            try:
                subprocess.Popen([str(widget)])
                return
            except OSError:
                pass
        try:
            webbrowser.open("http://localhost:8000")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Cerebro setup")
    parser.add_argument("--first-run", action="store_true",
                        help="launched by the installer immediately after install")
    arguments = parser.parse_args()

    try:
        wizard = Wizard(first_run=arguments.first_run)
    except tk.TclError as exc:
        sys.stderr.write(
            f"Could not open a window ({exc}).\n"
            "Configure Cerebro in a browser instead: http://localhost:8000/setup\n"
        )
        return 1
    wizard.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

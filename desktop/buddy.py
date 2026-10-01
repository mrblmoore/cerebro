"""
The desktop buddy: Cerebro's pixel mascot, out on the desktop while it works.

When Cerebro gets busy — thinking, searching, working in RightAnswers,
Dynamics or SharePoint, making a change — a small animated brain slides up in
the corner of the screen with a one-line caption of what it is doing. When the
work is done it lingers a moment and slides away. Its × hides it until the
next piece of work; clicking it opens Cerebro. "Show working buddy" in the
tray menu turns it off entirely.

It is a tiny Tkinter window on its own thread (Tk ships with Python and is
already part of the desktop app), so it never touches the main window's event
loop. On Windows the window is shaped to the sprite with a transparent colour
key, so only the brain and its caption are visible.
"""

import queue
import sys
import threading
import time
from typing import Callable, Optional

import brain_frames

#: Activity states that count as "working" (approval waits for the user and
#: idle/offline are not work, so those never summon the buddy).
BUSY_STATES = {"thinking", "searching", "browsing", "writing", "syncing", "listening", "error"}
#: How long the buddy stays after the work finishes, so short tasks are seen.
LINGER_SECONDS = 2.5
SPRITE_WIDTH = 120
KEY_COLOUR = "#ff00fe"          # never used by the art; becomes transparent
MARGIN = 16


class BuddyLogic:
    """When to show the buddy and what it shows — no Tk, so it can be tested."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.state = "idle"
        self.caption = ""
        self.busy = False
        self.last_busy: Optional[float] = None
        self.dismissed = False
        self.enabled = True

    def update(self, snapshot: dict) -> None:
        state = snapshot.get("state") or "idle"
        busy = state in BUSY_STATES
        if busy and not self.busy:
            self.dismissed = False          # a new piece of work: show again
        if busy:
            self.state = state
            self.caption = _caption(snapshot)
        if self.busy and not busy:
            self.last_busy = self.clock()
        self.busy = busy

    def dismiss(self) -> None:
        self.dismissed = True

    def visible(self) -> bool:
        if not self.enabled or self.dismissed:
            return False
        if self.busy:
            return True
        return self.last_busy is not None and self.clock() - self.last_busy < LINGER_SECONDS

    @property
    def sprite(self) -> str:
        return brain_frames.sprite_for(self.state)


def _caption(snapshot: dict) -> str:
    text = snapshot.get("detail") or snapshot.get("label") or "Working"
    return text if len(text) <= 34 else text[:33].rstrip() + "…"


class Buddy(threading.Thread):
    """The overlay window. Feed it activity snapshots with :meth:`feed`."""

    def __init__(self, on_open: Callable[[], None] = None,
                 avoid: Callable[[], Optional[tuple]] = None):
        super().__init__(name="cerebro-buddy", daemon=True)
        self.logic = BuddyLogic()
        self.on_open = on_open
        self.avoid = avoid                  # () -> (x, y, w, h) of the main window
        self._inbox: "queue.Queue[dict]" = queue.Queue()
        self._halt = threading.Event()
        self._frames = {}
        self._index = 0
        self._shown = False
        self._sliding = False
        self.root = None

    # ----------------------------------------------------------- public
    def feed(self, snapshot: dict) -> None:
        self._inbox.put(snapshot)

    def set_enabled(self, enabled: bool) -> None:
        self.logic.enabled = bool(enabled)

    def stop(self) -> None:
        self._halt.set()

    # ------------------------------------------------------------- Tk
    def run(self) -> None:
        try:
            import tkinter as tk
            from PIL import Image, ImageTk  # noqa: F401
        except ImportError:
            return                          # no Tk or Pillow: no buddy, nothing breaks
        try:
            self._build(tk)
        except tk.TclError:
            return                          # no display
        self._tick()
        self.root.mainloop()
        # Tk objects must die on the thread that made them; left to the
        # interpreter's exit they are freed from the main thread, which aborts.
        import gc

        self._frames.clear()
        self.canvas = self.caption = self.root = None
        gc.collect()

    def _build(self, tk) -> None:
        root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        background = KEY_COLOUR if sys.platform == "win32" else "#151726"
        root.configure(bg=background)
        if sys.platform == "win32":
            root.attributes("-transparentcolor", KEY_COLOUR)
        self.root, self._bg = root, background

        self.canvas = tk.Canvas(root, width=SPRITE_WIDTH, height=int(SPRITE_WIDTH * 128 / 148),
                                bg=background, highlightthickness=0, cursor="hand2")
        self.canvas.pack()
        self.sprite_item = self.canvas.create_image(0, 0, anchor="nw")
        self.close_item = self.canvas.create_text(SPRITE_WIDTH - 8, 8, text="×", fill="#e7eaf0",
                                                  font=("Segoe UI", 11, "bold"), state="hidden")
        self.caption = tk.Label(root, text="", bg="#1b1d2c", fg="#e7eaf0",
                                font=("Segoe UI", 9), padx=8, pady=3)
        self.caption.pack(pady=(2, 0))
        self.canvas.bind("<Enter>", lambda e: self.canvas.itemconfigure(self.close_item, state="normal"))
        self.canvas.bind("<Leave>", lambda e: self.canvas.itemconfigure(self.close_item, state="hidden"))
        self.canvas.bind("<Button-1>", self._click)
        self.caption.bind("<Button-1>", lambda e: self._open())
        try:
            import win_integration

            root.after(150, lambda: win_integration.make_tool_window(root))
        except ImportError:
            pass

    def _click(self, event) -> None:
        if event.x >= SPRITE_WIDTH - 18 and event.y <= 18:
            self.logic.dismiss()
        else:
            self._open()

    def _open(self) -> None:
        if self.on_open:
            threading.Thread(target=self.on_open, daemon=True).start()

    def _photo_frames(self, sprite: str):
        if sprite not in self._frames:
            from PIL import Image, ImageTk

            height = int(SPRITE_WIDTH * 128 / 148)
            key = tuple(int(KEY_COLOUR[i:i + 2], 16) for i in (1, 3, 5)) \
                if sys.platform == "win32" else (21, 23, 38)
            photos = []
            for frame in brain_frames.raw_frames(sprite):
                small = frame.resize((SPRITE_WIDTH, height), Image.BOX)
                # A colour key is all-or-nothing, so edges are made hard first.
                alpha = small.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
                flat = Image.new("RGB", small.size, key)
                flat.paste(small.convert("RGB"), mask=alpha)
                photos.append(ImageTk.PhotoImage(flat, master=self.root))
            self._frames[sprite] = photos
        return self._frames[sprite]

    def _tick(self) -> None:
        if self._halt.is_set():
            self.root.destroy()
            return
        while True:
            try:
                self.logic.update(self._inbox.get_nowait())
            except queue.Empty:
                break
        visible = self.logic.visible()
        caption = self.logic.caption or "Working…"
        if self.caption.cget("text") != caption:
            self.caption.configure(text=caption)
            if self._shown and not self._sliding:
                self._keep_in_place()
        if visible and not self._shown:
            self._show()
        elif not visible and self._shown:
            self._hide()
        delay = 120
        if self._shown:
            sprite = self.logic.sprite
            photos = self._photo_frames(sprite)
            self._index = (self._index + 1) % len(photos)
            self.canvas.itemconfigure(self.sprite_item, image=photos[self._index])
            delay = int(brain_frames.manifest()["states"][sprite]["ms"])
        self.root.after(delay, self._tick)

    def _placement(self):
        self.root.update_idletasks()
        width, height = self.root.winfo_reqwidth(), self.root.winfo_reqheight()
        left, top, right, bottom = _work_area(self.root)
        x, y = right - width - MARGIN, bottom - height - MARGIN
        rect = self.avoid() if self.avoid else None
        if rect:
            wx, wy, ww, wh = rect
            if x < wx + ww and x + width > wx and y < wy + wh and y + height > wy:
                x = max(left + MARGIN, wx - width - MARGIN)
        return x, y, bottom

    def _keep_in_place(self) -> None:
        """A longer caption widens the window; keep its right edge on screen."""
        x, y, _ = self._placement()
        self.root.geometry(f"+{x}+{y}")

    def _show(self) -> None:
        x, y, bottom = self._placement()
        self._shown = True
        self.root.geometry(f"+{x}+{bottom}")
        self.root.deiconify()
        self._slide(x, bottom, y)

    def _hide(self) -> None:
        self._shown = False
        x, y = self.root.winfo_x(), self.root.winfo_y()
        _, _, _, bottom = _work_area(self.root)
        self._slide(x, y, bottom, then=self.root.withdraw)

    def _slide(self, x: int, start: int, end: int, step: int = 0, then=None) -> None:
        steps = 8
        self._sliding = step <= steps
        if step > steps:
            if then:
                then()
            return
        t = step / steps
        eased = 1 - (1 - t) ** 3
        self.root.geometry(f"+{x}+{int(start + (end - start) * eased)}")
        self.root.after(16, lambda: self._slide(x, start, end, step + 1, then))


def _work_area(root):
    """The usable desktop (above the taskbar) as (left, top, right, bottom)."""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
            return rect.left, rect.top, rect.right, rect.bottom
        except Exception:  # noqa: BLE001
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 48

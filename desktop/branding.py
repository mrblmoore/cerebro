"""
Shared look-and-feel for the desktop pieces: the bundled font, the logo, and
the palette the widget and the setup wizard both draw from.

Kept in one place so the two windows cannot drift apart, and so the awkward
parts — finding assets in a frozen build, persuading Tkinter to use a font file
that was never installed — are solved once.
"""

import os
import sys
from pathlib import Path

#: The palette. Shared with the web UI's CSS variables, deliberately.
DARK = {
    "bg": "#12161d", "surface": "#1a1f28", "surface2": "#222834",
    "border": "#2b323f", "text": "#e7eaf0", "dim": "#9aa4b2", "faint": "#6b7484",
    "accent": "#7c74f5", "accent_text": "#ffffff",
    "ok": "#49c07d", "warn": "#e0a536", "err": "#f0705f",
    "title_bg": "#161b23",
}

LIGHT = {
    "bg": "#ffffff", "surface": "#f7f8fa", "surface2": "#eef0f4",
    "border": "#dfe3e9", "text": "#151a21", "dim": "#5c6472", "faint": "#8b93a1",
    "accent": "#4f46e5", "accent_text": "#ffffff",
    "ok": "#0f9d58", "warn": "#b7791f", "err": "#d93025",
    "title_bg": "#f2f4f7",
}

#: Set once :func:`load_fonts` has run, so callers do not each re-register.
_font_family = None


def assets_dir() -> Path:
    """Locate ``assets/`` in a source checkout or inside the frozen bundle."""
    here = Path(__file__).resolve().parent
    candidates = [
        Path(getattr(sys, "_MEIPASS", "")) / "assets",   # PyInstaller onefile
        Path(sys.executable).parent / "assets",           # next to the exe
        Path(sys.executable).parent / "_internal" / "assets",
        here.parent / "assets",                           # source checkout
        here / "assets",
    ]
    for candidate in candidates:
        if str(candidate) != "assets" and candidate.is_dir():
            return candidate
    return here.parent / "assets"


def load_fonts() -> str:
    """
    Make the bundled Inter available to Tkinter and return the family to use.

    Tkinter can only use fonts the *system* knows about, so a file sitting in
    the install directory is invisible to it. On Windows AddFontResourceEx
    registers it for this process only (FR_PRIVATE), which needs no admin
    rights and leaves nothing behind when Cerebro exits. Elsewhere there is no
    equivalent that works without installing the font system-wide, so those
    platforms fall back to the best native UI font.
    """
    global _font_family
    if _font_family is not None:
        return _font_family

    font_file = assets_dir() / "fonts" / "InterVariable.ttf"

    if sys.platform == "win32" and font_file.is_file():
        try:
            import ctypes

            FR_PRIVATE = 0x10
            added = ctypes.windll.gdi32.AddFontResourceExW(str(font_file), FR_PRIVATE, 0)
            if added:
                _font_family = "Inter Variable"
                return _font_family
        except Exception:  # noqa: BLE001 - falling back is always acceptable
            pass

    # Whatever the platform considers its interface font, newest first.
    if sys.platform == "win32":
        candidates = ["Segoe UI Variable Text", "Segoe UI"]
    elif sys.platform == "darwin":
        candidates = ["SF Pro Text", "Helvetica Neue"]
    else:
        candidates = ["Inter Variable", "Inter", "Cantarell", "DejaVu Sans"]

    try:
        from tkinter import font as tkfont

        available = set(tkfont.families())
        for name in candidates:
            if name in available:
                _font_family = name
                return _font_family
    except Exception:  # noqa: BLE001 - no display, or called before Tk starts
        pass

    # Deliberately not cached. tkfont.families() needs a live Tk root, so an
    # early call (before the window exists) cannot see the font list at all —
    # caching that answer would pin the app to the fallback for the rest of the
    # session even once Tk is up and the real font is available.
    return candidates[-1]


def logo_image(size: int = 32):
    """
    A Tk PhotoImage of the mark, or None if it cannot be loaded.

    Returns None rather than raising: a missing icon should never stop a window
    from opening. Callers must keep a reference — Tk drops un-referenced images.
    """
    try:
        import tkinter as tk

        for name in (f"cerebro-{size}.png", "cerebro-256.png"):
            path = assets_dir() / "icons" / name
            if path.is_file():
                image = tk.PhotoImage(file=str(path))
                if name != f"cerebro-{size}.png":
                    factor = max(1, image.width() // size)
                    image = image.subsample(factor, factor)
                return image
    except Exception:  # noqa: BLE001
        pass
    return None


def icon_path() -> str:
    """Path to the .ico, for window icons on Windows. Empty if unavailable."""
    for candidate in (assets_dir().parent / "packaging" / "cerebro.ico",
                      assets_dir() / "cerebro.ico",
                      Path(sys.executable).parent / "cerebro.ico"):
        if candidate.is_file():
            return str(candidate)
    return ""


def apply_window_icon(window) -> None:
    """Give a Tk window the Cerebro icon, quietly doing nothing if it cannot."""
    path = icon_path()
    if path and sys.platform == "win32":
        try:
            window.iconbitmap(path)
            return
        except Exception:  # noqa: BLE001
            pass
    image = logo_image(64)
    if image is not None:
        try:
            window.iconphoto(True, image)
            window._cerebro_icon = image   # Tk drops un-referenced images
        except Exception:  # noqa: BLE001
            pass

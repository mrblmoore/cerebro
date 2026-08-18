"""
Windows-specific window polish, applied only where it is supported.

Everything here is optional and guarded: on macOS, Linux, or an older Windows
build each function quietly does nothing and the widget still works.
"""

import ctypes
import os
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def enable_dpi_awareness() -> None:
    """Stop Windows from bitmap-scaling the widget into a blurry mess."""
    if not IS_WINDOWS:
        return
    try:
        # Per-monitor DPI awareness (Windows 8.1+)
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _hwnd(window) -> int:
    try:
        return ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
    except Exception:
        return 0


def make_tool_window(window) -> None:
    """
    Mark the widget as a tool window.

    A tool window stays out of the Alt+Tab list and off the taskbar — the right
    behaviour for a always-on-top panel that should feel like part of the desktop
    rather than another app to switch between.
    """
    if not IS_WINDOWS:
        return
    try:
        handle = _hwnd(window)
        if not handle:
            return
        style = ctypes.windll.user32.GetWindowLongW(handle, GWL_EXSTYLE)
        style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        ctypes.windll.user32.SetWindowLongW(handle, GWL_EXSTYLE, style)
    except Exception:
        pass


def round_corners(window) -> None:
    """Opt into Windows 11 rounded corners. No-op on Windows 10 and earlier."""
    if not IS_WINDOWS:
        return
    try:
        handle = _hwnd(window)
        if not handle:
            return
        preference = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            handle, DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference), ctypes.sizeof(preference),
        )
    except Exception:
        pass


def flash(window) -> None:
    """Briefly flash the window to draw attention to a context change."""
    if not IS_WINDOWS:
        return
    try:
        handle = _hwnd(window)
        if handle:
            ctypes.windll.user32.FlashWindow(handle, True)
    except Exception:
        pass


# ------------------------------------------------------------ start with Windows
def startup_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def startup_entry() -> Path:
    return startup_dir() / "Cerebro Widget.bat"


def startup_enabled() -> bool:
    return IS_WINDOWS and startup_entry().exists()


def set_startup(enabled: bool, project_root: Path) -> bool:
    """
    Add or remove the widget from the Windows startup folder.

    Returns True if the requested state was reached. Uses a .bat shim rather than
    a shortcut so no extra libraries are needed to create it.
    """
    if not IS_WINDOWS:
        return False

    entry = startup_entry()
    try:
        if not enabled:
            entry.unlink(missing_ok=True)
            return True

        entry.parent.mkdir(parents=True, exist_ok=True)
        widget_bat = project_root / "widget.bat"
        entry.write_text(
            "@echo off\r\n"
            "REM Created by the Cerebro widget — delete this file to stop it "
            "launching at sign-in.\r\n"
            f'cd /d "{project_root}"\r\n'
            f'start "" "{widget_bat}"\r\n',
            encoding="utf-8",
        )
        return True
    except OSError:
        return False

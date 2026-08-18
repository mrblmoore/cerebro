#!/usr/bin/env python3
"""
Cerebro — one command for everything.

    python cerebro.py setup      Install dependencies and prepare the workspace
    python cerebro.py start      Start the API + open the dashboard
    python cerebro.py widget     Launch the desktop widget
    python cerebro.py watch      Watch for documents you open
    python cerebro.py inbox      Import Outlook/Teams messages from the bridge folder
    python cerebro.py doctor     Diagnose a broken install
    python cerebro.py status     Check whether Cerebro is running
    python cerebro.py stop       Stop a running Cerebro

Runs on the system Python — it bootstraps its own virtual environment, so there
is nothing to activate first.
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
DESKTOP = ROOT / "desktop"
VENV = ROOT / ".venv"
MIN_PYTHON = (3, 9)

IS_WINDOWS = os.name == "nt"

EXTRAS = {
    "ai": ("requirements-ai.txt", "OpenAI support"),
    "search": ("requirements-search.txt", "Qdrant vector database"),
    "postgres": ("requirements-postgres.txt", "PostgreSQL driver"),
    "audio": (None, "Call recording and transcription"),
}

#: Installed by default — reading Word, Excel and PDF is core to what Cerebro
#: does, not an optional extra the user has to discover.
DEFAULT_REQUIREMENTS = ("requirements.txt", "requirements-documents.txt")


# --------------------------------------------------------------------- output
class Style:
    enabled = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    @classmethod
    def _wrap(cls, code, text):
        return f"\033[{code}m{text}\033[0m" if cls.enabled else text

    @classmethod
    def bold(cls, text): return cls._wrap("1", text)
    @classmethod
    def dim(cls, text): return cls._wrap("2", text)
    @classmethod
    def green(cls, text): return cls._wrap("32", text)
    @classmethod
    def yellow(cls, text): return cls._wrap("33", text)
    @classmethod
    def red(cls, text): return cls._wrap("31", text)
    @classmethod
    def blue(cls, text): return cls._wrap("36", text)


def head(text):
    print(f"\n{Style.bold(text)}")


def ok(text):
    print(f"  {Style.green('✓')} {text}")


def warn(text):
    print(f"  {Style.yellow('!')} {text}")


def fail(text):
    print(f"  {Style.red('✗')} {text}")


def step(text):
    print(f"  {Style.blue('→')} {text}")


BANNER = r"""
   ___                _
  / __|___ _ _ ___ _ | |_ _ _ ___
 | (__/ -_) '_/ -_) '_| '_/ _ \_/
  \___\___|_| \___|_| |_| \___/
"""


# ------------------------------------------------------------------ helpers
def venv_python() -> Path:
    return VENV / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")


def have_venv() -> bool:
    return venv_python().exists()


def python_for() -> Path:
    """The interpreter to run Cerebro with — the venv if present, else ours."""
    return venv_python() if have_venv() else Path(sys.executable)


def run(command, **kwargs) -> int:
    return subprocess.call([str(part) for part in command], **kwargs)


#: Set by `start --host/--port` so every other helper targets the same server.
_OVERRIDE = {"host": None, "port": None}


def api_base() -> str:
    """The URL Cerebro is (or will be) served on, honouring any CLI override."""
    host, port = "127.0.0.1", "8000"
    env_file = BACKEND / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "HOST" and value.strip():
                host = value.strip()
            elif key.strip() == "PORT" and value.strip():
                port = value.strip()
    host = _OVERRIDE["host"] or host
    port = _OVERRIDE["port"] or port
    if host in ("0.0.0.0", "::"):
        host = "localhost"
    return f"http://{host}:{port}"


def api_get(path: str, timeout: float = 2.0):
    try:
        with urllib.request.urlopen(f"{api_base()}{path}", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def is_running() -> bool:
    return api_get("/health") is not None


# -------------------------------------------------------------------- setup
def cmd_setup(args) -> int:
    print(BANNER)
    print(f"  Setting up Cerebro in {Style.dim(str(ROOT))}\n")

    head("1. Checking Python")
    if sys.version_info < MIN_PYTHON:
        fail(f"Python {'.'.join(map(str, MIN_PYTHON))}+ is required "
             f"(found {platform.python_version()}).")
        print("\n  Install a newer Python from https://python.org/downloads and re-run this.")
        return 1
    ok(f"Python {platform.python_version()} on {platform.system()}")

    head("2. Creating the virtual environment")
    if have_venv() and not args.recreate:
        ok(f"Already exists at {VENV.name}{os.sep}")
    else:
        if args.recreate and VENV.exists():
            step("Removing the old environment…")
            shutil.rmtree(VENV, ignore_errors=True)
        step(f"Creating {VENV.name}{os.sep} — this takes a few seconds…")
        try:
            venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
            ok("Virtual environment ready")
        except Exception as exc:
            fail(f"Could not create the virtual environment: {exc}")
            if platform.system() == "Linux":
                print("  On Debian/Ubuntu you may need: sudo apt install python3-venv")
            return 1

    python = venv_python()

    head("3. Installing dependencies")
    step("Upgrading pip…")
    run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
        stdout=subprocess.DEVNULL)

    step("Installing the backend…")
    if run([python, "-m", "pip", "install", "--quiet",
            "-r", BACKEND / "requirements.txt"]) != 0:
        fail("Dependency installation failed. Scroll up for the error from pip.")
        return 1
    ok("Backend dependencies installed")

    step("Installing document support (Word, Excel, PDF)…")
    if run([python, "-m", "pip", "install", "--quiet",
            "-r", BACKEND / "requirements-documents.txt"]) == 0:
        ok("Document support installed")
    else:
        warn("Document support failed to install — Cerebro still runs without it")

    step("Installing the desktop widget…")
    run([python, "-m", "pip", "install", "--quiet", "-r", DESKTOP / "requirements.txt"],
        stdout=subprocess.DEVNULL)
    ok("Desktop dependencies installed")

    for extra in args.with_extras or []:
        requirements, description = EXTRAS.get(extra, (None, None))
        if description is None:
            warn(f"Unknown extra '{extra}' — skipping")
            continue
        path = (DESKTOP / "requirements-audio.txt") if extra == "audio" else (BACKEND / requirements)
        step(f"Installing {description}…")
        if run([python, "-m", "pip", "install", "--quiet", "-r", path]) == 0:
            ok(description)
        else:
            warn(f"{description} failed to install — Cerebro still works without it")

    head("4. Preparing the workspace")
    result = subprocess.run(
        [str(python), "-c",
         "import sys; sys.path.insert(0, '.');"
         "from app.core import init_db, settings_store;"
         "init_db();"
         "print(settings_store.ensure_env_file() or 'exists')"],
        cwd=BACKEND, capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail("Could not initialise the database.")
        print(Style.dim((result.stderr or "").strip()[-1200:]))
        return 1
    ok("Database initialised")
    ok(f"Configuration at {(BACKEND / '.env').relative_to(ROOT)}")

    head("Setup complete")
    launcher = "cerebro.bat" if IS_WINDOWS else "./cerebro.sh"
    print(f"""
  Start Cerebro:      {Style.bold(f'{launcher} start')}
  Desktop widget:     {Style.bold(f'{launcher} widget')}
  Check the install:  {Style.bold(f'{launcher} doctor')}

  The first time you start it, a setup page opens in your browser.
  It takes about a minute and everything can be changed later.
""")
    return 0


# -------------------------------------------------------------------- start
def cmd_start(args) -> int:
    # Record the override first: is_running(), the browser opener and the
    # "already running" check must all point at the server we are about to start.
    if args.host:
        _OVERRIDE["host"] = args.host
    if args.port:
        _OVERRIDE["port"] = str(args.port)

    if not have_venv():
        warn("No virtual environment found — running setup first.\n")
        if cmd_setup(argparse.Namespace(recreate=False, with_extras=[])) != 0:
            return 1

    if is_running():
        print(f"Cerebro is already running at {api_base()}")
        if not args.no_browser:
            _open_browser(api_base())
        return 0

    command = [str(venv_python()), "-m", "uvicorn", "app.main:app"]
    if args.host:
        command += ["--host", args.host]
    if args.port:
        command += ["--port", str(args.port)]
    if args.reload:
        command += ["--reload"]

    if not args.no_browser:
        _schedule_browser(api_base())

    try:
        return run(command, cwd=BACKEND)
    except KeyboardInterrupt:
        print("\nCerebro stopped.")
        return 0


def _open_browser(url: str) -> None:
    import webbrowser

    info = api_get("/api/system/info") or {}
    target = url if info.get("setup_completed") else f"{url}/setup"
    try:
        webbrowser.open(target)
    except Exception:
        pass


def _schedule_browser(url: str) -> None:
    """Open the browser once the server answers, without blocking startup."""
    import threading
    import time

    def wait_and_open():
        for _ in range(40):
            if is_running():
                _open_browser(url)
                return
            time.sleep(0.5)

    threading.Thread(target=wait_and_open, daemon=True).start()


# ------------------------------------------------------------------- widget
def cmd_widget(args) -> int:
    if not is_running():
        warn(f"Cerebro's API is not responding at {api_base()}.")
        print(f"  The widget will keep retrying — start the API with: "
              f"{'cerebro.bat' if IS_WINDOWS else './cerebro.sh'} start\n")

    command = [str(python_for()), str(DESKTOP / "widget.py")]
    if args.api:
        command += ["--api", args.api]
    return run(command)


# ------------------------------------------------------- watch / inbox
def cmd_watch(args) -> int:
    """Run the document watcher, which notices the files you open."""
    if not is_running():
        warn(f"Cerebro's API is not responding at {api_base()}.")
        print("  The watcher will keep retrying.\n")

    command = [str(python_for()), str(DESKTOP / "document_watcher.py"),
               "--api", args.api or api_base()]
    for folder in args.folders or []:
        command += ["--folder", folder]
    return run(command)


def cmd_inbox(args) -> int:
    """Import Outlook/Teams JSON files written by Power Automate."""
    command = [str(python_for()), str(BACKEND / "enterprise_ingest.py")]
    if args.folder:
        command.append(args.folder)
    if args.watch:
        command.append("--watch")
    if args.api:
        command += ["--api", args.api]
    return run(command, cwd=ROOT)


# ------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    print(BANNER)
    head("Environment")
    problems = []

    if sys.version_info >= MIN_PYTHON:
        ok(f"Python {platform.python_version()}")
    else:
        fail(f"Python {platform.python_version()} — needs {'.'.join(map(str, MIN_PYTHON))}+")
        problems.append("Install Python 3.9 or newer.")

    if have_venv():
        ok(f"Virtual environment at {VENV.name}{os.sep}")
    else:
        fail("No virtual environment")
        problems.append("Run: python cerebro.py setup")

    try:
        import tkinter  # noqa: F401
        ok("Tkinter available (needed by the desktop widget)")
    except ImportError:
        warn("Tkinter is missing — the desktop widget will not start")
        if platform.system() == "Linux":
            problems.append("Install Tkinter: sudo apt install python3-tk")
        else:
            problems.append("Reinstall Python with the Tcl/Tk option enabled.")

    head("Dependencies")
    if have_venv():
        result = subprocess.run(
            [str(venv_python()), "-c",
             "import importlib.util as u, json;"
             "print(json.dumps({n: u.find_spec(n) is not None for n in "
             "['fastapi','uvicorn','pydantic_settings','sqlalchemy','requests','openai',"
             "'qdrant_client','docx','openpyxl','pypdf']}))"],
            capture_output=True, text=True,
        )
        try:
            found = json.loads(result.stdout)
        except (ValueError, TypeError):
            found = {}

        for name in ("fastapi", "uvicorn", "pydantic_settings", "sqlalchemy", "requests"):
            if found.get(name):
                ok(name)
            else:
                fail(f"{name} is missing")
                problems.append("Reinstall dependencies: python cerebro.py setup --recreate")
        for name in ("docx", "openpyxl", "pypdf"):
            if found.get(name):
                ok(f"{name} (document reading)")
            else:
                warn(f"{name} is missing — Cerebro cannot read some document types")
                problems.append("Install document support: pip install -r "
                                "backend/requirements-documents.txt")
        for name, extra in (("openai", "ai"), ("qdrant_client", "search")):
            print(f"  {Style.dim('○') if not found.get(name) else Style.green('✓')} "
                  f"{name} {Style.dim('(optional)' if not found.get(name) else '')}")
    else:
        warn("Skipped — no virtual environment yet")

    head("Service")
    diagnostics = api_get("/api/system/diagnostics", timeout=4)
    if diagnostics is None:
        warn(f"Cerebro is not running at {api_base()}")
        print(f"  Start it with: {'cerebro.bat' if IS_WINDOWS else './cerebro.sh'} start")
    else:
        for check in diagnostics["checks"]:
            if check.get("optional_package"):
                continue
            (ok if check["ok"] else (fail if check.get("required") else warn))(
                f"{check['label']}: {check.get('detail', '')}"
            )
            if not check["ok"] and check.get("required"):
                problems.append(f"Fix {check['label'].lower()}: {check.get('detail', '')}")

    head("Summary")
    if problems:
        for problem in dict.fromkeys(problems):
            print(f"  {Style.yellow('→')} {problem}")
        print()
        return 1

    print(f"  {Style.green('Everything looks healthy.')}\n")
    return 0


# ------------------------------------------------------------ status / stop
def cmd_status(args) -> int:
    info = api_get("/api/system/info", timeout=3)
    if info is None:
        print(f"{Style.yellow('stopped')} — nothing responding at {api_base()}")
        return 1

    context = api_get("/api/context/current") or {}
    print(f"{Style.green('running')} at {api_base()}  (v{info.get('version')})")
    print(f"  setup complete : {'yes' if info.get('setup_completed') else Style.yellow('no — open /setup')}")
    print(f"  ai generation  : {'enabled' if info.get('ai_enabled') else 'disabled'}")
    print(f"  current case   : {context.get('crm_case') or '—'}")
    print(f"  customer       : {context.get('customer') or '—'}")

    bridge = api_get("/api/enterprise/status") or {}
    if bridge.get("enabled"):
        print(f"  outlook/teams  : {bridge.get('messages', 0)} message(s), "
              f"{bridge.get('pending', 0)} waiting")
    documents = api_get("/api/documents?limit=1") or {}
    if documents.get("documents"):
        print(f"  last document  : {documents['documents'][0]['name']}")
    return 0


def cmd_stop(args) -> int:
    if not is_running():
        print("Cerebro is not running.")
        return 0

    pattern = "uvicorn app.main:app"
    if IS_WINDOWS:
        print("Stop Cerebro by closing its window, or press Ctrl+C in it.")
        return 0

    subprocess.call(["pkill", "-f", pattern])
    print("Stop signal sent.")
    return 0


# --------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cerebro",
        description="Cerebro — local-first operational copilot for technical support.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'python cerebro.py setup' first if you have not already.",
    )
    subparsers = parser.add_subparsers(dest="command")

    setup = subparsers.add_parser("setup", help="install dependencies and prepare the workspace")
    setup.add_argument("--recreate", action="store_true",
                       help="delete and rebuild the virtual environment")
    setup.add_argument("--with", dest="with_extras", action="append", choices=sorted(EXTRAS),
                       help="install an optional extra (repeatable)")
    setup.set_defaults(func=cmd_setup)

    start = subparsers.add_parser("start", help="start the API and open the dashboard")
    start.add_argument("--host")
    start.add_argument("--port", type=int)
    start.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    start.add_argument("--no-browser", action="store_true", help="do not open a browser")
    start.set_defaults(func=cmd_start)

    widget = subparsers.add_parser("widget", help="launch the desktop widget")
    widget.add_argument("--api", help="API base URL (default: from .env)")
    widget.set_defaults(func=cmd_widget)

    watch = subparsers.add_parser("watch", help="watch for documents you open")
    watch.add_argument("--api", help="API base URL (default: from .env)")
    watch.add_argument("--folder", action="append", dest="folders",
                       help="folder to watch (repeatable)")
    watch.set_defaults(func=cmd_watch)

    inbox = subparsers.add_parser(
        "inbox", help="import Outlook/Teams messages from the bridge folder")
    inbox.add_argument("folder", nargs="?", help="folder to read (default: from settings)")
    inbox.add_argument("--watch", action="store_true", help="keep watching for new files")
    inbox.add_argument("--api", help="send to a Cerebro at this URL instead of "
                                     "the local database")
    inbox.set_defaults(func=cmd_inbox)

    subparsers.add_parser("doctor", help="diagnose problems").set_defaults(func=cmd_doctor)
    subparsers.add_parser("status", help="show whether Cerebro is running").set_defaults(func=cmd_status)
    subparsers.add_parser("stop", help="stop a running Cerebro").set_defaults(func=cmd_stop)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

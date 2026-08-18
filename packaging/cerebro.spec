# PyInstaller build for Cerebro.
#
#   pyinstaller packaging/cerebro.spec --noconfirm
#
# Produces two executables in dist/Cerebro/:
#   Cerebro.exe        console app that runs the API and opens the dashboard
#   CerebroWidget.exe  windowed app, the always-on-top panel
#
# They share one bundle directory, so the Python runtime and libraries are
# included once rather than twice.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
BACKEND = ROOT / "backend"
DESKTOP = ROOT / "desktop"

block_cipher = None

# The web UI is read at runtime from app/web, so it must be shipped as data.
datas = [
    (str(BACKEND / "app" / "web"), "app/web"),
    (str(ROOT / "browser-extension" / "src"), "browser-extension"),
    (str(BACKEND / ".env.example"), "."),
]
for name in ("README.md", "GETTING_STARTED.md"):
    if (ROOT / name).exists():
        datas.append((str(ROOT / name), "."))
for name in ("INSTALL.md", "CONFIGURATION.md", "WIDGET.md", "POWER_AUTOMATE.md"):
    if (ROOT / "docs" / name).exists():
        datas.append((str(ROOT / "docs" / name), "docs"))

# Uvicorn, SQLAlchemy dialects and the optional document libraries are reached
# through dynamic imports, which PyInstaller's static analysis cannot see.
hiddenimports = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "sqlalchemy.dialects.sqlite",
    "app.main", "app.models", "app.api",
    "docx", "openpyxl", "pptx", "pypdf",
    # Every service is pinned here rather than left to static discovery: many are
    # imported lazily inside functions (to keep optional deps optional), which is
    # exactly the pattern PyInstaller's analysis can miss in a frozen build.
    "app.services.activity_service", "app.services.context_engine",
    "app.services.copilot_bridge", "app.services.copilot_guide",
    "app.services.document_editors", "app.services.document_readers",
    "app.services.document_service", "app.services.embeddings",
    "app.services.enterprise_service", "app.services.event_detector",
    "app.services.llm_service", "app.services.memory_service",
    "app.services.nudge_service", "app.services.rag_service",
    "app.services.redaction", "app.services.screenpipe_client",
    "app.services.style_service", "app.services.task_executors",
    "app.services.task_service", "app.services.watchers",
]

excludes = ["tkinter"]   # the server build has no interface

server = Analysis(
    [str(ROOT / "packaging" / "cerebro_app.py")],
    pathex=[str(BACKEND), str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
)

widget = Analysis(
    [str(ROOT / "packaging" / "cerebro_widget_app.py")],
    pathex=[str(DESKTOP), str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=["widget", "widget_config", "win_integration"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["fastapi", "uvicorn", "sqlalchemy"],
    cipher=block_cipher,
)

MERGE((server, "cerebro_app", "Cerebro"), (widget, "cerebro_widget_app", "CerebroWidget"))

server_pyz = PYZ(server.pure, server.zipped_data, cipher=block_cipher)
server_exe = EXE(
    server_pyz, server.scripts, [],
    exclude_binaries=True,
    name="Cerebro",
    console=True,
    icon=str(ROOT / "packaging" / "cerebro.ico") if (ROOT / "packaging" / "cerebro.ico").exists() else None,
    version=str(ROOT / "packaging" / "version_info.txt") if (ROOT / "packaging" / "version_info.txt").exists() else None,
)

widget_pyz = PYZ(widget.pure, widget.zipped_data, cipher=block_cipher)
widget_exe = EXE(
    widget_pyz, widget.scripts, [],
    exclude_binaries=True,
    name="CerebroWidget",
    console=False,              # no console window for the widget
    icon=str(ROOT / "packaging" / "cerebro.ico") if (ROOT / "packaging" / "cerebro.ico").exists() else None,
)

COLLECT(
    server_exe, server.binaries, server.zipfiles, server.datas,
    widget_exe, widget.binaries, widget.zipfiles, widget.datas,
    strip=False,
    upx=False,
    name="Cerebro",
)

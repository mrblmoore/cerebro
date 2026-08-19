"""
Everything the setup flow needs to know about whether this install is healthy.

Shared by the native wizard that the Windows installer runs and by the browser
wizard, so both ask exactly the same questions and offer the same repairs. The
rule throughout: never just report a problem — say what it means and, where
Cerebro can, fix it in place.
"""

import importlib.util
import subprocess
import sys
from typing import Any, Dict, List, Optional

from app.core import logger
from app.core.paths import FROZEN, PROJECT_ROOT

#: Optional feature groups, each mapped to the modules that prove it installed
#: and the requirements file that repairs it.
#:
#: ``required`` groups make Cerebro unusable when absent; the rest only switch
#: a feature off. Setup installs all of them, so anything missing here means
#: either a --minimal install or a wheel that would not build on this machine.
COMPONENTS: Dict[str, Dict[str, Any]] = {
    "core": {
        "label": "Cerebro core",
        "modules": ["fastapi", "uvicorn", "sqlalchemy", "pydantic_settings", "requests"],
        "requirements": "backend/requirements.txt",
        "required": True,
        "why": "The API, database layer and web interface.",
    },
    "documents": {
        "label": "Word, Excel, PowerPoint and PDF",
        "modules": ["docx", "openpyxl", "pptx", "pypdf"],
        "requirements": "backend/requirements-documents.txt",
        "required": False,
        "why": "Reading and editing the documents you open.",
    },
    "openai": {
        "label": "OpenAI",
        "modules": ["openai"],
        "requirements": "backend/requirements-ai.txt",
        "required": False,
        "why": "Needed only if you pick OpenAI as your AI provider.",
    },
    "bedrock": {
        "label": "Amazon Bedrock",
        # awscrt is listed because botocore needs it to sign SigV4a requests,
        # which every cross-Region inference profile uses. boto3 alone looks
        # installed and then fails at the first call.
        "modules": ["boto3", "awscrt"],
        "requirements": "backend/requirements-ai.txt",
        "required": False,
        "why": "Needed only if you pick Amazon Bedrock as your AI provider.",
    },
    "postgres": {
        "label": "PostgreSQL driver",
        "modules": ["psycopg"],
        "requirements": "backend/requirements-postgres.txt",
        "required": False,
        "why": "Only if you keep Cerebro's data in PostgreSQL instead of the built-in database.",
    },
    "search": {
        "label": "Qdrant vector search",
        "modules": ["qdrant_client"],
        "requirements": "backend/requirements-search.txt",
        "required": False,
        "why": "Only for large knowledge bases; the built-in search needs nothing.",
    },
    "capture": {
        "label": "Activity capture",
        "modules": ["mss", "PIL", "pynput"],
        "requirements": "desktop/requirements-capture.txt",
        "required": False,
        "why": "Screenshots and typed text for the second-brain memory.",
    },
}


def _module_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def missing_modules(component: str) -> List[str]:
    """Which of a component's modules are absent."""
    spec = COMPONENTS.get(component)
    if not spec:
        return []
    return [name for name in spec["modules"] if not _module_present(name)]


def component_status(component: str) -> Dict[str, Any]:
    spec = COMPONENTS[component]
    absent = missing_modules(component)
    return {
        "component": component,
        "label": spec["label"],
        "why": spec["why"],
        "required": spec["required"],
        "ok": not absent,
        "missing": absent,
        "repairable": bool(absent) and not FROZEN,
    }


def preflight() -> Dict[str, Any]:
    """Every dependency group, with enough detail to explain itself."""
    checks = [component_status(name) for name in COMPONENTS]
    blocking = [c for c in checks if c["required"] and not c["ok"]]
    return {
        "ok": not blocking,
        "checks": checks,
        "frozen": FROZEN,
        "python": sys.version.split()[0],
    }


def repair(component: str) -> Dict[str, Any]:
    """
    Install a missing component into the interpreter Cerebro is actually running.

    This is the whole point of doing it here rather than telling someone to run
    pip: a plain ``pip install`` at a prompt targets system Python, while Cerebro
    runs from its own virtual environment, so the package appears to install and
    changes nothing.
    """
    spec = COMPONENTS.get(component)
    if not spec:
        return {"ok": False, "detail": f"Unknown component: {component}"}

    if not missing_modules(component):
        return {"ok": True, "detail": f"{spec['label']} is already installed."}

    if FROZEN:
        # A packaged build has no pip and no source tree to install from.
        return {
            "ok": False,
            "detail": f"{spec['label']} is missing from this build. Reinstalling "
                      "Cerebro from the latest installer will restore it.",
        }

    requirements = PROJECT_ROOT / spec["requirements"]
    if not requirements.exists():
        return {"ok": False,
                "detail": f"Cannot repair: {spec['requirements']} is not in this install."}

    logger.info("setup", "Repairing component", {"component": component})
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True, text=True, timeout=900,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "detail": f"Could not run the installer: {exc}"}

    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-400:]
        return {"ok": False,
                "detail": f"Install failed. {tail}" if tail else "Install failed."}

    # find_spec caches negative results per interpreter run; clear it so a
    # just-installed package is visible without restarting Cerebro.
    importlib.invalidate_caches()
    still_absent = missing_modules(component)
    if still_absent:
        return {"ok": False,
                "detail": f"Installed, but {', '.join(still_absent)} still cannot be "
                          "imported. Restart Cerebro and try again."}
    return {"ok": True, "detail": f"{spec['label']} installed."}


def component_for_provider(provider: str) -> Optional[str]:
    """Which dependency group an AI provider needs, if any."""
    return {"openai": "openai", "bedrock": "bedrock"}.get((provider or "").lower())

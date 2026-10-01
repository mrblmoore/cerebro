"""Hidden-browser integrations (Playwright): the engine, connectors, and registry."""

from typing import Dict, List

from app.services.browser.connector import (  # noqa: F401
    BrowserConnector, NotConfigured, SignInRequired)
from app.services.browser.engine import BrowserUnavailable, engine  # noqa: F401

_CONNECTORS: Dict[str, BrowserConnector] = {}


def register(connector: BrowserConnector) -> BrowserConnector:
    _CONNECTORS[connector.name] = connector
    return connector


def connectors() -> List[BrowserConnector]:
    _load()
    return list(_CONNECTORS.values())


def get(name: str) -> BrowserConnector:
    _load()
    if name not in _CONNECTORS:
        raise KeyError(name)
    return _CONNECTORS[name]


def _load() -> None:
    """Import the built-in connectors once (they register themselves)."""
    if _CONNECTORS:
        return
    from app.services.browser import dynamics, rightanswers  # noqa: F401

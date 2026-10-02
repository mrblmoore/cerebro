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


_LOADED = False


def _load() -> None:
    """Import the built-in connectors once (they register themselves).

    A flag rather than "is the registry empty?": importing one connector
    module directly registers just that one.
    """
    global _LOADED
    if _LOADED:
        return
    from app.services.browser import dynamics, outlook, rightanswers, sharepoint, teams  # noqa: F401
    _LOADED = True

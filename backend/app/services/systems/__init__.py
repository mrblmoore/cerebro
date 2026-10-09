"""API-based system connectors (BeyondTrust, Genesys Cloud)."""

from typing import List

from app.services.systems.base import ApiConnector


def connectors() -> List[ApiConnector]:
    from app.services.systems import beyondtrust, genesys

    return [beyondtrust.connector, genesys.connector]


def get(name: str) -> ApiConnector:
    for connector in connectors():
        if connector.name == name:
            return connector
    raise KeyError(name)


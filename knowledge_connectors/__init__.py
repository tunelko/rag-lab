"""Offline-testable connectors for public cybersecurity knowledge sources."""
from .common import ConnectorError, HTTPClient, SyncResult
from .cve import CVEListConnector
from .epss import EPSSConnector
from .kev import KEVConnector


CONNECTORS = {
    "kev": KEVConnector,
    "epss": EPSSConnector,
    "cve": CVEListConnector,
}

__all__ = [
    "CONNECTORS", "CVEListConnector", "ConnectorError", "EPSSConnector",
    "HTTPClient", "KEVConnector", "SyncResult",
]

"""Data-source adapters; Zotero writes require preview plus explicit consent."""

from .base import ConnectionResult, IntegrationError, SearchPreviewItem
from .ima import ImaConnector
from .library import LibraryConnector, infer_access_route
from .zotero import ZoteroConnector, ZoteroWriteProposal

__all__ = [
    "ConnectionResult",
    "ImaConnector",
    "IntegrationError",
    "LibraryConnector",
    "SearchPreviewItem",
    "ZoteroConnector",
    "ZoteroWriteProposal",
    "infer_access_route",
]

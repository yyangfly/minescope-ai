from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RawRecord:
    source: str
    category: str
    external_id: str
    title: str
    body: str
    url: str
    published_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Document:
    id: str
    source: str
    category: str
    title: str
    body: str
    url: str
    published_at: datetime
    content_hash: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchHit:
    document: Document
    score: float

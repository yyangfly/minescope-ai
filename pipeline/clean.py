from __future__ import annotations

import hashlib

from pipeline.models import Document, RawRecord
from pipeline.text import canonical_url, content_hash, normalize_text


def document_id(record: RawRecord) -> str:
    if record.category == "price":
        stable = record.external_id
    else:
        stable = canonical_url(record.url) or record.external_id or content_hash(record.title, record.body)
    digest = hashlib.sha1(f"{record.category}:{stable}".encode("utf-8")).hexdigest()[:20]
    return f"{record.category}:{digest}"


def clean_record(record: RawRecord) -> Document | None:
    title = normalize_text(record.title)
    body = normalize_text(record.body)
    url = canonical_url(record.url)
    if not title and not body:
        return None
    digest = content_hash(record.category, title, body, url)
    return Document(
        id=document_id(record),
        source=record.source,
        category=record.category,
        title=title or body[:120],
        body=body,
        url=url,
        published_at=record.published_at,
        content_hash=digest,
        metadata=record.metadata,
    )


def clean_and_dedupe(records: list[RawRecord]) -> list[Document]:
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    docs: list[Document] = []
    for record in records:
        doc = clean_record(record)
        if doc is None:
            continue
        key = doc.id if doc.category == "price" else doc.url or doc.content_hash
        if doc.id in seen_ids or key in seen_hashes:
            continue
        seen_ids.add(doc.id)
        seen_hashes.add(key)
        docs.append(doc)
    return docs

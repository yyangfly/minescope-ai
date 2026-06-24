from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from pipeline.embedding import HashEmbedding, cosine
from pipeline.models import Document, SearchHit
from pipeline.text import tokenize


class SQLiteVectorStore:
    def __init__(self, path: str | Path, embedding: HashEmbedding | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.embedding = embedding or HashEmbedding()
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                url TEXT NOT NULL,
                published_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                vector_json TEXT NOT NULL,
                inserted_at TEXT NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_category_date ON documents(category, published_at)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash)")
        self.conn.commit()

    def upsert_many(self, docs: list[Document]) -> int:
        rows = []
        now = datetime.now(timezone.utc).isoformat()
        for doc in docs:
            text = f"{doc.title}\n{doc.body}\n{json.dumps(doc.metadata, ensure_ascii=False, sort_keys=True)}"
            rows.append(
                (
                    doc.id,
                    doc.source,
                    doc.category,
                    doc.title,
                    doc.body,
                    doc.url,
                    doc.published_at.astimezone(timezone.utc).isoformat(),
                    doc.content_hash,
                    json.dumps(doc.metadata, ensure_ascii=False, sort_keys=True),
                    json.dumps(self.embedding.embed(text), separators=(",", ":")),
                    now,
                )
            )
        self.conn.executemany(
            """
            INSERT INTO documents
            (id, source, category, title, body, url, published_at, content_hash, metadata_json, vector_json, inserted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                source=excluded.source,
                category=excluded.category,
                title=excluded.title,
                body=excluded.body,
                url=excluded.url,
                published_at=excluded.published_at,
                content_hash=excluded.content_hash,
                metadata_json=excluded.metadata_json,
                vector_json=excluded.vector_json
            """,
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count_by_category(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT category, COUNT(*) AS n FROM documents GROUP BY category").fetchall()
        return {row["category"]: int(row["n"]) for row in rows}

    def stats(self) -> dict:
        by_category = self.count_by_category()
        by_source_rows = self.conn.execute("SELECT source, category, COUNT(*) AS n FROM documents GROUP BY source, category").fetchall()
        fixture_rows = self.conn.execute("SELECT metadata_json FROM documents").fetchall()
        fixture_count = sum(1 for row in fixture_rows if '"fixture": true' in row["metadata_json"])
        total = sum(by_category.values())
        return {
            "total": total,
            "by_category": by_category,
            "by_source": [
                {"source": row["source"], "category": row["category"], "count": int(row["n"])}
                for row in by_source_rows
            ],
            "fixture_count": fixture_count,
            "real_count": total - fixture_count,
            "mode": "fixture" if fixture_count and fixture_count == total else "mixed" if fixture_count else "real",
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
        categories: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[SearchHit]:
        where = []
        params: list[str] = []
        if categories:
            where.append(f"category IN ({','.join('?' for _ in categories)})")
            params.extend(categories)
        if start_date:
            where.append("published_at >= ?")
            params.append(start_date)
        if end_date:
            where.append("published_at <= ?")
            params.append(end_date)
        sql = "SELECT * FROM documents"
        if where:
            sql += " WHERE " + " AND ".join(where)

        query_vector = self.embedding.embed(query)
        query_terms = set(tokenize(query))
        known_tickers = {"bhp", "rio", "vale", "fcx", "scco", "alb", "sqm", "lit", "copx", "pick", "glncy", "nem"}
        query_tickers = query_terms & known_tickers
        mineral_terms = {"lithium", "copper", "nickel", "iron", "ore", "rare", "earth"}
        query_minerals = query_terms & mineral_terms
        scored: list[SearchHit] = []
        for row in self.conn.execute(sql, params).fetchall():
            vector = json.loads(row["vector_json"])
            doc = Document(
                id=row["id"],
                source=row["source"],
                category=row["category"],
                title=row["title"],
                body=row["body"],
                url=row["url"],
                published_at=datetime.fromisoformat(row["published_at"]),
                content_hash=row["content_hash"],
                metadata=json.loads(row["metadata_json"]),
            )
            text = f"{doc.title} {doc.body} {json.dumps(doc.metadata, ensure_ascii=False)}".lower()
            lexical_matches = sum(1 for term in query_terms if term.lower() in text)
            lexical_bonus = min(0.45, lexical_matches * 0.06)
            title_bonus = 0.12 if any(term.lower() in doc.title.lower() for term in query_terms) else 0.0
            business_bonus = 0.0
            symbol = str(doc.metadata.get("symbol", "")).lower()
            if query_tickers and doc.category == "price":
                business_bonus += 1.0 if symbol in query_tickers else -0.35
                if symbol in query_tickers:
                    days_old = max(0, (datetime.now(timezone.utc) - doc.published_at.astimezone(timezone.utc)).days)
                    business_bonus += max(0.0, 0.45 - days_old * 0.015)
            if query_minerals:
                has_mineral = any(term in text for term in query_minerals)
                business_bonus += 0.3 if has_mineral else -0.2
            scored.append(SearchHit(document=doc, score=cosine(query_vector, vector) + lexical_bonus + title_bonus + business_bonus))
        scored.sort(key=lambda hit: hit.score, reverse=True)
        return scored[:top_k]

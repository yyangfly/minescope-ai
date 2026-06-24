from __future__ import annotations

import argparse
from collections import Counter

from pipeline.clean import clean_and_dedupe
from pipeline.sources import default_sources, fixture_records
from pipeline.vector_store import SQLiteVectorStore


def run_ingest(db_path: str, limit_per_source: int = 250, offline_fixture: bool = False) -> dict[str, int]:
    if offline_fixture:
        raw_records = fixture_records(per_category=200)
    else:
        raw_records = []
        for source in default_sources(limit_per_source):
            try:
                raw_records.extend(source.collect())
            except Exception as exc:  # External APIs can rate-limit; keep the rest of the run usable.
                print(f"Source failed: {source.__class__.__name__}: {exc}")

    docs = clean_and_dedupe(raw_records)
    store = SQLiteVectorStore(db_path)
    try:
        store.upsert_many(docs)
        counts = store.count_by_category()
    finally:
        store.close()

    raw_counts = Counter(record.category for record in raw_records)
    print("Raw counts:", dict(raw_counts))
    print("Stored counts:", counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect mining intelligence data and build the vector store.")
    parser.add_argument("--db", default="data/mining_knowledge.sqlite", help="SQLite vector DB path")
    parser.add_argument("--limit-per-source", type=int, default=250)
    parser.add_argument("--offline-fixture", action="store_true", help="Generate deterministic 600-row fixture data")
    args = parser.parse_args()
    run_ingest(args.db, args.limit_per_source, args.offline_fixture)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json

from pipeline.vector_store import SQLiteVectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Print corpus source/category statistics.")
    parser.add_argument("--db", default="data/mining_knowledge.sqlite")
    args = parser.parse_args()

    store = SQLiteVectorStore(args.db)
    try:
        print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
    finally:
        store.close()


if __name__ == "__main__":
    main()

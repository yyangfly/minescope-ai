from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serve.query_engine import answer_question


def load_ground_truth(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def recall_at_k(item: dict, citations: list[dict], k: int = 5) -> float:
    top = citations[:k]
    expected_ids = set(item.get("expected_doc_ids") or [])
    if expected_ids:
        found = {hit["id"] for hit in top}
        return len(expected_ids & found) / max(len(expected_ids), 1)

    keywords = [keyword.lower() for keyword in item.get("expected_keywords", [])]
    if not keywords:
        return 0.0
    evidence = " ".join(
        f"{hit.get('title', '')} {hit.get('snippet', '')} {json.dumps(hit.get('metadata', {}), ensure_ascii=False)}"
        for hit in top
    ).lower()
    matched = sum(1 for keyword in keywords if keyword in evidence)
    return matched / len(keywords)


def faithfulness(answer: str, citations: list[dict]) -> float:
    evidence = " ".join(f"{hit.get('title', '')} {hit.get('snippet', '')}" for hit in citations).lower()
    evidence_tokens = set(re.findall(r"[a-zA-Z0-9]+", evidence))
    sentences = [sentence.strip() for sentence in re.split(r"[\n。.!?]+", answer) if sentence.strip()]
    if not sentences:
        return 0.0
    faithful = 0
    for sentence in sentences:
        tokens = set(re.findall(r"[a-zA-Z0-9]+", sentence.lower()))
        content_tokens = {token for token in tokens if len(token) > 2}
        if not content_tokens or content_tokens & evidence_tokens:
            faithful += 1
    return faithful / len(sentences)


def run(db_path: str, gt_path: str, top_k: int = 5) -> dict:
    rows = []
    for item in load_ground_truth(gt_path):
        result = answer_question(db_path, item["question"], top_k=top_k)
        rows.append(
            {
                "id": item["id"],
                "recall_at_5": recall_at_k(item, result["citations"], 5),
                "faithfulness": faithfulness(result["answer"], result["citations"]),
                "answer": result["answer"],
            }
        )
    summary = {
        "n": len(rows),
        "mean_recall_at_5": sum(row["recall_at_5"] for row in rows) / max(len(rows), 1),
        "mean_faithfulness": sum(row["faithfulness"] for row in rows) / max(len(rows), 1),
        "rows": rows,
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate retrieval recall@5 and answer faithfulness.")
    parser.add_argument("--db", default="data/mining_knowledge.sqlite")
    parser.add_argument("--ground-truth", default="eval/ground_truth.jsonl")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    summary = run(args.db, args.ground_truth, args.top_k)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

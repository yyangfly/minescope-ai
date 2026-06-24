from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eval.run_eval import run
from pipeline.ingest import run_ingest
from serve.query_engine import answer_question


class SmokeTest(unittest.TestCase):
    def test_fixture_ingest_query_and_eval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "fixture.sqlite")
            counts = run_ingest(db, offline_fixture=True)
            self.assertGreaterEqual(counts.get("news", 0), 200)
            self.assertGreaterEqual(counts.get("policy", 0), 200)
            self.assertGreaterEqual(counts.get("price", 0), 200)

            result = answer_question(db, "近 7 天澳洲锂出口政策有何变化?", top_k=5)
            self.assertTrue(result["citations"])
            self.assertIn("answer", result)

            summary = run(db, "eval/ground_truth.jsonl", top_k=5)
            self.assertEqual(summary["n"], 20)
            self.assertGreaterEqual(summary["mean_faithfulness"], 0.5)


if __name__ == "__main__":
    unittest.main()

# DATA_NOTES

## Scope

This project builds a three-source mining intelligence corpus for the last 30-45 days:

- `news`: mining and critical-minerals news from GDELT DOC 2.1.
- `policy`: government and regulator material from GDELT official domains plus the Federal Register API.
- `price`: daily price observations for mining equities and ETFs from Yahoo Finance chart data.

The target production run is at least 200 records per category, 600+ total records. Use:

```powershell
python -m pipeline.ingest --db data/mining_knowledge.sqlite --limit-per-source 250
```

For offline validation, use deterministic fixture data. Fixture rows are marked with `metadata.fixture=true`; do not present them as real data:

```powershell
python -m pipeline.ingest --db data/mining_knowledge.sqlite --offline-fixture
```

To inspect the active corpus composition:

```powershell
python -m pipeline.inspect_db --db data/mining_knowledge.sqlite
```

## Stored Schema

All records are normalized into the `documents` SQLite table:

| Field | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PRIMARY KEY | Stable id generated as `{category}:{sha1(category + stable external key)[:20]}` |
| `source` | TEXT | Concrete collector name, e.g. `gdelt_news`, `federal_register`, `yahoo_chart` |
| `category` | TEXT | One of `news`, `policy`, `price` |
| `title` | TEXT | Normalized title or compact generated title for price bars |
| `body` | TEXT | Cleaned text used for retrieval evidence |
| `url` | TEXT | Canonical URL, with tracking parameters stripped |
| `published_at` | TEXT | UTC ISO-8601 timestamp |
| `content_hash` | TEXT | SHA-256 hash of normalized category/title/body/url |
| `metadata_json` | TEXT | Source-specific metadata as JSON |
| `vector_json` | TEXT | L2-normalized local hash embedding |
| `inserted_at` | TEXT | UTC ingestion timestamp |

## Primary Keys

- News and policy records use canonical URL when available. URL canonicalization lowercases scheme/host, strips fragments, trims trailing slashes, and removes common tracking parameters.
- Federal Register records prefer `document_number`.
- Price records use `{symbol}:{date}` from the daily bar, making each symbol-day one record.

## Deduplication

Deduplication happens before insertion:

1. Clean text with Unicode NFKC normalization, HTML stripping, and whitespace compaction.
2. Generate a stable document id from category and canonical external key.
3. Generate `content_hash` from normalized category, title, body, and URL.
4. Drop duplicates if either the document id or canonical URL/content hash has already appeared in the batch.
5. SQLite `ON CONFLICT(id) DO UPDATE` makes repeated ingestion idempotent.

## Vector Strategy

The vector store is intentionally dependency-light for a 24-hour assignment:

- Embeddings are deterministic 384-dimensional signed hash vectors.
- Query text is expanded with a small Chinese-to-English mining vocabulary, so questions such as `近 7 天澳洲锂出口政策有何变化?` retrieve English evidence for Australia, lithium, export, and policy.
- Search loads the filtered candidate set from SQLite and ranks by cosine similarity.

This can be swapped later for OpenAI embeddings, SentenceTransformers, Chroma, Qdrant, or pgvector without changing the ingestion schema materially.

## Evaluation

`eval/ground_truth.jsonl` contains 20 natural-language Q&A cases. `eval/run_eval.py` reports:

- `recall@5`: exact expected doc-id recall when `expected_doc_ids` are supplied; otherwise keyword recall over top-5 evidence, which is useful for dynamic live-source data.
- `answer faithfulness`: a lightweight evidence-overlap check that each generated answer sentence is supported by retrieved citations.

## Natural-Language Answering

The system uses a RAG pattern. Retrieval is local and deterministic; answer generation is optional.

- With `LLM_API_KEY` or `OPENAI_API_KEY` configured, `/query` sends the top-k citations to an OpenAI-compatible `/chat/completions` endpoint and asks the model to answer only from evidence.
- Without an LLM key, `/query` falls back to a deterministic local answer and marks `answer_mode=local_natural_summary` when natural answers are enabled.
- Returned citations are always included so users can inspect the evidence behind the answer.

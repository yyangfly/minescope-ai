# Mining Intelligence Aggregator

24h interview implementation for a mining news + policy + price aggregation pipeline.

## Layout

- `pipeline/`: collection, cleaning, deduplication, embeddings, and SQLite vector storage.
- `serve/`: FastAPI app with `/query`.
- `eval/`: 20 ground-truth Q&A examples and automatic metrics.
- `DATA_NOTES.md`: schema, primary key, and dedupe strategy.

## Quick Start

Create the database with real online sources:

```powershell
python -m pipeline.ingest --db data/mining_knowledge.sqlite --limit-per-source 250
```

If network access is unavailable, build a deterministic 600-row fixture corpus. This is only for local smoke testing and demos, not for claiming real data:

```powershell
python -m pipeline.ingest --db data/mining_knowledge.sqlite --offline-fixture
```

Check whether a database is real, fixture, or mixed:

```powershell
python -m pipeline.inspect_db --db data/mining_knowledge.sqlite
```

Run the API against a specific database:

```powershell
$env:MINING_DB_PATH = "data/mining_real.sqlite"
python -m uvicorn serve.app:app --host 127.0.0.1 --port 8001 --reload
```

Run the API:

```powershell
uvicorn serve.app:app --reload
```

By default, the app uses `data/mining_real.sqlite` when it exists. Override it with `MINING_DB_PATH` when needed.

Ask a question:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/query?q=近 7 天澳洲锂出口政策有何变化?"
```

Run evaluation:

```powershell
python eval/run_eval.py --db data/mining_knowledge.sqlite
```

## API

`GET /query?q=...&top_k=5`

`POST /query`

```json
{
  "q": "近 7 天澳洲锂出口政策有何变化?",
  "top_k": 5,
  "use_llm": true,
  "categories": ["policy"],
  "start_date": "2026-06-16T00:00:00+00:00"
}
```

Response includes:

- `answer`: extractive answer grounded in retrieved evidence.
- `answer_mode`: `llm` when a model generated the answer; otherwise a local fallback such as `local_natural_summary` or `local_summary`.
- `filters`: inferred and explicit filters.
- `citations`: top-k evidence with id, score, source, URL, timestamp, metadata, and snippet.

## LLM Answering

The query flow is RAG:

1. Expand Chinese mining terms into English retrieval terms.
2. Retrieve top-k evidence from the SQLite vector store.
3. Send the user question plus citations to an OpenAI-compatible chat-completions model.
4. Return a natural-language answer plus citations.

Configure any OpenAI-compatible provider:

```powershell
$env:LLM_API_KEY = "your_api_key"
$env:LLM_BASE_URL = "https://api.openai.com/v1"
$env:LLM_MODEL = "gpt-4.1-mini"
python -m uvicorn serve.app:app --host 127.0.0.1 --port 8001 --reload
```

OpenAI-style variable names also work:

```powershell
$env:OPENAI_API_KEY = "your_api_key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:OPENAI_MODEL = "gpt-4.1-mini"
```

For DeepSeek, Qwen, Kimi, or an internal gateway, set `LLM_BASE_URL` and `LLM_MODEL` to that provider's OpenAI-compatible endpoint and model name.

If no key is configured, `/query` still works and returns `answer_mode=local_natural_summary` when natural answers are enabled. That fallback is deterministic and less fluent than an LLM, but it avoids turning the answer area into a citation list.

# Deployment

This project is a FastAPI app with a small SQLite vector store. For interview demos,
the safest free setup is to generate an offline demo database during deployment.

## Recommended: Render Free

1. Push this repository to GitHub.
2. In Render, choose **New +** -> **Blueprint**.
3. Connect the GitHub repository and select `render.yaml`.
4. Deploy.

The Blueprint uses:

```bash
pip install -r requirements.txt && python -m pipeline.ingest --db data/mining_knowledge.sqlite --offline-fixture
```

and starts the app with:

```bash
uvicorn serve.app:app --host 0.0.0.0 --port $PORT
```

Default environment variables:

```text
MINING_DB_PATH=data/mining_knowledge.sqlite
```

Optional LLM variables:

```text
LLM_API_KEY=...
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1-mini
```

Do not commit `.env` or real API keys. Render free web services can sleep after
inactivity, so open the URL once before an interview.

## Alternative: Hugging Face Spaces

1. Create a new Space.
2. Choose **Docker** as the SDK.
3. Push this repo to the Space repository.
4. Add optional LLM secrets in Space settings.

The included `Dockerfile` builds the fixture database and runs FastAPI on port
`7860`.

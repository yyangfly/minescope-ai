FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN python -m pipeline.ingest --db data/mining_knowledge.sqlite --offline-fixture

ENV MINING_DB_PATH=data/mining_knowledge.sqlite

CMD ["uvicorn", "serve.app:app", "--host", "0.0.0.0", "--port", "7860"]

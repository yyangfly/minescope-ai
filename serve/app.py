from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from serve.query_engine import answer_question
from pipeline.vector_store import SQLiteVectorStore
from serve.llm import is_llm_configured

DEFAULT_DB = os.getenv(
    "MINING_DB_PATH",
    "data/mining_real.sqlite" if Path("data/mining_real.sqlite").exists() else "data/mining_knowledge.sqlite",
)
STATIC_DIR = Path(__file__).resolve().parent / "static"


class UTF8JSONResponse(JSONResponse):
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title="Mining Intelligence Query API",
    version="0.1.0",
    default_response_class=UTF8JSONResponse,
)


class QueryRequest(BaseModel):
    q: str = Field(..., min_length=1, description="Natural language question")
    top_k: int = Field(5, ge=1, le=20)
    categories: list[str] | None = None
    start_date: str | None = None
    end_date: str | None = None
    use_llm: bool = True


@app.get("/health")
def health() -> dict:
    return {"ok": True, "db": DEFAULT_DB, "llm_configured": is_llm_configured()}


@app.get("/stats")
def stats() -> dict:
    store = SQLiteVectorStore(DEFAULT_DB)
    try:
        result = store.stats()
        result["llm_configured"] = is_llm_configured()
        return result
    finally:
        store.close()


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html; charset=utf-8")


@app.get("/query")
def query_get(q: str, top_k: int = 5, use_llm: bool = True) -> dict:
    return answer_question(DEFAULT_DB, q, top_k=top_k, use_llm=use_llm)


@app.post("/query")
def query_post(request: QueryRequest) -> dict:
    filters = {
        key: value
        for key, value in {
            "categories": request.categories,
            "start_date": request.start_date,
            "end_date": request.end_date,
        }.items()
        if value
    }
    return answer_question(DEFAULT_DB, request.q, top_k=request.top_k, filters=filters, use_llm=request.use_llm)

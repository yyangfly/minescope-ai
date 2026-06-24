from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str
    model: str
    fallback_models: tuple[str, ...] = ()
    timeout: int = 45


def load_llm_config() -> LLMConfig | None:
    load_dotenv()
    api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    return LLMConfig(
        api_key=api_key,
        base_url=(os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
        model=os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1-mini",
        fallback_models=parse_fallback_models(os.getenv("LLM_FALLBACK_MODELS"), os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")),
    )


class OpenAICompatibleChat:
    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    def complete(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        last_error: RuntimeError | None = None
        for model in (self.config.model, *self.config.fallback_models):
            try:
                return self._complete_once(model, messages, temperature)
            except RuntimeError as exc:
                last_error = exc
                if not is_retryable_llm_error(str(exc)):
                    raise
                time.sleep(1.5)
        if last_error:
            raise last_error
        raise RuntimeError("No LLM model configured")

    def _complete_once(self, model: str, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self.config.base_url}/chat/completions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.config.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
        except URLError as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc

        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Unexpected LLM response: {str(data)[:500]}") from exc


def is_llm_configured() -> bool:
    return load_llm_config() is not None


def parse_fallback_models(value: str | None, base_url: str | None) -> tuple[str, ...]:
    if value:
        return tuple(model.strip() for model in value.split(",") if model.strip())
    if base_url and "generativelanguage.googleapis.com" in base_url:
        return ("gemini-2.5-flash-lite", "gemini-flash-latest")
    return ()


def is_retryable_llm_error(message: str) -> bool:
    return any(code in message for code in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "UNAVAILABLE"))


def load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = strip_inline_comment(value.strip()).strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def strip_inline_comment(value: str) -> str:
    if not value or value[0] in ('"', "'"):
        return value
    marker = value.find("#")
    if marker == -1:
        return value
    return value[:marker].rstrip()

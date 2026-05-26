"""OpenAI client wrapper with content-hashed JSONL cache."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from ..config import IDEA2_LLM_CACHE_JSONL, OPENAI_MODEL


def _cache_key(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class LLMCache:
    def __init__(self, path: Path = IDEA2_LLM_CACHE_JSONL):
        self.path = path
        self._index: dict[str, str] = {}
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    rec = json.loads(line)
                    self._index[rec["key"]] = rec["response"]

    def get(self, key: str) -> str | None:
        return self._index.get(key)

    def put(self, key: str, request: dict, response: str) -> None:
        self._index[key] = response
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"key": key, "request": request, "response": response}, ensure_ascii=False) + "\n")


class LLMClient:
    """Thin wrapper. Reuses one OpenAI client + one on-disk cache for the run."""

    def __init__(self, model: str | None = None, cache: LLMCache | None = None):
        self.model = model or OPENAI_MODEL
        self.cache = cache or LLMCache()
        self._client = None  # lazy

    def _client_lazy(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        return self._client

    def complete(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_output_tokens: int = 1500,
    ) -> str:
        request: dict[str, Any] = {
            "model": self.model,
            "system": system,
            "user": user,
            "temperature": temperature,
            "max_output_tokens": max_output_tokens,
        }
        key = _cache_key(request)
        hit = self.cache.get(key)
        if hit is not None:
            return hit

        client = self._client_lazy()
        # GPT-5 family requires `max_completion_tokens` and only supports temperature=1.
        # Fall back to `max_tokens` + arbitrary temperature on older models.
        is_gpt5 = self.model.startswith("gpt-5") or self.model.startswith("o1") or self.model.startswith("o3")
        kwargs: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if is_gpt5:
            kwargs["max_completion_tokens"] = max_output_tokens
            # GPT-5 + reasoning models only accept temperature=1; omit to use default
        else:
            kwargs["max_tokens"] = max_output_tokens
            kwargs["temperature"] = temperature
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        self.cache.put(key, request, text)
        return text

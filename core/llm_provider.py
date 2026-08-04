"""
core/llm_provider.py — Pluggable LLM provider (Section 8.1).

Default: local Ollama, OpenAI-compatible endpoint, no external account,
no credit card — matches Section 8's zero-credit-card requirement.
Opt-in: Claude API for production-quality agent reasoning once the org is
ready to pay for it (set LLM_PROVIDER=claude + ANTHROPIC_API_KEY).

Model default is intentionally `qwen2:7b`, NOT the spec's `qwen3:8b` — per
explicit instruction to keep qwen2 for now. Swap only LLM_MODEL (env var
or constructor arg) to move to qwen3 or any other Ollama-served model
later; no code change needed, since every Ollama model talks through the
same OpenAI-compatible /v1/chat/completions endpoint.
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODEL = "qwen2:7b"


@dataclass
class LLMResponse:
    text: str
    raw: Optional[dict] = None
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None


class LLMProvider(ABC):
    provider_name: str = "unknown"
    model: str = "unknown"

    @abstractmethod
    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> LLMResponse: ...


class OllamaProvider(LLMProvider):
    """Talks to a local Ollama instance via its OpenAI-compatible endpoint."""

    provider_name = "ollama"

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None, timeout: int = 60) -> None:
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or "http://localhost:11434/v1").rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_OLLAMA_MODEL
        self.timeout = timeout

    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> LLMResponse:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
            return LLMResponse(text=text, raw=data)
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            msg = (
                f"Could not reach Ollama at {self.base_url} (model={self.model}). "
                f"Is `ollama serve` running, and has `ollama pull {self.model}` been run? Detail: {exc}"
            )
            logger.error(msg)
            return LLMResponse(text="", error=msg)
        except Exception as exc:  # noqa: BLE001
            logger.error("Ollama completion failed: %s", exc)
            return LLMResponse(text="", error=str(exc))


class ClaudeProvider(LLMProvider):
    """Opt-in only (Section 8) — requires ANTHROPIC_API_KEY and the
    `anthropic` package. Not the default; use for production-quality agent
    reasoning once cost is acceptable."""

    provider_name = "claude"

    def __init__(self, model: Optional[str] = None) -> None:
        self.model = model or os.environ.get("LLM_MODEL") or "claude-sonnet-5"
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Claude is opt-in (Section 8) — "
                "set LLM_PROVIDER=ollama to use the free local default instead."
            )
        try:
            import anthropic  # noqa: F401  (imported lazily; not a hard dependency)
        except ImportError as exc:
            raise RuntimeError(
                "The `anthropic` package is not installed. It's intentionally NOT in "
                "requirements.txt (Ollama is the zero-dependency default per Section 8) — "
                "add it with an accompanying ADR per Section 10.1 before enabling this provider."
            ) from exc
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> LLMResponse:
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
            return LLMResponse(text=text)
        except Exception as exc:  # noqa: BLE001
            logger.error("Claude completion failed: %s", exc)
            return LLMResponse(text="", error=str(exc))


def load_default_provider() -> LLMProvider:
    """Reads LLM_PROVIDER (default: 'ollama', per Section 8.1's
    zero-credit-card default). Set LLM_PROVIDER=claude to opt in."""
    provider = (os.environ.get("LLM_PROVIDER") or "ollama").lower()
    if provider == "claude":
        return ClaudeProvider()
    if provider == "ollama":
        return OllamaProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r} (expected 'ollama' or 'claude')")


def extract_json_object(text: str) -> Optional[dict]:
    """Best-effort JSON extraction from an LLM response: strips markdown
    code fences, then falls back to grabbing the first {...} block — local
    open-weight models don't reliably honor 'respond with ONLY JSON.'"""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

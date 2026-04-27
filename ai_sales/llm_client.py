from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterable

from .domain import Message


class LLMError(RuntimeError):
    pass


def chat_completion(
    *,
    api_key: str,
    base_url: str,
    messages: Iterable[Message],
    model: str,
    temperature: float,
    max_tokens: int,
    timeout_s: float = 60.0,
    app_url: str | None = None,
    app_name: str | None = None,
) -> str:
    """
    Stateless OpenRouter/OpenAI-compatible chat call.
    Input: messages, model, temperature
    Output: assistant text
    """
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": list(messages),
    }
    data = json.dumps(payload).encode("utf-8")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if app_url:
        headers["HTTP-Referer"] = app_url
    if app_name:
        headers["X-Title"] = app_name

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        raise LLMError(f"LLM HTTPError {e.code}: {body or e.reason}") from e
    except Exception as e:
        raise LLMError(f"LLM request failed: {e}") from e

    try:
        obj = json.loads(raw)
        choices = obj.get("choices") or []
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMError(f"LLM empty response: {raw[:400]}")
        return content.strip()
    except LLMError:
        raise
    except Exception as e:
        raise LLMError(f"LLM parse failed: {e}. Raw: {raw[:400]}") from e


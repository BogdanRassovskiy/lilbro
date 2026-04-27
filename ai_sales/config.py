from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMConfig:
    api_key: str
    base_url: str = "https://openrouter.ai/api/v1"
    timeout_s: float = 60.0
    default_temperature: float = 0.5
    default_max_tokens: int = 800
    # Optional OpenRouter headers
    app_url: str | None = None
    app_name: str | None = None


@dataclass(frozen=True)
class AppConfig:
    llm: LLMConfig
    memory_dir: str = ".ai_sales_memory"


def load_config() -> AppConfig:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("Missing API key. Set OPENROUTER_API_KEY.")

    base_url = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    timeout_s = float(os.environ.get("OPENROUTER_TIMEOUT_S") or "60")
    default_temperature = float(os.environ.get("LLM_TEMPERATURE") or "0.5")
    default_max_tokens = int(os.environ.get("LLM_MAX_TOKENS") or "800")

    return AppConfig(
        llm=LLMConfig(
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s,
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
            app_url=os.environ.get("OPENROUTER_APP_URL"),
            app_name=os.environ.get("OPENROUTER_APP_NAME"),
        ),
        memory_dir=os.environ.get("AI_SALES_MEMORY_DIR") or ".ai_sales_memory",
    )


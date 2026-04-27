from __future__ import annotations

from .domain import LeadStatus


# Popular OpenRouter models (USD per 1M tokens, input/output).
# Prices are from OpenRouter model pages (can change over time).
#
# - openai/gpt-4.1:          $2.00 / $8.00
# - openai/gpt-4.1-mini:     $0.40 / $1.60
# - anthropic/claude-3.7-sonnet: $3.00 / $15.00
# - google/gemini-2.5-flash: $0.30 / $2.50
# - mistralai/mistral-large: $2.00 / $6.00
# - deepseek/deepseek-chat-v3.1: $0.15 / $0.75
POPULAR_MODELS: dict[str, str] = {
    "gpt41": "openai/gpt-4.1",
    "gpt41_mini": "openai/gpt-4.1-mini",
    "claude37_sonnet": "anthropic/claude-3.7-sonnet",
    "gemini25_flash": "google/gemini-2.5-flash",
    "mistral_large": "mistralai/mistral-large",
    "deepseek_v31": "deepseek/deepseek-chat-v3.1",
}


def select_model(status: LeadStatus) -> str:
    """
    Pure model selection.
    Keep it simple and configurable by environment/config later.
    """
    # Future (when lead statuses are wired end-to-end):
    # if status == LeadStatus.INTERESTED:
    #     return POPULAR_MODELS["gpt41"]
    # if status == LeadStatus.REPLIED:
    #     return POPULAR_MODELS["gpt41_mini"]
    # return POPULAR_MODELS["gpt41_mini"]

    # For now: use one model for all statuses.
    _ = status
    return POPULAR_MODELS["deepseek_v31"]


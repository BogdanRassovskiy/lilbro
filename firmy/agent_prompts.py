"""Shared agent prompt snippets for outbound generation (web UI, telegram bot)."""

from __future__ import annotations

from typing import Optional

from .models import FirmyAgent


def agent_strategy_prompt(agent: Optional[FirmyAgent]) -> str:
    """Strategy/tactics text: prompt_strategy, else system_prompt."""
    if not agent:
        return ""
    return (agent.prompt_strategy or "").strip() or (agent.system_prompt or "").strip()

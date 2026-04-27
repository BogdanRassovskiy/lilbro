from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import prompt_builder
from .llm_client import chat_completion
from .memory import MemoryStore
from .model_selector import select_model
from .domain import LeadContext, LeadStatus, Message


@dataclass(frozen=True)
class OrchestratorConfig:
    temperature: float = 0.5
    max_tokens: int = 800


class Orchestrator:
    """
    The ONLY place where modules interact:
      1) memory.get_context
      2) prompt_builder.*
      3) model_selector.select_model
      4) llm_client.chat_completion
      5) memory.save_message + memory.update_summary
    """

    def __init__(
        self,
        *,
        memory: MemoryStore,
        llm_call: Callable[[list[Message], str, float, int], str],
        config: OrchestratorConfig,
    ):
        self._memory = memory
        self._llm_call = llm_call
        self._cfg = config

    def _apply_fixed_edges(self, text: str, *, sender_name: str, lead_name: str | None, language: str) -> str:
        core = (text or "").strip()
        if not core:
            core = "Хочу кратко поделиться полезной идеей для вашей команды."
        if language.lower().startswith("ru"):
            greeting = "Здравствуйте,{}!".format(" " + lead_name if lead_name else "")
            closing = "С уважением,\n{}".format(sender_name or "Команда")
        else:
            greeting = "Hello{},".format(" " + lead_name if lead_name else "")
            closing = "Best regards,\n{}".format(sender_name or "Team")
        return "{}\n\n{}\n\n{}".format(greeting, core, closing)

    def generate_cold_email(self, *, lead_id: str, ctx: LeadContext) -> str:
        rec = self._memory.get_context(lead_id)
        messages = prompt_builder.build_cold_outreach(ctx, rec.summary, rec.messages or [])
        model = select_model(ctx.lead.status)
        raw = self._llm_call(messages, model, self._cfg.temperature, self._cfg.max_tokens)
        out = self._apply_fixed_edges(
            raw,
            sender_name=ctx.intent.sender_name,
            lead_name=ctx.lead.contact_name,
            language=ctx.intent.language,
        )
        self._memory.save_message(lead_id, {"role": "assistant", "content": out})
        self._memory.update_summary(lead_id)
        return out

    def generate_follow_up(self, *, lead_id: str, ctx: LeadContext) -> str:
        rec = self._memory.get_context(lead_id)
        messages = prompt_builder.build_follow_up(ctx, rec.summary, rec.messages or [])
        model = select_model(ctx.lead.status)
        raw = self._llm_call(messages, model, self._cfg.temperature, self._cfg.max_tokens)
        out = self._apply_fixed_edges(
            raw,
            sender_name=ctx.intent.sender_name,
            lead_name=ctx.lead.contact_name,
            language=ctx.intent.language,
        )
        self._memory.save_message(lead_id, {"role": "assistant", "content": out})
        self._memory.update_summary(lead_id)
        return out

    def reply_to_inbound(self, *, lead_id: str, ctx: LeadContext, inbound_text: str) -> str:
        # Save inbound first (as "user" message)
        self._memory.save_message(lead_id, {"role": "user", "content": inbound_text})
        rec = self._memory.get_context(lead_id)
        messages = prompt_builder.build_reply(ctx, rec.summary, rec.messages or [], inbound_text)
        model = select_model(LeadStatus.REPLIED)
        raw = self._llm_call(messages, model, self._cfg.temperature, self._cfg.max_tokens)
        out = self._apply_fixed_edges(
            raw,
            sender_name=ctx.intent.sender_name,
            lead_name=ctx.lead.contact_name,
            language=ctx.intent.language,
        )
        self._memory.save_message(lead_id, {"role": "assistant", "content": out})
        self._memory.update_summary(lead_id)
        return out


def build_default_orchestrator(
    *,
    memory: MemoryStore,
    api_key: str,
    base_url: str,
    timeout_s: float,
    temperature: float,
    max_tokens: int,
    app_url: str | None = None,
    app_name: str | None = None,
) -> Orchestrator:
    def _call(messages: list[Message], model: str, temp: float, max_toks: int) -> str:
        return chat_completion(
            api_key=api_key,
            base_url=base_url,
            messages=messages,
            model=model,
            temperature=temp,
            max_tokens=max_toks,
            timeout_s=timeout_s,
            app_url=app_url,
            app_name=app_name,
        )

    return Orchestrator(
        memory=memory,
        llm_call=_call,
        config=OrchestratorConfig(temperature=temperature, max_tokens=max_tokens),
    )


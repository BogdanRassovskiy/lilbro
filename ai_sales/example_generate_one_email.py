from __future__ import annotations

import os
import sys

# Allow running as: `python3 ai_sales/example_generate_one_email.py`
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# Prevent stdlib shadowing when running from ai_sales/ directory
if sys.path and os.path.basename(os.path.abspath(sys.path[0])) == "ai_sales":
    sys.path.pop(0)

from ai_sales.config import load_config
from ai_sales.memory import JsonMemoryStore
from ai_sales.orchestrator import build_default_orchestrator
from ai_sales.domain import CompanyProfile, LeadContext, LeadProfile, LeadStatus, OutreachIntent


def main() -> None:
    cfg = load_config()
    memory = JsonMemoryStore(cfg.memory_dir)

    orch = build_default_orchestrator(
        memory=memory,
        api_key=cfg.llm.api_key,
        base_url=cfg.llm.base_url,
        timeout_s=cfg.llm.timeout_s,
        temperature=cfg.llm.default_temperature,
        max_tokens=cfg.llm.default_max_tokens,
        app_url=cfg.llm.app_url,
        app_name=cfg.llm.app_name,
    )

    ctx = LeadContext(
        lead=LeadProfile(
            lead_id="lead_001",
            contact_name="Алексей",
            contact_role="CEO",
            email="alex@example.com",
            status=LeadStatus.COLD,
        ),
        company=CompanyProfile(
            name="Example Logistics",
            website="https://example.com",
            industry="логистика",
            location="Прага",
            notes="Малый/средний бизнес, B2B",
        ),
        intent=OutreachIntent(
            product="AI-ассистент продаж",
            value_prop="генерирует персонализированные письма и ответы, ведёт историю переписки, экономит время SDR",
            call_to_action="Могу предложить 15 минут созвон на этой неделе — удобно во вт/ср?",
            language="ru",
            sender_name="Иван",
            sender_company="Lilbro",
            sender_prompt="Ты Иван из Lilbro. Твоя задача — кратко и уважительно назначить созвон и предложить ценность продукта.",
        ),
    )

    memory.set_lead_context(ctx.lead.lead_id, ctx)
    email_text = orch.generate_cold_email(lead_id=ctx.lead.lead_id, ctx=ctx)
    print(email_text)


if __name__ == "__main__":
    main()


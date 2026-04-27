from __future__ import annotations

from .domain import LeadContext, Message


def _style_system(language: str) -> str:
    if language.lower().startswith("ru"):
        return (
            "Ты — опытный sales-ассистент. Пиши кратко, конкретно, без воды. "
            "Не выдумывай фактов о компании. Если данных мало — делай нейтральные формулировки. "
            "Формат: 1) Тема письма (Subject) 2) Тело письма. "
            "ВАЖНО: не пиши приветствие и прощание/подпись, генерируй только основную смысловую часть."
        )
    return (
        "You are an experienced sales assistant. Write concise, specific emails. "
        "Do not invent facts. If data is missing, use neutral phrasing. "
        "Format: 1) Subject 2) Body. "
        "IMPORTANT: do not include greeting or sign-off/signature, generate only the core body."
    )


def build_cold_outreach(ctx: LeadContext, summary: str | None, history: list[Message]) -> list[Message]:
    company = ctx.company
    lead = ctx.lead
    intent = ctx.intent

    user = (
        f"Task: Write a cold outreach email.\n"
        f"Lead: id={lead.lead_id}, name={lead.contact_name or '-'}, role={lead.contact_role or '-'}\n"
        f"Company: name={company.name}, website={company.website or '-'}, industry={company.industry or '-'}, location={company.location or '-'}\n"
        f"Product: {intent.product}\n"
        f"Value proposition: {intent.value_prop}\n"
        f"CTA: {intent.call_to_action}\n"
        f"Sender: {intent.sender_name} ({intent.sender_company})\n"
        f"Existing summary (if any): {summary or '-'}\n"
        f"Rules: be personalized, 80-140 words, no spammy claims, 1 clear CTA.\n"
    )

    msgs: list[Message] = [{"role": "system", "content": _style_system(intent.language)}]
    if intent.sender_prompt:
        msgs.append({"role": "system", "content": intent.sender_prompt.strip()})
    msgs.append({"role": "user", "content": user})
    return msgs


def build_follow_up(ctx: LeadContext, summary: str | None, history: list[Message]) -> list[Message]:
    intent = ctx.intent
    company = ctx.company
    lead = ctx.lead

    user = (
        f"Task: Write a short follow-up email to the previous cold outreach.\n"
        f"Lead: id={lead.lead_id}, name={lead.contact_name or '-'}\n"
        f"Company: {company.name}\n"
        f"Product: {intent.product}\n"
        f"CTA: {intent.call_to_action}\n"
        f"Summary: {summary or '-'}\n"
        f"History (last messages):\n"
        + "\n".join([f"- {m['role']}: {m['content'][:200]}" for m in history[-6:]])
        + "\nRules: 50-90 words, polite, add one new angle, no guilt-tripping.\n"
    )

    msgs: list[Message] = [{"role": "system", "content": _style_system(intent.language)}]
    if intent.sender_prompt:
        msgs.append({"role": "system", "content": intent.sender_prompt.strip()})
    msgs.append({"role": "user", "content": user})
    return msgs


def build_reply(ctx: LeadContext, summary: str | None, history: list[Message], inbound_text: str) -> list[Message]:
    intent = ctx.intent
    company = ctx.company
    lead = ctx.lead

    user = (
        f"Task: Write a reply to an inbound message from the lead.\n"
        f"Lead: id={lead.lead_id}, name={lead.contact_name or '-'}\n"
        f"Company: {company.name}\n"
        f"Product: {intent.product}\n"
        f"CTA: {intent.call_to_action}\n"
        f"Summary: {summary or '-'}\n"
        f"Inbound message:\n{inbound_text}\n"
        f"History (last messages):\n"
        + "\n".join([f"- {m['role']}: {m['content'][:200]}" for m in history[-10:]])
        + "\nRules: answer questions, be helpful, propose next step.\n"
    )

    msgs: list[Message] = [{"role": "system", "content": _style_system(intent.language)}]
    if intent.sender_prompt:
        msgs.append({"role": "system", "content": intent.sender_prompt.strip()})
    msgs.append({"role": "user", "content": user})
    return msgs


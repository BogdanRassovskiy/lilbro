#!/usr/bin/env python3
"""
Telegram bridge bot for lead-processing workflow.

Features:
- HOT/LOST notifications with show/hide conversation.
- ESCALATE_OWNER: operator can reply literally, or prompt LLM, then send/regenerate/cancel.

Run:
  export TELEGRAM_BOT_TOKEN="..."
  export DJANGO_SETTINGS_MODULE="lilbro.settings"
  python3 telegram_bridge_bot.py
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import django
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message


os.environ.setdefault("DJANGO_SETTINGS_MODULE", os.environ.get("DJANGO_SETTINGS_MODULE", "lilbro.settings"))
django.setup()

from django.utils import timezone  # noqa: E402

from ai_sales.llm_client import chat_completion  # noqa: E402
from firmy.agent_prompts import agent_strategy_prompt  # noqa: E402
from firmy.models import FirmyProcessingItem  # noqa: E402


logger = logging.getLogger("telegram_bridge")

STATE_FILE = Path(__file__).resolve().parent / ".telegram_bridge_state.json"
POLL_INTERVAL_SEC = float(os.environ.get("TG_BRIDGE_POLL_SEC", "5"))
OWNER_CHAT_ID = 104932971
TG_MAX_TEXT = 4096


class EscFlow(StatesGroup):
    waiting_literal = State()
    waiting_gen_prompt = State()


def _normalize_openrouter_model_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "deepseek/deepseek-chat-v3.1"
    if "/" in s:
        return s
    alias = s.lower()
    aliases = {
        "deepseek": "deepseek/deepseek-chat-v3.1",
        "deepseek-v3.1": "deepseek/deepseek-chat-v3.1",
        "deepseek_v3.1": "deepseek/deepseek-chat-v3.1",
        "deepseek_v31": "deepseek/deepseek-chat-v3.1",
        "gpt": "openai/gpt-4.1-mini",
        "gpt4": "openai/gpt-4.1-mini",
        "claude": "anthropic/claude-3.7-sonnet",
    }
    return aliases.get(alias, "deepseek/deepseek-chat-v3.1")


def _load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {"owner_chat_id": OWNER_CHAT_ID, "notified": {}, "escalations": {}}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        data["owner_chat_id"] = OWNER_CHAT_ID
        return data
    except Exception:
        return {"owner_chat_id": OWNER_CHAT_ID, "notified": {}, "escalations": {}}


def _save_state(state: Dict[str, Any]) -> None:
    state["owner_chat_id"] = OWNER_CHAT_ID
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _calc_contact_flags(convo: List[dict]) -> Tuple[bool, bool]:
    contacted = any(isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip() for m in convo)
    answered = any(isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip() for m in convo)
    return contacted, answered


def _load_conversation(item: FirmyProcessingItem) -> List[dict]:
    try:
        data = json.loads(item.conversation_json or "[]")
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _last_incoming_text(convo: List[dict]) -> str:
    """Last client message (incoming). Do not require '?' — Czech questions may omit it."""
    for m in reversed(convo):
        if isinstance(m, dict) and m.get("dir") == "in":
            text = (m.get("text") or "").strip()
            if text:
                return text
    return ""


def _escalation_signature(item: FirmyProcessingItem, last_incoming: str) -> str:
    payload = "|".join(
        [
            item.reply_status or "",
            item.gen_status or "",
            (item.reply_error or "")[:800],
            (item.gen_error or "")[:800],
            last_incoming[:400],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _should_track_escalation(item: FirmyProcessingItem) -> bool:
    re_err = item.reply_error or ""
    ge_err = item.gen_error or ""
    has_escalate = "ESCALATE_OWNER" in re_err or "ESCALATE_OWNER" in ge_err
    has_err_flag = item.reply_status == item.REPLY_ERROR or item.gen_status == item.GEN_ERROR
    return bool(has_escalate and has_err_flag)


def _extract_draft_from_message_text(text: str, item_id: int) -> str:
    """Fallback: take draft body from the Telegram message under 'Черновик для ID …'."""
    t = (text or "").strip()
    needle = f"Черновик для ID {item_id}:"
    if needle not in t:
        return ""
    rest = t.split(needle, 1)[1].lstrip()
    return rest.strip()


def _scan_events(state: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Tuple[str, int]]]:
    notified = state.setdefault("notified", {})
    escalations = state.setdefault("escalations", {})
    out_events: List[Tuple[str, int]] = []

    items = list(FirmyProcessingItem.objects.select_related("premise", "assigned_agent").all())
    for item in items:
        key = str(item.id)
        mark = (item.lead_state or "").strip()
        prev_mark = notified.get(key)
        if mark != prev_mark and item.lead_state in ("hot", "lost"):
            out_events.append(("lead", item.id))
        notified[key] = mark

        convo = _load_conversation(item)
        last_in = _last_incoming_text(convo)

        if _should_track_escalation(item) and last_in:
            sig = _escalation_signature(item, last_in)
            esc = escalations.get(key) or {}
            if esc.get("telegram_sig") != sig:
                old = dict(esc)
                preserving = (
                    old.get("status") == "awaiting_approval"
                    and (
                        (old.get("instruction") or "").strip()
                        or (old.get("last_operator_prompt") or "").strip()
                        or (old.get("draft") or "").strip()
                    )
                )
                if preserving:
                    old["telegram_sig"] = sig
                    old["question"] = last_in
                    escalations[key] = old
                else:
                    escalations[key] = {
                        "item_id": item.id,
                        "question": last_in,
                        "status": "awaiting_action",
                        "instruction": "",
                        "last_operator_prompt": "",
                        "draft": "",
                        "telegram_sig": sig,
                        "created_at": old.get("created_at") or timezone.now().isoformat(),
                    }
                    out_events.append(("escalation", item.id))
        elif key in escalations and not _should_track_escalation(item):
            esc = escalations.get(key) or {}
            if esc.get("status") == "awaiting_approval" and (esc.get("draft") or "").strip():
                continue
            del escalations[key]

    return state, out_events


def _build_lead_notification_text(item: FirmyProcessingItem) -> str:
    return (
        f"{'🔥 HOT' if item.lead_state == 'hot' else '⚠️ LOST'}\n"
        f"ID: {item.id}\n"
        f"Firma: {item.premise.title}\n"
        f"Agent: {(item.assigned_agent.name if item.assigned_agent else '—')}\n"
        f"Lead state: {item.lead_state}"
    )


def _extract_escalate_reason(item: FirmyProcessingItem) -> str:
    for raw in (item.reply_error, item.gen_error):
        s = (raw or "").strip()
        if s.startswith("ESCALATE_OWNER:"):
            return s.split(":", 1)[1].strip() or "out_of_scope"
    return ""


def _build_escalation_base_text(item: FirmyProcessingItem) -> str:
    reason = _extract_escalate_reason(item)
    last_in = _last_incoming_text(_load_conversation(item))
    lines = [
        "❓ ESCALATION (potreba operatora)",
        f"ID: {item.id}",
        f"Firma: {item.premise.title}",
        f"Agent: {(item.assigned_agent.name if item.assigned_agent else '—')}",
    ]
    if reason:
        lines.append(f"Duvod: {reason}")
    if last_in:
        lines.append(f"Posledni zprava klienta:\n{last_in}")
    return "\n".join(lines)


def _format_last_messages(item: FirmyProcessingItem, max_messages: int = 10, max_chars: int = 2300) -> str:
    convo = _load_conversation(item)
    if not convo:
        return "No messages yet."
    msgs = [m for m in convo if isinstance(m, dict) and (m.get("text") or "").strip()]
    if not msgs:
        return "No messages yet."
    msgs = msgs[-max_messages:]
    blocks: List[str] = []

    def short_ts(raw_ts: str) -> str:
        s = (raw_ts or "").strip()
        if not s:
            return "—"
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return f"{dt.day}.{dt.month}. {dt.hour}:{dt.minute:02d}"
        except Exception:
            return s[:16].replace("T", " ")

    for m in msgs:
        role_icon = "👤" if m.get("dir") == "in" else "🤖"
        ts = short_ts(str(m.get("ts") or ""))
        text = re.sub(r"\s+", " ", str(m.get("text") or "").strip())
        blocks.append(f"{role_icon} {text}\n{ts}")
    while blocks and len("\n\n".join(blocks)) > max_chars:
        blocks.pop(0)
    return "\n\n".join(blocks) if blocks else "No messages fit."


def _kb_lead_notification(item_id: int, expanded: bool) -> InlineKeyboardMarkup:
    if expanded:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Скрыть переписку", callback_data=f"lead_hide:{item_id}")]]
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Показать переписку", callback_data=f"lead_show:{item_id}")]]
    )


def _kb_escalation_main(item_id: int, expanded: bool) -> InlineKeyboardMarkup:
    conv_btn = (
        InlineKeyboardButton(text="Скрыть переписку", callback_data=f"esc_conv_hide:{item_id}")
        if expanded
        else InlineKeyboardButton(text="Показать переписку", callback_data=f"esc_conv_show:{item_id}")
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [conv_btn],
            [
                InlineKeyboardButton(text="Ответить буквально", callback_data=f"esc_lit:{item_id}"),
                InlineKeyboardButton(text="Сгенерировать ответ", callback_data=f"esc_gen:{item_id}"),
            ],
        ]
    )


def _kb_escalation_draft(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Отправить", callback_data=f"esc_draft_send:{item_id}"),
                InlineKeyboardButton(text="Сгенерировать снова", callback_data=f"esc_draft_regen:{item_id}"),
            ],
            [
                InlineKeyboardButton(text="Отменить", callback_data=f"esc_draft_cancel:{item_id}"),
            ],
        ]
    )


def _compose_lead_message(item_id: int, expanded: bool) -> Tuple[str, InlineKeyboardMarkup]:
    item = FirmyProcessingItem.objects.select_related("premise", "assigned_agent").get(pk=item_id)
    base = _build_lead_notification_text(item)
    if not expanded:
        return base, _kb_lead_notification(item_id, expanded=False)
    convo_block = _format_last_messages(item, max_messages=10, max_chars=2300)
    text = f"{base}\n\n--- Последние сообщения ---\n{convo_block}"
    if len(text) > TG_MAX_TEXT:
        allowed = max(400, TG_MAX_TEXT - len(base) - 30)
        convo_block = _format_last_messages(item, max_messages=10, max_chars=allowed)
        text = f"{base}\n\n--- Последние сообщения ---\n{convo_block}"
    return text[:TG_MAX_TEXT], _kb_lead_notification(item_id, expanded=True)


def _compose_escalation_message(item_id: int, expanded: bool) -> Tuple[str, InlineKeyboardMarkup]:
    item = FirmyProcessingItem.objects.select_related("premise", "assigned_agent").get(pk=item_id)
    base = _build_escalation_base_text(item)
    if not expanded:
        return base, _kb_escalation_main(item_id, expanded=False)
    convo_block = _format_last_messages(item, max_messages=10, max_chars=2200)
    text = f"{base}\n\n--- Последние сообщения ---\n{convo_block}"
    if len(text) > TG_MAX_TEXT:
        allowed = max(400, TG_MAX_TEXT - len(base) - 40)
        convo_block = _format_last_messages(item, max_messages=10, max_chars=allowed)
        text = f"{base}\n\n--- Последние сообщения ---\n{convo_block}"
    return text[:TG_MAX_TEXT], _kb_escalation_main(item_id, expanded=True)


def _generate_draft_with_instruction(item_id: int, instruction: str) -> str:
    item = FirmyProcessingItem.objects.select_related("premise", "assigned_agent").get(pk=item_id)
    agent = item.assigned_agent
    if not agent:
        raise RuntimeError("No assigned interviewer agent")
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    convo = _load_conversation(item)
    history_lines = []
    for m in convo[-20:]:
        if not isinstance(m, dict):
            continue
        prefix = "OD_KLIENTA" if m.get("dir") == "in" else "OD_TEBE"
        text = (m.get("summary") or m.get("text") or "").strip()
        if text:
            text = re.sub(r"^(Klient|Agent|CLIENT|AGENT)\s*:\s*", "", text, flags=re.I)
            history_lines.append(f"{prefix}: {text[:700]}")
    instr = (instruction or "").strip()
    strategy = agent_strategy_prompt(agent)
    system_parts = [
        "Jsi sales asistent. Pis vyhradne cesky.",
        "Historie: OD_KLIENTA = prichozi od klienta; OD_TEBE = tve drivejsi odchozi.",
        "Prompt operatora ma nejvyssi prioritu pro ton a zamer; historie = kontext dialogu a faktu.",
        "Nevymyslej fakta o firme — jen z historie nebo udaju nize. Vrat jen navrh odchozi odpovedi bez komentare.",
    ]
    if strategy:
        system_parts.append("Kontext odesilatele (strategie/taktika):\n" + strategy)
    system = "\n".join(system_parts)
    user = (
        f"Firma: {item.premise.title}\n"
        f"Kategorie: {item.premise.category}\n\n"
        f"PROMPT OPERATORA:\n{instr}\n\n"
        f"Historie konverzace:\n"
        f"{chr(10).join(history_lines) if history_lines else '(prazdne)'}\n\n"
        "Vrat jen text navrhu odpovedi klientovi."
    )
    model = _normalize_openrouter_model_id(agent.model_name)
    out = chat_completion(
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        model=model,
        temperature=0.2,
        max_tokens=450,
        timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "40"),
    )
    return re.sub(r"\s+", " ", (out or "").strip())


def _company_title_for_item(item_id: int, max_len: int = 200) -> str:
    """Human-readable company name for Telegram confirmations; fallback to #id."""
    try:
        item = FirmyProcessingItem.objects.select_related("premise").get(pk=item_id)
        t = (item.premise.title or "").strip()
        if not t:
            return f"#{item_id}"
        if len(t) > max_len:
            return t[: max_len - 1] + "…"
        return t
    except Exception:
        return f"#{item_id}"


def _append_outgoing_and_resolve(item_id: int, text: str) -> None:
    item = FirmyProcessingItem.objects.select_related("assigned_agent").get(pk=item_id)
    convo = _load_conversation(item)
    body = (text or "").strip()
    if not body:
        return
    agent = item.assigned_agent
    convo.append(
        {
            "ts": timezone.now().isoformat(),
            "dir": "out",
            "agent_id": agent.id if agent else None,
            "agent_name": agent.name if agent else "",
            "text": body,
            "summary": body[:2000],
        }
    )
    item.conversation_json = json.dumps(convo, ensure_ascii=False)
    item.was_contacted, item.was_answered = _calc_contact_flags(convo)
    item.reply_status = item.REPLY_DONE
    item.reply_error = ""
    item.reply_finished_at = timezone.now()
    item.gen_status = item.GEN_DONE
    item.gen_error = ""
    item.gen_finished_at = timezone.now()
    item.save(
        update_fields=[
            "conversation_json",
            "was_contacted",
            "was_answered",
            "reply_status",
            "reply_error",
            "reply_finished_at",
            "gen_status",
            "gen_error",
            "gen_finished_at",
            "updated_at",
        ]
    )


async def _poll_loop(bot: Bot) -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            state = _load_state()
            state, events = await asyncio.to_thread(_scan_events, state)
            _save_state(state)
            for kind, item_id in events:
                try:
                    if kind == "lead":
                        text, kb = await asyncio.to_thread(_compose_lead_message, item_id, False)
                        await bot.send_message(OWNER_CHAT_ID, text, reply_markup=kb)
                    elif kind == "escalation":
                        text, kb = await asyncio.to_thread(_compose_escalation_message, item_id, False)
                        await bot.send_message(OWNER_CHAT_ID, text, reply_markup=kb)
                except Exception:
                    logger.exception("Failed to send %s notification for item %s", kind, item_id)
        except Exception:
            logger.exception("Poll loop error")


def _require_owner(msg_chat_id: int, state: Dict[str, Any]) -> bool:
    _ = state
    return int(msg_chat_id) == OWNER_CHAT_ID


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN env var.")

    bot = Bot(token=token)
    dp = Dispatcher(storage=MemoryStorage())

    @dp.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if not _require_owner(message.chat.id, _load_state()):
            return
        await message.answer(
            "inTimeDevBot bridge is running.\n"
            "/escalations — список открытых эскалаций"
        )

    @dp.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        if not _require_owner(message.chat.id, _load_state()):
            return
        data = await state.get_data()
        item_id = data.get("item_id")
        card_chat_id = data.get("esc_card_chat_id")
        card_msg_id = data.get("esc_card_message_id")
        await state.clear()
        if item_id is not None and card_chat_id and card_msg_id:
            try:
                text, kb = await asyncio.to_thread(_compose_escalation_message, int(item_id), False)
                await message.bot.edit_message_text(
                    chat_id=card_chat_id,
                    message_id=card_msg_id,
                    text=text,
                    reply_markup=kb,
                )
            except Exception:
                await message.answer("Режим ввода сброшен.")
        else:
            await message.answer("Режим ввода сброшен.")

    @dp.message(Command("escalations"))
    async def cmd_escalations(message: Message) -> None:
        state = _load_state()
        if not _require_owner(message.chat.id, state):
            return
        esc = state.get("escalations", {})
        pending = []
        for e in esc.values():
            st = e.get("status")
            if st in ("awaiting_action", "awaiting_approval"):
                pending.append(e)
        if not pending:
            await message.answer("Нет открытых эскалаций.")
            return
        for e in pending:
            item_id = int(e["item_id"])
            text, kb = await asyncio.to_thread(_compose_escalation_message, item_id, False)
            await message.answer(text, reply_markup=kb)

    @dp.callback_query(F.data.startswith("esc_conv_show:"))
    async def cb_esc_show(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        try:
            text, kb = await asyncio.to_thread(_compose_escalation_message, item_id, True)
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            logger.exception("esc_conv_show edit failed")
        await call.answer()

    @dp.callback_query(F.data.startswith("esc_conv_hide:"))
    async def cb_esc_hide(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        try:
            text, kb = await asyncio.to_thread(_compose_escalation_message, item_id, False)
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            logger.exception("esc_conv_hide edit failed")
        await call.answer()

    @dp.callback_query(F.data == "esc_cancel_flow")
    async def cb_esc_cancel_flow(call: CallbackQuery, state: FSMContext) -> None:
        st = _load_state()
        if not _require_owner(call.message.chat.id, st):
            return
        data = await state.get_data()
        item_id = data.get("item_id")
        await state.clear()
        await call.answer("Отменено")
        if item_id is not None:
            try:
                text, kb = await asyncio.to_thread(_compose_escalation_message, int(item_id), False)
                await call.message.edit_text(text, reply_markup=kb)
            except Exception:
                logger.exception("esc_cancel_flow restore failed")
        else:
            try:
                await call.message.edit_text("Отменено.")
            except Exception:
                pass

    @dp.callback_query(F.data.startswith("esc_lit:"))
    async def cb_esc_literal(call: CallbackQuery, state: FSMContext) -> None:
        st = _load_state()
        if not _require_owner(call.message.chat.id, st):
            return
        item_id = int(call.data.split(":")[1])
        await state.set_state(EscFlow.waiting_literal)
        await state.update_data(
            item_id=item_id,
            esc_card_chat_id=call.message.chat.id,
            esc_card_message_id=call.message.message_id,
        )
        await call.answer()
        try:
            await call.message.edit_text(
                f"Режим: ответ буквально для ID {item_id}.\n\n"
                "Отправьте следующим сообщением текст — он уйдёт клиенту как есть.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="esc_cancel_flow")]]
                ),
            )
        except Exception:
            logger.exception("esc_lit edit failed")

    @dp.callback_query(F.data.startswith("esc_gen:"))
    async def cb_esc_gen(call: CallbackQuery, state: FSMContext) -> None:
        st = _load_state()
        if not _require_owner(call.message.chat.id, st):
            return
        item_id = int(call.data.split(":")[1])
        await state.set_state(EscFlow.waiting_gen_prompt)
        await state.update_data(
            item_id=item_id,
            esc_card_chat_id=call.message.chat.id,
            esc_card_message_id=call.message.message_id,
        )
        await call.answer()
        try:
            await call.message.edit_text(
                f"Режим: генерация для ID {item_id}.\n\n"
                "Отправьте промпт — по нему будет сгенерирован ответ.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="esc_cancel_flow")]]
                ),
            )
        except Exception:
            logger.exception("esc_gen edit failed")

    @dp.message(StateFilter(EscFlow.waiting_literal), F.text)
    async def on_literal_text(message: Message, state: FSMContext) -> None:
        st = _load_state()
        if not _require_owner(message.chat.id, st):
            return
        data = await state.get_data()
        item_id = int(data["item_id"])
        card_chat_id = data.get("esc_card_chat_id")
        card_msg_id = data.get("esc_card_message_id")
        body = (message.text or "").strip()
        if not body:
            if card_chat_id and card_msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=card_chat_id,
                        message_id=card_msg_id,
                        text=f"Режим: ответ буквально (ID {item_id}). Пустой текст — отправьте ещё раз.",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="esc_cancel_flow")]]
                        ),
                    )
                except Exception:
                    pass
            else:
                await message.answer("Пустой текст.")
            return
        try:
            await asyncio.to_thread(_append_outgoing_and_resolve, item_id, body)
        except Exception as e:
            if card_chat_id and card_msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=card_chat_id,
                        message_id=card_msg_id,
                        text=f"Ошибка отправки (ID {item_id}): {e}",
                        reply_markup=None,
                    )
                except Exception:
                    pass
            else:
                await message.answer(f"Ошибка: {e}")
            return
        est = _load_state()
        est.setdefault("escalations", {}).pop(str(item_id), None)
        _save_state(est)
        await state.clear()
        company = await asyncio.to_thread(_company_title_for_item, item_id)
        done_text = f"✅ Сообщение отправлено в чат ({company})."
        if card_chat_id and card_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=card_chat_id,
                    message_id=card_msg_id,
                    text=done_text,
                    reply_markup=None,
                )
            except Exception:
                await message.answer(done_text)
        else:
            await message.answer(done_text)

    @dp.message(StateFilter(EscFlow.waiting_gen_prompt), F.text)
    async def on_gen_prompt(message: Message, state: FSMContext) -> None:
        st = _load_state()
        if not _require_owner(message.chat.id, st):
            return
        data = await state.get_data()
        item_id = int(data["item_id"])
        card_chat_id = data.get("esc_card_chat_id")
        card_msg_id = data.get("esc_card_message_id")
        prompt = (message.text or "").strip()
        cancel_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data="esc_cancel_flow")]]
        )
        if not prompt:
            if card_chat_id and card_msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=card_chat_id,
                        message_id=card_msg_id,
                        text=f"Режим: генерация (ID {item_id}). Пустой промпт — отправьте ещё раз.",
                        reply_markup=cancel_kb,
                    )
                except Exception:
                    pass
            else:
                await message.answer("Пустой промпт.")
            return
        if card_chat_id and card_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=card_chat_id,
                    message_id=card_msg_id,
                    text="Генерирую…",
                    reply_markup=cancel_kb,
                )
            except Exception:
                pass
        try:
            draft = await asyncio.to_thread(_generate_draft_with_instruction, item_id, prompt)
        except Exception as e:
            err = f"Ошибка генерации (ID {item_id}): {e}"
            if card_chat_id and card_msg_id:
                try:
                    await message.bot.edit_message_text(
                        chat_id=card_chat_id,
                        message_id=card_msg_id,
                        text=err[:TG_MAX_TEXT],
                        reply_markup=cancel_kb,
                    )
                except Exception:
                    await message.answer(err)
            else:
                await message.answer(err)
            return
        esc = st.setdefault("escalations", {}).setdefault(str(item_id), {"item_id": item_id})
        esc["instruction"] = prompt
        esc["last_operator_prompt"] = prompt
        esc["draft"] = draft
        esc["status"] = "awaiting_approval"
        _save_state(st)
        await state.clear()
        kb = _kb_escalation_draft(item_id)
        body = f"Черновик для ID {item_id}:\n\n{draft}"
        if card_chat_id and card_msg_id and len(body) <= TG_MAX_TEXT:
            try:
                await message.bot.edit_message_text(
                    chat_id=card_chat_id,
                    message_id=card_msg_id,
                    text=body,
                    reply_markup=kb,
                )
            except Exception:
                await message.answer(body[:TG_MAX_TEXT], reply_markup=kb)
        elif card_chat_id and card_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=card_chat_id,
                    message_id=card_msg_id,
                    text=f"Черновик для ID {item_id} (слишком длинный для одного сообщения) — см. ниже.",
                    reply_markup=None,
                )
            except Exception:
                pass
            await message.answer(draft[:TG_MAX_TEXT], reply_markup=kb)
        else:
            await message.answer(body[:TG_MAX_TEXT], reply_markup=kb)

    @dp.callback_query(F.data.startswith("esc_draft_send:"))
    async def cb_draft_send(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        esc = dict(state.get("escalations", {}).get(str(item_id)) or {})
        draft = (esc.get("draft") or "").strip()
        if not draft and call.message and call.message.text:
            draft = _extract_draft_from_message_text(call.message.text, item_id)
        if not draft:
            await call.answer("Нет черновика", show_alert=True)
            return
        try:
            await asyncio.to_thread(_append_outgoing_and_resolve, item_id, draft)
        except Exception as e:
            await call.answer(str(e)[:200], show_alert=True)
            return
        st = _load_state()
        st.setdefault("escalations", {}).pop(str(item_id), None)
        _save_state(st)
        company = await asyncio.to_thread(_company_title_for_item, item_id)
        done = f"✅ Ответ отправлен в чат ({company})."
        try:
            await call.message.edit_text(done[:TG_MAX_TEXT], reply_markup=None)
        except Exception:
            try:
                await call.message.answer(done)
            except Exception:
                pass
        await call.answer()

    @dp.callback_query(F.data.startswith("esc_draft_regen:"))
    async def cb_draft_regen(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        esc = dict(state.get("escalations", {}).get(str(item_id)) or {})
        instr = (
            (esc.get("instruction") or "").strip()
            or (esc.get("last_operator_prompt") or "").strip()
            or (esc.get("question") or "").strip()
        )
        if not instr:
            await call.answer("Нет сохранённого промпта", show_alert=True)
            return
        await call.answer()
        try:
            draft = await asyncio.to_thread(_generate_draft_with_instruction, item_id, instr)
        except Exception as e:
            await call.message.answer(f"Ошибка: {e}")
            return
        esc["draft"] = draft
        esc["instruction"] = instr
        esc["last_operator_prompt"] = instr
        esc["status"] = "awaiting_approval"
        state.setdefault("escalations", {})[str(item_id)] = esc
        _save_state(state)
        kb = _kb_escalation_draft(item_id)
        try:
            await call.message.edit_text(f"Черновик для ID {item_id}:\n\n{draft}", reply_markup=kb)
        except Exception:
            await call.message.answer(f"Черновик для ID {item_id}:\n\n{draft}", reply_markup=kb)

    @dp.callback_query(F.data.startswith("esc_draft_cancel:"))
    async def cb_draft_cancel(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        esc = state.get("escalations", {}).get(str(item_id))
        if esc:
            esc["draft"] = ""
            esc["status"] = "awaiting_action"
            state.setdefault("escalations", {})[str(item_id)] = esc
            _save_state(state)
        await call.answer("Черновик отменён")
        await call.message.answer(f"Черновик для ID {item_id} отменён. Откройте эскалацию снова через /escalations.")

    @dp.callback_query(F.data.startswith("lead_show:"))
    async def cb_lead_show(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        try:
            text, kb = await asyncio.to_thread(_compose_lead_message, item_id, True)
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            logger.exception("lead_show edit failed")
        await call.answer()

    @dp.callback_query(F.data.startswith("lead_hide:"))
    async def cb_lead_hide(call: CallbackQuery) -> None:
        state = _load_state()
        if not _require_owner(call.message.chat.id, state):
            return
        item_id = int(call.data.split(":")[1])
        try:
            text, kb = await asyncio.to_thread(_compose_lead_message, item_id, False)
            await call.message.edit_text(text, reply_markup=kb)
        except Exception:
            logger.exception("lead_hide edit failed")
        await call.answer()

    poll_task = asyncio.create_task(_poll_loop(bot))
    try:
        await dp.start_polling(bot)
    finally:
        poll_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())

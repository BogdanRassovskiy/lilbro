import json
import os
import random
import re
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from typing import Optional

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .lead_markers import (
    ASKING_INFO_SUBSTRINGS,
    BUSY_LATER_SUBSTRINGS,
    CZECH_POSITIVE_INTEREST_NORMALIZED_REGEXES,
    CZECH_REJECTING_NORMALIZED_REGEXES,
    HESITATING_SUBSTRINGS,
    INTERESTED_SUBSTRINGS,
    LOST_OUTBOUND_CONTACT_REGEXES,
    LOST_OUTBOUND_CONTACT_SUBSTRINGS,
    LOOKS_REJECTING_REGEXES,
    POLITE_THANKS_EXPLICIT_INTEREST_NORMALIZED_REGEXES,
    POLITE_THANKS_NORMALIZED_REGEXES,
    POLITE_THANKS_QUESTION_OR_PRICE_NORMALIZED_REGEXES,
    PRICE_SENSITIVE_SUBSTRINGS,
    REJECTING_REGEXES,
    REJECTING_SUBSTRINGS,
    STYLE_DIRECT_SUBSTRINGS,
    STYLE_FORMAL_SUBSTRINGS,
    STYLE_FRIENDLY_SUBSTRINGS,
)
from .agent_prompts import agent_strategy_prompt as _agent_strategy_prompt
from .models import FirmyAgent, FirmyPremise, FirmyProcessingItem, FirmySearchHit, FirmySearchRun
from tools.parcer_firmy_cz import build_search_url, fetch_listings
from tools.universal_content_parser import merge_parsed_contents, parse_url_to_content
from ai_sales.llm_client import LLMError, chat_completion
from ai_sales.model_selector import POPULAR_MODELS


def _normalize_openrouter_model_id(raw: str) -> str:
    """
    Accept either a full OpenRouter model id (vendor/model) or a short alias.
    Falls back to a safe default.
    """
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
        "gpt-4": "openai/gpt-4.1-mini",
        "gpt-4.1": "openai/gpt-4.1",
        "gpt-4.1-mini": "openai/gpt-4.1-mini",
        "claude": "anthropic/claude-3.7-sonnet",
        "sonnet": "anthropic/claude-3.7-sonnet",
    }
    return aliases.get(alias, "deepseek/deepseek-chat-v3.1")


def _selected_agent_by_session_key(request, key: str):
    aid = request.session.get(key)
    if not aid:
        return None
    try:
        return FirmyAgent.objects.filter(pk=int(aid)).first()
    except Exception:
        return None


def _selected_interviewer_agent(request):
    agent = _selected_agent_by_session_key(request, "selected_interviewer_agent_id")
    if agent and agent.role == FirmyAgent.ROLE_INTERVIEWER:
        return agent
    return None


def _selected_evaluator_agent(request):
    agent = _selected_agent_by_session_key(request, "selected_evaluator_agent_id")
    if agent and agent.role == FirmyAgent.ROLE_EVALUATOR:
        return agent
    return None


def _safe_json_list(raw: str) -> list[str]:
    try:
        data = json.loads(raw or "[]")
        if isinstance(data, list):
            return [str(x) for x in data if str(x).strip()]
    except Exception:
        pass
    return []


def _agent_scope_prompt(agent: Optional[FirmyAgent]) -> str:
    if not agent:
        return ""
    return (agent.prompt_scope or "").strip() or (agent.system_prompt or "").strip()


def calc_contact_flags(convo: list[dict]) -> tuple[bool, bool]:
    was_contacted = any((m.get("dir") == "out") for m in convo if isinstance(m, dict))
    was_answered = any((m.get("dir") == "in") for m in convo if isinstance(m, dict))
    return was_contacted, was_answered


def _derive_response_type(convo: list[dict]) -> list[str]:
    incoming = [(m.get("text") or "").strip().lower() for m in convo if isinstance(m, dict) and m.get("dir") == "in"]
    if not incoming:
        return ["no_response"]
    all_text = "\n".join(incoming)
    out: list[str] = []
    norm_text = _normalize_text_for_match(all_text)
    polite_thanks_only = _is_polite_thanks_only(norm_text)

    def has_any(*parts):
        return any(p in all_text for p in parts)


    rejecting_by_regex = any(re.search(rx, all_text, re.I) for rx in REJECTING_REGEXES) or _looks_rejecting_text(all_text)

    if rejecting_by_regex or has_any(*REJECTING_SUBSTRINGS):
        out.append("rejecting")
    if has_any(*PRICE_SENSITIVE_SUBSTRINGS):
        out.append("price_sensitive")
    if has_any(*ASKING_INFO_SUBSTRINGS):
        out.append("asking_info")
    czech_positive_interest = (
        any(re.search(rx, norm_text, re.I) for rx in CZECH_POSITIVE_INTEREST_NORMALIZED_REGEXES)
    )
    if (not rejecting_by_regex) and (not polite_thanks_only) and (
        has_any(*INTERESTED_SUBSTRINGS) or czech_positive_interest
    ):
        out.append("interested")
    if has_any(*HESITATING_SUBSTRINGS):
        out.append("hesitating")
    if has_any(*BUSY_LATER_SUBSTRINGS):
        out.append("busy_later")
    # "dekuji/dekuju" alone should be treated as polite neutral, not interest.
    if polite_thanks_only:
        out = [x for x in out if x != "interested"]
        if "neutral" not in out:
            out.append("neutral")
    # Rejecting marker must always be explicitly present when detected.
    if rejecting_by_regex and "rejecting" not in out:
        out.insert(0, "rejecting")
    if not out:
        out.append("neutral")
    return out


def _normalize_text_for_match(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return ""
    t = "".join(ch for ch in unicodedata.normalize("NFKD", t) if not unicodedata.combining(ch))
    t = re.sub(r"[^a-z0-9а-яё\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _is_too_short_for_lead_eval(text: str) -> bool:
    norm = _normalize_text_for_match(text)
    if not norm:
        return True
    words = [w for w in norm.split(" ") if w]
    # Ignore very short one-word replies like "ne/ok/no".
    return len(words) < 2 and len(norm) < 10


def _has_czech_rejecting_markers(norm_text: str) -> bool:
    if not norm_text:
        return False
    return any(re.search(rx, norm_text, re.I) for rx in CZECH_REJECTING_NORMALIZED_REGEXES)


def _is_polite_thanks_only(norm_text: str) -> bool:
    if not norm_text:
        return False
    has_thanks = any(
        re.search(rx, norm_text, re.I)
        for rx in POLITE_THANKS_NORMALIZED_REGEXES
    )
    if not has_thanks:
        return False
    has_question_or_price = any(
        re.search(rx, norm_text, re.I)
        for rx in POLITE_THANKS_QUESTION_OR_PRICE_NORMALIZED_REGEXES
    )
    has_explicit_interest = any(
        re.search(rx, norm_text, re.I)
        for rx in POLITE_THANKS_EXPLICIT_INTEREST_NORMALIZED_REGEXES
    )
    has_rejecting = _has_czech_rejecting_markers(norm_text)
    return has_thanks and (not has_question_or_price) and (not has_explicit_interest) and (not has_rejecting)


def _has_outbound_lost_signoff_with_contacts(convo: list[dict]) -> bool:
    last_out_text = ""
    for m in reversed(convo):
        if isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip():
            last_out_text = (m.get("text") or "").strip()
            break
    if not last_out_text:
        return False
    # Hard rule: lost only when the latest bot message contains real contact data.
    has_contact_by_regex = any(re.search(rx, last_out_text, re.I) for rx in LOST_OUTBOUND_CONTACT_REGEXES)
    return bool(has_contact_by_regex)


def _derive_communication_style(convo: list[dict]) -> list[str]:
    incoming = [(m.get("text") or "").strip() for m in convo if isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip()]
    if not incoming:
        return []
    out: list[str] = []
    lengths = [len(t) for t in incoming]
    avg_len = sum(lengths) / max(1, len(lengths))
    joined = "\n".join(incoming).lower()
    if avg_len < 45:
        out.append("short")
    if avg_len > 220:
        out.append("detailed")
    if any(x in joined for x in STYLE_FORMAL_SUBSTRINGS):
        out.append("formal")
    if any(x in joined for x in STYLE_FRIENDLY_SUBSTRINGS):
        out.append("friendly")
    if any(x in joined for x in STYLE_DIRECT_SUBSTRINGS) or avg_len < 80:
        out.append("direct")
    if any("???" in t or "!!!" in t for t in incoming):
        out.append("chaotic")
    # Keep unique order.
    dedup: list[str] = []
    for x in out:
        if x not in dedup:
            dedup.append(x)
    return dedup


def _derive_lead_state(response_types: list[str], convo: list[dict]) -> str:
    if any(x in response_types for x in ("interested", "asking_info", "price_sensitive")):
        return "hot"
    has_incoming = any(isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip() for m in convo)
    if has_incoming or "busy_later" in response_types or "hesitating" in response_types:
        return "warm"
    return "cold"


_ALLOWED_LEAD_STATES = {"cold", "warm", "hot", "lost"}
_ALLOWED_RESPONSE_TYPES = {
    "no_response",
    "neutral",
    "interested",
    "asking_info",
    "price_sensitive",
    "hesitating",
    "rejecting",
    "busy_later",
}
_ALLOWED_COMMUNICATION_STYLES = {
    "short",
    "detailed",
    "formal",
    "friendly",
    "direct",
    "chaotic",
}


def _llm_refine_lead_traits(convo: list[dict], interviewer: Optional[FirmyAgent]) -> Optional[dict]:
    if not interviewer:
        return None
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        return None
    model = _normalize_openrouter_model_id(interviewer.model_name)
    history = []
    for m in convo[-20:]:
        if not isinstance(m, dict):
            continue
        direction = "company" if m.get("dir") == "in" else "agent"
        text = (m.get("text") or "").strip()
        if not text:
            continue
        history.append({"dir": direction, "text": text[:1000]})
    if not history:
        return None

    system = (
        "Ты анализатор продажной переписки. Определи статус лида строго по переписке. "
        "Особенно важно корректно распознавать отказ. "
        "Верни только JSON объект без пояснений."
    )
    user = (
        "Верни JSON строго формата:\n"
        "{"
        '"lead_state":"cold|warm|hot|lost",'
        '"response_type":["no_response|neutral|interested|asking_info|price_sensitive|hesitating|rejecting|busy_later"],'
        '"communication_style":["short|detailed|formal|friendly|direct|chaotic"]'
        "}\n\n"
        "История (последние сообщения):\n"
        + json.dumps(history, ensure_ascii=False)
    )
    try:
        raw = chat_completion(
            api_key=api_key,
            base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.0,
            max_tokens=220,
            timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "40"),
        )
        parsed = json.loads((raw or "").strip())
        if not isinstance(parsed, dict):
            return None
        lead_state = str(parsed.get("lead_state") or "").strip().lower()
        response_type = [str(x).strip().lower() for x in (parsed.get("response_type") or []) if str(x).strip()]
        communication_style = [str(x).strip().lower() for x in (parsed.get("communication_style") or []) if str(x).strip()]

        if lead_state not in _ALLOWED_LEAD_STATES:
            lead_state = ""
        response_type = [x for x in response_type if x in _ALLOWED_RESPONSE_TYPES]
        communication_style = [x for x in communication_style if x in _ALLOWED_COMMUNICATION_STYLES]
        return {
            "lead_state": lead_state or None,
            "response_type": response_type,
            "communication_style": communication_style,
        }
    except Exception:
        return None


def _apply_lead_traits_from_convo(
    item: FirmyProcessingItem,
    convo: list[dict],
    interviewer: Optional[FirmyAgent] = None,
    use_llm: bool = True,
) -> list[str]:
    last_incoming_text = ""
    for m in reversed(convo):
        if isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip():
            last_incoming_text = (m.get("text") or "").strip()
            break
    incoming_text = "\n".join(
        (m.get("text") or "").strip()
        for m in convo
        if isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip()
    )
    last_incoming_rejecting = (not _is_too_short_for_lead_eval(last_incoming_text)) and _looks_rejecting_text(last_incoming_text)
    polite_thanks_only = _is_polite_thanks_only(_normalize_text_for_match(incoming_text))

    # Layer 1: deterministic rules
    response_type = _derive_response_type(convo)
    communication_style = _derive_communication_style(convo)
    lead_state = _derive_lead_state(response_type, convo)
    outbound_lost_marker = _has_outbound_lost_signoff_with_contacts(convo)

    # Layer 2: LLM refinement (best effort)
    llm_traits = _llm_refine_lead_traits(convo, interviewer) if use_llm else None
    if llm_traits:
        llm_response = llm_traits.get("response_type") or []
        llm_style = llm_traits.get("communication_style") or []
        llm_state = llm_traits.get("lead_state")

        # Merge with priority for explicit reject detection.
        merged_response = []
        for x in response_type + llm_response:
            if x in _ALLOWED_RESPONSE_TYPES and x not in merged_response:
                merged_response.append(x)
        response_type = merged_response or response_type

        merged_style = []
        for x in communication_style + llm_style:
            if x in _ALLOWED_COMMUNICATION_STYLES and x not in merged_style:
                merged_style.append(x)
        communication_style = merged_style or communication_style

        if llm_state in _ALLOWED_LEAD_STATES:
            lead_state = llm_state

    if polite_thanks_only:
        response_type = [x for x in response_type if x in ("neutral", "no_response")]
        if "neutral" not in response_type:
            response_type.append("neutral")
        # Pure gratitude ("dekuju/dekuji") should not warm up the lead.
        lead_state = "cold"

    # Keep rejecting marker in response_type, but do not set lost from keywords.
    if "rejecting" in response_type or last_incoming_rejecting:
        if "rejecting" not in response_type:
            response_type.insert(0, "rejecting")
    # Lost is allowed only when bot sent contact handoff message.
    if outbound_lost_marker:
        lead_state = "lost"
    elif lead_state == "lost":
        lead_state = _derive_lead_state(response_type, convo)

    changed_fields: list[str] = []
    if item.lead_state != lead_state:
        item.lead_state = lead_state
        changed_fields.append("lead_state")
    response_json = json.dumps(response_type, ensure_ascii=False)
    if item.response_type != response_json:
        item.response_type = response_json
        changed_fields.append("response_type")
    style_json = json.dumps(communication_style, ensure_ascii=False)
    if item.communication_style != style_json:
        item.communication_style = style_json
        changed_fields.append("communication_style")
    return changed_fields


def _apply_lost_post_actions(
    item: FirmyProcessingItem,
    convo: list[dict],
    interviewer: Optional[FirmyAgent],
    prev_lead_state: str = "",
) -> tuple[list[dict], list[str]]:
    changed_fields: list[str] = []
    current_state = (item.lead_state or "").strip()
    prev_state = (prev_lead_state or "").strip()
    if current_state != "lost":
        return convo, changed_fields
    became_lost = prev_state != "lost"

    # Apply auto "stop contact" actions only when lead transitions into lost.
    if became_lost and not item.paused_individual:
        item.paused_individual = True
        changed_fields.append("paused_individual")
    if became_lost and not item.do_not_contact:
        item.do_not_contact = True
        changed_fields.append("do_not_contact")

    if (not became_lost) or (not interviewer):
        return convo, changed_fields

    last_in_idx = -1
    for idx in range(len(convo) - 1, -1, -1):
        m = convo[idx]
        if isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip():
            last_in_idx = idx
            break
    if last_in_idx < 0:
        return convo, changed_fields

    has_out_after = any(
        isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip()
        for m in convo[last_in_idx + 1 :]
    )
    if has_out_after:
        return convo, changed_fields

    signoff_text = _apply_fixed_greeting_and_signoff(_safe_core_or_fallback("", rejecting_case=True), interviewer).strip()
    if not signoff_text:
        return convo, changed_fields
    signoff_summary = _summarize_message_for_agent(signoff_text, interviewer, "out") or signoff_text
    convo.append(
        {
            "ts": timezone.now().isoformat(),
            "dir": "out",
            "agent_id": interviewer.id,
            "agent_name": interviewer.name,
            "text": signoff_text,
            "summary": signoff_summary,
        }
    )
    item.conversation_json = json.dumps(convo, ensure_ascii=False)
    item.was_contacted, item.was_answered = calc_contact_flags(convo)
    changed_fields.extend(["conversation_json", "was_contacted", "was_answered"])
    return convo, changed_fields


def _run_refine_traits_task(item_id: int, interviewer_id: int) -> None:
    from django.db import close_old_connections

    close_old_connections()
    try:
        item = FirmyProcessingItem.objects.select_related("assigned_agent").get(pk=item_id)
    except Exception:
        return
    if not item.assigned_agent_id or item.assigned_agent_id != interviewer_id:
        return
    interviewer = item.assigned_agent
    try:
        convo = json.loads(item.conversation_json or "[]")
        if not isinstance(convo, list):
            convo = []
    except Exception:
        convo = []
    prev_lead_state = (item.lead_state or "").strip()
    fields = _apply_lead_traits_from_convo(item, convo, interviewer, use_llm=True)
    convo, lost_fields = _apply_lost_post_actions(item, convo, interviewer, prev_lead_state=prev_lead_state)
    fields.extend(lost_fields)
    if fields:
        item.save(update_fields=list(dict.fromkeys(fields + ["updated_at"])))
    close_old_connections()


def _is_ajax_json(request) -> bool:
    return (
        request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
        or "application/json" in (request.META.get("HTTP_ACCEPT") or "")
    )


def _is_item_running(item: FirmyProcessingItem, interviewer: FirmyAgent) -> bool:
    return bool(interviewer.processing_enabled and not item.paused_individual)


def _has_unanswered_incoming(convo: list[dict]) -> bool:
    last_out_idx = -1
    for i, m in enumerate(convo):
        if isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip():
            last_out_idx = i
    for i, m in enumerate(convo):
        if i <= last_out_idx:
            continue
        if isinstance(m, dict) and m.get("dir") == "in" and (m.get("text") or "").strip():
            return True
    return False


def _enqueue_auto_action_for_item(item: FirmyProcessingItem, interviewer: FirmyAgent) -> None:
    if not _is_item_running(item, interviewer):
        return
    if item.reply_finished_at is None and item.reply_started_at is not None:
        return
    try:
        convo = json.loads(item.conversation_json or "[]")
        if not isinstance(convo, list):
            convo = []
    except Exception:
        convo = []
    has_out = any(isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip() for m in convo)
    if not has_out or _has_unanswered_incoming(convo):
        FirmyProcessingItem.objects.filter(pk=item.id).update(
            reply_status=FirmyProcessingItem.REPLY_IDLE,
            reply_error="",
            reply_started_at=timezone.now(),
            reply_finished_at=None,
        )
        t = threading.Thread(target=_run_auto_reply_task, args=(item.id, interviewer.id, True), daemon=True)
        t.start()


def _normalize_delay_pair(min_raw, max_raw) -> tuple[int, int]:
    try:
        dmin = int(min_raw)
    except Exception:
        dmin = 0
    try:
        dmax = int(max_raw)
    except Exception:
        dmax = 0
    dmin = max(0, min(10, dmin))
    dmax = max(0, min(10, dmax))
    if dmin > dmax:
        dmin, dmax = dmax, dmin
    return dmin, dmax


def _wait_before_reply(item_id: int, interviewer_id: int) -> bool:
    item = FirmyProcessingItem.objects.select_related("assigned_agent").filter(pk=item_id).first()
    if not item or not item.assigned_agent_id or item.assigned_agent_id != interviewer_id:
        return False
    interviewer = item.assigned_agent
    if not interviewer or not _is_item_running(item, interviewer):
        return False
    dmin, dmax = _normalize_delay_pair(item.reply_delay_min_minutes, item.reply_delay_max_minutes)
    if dmax <= 0:
        return True
    min_ms = dmin * 60 * 1000
    max_ms = dmax * 60 * 1000
    wait_ms = random.randint(min_ms, max_ms)
    elapsed_ms = 0
    tick_ms = 250
    while elapsed_ms < wait_ms:
        sleep_s = min(tick_ms, wait_ms - elapsed_ms) / 1000.0
        time.sleep(sleep_s)
        elapsed_ms += int(sleep_s * 1000)
        current = FirmyProcessingItem.objects.select_related("assigned_agent").filter(pk=item_id).first()
        if not current or not current.assigned_agent or not _is_item_running(current, current.assigned_agent):
            return False
    return True


@require_http_methods(["POST"])
def run_search(request):
    q = (request.POST.get("q") or "").strip()
    if not q:
        messages.error(request, "Введите поисковый запрос.")
        return redirect("home")

    try:
        expected = int(request.POST.get("expected") or "10")
    except ValueError:
        expected = 10
    expected = max(1, min(20, expected))

    run = FirmySearchRun.objects.create(
        query=q,
        expected_limit=expected,
        search_url=build_search_url(q),
        status=FirmySearchRun.STATUS_PENDING,
    )

    try:
        rows = fetch_listings(q, expected)

        created_premises = 0
        updated_premises = 0
        hits = []

        # (1) upsert уникальных карточек по premise_id
        premise_ids = [r["premise_id"] for r in rows]
        existing = {
            p.premise_id: p
            for p in FirmyPremise.objects.filter(premise_id__in=premise_ids)
        }

        to_create = []
        to_update = []

        for r in rows:
            pid = r["premise_id"]
            payload = {
                "title": r["title"][:500],
                "detail_url": r["detail_url"][:2048],
                "category": (r.get("category") or "")[:300],
                "address": (r.get("address") or "")[:500],
                "card_text": r.get("card_text") or "",
                "phones": r.get("phones") or "",
                "emails": r.get("emails") or "",
                "website_url": (r.get("website_url") or "")[:2048],
            }

            p = existing.get(pid)
            if not p:
                to_create.append(FirmyPremise(premise_id=pid, **payload))
                continue

            # Обновляем только если реально изменилось: так уменьшаем записи в БД.
            changed = False
            for k, v in payload.items():
                if getattr(p, k) != v:
                    setattr(p, k, v)
                    changed = True
            if changed:
                to_update.append(p)

        if to_create:
            FirmyPremise.objects.bulk_create(to_create, ignore_conflicts=True)
            created_premises = len(to_create)

        if to_update:
            # bulk_update доступен в Django 2.2
            FirmyPremise.objects.bulk_update(
                to_update,
                ["title", "detail_url", "category", "address", "card_text", "phones", "emails", "website_url"],
            )
            updated_premises = len(to_update)

        # перечитываем (на случай ignore_conflicts / параллельных запросов)
        premises = {
            p.premise_id: p
            for p in FirmyPremise.objects.filter(premise_id__in=premise_ids)
        }

        # (2) сохраняем попадания для конкретного запуска
        for r in rows:
            p = premises.get(r["premise_id"])
            if not p:
                continue
            hits.append(
                FirmySearchHit(
                    run=run,
                    position=r["position"],
                    premise=p,
                )
            )

        if hits:
            FirmySearchHit.objects.bulk_create(hits, ignore_conflicts=True)

        run.status = FirmySearchRun.STATUS_OK
        run.results_count = len(hits)
        messages.success(
            request,
            "Найдено: {} · новых карточек: {} · обновлено карточек: {}.".format(
                len(hits),
                created_premises,
                updated_premises,
            ),
        )
    except Exception as e:
        run.status = FirmySearchRun.STATUS_ERROR
        run.error_message = str(e)
        messages.error(request, "Ошибка загрузки: {}".format(e))

    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error_message", "results_count", "finished_at"])

    return redirect("firmy:results", run_id=run.pk)


def results(request, run_id):
    run = get_object_or_404(FirmySearchRun, pk=run_id)
    return render(
        request,
        "firmy/results.html",
        {"run": run, "results": run.hits.select_related("premise").all()},
    )


def _apply_premise_filters(request, qs):
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(address__icontains=q))

    category = (request.GET.get("category") or "").strip()
    if category:
        qs = qs.filter(category=category)

    has_email = request.GET.get("has_email")
    if has_email in ("1", "true", "yes", "on"):
        qs = qs.exclude(emails="")

    has_phone = request.GET.get("has_phone")
    if has_phone in ("1", "true", "yes", "on"):
        qs = qs.exclude(phones="")

    has_site = request.GET.get("has_site")
    if has_site in ("1", "true", "yes", "on"):
        qs = qs.exclude(website_url="")

    updated_from = (request.GET.get("updated_from") or "").strip()
    if updated_from:
        qs = qs.filter(updated_at__date__gte=updated_from)

    updated_to = (request.GET.get("updated_to") or "").strip()
    if updated_to:
        qs = qs.filter(updated_at__date__lte=updated_to)

    return qs, {
        "q": q,
        "category": category,
        "has_email": has_email,
        "has_phone": has_phone,
        "has_site": has_site,
        "updated_from": updated_from,
        "updated_to": updated_to,
    }

@require_http_methods(["GET", "POST"])
def premises(request):
    base = FirmyPremise.objects.all()
    filtered, filters = _apply_premise_filters(request, base)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        ids = request.POST.getlist("selected")
        select_all = request.POST.get("select_all") in ("1", "true", "yes", "on")

        if select_all:
            premise_ids = list(filtered.values_list("id", flat=True))
        else:
            premise_ids = [int(x) for x in ids if str(x).isdigit()]

        if not premise_ids:
            messages.error(request, "Ничего не выбрано.")
            return redirect(request.get_full_path())

        if action == "delete":
            cnt = FirmyPremise.objects.filter(id__in=premise_ids).count()
            FirmyPremise.objects.filter(id__in=premise_ids).delete()
            messages.success(request, "Удалено карточек: {}.".format(cnt))
            return redirect(request.get_full_path())

        if action == "take":
            interviewer = _selected_interviewer_agent(request)
            if not interviewer:
                messages.error(request, "Добавление в обработку доступно только при выбранном агенте с ролью 'собеседник'.")
                return redirect("firmy:agents")
            now = timezone.now()
            objs = [
                FirmyProcessingItem(
                    premise_id=pid,
                    queued_at=now,
                    assigned_agent_id=interviewer.id,
                    paused_individual=True,
                )
                for pid in premise_ids
            ]
            FirmyProcessingItem.objects.bulk_create(objs, ignore_conflicts=True)
            # Если элемент уже был у этого же собеседника — "переставляем" в конец его очереди.
            FirmyProcessingItem.objects.filter(
                premise_id__in=premise_ids,
                assigned_agent=interviewer,
            ).update(queued_at=now, paused_individual=True)
            messages.success(
                request,
                "Добавлено в обработку: {} (собеседник: {}).".format(len(premise_ids), interviewer.name),
            )
            if interviewer.processing_enabled:
                for obj in FirmyProcessingItem.objects.filter(premise_id__in=premise_ids, assigned_agent=interviewer):
                    _enqueue_auto_action_for_item(obj, interviewer)
            return redirect(request.get_full_path())

        messages.error(request, "Неизвестное действие.")
        return redirect(request.get_full_path())

    qs = filtered.order_by("-updated_at", "-id")

    categories = (
        FirmyPremise.objects.exclude(category="")
        .values_list("category", flat=True)
        .distinct()
        .order_by("category")
    )

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(
        request,
        "firmy/premises.html",
        {
            "page_obj": page_obj,
            "categories": categories,
            "filters": filters,
            "selected_interviewer": _selected_interviewer_agent(request),
            "selected_evaluator": _selected_evaluator_agent(request),
        },
    )


@require_http_methods(["GET", "POST"])
def processing(request):
    def calc_contact_flags(convo):
        was_contacted = any((m.get("dir") == "out") for m in convo if isinstance(m, dict))
        was_answered = any((m.get("dir") == "in") for m in convo if isinstance(m, dict))
        return was_contacted, was_answered

    interviewer = _selected_interviewer_agent(request)
    if not interviewer:
        messages.error(request, "Раздел 'В обработке' доступен только при выбранном агенте с ролью 'собеседник'.")
        return redirect("firmy:premises")

    qs = FirmyProcessingItem.objects.select_related("premise").filter(
        assigned_agent=interviewer
    ).order_by("queued_at", "id")
    page_num = request.GET.get("page") or request.POST.get("page") or 1

    if request.method == "POST":
        form_type = (request.POST.get("form_type") or "").strip()

        if form_type == "bulk":
            action = (request.POST.get("action") or "").strip()
            ids = request.POST.getlist("selected")
            item_ids = [int(x) for x in ids if str(x).isdigit()]
            if not item_ids:
                messages.error(request, "Ничего не выбрано.")
                return redirect("{}?page={}".format(request.path, page_num))
            if action == "delete":
                scope = qs.filter(id__in=item_ids)
                cnt = scope.count()
                scope.delete()
                messages.success(request, "Удалено из обработки: {}.".format(cnt))
                return redirect("{}?page={}".format(request.path, page_num))
            messages.error(request, "Неизвестное действие.")
            return redirect("{}?page={}".format(request.path, page_num))

        if form_type == "delete_one":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                messages.error(request, "Не выбран элемент для удаления.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            obj = qs.filter(pk=item_id).first()
            if not obj:
                messages.error(request, "Элемент не найден (возможно уже удалён).")
                return redirect("{}?page={}".format(request.path, page_num))
            obj.delete()
            messages.success(request, "Удалено из обработки.")
            return redirect("{}?page={}".format(request.path, page_num))

        if form_type == "toggle_global":
            interviewer.processing_enabled = not interviewer.processing_enabled
            interviewer.save(update_fields=["processing_enabled", "updated_at"])
            if interviewer.processing_enabled:
                for obj in qs:
                    _enqueue_auto_action_for_item(obj, interviewer)
                messages.success(request, "Глобальный режим: продолжено.")
            else:
                messages.success(request, "Глобальный режим: пауза.")
            return redirect("{}?page={}".format(request.path, page_num))

        if form_type == "toggle_item":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                messages.error(request, "Не выбран чат.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            item = qs.filter(pk=item_id).first()
            if not item:
                messages.error(request, "Чат не найден.")
                return redirect("{}?page={}".format(request.path, page_num))
            item.paused_individual = not item.paused_individual
            item.save(update_fields=["paused_individual", "updated_at"])
            if not item.paused_individual and interviewer.processing_enabled:
                _enqueue_auto_action_for_item(item, interviewer)
                messages.success(request, "Чат продолжен.")
            else:
                messages.success(request, "Чат поставлен на паузу.")
            return redirect("{}?page={}&item={}".format(request.path, page_num, item_id))

        if form_type == "set_timer":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
                messages.error(request, "Не выбран чат.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            item = qs.filter(pk=item_id).first()
            if not item:
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "not_found"}, status=404)
                messages.error(request, "Чат не найден.")
                return redirect("{}?page={}".format(request.path, page_num))
            dmin, dmax = _normalize_delay_pair(
                request.POST.get("reply_delay_min_minutes"),
                request.POST.get("reply_delay_max_minutes"),
            )
            item.reply_delay_min_minutes = dmin
            item.reply_delay_max_minutes = dmax
            item.save(update_fields=["reply_delay_min_minutes", "reply_delay_max_minutes", "updated_at"])
            if _is_ajax_json(request):
                return JsonResponse({"ok": True, "min": dmin, "max": dmax})
            messages.success(request, "Таймер ответа обновлен.")
            return redirect("{}?page={}&item={}".format(request.path, page_num, item_id))

        if form_type == "clear_chat":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
                messages.error(request, "Не выбран чат.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            item = qs.filter(pk=item_id).first()
            if not item:
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "not_found"}, status=404)
                messages.error(request, "Чат не найден.")
                return redirect("{}?page={}".format(request.path, page_num))

            item.conversation_json = "[]"
            item.was_contacted = False
            item.was_answered = False
            item.lead_state = ""
            item.response_type = "[]"
            item.communication_style = "[]"
            item.draft_text = ""
            item.draft_requires_confirmation = False
            item.reply_status = FirmyProcessingItem.REPLY_IDLE
            item.reply_error = ""
            item.reply_started_at = None
            item.reply_finished_at = None
            item.save(
                update_fields=[
                    "conversation_json",
                    "was_contacted",
                    "was_answered",
                    "lead_state",
                    "response_type",
                    "communication_style",
                    "draft_text",
                    "draft_requires_confirmation",
                    "reply_status",
                    "reply_error",
                    "reply_started_at",
                    "reply_finished_at",
                    "updated_at",
                ]
            )
            if _is_ajax_json(request):
                return JsonResponse({"ok": True, "status": "cleared"})
            messages.success(request, "Переписка очищена.")
            return redirect("{}?page={}&item={}".format(request.path, page_num, item_id))

        if form_type == "chat":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                messages.error(request, "Не выбран элемент для переписки.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            item = get_object_or_404(FirmyProcessingItem, pk=item_id, assigned_agent=interviewer)

            msg_text = (request.POST.get("message_text") or "").strip()
            message_side = (request.POST.get("message_side") or "out").strip().lower()
            if message_side not in ("in", "out"):
                message_side = "out"
            item.do_not_contact = request.POST.get("do_not_contact") in ("1", "on", "true", "yes")

            try:
                convo = json.loads(item.conversation_json or "[]")
                if not isinstance(convo, list):
                    convo = []
            except Exception:
                convo = []
            convo, _ = _ensure_conversation_summaries(convo, interviewer)

            if msg_text:
                msg_summary = _summarize_message_for_agent(msg_text, interviewer, message_side)
                msg_obj = {
                    "ts": timezone.now().isoformat(),
                    "dir": message_side,
                    "text": msg_text,
                    "summary": msg_summary or msg_text,
                }
                if message_side == "out":
                    msg_obj["agent_id"] = interviewer.id
                    msg_obj["agent_name"] = interviewer.name
                convo.append(msg_obj)
                # After sending a real message, clear any generated draft text.
                item.draft_text = ""
                item.draft_requires_confirmation = False
            item.conversation_json = json.dumps(convo, ensure_ascii=False)
            item.was_contacted, item.was_answered = calc_contact_flags(convo)
            prev_lead_state = (item.lead_state or "").strip()
            trait_fields = _apply_lead_traits_from_convo(item, convo, interviewer, use_llm=False)
            convo, lost_fields = _apply_lost_post_actions(item, convo, interviewer, prev_lead_state=prev_lead_state)
            trait_fields.extend(lost_fields)

            if msg_text and message_side == "in" and _is_item_running(item, interviewer):
                item.reply_status = FirmyProcessingItem.REPLY_IDLE
                item.reply_error = ""
                item.reply_started_at = timezone.now()
                item.reply_finished_at = None

            update_fields = ["was_contacted", "was_answered", "do_not_contact", "conversation_json", "updated_at"]
            if msg_text:
                update_fields.extend(["draft_text", "draft_requires_confirmation"])
            if msg_text and message_side == "in" and _is_item_running(item, interviewer):
                update_fields.extend(["reply_status", "reply_error", "reply_started_at", "reply_finished_at"])
            update_fields.extend(trait_fields)
            item.save(update_fields=list(dict.fromkeys(update_fields)))

            if msg_text and message_side == "in" and _is_item_running(item, interviewer):
                t = threading.Thread(target=_run_auto_reply_task, args=(item.id, interviewer.id, True), daemon=True)
                t.start()

            if _is_ajax_json(request):
                return JsonResponse(
                    {
                        "ok": True,
                        "status": "saved",
                        "reply_status": item.reply_status,
                        "reply_pending": bool(item.reply_started_at and item.reply_finished_at is None),
                        "draft_text": item.draft_text or "",
                        "draft_requires_confirmation": bool(item.draft_requires_confirmation),
                        "conversation": convo,
                        "lead_state": item.lead_state or "",
                        "response_type": _safe_json_list(item.response_type),
                        "communication_style": _safe_json_list(item.communication_style),
                    }
                )
            messages.success(request, "Карточка обновлена.")
            return redirect("{}?page={}&item={}".format(request.path, page_num, item_id))

        if form_type == "clear_draft":
            item_id_raw = request.POST.get("item_id")
            if not (item_id_raw and str(item_id_raw).isdigit()):
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
                messages.error(request, "Не выбран чат.")
                return redirect("{}?page={}".format(request.path, page_num))
            item_id = int(item_id_raw)
            item = qs.filter(pk=item_id).first()
            if not item:
                if _is_ajax_json(request):
                    return JsonResponse({"ok": False, "error": "not_found"}, status=404)
                messages.error(request, "Чат не найден.")
                return redirect("{}?page={}".format(request.path, page_num))
            item.draft_text = ""
            item.draft_requires_confirmation = False
            item.save(update_fields=["draft_text", "draft_requires_confirmation", "updated_at"])
            if _is_ajax_json(request):
                return JsonResponse({"ok": True, "status": "cleared"})
            return redirect("{}?page={}&item={}".format(request.path, page_num, item_id))

        messages.error(request, "Неизвестная форма.")
        return redirect("{}?page={}".format(request.path, page_num))

    paginator = Paginator(qs, 50)
    page_obj = paginator.get_page(page_num)

    selected_item = None
    selected_item_id = request.GET.get("item")
    chat_open = bool(selected_item_id and str(selected_item_id).isdigit())
    if selected_item_id and str(selected_item_id).isdigit():
        selected_item = qs.filter(pk=int(selected_item_id)).first()
    if not selected_item and page_obj.object_list:
        selected_item = page_obj.object_list[0]

    conversation = []
    selected_response_type = []
    selected_communication_style = []
    if selected_item:
        try:
            conversation = json.loads(selected_item.conversation_json or "[]")
            if not isinstance(conversation, list):
                conversation = []
        except Exception:
            conversation = []
        selected_response_type = _safe_json_list(selected_item.response_type)
        selected_communication_style = _safe_json_list(selected_item.communication_style)

    return render(
        request,
        "firmy/processing.html",
        {
            "page_obj": page_obj,
            "selected_item": selected_item,
            "conversation": conversation,
            "selected_response_type": selected_response_type,
            "selected_communication_style": selected_communication_style,
            "interviewer": interviewer,
            "processing_enabled": interviewer.processing_enabled,
            "evaluator": _selected_evaluator_agent(request),
            "chat_open": chat_open,
        },
    )


def _evaluation_context_for_generation(item: FirmyProcessingItem, max_chars: int = 7000) -> str:
    """
    Text from evaluator run (parser JSON + AI_Analysis), for interviewer generation context.
    Not tied to agent prompt_scope/strategy split — stored on FirmyProcessingItem.evaluation_text.
    """
    raw = (item.evaluation_text or "").strip()
    if not raw:
        return ""
    body = ""
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            ai = data.get("AI_Analysis")
            if isinstance(ai, str) and ai.strip():
                body = ai.strip()
            else:
                body = raw
        else:
            body = raw
    except Exception:
        body = raw
    if not body:
        return ""
    if len(body) > max_chars:
        body = body[:max_chars] + "\n[…]"
    return "\nHodnoceni webu a firmy (odborne JSON + AI_Analysis):\n{}\n".format(body)


def _build_generation_prompt(item: FirmyProcessingItem) -> list[dict]:
    premise = item.premise
    agent = item.assigned_agent
    try:
        convo = json.loads(item.conversation_json or "[]")
        if not isinstance(convo, list):
            convo = []
    except Exception:
        convo = []

    history_lines = []
    for m in convo[-20:]:
        if not isinstance(m, dict):
            continue
        # Explicit Czech roles: avoid COMPANY/ME (ambiguous). in=lead's firm, out=our agent.
        prefix = "OD_KLIENTA" if m.get("dir") == "in" else "OD_TEBE"
        # Use message summaries as primary context for generation.
        text = (m.get("summary") or "").strip().replace("\r", "")
        if not text:
            text = (m.get("text") or "").strip().replace("\r", "")
        if text:
            text = re.sub(r"^(Klient|Agent|CLIENT|AGENT)\s*:\s*", "", text, flags=re.I)
            history_lines.append(f"{prefix}: {text[:800]}")

    agent_label = (agent.name if agent else "") or "nas obchodni zastupce"
    system_parts = [
        "Jsi sales asistent ({}). Pises vzdy jako nas zastupce, nikoli jako firma klienta.".format(agent_label),
        "V historii znamena OD_KLIENTA = prichozi zprava od firmy na druhe strane; OD_TEBE = tvoje drivejsi odchozi zprava.",
        "Vygeneruj dalsi odchozi zpravu klientovi (pis jako TY / nas zastupce).",
        "Pis vyhradne cesky, kratce a vecne, 2-6 vet.",
        "Nevymyslej fakta. Zohledni historii dialogu.",
        "Nepridavej pozdrav ani podpis, generuj jen hlavni obsah zpravy.",
        "I kdyz je cast kontextu v jinem jazyce, vystup musi byt pouze v cestine.",
    ]
    strategy_prompt = _agent_strategy_prompt(agent)
    if strategy_prompt:
        system_parts.append("\nKontext odesilatele (strategie/taktika):\n" + strategy_prompt)
    system = " ".join(system_parts).strip()
    try:
        response_type = json.loads(item.response_type or "[]")
        if not isinstance(response_type, list):
            response_type = []
    except Exception:
        response_type = []
    try:
        communication_style = json.loads(item.communication_style or "[]")
        if not isinstance(communication_style, list):
            communication_style = []
    except Exception:
        communication_style = []

    lead_state = (item.lead_state or "").strip()
    lead_traits_block = (
        "Kontext leada:\n"
        f"- lead_state: {lead_state or '(prazdne)'}\n"
        f"- response_type: {', '.join([str(x) for x in response_type]) if response_type else '(prazdne)'}\n"
        f"- communication_style: {', '.join([str(x) for x in communication_style]) if communication_style else '(prazdne)'}\n"
    )
    evaluation_block = _evaluation_context_for_generation(item)
    user = (
        "Udaje o spolecnosti:\n"
        f"- Nazev: {premise.title}\n"
        f"- Kategorie: {premise.category}\n"
        f"- Adresa: {premise.address}\n"
        f"- Web: {premise.website_url}\n"
        f"- Email: {premise.emails}\n"
        f"- Telefony: {premise.phones}\n"
        "\nUdaje o odesilateli:\n"
        f"- Jmeno: {(agent.name if agent else '')}\n"
        f"- Email: {(agent.email if agent else '')}\n"
        f"- Telefon: {(agent.phone if agent else '')}\n"
        "\n"
        + evaluation_block
        + lead_traits_block
        + "\nHistorie konverzace (posledni zpravy):\n"
        + ("\n".join(history_lines) if history_lines else "(zatim prazdne)")
        + "\n\nUkol: vygeneruj pouze hlavni cast me dalsi zpravy. "
        "Povinne zohledni kontext leada (lead_state/response_type/communication_style). "
        "Vystup musi byt pouze v cestine."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _fallback_message_summary(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return ""
    lead = t[:260]
    nums = re.findall(r"(?:\d+[.,]?\d*\s*%|\d[\d\s.,]{0,12}\d\s*(?:Kc|CZK|EUR|USD|руб|RUB|€|\$)?)", t, re.I)
    uniq_nums: list[str] = []
    seen: set[str] = set()
    for n in nums:
        nn = re.sub(r"\s+", " ", n).strip()
        if not nn or nn in seen:
            continue
        seen.add(nn)
        uniq_nums.append(nn)
        if len(uniq_nums) >= 6:
            break
    if uniq_nums:
        return "{} Dulezite detaily: {}.".format(lead, ", ".join(uniq_nums))
    return lead


def _summarize_message_for_agent(text: str, interviewer: Optional[FirmyAgent], message_side: str = "out") -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    side = (message_side or "out").strip().lower()
    if side not in ("in", "out"):
        side = "out"
    actor_label = "Klient" if side == "in" else "Agent"
    # Hard guard: explicit rejection must stay rejection in summary.
    if _looks_rejecting_text(raw):
        if side == "in":
            return "Klient: Jasne odmitl nabidku a nema zajem o dalsi komunikaci."
        return "Agent: Zprava obsahuje odmitnuti dalsi komunikace."
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not interviewer or not api_key:
        base = _fallback_message_summary(raw)
        return "{}: {}".format(actor_label, base) if base else ""

    model = _normalize_openrouter_model_id(interviewer.model_name)
    system = (
        "Jsi asistent pro CRM sumarizaci. "
        "Vystup musi byt vzdy v cestine, i kdyz puvodni zprava je v jinem jazyce. "
        "Zkrat zpravu na 1-3 kratke vety a zachovej jen hlavni myslenku a fakticke detaily. "
        "Nikdy nemen vyznam, tonalitu ani zamer zpravy (hlavne nezajem/odmitnuti). "
        "Nezamenuj role mluvciho: mluvci je pevne zadany."
    )
    user = (
        "Udelej kratke shrnuti zpravy pro CRM v cestine. "
        + "Zachovej dulezite detaily: cisla, procenta, castky, terminy, data, kontakty. "
        + "Nepouzivej odrazky, jen souvisly kratky text.\n"
        + "Mluvci teto zpravy je: {}. Tuto roli nesmis menit.\n\n".format(actor_label)
        + "Zprava:\n"
        + raw[:6000]
    )
    try:
        out = chat_completion(
            api_key=api_key,
            base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.1,
            max_tokens=180,
            timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "35"),
        )
        clean = re.sub(r"\s+", " ", (out or "").strip())
        clean = re.sub(r"^(klient|agent)\s*:\s*", "", clean, flags=re.I)
        if clean:
            return "{}: {}".format(actor_label, clean)
        base = _fallback_message_summary(raw)
        return "{}: {}".format(actor_label, base) if base else ""
    except Exception:
        base = _fallback_message_summary(raw)
        return "{}: {}".format(actor_label, base) if base else ""


def _ensure_conversation_summaries(convo: list[dict], interviewer: Optional[FirmyAgent]) -> tuple[list[dict], bool]:
    changed = False
    for m in convo:
        if not isinstance(m, dict):
            continue
        text = (m.get("text") or "").strip()
        if not text:
            continue
        current = (m.get("summary") or "").strip()
        if current:
            continue
        m["summary"] = _summarize_message_for_agent(text, interviewer, m.get("dir") or "out") or text
        changed = True
    return convo, changed


def _apply_fixed_greeting_and_signoff(core_text: str, agent: Optional[FirmyAgent]) -> str:
    core = (core_text or "").strip()
    if not core:
        core = "Rad bych kratce navrhl napad, ktery by pro vas mohl byt uzitecny."
    # In interviewer chat mode keep text natural without forced greeting/signoff wrapper.
    if agent and agent.role == FirmyAgent.ROLE_INTERVIEWER:
        return core
    greeting = "Dobry den!"
    name = (agent.name if agent else "") or "Tym"
    signoff = "S pozdravem,\n{}".format(name)
    return "{}\n\n{}\n\n{}".format(greeting, core, signoff)


def _looks_rejecting_text(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    norm = _normalize_text_for_match(t)
    if _has_czech_rejecting_markers(norm):
        return True
    return any(re.search(rx, t, re.I) for rx in LOOKS_REJECTING_REGEXES)


def _is_likely_cutoff(text: str) -> bool:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if not t:
        return True
    if t.endswith((".", "!", "?", "…")):
        return False
    last = t.split(" ")[-1].lower()
    cut_tokens = {
        "не",
        "но",
        "и",
        "а",
        "что",
        "чтобы",
        "если",
        "когда",
        "по",
        "в",
        "на",
        "с",
        "для",
    }
    return (last in cut_tokens) or (len(t) < 22)


def _safe_core_or_fallback(core_text: str, *, rejecting_case: bool = False) -> str:
    t = re.sub(r"\s+", " ", (core_text or "").strip())
    if rejecting_case:
        return "Rozumim, dekuji za odpoved. Uz nebudu rusit. Pokud se situace zmeni, rad se ozvu znovu."
    if not t or _is_likely_cutoff(t):
        return "Rozumim, dekuji za zpetnou vazbu. Pokud vam to bude vyhovovat, mohu se pozdeji vratit s presnejsim navrhem."
    return t


def _last_incoming_text(convo: list[dict]) -> str:
    for m in reversed(convo):
        if isinstance(m, dict) and m.get("dir") == "in":
            text = (m.get("text") or "").strip()
            if text:
                return text
    return ""


def _route_question_by_prompt(
    *,
    interviewer: Optional[FirmyAgent],
    convo: list[dict],
    item: Optional[FirmyProcessingItem] = None,
) -> tuple[bool, str]:
    """
    Returns (should_escalate_to_owner, reason).
    """
    if not interviewer:
        return False, ""
    scope = _agent_scope_prompt(interviewer)
    if not scope:
        return False, ""
    question = _last_incoming_text(convo)
    if not question or "?" not in question:
        return False, ""

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        return False, ""

    history_lines = []
    for m in convo[-12:]:
        if not isinstance(m, dict):
            continue
        role = "CLIENT" if m.get("dir") == "in" else "AGENT"
        text = (m.get("summary") or m.get("text") or "").strip()
        if text:
            history_lines.append(f"{role}: {text[:500]}")

    company = ""
    if item and item.premise_id:
        company = f"\nCompany: {item.premise.title}\nCategory: {item.premise.category}\n"

    system = (
        "You are a strict routing classifier for sales chat. "
        "Decide if the agent can answer the client's latest question within allowed scope from AGENT_SCOPE. "
        "If out of scope -> route owner. If in scope -> route agent. "
        "Return JSON only: {\"route\":\"agent|owner\",\"reason\":\"short reason\"}."
    )
    user = (
        "AGENT_SCOPE:\n"
        + scope[:5000]
        + "\n\n"
        + "LATEST_CLIENT_QUESTION:\n"
        + question[:1200]
        + "\n\n"
        + "RECENT_HISTORY:\n"
        + ("\n".join(history_lines) if history_lines else "(empty)")
        + company
    )
    model = _normalize_openrouter_model_id(interviewer.model_name)
    try:
        raw = chat_completion(
            api_key=api_key,
            base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
            temperature=0.0,
            max_tokens=120,
            timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "35"),
        )
        parsed = json.loads((raw or "").strip())
        if not isinstance(parsed, dict):
            return False, ""
        route = str(parsed.get("route") or "").strip().lower()
        reason = str(parsed.get("reason") or "").strip()
        if route == "owner":
            return True, reason or "out_of_scope"
        return False, reason
    except Exception:
        return False, ""


def _generate_agent_message_for_item(item: FirmyProcessingItem, interviewer: FirmyAgent) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        raise RuntimeError("Не настроен ключ LLM (OPENROUTER_API_KEY).")
    try:
        convo = json.loads(item.conversation_json or "[]")
        if not isinstance(convo, list):
            convo = []
    except Exception:
        convo = []
    last_incoming = ""
    for m in reversed(convo):
        if isinstance(m, dict) and m.get("dir") == "in":
            last_incoming = (m.get("text") or "").strip()
            if last_incoming:
                break
    # Fast-path for explicit rejection: avoid long/hanging model calls.
    if _looks_rejecting_text(last_incoming):
        return _apply_fixed_greeting_and_signoff(_safe_core_or_fallback("", rejecting_case=True), interviewer)

    model = _normalize_openrouter_model_id(interviewer.model_name)
    max_tokens = interviewer.token_limit if interviewer.token_limit else 400
    core = chat_completion(
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        messages=_build_generation_prompt(item),
        model=model,
        temperature=0.4,
        max_tokens=max_tokens,
        timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "40"),
    )
    return _apply_fixed_greeting_and_signoff(_safe_core_or_fallback(core), interviewer)


def _run_auto_reply_task(item_id: int, interviewer_id: int, auto_send: bool = False) -> None:
    from django.db import close_old_connections

    close_old_connections()
    try:
        item = FirmyProcessingItem.objects.select_related("assigned_agent", "premise").get(pk=item_id)
    except Exception:
        return
    if not item.assigned_agent_id or item.assigned_agent_id != interviewer_id:
        return
    interviewer = item.assigned_agent
    if not interviewer:
        return
    if not _is_item_running(item, interviewer):
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            reply_status=FirmyProcessingItem.REPLY_IDLE,
            reply_finished_at=timezone.now(),
        )
        close_old_connections()
        return

    try:
        if not _wait_before_reply(item_id, interviewer_id):
            FirmyProcessingItem.objects.filter(pk=item_id).update(
                reply_status=FirmyProcessingItem.REPLY_IDLE,
                reply_finished_at=timezone.now(),
            )
            close_old_connections()
            return
        item = FirmyProcessingItem.objects.select_related("assigned_agent", "premise").get(pk=item_id)
        interviewer = item.assigned_agent
        if not interviewer or not _is_item_running(item, interviewer):
            FirmyProcessingItem.objects.filter(pk=item_id).update(
                reply_status=FirmyProcessingItem.REPLY_IDLE,
                reply_finished_at=timezone.now(),
            )
            close_old_connections()
            return
        try:
            convo = json.loads(item.conversation_json or "[]")
            if not isinstance(convo, list):
                convo = []
        except Exception:
            convo = []
        convo, convo_changed = _ensure_conversation_summaries(convo, interviewer)
        has_outgoing = any(isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip() for m in convo)
        need_reply = _has_unanswered_incoming(convo)
        need_init = not has_outgoing
        if not (need_reply or need_init):
            if convo_changed:
                item.conversation_json = json.dumps(convo, ensure_ascii=False)
                item.reply_status = FirmyProcessingItem.REPLY_DONE
                item.reply_error = ""
                item.reply_finished_at = timezone.now()
                item.save(update_fields=["conversation_json", "reply_status", "reply_error", "reply_finished_at", "updated_at"])
            else:
                FirmyProcessingItem.objects.filter(pk=item_id).update(
                    reply_status=FirmyProcessingItem.REPLY_DONE,
                    reply_error="",
                    reply_finished_at=timezone.now(),
                )
            close_old_connections()
            return
        should_escalate, esc_reason = _route_question_by_prompt(interviewer=interviewer, convo=convo, item=item)
        if should_escalate:
            item.reply_status = FirmyProcessingItem.REPLY_ERROR
            item.reply_error = "ESCALATE_OWNER: {}".format((esc_reason or "out_of_scope")[:300])
            item.reply_finished_at = timezone.now()
            item.save(update_fields=["reply_status", "reply_error", "reply_finished_at", "updated_at"])
            close_old_connections()
            return
        item.reply_status = FirmyProcessingItem.REPLY_RUNNING
        item.reply_error = ""
        item.save(update_fields=["reply_status", "reply_error", "updated_at"])
        reply = _generate_agent_message_for_item(item, interviewer)
        if (reply or "").strip() and auto_send:
            reply_summary = _summarize_message_for_agent(reply.strip(), interviewer, "out")
            convo.append(
                {
                    "ts": timezone.now().isoformat(),
                    "dir": "out",
                    "agent_id": interviewer.id,
                    "agent_name": interviewer.name,
                    "text": reply.strip(),
                    "summary": reply_summary or reply.strip(),
                }
            )
        if auto_send:
            item.conversation_json = json.dumps(convo, ensure_ascii=False)
            item.was_contacted = any((m.get("dir") == "out") for m in convo if isinstance(m, dict))
            item.was_answered = any((m.get("dir") == "in") for m in convo if isinstance(m, dict))
            item.draft_text = ""
            item.draft_requires_confirmation = False
            prev_lead_state = (item.lead_state or "").strip()
            trait_fields = _apply_lead_traits_from_convo(item, convo, item.assigned_agent, use_llm=False)
            convo, lost_fields = _apply_lost_post_actions(item, convo, interviewer, prev_lead_state=prev_lead_state)
            trait_fields.extend(lost_fields)
            item.reply_status = FirmyProcessingItem.REPLY_DONE
            item.reply_error = ""
            item.reply_finished_at = timezone.now()
            item.save(
                update_fields=list(dict.fromkeys([
                    "conversation_json",
                    "was_contacted",
                    "was_answered",
                    "do_not_contact",
                    "paused_individual",
                    "draft_text",
                    "draft_requires_confirmation",
                    "reply_status",
                    "reply_error",
                    "reply_finished_at",
                    "updated_at",
                ] + trait_fields))
            )
        else:
            item.draft_text = (reply or "").strip()
            item.draft_requires_confirmation = True
            item.reply_status = FirmyProcessingItem.REPLY_DONE
            item.reply_error = ""
            item.reply_finished_at = timezone.now()
            item.save(
                update_fields=[
                    "draft_text",
                    "draft_requires_confirmation",
                    "reply_status",
                    "reply_error",
                    "reply_finished_at",
                    "updated_at",
                ]
            )
    except Exception as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            reply_status=FirmyProcessingItem.REPLY_ERROR,
            reply_error=str(e),
            reply_finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


def _run_generation_task(item_id: int, interviewer_id: int, make_draft: bool = True) -> None:
    from django.db import close_old_connections

    close_old_connections()
    try:
        item = FirmyProcessingItem.objects.select_related("premise", "assigned_agent").get(pk=item_id)
    except Exception:
        return
    if not item.assigned_agent_id or item.assigned_agent_id != interviewer_id:
        return

    try:
        interviewer = item.assigned_agent
        if not interviewer:
            raise RuntimeError("Не выбран собеседник для генерации.")
        try:
            convo = json.loads(item.conversation_json or "[]")
            if not isinstance(convo, list):
                convo = []
        except Exception:
            convo = []
        should_escalate, esc_reason = _route_question_by_prompt(interviewer=interviewer, convo=convo, item=item)
        if should_escalate:
            FirmyProcessingItem.objects.filter(pk=item_id).update(
                gen_status=FirmyProcessingItem.GEN_ERROR,
                gen_error="ESCALATE_OWNER: {}".format((esc_reason or "out_of_scope")[:300]),
                gen_finished_at=timezone.now(),
            )
            close_old_connections()
            return
        convo, convo_changed = _ensure_conversation_summaries(convo, interviewer)
        if convo_changed:
            item.conversation_json = json.dumps(convo, ensure_ascii=False)
            item.save(update_fields=["conversation_json", "updated_at"])
        prev_lead_state = (item.lead_state or "").strip()
        trait_fields = _apply_lead_traits_from_convo(item, convo, interviewer, use_llm=True)
        convo, lost_fields = _apply_lost_post_actions(item, convo, interviewer, prev_lead_state=prev_lead_state)
        trait_fields.extend(lost_fields)
        if trait_fields:
            item.save(update_fields=list(dict.fromkeys(trait_fields + ["updated_at"])))

        draft = _generate_agent_message_for_item(item, interviewer)
        if make_draft:
            FirmyProcessingItem.objects.filter(pk=item_id).update(
                draft_text=draft,
                draft_requires_confirmation=True,
                gen_status=FirmyProcessingItem.GEN_DONE,
                gen_error="",
                gen_finished_at=timezone.now(),
            )
        else:
            msg = (draft or "").strip()
            if msg:
                convo.append(
                    {
                        "ts": timezone.now().isoformat(),
                        "dir": "out",
                        "agent_id": interviewer.id,
                        "agent_name": interviewer.name,
                        "text": msg,
                        "summary": _summarize_message_for_agent(msg, interviewer, "out") or msg,
                    }
                )
            item.conversation_json = json.dumps(convo, ensure_ascii=False)
            item.was_contacted = any((m.get("dir") == "out") for m in convo if isinstance(m, dict))
            item.was_answered = any((m.get("dir") == "in") for m in convo if isinstance(m, dict))
            item.draft_text = ""
            item.draft_requires_confirmation = False
            prev_lead_state = (item.lead_state or "").strip()
            trait_fields = _apply_lead_traits_from_convo(item, convo, interviewer, use_llm=False)
            convo, lost_fields = _apply_lost_post_actions(item, convo, interviewer, prev_lead_state=prev_lead_state)
            trait_fields.extend(lost_fields)
            item.gen_status = FirmyProcessingItem.GEN_DONE
            item.gen_error = ""
            item.gen_finished_at = timezone.now()
            item.save(
                update_fields=list(dict.fromkeys([
                    "conversation_json",
                    "was_contacted",
                    "was_answered",
                    "do_not_contact",
                    "paused_individual",
                    "draft_text",
                    "draft_requires_confirmation",
                    "gen_status",
                    "gen_error",
                    "gen_finished_at",
                    "updated_at",
                ] + trait_fields))
            )
    except RuntimeError as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            gen_status=FirmyProcessingItem.GEN_ERROR,
            gen_error=str(e),
            gen_finished_at=timezone.now(),
        )
    except LLMError as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            gen_status=FirmyProcessingItem.GEN_ERROR,
            gen_error=str(e),
            gen_finished_at=timezone.now(),
        )
    except Exception as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            gen_status=FirmyProcessingItem.GEN_ERROR,
            gen_error=str(e),
            gen_finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


@require_http_methods(["POST"])
def processing_generate_start(request):
    interviewer = _selected_interviewer_agent(request)
    if not interviewer:
        return JsonResponse({"ok": False, "error": "not_allowed"}, status=403)

    item_id_raw = request.POST.get("item_id")
    if not (item_id_raw and str(item_id_raw).isdigit()):
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
    item_id = int(item_id_raw)

    item = FirmyProcessingItem.objects.filter(pk=item_id, assigned_agent=interviewer).first()
    if not item:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    # If already running — don't start another.
    if item.gen_status == FirmyProcessingItem.GEN_RUNNING:
        return JsonResponse({"ok": True, "status": item.gen_status})

    FirmyProcessingItem.objects.filter(pk=item_id).update(
        gen_status=FirmyProcessingItem.GEN_RUNNING,
        gen_error="",
        gen_started_at=timezone.now(),
        gen_finished_at=None,
    )

    role_mode = (request.POST.get("role_mode") or "agent").strip().lower()
    make_draft = role_mode != "firm"
    t = threading.Thread(target=_run_generation_task, args=(item_id, interviewer.id, make_draft), daemon=True)
    t.start()

    return JsonResponse({"ok": True, "status": FirmyProcessingItem.GEN_RUNNING})


@require_http_methods(["GET"])
def processing_generate_status(request):
    interviewer = _selected_interviewer_agent(request)
    if not interviewer:
        return JsonResponse({"ok": False, "error": "not_allowed"}, status=403)

    item_id_raw = request.GET.get("item_id")
    if not (item_id_raw and str(item_id_raw).isdigit()):
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
    item_id = int(item_id_raw)

    item = FirmyProcessingItem.objects.filter(pk=item_id, assigned_agent=interviewer).first()
    if not item:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    return JsonResponse(
        {
            "ok": True,
            "status": item.gen_status,
            "error": item.gen_error,
            "draft_text": item.draft_text,
            "draft_requires_confirmation": bool(item.draft_requires_confirmation),
        }
    )


@require_http_methods(["GET"])
def processing_reply_status(request):
    interviewer = _selected_interviewer_agent(request)
    if not interviewer:
        return JsonResponse({"ok": False, "error": "not_allowed"}, status=403)

    item_id_raw = request.GET.get("item_id")
    if not (item_id_raw and str(item_id_raw).isdigit()):
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
    item_id = int(item_id_raw)

    item = FirmyProcessingItem.objects.filter(pk=item_id, assigned_agent=interviewer).first()
    if not item:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    try:
        convo = json.loads(item.conversation_json or "[]")
        if not isinstance(convo, list):
            convo = []
    except Exception:
        convo = []
    status = item.reply_status
    # Self-heal stale "running": only after we already have outgoing message
    # and there is no unanswered incoming left.
    if status == FirmyProcessingItem.REPLY_RUNNING:
        has_outgoing = any(
            isinstance(m, dict) and m.get("dir") == "out" and (m.get("text") or "").strip()
            for m in convo
        )
        if has_outgoing and not _has_unanswered_incoming(convo):
            item.reply_status = FirmyProcessingItem.REPLY_DONE
            item.reply_error = ""
            item.reply_finished_at = timezone.now()
            item.save(update_fields=["reply_status", "reply_error", "reply_finished_at", "updated_at"])
            status = item.reply_status
    return JsonResponse(
        {
            "ok": True,
            "status": status,
            "reply_pending": bool(item.reply_started_at and item.reply_finished_at is None),
            "error": item.reply_error,
            "draft_text": item.draft_text or "",
            "draft_requires_confirmation": bool(item.draft_requires_confirmation),
            "conversation": convo,
            "lead_state": item.lead_state or "",
            "response_type": _safe_json_list(item.response_type),
            "communication_style": _safe_json_list(item.communication_style),
        }
    )


def _build_evaluation_prompt(item: FirmyProcessingItem, evaluator: FirmyAgent, parsed_text: str, parsed_profile: dict) -> list[dict]:
    premise = item.premise
    system_parts = [
        "Ты — оценщик лидов и компаний для B2B outreach.",
        "Верни только полезную структурированную оценку на русском языке.",
        "Формат: 1) Краткий вывод 2) Плюсы 3) Риски 4) Что писать дальше.",
        "Опирайся только на переданные данные, не выдумывай факты.",
    ]
    evaluator_prompt = _agent_strategy_prompt(evaluator)
    if evaluator_prompt:
        system_parts.append("\nКонтекст оценщика (персона/задача):\n" + evaluator_prompt)
    system = " ".join(system_parts).strip()

    compact_text = (parsed_text or "")[:40000]
    user = (
        "Данные компании:\n"
        f"- Название: {premise.title}\n"
        f"- Категория: {premise.category}\n"
        f"- Адрес: {premise.address}\n"
        f"- Сайт: {premise.website_url}\n"
        f"- Email: {premise.emails}\n"
        f"- Телефоны: {premise.phones}\n"
        "\nОчищенный текст с сайта:\n"
        f"{compact_text}\n"
        "\nСтруктурированный JSON-профиль сайта (неизвестные поля = null):\n"
        + json.dumps(parsed_profile or {}, ensure_ascii=False, indent=2)
        + "\n\nЗадача: дай экспертный комментарий к этому профилю как к лиду."
        "\nВерни только текст раздела AI_Analysis без JSON-обертки."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _merge_unique_long_chunks(texts: list[str], min_chars: int = 180) -> str:
    """
    Merge parsed texts and remove repeated long paragraphs/chunks
    across all pages while preserving first appearance.
    """
    chunks: list[str] = []
    for text in texts:
        if not text:
            continue
        chunks.extend([c.strip() for c in re.split(r"\n\s*\n+", text) if c.strip()])

    seen: set[str] = set()
    out: list[str] = []
    for chunk in chunks:
        key = re.sub(r"[^\w\s]+", "", re.sub(r"\s+", " ", chunk.strip().lower()))
        if len(key) >= min_chars:
            if key in seen:
                continue
            seen.add(key)
        out.append(chunk)
    return "\n\n".join(out).strip()


def _canonical_link(url: str) -> str:
    s = (url or "").strip()
    if not s:
        return ""
    try:
        p = urlsplit(s)
    except Exception:
        return s
    path = (p.path or "").rstrip("/")
    # Drop fragment; keep query because it can represent distinct resource/page state.
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def _norm_host(host: str) -> str:
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def _is_internal_link_for_base(link: str, base_url: str) -> bool:
    try:
        return _norm_host(urlsplit(link).netloc) == _norm_host(urlsplit(base_url).netloc)
    except Exception:
        return False


def _only_internal_links(links: list[str], base_url: str) -> list[str]:
    out: list[str] = []
    for link in links or []:
        s = (link or "").strip()
        if not s:
            continue
        if _is_internal_link_for_base(s, base_url):
            out.append(s)
    seen: set[str] = set()
    deduped: list[str] = []
    for link in out:
        key = _canonical_link(link) or link
        if key in seen:
            continue
        seen.add(key)
        deduped.append(link)
    return deduped


def _set_eval_stage(item_id: int, stage: str) -> None:
    FirmyProcessingItem.objects.filter(pk=item_id).update(eval_stage=stage or "", updated_at=timezone.now())


def _write_parser_dump_file(
    item: FirmyProcessingItem,
    source_url: str,
    parsed_items: list,
    all_links_raw: list[str],
    merged_text: str,
    unique_links: list[str],
    merged_profile: dict,
) -> Optional[str]:
    """
    Save parser output into a human-readable .txt debug file.
    Returns absolute path on success.
    """
    try:
        base_dir = os.environ.get("PARSER_DUMP_DIR") or str(Path(settings.BASE_DIR) / "parser_dumps")
        dump_dir = Path(base_dir)
        dump_dir.mkdir(parents=True, exist_ok=True)
        file_name = "parser_eval_item_{item_id}.txt".format(item_id=item.id)
        out_path = dump_dir / file_name

        lines: list[str] = []
        lines.append("Parser Evaluation Dump")
        lines.append("=" * 80)
        lines.append("item_id: {}".format(item.id))
        lines.append("premise_id: {}".format(item.premise_id))
        lines.append("source_url: {}".format(source_url))
        lines.append("created_at: {}".format(timezone.now().isoformat()))
        lines.append("")
        lines.append("Parsed Pages")
        lines.append("-" * 80)
        for idx, p in enumerate(parsed_items, start=1):
            lines.append("[{}] {}".format(idx, getattr(p, "source_url", "") or ""))
            lines.append("  text_len: {}".format(len((getattr(p, "text", "") or "").strip())))
            links = getattr(p, "links", None) or []
            lines.append("  links: {}".format(len(links)))
        lines.append("")
        lines.append("Collected Content By Page (raw parser text)")
        lines.append("-" * 80)
        for idx, p in enumerate(parsed_items, start=1):
            page_url = getattr(p, "source_url", "") or ""
            page_text = (getattr(p, "text", "") or "").strip()
            lines.append("[PAGE {}] {}".format(idx, page_url or "(no url)"))
            if page_text:
                lines.append(page_text)
            else:
                lines.append("(empty text)")
            lines.append("")
        lines.append("")
        lines.append("All Links By Page (raw)")
        lines.append("-" * 80)
        for idx, p in enumerate(parsed_items, start=1):
            page_url = getattr(p, "source_url", "") or ""
            page_links = _only_internal_links(list(getattr(p, "links", None) or []), source_url)
            lines.append("[PAGE {}] {} | links={}".format(idx, page_url or "(no url)", len(page_links)))
            if page_links:
                lines.extend([str(x) for x in page_links])
            else:
                lines.append("(none)")
            lines.append("")
        lines.append("")
        lines.append("All Links (merged raw, with duplicates)")
        lines.append("-" * 80)
        all_links_dump = _only_internal_links(list(all_links_raw or []), source_url)
        if all_links_dump:
            lines.extend([str(x) for x in all_links_dump if x])
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("")
        lines.append("Unique Links (merged)")
        lines.append("-" * 80)
        unique_dump_links = _only_internal_links(list(unique_links or []), source_url)
        lines.extend(unique_dump_links or ["(none)"])
        lines.append("")
        lines.append("Merged Profile JSON")
        lines.append("-" * 80)
        lines.append(json.dumps(merged_profile or {}, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("Merged Text")
        lines.append("-" * 80)
        lines.append(merged_text or "")

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)
    except Exception:
        return None


def _run_evaluation_task(item_id: int, interviewer_id: int, evaluator_id: int) -> None:
    from django.db import close_old_connections

    close_old_connections()
    try:
        item = FirmyProcessingItem.objects.select_related("premise", "assigned_agent").get(pk=item_id)
    except Exception:
        return
    if not item.assigned_agent_id or item.assigned_agent_id != interviewer_id:
        return
    _set_eval_stage(item_id, "parsing")
    evaluator = FirmyAgent.objects.filter(pk=evaluator_id, role=FirmyAgent.ROLE_EVALUATOR).first()
    if not evaluator:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error="Не выбран корректный оценщик.",
            eval_finished_at=timezone.now(),
        )
        close_old_connections()
        return

    site_url = (item.premise.website_url or "").strip()
    if not site_url:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error="У компании не указан сайт.",
            eval_finished_at=timezone.now(),
        )
        close_old_connections()
        return

    api_key = os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
    if not api_key:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error="Не настроен ключ LLM (OPENROUTER_API_KEY).",
            eval_finished_at=timezone.now(),
        )
        close_old_connections()
        return

    try:
        parsed = parse_url_to_content(site_url)
    except Exception as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error="Ошибка парсинга сайта: {}".format(e),
            eval_finished_at=timezone.now(),
        )
        close_old_connections()
        return

    # Parse collected internal links as well (up to 5), then merge unique content.
    _set_eval_stage(item_id, "reparsing")
    raw_extra_links = parsed.links or []
    seen_extra: set[str] = set()
    extra_links: list[str] = []
    for link in raw_extra_links:
        canon = _canonical_link(link)
        if not canon or canon in seen_extra or canon == _canonical_link(site_url):
            continue
        seen_extra.add(canon)
        extra_links.append(link)
        if len(extra_links) >= 5:
            break
    parsed_items = [parsed]
    all_texts = [parsed.text or ""]
    all_links = list(parsed.links or [])
    for link in extra_links:
        try:
            extra = parse_url_to_content(link)
        except Exception:
            continue
        parsed_items.append(extra)
        all_texts.append(extra.text or "")
        if extra.links:
            all_links.extend(extra.links)
    merged_text = _merge_unique_long_chunks(all_texts)
    # Keep links unique, keep order.
    seen_links: set[str] = set()
    unique_links: list[str] = []
    for link in all_links:
        if not link or link in seen_links:
            continue
        seen_links.add(link)
        unique_links.append(link)
    all_links = _only_internal_links(all_links, site_url)
    unique_links = _only_internal_links(unique_links, site_url)
    _set_eval_stage(item_id, "analysis")
    merged_profile = merge_parsed_contents(parsed_items)
    parser_dump_file = _write_parser_dump_file(
        item=item,
        source_url=site_url,
        parsed_items=parsed_items,
        all_links_raw=all_links,
        merged_text=merged_text,
        unique_links=unique_links,
        merged_profile=merged_profile,
    )
    evaluation_payload = {
        "source_url": site_url,
        "links": unique_links,
        "text": merged_text,
    }
    if parser_dump_file:
        evaluation_payload["parser_dump_file"] = parser_dump_file
    # Keep parser output as base object.
    evaluation_payload.update(merged_profile)

    model = _normalize_openrouter_model_id(evaluator.model_name)
    max_tokens = evaluator.token_limit if evaluator.token_limit else 700
    _set_eval_stage(item_id, "generating")
    try:
        ai_analysis = chat_completion(
            api_key=api_key,
            base_url=os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
            messages=_build_evaluation_prompt(item, evaluator, merged_text, merged_profile),
            model=model,
            temperature=0.2,
            max_tokens=max_tokens,
            timeout_s=float(os.environ.get("OPENROUTER_TIMEOUT_S") or "90"),
        )
        clean_analysis = re.sub(r"\s+", " ", (ai_analysis or "").strip())
        evaluation_payload["AI_Analysis"] = clean_analysis or None
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            evaluation_text=json.dumps(evaluation_payload, ensure_ascii=False, indent=2),
            eval_status=FirmyProcessingItem.EVAL_DONE,
            eval_stage="done",
            eval_error="",
            eval_finished_at=timezone.now(),
        )
    except LLMError as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error=str(e),
            eval_finished_at=timezone.now(),
        )
    except Exception as e:
        FirmyProcessingItem.objects.filter(pk=item_id).update(
            eval_status=FirmyProcessingItem.EVAL_ERROR,
            eval_stage="error",
            eval_error=str(e),
            eval_finished_at=timezone.now(),
        )
    finally:
        close_old_connections()


@require_http_methods(["POST"])
def processing_evaluate_start(request):
    interviewer = _selected_interviewer_agent(request)
    evaluator = _selected_evaluator_agent(request)
    if not interviewer or not evaluator:
        return JsonResponse({"ok": False, "error": "not_allowed"}, status=403)

    item_id_raw = request.POST.get("item_id")
    if not (item_id_raw and str(item_id_raw).isdigit()):
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
    item_id = int(item_id_raw)

    item = FirmyProcessingItem.objects.select_related("premise").filter(pk=item_id, assigned_agent=interviewer).first()
    if not item:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    if not (item.premise.website_url or "").strip():
        return JsonResponse({"ok": False, "error": "no_website"}, status=400)

    if item.eval_status == FirmyProcessingItem.EVAL_RUNNING:
        return JsonResponse({"ok": True, "status": item.eval_status})

    FirmyProcessingItem.objects.filter(pk=item_id).update(
        eval_status=FirmyProcessingItem.EVAL_RUNNING,
        eval_stage="parsing",
        eval_error="",
        eval_started_at=timezone.now(),
        eval_finished_at=None,
    )

    t = threading.Thread(target=_run_evaluation_task, args=(item_id, interviewer.id, evaluator.id), daemon=True)
    t.start()

    return JsonResponse({"ok": True, "status": FirmyProcessingItem.EVAL_RUNNING, "stage": "parsing"})


@require_http_methods(["GET"])
def processing_evaluate_status(request):
    interviewer = _selected_interviewer_agent(request)
    evaluator = _selected_evaluator_agent(request)
    if not interviewer or not evaluator:
        return JsonResponse({"ok": False, "error": "not_allowed"}, status=403)

    item_id_raw = request.GET.get("item_id")
    if not (item_id_raw and str(item_id_raw).isdigit()):
        return JsonResponse({"ok": False, "error": "bad_request"}, status=400)
    item_id = int(item_id_raw)

    item = FirmyProcessingItem.objects.filter(pk=item_id, assigned_agent=interviewer).first()
    if not item:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)

    return JsonResponse(
        {
            "ok": True,
            "status": item.eval_status,
            "stage": item.eval_stage or "",
            "error": item.eval_error,
            "evaluation_text": item.evaluation_text,
            "has_evaluation": bool((item.evaluation_text or "").strip()),
        }
    )


def _agent_payload_from_request(request):
    prompt_scope = (request.POST.get("prompt_scope") or "").strip()
    prompt_strategy = (request.POST.get("prompt_strategy") or "").strip()
    return {
        "name": (request.POST.get("name") or "").strip(),
        "avatar": ((request.POST.get("avatar") or "").strip() or "👤")[:8],
        "email": (request.POST.get("email") or "").strip(),
        "phone": (request.POST.get("phone") or "").strip(),
        "system_prompt": (request.POST.get("system_prompt") or "").strip() or prompt_strategy,
        "prompt_scope": prompt_scope,
        "prompt_strategy": prompt_strategy,
        "model_name": (request.POST.get("model_name") or "").strip(),
        "role": (request.POST.get("role") or "").strip(),
        "token_limit": request.POST.get("token_limit") or "0",
    }


def _validate_agent_payload(payload):
    errors = []
    if not payload["name"]:
        errors.append("Укажите имя агента.")
    if payload["role"] not in (FirmyAgent.ROLE_SEARCHER, FirmyAgent.ROLE_EVALUATOR, FirmyAgent.ROLE_INTERVIEWER):
        errors.append("Выберите роль агента.")
    try:
        token_limit = int(payload["token_limit"])
        if token_limit < 0:
            raise ValueError
    except Exception:
        errors.append("Лимит токенов должен быть неотрицательным числом.")
        token_limit = 0

    if payload["role"] == FirmyAgent.ROLE_INTERVIEWER:
        if not payload["email"]:
            errors.append("Для роли 'собеседник' email обязателен.")
        if not payload["phone"]:
            errors.append("Для роли 'собеседник' номер телефона обязателен.")

    if payload["model_name"] and payload["model_name"] not in POPULAR_MODELS.values():
        errors.append("Выберите модель из списка.")

    payload["token_limit"] = token_limit
    return errors


@require_http_methods(["GET", "POST"])
def agents(request):
    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "select":
            agent_id = request.POST.get("agent_id")
            slot = (request.POST.get("slot") or "").strip()
            if agent_id and str(agent_id).isdigit() and slot in ("interviewer", "evaluator"):
                agent = FirmyAgent.objects.filter(pk=int(agent_id)).first()
                if not agent:
                    messages.error(request, "Не удалось выбрать агента.")
                    return redirect(request.path)
                if slot == "interviewer":
                    if agent.role != FirmyAgent.ROLE_INTERVIEWER:
                        messages.error(request, "В слот 'собеседник' можно выбрать только агента с ролью 'собеседник'.")
                        return redirect(request.path)
                    request.session["selected_interviewer_agent_id"] = agent.id
                    messages.success(request, "Собеседник выбран: {}.".format(agent.name))
                    return redirect(request.POST.get("next") or request.path)
                if slot == "evaluator":
                    if agent.role != FirmyAgent.ROLE_EVALUATOR:
                        messages.error(request, "В слот 'оценщик' можно выбрать только агента с ролью 'оценщик'.")
                        return redirect(request.path)
                    request.session["selected_evaluator_agent_id"] = agent.id
                    messages.success(request, "Оценщик выбран: {}.".format(agent.name))
                    return redirect(request.POST.get("next") or request.path)
            messages.error(request, "Не удалось выбрать агента.")
            return redirect(request.path)
    return render(
        request,
        "firmy/agents.html",
        {
            "agents": FirmyAgent.objects.all(),
            "selected_interviewer": _selected_interviewer_agent(request),
            "selected_evaluator": _selected_evaluator_agent(request),
        },
    )


@require_http_methods(["GET", "POST"])
def agent_new(request):
    payload = {
        "name": "",
        "avatar": "👤",
        "email": "",
        "phone": "",
        "system_prompt": "",
        "prompt_scope": "",
        "prompt_strategy": "",
        "model_name": "",
        "token_limit": 0,
        "role": FirmyAgent.ROLE_SEARCHER,
    }
    if request.method == "POST":
        payload = _agent_payload_from_request(request)
        errors = _validate_agent_payload(payload)
        if not errors:
            agent = FirmyAgent.objects.create(**payload)
            messages.success(request, "Агент создан.")
            return redirect("firmy:agent_settings", agent_id=agent.id)
        for e in errors:
            messages.error(request, e)
    return render(
        request,
        "firmy/agent_form.html",
        {"agent": None, "payload": payload, "model_choices": sorted(set(POPULAR_MODELS.values()))},
    )


@require_http_methods(["GET", "POST"])
def agent_settings(request, agent_id):
    agent = get_object_or_404(FirmyAgent, pk=agent_id)
    payload = {
        "name": agent.name,
        "avatar": agent.avatar or "👤",
        "email": agent.email,
        "phone": agent.phone,
        "system_prompt": agent.system_prompt,
        "prompt_scope": agent.prompt_scope,
        "prompt_strategy": agent.prompt_strategy,
        "model_name": agent.model_name,
        "token_limit": agent.token_limit,
        "role": agent.role,
    }
    if request.method == "POST":
        payload = _agent_payload_from_request(request)
        errors = _validate_agent_payload(payload)
        if not errors:
            for k, v in payload.items():
                setattr(agent, k, v)
            agent.save()
            messages.success(request, "Настройки агента сохранены.")
            return redirect("firmy:agent_settings", agent_id=agent.id)
        for e in errors:
            messages.error(request, e)
    return render(
        request,
        "firmy/agent_form.html",
        {"agent": agent, "payload": payload, "model_choices": sorted(set(POPULAR_MODELS.values()))},
    )

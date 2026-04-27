from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

from .domain import LeadContext, Message


@dataclass
class MemoryRecord:
    lead_id: str
    summary: str = ""
    messages: list[Message] | None = None
    lead_context: dict[str, Any] | None = None


class MemoryStore:
    def get_context(self, lead_id: str) -> MemoryRecord: ...
    def save_message(self, lead_id: str, message: Message) -> None: ...
    def update_summary(self, lead_id: str) -> str: ...


class JsonMemoryStore(MemoryStore):
    """
    File-per-lead JSON memory store.
    Designed to be simple and testable; no DB required.
    """

    def __init__(self, directory: str):
        self._dir = directory
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, lead_id: str) -> str:
        safe = "".join([c for c in lead_id if c.isalnum() or c in ("-", "_")]) or "lead"
        return os.path.join(self._dir, f"{safe}.json")

    def _read(self, lead_id: str) -> dict[str, Any]:
        p = self._path(lead_id)
        if not os.path.exists(p):
            return {"lead_id": lead_id, "summary": "", "messages": [], "lead_context": None}
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_atomic(self, lead_id: str, obj: dict[str, Any]) -> None:
        p = self._path(lead_id)
        tmp = p + f".tmp.{int(time.time() * 1000)}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)

    def set_lead_context(self, lead_id: str, ctx: LeadContext) -> None:
        obj = self._read(lead_id)
        obj["lead_context"] = asdict(ctx)
        self._write_atomic(lead_id, obj)

    def get_context(self, lead_id: str) -> MemoryRecord:
        obj = self._read(lead_id)
        return MemoryRecord(
            lead_id=obj.get("lead_id") or lead_id,
            summary=obj.get("summary") or "",
            messages=obj.get("messages") or [],
            lead_context=obj.get("lead_context"),
        )

    def save_message(self, lead_id: str, message: Message) -> None:
        obj = self._read(lead_id)
        msgs = obj.get("messages") or []
        msgs.append(message)
        obj["messages"] = msgs
        self._write_atomic(lead_id, obj)

    def update_summary(self, lead_id: str) -> str:
        """
        Cheap deterministic summarizer (no LLM here by design).
        Keeps last few turns compressed into a short rolling summary.
        """
        obj = self._read(lead_id)
        msgs: list[Message] = obj.get("messages") or []
        tail = msgs[-12:]
        parts: list[str] = []
        for m in tail:
            role = m.get("role", "?")
            content = (m.get("content") or "").strip().replace("\n", " ")
            if not content:
                continue
            parts.append(f"{role}: {content[:180]}")

        summary = " | ".join(parts)
        if len(summary) > 1400:
            summary = summary[:1400] + "…"
        obj["summary"] = summary
        self._write_atomic(lead_id, obj)
        return summary


from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypedDict


Role = Literal["system", "user", "assistant"]


class Message(TypedDict):
    role: Role
    content: str


class LeadStatus(str, Enum):
    COLD = "cold"
    REPLIED = "replied"
    INTERESTED = "interested"


@dataclass(frozen=True)
class CompanyProfile:
    name: str
    website: str | None = None
    industry: str | None = None
    location: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class LeadProfile:
    lead_id: str
    contact_name: str | None = None
    contact_role: str | None = None
    email: str | None = None
    status: LeadStatus = LeadStatus.COLD


@dataclass(frozen=True)
class OutreachIntent:
    product: str
    value_prop: str
    call_to_action: str
    language: str = "ru"
    sender_name: str = "Your Name"
    sender_company: str = "Your Company"
    sender_prompt: str | None = None


@dataclass(frozen=True)
class LeadContext:
    lead: LeadProfile
    company: CompanyProfile
    intent: OutreachIntent


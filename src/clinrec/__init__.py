"""ClinRec — on-prem clinical-timeline primitive.

Turns faxed and scanned PHI into a regulator-reviewable clinical timeline
on a single host: medspaCy clinical NER + an on-prem Llama 3.1 8B entity
linker (Ollama) + a per-op sha-256 audit chain that proves PHI never left
the box. No cloud calls, no SaaS.
"""
from __future__ import annotations

__version__ = "0.5.0"

from .models import (
    AuditEntry,
    ClinicalTimeline,
    CodeSystem,
    Entity,
    EntityType,
    EventStatus,
    EvidenceSpan,
    RawEntity,
    Record,
    TimelineEvent,
)

__all__ = [
    "AuditEntry",
    "ClinicalTimeline",
    "CodeSystem",
    "Entity",
    "EntityType",
    "EventStatus",
    "EvidenceSpan",
    "RawEntity",
    "Record",
    "TimelineEvent",
    "__version__",
]

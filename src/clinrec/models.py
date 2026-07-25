"""Core data model for the on-prem clinical timeline primitive.

The new composite primitive is the **on-prem clinical timeline with a
no-PHI-egress audit chain**. NER, dedup, and code normalization are
individually prior art; the defensible novel surfaces are (a) the on-prem
LLM linker that normalizes messy spans to coded entities with zero cloud
calls, and (b) the per-op audit chain that lets a regulator reconstruct
every extraction step from local artifacts.

These pydantic models are the single source of truth for the primitive's
shape; every other module (ingest / resolve / llm / audit / timeline)
operates on these types.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def _utcnow() -> datetime:
    """Timezone-aware UTC now (used as a model default factory)."""
    return datetime.now(timezone.utc)


def _new_id(prefix: str) -> str:
    """Stable, sortable-ish identifier prefixed by a domain slug."""
    return f"{prefix}_{uuid4().hex[:12]}"


class EntityType(str, Enum):
    """medspaCy clinical NER label set (also used by the fallback NER)."""

    CONDITION = "CONDITION"
    MEDICATION = "MEDICATION"
    PROCEDURE = "PROCEDURE"
    DATE = "DATE"
    PROVIDER = "PROVIDER"


class CodeSystem(str, Enum):
    """Normalization targets the on-prem linker maps messy spans onto."""

    ICD10 = "ICD-10"
    RXNORM = "RxNorm"
    CPT = "CPT"
    SNOMED = "SNOMED-CT"
    UNKNOWN = "UNKNOWN"


class EventStatus(str, Enum):
    """Coarse status carried on a timeline event for regulator reviewability."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    NEGATED = "negated"
    UNKNOWN = "unknown"


class Record(BaseModel):
    """A single ingested faxed/scanned record after OCR + content-hash dedup."""

    model_config = ConfigDict(frozen=True)

    record_id: str = Field(default_factory=lambda: _new_id("rec"))
    source_uri: str
    mime: str
    ocr_text: str
    content_sha256: str
    ingested_at: datetime = Field(default_factory=_utcnow)


class RawEntity(BaseModel):
    """Raw NER output before code normalization (medspaCy span + context)."""

    model_config = ConfigDict(frozen=True)

    entity_type: EntityType
    text_span: str
    start: int
    end: int
    is_negated: bool = False
    source_record_id: str


class Entity(BaseModel):
    """A coded, linked clinical entity (the LLM linker's output)."""

    model_config = ConfigDict(frozen=True)

    entity_id: str = Field(default_factory=lambda: _new_id("ent"))
    entity_type: EntityType
    text_span: str
    code_sys: CodeSystem = CodeSystem.UNKNOWN
    normalized_code: str = ""
    source_record_id: str
    confidence: float = 0.0
    llm_model_id: str = "rule-based"
    start: int = 0
    end: int = 0
    is_negated: bool = False


class AuditEntry(BaseModel):
    """One row of the regulator-reviewable audit chain.

    The ``phi_egress`` flag is the primitive's invariant: it is **always**
    False because the LLM linker runs on-prem (Ollama) and no input or
    output ever leaves the host. A regulator can replay every extraction
    step from the local sha-256 artifacts.
    """

    model_config = ConfigDict(frozen=True)

    audit_id: str = Field(default_factory=lambda: _new_id("aud"))
    op: str
    input_sha256: str
    llm_model_id: str = "rule-based"
    prompt_sha256: str = ""
    output_sha256: str = ""
    ts: datetime = Field(default_factory=_utcnow)
    phi_egress: bool = False


class EvidenceSpan(BaseModel):
    """A piece of evidence tying a timeline event back to source records."""

    model_config = ConfigDict(frozen=True)

    source_record_id: str
    text_span: str
    start: int
    end: int
    is_negated: bool = False


class TimelineEvent(BaseModel):
    """A de-duplicated, dated, normalized clinical event.

    Multiple records mentioning the same coded entity on the same onset
    date collapse into one event whose ``evidence_spans`` is the union of
    the supporting spans (with negation flags preserved).
    """

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(default_factory=lambda: _new_id("evt"))
    entity_ids: list[str] = Field(default_factory=list)
    entity_type: EntityType
    code_sys: CodeSystem = CodeSystem.UNKNOWN
    normalized_code: str = ""
    onset_date: Optional[date] = None
    status: EventStatus = EventStatus.UNKNOWN
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    audit_id: str = ""


class ClinicalTimeline(BaseModel):
    """The composite primitive: records + events + audit chain.

    ``patient_pseudonym`` is a non-reversible pseudonym derived locally
    (never real PHI) so a regulator can refer to a timeline without
    re-identifying the patient.
    """

    model_config = ConfigDict()

    patient_pseudonym: str = "patient-local"
    records: list[Record] = Field(default_factory=list)
    entities: list[Entity] = Field(default_factory=list)
    events: list[TimelineEvent] = Field(default_factory=list)
    audit_chain: list[AuditEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)


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
]

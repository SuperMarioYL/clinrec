"""m1+m2: pydantic primitive model tests."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clinrec.models import (
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


def test_record_defaults_and_immutability():
    r = Record(source_uri="fax.txt", mime="text/plain", ocr_text="hi", content_sha256="a" * 64)
    assert r.record_id.startswith("rec_")
    assert r.ingested_at.tzinfo is not None
    with pytest.raises(Exception):  # frozen model
        r.mime = "x"  # type: ignore[misc]


def test_entity_default_model_id_is_rule_based():
    e = Entity(
        entity_type=EntityType.CONDITION,
        text_span="diabetes",
        source_record_id="rec_1",
    )
    assert e.code_sys == CodeSystem.UNKNOWN
    assert e.normalized_code == ""
    assert e.llm_model_id == "rule-based"
    assert e.confidence == 0.0


def test_audit_entry_phi_egress_invariant():
    a = AuditEntry(op="ingest", input_sha256="x")
    assert a.phi_egress is False  # the primitive invariant
    assert a.llm_model_id == "rule-based"


def test_timeline_event_evidence_span_roundtrip():
    sp = EvidenceSpan(source_record_id="r1", text_span="diabetes", start=0, end=8, is_negated=False)
    ev = TimelineEvent(
        entity_type=EntityType.CONDITION,
        code_sys=CodeSystem.ICD10,
        normalized_code="E11.9",
        onset_date=date(2024, 1, 15),
        status=EventStatus.ACTIVE,
        evidence_spans=[sp],
        audit_id="aud_1",
    )
    assert ev.event_id.startswith("evt_")
    assert ev.entity_ids == []
    assert ev.evidence_spans[0].text_span == "diabetes"
    # JSON roundtrip preserves the shape
    raw = ev.model_dump(mode="json")
    back = TimelineEvent(**raw)
    assert back.normalized_code == "E11.9"
    assert back.onset_date == date(2024, 1, 15)


def test_clinical_timeline_aggregate():
    tl = ClinicalTimeline(patient_pseudonym="p1")
    assert tl.records == []
    assert tl.events == []
    assert tl.audit_chain == []
    assert tl.created_at.tzinfo is not None


def test_entity_type_and_code_system_enums():
    assert EntityType.CONDITION.value == "CONDITION"
    assert CodeSystem.ICD10.value == "ICD-10"
    assert CodeSystem.RXNORM.value == "RxNorm"
    assert CodeSystem.CPT.value == "CPT"
    assert CodeSystem.SNOMED.value == "SNOMED-CT"

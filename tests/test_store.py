"""m2: local state persistence (records + timeline + audit + dedup)."""
from __future__ import annotations

import json

from clinrec.audit import AuditChain
from clinrec.dedup import Deduplicator, content_sha256
from clinrec.models import (
    CodeSystem,
    Entity,
    EntityType,
    EvidenceSpan,
    Record,
    TimelineEvent,
    ClinicalTimeline,
    EventStatus,
)
from clinrec.store import State


def _timeline() -> ClinicalTimeline:
    rec = Record(source_uri="fax.txt", mime="text/plain", ocr_text="diabetes", content_sha256=content_sha256("diabetes"))
    ent = Entity(
        entity_type=EntityType.CONDITION,
        text_span="diabetes",
        code_sys=CodeSystem.ICD10,
        normalized_code="E11.9",
        source_record_id=rec.record_id,
        confidence=0.95,
    )
    ev = TimelineEvent(
        entity_type=EntityType.CONDITION,
        code_sys=CodeSystem.ICD10,
        normalized_code="E11.9",
        evidence_spans=[EvidenceSpan(source_record_id=rec.record_id, text_span="diabetes", start=0, end=8)],
        status=EventStatus.ACTIVE,
    )
    audit = AuditChain()
    audit.record(op="ingest", input_sha256=rec.content_sha256)
    return ClinicalTimeline(
        patient_pseudonym="p1",
        records=[rec],
        entities=[ent],
        events=[ev],
        audit_chain=audit.entries,
    )


def test_state_save_and_load_roundtrip(tmp_path):
    state = State(state_dir=tmp_path / ".clinrec")
    tl = _timeline()
    dedup = Deduplicator()
    dedup.add(content_sha256("diabetes"))
    state.save(tl, dedup, config={"ollama_model": "llama3.1:8b-instruct"})

    loaded_tl, loaded_dedup, cfg = state.load()
    assert loaded_tl.patient_pseudonym == "p1"
    assert len(loaded_tl.records) == 1
    assert loaded_tl.records[0].ocr_text == "diabetes"
    assert len(loaded_tl.events) == 1
    assert loaded_tl.events[0].normalized_code == "E11.9"
    assert loaded_dedup.is_duplicate(content_sha256("diabetes"))
    assert cfg["ollama_model"] == "llama3.1:8b-instruct"
    # audit chain round-trips
    assert len(loaded_tl.audit_chain) >= 1
    assert loaded_tl.audit_chain[0].op == "ingest"


def test_state_save_creates_parent_dir(tmp_path):
    state = State(state_dir=tmp_path / "nested" / "deep" / ".clinrec")
    state.save(_timeline(), Deduplicator())
    assert state.path.exists()


def test_state_json_is_inspectable(tmp_path):
    state = State(state_dir=tmp_path / ".clinrec")
    state.save(_timeline(), Deduplicator())
    data = json.loads(state.path.read_text())
    assert data["patient_pseudonym"] == "p1"
    assert "records" in data and "events" in data and "audit_chain" in data

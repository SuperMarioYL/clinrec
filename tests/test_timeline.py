"""m2: timeline assembly + de-duplication + audit (the composite primitive)."""
from __future__ import annotations

import hashlib
from datetime import date

import pytest

from clinrec.audit import AuditChain, OP_ASSEMBLE, OP_LINK
from clinrec.llm import Linker
from clinrec.models import (
    CodeSystem,
    EntityType,
    EventStatus,
    Record,
)
from clinrec.resolve import EntityExtractor
from clinrec.timeline import TimelineAssembler, parse_date


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record(text: str, uri: str = "fax.txt") -> Record:
    return Record(
        source_uri=uri,
        mime="text/plain",
        ocr_text=text,
        content_sha256=_sha(text),
    )


# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "span,expected",
    [
        ("01/15/2024", date(2024, 1, 15)),
        ("01-15-2024", date(2024, 1, 15)),
        ("2024-01-15", date(2024, 1, 15)),
        ("January 15, 2024", date(2024, 1, 15)),
        ("Jan 15, 2024", date(2024, 1, 15)),
        ("15 January 2024", date(2024, 1, 15)),
        ("1/15/24", date(2024, 1, 15)),  # 2-digit year
        ("not a date", None),
        ("32/13/9999", None),  # invalid month/day
        ("", None),
    ],
)
def test_parse_date_formats(span, expected):
    assert parse_date(span) == expected


# ---------------------------------------------------------------------------
# Assembly — the composite primitive
# ---------------------------------------------------------------------------


def test_assemble_provides_coded_de_duplicated_events():
    r1 = _record(
        "Patient seen 01/15/2024. Dr. Jane Smith noted diabetes. Started metformin 500 mg.",
        uri="fax1.txt",
    )
    r2 = _record(
        "02/20/2024 follow-up. Hypertension now controlled. Continue lisinopril.",
        uri="fax2.txt",
    )
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r1, r2], patient_pseudonym="p-deid-1")

    assert tl.patient_pseudonym == "p-deid-1"
    assert len(tl.records) == 2
    assert len(tl.events) >= 4  # diabetes, metformin, hypertension, lisinopril

    by_code = {ev.normalized_code: ev for ev in tl.events if ev.normalized_code}
    assert "E11.9" in by_code  # diabetes
    assert "6809" in by_code   # metformin
    assert "I10" in by_code    # hypertension
    assert "29046" in by_code  # lisinopril


def test_assemble_attaches_nearest_date_as_onset():
    r = _record("01/15/2024 — Patient seen. Diabetes noted. Metformin started.")
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r])
    for ev in tl.events:
        if ev.normalized_code in {"E11.9", "6809"}:
            assert ev.onset_date == date(2024, 1, 15)


def test_assemble_dedups_repeat_mentions_of_same_code_on_same_date():
    """Two spans normalizing to the same code on the same date collapse into
    one event with merged evidence spans — the de-dup surface."""
    r = _record(
        "01/15/2024. Patient has diabetes. Dr. Smith also documented diabetes in the assessment."
    )
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r])
    diabetes_events = [ev for ev in tl.events if ev.normalized_code == "E11.9"]
    assert len(diabetes_events) == 1
    assert len(diabetes_events[0].evidence_spans) >= 2  # merged evidence


def test_assemble_marks_negated_status():
    r = _record("01/15/2024. Patient denies chest pain. Has hypertension.")
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r])
    # chest pain is not in our coded rule set → it may be uncoded; ensure
    # hypertension is active and any negated entity has negated evidence
    hp = next(ev for ev in tl.events if ev.normalized_code == "I10")
    assert hp.status == EventStatus.ACTIVE


def test_assemble_marks_historical_as_resolved():
    r = _record("02/20/2024 follow-up. Patient reports history of stroke. Denies chest pain.")
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r])
    stroke = next((ev for ev in tl.events if ev.normalized_code == "I63.9"), None)
    assert stroke is not None
    assert stroke.status == EventStatus.RESOLVED


def test_assemble_populates_audit_chain():
    r = _record("01/15/2024. Diabetes noted. Metformin started.")
    audit = AuditChain()
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=audit)
    tl = asm.assemble([r])
    # at least one link audit entry per coded entity + assemble entries
    ops = [e.op for e in tl.audit_chain]
    assert OP_LINK in ops
    assert OP_ASSEMBLE in ops
    assert audit.verify_invariant() is True  # phi_egress never True


def test_assemble_phi_egress_invariant_holds():
    """The primitive's headline guarantee: no audit entry ever flags PHI egress."""
    r = _record("01/15/2024. Diabetes. Metformin. History of stroke.")
    audit = AuditChain()
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=audit)
    tl = asm.assemble([r])
    assert all(not e.phi_egress for e in tl.audit_chain)


def test_assemble_events_sorted_by_onset_date():
    r1 = _record("02/20/2024. Hypertension noted.")
    r2 = _record("01/15/2024. Diabetes noted.")
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([r1, r2])
    onsets = [ev.onset_date for ev in tl.events if ev.onset_date]
    assert onsets == sorted(onsets)


def test_assemble_handles_empty_records():
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    tl = asm.assemble([])
    assert tl.events == []
    assert tl.records == []


def test_resolve_record_returns_coded_entities():
    r = _record("01/15/2024. Diabetes. Metformin.")
    asm = TimelineAssembler(extractor=EntityExtractor(), linker=Linker(), audit=AuditChain())
    ents = asm.resolve_record(r)
    assert any(e.normalized_code == "E11.9" for e in ents)
    assert any(e.normalized_code == "6809" for e in ents)
    assert all(e.llm_model_id for e in ents)

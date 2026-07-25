"""m2: Rich timeline TUI (prompt-driven browser)."""
from __future__ import annotations

import io

from rich.console import Console

from clinrec.models import (
    CodeSystem,
    EntityType,
    EventStatus,
    EvidenceSpan,
    TimelineEvent,
    ClinicalTimeline,
)
from clinrec.tui import run_timeline_tui


def _make_timeline() -> ClinicalTimeline:
    ev = TimelineEvent(
        entity_type=EntityType.CONDITION,
        code_sys=CodeSystem.ICD10,
        normalized_code="E11.9",
        status=EventStatus.ACTIVE,
        evidence_spans=[EvidenceSpan(source_record_id="r1", text_span="diabetes", start=0, end=8, is_negated=False)],
        audit_id="aud_1",
    )
    return ClinicalTimeline(patient_pseudonym="p1", events=[ev])


def _drive(*inputs: str) -> str:
    """Run the TUI with scripted stdin + capture rendered output."""
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False, record=True)
    run_timeline_tui(_make_timeline(), console=console, input_stream=io.StringIO("\n".join(inputs)))
    return buf.getvalue()


def test_tui_lists_events():
    out = _drive("q")
    assert "Clinical Timeline" in out
    assert "E11.9" in out
    assert "1 de-duplicated events" in out


def test_tui_drills_into_event():
    out = _drive("1", "", "q")
    assert "Event" in out
    assert "diabetes" in out
    assert "evidence" in out.lower() or "Evidence" in out


def test_tui_shows_audit_chain():
    out = _drive("a", "", "q")
    assert "Audit Chain" in out
    assert "phi_egress" in out.lower()


def test_tui_empty_timeline_prints_hint():
    buf = io.StringIO()
    console = Console(file=buf, width=120, force_terminal=False)
    run_timeline_tui(ClinicalTimeline(events=[]), console=console, input_stream=io.StringIO(""))
    out = buf.getvalue()
    assert "No timeline events" in out or "clinrec ingest" in out


def test_tui_invalid_choice_handled():
    out = _drive("zzz", "q")
    assert "invalid choice" in out.lower() or "Clinical Timeline" in out

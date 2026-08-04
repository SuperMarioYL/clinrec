"""Timeline assembly + event de-duplication (m2).

Turns coded ``Entity`` objects (from NER + on-prem linker) into dated,
de-duplicated ``TimelineEvent`` objects: spans that normalize to the same
code on the same onset date collapse into one event whose
``evidence_spans`` is the union of supporting spans (with negation flags
preserved). The audit chain records every assembly step so a regulator
can replay how a final event was built from raw faxes.

This is the composite primitive's outer surface: the on-prem clinical
timeline with a no-PHI-egress audit chain.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from datetime import date
from typing import Iterable, Optional

from .audit import AuditChain, OP_ASSEMBLE, sha256_text
from .llm import LinkResult, Linker
from .models import (
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
from .resolve import EntityExtractor

log = logging.getLogger("clinrec.timeline")

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def parse_date(span: str) -> Optional[date]:
    """Parse the date formats used in faxed records → ``date`` or ``None``."""
    s = span.strip().rstrip(".,;:")
    # MM/DD/YYYY or MM-DD-YYYY (also 2-digit year)
    m = re.match(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$", s)
    if m:
        mo, da, yr = int(m[1]), int(m[2]), int(m[3])
        yr = yr + 2000 if yr < 100 else yr
        try:
            return date(yr, mo, da)
        except ValueError:
            return None
    # YYYY-MM-DD
    m = re.match(r"^(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})$", s)
    if m:
        yr, mo, da = int(m[1]), int(m[2]), int(m[3])
        try:
            return date(yr, mo, da)
        except ValueError:
            return None
    # Month DD, YYYY  /  Mon DD YYYY
    m = re.match(r"^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m[1].lower())
        if mo:
            try:
                return date(int(m[3]), mo, int(m[2]))
            except ValueError:
                return None
    # DD Month YYYY
    m = re.match(r"^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$", s)
    if m:
        mo = _MONTHS.get(m[2].lower())
        if mo:
            try:
                return date(int(m[3]), mo, int(m[1]))
            except ValueError:
                return None
    return None


def _nearest_date(
    raw_entities: list[RawEntity], target: RawEntity
) -> Optional[date]:
    """Closest DATE entity to ``target`` in the same record (prefers preceding)."""
    best: Optional[date] = None
    best_dist = 1 << 30
    for r in raw_entities:
        if r.entity_type != EntityType.DATE:
            continue
        if r.source_record_id != target.source_record_id:
            continue
        d = parse_date(r.text_span)
        if d is None:
            continue
        dist = abs(r.start - target.start)
        # prefer preceding dates (onset usually precedes the mention)
        if r.start <= target.start:
            dist = max(0, dist - 1)
        if dist < best_dist:
            best_dist = dist
            best = d
    return best


def _event_status(spans: list[EvidenceSpan], historical: bool) -> EventStatus:
    """Resolve a merged event's status from its evidence spans."""
    if not spans:
        return EventStatus.UNKNOWN
    if historical and not any(not s.is_negated for s in spans):
        return EventStatus.RESOLVED
    if all(s.is_negated for s in spans):
        return EventStatus.NEGATED
    if historical:
        return EventStatus.RESOLVED
    return EventStatus.ACTIVE


class TimelineAssembler:
    """Run NER + link + date-attach + de-duplicate → ``ClinicalTimeline``."""

    def __init__(
        self,
        extractor: Optional[EntityExtractor] = None,
        linker: Optional[Linker] = None,
        audit: Optional[AuditChain] = None,
    ) -> None:
        self.extractor = extractor or EntityExtractor()
        self.linker = linker or Linker()
        self.audit = audit or AuditChain()

    # -- per-record NER + link ------------------------------------------------

    def resolve_record(self, record: Record) -> list[Entity]:
        """Run NER + on-prem linker on one record → coded ``Entity`` list."""
        raw = self.extractor.extract(record.ocr_text, record.record_id)
        self.audit.record(
            op="ner",
            input_sha256=record.content_sha256,
            llm_model_id="medspacy" if self.extractor.uses_medspacy else "regex",
            output_sha256=sha256_text("|".join(r.text_span for r in raw)),
        )
        out: list[Entity] = []
        for r in raw:
            lr: LinkResult = self.linker.link(r.text_span, r.entity_type)
            self.audit.record(
                op="link",
                input_sha256=sha256_text(r.text_span),
                llm_model_id=lr.llm_model_id,
                prompt_sha256=lr.prompt_sha256,
                output_sha256=lr.output_sha256,
            )
            out.append(
                Entity(
                    entity_type=r.entity_type,
                    text_span=r.text_span,
                    code_sys=lr.code_sys,
                    normalized_code=lr.normalized_code,
                    source_record_id=record.record_id,
                    confidence=lr.confidence,
                    llm_model_id=lr.llm_model_id,
                    start=r.start,
                    end=r.end,
                    is_negated=r.is_negated,
                )
            )
        return out

    # -- full assemble ---------------------------------------------------------

    def assemble(
        self,
        records: Iterable[Record],
        patient_pseudonym: str = "patient-local",
    ) -> ClinicalTimeline:
        records = list(records)
        all_entities: list[Entity] = []
        # raw entities per record kept for date attachment
        per_record_raw: dict[str, list[RawEntity]] = {}

        for rec in records:
            raw = self.extractor.extract(rec.ocr_text, rec.record_id)
            # Record the NER op so a regulator can replay which engine
            # (medspacy vs regex) produced each record's spans + their
            # input/output hashes — mirrors resolve_record's ner call.
            self.audit.record(
                op="ner",
                input_sha256=rec.content_sha256,
                llm_model_id="medspacy" if self.extractor.uses_medspacy else "regex",
                output_sha256=sha256_text("|".join(r.text_span for r in raw)),
            )
            per_record_raw[rec.record_id] = raw
            for r in raw:
                lr = self.linker.link(r.text_span, r.entity_type)
                self.audit.record(
                    op="link",
                    input_sha256=sha256_text(r.text_span),
                    llm_model_id=lr.llm_model_id,
                    prompt_sha256=lr.prompt_sha256,
                    output_sha256=lr.output_sha256,
                )
                all_entities.append(
                    Entity(
                        entity_type=r.entity_type,
                        text_span=r.text_span,
                        code_sys=lr.code_sys,
                        normalized_code=lr.normalized_code,
                        source_record_id=rec.record_id,
                        confidence=lr.confidence,
                        llm_model_id=lr.llm_model_id,
                        start=r.start,
                        end=r.end,
                        is_negated=r.is_negated,
                    )
                )

        events = self._build_events(all_entities, per_record_raw, records)
        self.audit.record(
            op=OP_ASSEMBLE,
            input_sha256=sha256_text("|".join(e.normalized_code for e in all_entities)),
            output_sha256=sha256_text("|".join(ev.event_id for ev in events)),
        )

        return ClinicalTimeline(
            patient_pseudonym=patient_pseudonym,
            records=records,
            entities=all_entities,
            events=events,
            audit_chain=self.audit.entries,
        )

    def _build_events(
        self,
        entities: list[Entity],
        per_record_raw: dict[str, list[RawEntity]],
        records: list[Record],
    ) -> list[TimelineEvent]:
        """Group coded entities by (code, onset_date) → de-duplicated events."""
        record_text_map = {r.record_id: r.ocr_text for r in records}
        # bucket key: (entity_type, code_sys, normalized_code, onset_iso|'')
        buckets: dict[tuple, list[Entity]] = defaultdict(list)
        onset_map: dict[str, Optional[date]] = {}

        for ent in entities:
            if ent.entity_type in (EntityType.DATE, EntityType.PROVIDER):
                continue  # dates are attached, providers are not events
            if not ent.normalized_code:
                # un-coded span → still an event keyed on the raw text span
                key_code = ent.text_span.strip()
                sys_ = CodeSystem.UNKNOWN
            else:
                key_code = ent.normalized_code
                sys_ = ent.code_sys
            raw_list = per_record_raw.get(ent.source_record_id, [])
            # find the raw entity matching this Entity (by span offset)
            matching_raw = next(
                (r for r in raw_list if r.start == ent.start and r.end == ent.end),
                None,
            )
            onset = None
            if matching_raw is not None:
                onset = _nearest_date(raw_list, matching_raw)
            onset_iso = onset.isoformat() if onset else ""
            onset_map[ent.entity_id] = onset
            buckets[(ent.entity_type, sys_, key_code, onset_iso)].append(ent)

        events: list[TimelineEvent] = []
        for (etype, sys_, code, onset_iso), group in buckets.items():
            onset = date.fromisoformat(onset_iso) if onset_iso else None
            spans = [
                EvidenceSpan(
                    source_record_id=e.source_record_id,
                    text_span=e.text_span,
                    start=e.start,
                    end=e.end,
                    is_negated=e.is_negated,
                )
                for e in group
            ]
            historical = self._looks_historical(group, record_text_map)
            status = _event_status(spans, historical)
            audit_id = self.audit.record(
                op=OP_ASSEMBLE,
                input_sha256=sha256_text("|".join(e.entity_id for e in group)),
                llm_model_id="timeline-assembler",
                output_sha256=sha256_text(f"{code}|{onset_iso}|{status.value}"),
            ).audit_id
            events.append(
                TimelineEvent(
                    entity_ids=[e.entity_id for e in group],
                    entity_type=etype,
                    code_sys=sys_,
                    normalized_code=code,
                    onset_date=onset,
                    status=status,
                    evidence_spans=spans,
                    audit_id=audit_id,
                )
            )
        events.sort(key=lambda ev: (ev.onset_date or date.min, ev.normalized_code))
        return events

    def _looks_historical(
        self,
        group: list[Entity],
        record_text_map: dict[str, str],
    ) -> bool:
        """Heuristic: any evidence span preceded by a historical cue."""
        from .resolve import _span_historical  # local import to avoid cycle

        for e in group:
            text = record_text_map.get(e.source_record_id, "")
            if _span_historical(text, e.start, e.end):
                return True
        return False


__all__ = [
    "TimelineAssembler",
    "parse_date",
]

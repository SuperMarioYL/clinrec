"""Local JSON state store — persists the assembled timeline between commands.

``clinrec ingest`` writes the state; ``clinrec timeline`` and
``clinrec audit`` read it back. The store lives at ``./.clinrec/state.json``
(never shipped — it is operator-local working state). All artifacts in the
state are plain JSON so a regulator or operator can inspect them directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .audit import AuditChain
from .dedup import Deduplicator
from .models import ClinicalTimeline, Entity, Record, TimelineEvent

DEFAULT_STATE_DIR = ".clinrec"
DEFAULT_STATE_FILE = "state.json"


class State:
    """Thin JSON persistence wrapper over the composite primitive."""

    def __init__(self, state_dir: Path | str = DEFAULT_STATE_DIR) -> None:
        self.dir = Path(state_dir)
        self.path = self.dir / DEFAULT_STATE_FILE

    @classmethod
    def at(cls, cwd: Path | str = ".") -> "State":
        return cls(state_dir=Path(cwd) / DEFAULT_STATE_DIR)

    def exists(self) -> bool:
        return self.path.exists()

    def save(
        self,
        timeline: ClinicalTimeline,
        dedup: Deduplicator,
        config: Optional[dict] = None,
    ) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "patient_pseudonym": timeline.patient_pseudonym,
            "created_at": timeline.created_at.isoformat(),
            "records": [r.model_dump(mode="json") for r in timeline.records],
            "entities": [e.model_dump(mode="json") for e in timeline.entities],
            "events": [ev.model_dump(mode="json") for ev in timeline.events],
            "audit_chain": [
                {
                    "audit_id": a.audit_id,
                    "op": a.op,
                    "input_sha256": a.input_sha256,
                    "llm_model_id": a.llm_model_id,
                    "prompt_sha256": a.prompt_sha256,
                    "output_sha256": a.output_sha256,
                    "ts": a.ts.isoformat(),
                    "phi_egress": a.phi_egress,
                }
                for a in timeline.audit_chain
            ],
            "dedup": dedup.to_state(),
            "config": config or {},
        }
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        return self.path

    def load(self) -> tuple[ClinicalTimeline, Deduplicator, dict]:
        with self.path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        records = [Record(**r) for r in data.get("records", [])]
        entities = [Entity(**e) for e in data.get("entities", [])]
        events = [TimelineEvent(**ev) for ev in data.get("events", [])]
        audit = AuditChain.from_state(data.get("audit_chain", []))
        dedup = Deduplicator.from_state(data.get("dedup", {}))
        timeline = ClinicalTimeline(
            patient_pseudonym=data.get("patient_pseudonym", "patient-local"),
            records=records,
            entities=entities,
            events=events,
            audit_chain=audit.entries,
        )
        return timeline, dedup, data.get("config", {})


__all__ = ["State"]

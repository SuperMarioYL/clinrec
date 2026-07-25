"""Minimal programmatic example — build a clinical timeline from a folder.

    python examples/build_timeline.py ./sample-records
"""
from __future__ import annotations

import sys
from pathlib import Path

from clinrec.audit import AuditChain
from clinrec.ingest import ingest_folder
from clinrec.llm import Linker
from clinrec.resolve import EntityExtractor
from clinrec.timeline import TimelineAssembler


def main(folder: str) -> None:
    records, dedup = ingest_folder(folder)
    asm = TimelineAssembler(
        extractor=EntityExtractor(), linker=Linker(), audit=AuditChain()
    )
    timeline = asm.assemble(records, patient_pseudonym="example-patient")
    print(f"records={len(timeline.records)}  events={len(timeline.events)}  "
          f"audit={len(timeline.audit_chain)}  phi_egress_invariant={asm.audit.verify_invariant()}")
    for ev in timeline.events[:5]:
        print(f"  {ev.onset_date}  {ev.entity_type.value:<11} {ev.normalized_code:<10} {ev.status.value}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sample-records")

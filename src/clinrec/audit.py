"""Per-op audit chain (m2) — the regulator-reviewable, no-PHI-egress log.

For every extraction step (OCR → NER → link → assemble) the audit chain
records the sha-256 of the input and output, the on-prem model id that
produced it, the sha-256 of the prompt fed to that model, and a
``phi_egress`` flag that is the primitive's invariant: **always False**,
because the LLM linker runs on-prem and no input or output ever leaves
the host. A regulator can replay every step from these local artifacts.

``clinrec audit --export audit.jsonl`` writes one JSON object per line.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import AuditEntry

# Well-known op names so the audit log reads consistently across records.
OP_INGEST = "ingest"
OP_NER = "ner"
OP_LINK = "link"
OP_ASSEMBLE = "assemble"
OP_DEDUP_SKIP = "dedup_skip"


def sha256_text(text: str) -> str:
    """Convenience sha-256 over a UTF-8 string (audit fingerprint)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class AuditChain:
    """Append-only audit log.

    Thread-unsafe by design: ``clinrec`` is a single-operator CLI and the
    timeline assembly runs in one process. Append-only + frozen pydantic
    entries gives the regulator-replay property — no entry is ever mutated.
    """

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []

    def record(
        self,
        op: str,
        input_sha256: str,
        llm_model_id: str = "rule-based",
        prompt_sha256: str = "",
        output_sha256: str = "",
        phi_egress: bool = False,  # invariant: always False
    ) -> AuditEntry:
        """Append one entry. ``phi_egress`` defaults to False and must stay False.

        The ``phi_egress`` parameter exists only so a future cloud-egress
        detector could flag a regression; the current primitive guarantees
        it is False for every op (no PHI ever leaves the host).

        v0.3.0 — tamper-evidence: each entry's ``chain_hash`` incorporates
        the predecessor's ``chain_hash``, forming a linked hash chain. A
        regulator can call ``verify_chain_integrity()`` to detect reordering
        or silent insertion/modification of any past entry.
        """
        prev_hash = self._entries[-1].chain_hash if self._entries else ""
        chain_hash = sha256_text(
            f"{prev_hash}|{op}|{input_sha256}|{llm_model_id}|{output_sha256}"
        )
        entry = AuditEntry(
            op=op,
            input_sha256=input_sha256,
            llm_model_id=llm_model_id,
            prompt_sha256=prompt_sha256,
            output_sha256=output_sha256,
            phi_egress=phi_egress,
            prev_chain_hash=prev_hash,
            chain_hash=chain_hash,
        )
        self._entries.append(entry)
        return entry

    def record_io(
        self,
        op: str,
        input_text: str,
        llm_model_id: str = "rule-based",
        prompt_sha256: str = "",
        output_text: str = "",
    ) -> AuditEntry:
        """Convenience: hash the input/output strings, then append."""
        out_sha = sha256_text(output_text) if output_text else ""
        return self.record(
            op=op,
            input_sha256=sha256_text(input_text),
            llm_model_id=llm_model_id,
            prompt_sha256=prompt_sha256,
            output_sha256=out_sha,
        )

    @property
    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __iter__(self) -> Iterable[AuditEntry]:
        return iter(self._entries)

    def verify_invariant(self) -> bool:
        """Return True iff no entry ever flagged PHI egress (primitive guarantee)."""
        return all(not e.phi_egress for e in self._entries)

    def verify_chain_integrity(self) -> bool:
        """Return True iff the linked hash chain is unbroken (tamper-evidence).

        Recomputes each entry's ``chain_hash`` from its predecessor's and the
        op fields; any reordering, insertion, or modification of a past entry
        breaks the chain at that point.
        """
        prev = ""
        for e in self._entries:
            expected = sha256_text(
                f"{prev}|{e.op}|{e.input_sha256}|{e.llm_model_id}|{e.output_sha256}"
            )
            if e.prev_chain_hash != prev or e.chain_hash != expected:
                return False
            prev = e.chain_hash
        return True

    def last_for_op(self, op: str) -> AuditEntry | None:
        for e in reversed(self._entries):
            if e.op == op:
                return e
        return None

    def export(self, path: Path | str) -> Path:
        """Write the chain as JSON-lines (one entry per line)."""
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for e in self._entries:
                row = {
                    "audit_id": e.audit_id,
                    "op": e.op,
                    "input_sha256": e.input_sha256,
                    "llm_model_id": e.llm_model_id,
                    "prompt_sha256": e.prompt_sha256,
                    "output_sha256": e.output_sha256,
                    "ts": e.ts.isoformat(),
                    "phi_egress": e.phi_egress,
                    "prev_chain_hash": e.prev_chain_hash,
                    "chain_hash": e.chain_hash,
                }
                fh.write(json.dumps(row, sort_keys=True) + "\n")
        return out

    def to_state(self) -> list[dict]:
        return [
            {
                "audit_id": e.audit_id,
                "op": e.op,
                "input_sha256": e.input_sha256,
                "llm_model_id": e.llm_model_id,
                "prompt_sha256": e.prompt_sha256,
                "output_sha256": e.output_sha256,
                "ts": e.ts.isoformat(),
                "phi_egress": e.phi_egress,
                "prev_chain_hash": e.prev_chain_hash,
                "chain_hash": e.chain_hash,
            }
            for e in self._entries
        ]

    @classmethod
    def from_state(cls, state: Iterable[dict]) -> "AuditChain":
        chain = cls()
        for row in state:
            ts = row.get("ts")
            try:
                ts_dt = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            except (ValueError, TypeError):
                ts_dt = datetime.now(timezone.utc)
            chain._entries.append(
                AuditEntry(
                    audit_id=row["audit_id"],
                    op=row["op"],
                    input_sha256=row["input_sha256"],
                    llm_model_id=row.get("llm_model_id", "rule-based"),
                    prompt_sha256=row.get("prompt_sha256", ""),
                    output_sha256=row.get("output_sha256", ""),
                    ts=ts_dt,
                    phi_egress=bool(row.get("phi_egress", False)),
                    prev_chain_hash=row.get("prev_chain_hash", ""),
                    chain_hash=row.get("chain_hash", ""),
                )
            )
        return chain


__all__ = [
    "AuditChain",
    "AuditEntry",
    "OP_ASSEMBLE",
    "OP_DEDUP_SKIP",
    "OP_INGEST",
    "OP_LINK",
    "OP_NER",
    "sha256_text",
]

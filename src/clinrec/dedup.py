"""Content-hash deduplication for repeat faxes (m1).

Two faxes of the same encounter arrive days apart; without dedup the
timeline would double-count every event. The dedup primitive is a
``sha-256`` of the *normalized OCR text* (whitespace-collapsed, lowercased)
so a re-fax that re-OCR'd to the same canonical text is detected even when
the pixel layout shifted slightly.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Iterable


def normalize_text(text: str) -> str:
    """Collapse whitespace and lowercase so re-faxes dedup reliably."""
    if not text:
        return ""
    # Collapse runs of whitespace (including newlines) to single spaces,
    # strip, lowercase. This is intentionally lossy: it is the dedup key,
    # not the text fed to NER.
    return re.sub(r"\s+", " ", text).strip().lower()


def content_sha256(text: str) -> str:
    """SHA-256 of the normalized text — the dedup key + audit fingerprint."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


@dataclass
class Deduplicator:
    """Append-only set of seen content hashes.

    ``ingest_folder`` consults this before constructing a ``Record``: a hash
    already seen returns ``False`` (skip) and the duplicate is recorded for
    the audit summary; a new hash is added and returns ``True``.
    """

    _seen: set[str] = field(default_factory=set)
    duplicates_seen: int = 0

    def is_duplicate(self, sha: str) -> bool:
        return sha in self._seen

    def add(self, sha: str) -> bool:
        """Record a hash. Returns True if newly added, False if duplicate."""
        if sha in self._seen:
            self.duplicates_seen += 1
            return False
        self._seen.add(sha)
        return True

    def merge(self, others: Iterable["Deduplicator"]) -> "Deduplicator":
        """Merge another deduplicator's seen set into this one in place."""
        for other in others:
            self._seen |= other._seen
            self.duplicates_seen += other.duplicates_seen
        return self

    def __len__(self) -> int:
        return len(self._seen)

    def to_state(self) -> dict:
        return {"seen": sorted(self._seen), "duplicates_seen": self.duplicates_seen}

    @classmethod
    def from_state(cls, state: dict) -> "Deduplicator":
        seen = set(state.get("seen", []))
        return cls(_seen=seen, duplicates_seen=int(state.get("duplicates_seen", 0)))


__all__ = ["Deduplicator", "content_sha256", "normalize_text"]

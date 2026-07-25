"""m1: content-hash deduplication tests."""
from __future__ import annotations

from clinrec.dedup import Deduplicator, content_sha256, normalize_text


def test_normalize_collapses_whitespace_and_lowercases():
    assert normalize_text("  Hello   WORLD\nnext ") == "hello world next"


def test_content_sha256_stable_for_normalized_text():
    a = content_sha256("Patient has diabetes.")
    b = content_sha256("  patient   has   diabetes.\n")
    # normalization makes whitespace/case variations collapse to the same hash
    assert a == content_sha256("patient has diabetes.")
    assert b == content_sha256("patient has diabetes.")


def test_deduplicator_add_returns_true_for_new_hash():
    d = Deduplicator()
    sha = content_sha256("first record")
    assert d.add(sha) is True
    assert d.add(sha) is False  # duplicate
    assert d.duplicates_seen == 1
    assert d.is_duplicate(sha) is True
    assert len(d) == 1


def test_deduplicator_merge_combines_seen_sets():
    d1 = Deduplicator()
    d2 = Deduplicator()
    d1.add("a" * 64)
    d2.add("b" * 64)
    d1.merge([d2])
    assert d1.is_duplicate("a" * 64)
    assert d1.is_duplicate("b" * 64)
    assert len(d1) == 2


def test_deduplicator_state_roundtrip():
    d = Deduplicator()
    d.add("c" * 64)
    d.add("c" * 64)  # dup
    state = d.to_state()
    d2 = Deduplicator.from_state(state)
    assert d2.is_duplicate("c" * 64)
    assert d2.duplicates_seen == 1


def test_content_sha256_distinguishes_records():
    assert content_sha256("diabetes") != content_sha256("hypertension")
    assert len(content_sha256("x")) == 64  # sha-256 hex length

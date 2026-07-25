"""m1: ingest — OCR + text + content-hash dedup."""
from __future__ import annotations

from pathlib import Path

import pytest

from clinrec.dedup import Deduplicator, content_sha256
from clinrec.ingest import detect_mime, extract_text, ingest_folder


def test_detect_mime_known_extensions(tmp_path):
    assert detect_mime(Path("a.pdf")) == "application/pdf"
    assert detect_mime(Path("a.txt")) == "text/plain"
    assert detect_mime(Path("a.PNG")) == "image/png"
    assert detect_mime(Path("a.tif")) == "image/tiff"
    assert detect_mime(Path("a.unknown")) == "application/octet-stream"


def test_extract_text_reads_plain_text(tmp_path):
    f = tmp_path / "fax.txt"
    f.write_text("Patient has diabetes.", encoding="utf-8")
    assert extract_text(f) == "Patient has diabetes."


def test_ingest_folder_dedups_repeat_faxes(tmp_path):
    # two identical-content files (re-fax) + one unique record
    (tmp_path / "fax_a.txt").write_text("Patient seen for diabetes. Metformin started.", encoding="utf-8")
    (tmp_path / "fax_b.txt").write_text("  patient seen for diabetes.  metformin started.\n", encoding="utf-8")
    (tmp_path / "fax_c.txt").write_text("Follow-up visit. Hypertension noted.", encoding="utf-8")

    records, dedup = ingest_folder(tmp_path)
    assert len(records) == 2  # a/b collapse, c unique
    assert dedup.duplicates_seen == 1
    # content hashes are present + stable
    assert all(r.content_sha256 for r in records)
    assert records[0].mime == "text/plain"
    assert records[0].source_uri in {"fax_a.txt", "fax_c.txt"}


def test_ingest_folder_skips_unknown_extensions(tmp_path):
    (tmp_path / "notes.md").write_text("# header", encoding="utf-8")  # unknown ext (not registered)
    # .md IS registered as text/markdown, so it ingests; use a truly unknown ext:
    (tmp_path / "ignore.zip").write_text("binary", encoding="utf-8")
    records, _ = ingest_folder(tmp_path)
    # notes.md ingests, ignore.zip skipped
    assert len(records) == 1
    assert records[0].source_uri == "notes.md"


def test_ingest_folder_raises_on_missing_dir(tmp_path):
    with pytest.raises(NotADirectoryError):
        ingest_folder(tmp_path / "nope")


def test_ingest_folder_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.txt").write_text("alpha", encoding="utf-8")
    (sub / "b.txt").write_text("beta", encoding="utf-8")
    records, _ = ingest_folder(tmp_path, recursive=True)
    assert len(records) == 2
    # source_uri is relative to the root
    assert {r.source_uri for r in records} == {"a.txt", "sub/b.txt"}

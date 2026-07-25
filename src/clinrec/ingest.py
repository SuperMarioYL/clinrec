"""Ingest faxed/scanned records: OCR + text extraction + content-hash dedup (m1).

Reads a folder of de-identified faxed/scanned medical records (PDFs,
TIFFs/PNGs, and plain-text files), runs OCR where needed, computes a
content hash, and de-duplicates repeat faxes. The output is a list of
``Record`` objects ready for clinical NER + linking.

All work is local — no file, image, or extracted text ever leaves the host.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Iterable, Optional

from .dedup import Deduplicator, content_sha256
from .models import Record

log = logging.getLogger("clinrec.ingest")

# Mime detection by extension. We avoid a heavyweight sniff; the file-drop
# folder is operator-controlled and extensions are reliable for faxed
# records.
_EXT_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".rtf": "application/rtf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".gif", ".bmp", ".webp"}


def detect_mime(path: Path) -> str:
    return _EXT_MIME.get(path.suffix.lower(), "application/octet-stream")


def _read_pdf(path: Path) -> str:
    """Extract text from a PDF via pdfplumber (page by page)."""
    import pdfplumber

    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                chunks.append(txt)
    return "\n\n".join(chunks)


def _read_image(path: Path) -> str:
    """OCR an image via pytesseract (tesseract must be on PATH)."""
    import pytesseract

    return pytesseract.image_to_string(str(path))


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def extract_text(path: Path) -> str:
    """Extract text from a single file according to its mime/extension.

    PDFs use pdfplumber; images use tesseract OCR; everything else is read
    as UTF-8 text (so a de-identified OCR dump or a hand-typed fixture
    works without a PDF/Image pipeline). Returns empty string on failure.
    """
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            return _read_pdf(path)
        if ext in _IMAGE_EXTS:
            return _read_image(path)
        return _read_text(path)
    except Exception as exc:  # noqa: BLE001 — ingest must not abort the batch
        log.warning("extraction failed for %s: %s", path, exc)
        return ""


def file_sha256(path: Path) -> str:
    """Byte-level sha-256 of a file's contents (audit fingerprint)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def ingest_file(
    path: Path,
    dedup: Deduplicator,
    source_root: Optional[Path] = None,
) -> Optional[Record]:
    """Ingest one file. Returns ``Record`` or ``None`` if a duplicate/empty.

    ``source_root`` (if given) makes ``source_uri`` relative to the drop
    folder so the audit log does not leak absolute operator paths.
    """
    mime = detect_mime(path)
    text = extract_text(path)
    if not text.strip():
        log.info("skipping empty file: %s", path)
        return None

    sha = content_sha256(text)
    if not dedup.add(sha):
        log.info("duplicate fax skipped (content_sha=%s): %s", sha[:10], path.name)
        return None

    if source_root is not None:
        try:
            source_uri = str(path.relative_to(source_root))
        except ValueError:
            source_uri = path.name
    else:
        source_uri = path.name

    return Record(
        source_uri=source_uri,
        mime=mime,
        ocr_text=text,
        content_sha256=sha,
    )


def ingest_folder(
    folder: Path | str,
    dedup: Optional[Deduplicator] = None,
    recursive: bool = True,
) -> tuple[list[Record], Deduplicator]:
    """Walk a folder of faxed/scanned records and return de-duplicated Records.

    Returns ``(records, dedup)`` so the caller can persist the dedup set and
    report duplicate counts in the audit summary.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise NotADirectoryError(f"not a directory: {folder}")

    if dedup is None:
        dedup = Deduplicator()

    records: list[Record] = []
    iterator: Iterable[Path]
    if recursive:
        iterator = sorted(folder.rglob("*"))
    else:
        iterator = sorted(folder.glob("*"))

    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in _EXT_MIME:
            continue
        rec = ingest_file(path, dedup, source_root=folder)
        if rec is not None:
            records.append(rec)

    log.info(
        "ingested %d unique records (%d duplicates skipped) from %s",
        len(records),
        dedup.duplicates_seen,
        folder,
    )
    return records, dedup


__all__ = [
    "Deduplicator",
    "content_sha256",
    "detect_mime",
    "extract_text",
    "file_sha256",
    "ingest_file",
    "ingest_folder",
]

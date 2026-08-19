# Changelog

All notable changes to ClinRec are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] - 2026-08-20

### Fixed

- **fix-rtf-ingest-audit-wrong-extractor-id** (`src/clinrec/cli.py`): the `_ingest_extractor_id` mapping fell through `.rtf` (`application/rtf`) to the catch-all `"text"` id, but `ingest.extract_text` routes `.rtf` to `_read_rtf` (the `striprtf` extractor). The ingest audit op therefore recorded the wrong extractor, and a regulator replaying with `path.read_text` on the raw RTF markup could not match the recorded `output_sha256`. `.rtf` now maps to `"striprtf"`, mirroring `ingest.extract_text` (same class as the v0.2.0 `.txt`→`pdfplumber` fix; the v0.3.0 RTF fix added `_read_rtf` without updating this mapping).
- **fix-audit-verify-omits-phi-egress-invariant** (`src/clinrec/cli.py`): the linked chain hash deliberately excludes `phi_egress`, and `clinrec audit --verify` called only `first_broken_link()`, never `verify_invariant()`. A saved chain with `phi_egress` flipped to `True` exited `0` (`PASS`), hiding a PHI-egress event from the regulator's independent attestation while the sibling `--export` path checked both. `--verify` now also calls `verify_invariant()`, mirroring the `--export` path's dual (chain + invariant) attestation.
- **fix-llm-link-error-output-sha-non-replayable** (`src/clinrec/llm.py`): when an Ollama `chat()` call failed mid-batch (cached `_available=True` kept every subsequent link on the failing LLM path), the `except` branch recorded `output_sha256=_sha(f"err:{exc}")` while returning the rule-based `normalized_code`. A regulator replaying the link op hashed the rule-based output (`{rb_code}|{rb_sys.value}|{rb_conf}`) and got a non-matching digest — a non-replayable link breaking the "regulator can replay every step" guarantee. The `except` branch now records the sha of the actual returned rule-based output, matching the never-available path, so the link op is replayable regardless of why the LLM was not used.

## [0.5.0]

### Fixed

- Include `prompt_sha256` (and `ts`) in the audit chain hash so prompt/timestamp tampering breaks the chain.
- Make an all-negated event `NEGATED` even when a historical cue precedes it.
- Record the raw extracted-text hash (and byte-level file hash) in the ingest audit op, not the normalized dedup hash.
- Use a 2-digit-year pivot for `parse_date` so past dates do not land in the 2000s/2100s.

### Added

- `clinrec audit --verify <state.json>` to independently verify a saved audit chain's integrity from the CLI.

## [0.4.0]

### Fixed

- Scope fallback negation to the entity's own sentence so a cue in a prior sentence does not negate entities in a later sentence.

### Added

- `clinrec timeline --json` machine-readable export of the de-duplicated timeline events.

## [0.3.0]

### Added

- SHA-256 chain hashing (tamper-evidence) to the audit chain so each entry cryptographically links to its predecessor.

### Fixed

- Strip RTF markup in `extract_text` so `.rtf` records feed clean text to NER.
- Replace obfuscated `_RXNORM` placeholder entries with direct mappings and deduplicate `_MEDICATIONS`.

## [0.2.0]

### Fixed

- Record the correct OCR extractor id in the ingest audit op for non-image/non-PDF files (`text`, not `pdfplumber`).
- Record the `ner` audit op in `TimelineAssembler.assemble`.
- Fix `_trim_provider_span` truncating the last name token when provider spans contain multiple whitespace.

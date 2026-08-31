# Changelog

All notable changes to ClinRec are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.7.0] - 2026-08-31

### Fixed

- **fix-ner-audit-input-hash-uses-dedup-key** (`src/clinrec/timeline.py`): the `ner` audit op recorded `input_sha256=rec.content_sha256` (the whitespace-collapsed/lowercased dedup key from `dedup.py`), but the NER engine consumes `rec.ocr_text` whose sha-256 is `rec.raw_text_sha256`. For any record whose OCR text carries uppercase or extra whitespace (the common faxed-record case) the two hashes differ, so a regulator replaying the ner op hashed the actual NER input and got a non-matching digest — a non-replayable ner op breaking the "regulator can replay every step" guarantee. The v0.5.0 ingest fix made the ingest op bind the raw-text hash as its output; the ner op (which actually consumes that text) was never updated, so the ingest→ner chain link was broken. The ner op now records `input_sha256=rec.raw_text_sha256` (in both `assemble` and `resolve_record`), making `ingest.output_sha256 == ner.input_sha256 == raw_text_sha256` and completing the v0.5.0 chain-binding contract.
- **fix-link-audit-date-provider-output-sha-equals-input** (`src/clinrec/llm.py`): the `Linker.link` DATE/PROVIDER branch recorded `output_sha256=_sha(span)`, which equals the link op's `input_sha256` (`sha256_text(r.text_span)` in `timeline.py`, `span == r.text_span`) — making the link op indistinguishable from a no-op in the audit chain for every date and provider entity — and diverged from the rule-based output formula `_sha(f"{code}|{sys}|{conf}")` used by the never-available path and the v0.6.0 except path, so a regulator replaying via that formula got a non-matching digest (same non-replayable-link defect class as the v0.6.0 `fix-llm-link-error-output-sha-non-replayable`, in a branch that fix missed). The DATE/PROVIDER branch now records `output_sha256=_sha(f"{normalized_code}|{code_sys.value}|{confidence}")`, matching the rule-based output formula, representing the link op's actual normalization output, and differing from the input hash.

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

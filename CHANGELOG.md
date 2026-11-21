# Changelog

All notable changes to ClinRec are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.10.0] - 2026-11-22

### Fixed

- **fix-rule-based-code-first-substring-wins** (`src/clinrec/llm.py`): after the exact-match check, `rule_based_code` returned the first table key satisfying `k in key or key in k` in dict insertion order, ignoring where the match sits and how specific it is. A span that leads with a specific diagnosis or formulation was therefore coded as whichever generic key the table happened to list first: `"type 1 diabetes mellitus"` returned `E11.9` (type 2 diabetes) and `"insulin lispro 100 units"` returned `253182` (plain insulin instead of `253145`). The substring path now ranks literal in-span keys by earliest occurrence, then longest key — the term the mention leads with — and only then keeps the "span is a fragment of a canonical key" fallback (longest such key). Exact matches still return 0.95 and every other exact mapping is unchanged.
- **fix-eval-empty-gold-set-passes** (`src/clinrec/eval.py`, `src/clinrec/cli.py`): `run_eval` had no post-loop guard, so an empty gold file (or one whose every row was skipped as malformed) left every metric at 0.0 and the `linker.f1 >= baseline.f1` comparison reported PASS, exit 0 — `clinrec eval --gold empty.jsonl` printed `Gold items: 0` and `PASS — on-prem linker clears the medspaCy-only baseline.` The eval harness is the plan's killer-falsifier, and zero scored rows is not a measurement. `run_eval` now raises `EmptyGoldSetError`, `EvalReport.to_markdown` renders `UNVERIFIED` for a zero-item report, and the eval command prints `eval cannot produce a verdict: no scorable gold items` and exits 1. The same input surface also caught only `FileNotFoundError`: `load_gold` now raises `GoldFileError` naming the path and line for an unreadable file or a malformed JSON line, which the command reports cleanly instead of leaking a `JSONDecodeError` traceback. `docs/eval.md` is corrected to the actual 12-item default set (it claimed 22 items and 22/0/0 counts).
- **fix-audit-verify-empty-chain-pass** (`src/clinrec/cli.py`): `clinrec audit --verify` read `data.get("audit_chain", [])`, so a readable JSON file with no chain — `{}`, a config object, a truncated export, `{"audit_chain": []}` — produced a zero-entry chain on which `first_broken_link()` returns `None` and `verify_invariant()` is vacuously true, and printed `PASS — audit chain integrity verified (0 entries)` with exit 0. The regulator-facing attestation surface now requires a present, list-typed, non-empty `audit_chain` and otherwise prints `FAIL — no audit entries in state file: <path>` with exit 1. The v0.9.0 malformed-row guard, the broken-link FAIL and the `phi_egress` FAIL are unchanged.
- **fix-state-load-corrupt-traceback** (`src/clinrec/store.py`, `src/clinrec/cli.py`): `State.load` read and parsed `state.json` and built every model row unguarded, so a truncated file made `clinrec timeline` and `clinrec audit` print a `JSONDecodeError` traceback, and a valid-JSON document of the wrong shape printed `AttributeError: 'list' object has no attribute 'get'`. v0.9.0 had hardened only the explicit `--verify` path; the default read path used by both commands still leaked the same failure class. `store.py` now owns `StateError` and raises it (naming the path) for unreadable JSON, a non-object document, and malformed rows, and both commands share one handler that prints `could not read state: <path> (<reason>)` and exits 1.

### Changed

- **quality-state-created-at-roundtrip** (`src/clinrec/store.py`): `State.save` persists `ClinicalTimeline.created_at`, but `State.load` constructed the timeline without it, so the persisted ingest timestamp was replaced by the read time on every round-trip. `load` now restores `created_at` when the state file carries it and keeps the default only when the key is absent.
- The CLI now accepts the conventional `clinrec --version` flag (previously only the `clinrec version` subcommand existed); both print `clinrec 0.10.0`.

## [0.9.0] - 2026-10-19

### Fixed

- **fix-audit-verify-malformed-state-traceback** (`src/clinrec/cli.py`): `clinrec audit --verify` guarded only the file *read* (`OSError` / `json.JSONDecodeError`) but built the chain outside that guard, so a structurally corrupt — yet valid-JSON — state file (rows missing `audit_id`/`op`/`input_sha256`, wrong-typed fields, or `audit_chain` not a list) crashed with a raw `KeyError` / `TypeError` / `AttributeError` / pydantic `ValidationError` traceback raised from `AuditChain.from_state`. `--verify` is the regulator's independent attestation surface (v0.5.0 feature-audit-verify-cli, hardened in v0.6.0); a corrupt chain is exactly what it must report cleanly, and every other user-input failure in this CLI prints a clean message and exits 1 (the v0.8.0 `fix-ingest-bad-folder-traceback` convention). The parse step is now wrapped and prints `FAIL — could not parse state file: <path> (<reason>)` with exit 1.
- **fix-eval-gold-path-silent-fallback** (`src/clinrec/eval.py`, `src/clinrec/cli.py`): `load_gold` silently returned the embedded gold set when the explicitly requested `--gold` path did not exist, so `clinrec eval --gold ./typo.jsonl` printed a full report scoring the 12 embedded items — exit 0, with zero indication the requested file was never read. The eval harness is the plan's m1 killer-falsifier ("emits the numbers a regulator and an operator both need"); presenting embedded-set numbers as if the operator's gold file had been scored is misattributed evidence — the same truthfulness class as v0.8.0's `fix-eval-report-wrong-linker-engine`. `load_gold` now raises `FileNotFoundError` for a missing explicit path and the eval command prints a clean `gold file not found: <path>` with exit 1. The same loop also read `item["text"]` outside the per-item guard, so a gold row with a `type` but no `text` crashed `run_eval` with a raw `KeyError`; the guard now covers the whole row read (including non-dict rows) and skips malformed rows like bad-type rows already were.
- **fix-init-clobbers-state-dir** (`src/clinrec/cli.py`): `_write_config` hardcoded `state_dir = ".clinrec"` in the config it writes, and `clinrec init` called it unconditionally. `state_dir` has no CLI flag — hand-editing `clinrec.toml` is the only way to set it — and v0.8.0's `fix-state-dir-config-ignored-on-read` made that key the honored round-trip location for ingest/timeline/audit; re-running `init --host <new>` to change the Ollama endpoint silently reset it and orphaned the operator's state directory. `init` now preserves the current `state_dir` read from the existing config (fresh inits still write the `.clinrec` default) while its managed keys (`ollama_host`/`ollama_model`) still update from the CLI options.

## [0.8.0] - 2026-09-14

### Fixed

- **fix-eval-report-wrong-linker-engine** (`src/clinrec/eval.py`): `run_eval` set `EvalReport.llm_model_id = linker.model` — the *configured* Ollama model tag — regardless of which engine actually produced the scored links. With Ollama unavailable (a fresh clone, or CI, whose smoke test runs `clinrec eval` with no daemon), every `LinkResult` came from the rule-based fallback (`llm_model_id="rule-based"`) while the report printed "Linker engine: `llama3.1:8b-instruct`", attributing rule-based numbers to the on-prem LLM — the same wrong-producer-id class as the v0.2.0 `.txt`→`pdfplumber` and v0.6.0 `.rtf`→`text` audit fixes, on the killer-falsifier quality surface. The report now derives the engine from the observed `LinkResult.llm_model_id` values: `rule-based` when every link was rule-based, `<model> + rule-based fallback` on a mix, the model tag when the LLM produced every link.
- **fix-state-dir-config-ignored-on-read** (`src/clinrec/cli.py`): the `state_dir` key in `clinrec.toml` was honored by `clinrec ingest` (`State(cfg.get("state_dir", ...))`) but the reader commands constructed bare `State()`, hardcoding `./.clinrec` — so an operator with a customized `state_dir` got a successful ingest followed by `clinrec timeline` / `clinrec audit --export` exiting 1 with a false "No state found", breaking the documented write→read round-trip. Both reader commands now build the state location from the same config via a shared `_state_from_config()` helper (`audit --verify` takes an explicit path and is unaffected).
- **fix-ingest-bad-folder-traceback** (`src/clinrec/cli.py`): `clinrec ingest <missing-or-invalid-folder>` crashed with a raw `NotADirectoryError` traceback from `ingest_folder` (the folder is the first argument of the README quickstart, making it the likeliest first-run crash), while every other user-input failure in the same CLI prints a clean message and exits 1. The ingest command now catches `NotADirectoryError`, prints `folder not found or not a directory: <folder>` and exits 1.

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

[简体中文](README.zh-CN.md) | **English**

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/hero-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/hero-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/hero-dark.svg">
  <img src="assets/presentation/hero-light.svg" width="960" alt="ClinRec — Organize records into a source-linked timeline.">
</picture>

**ClinRec organizes local records into timelines through text deduplication, entity extraction and coding, retaining evidence spans and hashes for processing steps.**

`Python 3.12+` · [MIT](LICENSE) · [GitHub](https://github.com/SuperMarioYL/clinrec) · [Website](https://clinrec.lei6393.com)

## Why it helps

Repeated records and events scattered across dated documents make review laborious. Deduplicating equivalent text and keeping events alongside evidence can reduce the search for source material. Extracted results still require professional review; a processing log is not clinical validation or compliance certification.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/process-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/process-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/process-dark.svg">
  <img src="assets/presentation/process-light.svg" width="960" alt="Inspect a fictional local example">
</picture>

## Architecture

ingest extracts text by file type and dedup hashes normalized text. resolve extracts entities through medspaCy or regex; Linker uses the configured Ollama endpoint or rule fallback. TimelineAssembler combines codes, dates and evidence, State persists JSON, and AuditChain links per-operation input/output hashes.

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/architecture-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/architecture-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/architecture-dark.svg">
  <img src="assets/presentation/architecture-light.svg" width="960" alt="Records, evidence and processing history">
</picture>

## Install

Requires Python 3.12+. Full installation includes NLP/OCR client dependencies; image OCR also requires system Tesseract. The TXT demo requires neither OCR nor model weights.

```bash
git clone https://github.com/SuperMarioYL/clinrec.git
cd clinrec
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Quickstart

```bash
python examples/presentation_demo.py
```

The script creates three fictional TXT files, one differing only in case and whitespace. The observed run retains two records, skips one duplicate, and produces three events and thirteen intact audit entries with the rule-based linker. This run uses regex NER fallback; an installed medspaCy environment is identified in ner_engine.

## Usage

```bash
# Prepare Ollama for your deployment, then write configuration
clinrec init
clinrec ingest ./sample-records --patient fictional-example
clinrec timeline
clinrec audit --export audit.jsonl
clinrec eval --gold tests/gold.jsonl
```

These commands may contact the configured Ollama service. Validate the environment and output with fictional data before integrating your workflow. This demo does not exercise remote services, OCR or gold-set evaluation.

## Capabilities and integrations

| Stage | Actual capability |
|---|---|
| TXT/MD | Text reading |
| RTF | striprtf extraction |
| PDF | pdfplumber text-layer extraction |
| Images | pytesseract OCR with external Tesseract |
| NLP/linker | medspaCy/regex and Ollama/rule fallback |
| Output | Timeline, JSON state, JSONL audit and terminal view |

<picture>
  <source media="(max-width: 640px) and (prefers-color-scheme: dark)" srcset="assets/presentation/integrations-mobile-dark.svg">
  <source media="(max-width: 640px)" srcset="assets/presentation/integrations-mobile-light.svg">
  <source media="(prefers-color-scheme: dark)" srcset="assets/presentation/integrations-dark.svg">
  <img src="assets/presentation/integrations-light.svg" width="960" alt="Document inputs and review outputs">
</picture>

## Configuration and limits

| clinrec.toml key | Default |
|---|---|
| ollama_host | http://127.0.0.1:11434 |
| ollama_model | llama3.1:8b-instruct |
| state_dir | .clinrec |

The default model endpoint is loopback, but the code accepts other configured hosts; it does not enforce network isolation or monitor egress. phi_egress is an audit field, and False is not evidence that no data left the host.

Deduplication collapses whitespace and case; it is not fuzzy semantic matching. The PDF path does not automatically OCR pages lacking a text layer; extraction failures may be logged and skipped. Entity rules and code mappings are limited, date association is heuristic, and evidence spans need source review.

## Recorded demo

A constructed local v0.7.0 example. A reserved, non-listening loopback port makes the real linker use its implemented rule fallback; no model is called. Results do not validate clinical records, OCR, model quality or regulatory requirements.

[Inputs, commands and complete output](docs/demo-results.json)

[Retained terminal recording](assets/demo.gif) · [Recording script](docs/demo.tape). The replayable record above describes this example.

## Roadmap

- [x] Text/image ingestion and normalized-text deduplication.
- [x] Entity extraction, coding, evidence and timeline assembly.
- [x] JSON state, linked hash records, terminal browsing and an evaluation entrypoint.
- [ ] Extensions such as FHIR export and cross-provider patient matching.

No hosted plan, service SLA or compliance certification is delivered by this repository.

## Development and license

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

See [docs/eval.md](docs/eval.md) for the evaluation interface; inspect the actual dataset and metrics used.

[MIT](LICENSE) · [Issues](https://github.com/SuperMarioYL/clinrec/issues)

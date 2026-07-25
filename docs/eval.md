# ClinRec Eval — the m1 killer-falsifier

> The single highest-risk technical question for ClinRec is whether an
> on-prem 7B–32B entity linker clears a regulator-reviewable quality bar.
> This harness is the first milestone built to answer it; if it does not
> clear the bar, the plan says kill.

## What it scores

`clinrec eval` runs the full extraction + linking pipeline over a
de-identified gold set and reports **precision / recall / F1** for code
normalization, comparing:

1. **On-prem linker** — medspaCy NER + the on-prem Llama 3.1 8B linker
   (Ollama, zero cloud calls) → ICD-10 / RxNorm / CPT codes.
2. **Rule-based baseline** (the "medspaCy-only" bar) — the deterministic
   lookup table the linker degrades to when Ollama is down.

Both are scored against the same gold labels so the comparison is
apples-to-apples. The verdict is `PASS` when the on-prem linker's F1 is
`>=` the rule-based baseline's F1; otherwise `FAIL`.

## Kill criteria (from the plan, §8)

- **Quality falsifier (m1, end of weekend 2):** if on-prem NER+linker
  precision/recall on the de-identified faxed set does not clear the
  medspaCy-only baseline **AND** stay within 10 points of a hosted-frontier
  baseline, kill — the regulator-reviewable quality bar is not met on-prem.
- The hosted-frontier baseline is not wired into this OSS harness (it would
  leak PHI to a cloud model). Operators run it offline against the same
  gold set and compare the two F1 numbers manually.

## Running it

```bash
# default: the embedded curated gold set (22 de-identified items)
clinrec eval

# against a custom gold JSON-lines file
clinrec eval --gold tests/gold.jsonl
```

The gold file is one JSON object per line:

```json
{"text": "metformin 500 mg", "type": "MEDICATION", "code": "6809", "system": "RxNorm"}
```

`type` is one of `CONDITION`, `MEDICATION`, `PROCEDURE`, `DATE`,
`PROVIDER`. `code` is the expected normalized code
(`""` for uncoded types like DATE/PROVIDER).

## Scoring definition

For each gold item with a non-empty `code`:

- **TP** — the linker's predicted code equals the gold code.
- **FN** — the linker's predicted code differs from (or is empty vs.) gold.
- **FP** — the linker's predicted code is non-empty and wrong, or
  non-empty when gold has no code.

Then:

- `precision = TP / (TP + FP)`
- `recall    = TP / (TP + FN)`
- `F1        = 2 * P * R / (P + R)`

## Current result (no Ollama daemon)

On a fresh clone with no Ollama running, both paths degenerate to the
rule-based coder, so the harness reports the **baseline bar** itself — the
floor the on-prem LLM linker must meet or beat once `ollama serve` is up
and `llama3.1:8b-instruct` is pulled:

```
| Metric    | On-prem linker | Rule-based baseline |
| Precision | 1.000          | 1.000               |
| Recall    | 1.000          | 1.000               |
| F1        | 1.000          | 1.000               |
| TP/FP/FN  | 22/0/0         | 22/0/0              |
```

The curated gold set is intentionally built so the rule-based coder clears
it cleanly; that is the *floor*. The open question (whether the on-prem
LLM linker beats this bar on messier, out-of-vocabulary spans) is what the
design-partner de-identified fax sets answer.

## Reproducibility

The harness is deterministic: the medspaCy pipeline + rule-based coder
produce the same output on every run (the only non-determinism would be
the LLM, which is pinned to `temperature=0.0`). Commit your `gold.jsonl`
next to this file so the numbers are reproducible across reviewers and
across the quarterly eval reruns a ClinRec Pro audit pack ships.

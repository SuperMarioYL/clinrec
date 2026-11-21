"""Quality eval harness — the m1 killer-falsifier.

``clinrec eval`` runs NER + on-prem linker over a small de-identified gold
set and reports precision / recall / F1 for code normalization, comparing
the on-prem LLM linker against the rule-based (medspaCy-only) baseline.
The plan's kill criteria: if the on-prem NER+linker does not clear the
medspaCy-only baseline AND stay within 10 points of a hosted-frontier
baseline, kill. This harness emits the numbers a regulator and an
operator both need.

When Ollama is not running, both paths degenerate to rule-based coding so
the harness still produces a baseline report (the LLM-vs-baseline gap is
populated only with a live Ollama daemon).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .llm import LinkResult, Linker, rule_based_code
from .models import CodeSystem, EntityType
from .resolve import EntityExtractor
from .audit import AuditChain

# A small embedded de-identified gold set. Real runs load tests/gold.jsonl
# (the committed fixtures) so the numbers are reproducible. These examples
# are synthetic clinical text — no real PHI.
EMBEDDED_GOLD = [
    {"text": "Type 2 diabetes", "type": "CONDITION", "code": "E11.9", "system": "ICD-10"},
    {"text": "diabetes mellitus", "type": "CONDITION", "code": "E11.9", "system": "ICD-10"},
    {"text": "hypertension", "type": "CONDITION", "code": "I10", "system": "ICD-10"},
    {"text": "atrial fibrillation", "type": "CONDITION", "code": "I48.91", "system": "ICD-10"},
    {"text": "metformin 500 mg", "type": "MEDICATION", "code": "6809", "system": "RxNorm"},
    {"text": "lisinopril", "type": "MEDICATION", "code": "29046", "system": "RxNorm"},
    {"text": "atorvastatin 40 mg", "type": "MEDICATION", "code": "83367", "system": "RxNorm"},
    {"text": "colonoscopy", "type": "PROCEDURE", "code": "45378", "system": "CPT"},
    {"text": "echocardiogram", "type": "PROCEDURE", "code": "93306", "system": "CPT"},
    {"text": "copd", "type": "CONDITION", "code": "J44.9", "system": "ICD-10"},
    {"text": "metoprolol", "type": "MEDICATION", "code": "8948", "system": "RxNorm"},
    {"text": "mammogram", "type": "PROCEDURE", "code": "77067", "system": "CPT"},
]


class EmptyGoldSetError(ValueError):
    """The gold set yielded no scorable items, so there is no verdict to report.

    v0.10.0 — fix-eval-empty-gold-set-passes: an empty (or wholly unparsable)
    gold set previously left every metric at 0.0 and the 0.0 >= 0.0 comparison
    printed PASS, presenting a non-measurement as evidence the killer-falsifier
    cleared its bar.
    """


class GoldFileError(ValueError):
    """A gold JSONL file could not be read or parsed (the message locates it)."""


@dataclass
class LinkMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    n: int = 0

    @property
    def precision(self) -> float:
        denom = self.tp + self.fp
        return self.tp / denom if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.tp + self.fn
        return self.tp / denom if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass
class EvalReport:
    linker_metrics: LinkMetrics
    baseline_metrics: LinkMetrics
    llm_model_id: str = "rule-based"
    uses_medspacy: bool = False
    per_item: list[dict] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [
            "# ClinRec Eval Report",
            "",
            f"- NER engine: {'medspaCy' if self.uses_medspacy else 'regex fallback'}",
            f"- Linker engine: `{self.llm_model_id}`",
            f"- Gold items: {self.linker_metrics.n}",
            "",
            "| Metric | On-prem linker | Rule-based baseline |",
            "|---|---|---|",
            f"| Precision | {self.linker_metrics.precision:.3f} | {self.baseline_metrics.precision:.3f} |",
            f"| Recall    | {self.linker_metrics.recall:.3f} | {self.baseline_metrics.recall:.3f} |",
            f"| F1        | {self.linker_metrics.f1:.3f} | {self.baseline_metrics.f1:.3f} |",
            f"| TP/FP/FN  | {self.linker_metrics.tp}/{self.linker_metrics.fp}/{self.linker_metrics.fn} "
            f"| {self.baseline_metrics.tp}/{self.baseline_metrics.fp}/{self.baseline_metrics.fn} |",
            "",
        ]
        # v0.10.0 — fix-eval-empty-gold-set-passes: never read two degenerate
        # 0.0 scores as a measured PASS.
        if self.linker_metrics.n == 0:
            lines.append(
                "**Verdict:** UNVERIFIED — no scorable gold items; there is no "
                "measurement to compare."
            )
            return "\n".join(lines)
        clears_baseline = self.linker_metrics.f1 >= self.baseline_metrics.f1
        lines.append(
            f"**Verdict:** {'PASS' if clears_baseline else 'FAIL'} — "
            f"on-prem linker {'clears' if clears_baseline else 'does NOT clear'} "
            "the medspaCy-only baseline bar."
        )
        return "\n".join(lines)


def load_gold(path: Path | str) -> list[dict]:
    """Load a gold set from a JSON-lines file (one item per line).

    v0.9.0 — fix-eval-gold-path-silent-fallback: an explicitly requested gold
    file that does not exist raises FileNotFoundError instead of silently
    substituting EMBEDDED_GOLD — the killer-falsifier report must reflect the
    set the operator asked to score, and a typo'd path previously produced an
    exit-0 report over the embedded items with no indication.

    v0.10.0 — fix-eval-gold-input-verdict: an unreadable file or a malformed
    JSON line raises GoldFileError naming the path and line, instead of leaking
    a raw OSError/JSONDecodeError traceback out of the eval command.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"gold file not found: {p}")
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise GoldFileError(f"{p}: {exc}") from exc
    out: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise GoldFileError(f"{p}: line {lineno}: {exc.msg}") from exc
    return out


def _score(pred_code: str, pred_sys: CodeSystem, gold: dict, metrics: LinkMetrics) -> bool:
    gold_code = str(gold.get("code", "")).strip()
    metrics.n += 1
    if not gold_code:
        # no gold code — predicted anything is a FP, nothing is a TN
        if pred_code:
            metrics.fp += 1
            return False
        return True
    if pred_code == gold_code:
        metrics.tp += 1
        return True
    # wrong code: FN (missed the right one) + FP (asserted a wrong one) if non-empty
    metrics.fn += 1
    if pred_code:
        metrics.fp += 1
    return False


def run_eval(
    gold: Iterable[dict] | None = None,
    linker: Linker | None = None,
    extractor: EntityExtractor | None = None,
) -> EvalReport:
    """Run the killer-falsifier: linker vs rule-based baseline on the gold set."""
    gold = list(gold) if gold is not None else list(EMBEDDED_GOLD)
    linker = linker or Linker()
    extractor = extractor or EntityExtractor()
    audit = AuditChain()

    llm_metrics = LinkMetrics()
    base_metrics = LinkMetrics()
    per_item: list[dict] = []
    # Distinct engines that actually produced the scored link results, so the
    # report names the engine behind the numbers rather than the configured
    # tag (v0.8.0 — fix-eval-report-wrong-linker-engine).
    used_engines: set[str] = set()

    for item in gold:
        # v0.9.0 — fix-eval-gold-path-silent-fallback: the guard covers the
        # whole row read (type + text, non-dict rows) so a malformed line in
        # an operator-supplied gold file is skipped like a bad-type row
        # instead of crashing run_eval with a raw KeyError/TypeError.
        try:
            etype = EntityType(item["type"])
            span = str(item["text"])
        except (KeyError, TypeError, ValueError):
            continue
        lr: LinkResult = linker.link(span, etype)
        used_engines.add(lr.llm_model_id)
        rb_code, rb_sys, rb_conf = rule_based_code(span, etype)
        ok_llm = _score(lr.normalized_code, lr.code_sys, item, llm_metrics)
        ok_base = _score(rb_code, rb_sys, item, base_metrics)
        per_item.append(
            {
                "text": span,
                "type": etype.value,
                "gold_code": item.get("code", ""),
                "linker_code": lr.normalized_code,
                "linker_system": lr.code_sys.value,
                "linker_confidence": lr.confidence,
                "linker_ok": ok_llm,
                "baseline_code": rb_code,
                "baseline_ok": ok_base,
            }
        )

    # v0.10.0 — fix-eval-empty-gold-set-passes: zero scored rows mean there is
    # no measurement. Previously every metric stayed 0.0 and the downstream
    # 0.0 >= 0.0 comparison reported PASS for an empty or wholly malformed set.
    if llm_metrics.n == 0:
        raise EmptyGoldSetError(
            "no scorable gold items (the set is empty or every row was malformed)"
        )

    # v0.8.0 — fix-eval-report-wrong-linker-engine: report the engine that
    # actually produced the scored links, derived from the observed
    # LinkResult.llm_model_id values. Previously this recorded
    # linker.model (the CONFIGURED Ollama tag), so with the daemon down —
    # a fresh clone or CI, which runs `clinrec eval` with no Ollama — every
    # link came from the rule-based fallback while the report printed
    # "Linker engine: `llama3.1:8b-instruct`", attributing rule-based
    # numbers to the on-prem LLM (the same wrong-producer-id class as the
    # v0.2.0/v0.6.0 audit extractor-id fixes).
    if not used_engines:
        llm_engine = linker.model  # no links scored — nothing to misattribute
    elif used_engines == {"rule-based"}:
        llm_engine = "rule-based"
    elif "rule-based" in used_engines:
        llm_engine = f"{linker.model} + rule-based fallback"
    else:
        llm_engine = linker.model

    return EvalReport(
        linker_metrics=llm_metrics,
        baseline_metrics=base_metrics,
        llm_model_id=llm_engine,
        uses_medspacy=extractor.uses_medspacy,
        per_item=per_item,
    )


__all__ = [
    "EMBEDDED_GOLD",
    "EmptyGoldSetError",
    "EvalReport",
    "GoldFileError",
    "LinkMetrics",
    "load_gold",
    "run_eval",
]

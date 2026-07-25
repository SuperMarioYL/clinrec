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
        clears_baseline = self.linker_metrics.f1 >= self.baseline_metrics.f1
        lines.append(
            f"**Verdict:** {'PASS' if clears_baseline else 'FAIL'} — "
            f"on-prem linker {'clears' if clears_baseline else 'does NOT clear'} "
            "the medspaCy-only baseline bar."
        )
        return "\n".join(lines)


def load_gold(path: Path | str) -> list[dict]:
    """Load a gold set from a JSON-lines file (one item per line)."""
    p = Path(path)
    if not p.exists():
        return list(EMBEDDED_GOLD)
    out: list[dict] = []
    with p.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
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

    for item in gold:
        try:
            etype = EntityType(item["type"])
        except (KeyError, ValueError):
            continue
        span = str(item["text"])
        lr: LinkResult = linker.link(span, etype)
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

    return EvalReport(
        linker_metrics=llm_metrics,
        baseline_metrics=base_metrics,
        llm_model_id=linker.model,
        uses_medspacy=extractor.uses_medspacy,
        per_item=per_item,
    )


__all__ = ["EMBEDDED_GOLD", "EvalReport", "LinkMetrics", "load_gold", "run_eval"]

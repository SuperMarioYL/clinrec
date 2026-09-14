"""m1: killer-falsifier eval harness — linker vs rule-based baseline."""
from __future__ import annotations

from clinrec.eval import EMBEDDED_GOLD, EvalReport, LinkMetrics, run_eval


def test_run_eval_returns_metrics():
    report = run_eval()
    assert isinstance(report, EvalReport)
    assert report.linker_metrics.n == len(EMBEDDED_GOLD)
    assert 0.0 <= report.linker_metrics.precision <= 1.0
    assert 0.0 <= report.linker_metrics.recall <= 1.0
    assert 0.0 <= report.linker_metrics.f1 <= 1.0


def test_rule_based_baseline_clears_high_bar_on_embedded_gold():
    """The embedded gold set is curated so the rule-based baseline clears a
    high bar — this is the floor the on-prem LLM linker must meet or beat."""
    report = run_eval()
    # without ollama both paths are rule-based → equal F1, both ~1.0
    assert report.baseline_metrics.f1 >= 0.9
    assert report.linker_metrics.f1 >= 0.9


def test_eval_passes_when_linker_meets_baseline():
    """On a fresh clone (no ollama), linker == rule-based baseline → PASS."""
    report = run_eval()
    md = report.to_markdown()
    assert "PASS" in md
    assert "medspaCy-only baseline" in md


def test_eval_per_item_records_predictions():
    report = run_eval()
    assert len(report.per_item) == len(EMBEDDED_GOLD)
    item = report.per_item[0]
    assert {"text", "type", "gold_code", "linker_code", "linker_ok", "baseline_code", "baseline_ok"} <= set(item)


def test_link_metrics_f1_zero_on_no_tp():
    m = LinkMetrics(tp=0, fp=5, fn=5, n=10)
    assert m.precision == 0.0
    assert m.recall == 0.0
    assert m.f1 == 0.0


def test_link_metrics_perfect():
    m = LinkMetrics(tp=10, fp=0, fn=0, n=10)
    assert m.precision == 1.0
    assert m.recall == 1.0
    assert m.f1 == 1.0


def test_run_eval_with_custom_gold():
    custom = [
        {"text": "diabetes", "type": "CONDITION", "code": "E11.9", "system": "ICD-10"},
        {"text": "metformin", "type": "MEDICATION", "code": "6809", "system": "RxNorm"},
    ]
    report = run_eval(gold=custom)
    assert report.linker_metrics.n == 2
    assert report.linker_metrics.f1 == 1.0  # both exact-match


def test_eval_reports_rule_based_engine_when_ollama_down():
    """v0.8.0 fix-eval-report-wrong-linker-engine — with Ollama unavailable
    every scored link comes from the rule-based fallback (LinkResult
    llm_model_id == "rule-based"), so the report must name rule-based — not
    the configured model tag — as the engine that produced the numbers.
    Previously the report printed "Linker engine: `llama3.1:8b-instruct`"
    over rule-based results, the same wrong-producer-id class as the
    v0.2.0/v0.6.0 audit extractor-id fixes."""
    from clinrec.llm import Linker

    linker = Linker(host="http://127.0.0.1:1")  # deterministically dead port
    assert linker.is_available() is False
    report = run_eval(linker=linker)
    assert report.llm_model_id == "rule-based"
    engine_line = [
        line for line in report.to_markdown().splitlines() if "Linker engine" in line
    ][0]
    assert "rule-based" in engine_line
    assert "llama3.1" not in engine_line

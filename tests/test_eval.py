"""m1: killer-falsifier eval harness — linker vs rule-based baseline."""
from __future__ import annotations

import pytest

from clinrec.eval import (
    EMBEDDED_GOLD,
    EmptyGoldSetError,
    EvalReport,
    GoldFileError,
    LinkMetrics,
    load_gold,
    run_eval,
)


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


def test_load_gold_missing_path_raises(tmp_path):
    """v0.9.0 fix-eval-gold-path-silent-fallback — an explicitly requested
    gold file that does not exist raises FileNotFoundError instead of silently
    substituting EMBEDDED_GOLD (the report must reflect the set the operator
    asked to score; a typo'd path previously scored the embedded set, exit 0,
    with no indication)."""
    with pytest.raises(FileNotFoundError):
        load_gold(tmp_path / "nope.jsonl")


def test_run_eval_skips_malformed_gold_rows():
    """v0.9.0 fix-eval-gold-path-silent-fallback (adjacent surface) — a gold
    row with a valid type but a missing text field (or a non-dict row) is
    skipped like a bad-type row instead of crashing run_eval with a raw
    KeyError/TypeError."""
    gold = [
        {"text": "diabetes", "type": "CONDITION", "code": "E11.9", "system": "ICD-10"},
        {"type": "CONDITION", "code": "I10"},  # missing text
        "not-a-dict",  # non-dict row
    ]
    report = run_eval(gold=gold)
    assert report.linker_metrics.n == 1
    assert report.linker_metrics.f1 == 1.0


def test_eval_empty_gold_set_is_an_error():
    """v0.10.0 fix-eval-empty-gold-set-passes — a gold set with zero scorable
    items is not a measurement. Previously every metric stayed 0.0 on both
    sides and the 0.0 >= 0.0 comparison printed PASS (exit 0), so an empty or
    wholly unparsable gold file looked like the killer-falsifier had passed."""
    with pytest.raises(EmptyGoldSetError):
        run_eval(gold=[])
    with pytest.raises(EmptyGoldSetError):
        run_eval(gold=[{"type": "CONDITION", "code": "I10"}])  # no text -> skipped


def test_zero_item_report_does_not_pass():
    """v0.10.0 — even a directly constructed zero-item report must not claim
    PASS off two degenerate 0.0 F1 values."""
    report = EvalReport(linker_metrics=LinkMetrics(), baseline_metrics=LinkMetrics())
    md = report.to_markdown()
    assert "UNVERIFIED" in md
    assert "PASS" not in md


def test_load_gold_malformed_line_names_line(tmp_path):
    """v0.10.0 fix-eval-gold-input-verdict — a malformed JSON line in an
    operator gold file is a clean, located error instead of an uncaught
    JSONDecodeError traceback out of the eval command."""
    p = tmp_path / "bad.jsonl"
    p.write_text(
        '{"text": "diabetes", "type": "CONDITION", "code": "E11.9"}\nnot-json\n',
        encoding="utf-8",
    )
    with pytest.raises(GoldFileError) as exc:
        load_gold(p)
    assert "line 2" in str(exc.value)

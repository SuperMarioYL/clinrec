"""m2: CLI commands (init / ingest / timeline / audit / eval / version)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from clinrec.cli import app

runner = CliRunner()


def _write_sample(folder: Path) -> None:
    (folder / "fax1.txt").write_text(
        "Patient seen 01/15/2024. Dr. Jane Smith noted diabetes. Started metformin 500 mg. denies chest pain.",
        encoding="utf-8",
    )
    (folder / "fax2.txt").write_text(
        "02/20/2024 follow-up. Hypertension now controlled. Continue lisinopril. history of stroke.",
        encoding="utf-8",
    )
    # a re-fax (duplicate content, different filename) to exercise dedup
    (folder / "fax1_refax.txt").write_text(
        "  patient seen 01/15/2024. dr. jane smith noted diabetes.   started metformin 500 mg. denies chest pain.\n",
        encoding="utf-8",
    )


def test_cli_version():
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    assert "0.3.0" in res.stdout


def test_cli_init_writes_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0
    cfg = tmp_path / "clinrec.toml"
    assert cfg.exists()
    body = cfg.read_text()
    assert "ollama_host" in body
    assert "llama3.1:8b-instruct" in body


def test_cli_ingest_creates_state_and_summary(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)

    res = runner.invoke(app, ["ingest", str(sample)])
    assert res.exit_code == 0, res.stdout
    assert "Ingest summary" in res.stdout
    assert "records" in res.stdout
    # state file written
    state = tmp_path / ".clinrec" / "state.json"
    assert state.exists()
    payload = json.loads(state.read_text())
    assert payload["records"]
    assert payload["events"]
    assert payload["audit_chain"]


def test_cli_ingest_reports_duplicate_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    res = runner.invoke(app, ["ingest", str(sample)])
    assert "1" in res.stdout  # 1 duplicate skipped


def test_cli_audit_export_jsonl(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    out_path = tmp_path / "audit.jsonl"
    res = runner.invoke(app, ["audit", "--export", str(out_path)])
    assert res.exit_code == 0, res.stdout
    assert out_path.exists()
    lines = out_path.read_text().strip().splitlines()
    assert len(lines) > 0
    for line in lines:
        row = json.loads(line)
        assert row["phi_egress"] is False
    assert "phi_egress invariant" in res.stdout
    assert "PASS" in res.stdout


def test_cli_timeline_runs_tui(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    # drive the TUI by piping 'q' to quit immediately
    res = runner.invoke(app, ["timeline"], input="q\n")
    assert res.exit_code == 0
    assert "Clinical Timeline" in res.stdout or "No timeline events" in res.stdout


def test_cli_eval_runs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["eval"])
    assert res.exit_code == 0, res.stdout
    assert "Eval" in res.stdout
    assert "PASS" in res.stdout


def test_cli_timeline_without_state_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["timeline"])
    assert res.exit_code == 1
    assert "No state" in res.stdout or "ingest" in res.stdout


def test_cli_audit_without_state_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["audit"])
    assert res.exit_code == 1


def test_cli_help():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "ingest" in res.stdout
    assert "timeline" in res.stdout
    assert "audit" in res.stdout

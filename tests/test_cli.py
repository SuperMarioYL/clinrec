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
    assert "0.5.0" in res.stdout


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


def test_cli_timeline_json(tmp_path, monkeypatch):
    # v0.4.0 feat-timeline-json-export: --json emits the de-duplicated
    # timeline events as a JSON array that round-trips into a list.
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    res = runner.invoke(app, ["timeline", "--json"])
    assert res.exit_code == 0, res.stdout
    events = json.loads(res.stdout)
    assert isinstance(events, list)
    assert len(events) >= 1
    # each event carries the regulator-reviewable fields
    for ev in events:
        assert "event_id" in ev
        assert "entity_type" in ev
        assert "normalized_code" in ev
        assert "status" in ev


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


def test_cli_ingest_audit_binds_file_bytes_and_raw_text(tmp_path, monkeypatch):
    """v0.5.0 fix-ingest-audit-output-hash-mismatch — the ingest audit op's
    output_sha256 must equal sha256(rec.ocr_text) (the raw NER input) and its
    input_sha256 must equal the byte-level file hash, not the lossy normalized
    dedup hash. Previously both were rec.content_sha256, which NER never
    consumed, so a regulator replaying the chain could not match the ingest
    output to the NER input.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    fax = sample / "fax1.txt"
    fax.write_text(
        "Patient seen 01/15/2024. Dr. Jane Smith noted diabetes.", encoding="utf-8"
    )
    res = runner.invoke(app, ["ingest", str(sample)])
    assert res.exit_code == 0, res.stdout

    payload = json.loads((tmp_path / ".clinrec" / "state.json").read_text())
    ingest_entries = [e for e in payload["audit_chain"] if e["op"] == "ingest"]
    assert ingest_entries, "no ingest audit entry"
    rec = payload["records"][0]

    from clinrec.audit import sha256_text
    from clinrec.ingest import file_sha256

    # output is the raw extracted text (the actual NER input), not the dedup hash
    assert ingest_entries[0]["output_sha256"] == sha256_text(rec["ocr_text"])
    assert ingest_entries[0]["output_sha256"] != rec["content_sha256"]
    # input is the byte-level file fingerprint, binding the original fax bytes
    assert ingest_entries[0]["input_sha256"] == file_sha256(fax)


def test_cli_audit_verify_pass_on_clean_chain(tmp_path, monkeypatch):
    """v0.5.0 feature-audit-verify-cli — a clean saved chain verifies PASS (exit 0)
    from the CLI, so a regulator can independently attest the chain without Python.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    state = tmp_path / ".clinrec" / "state.json"
    res = runner.invoke(app, ["audit", "--verify", str(state)])
    assert res.exit_code == 0, res.stdout
    assert "PASS" in res.stdout
    assert "audit chain integrity" in res.stdout


def test_cli_audit_verify_fails_on_tampered_chain(tmp_path, monkeypatch):
    """v0.5.0 feature-audit-verify-cli (golden test) — a tampered state.json
    exits non-zero with a diagnostic naming the first broken link's index +
    op. Tampering a link op's prompt_sha256 also exercises
    fix-chain-hash-omits-prompt-sha at the CLI level.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    state = tmp_path / ".clinrec" / "state.json"
    payload = json.loads(state.read_text())
    # tamper the first link op's prompt_sha256 in the saved chain
    link_idx = next(
        i for i, e in enumerate(payload["audit_chain"]) if e["op"] == "link"
    )
    payload["audit_chain"][link_idx]["prompt_sha256"] = "x" * 64
    state.write_text(json.dumps(payload), encoding="utf-8")

    res = runner.invoke(app, ["audit", "--verify", str(state)])
    assert res.exit_code == 1, res.stdout
    assert "FAIL" in res.stdout
    assert "broken at index" in res.stdout
    assert "link" in res.stdout  # the tampered op


def test_cli_audit_verify_missing_file_errors(tmp_path, monkeypatch):
    """v0.5.0 feature-audit-verify-cli — a missing state path exits non-zero."""
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["audit", "--verify", str(tmp_path / "nope.json")])
    assert res.exit_code == 1
    assert "not found" in res.stdout

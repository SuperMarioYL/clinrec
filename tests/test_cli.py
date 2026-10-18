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
    assert "0.9.0" in res.stdout


def test_cli_init_writes_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["init"])
    assert res.exit_code == 0
    cfg = tmp_path / "clinrec.toml"
    assert cfg.exists()
    body = cfg.read_text()
    assert "ollama_host" in body
    assert "llama3.1:8b-instruct" in body
    # v0.9.0 fix-init-clobbers-state-dir: a fresh init still writes the default
    assert 'state_dir = ".clinrec"' in body


def test_cli_init_preserves_custom_state_dir(tmp_path, monkeypatch):
    """v0.9.0 fix-init-clobbers-state-dir — state_dir has no CLI flag (hand-
    editing clinrec.toml is the only way to set it) and v0.8.0 made it the
    honored round-trip location for every command; re-running init to change
    the Ollama endpoint must keep it instead of silently resetting it to
    '.clinrec' and orphaning the operator's state directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "clinrec.toml").write_text(
        'ollama_host = "http://10.0.0.5:11434"\n'
        'ollama_model = "llama3.1:8b-instruct"\n'
        'state_dir = "custom-state"\n',
        encoding="utf-8",
    )
    res = runner.invoke(app, ["init", "--host", "http://127.0.0.1:1"])
    assert res.exit_code == 0, res.stdout
    body = (tmp_path / "clinrec.toml").read_text()
    assert 'state_dir = "custom-state"' in body
    # init's managed keys still update from the CLI options
    assert 'ollama_host = "http://127.0.0.1:1"' in body


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


def test_cli_ingest_ner_audit_binds_raw_text_input(tmp_path, monkeypatch):
    """v0.7.0 fix-ner-audit-input-hash-uses-dedup-key — the ner audit op's
    input_sha256 must equal sha256(rec.ocr_text) (the actual NER input, exposed
    on the Record as raw_text_sha256), NOT rec.content_sha256 (the lossy
    whitespace-collapsed/lowercased dedup key). The v0.5.0 ingest fix made the
    ingest op's output_sha256 bind the raw text; the ner op (which actually
    consumes rec.ocr_text) must bind the SAME raw-text hash as its input, so
    the ingest->ner chain link is verifiably intact (ingest.output ==
    ner.input == raw_text_sha256). Previously the ner op recorded the dedup
    key, so a regulator replaying the ner op hashed the actual NER input and
    got a non-matching digest.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    fax = sample / "fax1.txt"
    # uppercase + double-space so the normalized dedup key differs from the raw
    # text hash (exposes the bug; a pure-lowercase single-space record would mask it)
    fax.write_text(
        "Patient seen 01/15/2024.  Dr. Jane Smith noted Diabetes.  Started metformin 500 mg.",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["ingest", str(sample)])
    assert res.exit_code == 0, res.stdout

    payload = json.loads((tmp_path / ".clinrec" / "state.json").read_text())
    ner_entries = [e for e in payload["audit_chain"] if e["op"] == "ner"]
    assert ner_entries, "no ner audit entry"
    ingest_entries = [e for e in payload["audit_chain"] if e["op"] == "ingest"]
    assert ingest_entries, "no ingest audit entry"
    rec = payload["records"][0]

    from clinrec.audit import sha256_text

    # the ner op binds the actual NER input (raw extracted text), not the dedup key
    assert ner_entries[0]["input_sha256"] == sha256_text(rec["ocr_text"])
    assert ner_entries[0]["input_sha256"] == rec["raw_text_sha256"]
    assert ner_entries[0]["input_sha256"] != rec["content_sha256"]
    # the ingest->ner chain link is intact: ingest output == ner input (raw text)
    assert ingest_entries[0]["output_sha256"] == ner_entries[0]["input_sha256"]


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


def test_cli_ingest_rtf_records_striprtf_extractor_and_replayable_sha(tmp_path, monkeypatch):
    """v0.6.0 fix-rtf-ingest-audit-wrong-extractor-id — ingesting a .rtf
    records the striprtf extractor id (not the catch-all 'text'), and the
    ingest output_sha256 matches a regulator replay that re-runs striprtf on
    the raw RTF markup — NOT a path.read_text replay of the raw markup. The
    v0.3.0 RTF fix added _read_rtf without updating the mime->extractor
    mapping, so a regulator replaying with path.read_text could not match the
    recorded output_sha256.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    rtf_path = sample / "note.rtf"
    rtf_body = r"{\rtf1\ansi\deff0 {\fonttbl {\f0 Helvetica;}} \f0 Patient has diabetes. Started metformin 500 mg.}"
    rtf_path.write_text(rtf_body, encoding="utf-8")
    res = runner.invoke(app, ["ingest", str(sample)])
    assert res.exit_code == 0, res.stdout

    payload = json.loads((tmp_path / ".clinrec" / "state.json").read_text())
    ingest_entries = [e for e in payload["audit_chain"] if e["op"] == "ingest"]
    assert ingest_entries, "no ingest audit entry"

    # the audit records the extractor that actually produced the text
    assert ingest_entries[0]["llm_model_id"] == "striprtf"

    from clinrec.audit import sha256_text
    from striprtf.striprtf import rtf_to_text

    cleaned = rtf_to_text(rtf_body)
    rec = payload["records"][0]
    # the recorded output is the striprtf-cleaned text (the actual NER input)
    assert rec["ocr_text"] == cleaned
    # a regulator replaying via striprtf reproduces the recorded digest
    assert ingest_entries[0]["output_sha256"] == sha256_text(cleaned)
    # a path.read_text replay (raw RTF markup) does NOT match
    assert ingest_entries[0]["output_sha256"] != sha256_text(rtf_body)


def test_cli_audit_verify_fails_on_phi_egress_tampered(tmp_path, monkeypatch):
    """v0.6.0 fix-audit-verify-omits-phi-egress-invariant — the linked
    chain hash deliberately excludes phi_egress, so first_broken_link()
    alone could not detect a flipped phi_egress=True and --verify printed
    PASS / exit 0 for a chain recording a PHI-egress event, hiding the
    primitive's headline compliance guarantee. --verify now also calls
    verify_invariant(), so a tampered state.json with phi_egress flipped
    True FAILS (non-zero) with the invariant diagnostic.
    """
    monkeypatch.chdir(tmp_path)
    sample = tmp_path / "sample-records"
    sample.mkdir()
    _write_sample(sample)
    runner.invoke(app, ["ingest", str(sample)])
    state = tmp_path / ".clinrec" / "state.json"
    payload = json.loads(state.read_text())
    # flip one entry's phi_egress to True (the chain hash does NOT cover this
    # field, so first_broken_link() alone still returns None / PASS)
    link_idx = next(
        i for i, e in enumerate(payload["audit_chain"]) if e["op"] == "link"
    )
    payload["audit_chain"][link_idx]["phi_egress"] = True
    state.write_text(json.dumps(payload), encoding="utf-8")

    res = runner.invoke(app, ["audit", "--verify", str(state)])
    assert res.exit_code == 1, res.stdout
    assert "FAIL" in res.stdout
    assert "phi_egress" in res.stdout
    assert "invariant" in res.stdout


def test_cli_ingest_missing_folder_clean_error(tmp_path, monkeypatch):
    """v0.8.0 fix-ingest-bad-folder-traceback — a missing/invalid folder is
    the most common first-run error (the README quickstart's first argument);
    it must exit 1 with a clean message, not a raw NotADirectoryError
    traceback, mirroring every other user-input failure in this CLI."""
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["ingest", str(tmp_path / "nope")])
    assert res.exit_code == 1
    assert "not a directory" in res.stdout
    # a clean typer.Exit (SystemExit), not the unhandled NotADirectoryError
    # that previously propagated out of ingest_folder
    assert not isinstance(res.exception, NotADirectoryError)


def test_cli_state_dir_config_roundtrip(tmp_path, monkeypatch):
    """v0.8.0 fix-state-dir-config-ignored-on-read — ingest honors the
    clinrec.toml state_dir; timeline and audit must read the state from the
    SAME configured directory instead of the hardcoded ./.clinrec default
    (previously both readers exited 1 with a false 'No state found')."""
    monkeypatch.chdir(tmp_path)
    # a dead loopback port keeps the linker deterministically rule-based
    (tmp_path / "clinrec.toml").write_text(
        'ollama_host = "http://127.0.0.1:1"\n'
        'ollama_model = "llama3.1:8b-instruct"\n'
        'state_dir = "custom-state"\n',
        encoding="utf-8",
    )
    sample = tmp_path / "records"
    sample.mkdir()
    _write_sample(sample)

    res = runner.invoke(app, ["ingest", str(sample)])
    assert res.exit_code == 0, res.stdout
    assert (tmp_path / "custom-state" / "state.json").exists()
    assert not (tmp_path / ".clinrec").exists()  # state went to the config dir

    res = runner.invoke(app, ["timeline", "--json"])
    assert res.exit_code == 0, res.output
    events = json.loads(res.stdout)
    assert len(events) > 0
    assert not (tmp_path / ".clinrec").exists()  # reader did not fall back

    res = runner.invoke(app, ["audit", "--export", str(tmp_path / "audit.jsonl")])
    assert res.exit_code == 0, res.output
    lines = (tmp_path / "audit.jsonl").read_text().strip().splitlines()
    assert len(lines) > 0


def test_cli_audit_verify_malformed_state_clean_error(tmp_path, monkeypatch):
    """v0.9.0 fix-audit-verify-malformed-state-traceback — a structurally
    corrupt (yet valid-JSON) state file must exit 1 with a clean FAIL naming
    the path, not leak the raw KeyError/TypeError/ValidationError traceback
    that AuditChain.from_state raises for malformed rows. --verify is the
    regulator's attestation surface; a corrupt chain is exactly what it must
    report cleanly, mirroring every other user-input failure in this CLI."""
    monkeypatch.chdir(tmp_path)
    malformed_files = [
        # a row missing audit_id (the reproduced KeyError)
        '{"audit_chain": [{"op": "ingest", "input_sha256": "abc"}]}',
        # audit_chain not a list
        '{"audit_chain": "not-a-list"}',
        # wrong-typed field (pydantic ValidationError)
        '{"audit_chain": [{"audit_id": "a1", "op": "ingest", "input_sha256": 123}]}',
    ]
    for i, payload in enumerate(malformed_files):
        bad = tmp_path / f"bad{i}.json"
        bad.write_text(payload, encoding="utf-8")
        res = runner.invoke(app, ["audit", "--verify", str(bad)])
        assert res.exit_code == 1, res.stdout
        assert "could not parse state file" in res.stdout
        assert "Traceback" not in res.output


def test_cli_eval_missing_gold_clean_error(tmp_path, monkeypatch):
    """v0.9.0 fix-eval-gold-path-silent-fallback — an explicitly requested
    gold file that does not exist is a clean CLI error (exit 1), not a silent
    exit-0 report scoring the embedded gold set with no indication that the
    requested file was never read."""
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["eval", "--gold", "missing-gold.jsonl"])
    assert res.exit_code == 1
    assert "gold file not found" in res.stdout
    # no eval report is printed over the wrong (embedded) set
    assert "ClinRec Eval" not in res.stdout
    assert "Traceback" not in res.output

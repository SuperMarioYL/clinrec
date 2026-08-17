"""m2: per-op audit chain + no-PHI-egress invariant."""
from __future__ import annotations

import json
from pathlib import Path

from clinrec.audit import AuditChain, OP_ASSEMBLE, OP_INGEST, OP_LINK, sha256_text
from clinrec.models import AuditEntry


def test_audit_chain_record_appends_entry():
    chain = AuditChain()
    e = chain.record(op=OP_INGEST, input_sha256="a" * 64, output_sha256="b" * 64)
    assert isinstance(e, AuditEntry)
    assert e.op == OP_INGEST
    assert e.phi_egress is False
    assert len(chain) == 1


def test_audit_chain_phi_egress_invariant_always_false_by_default():
    chain = AuditChain()
    chain.record(op=OP_LINK, input_sha256="x" * 64)
    chain.record(op=OP_ASSEMBLE, input_sha256="y" * 64)
    assert chain.verify_invariant() is True


def test_audit_chain_record_io_hashes_strings():
    chain = AuditChain()
    e = chain.record_io(op=OP_INGEST, input_text="raw fax text", output_text="ocr'd text")
    assert e.input_sha256 == sha256_text("raw fax text")
    assert e.output_sha256 == sha256_text("ocr'd text")


def test_audit_chain_export_jsonl(tmp_path):
    chain = AuditChain()
    chain.record(op=OP_INGEST, input_sha256="a" * 64, output_sha256="b" * 64)
    chain.record(op=OP_LINK, input_sha256="c" * 64, llm_model_id="llama3.1:8b-instruct", prompt_sha256="p" * 64, output_sha256="d" * 64)
    out = chain.export(tmp_path / "audit.jsonl")
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        row = json.loads(line)
        assert {"audit_id", "op", "input_sha256", "llm_model_id", "prompt_sha256", "output_sha256", "ts", "phi_egress"} <= set(row)
        assert row["phi_egress"] is False


def test_audit_chain_state_roundtrip():
    chain = AuditChain()
    chain.record(op=OP_INGEST, input_sha256="a" * 64)
    chain.record(op=OP_LINK, input_sha256="b" * 64, llm_model_id="llama3.1:8b-instruct")
    state = chain.to_state()
    restored = AuditChain.from_state(state)
    assert len(restored) == 2
    assert [e.op for e in restored] == [OP_INGEST, OP_LINK]
    assert restored.verify_invariant() is True


def test_audit_chain_last_for_op():
    chain = AuditChain()
    chain.record(op=OP_INGEST, input_sha256="a" * 64)
    chain.record(op=OP_LINK, input_sha256="b" * 64)
    chain.record(op=OP_INGEST, input_sha256="c" * 64)
    last = chain.last_for_op(OP_INGEST)
    assert last is not None
    assert last.input_sha256 == "c" * 64


def test_audit_entries_are_immutable():
    """Frozen pydantic models — a regulator can replay without tampering."""
    chain = AuditChain()
    e = chain.record(op=OP_INGEST, input_sha256="a" * 64)
    try:
        e.op = "tampered"  # type: ignore[misc]
        assert False, "should be frozen"
    except Exception:
        pass


def test_chain_hash_detects_prompt_sha_and_ts_tamper():
    """v0.5.0 fix-chain-hash-omits-prompt-sha — the chain hash now covers
    prompt_sha256 (the regulator-critical field recording which LLM prompt
    was fed) and ts, so mutating a link-op entry's prompt hash or timestamp
    in a saved chain breaks verify_chain_integrity. Previously the digest
    omitted both, so such tampering left the stored chain_hash valid — a
    direct break of the tamper-evidence claim.
    """
    chain = AuditChain()
    chain.record(op=OP_INGEST, input_sha256="a" * 64, output_sha256="b" * 64)
    chain.record(
        op=OP_LINK,
        input_sha256="c" * 64,
        llm_model_id="llama3.1:8b-instruct",
        prompt_sha256="p" * 64,
        output_sha256="d" * 64,
    )
    state = chain.to_state()
    # sanity: the clean chain verifies
    assert AuditChain.from_state(state).verify_chain_integrity() is True

    # mutate the link op's prompt_sha256 in the saved state, reload, verify
    tampered_prompt = [dict(row) for row in state]
    tampered_prompt[1]["prompt_sha256"] = "x" * 64
    restored = AuditChain.from_state(tampered_prompt)
    assert restored.verify_chain_integrity() is False
    broken = restored.first_broken_link()
    assert broken is not None and broken[0] == 1 and broken[1].op == OP_LINK

    # mutating the timestamp also breaks the chain
    tampered_ts = [dict(row) for row in state]
    tampered_ts[1]["ts"] = "2099-01-01T00:00:00+00:00"
    assert AuditChain.from_state(tampered_ts).verify_chain_integrity() is False

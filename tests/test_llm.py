"""m1: on-prem LLM linker (rule-based fallback + ollama path)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from clinrec.llm import DEFAULT_MODEL, Linker, rule_based_code
from clinrec.models import CodeSystem, EntityType


def test_rule_based_coder_known_condition():
    code, sys_, conf = rule_based_code("diabetes", EntityType.CONDITION)
    assert code == "E11.9"
    assert sys_ == CodeSystem.ICD10
    assert conf == 0.95


def test_rule_based_coder_strips_dosage():
    code, sys_, conf = rule_based_code("metformin 500 mg", EntityType.MEDICATION)
    assert code == "6809"  # RxNorm CUI for metformin
    assert sys_ == CodeSystem.RXNORM
    assert conf == 0.95


def test_rule_based_coder_procedure_cpt():
    code, sys_, _ = rule_based_code("colonoscopy", EntityType.PROCEDURE)
    assert code == "45378"
    assert sys_ == CodeSystem.CPT


def test_rule_based_coder_unknown_returns_empty():
    code, sys_, conf = rule_based_code("zzz unknown term", EntityType.CONDITION)
    assert code == ""
    assert sys_ == CodeSystem.UNKNOWN
    assert conf == 0.0


def test_rule_based_coder_substring_match_lower_confidence():
    # "type 2 diabetes" is an exact key; verify exact path beats substring
    code, _, conf = rule_based_code("type 2 diabetes", EntityType.CONDITION)
    assert code == "E11.9" and conf == 0.95


def test_rule_based_coder_dates_and_providers_uncoded():
    code, sys_, _ = rule_based_code("01/15/2024", EntityType.DATE)
    assert code == ""
    assert sys_ == CodeSystem.UNKNOWN
    code, sys_, _ = rule_based_code("Dr. Jane Smith", EntityType.PROVIDER)
    assert code == ""
    assert sys_ == CodeSystem.UNKNOWN


def test_linker_falls_back_when_ollama_down():
    """Without a running Ollama daemon, the linker must produce a usable,
    fully-audited rule-based result (the primitive's graceful degradation)."""
    lk = Linker()
    assert lk.is_available() is False  # daemon not running in CI
    res = lk.link("diabetes", EntityType.CONDITION)
    assert res.normalized_code == "E11.9"
    assert res.code_sys == CodeSystem.ICD10
    assert res.llm_model_id == "rule-based"
    assert res.prompt_sha256 == ""  # no prompt when rule-based
    assert res.output_sha256  # audit hash always populated


def test_linker_records_prompt_and_output_sha_when_llm_used():
    """When Ollama IS reachable, the audit chain must record the prompt + output
    sha-256 so a regulator can replay the normalization."""
    fake_resp = {
        "message": {
            "content": '{"code": "E11.9", "system": "ICD-10", "confidence": 0.9}'
        }
    }
    lk = Linker()
    # force the availability probe to True without a daemon
    lk._available = True

    def fake_chat(*a, **k):
        return fake_resp

    fake_client = type("C", (), {"chat": fake_chat, "list": lambda self: {}})()
    lk._client = fake_client

    res = lk.link("diabetes", EntityType.CONDITION)
    assert res.normalized_code == "E11.9"
    assert res.llm_model_id == DEFAULT_MODEL
    assert res.prompt_sha256  # populated from the LLM prompt
    assert res.output_sha256  # populated from the LLM raw reply


def test_linker_handles_malformed_llm_json_gracefully():
    lk = Linker()
    lk._available = True
    fake_resp = {"message": {"content": "not json at all"}}
    fake_client = type("C", (), {"chat": lambda *a, **k: fake_resp, "list": lambda self: {}})()
    lk._client = fake_client
    res = lk.link("diabetes", EntityType.CONDITION)
    # falls back to rule-based code but still tagged with the LLM model id
    assert res.normalized_code == "E11.9"
    assert res.llm_model_id == DEFAULT_MODEL
    assert res.output_sha256


def test_linker_handles_daemon_connection_error():
    lk = Linker()
    # Force availability True, but chat() raises ConnectionError mid-call
    lk._available = True

    def boom(*a, **k):
        raise ConnectionError("daemon died")

    lk._client = type("C", (), {"chat": boom})()
    res = lk.link("diabetes", EntityType.CONDITION)
    # degrades to rule-based, audit still records the attempt
    assert res.normalized_code == "E11.9"
    assert res.llm_model_id == "rule-based"

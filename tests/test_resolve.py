"""m1: clinical NER (medspaCy path + regex fallback)."""
from __future__ import annotations

import pytest

from clinrec.models import EntityType
from clinrec.resolve import EntityExtractor, _trim_provider_span


SAMPLE = (
    "Patient seen 01/15/2024. Dr. Jane Smith noted diabetes. "
    "Started metformin 500 mg. denies chest pain."
)


def _labels(ents):
    return {e.entity_type for e in ents}


def test_extractor_picks_up_all_entity_types():
    ex = EntityExtractor()
    ents = ex.extract(SAMPLE, "rec1")
    labels = _labels(ents)
    assert EntityType.CONDITION in labels
    assert EntityType.MEDICATION in labels
    assert EntityType.DATE in labels
    assert EntityType.PROVIDER in labels


def test_extractor_returns_negation_for_denies():
    ex = EntityExtractor()
    ents = ex.extract("Patient denies hypertension. Has diabetes.", "rec1")
    negated = [e for e in ents if e.is_negated]
    active = [e for e in ents if not e.is_negated]
    assert any(e.text_span.lower() == "hypertension" for e in negated), "hypertension should be negated"
    assert any(e.text_span.lower() == "diabetes" for e in active), "diabetes should be active"


def test_negation_does_not_cross_sentence_boundary():
    # v0.4.0 fix-negation-scope-cross-sentence: a negation cue in one sentence
    # must not leak across the sentence terminator into a later sentence.
    ex = EntityExtractor()
    ents = ex.extract("Patient denies hypertension. Has diabetes. Denies asthma.", "rec1")
    by_span = {e.text_span.lower(): e for e in ents}
    assert by_span["hypertension"].is_negated, "hypertension shares a sentence with 'denies'"
    assert not by_span["diabetes"].is_negated, "diabetes is in a later sentence; must NOT be negated"
    assert by_span["asthma"].is_negated, "asthma shares its own sentence with 'denies'"


def test_extractor_empty_text_returns_empty():
    ex = EntityExtractor()
    assert ex.extract("", "rec1") == []
    assert ex.extract("   \n  ", "rec1") == []


def test_extractor_assigns_source_record_id():
    ex = EntityExtractor()
    ents = ex.extract("diabetes", "rec_abc")
    assert all(e.source_record_id == "rec_abc" for e in ents)


def test_provider_span_does_not_swallow_sentence_verb():
    """medspaCy compiles patterns with re.IGNORECASE; provider spans must be
    trimmed so they do not grab trailing lowercase verbs like 'noted'."""
    text = "Dr. Jane Smith noted diabetes."
    start, end, span = _trim_provider_span(text, 4, 25)  # "Jane Smith noted"
    # 'noted' is lowercase-initial + a stopword → trimmed
    assert "noted" not in span.lower()


def test_extractor_provider_span_is_just_the_name():
    ex = EntityExtractor()
    ents = ex.extract("Dr. Jane Smith noted diabetes.", "rec1")
    providers = [e for e in ents if e.entity_type == EntityType.PROVIDER]
    assert providers, "expected a provider entity"
    for p in providers:
        assert "noted" not in p.text_span.lower()
        assert p.text_span.startswith("Dr")


def test_extractor_handles_multiple_meds_and_conditions():
    ex = EntityExtractor()
    ents = ex.extract(
        "diabetes, hypertension and atrial fibrillation. "
        "metformin, lisinopril and warfarin.",
        "rec1",
    )
    types = _labels(ents)
    assert EntityType.CONDITION in types
    assert EntityType.MEDICATION in types


def test_extractor_is_deterministic_across_runs():
    ex = EntityExtractor()
    a = ex.extract(SAMPLE, "rec1")
    b = ex.extract(SAMPLE, "rec1")
    assert [(e.entity_type, e.text_span, e.start, e.end) for e in a] == [
        (e.entity_type, e.text_span, e.start, e.end) for e in b
    ]

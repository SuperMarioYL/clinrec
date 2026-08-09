"""Clinical NER (m1) — medspaCy target matcher with a rule-based fallback.

medspaCy owns clinical NER; the on-prem LLM linker (``llm.py``) is kept
narrow (entity normalization, not span detection). This module extracts
``RawEntity`` spans of five types — CONDITION, MEDICATION, PROCEDURE,
DATE, PROVIDER — and carries medspaCy context flags (negation / historical
/ hypothetical) so the timeline can mark a span ``negated`` or ``resolved``
rather than treating every mention as active.

If medspaCy cannot be imported (a stripped-down install), the same rule set
runs through a plain-regex fallback so the CLI still produces a usable
timeline. The real clinical path is medspaCy.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Optional

from .models import EntityType, RawEntity

log = logging.getLogger("clinrec.resolve")

# ---------------------------------------------------------------------------
# Clinical rule set (shared by the medspaCy path and the regex fallback).
# Literals are matched case-insensitively; patterns are raw regex anchored
# by medspaCy's TargetRule (and by re in the fallback). Keep this curated —
# the eval harness scores precision/recall against a gold set, so a noisy
# rule set hurts the on-prem baseline bar.
# ---------------------------------------------------------------------------

_CONDITIONS = [
    "diabetes", "diabetes mellitus", "type 2 diabetes", "type 1 diabetes",
    "hypertension", "essential hypertension", "high blood pressure",
    "hyperlipidemia", "high cholesterol", "atrial fibrillation",
    "coronary artery disease", "myocardial infarction", "heart attack",
    "congestive heart failure", "chronic kidney disease", "copd",
    "chronic obstructive pulmonary disease", "asthma", "pneumonia",
    "depression", "anxiety", "hypothyroidism", "hyperthyroidism",
    "stroke", "cerebrovascular accident", "tia", "transient ischemic attack",
    "anemia", "gout", "gerd", "gastroesophageal reflux disease",
    "osteoarthritis", "rheumatoid arthritis", "migraine", "epilepsy",
    "seizure", "seizures", "dementia", "alzheimer", "cirrhosis",
    "hepatitis c", "hepatitis b", "obesity", "sleep apnea",
    "peripheral vascular disease", "deep vein thrombosis", "pulmonary embolism",
    "hypotension", "hyponatremia", "hypokalemia", "hyperkalemia",
]

_MEDICATIONS = [
    "metformin", "glipizide", "insulin", "liraglutide", "semaglutide",
    "lisinopril", "enalapril", "ramipril", "losartan", "valsartan",
    "olmesartan", "amlodipine", "nifedipine", "atorvastatin", "simvastatin",
    "rosuvastatin", "pravastatin", "metoprolol", "carvedilol", "atenolol",
    "bisoprolol", "aspirin", "clopidogrel", "warfarin", "apixaban",
    "rivaroxaban", "heparin", "furosemide", "spironolactone",
    "hydrochlorothiazide", "levothyroxine", "omeprazole", "pantoprazole",
    "gabapentin", "sertraline", "fluoxetine", "citalopram", "escitalopram",
    "albuterol", "montelukast", "fluticasone", "prednisone",
    "hydrocortisone", "amoxicillin", "azithromycin", "ciprofloxacin",
    "doxycycline", "trimethoprim", "sulfamethoxazole", "metronidazole",
    "acetaminophen", "ibuprofen", "naproxen", "morphine", "hydromorphone",
    "tramadol", "insulin glargine", "insulin lispro",
]

_PROCEDURES = [
    "colonoscopy", "mammogram", "mammography", "echocardiogram",
    "cardiac catheterization", "coronary angiography", "stress test",
    "appendectomy", "cholecystectomy", "cesarean section", "knee replacement",
    "hip replacement", "endoscopy", "upper endoscopy", "egd",
    "bronchoscopy", "biopsy", "cat scan", "ct scan", "mri",
    "ultrasound", "x-ray", "xray", "ekg", "ecg", "electrocardiogram",
    "dialysis", "hemodialysis", "laparoscopy", "arthroscopy",
    "carotid endarterectomy", "cabg", "coronary artery bypass",
    "angioplasty", "stent placement", "transfusion",
]

# Regex patterns (medspaCy TargetRule `pattern` / fallback `re`).
_DATE_PATTERNS = [
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b",                      # 01/15/2024
    r"\b\d{1,2}-\d{1,2}-\d{2,4}\b",                      # 01-15-2024
    r"\b\d{4}-\d{1,2}-\d{1,2}\b",                        # 2024-01-15
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4}\b",
    r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
    r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b",
]

_PROVIDER_PATTERNS = [
    r"\bDr\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}\b",   # Dr. Jane Smith
    r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+,\s*(?:MD|DO|NP|PA|RN|FACP)\b",
    r"\bDr\.?\s+[A-Z][a-z]+\s+[A-Z][a-z]+\b",
]

# Negation cues used by the fallback (medspaCy's context component is the
# real source; this mirrors the common cases for the no-medspacy path).
_NEGATION_CUES = (
    "no ", "denies", "denied", "denying", "negative for", "without",
    "absence of", "not ", "rule out", "ruled out", "no evidence of",
    "free of", "no signs of",
)
_HISTORICAL_CUES = (
    "history of", "h/o", "past", "previously", "prior ", "remote",
)


def _build_rules() -> list[tuple[str, EntityType, Optional[str]]]:
    """Return (literal, type, pattern) tuples for both NER paths."""
    rules: list[tuple[str, EntityType, Optional[str]]] = []
    for c in _CONDITIONS:
        rules.append((c, EntityType.CONDITION, rf"(?i)\b{re.escape(c)}\b"))
    for m in _MEDICATIONS:
        rules.append((m, EntityType.MEDICATION, rf"(?i)\b{re.escape(m)}\b"))
    for p in _PROCEDURES:
        rules.append((p, EntityType.PROCEDURE, rf"(?i)\b{re.escape(p)}\b"))
    for pat in _DATE_PATTERNS:
        rules.append(("date", EntityType.DATE, pat))
    for pat in _PROVIDER_PATTERNS:
        rules.append(("provider", EntityType.PROVIDER, pat))
    return rules


# ---------------------------------------------------------------------------
# Negation / historical detection (used by the fallback path and to backfill
# on medspaCy docs that lack the context component).
# ---------------------------------------------------------------------------


def _span_negated(text: str, span_start: int, span_end: int) -> bool:
    window = text[max(0, span_start - 40):span_start].lower()
    return any(cue in window for cue in _NEGATION_CUES)


def _span_historical(text: str, span_start: int, span_end: int) -> bool:
    window = text[max(0, span_start - 40):span_start].lower()
    return any(cue in window for cue in _HISTORICAL_CUES)


# ---------------------------------------------------------------------------
# medspaCy pipeline (loaded once, cached). Suppresses PyRuSH's verbose
# loguru DEBUG output so the CLI/tests stay readable.
# ---------------------------------------------------------------------------


def _suppress_medspacy_log() -> None:
    try:
        from loguru import logger  # type: ignore

        logger.disable("PyRuSH")
        logger.disable("medspacy")
    except Exception:  # noqa: BLE001 — best-effort log silencing
        pass
    logging.getLogger("medspacy").setLevel(logging.WARNING)
    logging.getLogger("PyRuSH").setLevel(logging.WARNING)


@lru_cache(maxsize=1)
def _load_medspacy():
    """Load (and cache) the medspaCy pipeline with the clinical rule set.

    Returns ``(nlp, available)``. ``available=False`` triggers the fallback.
    """
    try:
        _suppress_medspacy_log()
        import medspacy
        from medspacy.ner import TargetRule
    except Exception as exc:  # noqa: BLE001 — graceful fallback
        log.info("medspaCy unavailable, using regex NER fallback (%s)", exc)
        return None, False

    nlp = medspacy.load()
    tm = nlp.get_pipe("medspacy_target_matcher") if "medspacy_target_matcher" in nlp.pipe_names else None
    if tm is None:  # pragma: no cover — defensive; medspacy.load always adds it
        from medspacy.ner import TargetMatcher

        tm = nlp.add_pipe("medspacy_target_matcher")
    rules = [
        TargetRule(literal=lit, category=et.value, pattern=pat)
        if pat is not None
        else TargetRule(literal=lit, category=et.value)
        for lit, et, pat in _build_rules()
    ]
    tm.add(rules)
    return nlp, True


# Trailing words that are never part of a provider name. medspaCy compiles
# TargetRule patterns with re.IGNORECASE, so `[A-Z][a-z]+` over-matches
# lowercase sentence verbs ("Dr. Jane Smith noted"); we trim them in
# post-processing rather than fight the flag inside the regex.
_PROVIDER_STOPWORDS = {
    "noted", "is", "was", "were", "reports", "reported", "saw", "sees", "seen",
    "diagnosed", "started", "presents", "presented", "complains", "complained",
    "denies", "denied", "has", "had", "have", "the", "a", "an", "with", "to",
    "for", "of", "and", "in", "on", "at", "by", "from", "this", "that",
    "patient", "who", "will", "would", "should", "may", "might", "today",
    "yesterday", "tomorrow", "then", "now", "subsequently", "followed",
    "follow-up", "advised", "recommended", "ordered", "prescribed",
    "discharged", "admitted", "evaluated", "examined", "assessed", "found",
    "states", "stated", "describes", "described", "complains", "c/o",
}


def _trim_provider_span(text: str, start: int, end: int) -> tuple[int, int, str]:
    """Trim trailing tokens that aren't name parts (lowercase-initial or stopwords)."""
    span_text = text[start:end]
    tokens = span_text.split()
    if len(tokens) <= 1:
        return start, end, span_text
    while len(tokens) > 1:
        last = tokens[-1]
        last_word = last.rstrip(".,;:")
        # a name part must start with an uppercase letter and not be a stopword
        if last_word and last_word[0].isupper() and last_word.lower() not in _PROVIDER_STOPWORDS:
            break
        tokens.pop()
    # Recompute the end offset by walking the kept tokens forward over the
    # ORIGINAL whitespace. medspaCy spans often contain internal multi-space
    # (its `\s+` regex preserves them in ent.text); a single-space rejoin
    # (`new_end = start + len(" ".join(tokens))`) is shorter than the real
    # prefix, so text[start:new_end] cuts into the final name token — e.g.
    # "Dr.  Jane  Smith  noted" wrongly returns "Dr.  Jane  Smi". Each
    # split() token is a contiguous non-whitespace run, so skipping whitespace
    # then matching the token verbatim lands new_end on the true end of the
    # last kept name token, preserving the span text + offset persisted to
    # state.json for audit-replay fidelity.
    pos = start
    new_end = end  # conservative: if a token can't be verbatim-matched, don't trim past it
    for tok in tokens:
        while pos < end and text[pos].isspace():
            pos += 1
        if pos + len(tok) <= end and text[pos:pos + len(tok)] == tok:
            pos += len(tok)
            new_end = pos
        else:
            break
    return start, new_end, text[start:new_end]


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


class EntityExtractor:
    """Run clinical NER over a record's OCR text → ``RawEntity`` list.

    Negation/historical flags come from medspaCy's context component when
    available; otherwise from a windowed cue lookup in the fallback.
    """

    def __init__(self) -> None:
        self._nlp, self._medspacy = _load_medspacy()

    @property
    def uses_medspacy(self) -> bool:
        return self._medspacy

    def extract(self, text: str, source_record_id: str) -> list[RawEntity]:
        if not text or not text.strip():
            return []
        if self._medspacy and self._nlp is not None:
            return self._extract_medspacy(text, source_record_id)
        return self._extract_fallback(text, source_record_id)

    def _extract_medspacy(self, text: str, source_record_id: str) -> list[RawEntity]:
        doc = self._nlp(text)
        out: list[RawEntity] = []
        seen_spans: set[tuple[int, int]] = set()
        for ent in doc.ents:
            try:
                etype = EntityType(ent.label_)
            except ValueError:
                continue  # unknown label — skip rather than crash
            start, end = ent.start_char, ent.end_char
            span_text = ent.text
            is_negated = bool(getattr(ent._, "is_negated", False))
            is_hist = bool(getattr(ent._, "is_historical", False))
            # medspaCy compiles patterns with re.IGNORECASE → provider spans
            # over-grab lowercase sentence verbs; trim them back.
            if etype == EntityType.PROVIDER:
                start, end, span_text = _trim_provider_span(text, start, end)
            key = (start, end)
            if key in seen_spans:
                continue
            seen_spans.add(key)
            out.append(
                RawEntity(
                    entity_type=etype,
                    text_span=span_text,
                    start=start,
                    end=end,
                    is_negated=is_negated,
                    source_record_id=source_record_id,
                )
            )
            # historical flag is preserved by the context component; downstream
            # timeline marks such events 'resolved'.
            _ = is_hist
        return out

    def _extract_fallback(self, text: str, source_record_id: str) -> list[RawEntity]:
        matches: list[tuple[int, int, EntityType, str]] = []
        for _lit, etype, pat in _build_rules():
            if pat is None:
                continue
            for m in re.finditer(pat, text):
                matches.append((m.start(), m.end(), etype, m.group(0)))
        # sort by (start, -length) so the longest first span wins overlaps
        matches.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        kept: list[RawEntity] = []
        occupied: list[tuple[int, int]] = []
        for start, end, etype, span_text in matches:
            # skip if fully inside an already-kept (longer) span
            if any(s <= start and end <= e for s, e in occupied):
                continue
            occupied.append((start, end))
            kept.append(
                RawEntity(
                    entity_type=etype,
                    text_span=span_text,
                    start=start,
                    end=end,
                    is_negated=_span_negated(text, start, end),
                    source_record_id=source_record_id,
                )
            )
        return kept


def seen_spans(out: list[RawEntity]) -> list[tuple[int, int]]:
    """Back-compat helper — list of (start, end) for a RawEntity list."""
    return [(r.start, r.end) for r in out]


__all__ = ["EntityExtractor", "EntityType", "RawEntity"]

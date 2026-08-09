"""On-prem LLM entity linker (m1) — Ollama llama3.1:8b-instruct, zero cloud calls.

The genuinely new surface of the primitive: the on-prem linker normalizes
messy OCR spans (``metformin 500 mg``, ``T2DM``, ``Colonoscopy last year``)
to coded entities (ICD-10 / RxNorm / CPT / SNOMED-CT) **with no PHI ever
leaving the host**. medspaCy owns span detection; the LLM role stays narrow
(normalize → code), which de-risks the 8B-on-prem quality bar.

When the Ollama daemon is not running (a fresh `git clone` with no model
pulled), a deterministic rule-based coder takes over so the CLI still
produces a usable, fully-audited timeline — the same audit chain records
which path produced each code (``llm_model_id`` distinguishes them).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

from .models import CodeSystem, EntityType

log = logging.getLogger("clinrec.llm")

DEFAULT_MODEL = "llama3.1:8b-instruct"
DEFAULT_HOST = "http://127.0.0.1:11434"

# ---------------------------------------------------------------------------
# Rule-based coder (the fallback + the medspaCy-only baseline the eval
# harness scores against). Curated mappings to common clinical codes.
# ---------------------------------------------------------------------------

_ICD10 = {
    # endocrine
    "diabetes": "E11.9", "diabetes mellitus": "E11.9",
    "type 2 diabetes": "E11.9", "type 1 diabetes": "E10.9",
    "hypothyroidism": "E03.9", "hyperthyroidism": "E05.90",
    "hyperlipidemia": "E78.5", "high cholesterol": "E78.5",
    "obesity": "E66.01",
    # circulatory
    "hypertension": "I10", "essential hypertension": "I10",
    "high blood pressure": "I10",
    "atrial fibrillation": "I48.91", "coronary artery disease": "I25.10",
    "myocardial infarction": "I21.9", "heart attack": "I21.9",
    "congestive heart failure": "I50.9",
    "stroke": "I63.9", "cerebrovascular accident": "I63.9",
    "tia": "G45.9", "transient ischemic attack": "G45.9",
    "hypotension": "I95.9", "peripheral vascular disease": "I73.9",
    "deep vein thrombosis": "I80.2", "pulmonary embolism": "I26.99",
    # respiratory
    "copd": "J44.9", "chronic obstructive pulmonary disease": "J44.9",
    "asthma": "J45.909", "pneumonia": "J18.9", "sleep apnea": "G47.30",
    # digestive
    "gerd": "K21.9", "gastroesophageal reflux disease": "K21.9",
    "cirrhosis": "K74.60", "hepatitis c": "B19.21", "hepatitis b": "B19.1",
    # renal/genitourinary
    "chronic kidney disease": "N18.9",
    # mental
    "depression": "F32.A", "anxiety": "F41.1",
    # neuro
    "migraine": "G43.909", "epilepsy": "G40.909",
    "seizure": "R56.9", "seizures": "R56.9",
    "dementia": "F03.90", "alzheimer": "G30.9",
    # musculoskeletal/blood
    "osteoarthritis": "M19.90", "rheumatoid arthritis": "M06.9",
    "gout": "M10.9", "anemia": "D64.9",
    # metabolic
    "hyponatremia": "E87.1", "hypokalemia": "E87.6", "hyperkalemia": "E87.5",
}

_RXNORM = {
    # antidiabetics
    "metformin": "6809", "glipizide": "4622", "insulin": "253182",
    "insulin glargine": "253182", "insulin lispro": "253145",
    "liraglutide": "86009", "semaglutide": "206832",
    # acei / arb / ccb
    "lisinopril": "29046", "enalapril": "351131", "ramipril": "74905",
    "losartan": "83813", "valsartan": "84062",
    "olmesartan": "84055", "amlodipine": "89653", "nifedipine": "7577",
    # statins
    "atorvastatin": "83367", "simvastatin": "36567", "rosuvastatin": "857605",
    "pravastatin": "36708",
    # beta-blockers
    "metoprolol": "8948", "carvedilol": "73547", "atenolol": "704",
    "bisoprolol": "1587",
    # antiplatelet/anticoag
    "aspirin": "1191", "clopidogrel": "32968", "warfarin": "11289",
    "apixaban": "1247803", "rivaroxaban": "1303283", "heparin": "1247483",
    # diuretics
    "furosemide": "4604", "spironolactone": "9994",
    "hydrochlorothiazide": "5487",
    # thyroid
    "levothyroxine": "1153403",
    # gi
    "omeprazole": "7646", "pantoprazole": "407110",
    # neuro/psych
    "gabapentin": "2554", "sertraline": "36437", "fluoxetine": "4493",
    "citalopram": "2670", "escitalopram": "1247434",
    # respiratory
    "albuterol": "1130", "montelukast": "115199", "fluticasone": "25278",
    # steroids
    "prednisone": "8640", "hydrocortisone": "5456",
    # antibiotics
    "amoxicillin": "723", "azithromycin": "18631", "ciprofloxacin": "182231",
    "doxycycline": "3639", "metronidazole": "6744",
    # analgesics
    "acetaminophen": "161", "ibuprofen": "5640", "naproxen": "7260",
    "morphine": "7352", "hydromorphone": "3423", "tramadol": "10689",
    # trimethoprim/sulfa
    "trimethoprim": "10685", "sulfamethoxazole": "4458",
}

_CPT = {
    "colonoscopy": "45378", "mammogram": "77067", "mammography": "77067",
    "echocardiogram": "93306", "cardiac catheterization": "93458",
    "coronary angiography": "93458", "stress test": "93015",
    "appendectomy": "44970", "cholecystectomy": "47562",
    "cesarean section": "59510", "knee replacement": "27447",
    "hip replacement": "27130", "upper endoscopy": "43239", "egd": "43239",
    "endoscopy": "43239", "bronchoscopy": "31622", "biopsy": "11102",
    "ct scan": "71260", "cat scan": "71260", "mri": "70551",
    "ultrasound": "76700", "x-ray": "71046", "xray": "71046",
    "ekg": "93000", "ecg": "93000", "electrocardiogram": "93000",
    "dialysis": "90935", "hemodialysis": "90935", "laparoscopy": "49320",
    "arthroscopy": "29881", "carotid endarterectomy": "37916",
    "cabg": "33533", "coronary artery bypass": "33533",
    "angioplasty": "92928", "stent placement": "92928", "transfusion": "36430",
}


def _normalize_span(span: str) -> str:
    s = span.lower().strip()
    s = re.sub(r"\s+\d+(\.\d+)?\s*mg.*$", "", s)  # strip dosages
    s = re.sub(r"\s+\d+\s*$", "", s)
    s = re.sub(r"\s+(bid|tid|qd|qhs|prn|po|iv|sc|im)\b.*$", "", s)
    return s.strip()


def rule_based_code(span: str, entity_type: EntityType) -> tuple[str, CodeSystem, float]:
    """Deterministic code lookup. Returns (code, system, confidence).

    Exact key match → 0.95; substring/key-in-span → 0.70; no match → ("", UNKNOWN, 0.0).
    """
    key = _normalize_span(span)
    table: dict[str, str]
    if entity_type == EntityType.CONDITION:
        table = _ICD10
        sys_ = CodeSystem.ICD10
    elif entity_type == EntityType.MEDICATION:
        table = _RXNORM
        sys_ = CodeSystem.RXNORM
    elif entity_type == EntityType.PROCEDURE:
        table = _CPT
        sys_ = CodeSystem.CPT
    else:
        return "", CodeSystem.UNKNOWN, 0.0

    if key in table:
        return table[key], sys_, 0.95
    # substring match: any table key appears inside the (normalized) span
    for k, code in table.items():
        if k and (k in key or key in k):
            return code, sys_, 0.70
    return "", CodeSystem.UNKNOWN, 0.0


# ---------------------------------------------------------------------------
# LLM linker (Ollama). Zero cloud calls; the audit chain records the model
# id + prompt/output sha-256 so a regulator can replay every normalization.
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = (
    "You are a clinical entity linker running fully on-prem. "
    "Map the following clinical mention to its canonical medical code.\n\n"
    "Mention: {span}\n"
    "Entity type: {etype}\n\n"
    "Respond with ONLY a JSON object (no prose) with keys:\n"
    '  "code": the code string (e.g. "E11.9", "6809", "45378") or "" if unsure,\n'
    '  "system": one of "ICD-10", "RxNorm", "CPT", "SNOMED-CT",\n'
    '  "confidence": a float in [0.0, 1.0].\n'
    "Mapping guide: conditions→ICD-10, medications→RxNorm, procedures→CPT, "
    "general clinical concepts→SNOMED-CT. Dates and providers have no code; "
    'return code="" and system="SNOMED-CT" for those.'
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse_llm_json(raw: str) -> Optional[dict]:
    """Robustly pull the first {...} JSON object out of an LLM reply."""
    if not raw:
        return None
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(raw)):
        if raw[i] == "{":
            depth += 1
        elif raw[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


@dataclass
class LinkResult:
    normalized_code: str
    code_sys: CodeSystem
    confidence: float
    llm_model_id: str
    prompt_sha256: str
    output_sha256: str


class Linker:
    """On-prem entity linker. Falls back to ``rule_based_code`` if Ollama is down."""

    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST) -> None:
        self.model = model
        self.host = host
        self._client = None
        self._available: Optional[bool] = None

    def _get_client(self):
        if self._client is None:
            import ollama

            self._client = ollama.Client(host=self.host)
        return self._client

    def is_available(self) -> bool:
        """Probe Ollama once (cached). Returns True if reachable."""
        if self._available is not None:
            return self._available
        try:
            client = self._get_client()
            client.list()  # cheap round-trip; raises ConnectionError if down
            self._available = True
        except Exception as exc:  # noqa: BLE001 — daemon may be absent
            log.info("Ollama not reachable (%s); linker will use rule-based coder", exc)
            self._available = False
        return self._available

    def link(self, span: str, entity_type: EntityType) -> LinkResult:
        """Normalize one span to a coded entity (LLM, fallback rule-based)."""
        # rule-based always computes a baseline; we use it as the fallback and
        # (in eval mode) as the comparison baseline the m1 falsifier scores.
        rb_code, rb_sys, rb_conf = rule_based_code(span, entity_type)

        if entity_type in (EntityType.DATE, EntityType.PROVIDER):
            # no code system applies; record the raw span as its own "code"
            return LinkResult(
                normalized_code=span.strip(),
                code_sys=CodeSystem.UNKNOWN,
                confidence=0.0,
                llm_model_id="rule-based",
                prompt_sha256="",
                output_sha256=_sha(span),
            )

        if not self.is_available():
            return LinkResult(
                normalized_code=rb_code,
                code_sys=rb_sys,
                confidence=rb_conf,
                llm_model_id="rule-based",
                prompt_sha256="",
                output_sha256=_sha(f"{rb_code}|{rb_sys.value}|{rb_conf}"),
            )

        return self._link_llm(span, entity_type, rb_code, rb_sys, rb_conf)

    def _link_llm(
        self,
        span: str,
        entity_type: EntityType,
        rb_code: str,
        rb_sys: CodeSystem,
        rb_conf: float,
    ) -> LinkResult:
        prompt = _PROMPT_TEMPLATE.format(span=span, etype=entity_type.value)
        prompt_sha = _sha(prompt)
        try:
            client = self._get_client()
            resp = client.chat(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a precise clinical coder. Output only JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": 0.0},
            )
        except Exception as exc:  # noqa: BLE001 — model missing / daemon died
            log.warning("Ollama call failed (%s); falling back to rule-based", exc)
            return LinkResult(
                normalized_code=rb_code,
                code_sys=rb_sys,
                confidence=rb_conf,
                llm_model_id="rule-based",
                prompt_sha256=prompt_sha,
                output_sha256=_sha(f"err:{exc}"),
            )

        raw = (resp.get("message", {}).get("content", "") or "").strip()
        output_sha = _sha(raw)
        parsed = _parse_llm_json(raw)
        if not parsed:
            log.warning("could not parse LLM JSON: %r", raw[:120])
            return LinkResult(
                normalized_code=rb_code,
                code_sys=rb_sys,
                confidence=rb_conf,
                llm_model_id=self.model,
                prompt_sha256=prompt_sha,
                output_sha256=output_sha,
            )

        code = str(parsed.get("code", "")).strip()
        try:
            sys_ = CodeSystem(str(parsed.get("system", "")).strip())
        except ValueError:
            sys_ = rb_sys
        conf = float(parsed.get("confidence", 0.0) or 0.0)
        conf = max(0.0, min(1.0, conf))

        if not code:
            code = rb_code
            sys_ = rb_sys if sys_ == CodeSystem.UNKNOWN else sys_
            conf = max(conf, rb_conf)

        return LinkResult(
            normalized_code=code,
            code_sys=sys_,
            confidence=conf,
            llm_model_id=self.model,
            prompt_sha256=prompt_sha,
            output_sha256=output_sha,
        )


__all__ = ["DEFAULT_HOST", "DEFAULT_MODEL", "LinkResult", "Linker", "rule_based_code"]

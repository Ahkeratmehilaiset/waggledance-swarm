"""B.5 — Question-frame parser (FI/EN negation, threshold, unit detection).

Per v3 §1.8 + §1.18:
Embedding retrieval finds the relevant solver; the question-frame parser
finds the answer SHAPE. These are orthogonal concerns:

  "paljonko lämmitys maksaa"      → numeric
  "onko lämmityskustannus yli 50 €?" → boolean_comparison (op=">", 50, EUR)
  "älä laske sähkölämmitystä..."   → numeric with negation scope
  "miksi pumppu värisee?"          → diagnosis

Deterministic parser, no LLM. Regex + word lists for FI and EN.

Usage:
    from waggledance.core.reasoning.question_frame import parse
    frame = parse("onko lämmityskustannus yli 50 €?")
    # QuestionFrame(desired_output='boolean_comparison',
    #               comparator=Comparator(op='>', threshold=50.0, unit='EUR'),
    #               negation=Negation(present=False, scope=None))
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Optional


# ── Vocabulary ─────────────────────────────────────────────────────

NEGATION_WORDS_FI = [
    r"\b(?:ei|älä|ilman|paitsi|pois lukien|lukuunottamatta)\b",
]
NEGATION_WORDS_EN = [
    r"\b(?:not|no|without|except|excluding|don't|doesn't|isn't|aren't)\b",
]
NEGATION_PATTERN = re.compile(
    "|".join(NEGATION_WORDS_FI + NEGATION_WORDS_EN),
    re.IGNORECASE,
)

DIAGNOSIS_WORDS = re.compile(
    r"\b(?:miksi|mika\s+vika|vika|virhe|ongelma|korjaa|korjaus|"
    r"why|what's wrong|problem|fault|broken|diagnose|fix|repair)\b",
    re.IGNORECASE,
)

EXPLANATION_WORDS = re.compile(
    r"\b(?:kerro|selitä|selita|kuvaile|mita\s+tarkoittaa|miten toimii|"
    r"miten\s+\w+\s+toimi|"
    r"explain|describe|what does it mean|how does)\b",
    re.IGNORECASE,
)

OPTIMIZATION_WORDS = re.compile(
    r"\b(?:optimoi|minimi|maksimi|halvin|edullisin|paras|tehokkain|"
    r"optimize|minimum|maximum|cheapest|best|most efficient)\b",
    re.IGNORECASE,
)

# Boolean indicators — these trigger boolean_comparison ONLY when combined
# with a comparator or a numeric+unit threshold. "what is X" / "what does X"
# are not boolean questions.
BOOLEAN_INDICATORS = re.compile(
    r"\b(?:onko|onkö|oliko|ylittääkö|alittaako|tuleeko|exceed|"
    r"above|below|over|under|more than|less than)\b",
    re.IGNORECASE,
)


# Comparison operators — check in order (more specific first)
COMPARATORS = [
    (r"\b(?:vähintään|least|at least|>=|≥|ge)\b", ">="),
    (r"\b(?:enintään|most|at most|<=|≤|le)\b", "<="),
    (r"\b(?:yli|above|over|more than|greater than|>|gt)\b", ">"),
    (r"\b(?:alle|below|under|less than|<|lt)\b", "<"),
    (r"\b(?:yhtä suuri|equal to|equals|=|eq)\b", "="),
]


# Unit detection — match symbol, extract preceding number
UNIT_PATTERNS = [
    (r"(\d+(?:[.,]\d+)?)\s*€", "EUR"),
    (r"(\d+(?:[.,]\d+)?)\s*eur\b", "EUR"),
    (r"(\d+(?:[.,]\d+)?)\s*euro\w*\b", "EUR"),
    (r"(\d+(?:[.,]\d+)?)\s*\$", "USD"),
    (r"(\d+(?:[.,]\d+)?)\s*usd\b", "USD"),
    (r"(\d+(?:[.,]\d+)?)\s*kwh\b", "kWh"),
    (r"(\d+(?:[.,]\d+)?)\s*kw\b", "kW"),
    (r"(\d+(?:[.,]\d+)?)\s*wh\b", "Wh"),
    (r"(\d+(?:[.,]\d+)?)\s*w\b", "W"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:°c|celsius)\b", "C"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:°f|fahrenheit)\b", "F"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:kg|kilogram)\b", "kg"),
    (r"(\d+(?:[.,]\d+)?)\s*(?:m2|m²|neliö\w*)\b", "m2"),
    (r"(\d+(?:[.,]\d+)?)\s*%", "percent"),
]


# ── Dataclasses ────────────────────────────────────────────────────


@dataclass
class Comparator:
    op: str
    threshold: Optional[float] = None
    unit: Optional[str] = None


@dataclass
class Negation:
    present: bool
    scope: Optional[str] = None


@dataclass
class QuestionFrame:
    desired_output: str  # numeric | boolean_comparison | explanation | diagnosis | optimization
    comparator: Optional[Comparator] = None
    negation: Optional[Negation] = None
    raw_query: str = ""

    def to_dict(self) -> dict:
        d = {
            "desired_output": self.desired_output,
            "raw_query": self.raw_query,
        }
        if self.comparator:
            d["comparator"] = asdict(self.comparator)
        if self.negation:
            d["negation"] = asdict(self.negation)
        return d


# ── Parser ─────────────────────────────────────────────────────────


def _detect_comparator(query: str) -> Optional[Comparator]:
    """Find first comparator keyword and any adjacent numeric+unit."""
    for pattern, op in COMPARATORS:
        if re.search(pattern, query, re.IGNORECASE):
            # Try to find a threshold near it
            for unit_pat, unit_name in UNIT_PATTERNS:
                m = re.search(unit_pat, query, re.IGNORECASE)
                if m:
                    threshold = float(m.group(1).replace(",", "."))
                    return Comparator(op=op, threshold=threshold, unit=unit_name)
            # Comparator without unit — might still have a bare number
            num_m = re.search(r"\b(\d+(?:[.,]\d+)?)\b", query)
            if num_m:
                threshold = float(num_m.group(1).replace(",", "."))
                return Comparator(op=op, threshold=threshold, unit=None)
            return Comparator(op=op, threshold=None, unit=None)
    return None


def _detect_negation(query: str) -> Negation:
    m = NEGATION_PATTERN.search(query)
    if not m:
        return Negation(present=False, scope=None)
    # Scope: the word after the negation
    tail = query[m.end():].strip()
    first_word = tail.split(None, 1)[0] if tail else None
    return Negation(present=True, scope=first_word)


def _detect_desired_output(query: str, comparator: Optional[Comparator]) -> str:
    """Ranked detection: diagnosis, optimization, explanation, boolean, numeric.

    Key rules:
      - "selitä miten X toimii" → explanation (explanation wins over bare "miten")
      - Boolean requires comparator OR explicit BOOLEAN_INDICATORS match
      - Plain "what is / what does" is NOT boolean
    """
    # Diagnosis wins IF it's not just a neutral "miten" that would also hit explanation.
    # Check explanation first for patterns like "selitä miten X toimii".
    has_explanation = bool(EXPLANATION_WORDS.search(query))
    has_diagnosis = bool(DIAGNOSIS_WORDS.search(query))
    has_optimization = bool(OPTIMIZATION_WORDS.search(query))

    # Optimization AND diagnosis: diagnosis wins per test_diagnosis_overrides_optimization
    # Optimization AND explanation: optimization wins per test_optimization_overrides_explanation
    if has_diagnosis:
        return "diagnosis"
    if has_optimization:
        return "optimization"
    if has_explanation:
        return "explanation"
    if comparator is not None or BOOLEAN_INDICATORS.search(query):
        return "boolean_comparison"
    return "numeric"


def parse(query: str) -> QuestionFrame:
    """Parse a user query into a QuestionFrame."""
    import unicodedata
    q = unicodedata.normalize("NFC", query).strip()

    comparator = _detect_comparator(q)
    negation = _detect_negation(q)
    desired_output = _detect_desired_output(q, comparator)

    return QuestionFrame(
        desired_output=desired_output,
        comparator=comparator,
        negation=negation,
        raw_query=query,
    )

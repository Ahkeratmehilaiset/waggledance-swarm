"""
WaggleDance — Finnish Text Normalizer (Voikko-powered)
=======================================================
Single normalize_fi() function shared by HotCache, MicroModel V1, and YAML routing.

Uses Voikko lemmatization when available, falls back to suffix-stripping.
Bee-domain compound words expanded via configs/bee_terms.yaml.

Usage:
    from core.normalizer import normalize_fi
    key = normalize_fi("Miten käsittelen varroapudotusta?", sort_words=True)
    # → "käsitellä varroa pudotus"
"""
import logging
import os
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger("normalizer")

# ═══════════════════════════════════════════════════════════════
# MODULE-LEVEL SINGLETONS (initialized once on first import)
# ═══════════════════════════════════════════════════════════════

_voikko = None          # libvoikko.Voikko instance or None
_voikko_loaded = False  # True after init attempt (even if failed)
_bee_terms: dict[str, list[str]] = {}  # compound -> [parts]

# Finnish stopwords — common question/grammar words to remove
STOP_FI = {
    # Question words
    "mikä", "mitä", "miten", "milloin", "missä", "miksi", "kuka",
    "kenelle", "onko", "voiko", "saako", "mitkä", "mihin", "mistä",
    "kuinka", "paljonko", "montako",
    # Verbs / grammar
    "olla", "ole", "ovat", "on", "ei", "eivät",
    "että", "tämä", "tämän", "tätä", "se", "ne",
    "kun", "jos", "niin", "myös", "vain", "jo", "vielä",
    "ja", "tai", "mutta", "vai",
    # Generic action words
    "pitää", "pitäisi", "pitääkö", "kannattaa", "kannattaako",
    "voiko", "saako", "tehdä", "tehdään",
    # Pronouns / determiners
    "minä", "sinä", "hän", "me", "te", "he",
    "minun", "sinun", "hänen", "meidän", "teidän", "heidän",
    # Prepositions / adverbs
    "kanssa", "ilman", "ennen", "jälkeen", "aikana",
    "noin", "yli", "alle", "hyvin", "paljon", "vähän",
}

# Fallback suffix list (used when Voikko is unavailable)
_FI_SUFFIXES = [
    "kään", "kaan",
    "ssä", "ssa", "stä", "sta", "llä", "lla", "lle", "ltä", "lta",
    "kin", "kö", "ko",
    "an", "en", "in", "on", "un", "yn", "än", "ön",
]


def _init_voikko():
    """Initialize Voikko singleton. Called once on first normalize_fi() call.

    Mirrors translation_proxy.py VoikkoEngine.__init__ pattern exactly:
    setLibrarySearchPath() first (Windows DLL fix), then Voikko("fi", path=...).
    Only attempts paths that contain actual dictionary data (5/ subdir)
    to avoid creating broken Voikko objects whose __del__ crashes.
    """
    global _voikko, _voikko_loaded
    if _voikko_loaded:
        return
    _voikko_loaded = True

    search_paths = [
        os.environ.get("VOIKKO_DICTIONARY_PATH"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "voikko"),
        r"U:\project\voikko",
        r"C:\voikko",
        "/usr/lib/voikko",
        "/usr/share/voikko",
    ]

    for path in search_paths:
        if not path:
            continue
        p = Path(path).resolve()
        # Only try if path exists AND contains Voikko dictionary data (5/ subdir)
        if not p.exists() or not (p / "5").exists():
            continue
        try:
            import libvoikko
            # CRITICAL Windows fix: tell libvoikko where the DLL is
            libvoikko.Voikko.setLibrarySearchPath(str(p))
            _voikko = libvoikko.Voikko("fi", path=str(p))
            log.info(f"Normalizer Voikko loaded: {p}")
            return
        except Exception as e:
            log.debug(f"Normalizer Voikko init failed ({p}): {e}")
            _voikko = None

    log.info("Normalizer: Voikko unavailable, using suffix-stripping fallback")


def _init_bee_terms():
    """Load bee_terms from configs/bee_terms.yaml."""
    global _bee_terms
    if _bee_terms:
        return

    yaml_paths = [
        Path(__file__).resolve().parent.parent / "configs" / "bee_terms.yaml",
        Path("configs/bee_terms.yaml"),
    ]

    for path in yaml_paths:
        if path.exists():
            try:
                import yaml
                with open(path, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                raw = data.get("bee_terms", {})
                _bee_terms = {k.lower(): v for k, v in raw.items() if isinstance(v, list)}
                log.info(f"Normalizer: loaded {len(_bee_terms)} bee terms from {path}")
                return
            except Exception as e:
                log.warning(f"Normalizer: bee_terms load failed ({path}): {e}")

    log.debug("Normalizer: no bee_terms.yaml found")


def _split_compound(word: str) -> Optional[list[str]]:
    """Try to split a compound word using Voikko WORDBASES attribute.

    Voikko analyze() returns WORDBASES like '+kissa(kissa)+n+pentu(pentu)'
    where parenthesized values are base forms of each compound part.
    Linking elements (like genitive 'n') have no parentheses and are skipped.

    Returns list of base form parts if compound (2+ parts), None otherwise.
    bee_terms should be checked BEFORE calling this — it handles domain terms
    Voikko doesn't know (varroa, propolis, nosema, etc).
    """
    if _voikko is None:
        return None
    try:
        analyses = _voikko.analyze(word)
        if not analyses:
            return None
        wordbases = analyses[0].get("WORDBASES", "")
        if not wordbases:
            return None
        # Extract base forms from parentheses:
        # '+kissa(kissa)+n+pentu(pentu)' → ['kissa', 'pentu']
        parts = re.findall(r'\(([^)]+)\)', wordbases)
        if len(parts) >= 2:
            return [p.lower() for p in parts]
    except Exception:
        pass
    return None


def _lemmatize_word(word: str) -> str:
    """Lemmatize a single word using Voikko or fallback suffix-stripping.

    Returns the best base form (single string, lowercase).
    """
    w = word.lower()

    # Try Voikko analyze()
    if _voikko is not None:
        try:
            analyses = _voikko.analyze(w)
            if analyses:
                base = analyses[0].get("BASEFORM", w).lower()
                return base
        except Exception:
            pass

    # Fallback: simple suffix-stripping
    for suffix in _FI_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= 2:
            return w[:-len(suffix)]
    return w


def _levenshtein(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(
                prev[j + 1] + 1,       # deletion
                curr[j] + 1,            # insertion
                prev[j] + (c1 != c2),   # substitution
            ))
        prev = curr
    return prev[-1]


def autocorrect_fi(text: str) -> str:
    """Auto-correct Finnish text using Voikko spell-checker.

    For each word, call spell(). If misspelled, call suggest() and take
    first suggestion if Levenshtein distance <= 2. Returns corrected text.
    Fallback: returns original text unchanged if Voikko unavailable.
    """
    _init_voikko()
    if _voikko is None:
        return text

    # Split into word tokens and separators to reconstruct later
    parts = re.split(r'([\wäöåÄÖÅ-]+)', text)
    result = []
    for part in parts:
        # Non-word parts (spaces, punctuation) pass through
        if not re.fullmatch(r'[\wäöåÄÖÅ-]+', part):
            result.append(part)
            continue
        word = part
        try:
            if _voikko.spell(word.lower()):
                result.append(word)
            else:
                suggestions = _voikko.suggest(word.lower())
                if suggestions and _levenshtein(word.lower(), suggestions[0].lower()) <= 2:
                    # Preserve original case pattern
                    corrected = suggestions[0]
                    if word.islower():
                        corrected = corrected.lower()
                    elif word.isupper():
                        corrected = corrected.upper()
                    result.append(corrected)
                else:
                    result.append(word)
        except Exception:
            result.append(word)

    return "".join(result)


# Content word classes to KEEP (nouns, verbs, adjectives)
_CONTENT_CLASSES = {'nimisana', 'teonsana', 'laatusana', 'nimisana_laatusana'}
# Function word classes to REMOVE (adverbs, conjunctions, prepositions, pronouns, interjections)
_FUNCTION_CLASSES = {'seikkasana', 'sidesana', 'suhdesana', 'asemosana', 'huudahdussana'}


def _filter_stopwords(tokens: list[str]) -> list[str]:
    """Filter stopwords using Voikko CLASS-based POS tagging.

    Keeps content words (nouns, verbs, adjectives).
    Removes function words (adverbs, conjunctions, prepositions, pronouns).
    Falls back to STOP_FI set when Voikko is unavailable.
    """
    if _voikko is None:
        return [t for t in tokens if t and t not in STOP_FI]

    result = []
    for token in tokens:
        if not token:
            continue
        try:
            analyses = _voikko.analyze(token)
            if not analyses:
                # Unknown word — keep it (don't drop unknowns)
                result.append(token)
                continue
            word_class = analyses[0].get("CLASS", "")
            if word_class in _CONTENT_CLASSES:
                result.append(token)
            elif word_class in _FUNCTION_CLASSES:
                continue  # Remove function word
            else:
                # Unknown CLASS — keep (safe default)
                result.append(token)
        except Exception:
            # Error analyzing — fall back to STOP_FI for this token
            if token not in STOP_FI:
                result.append(token)
    return result


def normalize_fi(text: str, sort_words: bool = False) -> str:
    """Normalize Finnish text for matching/cache keys.

    Algorithm:
        1. Lowercase, strip whitespace and trailing punctuation
        2. Tokenize (word chars + Finnish chars + hyphens)
        3. For each word:
           a. Check bee_terms -> expand compound to parts (domain override)
           b. Else Voikko WORDBASES -> auto-split compound into base parts
           c. Else Voikko BASEFORM -> single lemma
           d. Else fallback suffix-strip
        4. Remove stopwords
        5. Optionally sort alphabetically (for order-independent cache keys)
        6. Return space-joined tokens

    Args:
        text: Finnish text to normalize
        sort_words: If True, sort tokens alphabetically (for cache keys)

    Returns:
        Normalized string of lemmatized tokens
    """
    if not text:
        return ""

    # Lazy init singletons
    _init_voikko()
    _init_bee_terms()

    # Step 1: lowercase, strip trailing punctuation
    text = text.lower().strip()
    text = text.rstrip("?!.")
    text = text.strip()

    if not text:
        return ""

    # Step 2: tokenize
    words = re.findall(r'[\wäöåÄÖÅ-]+', text)

    # Step 3: lemmatize each word
    tokens = []
    for w in words:
        # Clean: remove non-word chars except hyphens handled by regex
        w_clean = w.strip("-")
        if not w_clean:
            continue
        w_lower = w_clean.lower()

        # 3a: check bee_terms compound dictionary (domain override)
        if w_lower in _bee_terms:
            parts = _bee_terms[w_lower]
            tokens.extend(p.lower() for p in parts)
            continue

        # 3b: Voikko WORDBASES auto-split (for compounds bee_terms doesn't cover)
        compound_parts = _split_compound(w_lower)
        if compound_parts:
            tokens.extend(compound_parts)
            continue

        # 3c/3d: Voikko BASEFORM or fallback suffix-strip
        lemma = _lemmatize_word(w_clean)
        if lemma:
            tokens.append(lemma)

    # Step 4: remove stopwords (dynamic Voikko CLASS or STOP_FI fallback)
    tokens = _filter_stopwords(tokens)

    # Step 5: optional sort
    if sort_words:
        tokens.sort()

    # Step 6: join
    return " ".join(tokens)

"""Opt-in messages-only source verification and result resolution, read-only.

This module has no CLI, encoder, native backend, builder, writer or runtime
activation. All inputs are explicit caller bindings. Hashes and HMACs prove
consistency, not operator authority, source authentication, freshness or
same-user ABA resistance. Defaults refuse. No live tuple is provisioned here.

The v2 schemas deliberately admit operator-approved build-lane review
discussion while retaining RCO author/recipient and content exclusions.
Historical v1 projections/decisions are not upgraded. Screening primitives
are extracted from d108d5fc; record/hash/visibility rules derive from 30fc2dbc.
Semantic memory/descriptor/encoder query support is a separate unfinished
slice. This verifier alone does not implement semantic recall.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import importlib
import importlib.util
import json
import re
import unicodedata
from pathlib import Path
from types import FunctionType, ModuleType

SEALED_MARKERS = ("sealed", "gold", "heldout", "judge")


def _has_marker(text):
    # Same bounded compatibility/unescape guard as the existing workspace.
    form = text
    for _ in range(4):
        normalized = unicodedata.normalize("NFKC", form).casefold().upper()
        if any(marker in normalized for marker in ("PRIVATE_MARKER", "_DO_NOT_LEAK")):
            return True
        unescaped = re.sub(r"\\u([0-9a-fA-F]{4})", lambda match: chr(int(match[1], 16)), form)
        if unescaped == form:
            return False
        form = unescaped
    return True

MAX_MESSAGE_CHARS = 4096

MAX_CHUNK_CHARS = 300

MIN_CHUNK_CHARS = 16

DROP_TOKENS = frozenset({"credential", "identifier", "personal", "sealed", "mixed_script", "encoding", "empty",
                         "oversize", "unknown"})

_VERBATIM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.:- ")

_ESCAPE = re.compile(r"_([0-9a-f]{4})")

_OUTPUT = re.compile(r"[A-Za-z0-9._: -]{1,320}\Z")

_CREDENTIAL = (
    re.compile(r"(?i)(?<![a-z0-9])(?:sk-|gh[pousr]_|github_pat_|xox[abposr]-)[a-z0-9_-]{8,}"),
    re.compile(r"(?<![A-Za-z0-9])(?:AKIA|ASIA)[A-Z0-9]{12,}"),
    re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"(?i)\bauthorization\s*[:=]\s*\S{8,}"),
    re.compile(r"(?i)(?<![a-z0-9])(?:[a-z0-9]+_)*(?:password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret|private[_-]?key"
               r"|access[_-]?key|salasana|tunnussana|pin[-_ ]?koodi)\s*(?:[:=]|\bis\b|\bon\b)\s*\S+"),
    re.compile(r"-----BEGIN [A-Z0-9 ]*-----"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*"),
    re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]*@"),
    re.compile(r"(?i)(?<![a-z0-9])\d{2}(?:-[a-z0-9]{4,7}){5,}(?![a-z0-9-])"),
    re.compile(r"(?i)(?<![a-z0-9])(?:[a-z0-9]{5}-){4}[a-z0-9]{5}(?![a-z0-9-])"),
    re.compile(r"[0-9a-fA-F]{32,}"),
)

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")

_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

_ISO_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?Z?)?")

_IBAN_LENGTHS = dict((code[:2], int(code[2:])) for code in (
    "AD24 AE23 AL28 AT20 AZ28 BA20 BE16 BG22 BH22 BR29 BY28 CH21 CR22 CY28 CZ24 DE22 DK18 DO28 EE20 EG29 ES24 FI18 "
    "FO18 FR27 GB22 GE22 GI23 GL18 GR27 GT28 HR21 HU28 IE22 IL23 IQ23 IS26 IT27 JO30 KW30 KZ20 LB28 LC32 LI21 LT20 "
    "LU20 LV21 LY25 MC27 MD24 ME22 MK19 MR27 MT31 MU30 NL18 NO15 PK24 PL28 PS29 PT25 QA29 RO24 RS22 SA24 SC31 SE24 "
    "SI19 SK24 SM27 ST25 SV28 TL23 TN24 TR26 UA29 VA22 VG24 XK20").split())

_IBAN_CANDIDATE = re.compile(r"(?i)\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.+-]+")

_CREDIT_CARD_RE = re.compile(r"\b\d{13,19}\b")

_HETU_RE = re.compile(r"\b[0-3]\d[01]\d\d{2}[+\-A-FYXWVU]\d{3}[0-9A-Y]\b")

_Y_TUNNUS_RE = re.compile(r"\b\d{7}-\d\b")

_PHONE_RE = re.compile(r"\+?\d[\d\s-]{8,}")

_PERSONAL = (_EMAIL_RE, _CREDIT_CARD_RE, _HETU_RE, _Y_TUNNUS_RE)

_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://\S+")

_LOCAL_PATH = (re.compile(r"[A-Za-z]:[\\/][^\s:]*(?:[ \t][^\s\\/:]+)+[\\/][^\s]*"),
               re.compile(r"[A-Za-z]:[\\/][^\s]*"), re.compile(r"\\\\[^\s]+"),
               re.compile(r"(?<![^\s(\"'])(?:~|/)[\w.-]*(?:/[\w.-]+)+/?"))

_AMBIGUOUS_PROFILE = re.compile(r"(?i)[a-z]:[\\/]users[\\/][^\s\\/]+[ \t]+[a-z][^\s\\/]*")

_AMBIGUOUS_TERMINAL_PATH = re.compile(r"(?i)[a-z]:[\\/][^\s]+[ \t]+[^\s]+$")

_AMBIGUOUS_SPACED_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?<!\S)(?:~/|/))[^\s]*[ \t]+[A-Z][A-Za-z]+")

_OWNER_HEX = re.compile(r"(?i)(?<![a-z0-9])(?:[a-z0-9]+_)*owner\s*[:=]\s*[0-9a-f]{32,}")

class ContentRefused(ValueError):
    """One fixed reason token; never the offending value."""

class _Drop(Exception):
    def __init__(self, token):
        super().__init__(token)
        self.token = token

def _refuse(token):
    raise ContentRefused(token)

def encode_text(text):
    """NFC text to the lane output alphabet; refuses any character outside the BMP or any non-text."""
    if type(text) is not str:
        _refuse("encoding")
    out = []
    for char in unicodedata.normalize("NFC", text):
        if char in _VERBATIM:
            out.append(char)
        elif ord(char) <= 0xFFFF and not 0xD800 <= ord(char) <= 0xDFFF:
            out.append("_%04x" % ord(char))
        else:
            _refuse("encoding")
    return "".join(out)

def decode_text(encoded):
    """Strict inverse of ``encode_text``: only the output alphabet, only canonical escapes."""
    if type(encoded) is not str or not encoded:
        _refuse("encoding")
    out, index = [], 0
    while index < len(encoded):
        char = encoded[index]
        if char != "_":
            if char not in _VERBATIM:
                _refuse("encoding")
            out.append(char)
            index += 1
            continue
        match = _ESCAPE.match(encoded, index)
        if match is None:
            _refuse("encoding")
        decoded = chr(int(match.group(1), 16))
        if decoded in _VERBATIM or 0xD800 <= ord(decoded) <= 0xDFFF:
            _refuse("encoding")
        out.append(decoded)
        index = match.end()
    return "".join(out)

def _normalize(text, limit):
    if type(text) is not str:
        raise _Drop("encoding")
    if len(text) > limit:
        raise _Drop("oversize")
    try:
        text.encode("utf-8", "strict")
    except UnicodeError:
        raise _Drop("encoding") from None
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    text = unicodedata.normalize("NFKC", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Cf")
    if len(text) > limit:
        raise _Drop("oversize")
    chars = []
    for char in text:
        if char.isspace():
            chars.append(" ")
        elif unicodedata.category(char) in ("Cc", "Cs", "Co", "Cn"):
            raise _Drop("encoding")
        else:
            chars.append(char)
    return " ".join("".join(chars).split())

def _base64_secret(text):
    for match in _BASE64_RUN.finditer(text):
        run = match.group(0)
        if re.fullmatch(r"[0-9a-fA-F]{32,}", run):
            continue  # Hex is independently screened or replaced; never retained.
        if any(c.isdigit() for c in run) and any(c.isupper() for c in run) and any(c.islower() for c in run):
            return True
    return False

def _redact_sensitive_runs(text, *, include_urls=False):
    """Replace the union of matches found on the same original text."""
    spans = []
    words = list(re.finditer(r"\S+", text))
    for word in words:
        if len(word.group()) >= 16:
            label = "hex-redacted" if re.fullmatch(r"[0-9a-fA-F]{32,}", word.group()) else "token-redacted"
            spans.append((word.start(), word.end(), label))
    for left, right in zip(words, words[1:]):
        if len(left.group()) >= 16 and len(right.group()) >= 16:
            spans.append((left.start(), right.end(), "token-redacted"))
    for match in _BASE64_RUN.finditer(text):
        label = "hex-redacted" if re.fullmatch(r"[0-9a-fA-F]{32,}", match.group()) else "token-redacted"
        spans.append((match.start(), match.end(), label))
    if include_urls:
        spans.extend((match.start(), match.end(), "url") for match in _URL.finditer(text))
    spans.sort()
    merged = []
    for start, end, label in spans:
        if merged and start < merged[-1][1]:
            left, right, old_label = merged[-1]
            merged[-1] = (left, max(right, end), old_label if label == old_label else "token-redacted")
        else:
            merged.append((start, end, label))
    out, position = [], 0
    for start, end, label in merged:
        out.extend((text[position:start], label))
        position = end
    out.append(text[position:])
    return "".join(out)

def _iban(text):
    """An IBAN of a registered country at exactly its ISO 13616 length with a valid mod-97 sum. The greedy candidate
    may run into the next words, so only its country-length prefix is checked."""
    for match in _IBAN_CANDIDATE.finditer(text):
        value = match.group(0).replace(" ", "").upper()
        size = _IBAN_LENGTHS.get(value[:2])
        if size is not None and len(value) >= size:
            candidate = value[:size]
            digits = "".join(str(int(c, 36)) for c in candidate[4:] + candidate[:4])
            if int(digits) % 97 == 1:
                return True
    return False

def _script(text):
    latin = other = False
    for char in text:
        category = unicodedata.category(char)
        if category.startswith("L"):
            if unicodedata.name(char, "").startswith("LATIN"):
                latin = True
            else:
                other = True
        elif category == "Nd" and not char.isascii():
            other = True
        if ord(char) > 0xFFFF:
            raise _Drop("encoding")
    if other:
        raise _Drop("mixed_script" if latin else "encoding")

def _screen(text, *, redact_hex=False, identifier_policy=None):
    """The detectors, in order, on one normalized text; raises ``_Drop`` with the first matching class."""
    path_text = lowered = phone_text = None
    try:
        patterns = _CREDENTIAL[:-1] if redact_hex else _CREDENTIAL
        if any(pattern.search(text) for pattern in patterns) or (not redact_hex and _base64_secret(text)) or _OWNER_HEX.search(text):
            raise _Drop("credential")
        # Check original credentials before masking URLs for path screening.
        path_text = _URL.sub("url", text)
        if (_AMBIGUOUS_PROFILE.search(path_text) or _AMBIGUOUS_TERMINAL_PATH.search(path_text)
                or _AMBIGUOUS_SPACED_PATH.search(path_text)):
            raise _Drop("personal")
        lowered = text.lower()
        if _UUID.search(text) or identifier_policy.contains(text):
            raise _Drop("identifier")
        if any(marker in lowered for marker in SEALED_MARKERS) or _has_marker(text):
            raise _Drop("sealed")
        phone_text = _CREDENTIAL[-1].sub("hex-redacted", text) if redact_hex else text
        if (any(pattern.search(text) for pattern in _PERSONAL) or _iban(text)
                or _PHONE_RE.search(_ISO_TIME.sub(" ", phone_text))):
            raise _Drop("personal")
        if redact_hex:
            text = _redact_sensitive_runs(text)
        lowered = text.lower()
        if _UUID.search(text) or identifier_policy.contains(text):
            raise _Drop("identifier")
        if (any(pattern.search(text) for pattern in _PERSONAL) or _iban(text)
                or _PHONE_RE.search(_ISO_TIME.sub(" ", text))):
            raise _Drop("personal")
        if any(marker in lowered for marker in SEALED_MARKERS) or _has_marker(text):
            raise _Drop("sealed")
        _script(text)
    finally:
        text = identifier_policy = path_text = lowered = phone_text = None

def _replace_locations(text):
    text = _URL.sub("url", text)
    for pattern in _LOCAL_PATH:
        text = pattern.sub("local-path", text)
    if "\\" in text or re.search(r"(?i)(?<![a-z0-9])[a-z]:[\\/]", text):
        raise _Drop("personal")
    return " ".join(text.split())

def _classify_with_policy(raw_text, limit, policy):
    token = encoded = text = None
    try:
        text = _normalize(raw_text, limit)
        if not text:
            raise _Drop("empty")
        _screen(text, redact_hex=True, identifier_policy=policy)
        text = _redact_sensitive_runs(text, include_urls=True)
        text = _replace_locations(text)
        if not text:
            raise _Drop("empty")
        _screen(text, identifier_policy=policy)
        encoded = encode_text(text)
        if _UUID.search(encoded) or any(pattern.search(encoded) for pattern in _CREDENTIAL[-1:]):
            raise _Drop("credential")
    except _Drop as exc:
        token = exc.token if exc.token in DROP_TOKENS else "unknown"
    except ContentRefused:
        token = "encoding"
    except Exception:
        token = "unknown"
    finally:
        raw_text = text = policy = None
    if token is not None:
        return "DROP", token
    return "KEEP", encoded

def chunk(encoded, limit=MAX_CHUNK_CHARS):
    """Chunks of at most ``limit`` characters on spaces; an over-long word splits between characters only."""
    units = []
    for word in encoded.split(" "):
        pieces, index = [], 0
        while index < len(word):
            step = 5 if word[index] == "_" else 1
            pieces.append(word[index:index + step])
            index += step
        units.append(pieces)
    chunks, current = [], ""
    for pieces in units:
        word = "".join(pieces)
        candidate = word if not current else current + " " + word
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(word) <= limit:
            current = word
            continue
        part = ""
        for piece in pieces:
            if len(part) + len(piece) > limit:
                chunks.append(part)
                part = ""
            part += piece
        current = part
    if current:
        chunks.append(current)
    return chunks

_SCHEMA = "wd.mind-identifier-policy.v1"

_TAG = re.compile(r"[0-9a-f]{64}\Z")

class PolicyRefused(ValueError):
    """Fixed-token refusal; input material is never included."""

def _identifier_refuse():
    raise PolicyRefused("policy_identifier_refused") from None

def _normalize_identifier(text):
    normalized = chars = char = category = None
    try:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        chars = []
        for char in normalized:
            category = unicodedata.category(char)
            if category == "Cf":
                continue
            if category.startswith("C") and not char.isspace():
                raise ValueError()
            chars.append(char)
        return " ".join("".join(chars).split())
    finally:
        text = normalized = chars = char = category = None

class IdentifierPolicy:
    """Immutable match interface; callers must not log or serialize its state."""

    __slots__ = ("__values",)

    def __init__(self, values):
        try:
            object.__setattr__(self, "_IdentifierPolicy__values", tuple(values))
        finally:
            self = values = None

    def __setattr__(self, name, value):
        raise AttributeError("immutable")

    def __repr__(self):
        return "<IdentifierPolicy private>"

    def contains(self, text):
        normalized = value = None
        try:
            if type(text) is not str:
                _identifier_refuse()
            normalized = _normalize_identifier(text)
            for value in self.__values:
                if value in normalized:
                    return True
            return False
        finally:
            self = text = normalized = value = None

def _load_identifier_policy(policy_bytes, key, expected_tag):
    """Validate caller-supplied private policy bytes and their keyed HMAC-SHA256."""
    document = values = item = normalized = actual = None
    try:
        if (type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= 65536
                or type(key) is not bytes or not 32 <= len(key) <= 1024
                or type(expected_tag) is not str or _TAG.fullmatch(expected_tag) is None):
            _identifier_refuse()
        actual = hmac.new(key, policy_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(actual, expected_tag):
            _identifier_refuse()
        document = json.loads(policy_bytes.decode("utf-8"), object_pairs_hook=_unique_pairs)
        if (type(document) is not dict or set(document) != {"schema", "identifiers"}
                or document["schema"] != _SCHEMA or type(document["identifiers"]) is not list
                or not 1 <= len(document["identifiers"]) <= 256):
            _identifier_refuse()
        values = []
        for item in document["identifiers"]:
            if type(item) is not str:
                _identifier_refuse()
            normalized = _normalize_identifier(item)
            if (not 3 <= len(normalized) <= 128 or normalized != normalized.strip()
                    or any(unicodedata.category(c).startswith("C") for c in normalized)
                    or normalized in values):
                _identifier_refuse()
            values.append(normalized)
        return IdentifierPolicy(values)
    except Exception:
        pass
    finally:
        document = values = item = normalized = actual = None
        policy_bytes = key = expected_tag = None
    # Raise outside the handler so the original parse exception and its input
    # are not reachable through __context__. Clear sensitive locals first.
    _identifier_refuse()


# Verification/resolution boundary. These schemas are intentionally distinct
# from the historical v1 decisions that excluded all review discussion.
_COMBINED_SCHEMA = "wd.mind-content-memory-projection.v2"
_MESSAGE_SCHEMA = "wd.bridge-mind-content-projection.v2"
_DECISION_SCHEMA = "wd.mind-content-route-decision.v2"
_ROUTE_SCOPE = "wd.q4-shared-projected-events.v1"
_RELEASE_TASK_LIST_SHA256 = "40e04e7002050b66085f8eff5723feb73e5f62a26910fe73405ab59e5ee5b765"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_TASK = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z")
_GENERATION = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
_LANES = frozenset(("codex-lead-1", "codex-tools-1", "fable-5", "claude-rco-1", "claude-rco-2"))
_BUILD_LANES = _LANES - {"claude-rco-1", "claude-rco-2"}
_TYPES = frozenset(("message", "wake_request", "handoff", "test", "claim", "release", "done", "milestone",
                    "status", "progress"))
_STATUSES = frozenset(("none", "requested", "answered", "milestone", "info", "in_progress", "handoff", "done",
                       "active", "delivered", "blocked", "released"))
_ROUTING_KEYS = frozenset(("to", "to_list", "recipient", "recipients", "recipient_agent", "target", "target_agent",
                         "target_agents", "destination", "destinations", "audience", "reply_to", "expected_responders",
                         "responders", "targets", "target_list", "recipient_agents", "recipient_list", "cc", "bcc",
                         "addressee", "addressees", "send_to"))
_RCO_NAME = re.compile(r"rco", re.IGNORECASE)
_INPUT_KEYS = frozenset(("enabled", "messages", "documents", "decision_bytes", "decision_sha256", "policy_bytes",
                        "policy_sha256", "roster_bytes", "roster_sha256", "approved_tasks_bytes", "approved_tasks_sha256",
                        "identifier_policy_binding"))
_MESSAGE_KEYS = frozenset(("source_path", "generation_path", "end_cursor", "policy_bytes", "policy_sha256"))
_POLICY_KEYS = frozenset(("schema", "lanes", "types", "approved_tasks", "removed_source_refs", "max_message_chars",
                         "max_chunk_chars"))
_DECISION_KEYS = frozenset(("schema", "decision", "projection_schema", "scope", "content_module_sha256",
                           "message_module_sha256", "doc_module_sha256", "nonreview_policy_sha256", "roster_sha256",
                           "message_policy_sha256", "doc_policy_sha256", "source_prefix", "message_projection_schema",
                           "review_signal_excluded", "approved_tasks_sha256", "identifier_policy_hmac"))
_REQUIRED_CODE = {
    "tools/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "tools/wd_bridge_mind.py": "8555bd3fda512078ba61e3638af7f17ba3de55be11e126f6a7740ae140a67211",
    "waggledance/core/bridge_log_reader.py": "1949e26526153363d8c19f5051f636a646bb6839758d779c904f1eab72c5f49e",
    "tools/wd_mind_content_prefix_readonly.py": "2928e877f48229fd75e9a98d00cb180aec22b46608f197337274a4f26ae46424",
    "tools/bridge_compact_view.py": "c808da1b1dd8f4e0220f8ffce730963455d0a706c2b4f1dd6387e450d5c84c83",
    "waggledance/__init__.py": "a48c64790f6d239f53960e5011fe87129555d7978197e9f0d0b43ef3b93d5e82",
    "waggledance/core/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
}
_MAX_CONTROL_BYTES = 65536
_MAX_PROJECTION_BYTES = 64 * 1024 * 1024


class ContentQueryRefused(ValueError):
    """The public fixed-token refusal; no caller or source text is emitted."""


def _deny():
    raise ContentQueryRefused("content_query_refused") from None


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _serialized(value):
    return json.dumps(value, sort_keys=True, indent=1, allow_nan=False).encode("utf-8")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _bad_constant(_):
    raise ValueError("non_json_number")


def _hex(value):
    return type(value) is str and _HEX64.fullmatch(value) is not None


def _pinned(data, digest, limit=_MAX_CONTROL_BYTES):
    if (type(data) is not bytes or not 0 < len(data) <= limit or not _hex(digest)
            or not hmac.compare_digest(_sha(data), digest)):
        raise ValueError("invalid_pin")
    return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)


def _bounded_source_bytes(path, limit=2 * 1024 * 1024, *, allow_empty=False):
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit or (not data and not allow_empty):
        raise ValueError("code_over_budget")
    return data


def module_sha256s():
    """The v2 roles live in this one file, including the closed document guard.

    Each field binds these extraction bytes, not an old module or approval.
    Dependency bytes are separately held at the unchanged b3/main baseline.
    This is code consistency, not loaded-code or publisher authentication.
    """
    digest = None
    failed = False
    try:
        digest = _sha(_bounded_source_bytes(Path(__file__)))
    except Exception:
        failed = True
    if failed:
        _deny()
    return {"content_module_sha256": digest, "message_module_sha256": digest, "doc_module_sha256": digest}


def _check_code():
    root = Path(__file__).resolve().parents[1]
    for relative, digest in _REQUIRED_CODE.items():
        if not hmac.compare_digest(_sha(_bounded_source_bytes(root / relative, allow_empty=True)), digest):
            raise ValueError("dependency_changed")


def _pinned_dependency(name, relative, entrypoints):
    """Reject provably wrong import origins before invoking a dependency.

    Origin/spec/function metadata and source hashes do not authenticate a
    hostile interpreter, same-path bytecode, mutable globals or same-user
    races. There is no reload or fallback to an unverified alternate module.
    """
    expected = (Path(__file__).resolve().parents[1] / relative).resolve()
    digest = _REQUIRED_CODE[relative]
    if not hmac.compare_digest(_sha(_bounded_source_bytes(expected)), digest):
        raise ValueError("dependency_changed")
    spec = importlib.util.find_spec(name)
    if spec is None or type(spec.origin) is not str or Path(spec.origin).resolve() != expected:
        raise ValueError("dependency_origin_mismatch")
    module = importlib.import_module(name)
    if type(module) is not ModuleType:
        raise ValueError("dependency_origin_mismatch")
    fields = module.__dict__
    loaded_spec = fields.get("__spec__")
    if (type(fields.get("__file__")) is not str or Path(fields["__file__"]).resolve() != expected
            or loaded_spec is None or type(loaded_spec.origin) is not str
            or Path(loaded_spec.origin).resolve() != expected):
        raise ValueError("dependency_origin_mismatch")
    for entry in entrypoints:
        function = fields.get(entry)
        if (type(function) is not FunctionType or function.__module__ != name
                or function.__globals__ is not fields
                or Path(function.__code__.co_filename).resolve() != expected):
            raise ValueError("dependency_callable_mismatch")
    if not hmac.compare_digest(_sha(_bounded_source_bytes(expected)), digest):
        raise ValueError("dependency_changed")
    return module


def _strings(value, check, *, minimum=0, maximum=4096):
    if (type(value) is not list or not minimum <= len(value) <= maximum
            or any(type(item) is not str or not check(item) for item in value)
            or len(set(value)) != len(value)):
        raise ValueError("invalid_set")
    return frozenset(value)


def _task(value):
    return _TASK.fullmatch(value) is not None and not any(word in value.casefold() for word in SEALED_MARKERS)


def _roster(document):
    if (type(document) is not dict or set(document) != {"schema", "lanes", "tools_supervisor"}
            or document["schema"] != "wd.q4-roster-snapshot.v1"
            or type(document["lanes"]) is not list or len(document["lanes"]) != 4
            or type(document["tools_supervisor"]) is not dict):
        raise ValueError("invalid_roster")
    entries = document["lanes"] + [document["tools_supervisor"]]
    result = {}
    for entry in entries:
        if (type(entry) is not dict or set(entry) not in ({"agent", "agent_uuid"}, {"agent", "agent_uuid", "role"})
                or type(entry["agent"]) is not str or entry["agent"] not in _LANES
                or ("role" in entry and (type(entry["role"]) is not str
                    or re.fullmatch(r"[a-z][a-z0-9-]{0,39}", entry["role"]) is None))):
            raise ValueError("invalid_roster")
        agent = entry["agent"]
        identity = entry["agent_uuid"]
        if (agent in result or type(identity) is not str
                or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", identity) is None
                or identity in result.values()):
            raise ValueError("invalid_roster")
        result[agent] = identity
    projection_lanes = _LANES - {"codex-tools-1"}
    if (set(result) != _LANES or document["tools_supervisor"]["agent"] != "codex-tools-1"
            or [entry["agent"] for entry in document["lanes"]] != sorted(projection_lanes)):
        raise ValueError("invalid_roster_lane_set")
    return result


def _message_policy(document, tasks):
    if type(document) is not dict:
        raise ValueError("invalid_message_policy")
    schema = document.get("schema")
    keys = (_POLICY_KEYS if schema == "wd.mind-content-policy.v1" else
            _POLICY_KEYS | {"approved_source_refs"} if schema == "wd.mind-content-policy.v2" else None)
    if keys is None or set(document) != keys:
        raise ValueError("invalid_message_policy")
    result = {
        "lanes": _strings(document["lanes"], lambda lane: lane in _BUILD_LANES, minimum=1, maximum=3),
        "types": _strings(document["types"], lambda kind: kind in _TYPES, minimum=1, maximum=len(_TYPES)),
        "approved_tasks": _strings(document["approved_tasks"], _task, minimum=1, maximum=8),
        "removed_source_refs": _strings(document["removed_source_refs"], _hex),
    }
    if not result["approved_tasks"] <= tasks:
        raise ValueError("task_widening")
    for name, low, high in (("max_message_chars", 1, MAX_MESSAGE_CHARS),
                            ("max_chunk_chars", MIN_CHUNK_CHARS, MAX_CHUNK_CHARS)):
        if type(document[name]) is not int or not low <= document[name] <= high:
            raise ValueError("invalid_budget")
        result[name] = document[name]
    if "approved_source_refs" in document:
        result["approved_source_refs"] = _strings(document["approved_source_refs"], _hex)
    return result


def _inputs(values):
    # All policy/decision pins are checked before source prefix I/O, including
    # required private policy. Unknown fields or missing evidence refuse.
    if set(values) != _INPUT_KEYS or values["enabled"] is not True or values["documents"] is not None:
        raise ValueError("invalid_inputs")
    tasks = _strings(_pinned(values["approved_tasks_bytes"], values["approved_tasks_sha256"]),
                     _task, minimum=8, maximum=8)
    policy = _pinned(values["policy_bytes"], values["policy_sha256"])
    if (type(policy) is not dict or set(policy) != {"schema", "approved_tasks"}
            or policy["schema"] != "wd.q4-non-review-policy.v1"
            or _strings(policy["approved_tasks"], _task, minimum=8, maximum=8) != tasks):
        raise ValueError("invalid_task_policy")
    roster_document = _pinned(values["roster_bytes"], values["roster_sha256"])
    roster = _roster(roster_document)
    if not hmac.compare_digest(_canonical(roster_document), values["roster_bytes"]):
        raise ValueError("noncanonical_roster")
    messages = values["messages"]
    if type(messages) is not dict or set(messages) != _MESSAGE_KEYS:
        raise ValueError("invalid_message_source")
    message_policy = _message_policy(_pinned(messages["policy_bytes"], messages["policy_sha256"]), tasks)
    binding = values["identifier_policy_binding"]
    if type(binding) is not tuple or len(binding) != 3:
        raise ValueError("missing_identifier_policy")
    private = _load_identifier_policy(*binding)
    decision = _pinned(values["decision_bytes"], values["decision_sha256"])
    if (type(decision) is not dict or set(decision) != _DECISION_KEYS
            or _canonical(decision) != values["decision_bytes"]
            or decision["schema"] != _DECISION_SCHEMA or decision["decision"] != "enable_content_route"
            or decision["projection_schema"] != _COMBINED_SCHEMA or decision["scope"] != _ROUTE_SCOPE
            or decision["message_projection_schema"] != _MESSAGE_SCHEMA
            or decision["review_signal_excluded"] is not False or decision["doc_policy_sha256"] is not None):
        raise ValueError("invalid_decision")
    pins = dict(module_sha256s(), nonreview_policy_sha256=values["policy_sha256"],
                roster_sha256=values["roster_sha256"], message_policy_sha256=messages["policy_sha256"],
                approved_tasks_sha256=values["approved_tasks_sha256"], identifier_policy_hmac=binding[2])
    if any(not _hex(decision[name]) or not hmac.compare_digest(decision[name], digest)
           for name, digest in pins.items()):
        raise ValueError("decision_binding_mismatch")
    anchored = decision["source_prefix"]
    if (type(anchored) is not dict or set(anchored) != {"sha256", "offset", "generation"}
            or not _hex(anchored["sha256"]) or type(anchored["offset"]) is not int
            or not 0 < anchored["offset"] <= 256 * 1024 * 1024
            or type(anchored["generation"]) is not str or _GENERATION.fullmatch(anchored["generation"]) is None):
        raise ValueError("invalid_anchor")
    _check_code()
    # Imports only after the pinned dependency byte check. No native/model
    # modules, subprocesses or Q4 lane caller are involved in this slice.
    bridge = _pinned_dependency("waggledance.core.bridge_log_reader", "waggledance/core/bridge_log_reader.py",
                                ("_validate_cursor",))
    cursor = messages["end_cursor"]
    if (type(cursor) is not bridge.BridgeCursor or bridge._validate_cursor(cursor)
            or cursor.offset != anchored["offset"] or cursor.generation != anchored["generation"]
            or any(type(messages[name]) is not str or not Path(messages[name]).is_absolute()
                   for name in ("source_path", "generation_path"))):
        raise ValueError("invalid_source_cursor")
    return {"messages": messages, "policy": message_policy, "roster": roster, "private": private,
            "identifier_tag": binding[2], "decision": decision, "decision_sha256": values["decision_sha256"]}


def _unsafe_recipient(value):
    if type(value) is str:
        return _RCO_NAME.search(value) is not None
    if type(value) is list:
        return any(type(item) is not str or _RCO_NAME.search(item) is not None for item in value)
    return True


def _unsafe_responders(value):
    if type(value) is not dict or not value:
        return True
    for lane, identity in value.items():
        if (type(lane) is not str or lane not in _BUILD_LANES or type(identity) is not dict
                or set(identity) != {"agent_uuid", "run_id", "session_id"}):
            return True
        for key, item in identity.items():
            pattern = (r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}" if key == "agent_uuid"
                       else r"[A-Za-z0-9._:-]{1,128}")
            if type(item) is not str or re.fullmatch(pattern, item) is None or _RCO_NAME.search(item):
                return True
    return False


def _excluded_route(event):
    if type(event.get("agent")) is not str or event["agent"] not in _BUILD_LANES:
        return True
    if event.get("payload") is not None and type(event["payload"]) is not dict:
        return True
    stack = [event]
    count = 0
    while stack:
        value = stack.pop()
        count += 1
        if count > 65536:
            return True
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    return True
                normalized = key.lower().replace("-", "_")
                if normalized == "expected_responders":
                    if _unsafe_responders(item):
                        return True
                elif normalized in _ROUTING_KEYS and _unsafe_recipient(item):
                    return True
                if type(item) in (dict, list):
                    stack.append(item)
        elif type(value) is list:
            stack.extend(item for item in value if type(item) in (dict, list))
    return False


def _utc(value):
    if type(value) is not str:
        raise ValueError("invalid_time")
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("naive_time")
    return parsed.astimezone(dt.timezone.utc)


def _utc_text(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fields(event, policy, roster):
    if _excluded_route(event):
        return None
    agent = event.get("agent")
    if agent not in policy["lanes"]:
        return "lane_not_allowed"
    if event.get("agent_uuid") != roster[agent]:
        return "agent_identity_mismatch"
    if type(event.get("type")) is not str or event["type"] not in policy["types"]:
        return "type_not_allowed"
    if type(event.get("task_id")) is not str or event["task_id"] not in policy["approved_tasks"]:
        return "task_not_approved"
    status = event.get("status")
    if status not in (None, "") and (type(status) is not str or status not in _STATUSES):
        return "status_not_allowed"
    try:
        when = _utc(event.get("ts_utc")).replace(second=0, microsecond=0)
        available = when + dt.timedelta(seconds=60)
    except (ValueError, TypeError, OverflowError):
        return "time_invalid"
    return {"agent": agent, "type": event["type"], "task_id": event["task_id"],
            "ts_utc": _utc_text(when), "available": _utc_text(available)}


def _record(source_ref, fields, chunks, index):
    text = chunks[index]
    ref = _sha(_canonical({"text": text, "source": source_ref, "chunk": index}))
    return {"record_id": "content:" + ref, "kind": "event", "time_utc": fields["ts_utc"],
            "available_utc": fields["available"], "stored_text": text, "summary": text,
            "meta": {"agent": fields["agent"], "type": fields["type"], "task_id": fields["task_id"],
                     "text_encoding": "wd.content-text.v1"},
            "provenance": {"source_kind": "bridge_message", "event_ref": ref,
                           "ref_kind": "sha256_of_content_chunk_and_source", "canonical_event": False,
                           "source_event_ref": source_ref, "chunk_index": index, "chunk_count": len(chunks),
                           "tier": {"source_type": "bridge_event"}, "available_utc": fields["available"]}}


def _projection(context):
    prefix = _pinned_dependency("tools.wd_mind_content_prefix_readonly", "tools/wd_mind_content_prefix_readonly.py",
                                ("read_prefix_rows",))
    source, policy = context["messages"], context["policy"]
    rows = prefix.read_prefix_rows(source["source_path"], source["generation_path"], source["end_cursor"],
                                  context["decision"]["source_prefix"]["sha256"], set(policy["approved_tasks"]))
    records, refused = [], {}
    for ref in sorted(rows):
        event = rows[ref]
        if not _hex(ref) or type(event) is not dict or not hmac.compare_digest(_sha(_canonical(event)), ref):
            raise ValueError("source_record_mismatch")
        if "approved_source_refs" in policy and ref not in policy["approved_source_refs"]:
            continue
        fields = _fields(event, policy, context["roster"])
        if fields is None:
            continue
        token = fields if type(fields) is str else None
        if token is None and ref in policy["removed_source_refs"]:
            token = "removed"
        if token is None:
            message = event.get("message")
            if message is None or message == "":
                token = "no_message"
            else:
                verdict, value = _classify_with_policy(message, policy["max_message_chars"], context["private"])
                if verdict != "KEEP":
                    token = value
        if token is not None:
            refused[token] = refused.get(token, 0) + 1
            continue
        chunks = chunk(value, policy["max_chunk_chars"])
        if not chunks or any(_OUTPUT.fullmatch(text) is None or _has_marker(text) for text in chunks):
            refused["encoding"] = refused.get("encoding", 0) + 1
            continue
        records.extend(_record(ref, fields, chunks, index) for index in range(len(chunks)))
    records.sort(key=lambda item: (item["time_utc"], item["provenance"]["source_event_ref"],
                                   item["provenance"]["chunk_index"]))
    messages = {"schema": _MESSAGE_SCHEMA, "authority": "none", "scope": "shared_content_projection_not_gate",
                "policy_sha256": source["policy_sha256"], "text_encoding": "wd.content-text.v1",
                "accepted": len(records), "refused": dict(sorted(refused.items())), "records": records,
                "authorship": "declared_roster_consistent", "review_signal_excluded": False,
                "identifier_policy_hmac": context["identifier_tag"]}
    return {"schema": _COMBINED_SCHEMA, "authority": "none", "scope": "wd.mind-content-events.v1",
            "messages": messages, "documents": None}


def _verified(snapshot):
    inputs = dict(snapshot)
    raw = inputs.pop("projection_bytes", None)
    digest = inputs.pop("projection_sha256", None)
    _pinned(raw, digest, _MAX_PROJECTION_BYTES)  # checked before source I/O
    context = _inputs(inputs)
    projection = _projection(context)
    if not hmac.compare_digest(_serialized(projection), raw):
        raise ValueError("snapshot_mismatch")
    # MATCH is source consistency only. A release/query caller is still
    # responsible for checking the independently authorized list bytes against
    # this fixed digest, plus its other authority and runtime tuple conditions.
    report = {"status": "MATCH", "projection_sha256": digest, "decision_sha256": context["decision_sha256"],
              "source_prefix": dict(context["decision"]["source_prefix"]), "doc_commit": None,
              "operator_scope_authorization": "UNKNOWN", "required_release_task_list_sha256": _RELEASE_TASK_LIST_SHA256}
    return projection, context, report


def rederive_message_projection(**inputs):
    """Re-read the caller-anchored prefix and return only the screened v2 projection.

    This computes verification bytes in memory. It never writes a projection,
    memory, pointer, cursor, receipt or runtime state.
    """
    output = None
    succeeded = False
    try:
        output = _projection(_inputs(inputs))
        succeeded = True
    except Exception:
        pass
    inputs = None
    if not succeeded:
        _deny()
    return output


def validate_content_snapshot(**snapshot):
    """MATCH only on exact pinned v2 bytes re-derived from the anchored source."""
    report = None
    succeeded = False
    try:
        _, context, report = _verified(snapshot)
        context = None
        succeeded = True
    except Exception:
        pass
    snapshot = None
    if not succeeded:
        _deny()
    return report


def resolve_content_results(results, query_utc, **snapshot):
    """Revalidate, resolve exact visible hash pairs, rescreen and recheck source.

    The source_reference names canonical event JSON, never the raw JSONL line
    or authenticated sender. Only an exact re-derived record can supply it;
    the backend cannot inject a reference sidecar or text.
    """
    output = None
    succeeded = False
    try:
        when = _utc(query_utc) - dt.timedelta(seconds=1)
        if type(results) is not list or len(results) > 100:
            raise ValueError("invalid_results")
        projection, context, report = _verified(snapshot)
        matches = {}
        for record in projection["messages"]["records"]:
            key = (_sha(record["provenance"]["event_ref"].encode()), _sha(record["record_id"].encode()))
            matches.setdefault(key, []).append(record)
        output, seen = [], set()
        for item in results:
            if (type(item) is not dict or set(item) != {"ref_sha256", "doc_id_sha256", "ref_kind", "canonical_event"}
                    or not _hex(item["ref_sha256"]) or not _hex(item["doc_id_sha256"])
                    or item["ref_kind"] != "sha256_of_projected_metadata" or item["canonical_event"] is not False):
                raise ValueError("invalid_result")
            key = item["ref_sha256"], item["doc_id_sha256"]
            resolved = matches.get(key, [])
            if len(resolved) != 1:
                raise ValueError("result_unresolved")
            record = resolved[0]
            if _utc(record["provenance"]["available_utc"]) >= when:
                raise ValueError("result_not_visible")
            decoded = decode_text(record["stored_text"])
            verdict, text = _classify_with_policy(decoded, MAX_MESSAGE_CHARS, context["private"])
            if verdict != "KEEP" or text != record["stored_text"]:
                raise ValueError("result_screen_refused")
            if key not in seen:
                output.append(dict(item, stored_text=text, source_reference={
                    "sha256": record["provenance"]["source_event_ref"],
                    "kind": "sha256_of_canonical_event_json", "verified": "MATCH_WITHIN_PREFIX",
                    "sender_authentication": "UNKNOWN"}))
                seen.add(key)
        # Do not let a source/generation change after MATCH turn a stale hit
        # into a fresh claim. Same-user ABA and publisher authority stay unknown.
        _, final_context, final_report = _verified(snapshot)
        if report != final_report:
            raise ValueError("source_changed")
        context = final_context = projection = matches = None
        succeeded = True
    except Exception:
        pass
    snapshot = results = query_utc = None
    if not succeeded:
        output = None
        _deny()
    return output

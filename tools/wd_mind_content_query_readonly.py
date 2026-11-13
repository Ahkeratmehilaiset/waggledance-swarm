"""Opt-in messages-only source verification and frozen semantic query, read-only.

This module has no CLI, encoder construction, native import, builder, writer or
runtime activation. All inputs are explicit caller bindings. Hashes and HMACs prove
consistency, not operator authority, source authentication, freshness or
same-user ABA resistance. Defaults refuse. No live tuple is provisioned here.

The v2 schemas deliberately admit operator-approved build-lane review
discussion while retaining RCO author/recipient and content exclusions.
Historical v1 projections/decisions are not upgraded. Screening primitives
are extracted from d108d5fc; record/hash/visibility rules derive from 30fc2dbc.
The frozen semantic API uses caller-established providers. Validation uses
synthetic providers; real-provider provenance, native numerical parity,
runtime authority and installed delivery are separate unproven boundaries.
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


# Synthetic-first frozen semantic boundary. Providers are established by the
# caller; identity metadata is consistency evidence, not loaded-code authority.
# This module never constructs an encoder, imports a native backend or writes
# cache/lock/scratch state. Native/provider/runtime adoption requires its own
# independently authorized tuple.
_Q4_SOURCE_KEYS = _INPUT_KEYS | {"projection_bytes", "projection_sha256"}
_Q4_KEYS = _Q4_SOURCE_KEYS | {
    "descriptor_bytes", "descriptor_sha256", "memory_bytes", "memory_sha256",
    "cell_snapshots", "encoder_sha256", "runtime_revision", "encoder_provider",
    "encoder_binding_bytes", "encoder_binding_sha256", "index_provider",
    "index_binding_bytes", "index_binding_sha256", "stream",
}
_Q4_CELLS = frozenset(("general", "thermal", "energy", "safety", "seasonal", "math", "system", "learning"))
_Q4_PLACEMENT = {"message": "general", "milestone": "general", "progress": "general",
                 "status": "system", "wake_request": "system", "handoff": "system",
                 "claim": "system", "release": "system", "done": "system", "test": "learning"}
_Q4_DIM = 384
_Q4_MAX_ROWS = 20000  # Additional availability bound, not a native allocation guarantee.
_Q4_TOTAL_BYTES = 512 * 1024 * 1024
_Q4_ARTIFACT_BYTES = 16 * 1024 * 1024
_Q4_TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
_Q4_WORD = re.compile(r"[^\W_]+", re.UNICODE)


def _q4_identity(value):
    if (type(value) is not dict or set(value) != {"model", "revision", "weights_sha256"}
            or value["model"] != "intfloat/multilingual-e5-small"
            or type(value["revision"]) is not str or not value["revision"]
            or len(value["revision"]) > 256 or not _hex(value["weights_sha256"])):
        raise ValueError("invalid_encoder_identity")
    return value


def _q4_bindings(encoder_binding, index_binding, identity=None):
    if type(encoder_binding) is not dict:
        raise ValueError("invalid_encoder_binding")
    own_identity = _q4_identity(encoder_binding.get("encoder"))
    if identity is None:
        identity = own_identity
    expected_policy = {"prefix": "query: ", "truncate_chars": 2000, "normalize": True,
                       "dtype": "float32", "dim": _Q4_DIM}
    if (type(encoder_binding) is not dict
            or set(encoder_binding) != {"schema", "encoder", "code_sha256", "query_policy"}
            or encoder_binding["schema"] != "wd.mind-readonly-encoder-binding.v1"
            or encoder_binding["encoder"] != identity
            or not _hex(encoder_binding["code_sha256"])
            or encoder_binding["query_policy"] != expected_policy
            or type(encoder_binding["query_policy"]) is not dict
            or type(encoder_binding["query_policy"].get("normalize")) is not bool
            or type(encoder_binding["query_policy"].get("dim")) is not int):
        raise ValueError("invalid_encoder_binding")
    if (type(index_binding) is not dict
            or set(index_binding) != {"schema", "index_type", "metric_type", "code_sha256"}
            or index_binding["schema"] != "wd.mind-readonly-index-binding.v1"
            or index_binding["index_type"] != "IndexFlatIP"
            or type(index_binding["metric_type"]) is not int or index_binding["metric_type"] != 0
            or not _hex(index_binding["code_sha256"])):
        raise ValueError("invalid_index_binding")


def _q4_controls(values):
    if type(values) is not dict or set(values) != _Q4_KEYS or values["enabled"] is not True:
        raise ValueError("invalid_semantic_inputs")
    # Independently enforce the exact operator list bytes, not a digest derived
    # from the same untrusted document or merely an equivalent set of task IDs.
    tasks = values["approved_tasks_bytes"]
    if (type(tasks) is not bytes or not 0 < len(tasks) <= _MAX_CONTROL_BYTES
            or values["approved_tasks_sha256"] != _RELEASE_TASK_LIST_SHA256
            or not hmac.compare_digest(_sha(tasks), _RELEASE_TASK_LIST_SHA256)):
        raise ValueError("operator_task_list_mismatch")
    descriptor = _pinned(values["descriptor_bytes"], values["descriptor_sha256"])
    memory = _pinned(values["memory_bytes"], values["memory_sha256"], limit=1024 * 1024)
    encoder_binding = _pinned(values["encoder_binding_bytes"], values["encoder_binding_sha256"])
    index_binding = _pinned(values["index_binding_bytes"], values["index_binding_sha256"])
    if (type(descriptor) is not dict
            or set(descriptor) != {"schema", "scope", "memory_sha256", "dim", "encoder"}
            or descriptor["schema"] != "wd.bridge-mind-q4-memory-descriptor.v2"
            or descriptor["scope"] != "wd.q4-shared-projected-events.v1"
            or type(descriptor["dim"]) is not int or descriptor["dim"] != _Q4_DIM
            or descriptor["memory_sha256"] != values["memory_sha256"]):
        raise ValueError("invalid_descriptor")
    identity = _q4_identity(descriptor["encoder"])
    if (not _hex(values["encoder_sha256"])
            or not hmac.compare_digest(_sha(_canonical(identity)), values["encoder_sha256"])
            or type(values["runtime_revision"]) is not str
            or values["runtime_revision"] != identity["revision"]):
        raise ValueError("encoder_binding_mismatch")
    _q4_bindings(encoder_binding, index_binding, identity)
    if (type(memory) is not dict or memory.get("schema") != "wd.bridge-mind-hex-memory-step2.v1"
            or memory.get("scope") != descriptor["scope"]
            or type(memory.get("dim")) is not int or memory["dim"] != _Q4_DIM
            or memory.get("encoder") != identity or memory.get("projection_schema") != _COMBINED_SCHEMA
            or memory.get("projection_sha256") != values["projection_sha256"]
            or type(memory.get("cells")) is not dict or not memory["cells"]
            or not set(memory["cells"]) <= _Q4_CELLS
            or type(values["cell_snapshots"]) is not dict
            or set(values["cell_snapshots"]) != set(memory["cells"])):
        raise ValueError("invalid_memory")
    return descriptor, memory, encoder_binding, index_binding


def _q4_provider_identity(provider, expected, method):
    # Calling arbitrary in-process objects is an explicit caller trust boundary.
    # Matching this metadata cannot authenticate their implementation or globals.
    identity = getattr(provider, "identity", None)
    if (type(identity) is not dict or _canonical(identity) != _canonical(expected)
            or not callable(getattr(provider, method, None))):
        raise ValueError("invalid_provider")


def _q4_cell_controls(cell_id, item, bound, memory, records):
    if (type(item) is not dict or set(item) != {"pointer", "log", "artifacts"}
            or type(bound) is not dict or type(bound.get("generation")) is not int
            or bound["generation"] < 0 or not _hex(bound.get("receipt_sha256"))
            or not set(bound) <= {"generation", "seq", "receipt_sha256", "count"}):
        raise ValueError("invalid_cell")
    pointer_bytes, log_bytes, data = item["pointer"], item["log"], item["artifacts"]
    if (type(pointer_bytes) is not bytes or not 0 < len(pointer_bytes) <= 65536
            or type(log_bytes) is not bytes or not 0 < len(log_bytes) <= _Q4_ARTIFACT_BYTES
            or type(data) is not dict
            or not {"receipt", "index", "meta", "provenance"} <= set(data)
            or not set(data) <= {"receipt", "index", "meta", "provenance", "vectors"}
            or any(type(raw) is not bytes for raw in data.values())):
        raise ValueError("invalid_cell_bytes")
    data = dict(data)  # Capture immutable bytes before any provider is invoked.
    pointer = json.loads(pointer_bytes, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
    if (type(pointer) is not dict or set(pointer) != {"generation", "seq", "receipt_sha256"}
            or type(pointer["generation"]) is not int or pointer["generation"] != bound["generation"]
            or type(pointer["seq"]) is not int or pointer["seq"] < 1
            or pointer["receipt_sha256"] != bound["receipt_sha256"]):
        raise ValueError("invalid_cell_pointer")
    if "seq" in bound and (type(bound["seq"]) is not int or bound["seq"] != pointer["seq"]):
        raise ValueError("cell_sequence_mismatch")
    lines = log_bytes.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines or len(lines) > 100000:
        raise ValueError("invalid_cell_log")
    previous = "0" * 64
    found = None
    for number, raw in enumerate(lines, 1):
        line = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
        if (type(line) is not dict or line.get("schema") != "wd.bridge-mind-hex-cell-log.v1"
                or type(line.get("seq")) is not int or line["seq"] != number
                or line.get("prev_line_sha256") != previous):
            raise ValueError("invalid_cell_log")
        previous = _sha(raw)
        if number == pointer["seq"]:
            found = line
    if (found is None or type(found.get("generation")) is not int
            or found["generation"] != pointer["generation"]
            or found.get("receipt_sha256") != pointer["receipt_sha256"]):
        raise ValueError("cell_log_binding_mismatch")
    receipt = _pinned(data["receipt"], bound["receipt_sha256"], limit=1024 * 1024)
    content = receipt.get("content") if type(receipt) is dict else None
    if (type(receipt) is not dict or receipt.get("schema") != "wd.bridge-mind-hex-cell.v1"
            or receipt.get("scope") != memory["scope"] or type(content) is not dict
            or receipt.get("content_sha256") != _sha(_canonical(content))
            or content.get("cell_id") != cell_id or type(content.get("count")) is not int
            or not 0 < content["count"] <= _Q4_MAX_ROWS or content["count"] % 2
            or type(content.get("dim")) is not int or content["dim"] != _Q4_DIM
            or content.get("encoder") != memory["encoder"]
            or content.get("projection_schema") != _COMBINED_SCHEMA
            or content.get("projection_sha256") != memory["projection_sha256"]
            or not _hex(content.get("ids_sha256")) or not _hex(content.get("pairs_sha256"))
            or type(content.get("files")) is not dict):
        raise ValueError("invalid_cell_receipt")
    if "count" in bound and (type(bound["count"]) is not int or bound["count"] != content["count"]):
        raise ValueError("cell_count_mismatch")
    if content.get("cutoff_utc") != memory.get("cutoff_utc"):
        raise ValueError("cell_cutoff_mismatch")
    files = content["files"]
    if (not {"index", "meta", "provenance"} <= set(files)
            or not set(files) <= {"index", "meta", "provenance", "vectors"}
            or any(not _hex(pin) for pin in files.values())
            or len(data["index"]) > 256 * 1024 * 1024):
        raise ValueError("invalid_cell_artifacts")
    for name in ("index", "meta", "provenance", "vectors"):
        if name in data:
            if name not in files or not hmac.compare_digest(_sha(data[name]), files[name]):
                raise ValueError("cell_artifact_mismatch")
            if name in ("meta", "provenance") and len(data[name]) > _Q4_ARTIFACT_BYTES:
                raise ValueError("cell_artifact_over_budget")
    meta = _pinned(data["meta"], files["meta"], limit=_Q4_ARTIFACT_BYTES)
    count = content["count"]
    if (type(meta) is not dict or set(meta) != {"dim", "doc_ids", "texts", "metadata"}
            or type(meta["dim"]) is not int or meta["dim"] != _Q4_DIM
            or any(type(meta[name]) is not list or len(meta[name]) != count
                   for name in ("doc_ids", "texts", "metadata"))
            or any(type(value) is not str for value in meta["doc_ids"])
            or len(set(meta["doc_ids"])) != count
            or content["ids_sha256"] != _sha("\n".join(meta["doc_ids"]).encode())
            or any(type(value) is not dict for value in meta["metadata"])):
        raise ValueError("invalid_cell_meta")
    raw_rows = data["provenance"].splitlines()
    if len(raw_rows) != count:
        raise ValueError("invalid_cell_rows")
    rows, seen = [], set()
    cutoff = _utc(content.get("cutoff_utc"))
    for position, raw in enumerate(raw_rows):
        row = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
        provenance = row.get("provenance") if type(row) is dict else None
        if (type(row) is not dict or type(provenance) is not dict or row.get("kind") != "event"
                or row.get("cell_id") != cell_id or row.get("record_id") != meta["doc_ids"][position]
                or _utc(row.get("time_utc")) >= cutoff):
            raise ValueError("invalid_cell_row")
        record_id, view = provenance.get("record"), provenance.get("view")
        record = records.get(record_id) if type(record_id) is str else None
        if (record is None or view not in ("w", "d") or (record_id, view) in seen
                or row["record_id"] != record_id + "#" + view
                or row["time_utc"] != record["time_utc"]
                or _Q4_PLACEMENT.get(record["meta"]["type"]) != cell_id
                or meta["texts"][position] != decode_text(record["stored_text"])
                or provenance != dict(record["provenance"], record=record_id, view=view,
                                      ref_kind="sha256_of_projected_metadata", canonical_event=False)):
            raise ValueError("cell_source_binding_mismatch")
        seen.add((record_id, view))
        rows.append((record_id, view))
    if any((record_id, other) not in seen for record_id, _ in seen for other in ("w", "d")):
        raise ValueError("cell_view_missing")
    if "vectors" in data and len(data["vectors"]) != count * _Q4_DIM * 4:
        raise ValueError("invalid_retained_vectors")
    return data, content, meta, rows


def _q4_vector(raw):
    import math
    if len(raw) != _Q4_DIM:
        raise ValueError("invalid_vector_dimension")
    vector = []
    for value in raw:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid_vector")
        vector.append(float(value))
    norm = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
        raise ValueError("vector_not_normalized")
    return tuple(vector)


def _provider_result(operation):
    # Leave the except block before denying: no provider exception is chained.
    succeeded, output = False, None
    try:
        output = operation()
        succeeded = True
    except Exception:
        pass
    operation = None
    if not succeeded:
        _deny()
    return output


def _provider_float32_scalar(value):
    import struct
    if type(value) in (int, float):
        return value
    if (getattr(getattr(value, "dtype", None), "str", None) != "<f4"
            or type(getattr(value, "shape", None)) is not tuple or value.shape != ()
            or not callable(getattr(value, "item", None))
            or not callable(getattr(value, "tobytes", None))):
        raise ValueError("invalid_float32_scalar")
    number, raw = value.item(), value.tobytes()
    if (type(number) not in (int, float) or type(raw) is not bytes or len(raw) != 4
            or struct.pack("<f", number) != raw):
        raise ValueError("invalid_float32_scalar_bits")
    return struct.unpack("<f", raw)[0]


def _provider_float32_array(value, shape):
    # Inspect bounded dimensions and dtype before asking a native object to copy.
    actual = getattr(value, "shape", None)
    if (getattr(getattr(value, "dtype", None), "str", None) != "<f4"
            or type(actual) is not tuple or len(actual) != len(shape)
            or any(type(n) is not int for n in actual)
            or actual != shape or not callable(getattr(value, "tolist", None))
            or not callable(getattr(value, "tobytes", None))):
        raise ValueError("invalid_float32_array")
    values, raw = value.tolist(), value.tobytes()
    size = 4
    for dimension in shape:
        size *= dimension
    if type(values) is not list or type(raw) is not bytes or len(raw) != size:
        raise ValueError("invalid_float32_array_bytes")
    return values, raw


def _provider_vector(value):
    import struct
    raw = None
    if type(value) not in (tuple, list):
        value, raw = _provider_float32_array(value, (_Q4_DIM,))
    if len(value) != _Q4_DIM:
        raise ValueError("invalid_vector_dimension")
    vector = _q4_vector(tuple(_provider_float32_scalar(number) for number in value))
    if raw is not None and struct.pack("<" + "f" * _Q4_DIM, *vector) != raw:
        raise ValueError("invalid_float32_array_bits")
    return _q4_vector(struct.unpack("<" + "f" * _Q4_DIM, raw)) if raw is not None else vector


def _provider_matrix(value, count):
    import struct
    raw = None
    if type(value) not in (tuple, list):
        value, raw = _provider_float32_array(value, (count, _Q4_DIM))
    if len(value) != count:
        raise ValueError("invalid_matrix_count")
    rows = tuple(_provider_vector(row) for row in value)
    if raw is not None:
        # Compare per row without constructing a second full matrix buffer.
        width = _Q4_DIM * 4
        converted = []
        for position, row in enumerate(rows):
            if struct.pack("<" + "f" * _Q4_DIM, *row) != raw[position * width:(position + 1) * width]:
                raise ValueError("invalid_float32_matrix_bits")
            converted.append(_q4_vector(struct.unpack(
                "<" + "f" * _Q4_DIM, raw[position * width:(position + 1) * width])))
        rows = tuple(converted)
    return rows


def _provider_index_controls(index):
    dimension, count, metric = (getattr(index, name, None) for name in ("d", "ntotal", "metric_type"))
    if (type(dimension) is not int or dimension != _Q4_DIM
            or type(count) is not int or not 0 < count <= _Q4_MAX_ROWS
            or type(metric) is not int or metric != 0
            or not callable(getattr(index, "reconstruct_n", None))):
        raise ValueError("invalid_decoded_index")
    return dimension, count, metric


class _ReadonlyEncoderAdapter:
    __slots__ = ("_encoder", "_identity_bytes")

    def __init__(self, encoder, binding):
        self._encoder, self._identity_bytes = encoder, _canonical(binding)

    @property
    def identity(self):
        return json.loads(self._identity_bytes)

    def _query(self, text):
        if type(text) is not str or not 0 < len(text) <= 4000:
            raise ValueError("invalid_query")
        # The trusted encoder owns the approved query prefix and normalization.
        # Passing raw text avoids adding the E5 prefix twice.
        return _provider_vector(self._encoder.query(text))

    def query(self, text):
        return _provider_result(lambda: self._query(text))


class _ReadonlyDecodedIndex:
    __slots__ = ("_index", "_controls")

    def __init__(self, index):
        self._controls = _provider_index_controls(index)
        self._index = index

    @property
    def d(self):
        return self._controls[0]

    @property
    def ntotal(self):
        return self._controls[1]

    @property
    def metric_type(self):
        return self._controls[2]

    def _reconstruct(self, start, count):
        if (type(start) is not int or type(count) is not int
                or start != 0 or count != self.ntotal
                or _provider_index_controls(self._index) != self._controls):
            raise ValueError("invalid_reconstruction_range")
        rows = _provider_matrix(self._index.reconstruct_n(start, count), count)
        if _provider_index_controls(self._index) != self._controls:
            raise ValueError("decoded_index_changed")
        return rows

    def reconstruct_n(self, start, count):
        return _provider_result(lambda: self._reconstruct(start, count))


class _ReadonlyIndexAdapter:
    __slots__ = ("_decoder", "_identity_bytes", "_index_sha256s")

    def __init__(self, decoder, binding, index_sha256s):
        self._decoder, self._identity_bytes = decoder, _canonical(binding)
        self._index_sha256s = frozenset(index_sha256s)

    @property
    def identity(self):
        return json.loads(self._identity_bytes)

    def _load(self, raw):
        if (type(raw) is not bytes or not 0 < len(raw) <= 256 * 1024 * 1024
                or _sha(raw) not in self._index_sha256s):
            raise ValueError("unverified_index_bytes")
        return _ReadonlyDecodedIndex(self._decoder(raw))

    def load(self, raw):
        return _provider_result(lambda: self._load(raw))


def _adapt_readonly_providers(encoder, index_decoder, encoder_binding_bytes, encoder_binding_sha256,
                             index_binding_bytes, index_binding_sha256, index_sha256s):
    encoder_binding = _pinned(encoder_binding_bytes, encoder_binding_sha256)
    index_binding = _pinned(index_binding_bytes, index_binding_sha256)
    _q4_bindings(encoder_binding, index_binding)
    if (isinstance(encoder, type) or not callable(getattr(encoder, "query", None))
            or isinstance(index_decoder, type) or not callable(index_decoder)
            or type(index_sha256s) not in (tuple, list, set, frozenset)
            or not 0 < len(index_sha256s) <= len(_Q4_CELLS)
            or any(not _hex(digest) for digest in index_sha256s)):
        raise ValueError("invalid_provider_inputs")
    return (_ReadonlyEncoderAdapter(encoder, encoder_binding),
            _ReadonlyIndexAdapter(index_decoder, index_binding, index_sha256s))


def adapt_readonly_providers(*, encoder, index_decoder, encoder_binding_bytes, encoder_binding_sha256,
                            index_binding_bytes, index_binding_sha256, index_sha256s):
    """Adapt caller-trusted objects to the strict read-only semantic ABI.

    No encoder/index provider is constructed here. The supplied query object and
    byte-only decoder execute caller-trusted code; protocol, metadata and hashes
    prove consistency, never object/code authentication or execution isolation.
    Only little-endian float32 arrays/scalars with exact shape and byte roundtrip
    are converted. Builtin sequences retain the reader's finite/norm/size checks.
    No casting, normalization, native imports, model load or fallback is added.
    Use the existing query_content_snapshot boundary for source/provenance
    validation; these facades alone do not authorize any source or runtime use.
    """
    return _provider_result(lambda: _adapt_readonly_providers(
        encoder, index_decoder, encoder_binding_bytes, encoder_binding_sha256,
        index_binding_bytes, index_binding_sha256, index_sha256s))


def _q4_decode_cells(captured, provider, records):
    import struct
    entries = {}
    for cell_id, (data, content, meta, rows) in captured.items():
        index = provider.load(data["index"])
        if (type(getattr(index, "d", None)) is not int or index.d != _Q4_DIM
                or type(getattr(index, "ntotal", None)) is not int or index.ntotal != content["count"]
                or type(getattr(index, "metric_type", None)) is not int or index.metric_type != 0
                or not callable(getattr(index, "reconstruct_n", None))):
            raise ValueError("invalid_decoded_index")
        vectors = index.reconstruct_n(0, content["count"])
        if len(vectors) != content["count"]:
            raise ValueError("invalid_decoded_count")
        pairs = hashlib.sha256()
        for position, (record_id, view) in enumerate(rows):
            vector = _q4_vector(vectors[position])
            raw = struct.pack("<" + "f" * _Q4_DIM, *vector)
            pairs.update(meta["doc_ids"][position].encode() + b"\x00" + raw)
            if "vectors" in data and data["vectors"][position * _Q4_DIM * 4:(position + 1) * _Q4_DIM * 4] != raw:
                raise ValueError("retained_vector_mismatch")
            entry = entries.setdefault(record_id, {})
            if view in entry:
                raise ValueError("duplicate_memory_view")
            entry[view] = vector
        if not hmac.compare_digest(pairs.hexdigest(), content["pairs_sha256"]):
            raise ValueError("decoded_pairs_mismatch")
    if set(entries) != set(records) or any(set(entry) != {"w", "d"} for entry in entries.values()):
        raise ValueError("incomplete_memory")
    return entries


def _q4_self_match(query, text):
    words = _Q4_WORD.findall(query.lower())
    content = _Q4_WORD.findall(text.lower())
    if not words:
        return False
    if len(words) < 8:
        return (" " + " ".join(words) + " ") in (" " + " ".join(content) + " ")
    wanted = {" ".join(words[i:i + 8]) for i in range(len(words) - 7)}
    stored = {" ".join(content[i:i + 8]) for i in range(len(content) - 7)}
    return len(wanted & stored) >= 0.5 * len(wanted)


def _q4_rank(records, entries, encoder, query, when):
    import math
    from collections import Counter
    ordered = sorted(records)
    texts = {key: decode_text(records[key]["stored_text"]) for key in ordered}
    limit = when - dt.timedelta(seconds=1)
    eligible = []
    for key in ordered:
        provenance = records[key]["provenance"]
        if _utc(provenance["available_utc"]) >= limit or _q4_self_match(query, texts[key]):
            continue
        if "valid_until_utc" in provenance and when >= _utc(provenance["valid_until_utc"]):
            continue
        eligible.append(key)
    if len(eligible) <= 3:
        return []
    vector = _q4_vector(encoder.query(query))
    scores = {view: {key: sum(a * b for a, b in zip(entries[key][view], vector))
                     for key in eligible} for view in ("w", "d")}
    tokens = {key: Counter(_Q4_TOKEN.findall(texts[key].lower())) for key in ordered}
    lengths = {key: sum(tokens[key].values()) for key in ordered}
    average = sum(lengths.values()) / len(ordered)
    lexical = {key: 0.0 for key in eligible}
    for token in sorted(set(_Q4_TOKEN.findall(query.lower()))):
        frequency = sum(token in tokens[key] for key in ordered)
        if not frequency:
            continue
        idf = math.log(1.0 + (len(ordered) - frequency + 0.5) / (frequency + 0.5))
        for key in eligible:
            count = tokens[key][token]
            if count:
                norm = count + 1.2 * (1.0 - 0.75 + 0.75 * lengths[key] / average)
                lexical[key] += idf * count * 2.2 / norm
    scores["b"] = lexical
    ranks = {}
    for view, values in scores.items():
        ranks[view] = {key: number for number, key in enumerate(
            sorted(eligible, key=lambda key: (-values[key], key)), 1)}
        if view == "b":
            for key in eligible:
                if values[key] <= 0.0:
                    ranks[view][key] = max(len(eligible), 3) + 1
    agreed = [key for key in eligible if all(ranks[view][key] <= 3 for view in ("w", "d", "b"))]
    return sorted(agreed, key=lambda key: (
        -sum(1.0 / (60 + ranks[view][key]) for view in ("w", "d", "b")), key))[:3]


def _query_content_snapshot(values):
    descriptor, memory, encoder_binding, index_binding = _q4_controls(values)
    snapshot = {key: values[key] for key in _Q4_SOURCE_KEYS}
    projection, context, initial_report = _verified(snapshot)
    record_list = projection["messages"]["records"]
    records = {record["record_id"]: record for record in record_list}
    if not records or len(records) != len(record_list) or len(records) * 2 > _Q4_MAX_ROWS:
        raise ValueError("invalid_memory_record_count")
    if (type(memory.get("records")) is not int or memory["records"] != len(records)
            or memory.get("views") != ["w", "d"] or memory.get("independent_views") is not False
            or memory.get("cutoff_utc") != max(record["available_utc"] for record in record_list)):
        raise ValueError("memory_record_binding_mismatch")
    captured, total = {}, len(values["memory_bytes"]) + len(values["projection_bytes"])
    for cell_id in sorted(memory["cells"]):
        item = values["cell_snapshots"][cell_id]
        # All cells' bounded controls, bytes, hashes and source metadata are
        # checked before the separate decoder loop or any query input read.
        data, content, meta, rows = _q4_cell_controls(cell_id, item, memory["cells"][cell_id], memory, records)
        total += len(item["pointer"]) + len(item["log"]) + sum(map(len, data.values()))
        if total > _Q4_TOTAL_BYTES:
            raise ValueError("memory_over_budget")
        captured[cell_id] = data, content, meta, rows
    pairs = [pair for _, _, _, rows in captured.values() for pair in rows]
    expected_pairs = {(key, view) for key in records for view in ("w", "d")}
    if len(pairs) != len(expected_pairs) or set(pairs) != expected_pairs:
        raise ValueError("incomplete_memory")
    encoder, index_provider = values["encoder_provider"], values["index_provider"]
    _q4_provider_identity(encoder, encoder_binding, "query")
    _q4_provider_identity(index_provider, index_binding, "load")
    entries = _q4_decode_cells(captured, index_provider, records)
    raw = values["stream"].read(65537)
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError("invalid_query_bytes")
    query = json.loads(raw, object_pairs_hook=_unique_pairs, parse_constant=_bad_constant)
    if (type(query) is not dict or set(query) != {"situation", "time"}
            or type(query["situation"]) is not str or not query["situation"].strip()
            or len(query["situation"]) > 4000):
        raise ValueError("invalid_query")
    when = _utc(query["time"])
    selected = _q4_rank(records, entries, encoder, query["situation"], when)
    hits = [{"ref_sha256": _sha(records[key]["provenance"]["event_ref"].encode()),
             "doc_id_sha256": _sha(key.encode()), "ref_kind": "sha256_of_projected_metadata",
             "canonical_event": False} for key in selected]
    resolved = resolve_content_results(hits, query["time"], **snapshot)
    _, _, final_report = _verified(snapshot)
    if initial_report != final_report:
        raise ValueError("source_changed")
    _q4_provider_identity(encoder, encoder_binding, "query")
    _q4_provider_identity(index_provider, index_binding, "load")
    output = {"schema": "wd.q4-content-snapshot-query.v2", "status": "ANSWER" if resolved else "ABSTAIN",
        "rule": "q4-rule-v1.2", "results": resolved, "binding": {
            "descriptor_sha256": values["descriptor_sha256"], "memory_sha256": values["memory_sha256"],
            "projection_sha256": values["projection_sha256"], "encoder_sha256": values["encoder_sha256"],
            "encoder_binding_sha256": values["encoder_binding_sha256"],
            "index_binding_sha256": values["index_binding_sha256"], "policy_sha256": values["policy_sha256"],
            "roster_sha256": values["roster_sha256"], "decision_sha256": values["decision_sha256"],
            "approved_tasks_sha256": _RELEASE_TASK_LIST_SHA256},
        "authority": "none", "publisher_authentication": "UNKNOWN", "provider_authentication": "UNKNOWN",
        "source_filter_attestation": "UNKNOWN", "freshness": "UNKNOWN", "same_user_aba": "UNKNOWN"}
    if len(_canonical(output)) > 1024 * 1024:
        raise ValueError("query_output_over_budget")
    return output


def query_content_snapshot(**inputs):
    """Explicit frozen query; no provider construction, native import or writes.

    This implements the mathematical Q4 v1.2 rule over verified index-provider
    reconstructions, using Python scalar arithmetic. Native float32 numerical
    parity, actual provider/code/assets authenticity, isolation and deployment
    remain unproven. Metadata identity is not authentication. The caller must
    supply only independently authorized providers and runtime inputs. Provider
    methods are synchronous; the caller owns external deadlines and memory
    controls. This API installs no process watchdog.
    """
    output = None
    succeeded = False
    try:
        output = _query_content_snapshot(inputs)
        succeeded = True
    except Exception:
        pass
    inputs = None
    if not succeeded:
        output = None
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

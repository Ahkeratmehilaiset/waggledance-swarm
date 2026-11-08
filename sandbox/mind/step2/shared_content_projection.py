"""HEX MEMORY STEP 2, useful work content projection (increment 1: bridge messages). Default OFF.

Operator decision 2026-11-07 (via Lead): Mind may retain useful work-message and document content, excluding secrets,
credentials and identifiers, sealed evaluation material and unnecessary personal data. This module is the smallest
bounded opt-in route for the bridge-message half; repository documents are a later increment. It is a separate
schema beside the v6 metadata projection and the closed cards; neither of those changes.

Source boundary. Rows come only from the caller, as the mapping ``{event_ref: event}`` that the card prefix verifier
reads from a C2-pinned bridge prefix (``wd_q4_public_card_prefix_verifier._read``); every key must equal
``bridge_compact_view.event_id`` of its row, so each record binds the canonical source event ref. Eligibility reuses
the v6 checks (``shared_projection.approved_fields`` with roster authorship and review exclusion) plus the capture
adapter's RCO exclusion, and the pinned policy narrows lanes, types and approved tasks. Review lanes, RCO-addressed
rows and decision/finding/vote/veto-type or -status rows never reach the classifier and are not counted (a count
would itself be a review signal). Only the ``message`` field is read; payload, paths, write scope, identities and
unknown keys are never read.

Screening (fail closed, before any chunking, on the whole text). The text must be valid UTF-8-encodable, at most
``max_message_chars``; format characters (zero width, BOM, bidi controls) are removed and the text is NFKC
normalized, so a fullwidth or zero-width-split credential is still seen. Then, in order, the whole row drops with one
closed token: ``credential`` (key prefixes, bearer/authorization values, password/secret/token/key assignments, PEM
headers, JWTs, URL user info, hex runs of 32 or more, mixed-case base64 runs of 32 or more), ``identifier`` (UUIDs,
the build machine's user and computer names compared in memory only), ``personal`` (copies of the bridge LLM redactor's e-mail,
card, HETU, Y-tunnus and phone detectors, with ISO dates and times masked for the phone detector only, plus an IBAN
detector with a mod-97 check), ``sealed`` (sealed, gold, heldout or judge anywhere, or a workspace guarded word), ``mixed_script`` (a non-Latin
letter next to Latin text), ``encoding`` (other scripts, non-BMP characters, control characters, invalid text),
``empty`` or ``oversize``. Any detector or normalizer error drops the row as ``unknown``; KEEP is reachable only
through the success path. A credential is never redacted and kept. Absolute local paths and URLs are replaced by the
fixed words ``local-path`` and ``url``; repository-relative paths are kept. Deterministic screening has limits: it
over-blocks (for example digit groups that look like phone numbers, or "golden") and cannot prove that no sensitive
value passes; it is a screen, not a guarantee.

Text encoding ``wd.content-text.v1`` (Unicode preserved, never transliterated). The lane output class on every hop
is ``[A-Za-z0-9._: -]{1,320}``. The NFC text is written with ``A-Z a-z 0-9 . : - space`` verbatim and every other
BMP character, including ``_``, as ``_`` plus four lowercase hex digits; ``decode_text`` reverses it strictly (a
non-canonical or malformed escape refuses). Encoded text is chunked on spaces into at most ``max_chunk_chars``
characters (an over-long word is split between characters, never inside an escape); one record per chunk with the
source ref, chunk index and count.

Removal. ``removed_source_refs`` in the pinned policy removes every chunk of a source event from the next build, and
``validate_content_projection`` refuses any projection that is not the exact re-derivation from the pinned rows and
policy, so a removed ref cannot survive. Nothing here reads files, writes, logs, posts or indexes; exceptions carry
only a closed token, never input text.
"""
import hashlib
import hmac
import json
import os
import re
import unicodedata

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import shared_capture_adapter as capture
from sandbox.mind.step2 import shared_projection as sp
from tools.bridge_compact_view import event_id

SCHEMA = "wd.bridge-mind-content-projection.v1"
POLICY_SCHEMA = "wd.mind-content-policy.v1"
TEXT_ENCODING = "wd.content-text.v1"
SCOPE = "shared_content_projection_not_gate"
SOURCE_KIND = "bridge_message"
REF_KIND = "sha256_of_content_chunk_and_source"
MAX_MESSAGE_CHARS = 4096
MAX_CHUNK_CHARS = 300
MIN_CHUNK_CHARS = 16
POLICY_LIMIT = 65536
DROP_TOKENS = frozenset({"credential", "identifier", "personal", "sealed", "mixed_script", "encoding", "empty",
                         "oversize", "unknown"})
ROW_TOKENS = frozenset({"lane_not_allowed", "type_not_allowed", "task_not_approved", "removed", "no_message"})
_SILENT = frozenset({"review_lane_excluded", "pr_branch_task_excluded"})
_POLICY_KEYS = frozenset({"schema", "lanes", "types", "approved_tasks", "removed_source_refs", "max_message_chars",
                          "max_chunk_chars"})
_PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "policy_sha256", "text_encoding", "accepted",
                              "refused", "records", "authorship", "review_signal_excluded"})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
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
# Copies of the bridge LLM redactor's personal-data detectors (waggledance/core/bridge_llm/redactor.py): importing
# that module runs the bridge_llm package, which loads the LLM provider clients that the bridge code package does
# not ship. test_shared_content_projection pins every copy to the redactor's pattern and flags.
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


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError()
        out[key] = value
    return out


# --- policy ----------------------------------------------------------------------------------------------------------

def _string_set(value, check):
    if type(value) is not list or any(type(item) is not str or not check(item) for item in value):
        raise ValueError()
    if len(set(value)) != len(value):
        raise ValueError()
    return frozenset(value)


def _bounded_int(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError()
    return value


def load_content_policy(policy_bytes, policy_sha256):
    """The pinned policy: exact keys, closed values; any unknown, unsafe or widening value refuses the build."""
    failed = False
    try:
        if (type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= POLICY_LIMIT
                or type(policy_sha256) is not str or _HEX64.fullmatch(policy_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(policy_bytes).hexdigest(), policy_sha256)):
            raise ValueError()
        doc = json.loads(policy_bytes.decode("ascii"), object_pairs_hook=_unique_pairs)
        if type(doc) is not dict or set(doc) != _POLICY_KEYS or doc["schema"] != POLICY_SCHEMA:
            raise ValueError()
        lanes = _string_set(doc["lanes"], lambda lane: lane in sp.LANES and lane not in sp.REVIEW_LANES)
        types = _string_set(doc["types"], lambda kind: kind in sp.ALLOWED_TYPES and kind not in sp.EXCLUDED_TYPES)
        tasks = _string_set(doc["approved_tasks"], lambda task: sp.TASK.fullmatch(task) is not None
                            and not any(marker in task for marker in sp.SEALED_MARKERS))
        removed = _string_set(doc["removed_source_refs"], lambda ref: _HEX64.fullmatch(ref) is not None)
        policy = {"lanes": lanes, "types": types, "approved_tasks": tasks, "removed_source_refs": removed,
                  "max_message_chars": _bounded_int(doc["max_message_chars"], 1, MAX_MESSAGE_CHARS),
                  "max_chunk_chars": _bounded_int(doc["max_chunk_chars"], MIN_CHUNK_CHARS, MAX_CHUNK_CHARS)}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        failed = True
    if failed:
        _refuse("policy_refused")
    return policy


# --- text encoding wd.content-text.v1 ------------------------------------------------------------------------------

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


# --- screening ---------------------------------------------------------------------------------------------------------

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


def _local_identifiers():
    names = []
    for key in ("USERNAME", "USER", "COMPUTERNAME", "HOSTNAME"):
        value = os.environ.get(key, "")
        if len(value) >= 3:
            names.append(value.lower())
    profile = os.environ.get("USERPROFILE", "") or os.environ.get("HOME", "")
    base = os.path.basename(profile.rstrip("\\/"))
    if len(base) >= 3:
        names.append(base.lower())
    return names


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
        if _UUID.search(text) or (identifier_policy.contains(text) if identifier_policy is not None
                                  else any(name in lowered for name in _local_identifiers())):
            raise _Drop("identifier")
        if any(marker in lowered for marker in sp.SEALED_MARKERS) or corpus_mod.ws._has_marker(text):
            raise _Drop("sealed")
        phone_text = _CREDENTIAL[-1].sub("hex-redacted", text) if redact_hex else text
        if (any(pattern.search(text) for pattern in _PERSONAL) or _iban(text)
                or _PHONE_RE.search(_ISO_TIME.sub(" ", phone_text))):
            raise _Drop("personal")
        if redact_hex:
            text = _redact_sensitive_runs(text)
        lowered = text.lower()
        if _UUID.search(text) or (identifier_policy.contains(text) if identifier_policy is not None
                                  else any(name in lowered for name in _local_identifiers())):
            raise _Drop("identifier")
        if (any(pattern.search(text) for pattern in _PERSONAL) or _iban(text)
                or _PHONE_RE.search(_ISO_TIME.sub(" ", text))):
            raise _Drop("personal")
        if any(marker in lowered for marker in sp.SEALED_MARKERS) or corpus_mod.ws._has_marker(text):
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


def classify(raw_text, limit=MAX_MESSAGE_CHARS, *, require_identifier_policy=False, identifier_policy_binding=None):
    """Classify without logging; the opt-in list must include every required local identifier.

    The pinned list replaces environment-derived names. Missing/invalid binding
    refuses a row, but is not a builder-level validation or activation gate.
    """
    token = encoded = text = policy = None
    try:
        if type(require_identifier_policy) is not bool:
            raise _Drop("unknown")
        if require_identifier_policy:
            if type(identifier_policy_binding) is not tuple or len(identifier_policy_binding) != 3:
                raise _Drop("unknown")
            from sandbox.mind.step2.content_identifier_policy import load_policy
            policy = load_policy(*identifier_policy_binding)
        elif identifier_policy_binding is not None:
            raise _Drop("unknown")
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
        raw_text = text = policy = identifier_policy_binding = None
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


# --- projection ----------------------------------------------------------------------------------------------------

def _eligible(event, policy, roster):
    """Approved fields of an eligible row, ``None`` for a silently excluded review-signal row, or a row token."""
    if capture._exclude_rco_event(event) or capture._exclude_review_signal(event):
        return None
    try:
        fields, _ = sp.approved_fields(event, policy["approved_tasks"], False, roster, True)
    except sp.ProjectionRefused as exc:
        return None if str(exc) in _SILENT else str(exc)
    if fields["agent"] not in policy["lanes"]:
        return "lane_not_allowed"
    if fields["type"] not in policy["types"]:
        return "type_not_allowed"
    if fields["task_id"] == sp.UNKNOWN:
        return "task_not_approved"
    return fields


def _records(source_ref, fields, chunks):
    available = sp._available(fields["ts_utc"])
    out = []
    for index, text in enumerate(chunks):
        ref = hashlib.sha256(_canonical({"text": text, "source": source_ref, "chunk": index})).hexdigest()
        out.append({"record_id": "content:" + ref, "kind": "event", "time_utc": fields["ts_utc"],
                    "available_utc": available, "stored_text": text, "summary": text,
                    "meta": {"agent": fields["agent"], "type": fields["type"], "task_id": fields["task_id"],
                             "text_encoding": TEXT_ENCODING},
                    "provenance": {"source_kind": SOURCE_KIND, "event_ref": ref, "ref_kind": REF_KIND,
                                   "canonical_event": False, "source_event_ref": source_ref,
                                   "chunk_index": index, "chunk_count": len(chunks),
                                   "tier": {"source_type": "bridge_event"}, "available_utc": available}})
    return out


def _build(rows, policy_bytes, policy_sha256, roster):
    policy = load_content_policy(policy_bytes, policy_sha256)
    try:
        roster = sp._roster(roster)
    except sp.ProjectionRefused:
        roster = None
    if roster is None:
        _refuse("roster_refused")
    if type(rows) is not dict:
        _refuse("rows_refused")
    for ref, event in rows.items():
        if type(ref) is not str or _HEX64.fullmatch(ref) is None or type(event) is not dict:
            _refuse("rows_refused")
        try:
            same = event_id(event) == ref
        except (TypeError, ValueError, RecursionError):
            same = False
        if not same:
            _refuse("rows_refused")
    records, refused = [], {}
    for ref in sorted(rows):
        event = rows[ref]
        fields = _eligible(event, policy, roster)
        if fields is None:
            continue
        if type(fields) is str:
            refused[fields] = refused.get(fields, 0) + 1
            continue
        if ref in policy["removed_source_refs"]:
            refused["removed"] = refused.get("removed", 0) + 1
            continue
        message = event.get("message")
        if message is None or message == "":
            refused["no_message"] = refused.get("no_message", 0) + 1
            continue
        verdict, value = classify(message, policy["max_message_chars"])
        if verdict != "KEEP":
            refused[value] = refused.get(value, 0) + 1
            continue
        chunks = chunk(value, policy["max_chunk_chars"])
        if not chunks or any(_OUTPUT.fullmatch(text) is None for text in chunks):
            refused["encoding"] = refused.get("encoding", 0) + 1
            continue
        if any(corpus_mod.ws._has_marker(text) for text in chunks):
            refused["sealed"] = refused.get("sealed", 0) + 1
            continue
        records.extend(_records(ref, fields, chunks))
    records.sort(key=lambda record: (record["time_utc"], record["provenance"]["source_event_ref"],
                                     record["provenance"]["chunk_index"]))
    projection = {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "policy_sha256": policy_sha256,
                  "text_encoding": TEXT_ENCODING, "accepted": len(records), "refused": dict(sorted(refused.items())),
                  "records": records, "authorship": sp.AUTHORSHIP_ROSTER, "review_signal_excluded": True}
    corpus_mod.ws.guard_object(projection)
    return projection


def build_content_projection(*, enabled=False, rows=None, policy_bytes=None, policy_sha256=None, roster=None):
    """Default OFF. With ``enabled=True``: the content projection of the caller-pinned prefix rows under the pinned
    policy and roster. Row-level problems are counted by closed token; input-level problems refuse the build."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        _refuse("enabled_invalid")
    return _build(rows, policy_bytes, policy_sha256, roster)


def validate_content_projection(projection, *, rows, policy_bytes, policy_sha256, roster):
    """Refuse unless ``projection`` is exactly the re-derivation from the pinned rows, policy and roster."""
    if type(projection) is not dict or set(projection) != _PROJECTION_KEYS:
        _refuse("projection_mismatch")
    expected = _build(rows, policy_bytes, policy_sha256, roster)
    try:
        same = hmac.compare_digest(_canonical(projection), _canonical(expected))
    except (TypeError, ValueError, RecursionError):
        same = False
    if not same:
        _refuse("projection_mismatch")
    return None

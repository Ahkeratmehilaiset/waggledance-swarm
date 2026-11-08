"""HEX MEMORY STEP 2, useful work content projection (increment 2: exact-allowlisted repository documents). Default OFF.

Operator decision 2026-11-07 (via Lead, scope decision 18:16Z): Mind may retain useful document content, excluding
secrets, credentials and identifiers, sealed evaluation material and unnecessary personal data. This module is the
repository-document half beside ``shared_content_projection`` (bridge messages); it reuses that module's screen,
``wd.content-text.v1`` encoding and chunker unchanged and adds no resolver, transport, policy file or builder entry.

Source boundary. The pinned policy names one full commit id and an exact list of repository-relative paths; nothing
else is read. A path is plain ASCII segments (``[A-Za-z0-9][A-Za-z0-9._-]*``, so no dot-files, ``..``, drive,
backslash, space or pathspec magic), ends in ``.md``, ``.txt`` or ``.rst`` and holds no excluded segment (memory,
memories, security, codex-audit, agent-bridge) or excluded word (the sealed markers, approval, secret, credential,
password, passwd, token, private, ``.env``, ``.pem``, ``.key``, ``id_rsa`` or a workspace guarded word); any unclear
path refuses the whole policy rather than indexing unknown content. Content is read only as git objects of the pinned
commit (``cat-file`` / ``ls-tree``), never from the worktree, with every inherited ``GIT_*`` variable dropped, global
and system config off, replace objects off, literal pathspecs and a ceiling directory so the repository must be the
given root. Each path must be a regular ``100644`` blob: a symlink, submodule, executable, tree or missing path is
counted by a closed token and never followed.

The caller must supply an absolute Git executable path and its SHA-256 digest; this module never resolves Git from
PATH. The executable is checked before each call. Commit, tree and blob bytes are independently rehashed using Git's
SHA-1 object framing and tree paths are walked from the pinned commit. These checks do not authenticate the caller,
the executable publisher or Git's helpers, and do not prevent same-user executable replacement races.

Screening. A document that is larger than ``max_doc_bytes``, holds a NUL byte or is not strict UTF-8 is counted and
skipped. The whole normalized document is then checked once: named credential shapes (key
prefixes, bearer/authorization values, secret assignments, PEM headers, JWTs and URL user info)
or any sealed marker or workspace guarded word drops the whole document, so a secret or sealed fragment split over
sections still taints it. The rest is split into sections on blank lines (an over-long section on line boundaries)
and each section goes through ``shared_content_projection.classify`` with its full screen, so one personal,
identifier or mixed-script section drops alone. Ambiguous long runs are redacted by the section screen before records
are produced. A long token-like line followed by a token-like continuation refuses the whole document. Deterministic
screening over-blocks and is a screen, not a guarantee.

Records and removal. Each kept section is encoded, chunked and written as records with the commit, path, blob id,
section and chunk position and a source ref ``sha256({blob, commit, path})``; the record time is the pinned commit's
committer time. ``removed_blobs`` in the pinned policy removes every record of a blob (so the content stays removed
across later commits until it changes), and ``validate_doc_projection`` refuses any projection that is not the exact
re-derivation. Nothing here writes, logs, posts or indexes; exceptions carry only a closed token.
"""
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import subprocess

from sandbox.mind.step2 import corpus as corpus_mod
from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_projection as sp

SCHEMA = "wd.repo-doc-content-projection.v1"
POLICY_SCHEMA = "wd.mind-doc-policy.v1"
SCOPE = "shared_doc_projection_not_gate"
SOURCE_KIND = "repo_doc"
REF_KIND = "sha256_of_doc_chunk_and_source"
MAX_SECTION_CHARS = cp.MAX_MESSAGE_CHARS
MAX_DOC_BYTES = 262144
MAX_PATHS = 256
GIT_TIMEOUT_SECONDS = 60
DOC_TOKENS = frozenset({"path_missing", "not_blob", "symlink", "submodule", "not_regular", "binary", "encoding",
                        "oversize", "credential", "sealed", "removed", "empty", "unknown"})
_POLICY_KEYS = frozenset({"schema", "commit", "paths", "removed_blobs", "max_section_chars", "max_chunk_chars",
                          "max_doc_bytes"})
_PROJECTION_KEYS = frozenset({"schema", "authority", "scope", "policy_sha256", "commit", "text_encoding", "accepted",
                              "refused_docs", "refused_sections", "records"})
_HEX40 = re.compile(r"[0-9a-f]{40}")
_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
_SUFFIXES = (".md", ".txt", ".rst")
_EXCLUDED_SEGMENTS = frozenset({"memory", "memories", "security", "codex-audit", "agent-bridge"})
_EXCLUDED_WORDS = tuple(sp.SEALED_MARKERS) + ("approval", "secret", "credential", "password", "passwd", "token",
                                              "private", ".env", ".pem", ".key", "id_rsa")
_MODES = {b"100644": None, b"100755": "not_regular", b"120000": "symlink", b"160000": "submodule"}
_COMMITTER = re.compile(rb"committer [^\n]* ([0-9]{1,12}) [+-][0-9]{4}")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


class DocRefused(ValueError):
    """One fixed reason token; never the offending value."""


class _GitFailed(Exception):
    pass


def _refuse(token):
    raise DocRefused(token)


# --- policy ----------------------------------------------------------------------------------------------------------

def _doc_path(path):
    """True only for a plain, allowlist-safe repository-relative document path."""
    if type(path) is not str or not 0 < len(path) <= 200:
        return False
    segments = path.split("/")
    if any(_SEGMENT.fullmatch(segment) is None for segment in segments):
        return False
    if not segments[-1].endswith(_SUFFIXES):
        return False
    lowered = path.lower()
    if any(segment.lower() in _EXCLUDED_SEGMENTS for segment in segments):
        return False
    if any(word in lowered for word in _EXCLUDED_WORDS) or corpus_mod.ws._has_marker(path):
        return False
    return True


def load_doc_policy(policy_bytes, policy_sha256):
    """The pinned policy: exact keys, closed values; any unknown, unsafe or unclear value refuses the build."""
    failed = False
    try:
        if (type(policy_bytes) is not bytes or not 0 < len(policy_bytes) <= cp.POLICY_LIMIT
                or type(policy_sha256) is not str or cp._HEX64.fullmatch(policy_sha256) is None
                or not hmac.compare_digest(hashlib.sha256(policy_bytes).hexdigest(), policy_sha256)):
            raise ValueError()
        doc = json.loads(policy_bytes.decode("ascii"), object_pairs_hook=cp._unique_pairs)
        if type(doc) is not dict or set(doc) != _POLICY_KEYS or doc["schema"] != POLICY_SCHEMA:
            raise ValueError()
        if type(doc["commit"]) is not str or _HEX40.fullmatch(doc["commit"]) is None:
            raise ValueError()
        paths = cp._string_set(doc["paths"], _doc_path)
        if not 0 < len(paths) <= MAX_PATHS:
            raise ValueError()
        removed = cp._string_set(doc["removed_blobs"], lambda blob: _HEX40.fullmatch(blob) is not None)
        policy = {"commit": doc["commit"], "paths": paths, "removed_blobs": removed,
                  "max_section_chars": cp._bounded_int(doc["max_section_chars"], 1, MAX_SECTION_CHARS),
                  "max_chunk_chars": cp._bounded_int(doc["max_chunk_chars"], cp.MIN_CHUNK_CHARS, cp.MAX_CHUNK_CHARS),
                  "max_doc_bytes": cp._bounded_int(doc["max_doc_bytes"], 1, MAX_DOC_BYTES)}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        failed = True
    if failed:
        _refuse("policy_refused")
    return policy


# --- git object reads ----------------------------------------------------------------------------------------------

def _git_env(root, git_executable=None, git_sha256=None):
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "GIT_LITERAL_PATHSPECS": "1",
                "GIT_CEILING_DIRECTORIES": os.path.dirname(root)})
    env["WD_DOC_GIT_EXECUTABLE"] = git_executable or ""
    env["WD_DOC_GIT_SHA256"] = git_sha256 or ""
    return env


def _git(root, env, *args):
    try:
        executable = env.get("WD_DOC_GIT_EXECUTABLE", "")
        expected = env.get("WD_DOC_GIT_SHA256", "")
        if (type(executable) is not str or type(expected) is not str or not os.path.isabs(executable)
                or cp._HEX64.fullmatch(expected) is None):
            raise ValueError()
        with open(executable, "rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if not hmac.compare_digest(digest, expected):
            raise ValueError()
        result = subprocess.run([executable, "-C", root, *args], stdin=subprocess.DEVNULL, capture_output=True, env=env,
                                timeout=GIT_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise _GitFailed() from None
    if result.returncode != 0:
        raise _GitFailed()
    return result.stdout


def _open_repo(repo_root, git_executable, git_sha256):
    if type(repo_root) is not str or not repo_root or "\0" in repo_root or not os.path.isdir(repo_root):
        _refuse("repo_refused")
    root = os.path.abspath(repo_root)
    env = _git_env(root, git_executable, git_sha256)
    try:
        _git(root, env, "rev-parse", "--git-dir")
    except _GitFailed:
        _refuse("repo_refused")
    return root, env


def _object(root, env, kind, oid):
    data = _git(root, env, "cat-file", kind, oid)
    actual = hashlib.sha1(kind.encode("ascii") + b" " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if not hmac.compare_digest(actual, oid):
        raise _GitFailed()
    return data


def _commit_time(root, env, commit):
    match = None
    try:
        if _git(root, env, "cat-file", "-t", commit) == b"commit\n":
            header = _object(root, env, "commit", commit).split(b"\n\n", 1)[0]
            match = [m for m in (_COMMITTER.fullmatch(line) for line in header.split(b"\n")) if m]
    except _GitFailed:
        match = None
    if not match or len(match) != 1:
        _refuse("commit_refused")
    try:
        when = dt.datetime.fromtimestamp(int(match[0].group(1)), dt.timezone.utc)
    except (OverflowError, ValueError, OSError):
        _refuse("commit_refused")
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry(root, env, commit, path):
    """``(blob_id, None)`` for a regular blob at exactly ``path``, else ``(None, token)``."""
    if not path or path.endswith("/"):
        return None, "path_missing"
    header = _object(root, env, "commit", commit).split(b"\n\n", 1)[0]
    roots = [line[5:] for line in header.split(b"\n") if line.startswith(b"tree ")]
    if len(roots) != 1 or _HEX40.fullmatch(roots[0].decode("ascii", "replace")) is None:
        raise _GitFailed()
    tree = roots[0].decode("ascii")
    parts = path.split("/")
    for index, part in enumerate(parts):
        data = _object(root, env, "tree", tree)
        found = []
        pos = 0
        while pos < len(data):
            space = data.find(b" ", pos)
            end = data.find(b"\0", space + 1) if space >= 0 else -1
            if space < 0 or end < 0 or end + 21 > len(data):
                raise _GitFailed()
            mode, name, oid = data[pos:space], data[space + 1:end], data[end + 1:end + 21].hex()
            if name == part.encode("ascii"):
                found.append((mode, oid))
            pos = end + 21
        if not found:
            return None, "path_missing"
        if len(found) != 1:
            raise _GitFailed()
        mode, oid = found[0]
        if index < len(parts) - 1:
            if mode not in (b"40000", b"040000"):
                return None, "path_missing"
            tree = oid
    kind = b"blob" if mode in (b"100644", b"100755", b"120000") else b"other"
    if mode in _MODES and _MODES[mode] is not None:
        return None, _MODES[mode]
    if mode != b"100644" or kind != b"blob" or _HEX40.fullmatch(oid) is None:
        return None, "not_blob"
    return oid, None


def _read_blob(root, env, oid, limit):
    """``(text, None)`` or ``(None, token)``; the size is checked before the content is read."""
    try:
        size = int(_git(root, env, "cat-file", "-s", oid).decode("ascii").strip())
    except ValueError:
        raise _GitFailed() from None
    if size > limit:
        return None, "oversize"
    data = _object(root, env, "blob", oid)
    if len(data) != size:
        raise _GitFailed()
    if b"\0" in data:
        return None, "binary"
    try:
        return data.decode("utf-8", "strict"), None
    except UnicodeError:
        return None, "encoding"


# --- screening and sections ----------------------------------------------------------------------------------------

def _wrapped_long_run(text, limit):
    """Fail closed when a long token-like line has a token-like continuation."""
    preceding_long = False
    for raw_line in text.splitlines():
        line = cp._normalize(raw_line, limit)
        if preceding_long and re.fullmatch(r"[A-Za-z0-9+/_=-]{8,}", line):
            return True
        preceding_long = cp._BASE64_RUN.fullmatch(line) is not None
    return False


def _doc_screen(text, limit):
    """Whole-document taint check; raises ``cp._Drop`` with ``credential``, ``sealed`` or a normalizer token."""
    normalized = cp._normalize(text, limit)
    if not normalized:
        raise cp._Drop("empty")
    # Named credentials still taint the whole document. Ambiguous long runs
    # are not trusted: each retained section passes classify(), which redacts
    # the entire matched run before a record is constructed.
    if (any(pattern.search(normalized) for pattern in cp._CREDENTIAL[:-1])
            or cp._OWNER_HEX.search(normalized) or _wrapped_long_run(text, limit)):
        raise cp._Drop("credential")
    lowered = normalized.lower()
    if any(marker in lowered for marker in sp.SEALED_MARKERS) or corpus_mod.ws._has_marker(normalized):
        raise cp._Drop("sealed")


def _sections(text, limit):
    """Blank-line sections; a section over ``limit`` characters is split on line boundaries."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out = []
    for block in _BLANK_LINE.split(text):
        if not block.strip():
            continue
        if len(block) <= limit:
            out.append(block)
            continue
        current = None
        for line in block.split("\n"):
            if not line.strip():
                continue
            candidate = line if current is None else current + "\n" + line
            if len(candidate) <= limit:
                current = candidate
                continue
            if current is not None:
                out.append(current)
            current = line
        if current is not None:
            out.append(current)
    return out


def _records(commit, path, oid, time_text, section_index, chunks):
    source = hashlib.sha256(cp._canonical({"blob": oid, "commit": commit, "path": path})).hexdigest()
    available = sp._available(time_text)
    out = []
    for index, text in enumerate(chunks):
        ref = hashlib.sha256(cp._canonical({"text": text, "source": source, "section": section_index,
                                            "chunk": index})).hexdigest()
        out.append({"record_id": "doc:" + ref, "kind": "document", "time_utc": time_text,
                    "available_utc": available, "stored_text": text, "summary": text,
                    "meta": {"path": path, "text_encoding": cp.TEXT_ENCODING},
                    "provenance": {"source_kind": SOURCE_KIND, "event_ref": ref, "ref_kind": REF_KIND,
                                   "canonical_event": False, "source_doc_ref": source, "commit": commit,
                                   "path": path, "blob_id": oid, "section_index": section_index,
                                   "chunk_index": index, "chunk_count": len(chunks),
                                   "tier": {"source_type": "repo_doc"}, "available_utc": available}})
    return out


def _count(counts, token):
    counts[token] = counts.get(token, 0) + 1


def _document(root, env, policy, path, time_text, refused_docs, refused_sections):
    commit = policy["commit"]
    oid, token = _entry(root, env, commit, path)
    if token is not None:
        return _count(refused_docs, token) or []
    if oid in policy["removed_blobs"]:
        return _count(refused_docs, "removed") or []
    text, token = _read_blob(root, env, oid, policy["max_doc_bytes"])
    if token is not None:
        return _count(refused_docs, token) or []
    try:
        _doc_screen(text, policy["max_doc_bytes"])
        sections = _sections(text, policy["max_section_chars"])
    except cp._Drop as exc:
        return _count(refused_docs, exc.token if exc.token in DOC_TOKENS else "unknown") or []
    except Exception:
        return _count(refused_docs, "unknown") or []
    records = []
    for index, section in enumerate(sections):
        verdict, value = cp.classify(section, policy["max_section_chars"])
        if verdict != "KEEP":
            _count(refused_sections, value)
            continue
        chunks = cp.chunk(value, policy["max_chunk_chars"])
        if not chunks or any(cp._OUTPUT.fullmatch(text) is None for text in chunks):
            _count(refused_sections, "encoding")
            continue
        if any(corpus_mod.ws._has_marker(text) for text in chunks):
            _count(refused_sections, "sealed")
            continue
        records.extend(_records(commit, path, oid, time_text, index, chunks))
    return records


def _build(repo_root, policy_bytes, policy_sha256, git_executable, git_sha256):
    policy = load_doc_policy(policy_bytes, policy_sha256)
    root, env = _open_repo(repo_root, git_executable, git_sha256)
    time_text = _commit_time(root, env, policy["commit"])
    records, refused_docs, refused_sections = [], {}, {}
    try:
        for path in sorted(policy["paths"]):
            records.extend(_document(root, env, policy, path, time_text, refused_docs, refused_sections))
    except _GitFailed:
        _refuse("read_refused")
    projection = {"schema": SCHEMA, "authority": "none", "scope": SCOPE, "policy_sha256": policy_sha256,
                  "commit": policy["commit"], "text_encoding": cp.TEXT_ENCODING, "accepted": len(records),
                  "refused_docs": dict(sorted(refused_docs.items())),
                  "refused_sections": dict(sorted(refused_sections.items())), "records": records}
    corpus_mod.ws.guard_object(projection)
    return projection


def build_doc_projection(*, enabled=False, repo_root=None, policy_bytes=None, policy_sha256=None,
                         git_executable=None, git_sha256=None):
    """Default OFF. With ``enabled=True``: the content projection of the policy's exact paths at its pinned commit.
    Document- and section-level problems are counted by closed token; input-level problems refuse the build."""
    if enabled is False:
        return {"status": "OFF"}
    if enabled is not True:
        _refuse("enabled_invalid")
    return _build(repo_root, policy_bytes, policy_sha256, git_executable, git_sha256)


def validate_doc_projection(projection, *, repo_root, policy_bytes, policy_sha256, git_executable=None, git_sha256=None):
    """Refuse unless ``projection`` is exactly the re-derivation from the pinned commit and policy."""
    if type(projection) is not dict or set(projection) != _PROJECTION_KEYS:
        _refuse("projection_mismatch")
    expected = _build(repo_root, policy_bytes, policy_sha256, git_executable, git_sha256)
    try:
        same = hmac.compare_digest(cp._canonical(projection), cp._canonical(expected))
    except (TypeError, ValueError, RecursionError):
        same = False
    if not same:
        _refuse("projection_mismatch")
    return None

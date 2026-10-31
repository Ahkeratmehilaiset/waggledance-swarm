"""Inactive synthetic public-status publisher and session reader.

No starter, service, scheduler or live source is wired here. Pins and lane/UUID
are caller assertions, NOT authentication between same-user processes. The
caller also supplies the clock: freshness covers publication age only, not
completeness or freshness of the source bridge. Filesystem checks and atomic
replace do not prevent hostile same-user replacement or TOCTOU. The selected
.codex-audit directory must already exist and be independently access-controlled.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

from sandbox.mind.step2 import shared_capture_adapter as adapter, shared_projection as projection
from sandbox.mind.step2 import status_metadata_producer as producer

SCHEMA = "wd.mind-public-status-publication.v1"
FILENAME = "public-status.json"
MAX_BYTES = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_FLOORED_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00\.000000Z\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class PublicStatusRefused(ValueError):
    """Fixed error without content, paths, identities or cursor details."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _object(raw):
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise ValueError()
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=adapter._strict_pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    if type(value) is not dict:
        raise ValueError()
    return value


def _instant(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError()
    return value.astimezone(timezone.utc)


def _directory(audit_root):
    root = Path(audit_root)
    if not root.is_absolute() or root.name != ".codex-audit" or ".." in root.parts:
        raise ValueError()
    for item in (root, *root.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    if not root.is_dir():
        raise ValueError()
    return root


def _regular(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
        raise ValueError()


def _pins(anchor_bytes, anchor_sha256, policy_bytes, policy_sha256, roster_bytes, roster_sha256,
          public_policy_bytes, public_policy_sha256):
    tasks, roster = adapter.strict_pins(policy_bytes, policy_sha256, roster_bytes, roster_sha256)
    public_tasks, public_types = adapter.public_status_policy(public_policy_bytes, public_policy_sha256, tasks)
    if (not isinstance(anchor_sha256, str) or _HEX.fullmatch(anchor_sha256) is None
            or type(anchor_bytes) is not bytes or len(anchor_bytes) > 16384
            or hashlib.sha256(anchor_bytes).hexdigest() != anchor_sha256):
        raise ValueError()
    anchor = _object(anchor_bytes)
    if (set(anchor) != {"schema", "source_path", "generation_path", "cursor_start", "policy_sha256",
                        "roster_sha256", "status_mode", "public_policy_sha256"}
            or anchor["schema"] != "wd.q4-source-anchor.v1" or anchor["status_mode"] != "public-v1"
            or anchor["policy_sha256"] != policy_sha256 or anchor["roster_sha256"] != roster_sha256
            or anchor["public_policy_sha256"] != public_policy_sha256):
        raise ValueError()
    return roster, public_tasks, public_types


def _body(value, tasks, types):
    if (type(value) is not dict or set(value) != {"schema", "authority", "scope", "freshness", "records"}
            or value["schema"] != "wd.mind-status-metadata.v1" or value["authority"] != "none"
            or value["scope"] != "in_memory_metadata_not_gate" or value["freshness"] != "UNKNOWN"
            or type(value["records"]) is not list or len(value["records"]) > 10000):
        raise ValueError()
    for item in value["records"]:
        if (type(item) is not dict or set(item) != {"time_utc", "agent", "type", "status", "task_id"}
                or type(item["time_utc"]) is not str or _FLOORED_UTC.fullmatch(item["time_utc"]) is None
                or item["agent"] not in projection.LANES or item["agent"] in ("claude-rco-1", "claude-rco-2")
                or item["type"] not in types or item["status"] not in projection.ALLOWED_STATUS
                or item["task_id"] not in tasks):
            raise ValueError()
        parsed = datetime.fromisoformat(item["time_utc"].replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
            raise ValueError()
    return value


def publish_status(audit_root, snapshot_result, *, as_of, enabled=False, **pins):
    """Publish one verified synthetic snapshot; returns no path, cursor or digest."""
    stage = None
    owned = False
    try:
        if enabled is not True:
            raise ValueError()
        _, tasks, types = _pins(**pins)
        now = _instant(as_of)
        data = producer.build_status(snapshot_result, enabled=True, **pins)
        body = _body(_object(data), tasks, types)
        doc = {"schema": SCHEMA, "authority": "none", "as_of_utc": now.isoformat(),
               "anchor_sha256": pins["anchor_sha256"], "policy_sha256": pins["policy_sha256"],
               "roster_sha256": pins["roster_sha256"], "public_policy_sha256": pins["public_policy_sha256"],
               "body_sha256": hashlib.sha256(_json(body)).hexdigest(), "body": body}
        payload = _json(doc)
        if len(payload) > MAX_BYTES:
            raise ValueError()
        root = _directory(audit_root)
        target = root / FILENAME
        if os.path.lexists(target):
            _regular(target)
        stage = root / (".public-status-" + secrets.token_hex(16) + ".tmp")
        with stage.open("xb") as stream:
            owned = True
            if stream.write(payload) != len(payload):
                raise OSError("short_write")
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(target):
            _regular(target)
        os.replace(stage, target)
        owned = False
        return None
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None
    finally:
        if owned:
            try:
                stage.unlink()
            except OSError:
                pass  # An orphan is never interpreted as the commit pointer.


def read_status(audit_root, *, lane, agent_uuid, as_of, max_age_seconds, enabled=False, **pins):
    """Read a publication with declared roster identity; never returns pins."""
    try:
        if enabled is not True or type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 900:
            raise ValueError()
        roster, tasks, types = _pins(**pins)
        if (not isinstance(lane, str) or lane not in roster or not isinstance(agent_uuid, str)
                or roster[lane].lower() != agent_uuid.lower()):
            raise ValueError()
        now = _instant(as_of)
        path = _directory(audit_root) / FILENAME
        _regular(path)
        with path.open("rb") as stream:
            doc = _object(stream.read(MAX_BYTES + 1))
        if (set(doc) != {"schema", "authority", "as_of_utc", "anchor_sha256", "policy_sha256", "roster_sha256",
                         "public_policy_sha256", "body_sha256", "body"}
                or doc["schema"] != SCHEMA or doc["authority"] != "none"
                or any(doc[name] != pins[name] for name in
                       ("anchor_sha256", "policy_sha256", "roster_sha256", "public_policy_sha256"))
                or type(doc["as_of_utc"]) is not str or len(doc["as_of_utc"]) > 40
                or type(doc["body_sha256"]) is not str or _HEX.fullmatch(doc["body_sha256"]) is None):
            raise ValueError()
        published = _instant(datetime.fromisoformat(doc["as_of_utc"]))
        age = (now - published).total_seconds()
        if age < 0 or age > max_age_seconds:
            raise ValueError()
        body = _body(doc["body"], tasks, types)
        if hashlib.sha256(_json(body)).hexdigest() != doc["body_sha256"]:
            raise ValueError()
        return _json({"schema": "wd.mind-public-session.v1", "authority": "none", "lane": lane,
                      "identity_verification": "caller_declared_not_authenticated",
                      "as_of_utc": now.isoformat(), "publication_age_seconds": age,
                      "source_freshness": "UNKNOWN", "records": body["records"]})
    except Exception:
        raise PublicStatusRefused("public_status_refused") from None

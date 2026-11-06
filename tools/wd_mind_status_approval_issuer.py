"""Default-off, in-memory public approval CANDIDATE builder; no I/O or activation.

The caller independently authenticates the policy, roster and public descriptor
pins and approves the public labels for disclosure. The returned digest is a
candidate only: it is neither an authenticated configuration nor a permission.
An independently trusted signed/config channel must bind it before use. No
source, publisher, lane, freshness or safe distribution is authenticated here.
Only the supplied policy/roster digests are filtered from labels; unknown anchor
hashes and other sensitive data require independent disclosure review.
Traceback frame locals and the caller still hold the supplied private bytes.
"""
import hashlib
import json

from sandbox.mind.step2 import shared_capture_adapter as adapter
from tools import wd_mind_status_projection_v3 as projection

_FIELDS = frozenset({"policy_bytes", "expected_policy_sha256", "roster_bytes", "expected_roster_sha256",
                     "descriptor_bytes", "expected_descriptor_sha256"})


class IssuerRefused(ValueError):
    """Fixed refusal with no input, path, roster or hash details."""


def _refuse_private_digests(tasks, types, policy_sha256, roster_sha256):
    forbidden = (policy_sha256.lower(), roster_sha256.lower())
    if any(digest in label.lower() for label in (*tasks, *types) for digest in forbidden):
        raise ValueError()


def _candidate(issuance):
    if type(issuance) is not dict or set(issuance) != _FIELDS:
        raise ValueError()
    approved, _ = adapter.strict_pins(issuance["policy_bytes"], issuance["expected_policy_sha256"],
                                      issuance["roster_bytes"], issuance["expected_roster_sha256"])
    public_tasks, public_types = adapter.public_status_policy(issuance["descriptor_bytes"],
                                                              issuance["expected_descriptor_sha256"], approved)
    _refuse_private_digests(public_tasks, public_types, issuance["expected_policy_sha256"],
                            issuance["expected_roster_sha256"])
    data = json.dumps({"schema": "wd.mind-public-approval.v1",
                       "descriptor_sha256": issuance["expected_descriptor_sha256"],
                       "approved_tasks": sorted(public_tasks), "approved_types": sorted(public_types)},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    digest = hashlib.sha256(data).hexdigest()
    # This verifies candidate shape/label restrictions, not caller trust in its hash.
    projection.validate_public_approval(issuance["descriptor_bytes"], issuance["expected_descriptor_sha256"],
                                        data, digest)
    return {"approval_bytes": data, "approval_sha256": digest, "authority": "candidate_only",
            "config_authentication": "UNKNOWN"}


def issue(*, enabled=False, issuance=None):
    """Return a candidate or a fixed refusal; explicitly disabled by default."""
    if enabled is False:
        return {"status": "OFF"}
    result = None
    if enabled is True:
        try:
            result = _candidate(issuance)
        except Exception:
            pass
    if result is None:
        raise IssuerRefused("issuer_refused")
    return result

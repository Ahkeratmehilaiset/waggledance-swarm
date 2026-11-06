"""Provision guard state only from a separately pinned deployment manifest.

The operator or launcher must obtain the manifest hash from the verified
deployment pointer. Computing a hash from untrusted manifest bytes is not an
authority check. This module does not modify the bundle or schedule itself.
"""

import json
import sys

from tools import wd_q4_guard_state_provision as provisioner
from tools import wd_q4_lane_caller_cli as caller


class AnchoredProvisionUnavailable(RuntimeError):
    """A closed refusal without paths or manifest data."""


def provision_anchored(*, bundle_root=None, manifest_sha256=None, enabled=False):
    if enabled is False:
        return {"status": "OFF"}
    try:
        if enabled is not True:
            raise ValueError()
        root = caller._path(bundle_root)
        raw = caller._read(str(root / "deployment-manifest.json"), caller.MAX_MANIFEST, manifest_sha256)
        manifest = caller._json(raw)
        if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
            raise ValueError()
        if type(manifest.get("files")) is not dict:
            raise ValueError()
        fleet_pin = caller._hex(manifest["files"].get("wd-fleet.json"))
        fleet = caller._read(str(root / "wd-fleet.json"), caller.MAX_FLEET, fleet_pin)
        return provisioner.provision_guard_state(fleet_bytes=fleet, expected_fleet_sha256=fleet_pin, enabled=True)
    except Exception:
        raise AnchoredProvisionUnavailable("guard_state_provision_unavailable") from None


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    try:
        if not args:
            result = provision_anchored()
        elif len(args) == 3 and args[0] == "--enable":
            result = provision_anchored(bundle_root=args[1], manifest_sha256=args[2], enabled=True)
        else:
            raise AnchoredProvisionUnavailable()
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except Exception:
        print('{"status":"REFUSED"}')
        return 1


if __name__ == "__main__":
    sys.exit(main())

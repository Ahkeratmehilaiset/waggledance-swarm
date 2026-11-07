"""Default-off fixed public status; never retries an uncertain append.

The caller must run this manifest-pinned code in the existing lane session and
pin the inherited active bridge separately. A queued result is not canonical.
"""
import json
import re
import sys

from tools import wd_mind_public_event_writer as writer


def _emit(value):
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _emit({"status": "OFF"})
        return 0
    if args != ["--enable"]:
        _emit({"status": "REFUSED"})
        return 2
    try:
        result = writer.publish(enabled=True)
        if (type(result) is not dict or type(result.get("canonical_durable")) is not bool
                or result.get("delivery_status") not in ("canonical", "queued")):
            raise ValueError()
        if result["delivery_status"] == "canonical":
            if set(result) != {"delivery_status", "canonical_durable"} or result["canonical_durable"] is not True:
                raise ValueError()
            output = {"status": "WRITTEN", "delivery": "canonical"}
        else:
            if (set(result) != {"delivery_status", "canonical_durable", "retained_wal_sha256"}
                    or result["canonical_durable"] is not False or type(result["retained_wal_sha256"]) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", result["retained_wal_sha256"]) is None):
                raise ValueError()
            output = {"status": "QUEUED", "retained_wal_sha256": result["retained_wal_sha256"]}
    except writer.PublicStatusRefused:
        _emit({"status": "REFUSED"})
        return 2
    except BaseException:
        _emit({"status": "UNKNOWN"})
        return 3
    _emit(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())

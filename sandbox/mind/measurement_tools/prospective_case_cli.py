"""NOT_READY placeholder, deliberately NOT an operational measurement caller.

Root's final Source contract requires a separate CASE_INPUT_SEAL_GO and fresh
consumed seal_inputs intent BEFORE any private collection/native reads, exactly
one bounded evidence extraction, immutable private CaseInput/capsule/count and
original-refusal provenance publication, then a HASH-ONLY PublisherGO request.
Later prepass_inputs/complete_cases must load/revalidate SAME sealed bytes, not
re-extract or authorize their own observed hashes. That transition could not be
safely implemented and verified within the bounded source-author slice.

This entrypoint therefore refuses EVERY invocation before any file/data read or
write. --execute does not unlock it. It never imports or invokes the publisher,
native worker, SUT, model, encoder or scorer. Command-shaped is NOT ready.
The withdrawn direct-assembly draft is audit evidence only, never executable
authority. No actual protocol/GO/capture/collection was read by this author.
"""
from __future__ import annotations

import argparse
import json


MISSING = "case_input_seal_transition_not_implemented"


class _Arguments(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise _Arguments()


def main(argv=None):
    reason = "arguments"
    phase = None
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--phase", choices=("seal_inputs", "prepass_inputs", "complete_cases"), required=True)
        for flag in ("protocol", "protocol-sha256", "preregistration", "preregistration-sha256", "go", "go-sha256",
                     "runner-binding", "runner-binding-sha256", "collection-result-sha256", "native-manifest",
                     "native-manifest-sha256", "sealed-result-sha256", "baseline-manifest-sha256",
                     "baseline-seal-sha256", "journal-sha256"):
            parser.add_argument("--" + flag)
        args = parser.parse_args(argv)
        phase = args.phase
        reason = MISSING
    except (_Arguments, TypeError, ValueError):
        pass
    print(json.dumps({"status": "STOP_NOT_READY", "reason": reason, "phase": phase,
        "invocation_consumed": False, "actual_data_opened": False, "publisher_invoked": False},
        sort_keys=True, separators=(",", ":")))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

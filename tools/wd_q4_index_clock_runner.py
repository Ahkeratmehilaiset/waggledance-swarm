"""Default-off, one-shot caller-clock wrapper around the existing fenced child.

No clock is read, no scheduler or long-running process is created, and this is
not an authentication, deployment, or freshness proof. The caller supplies
and protects the exact cutoff and all existing child pins; the cutoff is an
unverified assertion. Never pass live data without separate authorization.

Once the child is entered, any exception or interruption means output state
UNKNOWN: the pointer may have moved and a fence/intent may remain. The caller
must quarantine and reconcile the roots; this helper never retries or cleans.
Before the child is entered, even an interruption is reported as a sanitized
refusal; the caller must not treat this as memory erasure in other frames.
"""
from datetime import datetime, timezone


class IndexRunnerRefused(ValueError):
    """Refused before the child was entered."""


class IndexRunnerOutcomeUnknown(RuntimeError):
    """The child was entered; output state must be reconciled."""


def run(*, enabled=False, caller_cutoff_utc=None, plan_bytes=None, expected_plan_sha256=None,
        config=None, start_prefix=None, root=None, private_state_root=None, trusted_model_root=None):
    if enabled is False:
        return {"status": "OFF"}
    preflight_ok = False
    try:
        if (enabled is not True or type(caller_cutoff_utc) is not datetime
                or caller_cutoff_utc.tzinfo is None or caller_cutoff_utc.utcoffset() is None):
            raise ValueError()
        cutoff = caller_cutoff_utc.astimezone(timezone.utc)
        from sandbox.mind.step2.wd_q4_prefix_build_child import build_prefix_generation
        preflight_ok = True
    except BaseException:
        pass
    if not preflight_ok:
        caller_cutoff_utc = plan_bytes = expected_plan_sha256 = config = start_prefix = None
        root = private_state_root = trusted_model_root = enabled = cutoff = build_prefix_generation = None
        raise IndexRunnerRefused("index_request_refused") from None
    result = None
    try:
        child = build_prefix_generation(enabled=True, plan_bytes=plan_bytes,
            expected_plan_sha256=expected_plan_sha256, config=config, start_prefix=start_prefix,
            root=root, private_state_root=private_state_root, trusted_model_root=trusted_model_root,
            cutoff_utc=cutoff)
        if (type(child) is not dict or child.get("status") not in
                ("PUBLISHED_GENESIS", "PUBLISHED_HISTORY", "EMPTY_ADVANCED", "EMPTY_NO_CHANGE")
                or child.get("freshness") != "UNKNOWN" or child.get("source_authenticity") != "UNKNOWN"):
            raise ValueError()
        result = {"status": "INDEX_RUN_COMPLETE", "clock": "CALLER_UNVERIFIED",
                  "cutoff_utc": cutoff.isoformat(), "child": child}
    except BaseException:
        pass
    if result is not None:
        return result
    caller_cutoff_utc = plan_bytes = expected_plan_sha256 = config = start_prefix = None
    root = private_state_root = trusted_model_root = enabled = cutoff = child = build_prefix_generation = None
    raise IndexRunnerOutcomeUnknown("index_outcome_unknown") from None

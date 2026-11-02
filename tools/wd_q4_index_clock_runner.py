"""Default-off, one-shot caller-clock wrapper around the existing fenced child.

No clock is read, no scheduler or long-running process is created, and this is
not an authentication, deployment, or freshness proof. The caller supplies
and protects the exact cutoff and all existing child pins; the cutoff is an
unverified assertion. Never pass live data without separate authorization.

Once the child is entered, any exception or interruption means output state
UNKNOWN: the pointer may have moved and a fence/intent may remain. The caller
must quarantine and reconcile the roots; this helper never retries or cleans.
Before the child is entered, KeyboardInterrupt, SystemExit and GeneratorExit
retain their control-flow type without private payloads; other BaseExceptions
become a generic BaseException. This is not erasure of other frames.
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
    preflight_signal = None
    exit_code = None
    try:
        if (enabled is not True or type(caller_cutoff_utc) is not datetime
                or caller_cutoff_utc.tzinfo is None or caller_cutoff_utc.utcoffset() is None):
            raise ValueError()
        cutoff = caller_cutoff_utc.astimezone(timezone.utc)
        from sandbox.mind.step2.wd_q4_prefix_build_child import build_prefix_generation
        preflight_ok = True
    except KeyboardInterrupt:
        preflight_signal = "interrupt"
    except SystemExit as error:
        preflight_signal = "exit"
        # A subclass may override attribute access and execute arbitrary code
        # while the caller's private inputs are still in this frame.
        exit_code = (error.code if type(error) is SystemExit
                     and (error.code is None or type(error.code) is int) else 1)
    except GeneratorExit:
        preflight_signal = "generator_exit"
    except Exception:
        pass
    except BaseException:
        preflight_signal = "other"
    if not preflight_ok:
        caller_cutoff_utc = plan_bytes = expected_plan_sha256 = config = start_prefix = None
        root = private_state_root = trusted_model_root = enabled = cutoff = build_prefix_generation = None
        if preflight_signal == "interrupt":
            raise KeyboardInterrupt() from None
        if preflight_signal == "exit":
            raise SystemExit(exit_code) from None
        if preflight_signal == "generator_exit":
            raise GeneratorExit() from None
        if preflight_signal == "other":
            raise BaseException("index_preflight_interrupted") from None
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

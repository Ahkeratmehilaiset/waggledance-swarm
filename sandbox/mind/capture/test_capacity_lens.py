"""Offline cases for the capacity lens: synthetic observer status only, no observer, no process."""

import json
import subprocess  # only its TimeoutExpired class: every status run here is the fake one
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402

NOW = "2026-10-13T11:00:00+00:00"
DIGIT_LIMIT = sys.get_int_max_str_digits()  # 4300 unless -X int_max_str_digits or PYTHONINTMAXSTRDIGITS set it; 0 none


def status(observations=(), activity=(), collection=None):
    return {"schema": lens.STATUS_SCHEMA, "execution_allowed": False, "observed_at": NOW,
            "observations": list(observations), "native_activity": list(activity), "collection": collection or {}}


def observation(thread, *windows, provider="claude", freshness="provider_timestamp_unknown", quota="unknown",
                age=1.0):
    # The defaults are the labels the installed observer gives every Claude observation.
    return {"provider": provider, "native_thread_id": thread, "observation_age_seconds": age,
            "freshness": freshness, "quota_state": quota, "quota_windows": list(windows)}


def render(value, declared=None):
    return lens.text_lines(*lens.project(value, declared or {}))


def test_windows_list_each_thread_never_sum_and_flag_risk_at_80():
    reset = 1792000800  # 2026-10-14T18:00:00Z
    lines = render(status([
        observation("9f375967-aaaa", {"name": "seven_day", "used_percent": 80, "resets_at": reset}),
        observation("8abcb653-bbbb", {"name": "seven_day", "used_percent": 79, "resets_at": reset})]),
        {"9f375967": "fable-5"})
    row = [line for line in lines if line.startswith("LIMITS claude seven_day")]
    assert row == ["LIMITS claude seven_day: RISK 9f375967 (fable-5, declared) 80%, 8abcb653 79%; "
                   "resets 2026-10-14T18:00Z (in 1860 min); "
                   "observer provider_timestamp_unknown, quota unknown, under 1 min old"]


def test_a_window_without_an_observed_reset_says_unknown_and_no_risk_below_80():
    lines = render(status([observation("9f375967-aaaa", {"name": "five_hour", "used_percent": 7})]))
    assert ("LIMITS claude five_hour: 9f375967 7%; reset UNKNOWN (no readable reset observed); "
            "observer provider_timestamp_unknown, quota unknown, under 1 min old") in lines


def test_each_line_keeps_the_observer_labels_and_the_age_of_its_oldest_figure():
    # Own audit 10-16: in my live loop's 729 captures every Claude figure was labelled provider_timestamp_unknown
    # with quota state unknown, up to 30 min old, and the lines dropped all three. The view rows keep the labels.
    reset = 1792000800  # 2026-10-14T18:00:00Z
    value = status([
        observation("9f375967-aaaa", {"name": "seven_day", "used_percent": 85, "resets_at": reset}, age=391.0),
        observation("7cdef012-cccc", {"name": "five_hour", "used_percent": 5, "resets_at": reset},
                    freshness=None, quota=None, age=None),
        observation("11112222-dddd", {"name": "primary", "used_percent": 40, "resets_at": reset}, provider="codex",
                    freshness="fresh", quota="observed_headroom", age=20.0),
        observation("33334444-eeee", {"name": "primary", "used_percent": 70, "resets_at": reset}, provider="codex",
                    freshness="unknown_or_stale", quota="unknown", age=1807.2)])
    assert [line for line in render(value) if line.startswith("LIMITS")] == [
        "LIMITS claude five_hour: 7cdef012 5%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer UNKNOWN, quota UNKNOWN, oldest age UNKNOWN",
        "LIMITS claude seven_day: RISK 9f375967 85%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer provider_timestamp_unknown, quota unknown, up to 7 min old",
        "LIMITS codex primary: 11112222 40%, 33334444 70%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer fresh, unknown_or_stale, quota observed_headroom, unknown, up to 30 min old"]
    rows = lens.project(value, {})[1][("codex", "primary")]
    assert [(row["freshness"], row["quota_state"], row["observation_age_s"]) for row in rows] == [
        ("fresh", "observed_headroom", 20.0), ("unknown_or_stale", "unknown", 1807.2)]


def test_a_passed_reset_says_passed_and_differing_resets_are_given_per_thread():
    now = int(lens.parse_time(NOW).timestamp())
    lines = render(status([
        observation("9f375967-aaaa", {"name": "five_hour", "used_percent": 90, "resets_at": now},
                    {"name": "seven_day", "used_percent": 50, "resets_at": now + 7200},
                    {"name": "seven_day_opus", "used_percent": 10, "resets_at": now - 3600}),
        observation("8abcb653-bbbb", {"name": "five_hour", "used_percent": 91, "resets_at": now},
                    {"name": "seven_day", "used_percent": 60})]))
    tail = "; observer provider_timestamp_unknown, quota unknown, under 1 min old"
    assert [line for line in lines if line.startswith("LIMITS")] == [
        "LIMITS claude five_hour: RISK 9f375967 90%, 8abcb653 91%; reset 2026-10-13T11:00Z passed 0 min ago" + tail,
        "LIMITS claude seven_day: 9f375967 50%, 8abcb653 60%; resets differ by thread: "
        "9f375967 2026-10-13T13:00Z (in 120 min), 8abcb653 UNKNOWN" + tail,
        "LIMITS claude seven_day_opus: 9f375967 10%; reset 2026-10-13T10:00Z passed 60 min ago" + tail]


def test_an_unreadable_figure_is_unknown_and_never_a_crash():
    nan, inf = float("nan"), float("inf")
    value = status([observation("9f375967-aaaa", {"name": "a", "used_percent": None, "resets_at": nan},
                                {"name": "b", "used_percent": nan, "resets_at": inf},
                                {"name": "c", "used_percent": 81, "resets_at": 1e20},
                                {"name": "d", "used_percent": True, "resets_at": True}, age=inf),
                    observation("8abcb653-bbbb", {"name": "e", "used_percent": 1}, age=-5.0)])
    tail = ("; reset UNKNOWN (no readable reset observed); "
            "observer provider_timestamp_unknown, quota unknown, oldest age UNKNOWN")
    assert [line for line in render(value) if line.startswith("LIMITS")] == [
        "LIMITS claude a: 9f375967 UNKNOWN" + tail, "LIMITS claude b: 9f375967 UNKNOWN" + tail,
        "LIMITS claude c: RISK 9f375967 81%" + tail, "LIMITS claude d: 9f375967 UNKNOWN" + tail,
        "LIMITS claude e: 8abcb653 1%" + tail]
    rows = [row for _, group in sorted(lens.project(value, {})[1].items()) for row in group]
    assert [(row["used_percent"], row["reset_utc"], row["observation_age_s"]) for row in rows] == [
        (None, None, None), (None, None, None), (81, None, None), (None, None, None), (1, None, None)]
    json.dumps(rows, allow_nan=False)  # the view stays strict JSON


def test_an_integer_too_large_for_a_float_is_unknown_and_never_a_crash():
    # Lead C1 (10-16, suspected): number() called math.isfinite on any int, but json.loads reads an int literal up to
    # the int digit limit (4300 digits by default), so an int beyond the float range as a percent, reset or age
    # stopped the lens with OverflowError.
    huge = json.loads("1" + "0" * 400)
    value = status([observation("9f375967-aaaa", {"name": "a", "used_percent": huge, "resets_at": huge}, age=huge)])
    assert [line for line in render(value) if line.startswith("LIMITS")] == [
        "LIMITS claude a: 9f375967 UNKNOWN; reset UNKNOWN (no readable reset observed); "
        "observer provider_timestamp_unknown, quota unknown, oldest age UNKNOWN"]
    [row] = lens.project(value, {})[1][("claude", "a")]
    assert (row["used_percent"], row["reset_utc"], row["observation_age_s"]) == (None, None, None)
    assert lens.number(2 ** 1023) and not lens.number(2 ** 1024)  # the float range is the boundary


def test_a_provider_never_collected_is_unknown_with_the_observer_state():
    lines = render(status(collection={"codex": {"last_success": None, "collection_state": "pending_or_interrupted",
                                                "last_attempt": "2026-09-29T20:24:17+00:00"}}))
    assert ("LIMITS codex: UNKNOWN (observer has no successful collection; state pending_or_interrupted, "
            "last attempt 2026-09-29T20:24)") in lines


def test_old_idle_threads_fold_but_errors_and_current_work_stay():
    def act(thread, state, last, error=None):
        return {"provider": "claude", "native_thread_id": thread, "activity_state": state,
                "availability_state": "x", "last_successful_turn_at": last, "error_type": error,
                "first_error_at": "2026-10-10T08:00:00+00:00" if error else None}
    lines = render(status(activity=[act("11111111-x", "idle_observed", "2026-10-11T10:00:00+00:00"),
                                    act("22222222-x", "idle_observed", "2026-10-10T10:00:00+00:00", "rate_limited"),
                                    act("33333333-x", "work_requested", "2026-10-13T10:50:00+00:00")]))
    assert "ACTIVITY claude thread 33333333: work_requested; last success 10:50Z (10 min ago)" in lines
    assert any(line.startswith("ACTIVITY claude thread 22222222: idle_observed") and "error rate_limited" in line
               for line in lines)
    assert lines[-1] == "ACTIVITY: 1 more threads idle with no error, last success over 12 h ago (oldest 49 h)"


def test_publish_replaces_the_file_whole_and_leaves_no_temporary_file(tmp_path):
    # How the lens writes into the live dir (main writes its two cycle-dir files directly); it runs the core's
    # sharing retry, so a core without one fails here.
    target = tmp_path / "capacity"
    lens.publish(target, "lens-limits.txt", b"first\n")
    lens.publish(target, "lens-limits.txt", b"second\n")
    assert (target / "lens-limits.txt").read_bytes() == b"second\n"
    assert [path.name for path in target.iterdir()] == ["lens-limits.txt"]


def run_hook(tmp_path, monkeypatch, raw, returncode=0, error=None):
    """lens.main() over a fake status run (nothing starts) that prints raw and exits with returncode, or raises error:
    its result or refusal, and the two dirs. The one status run must have the lens's exact argv."""
    out, published, runs = tmp_path / "cycle" / "hook-capacity", tmp_path / "live" / "capacity", []

    def fake_run(argv, **kwargs):
        runs.append(argv)
        assert kwargs == {"capture_output": True, "timeout": 60}  # the lens's own deadline, under the hook's 120 s
        if error is not None:
            raise error
        return types.SimpleNamespace(returncode=returncode, stdout=raw)

    monkeypatch.setattr(lens, "subprocess", types.SimpleNamespace(run=fake_run,
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(sys, "argv", ["capacity_lens.py", "--status-script", "status.ps1", "--out-dir", str(out),
                                      "--publish-dir", str(published)])
    try:
        result = lens.main()
    except SystemExit as exc:
        result = str(exc)
    assert runs == [["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "status.ps1", "-Json"]]
    return result, out, published


def encoded(value):
    return json.dumps(value).encode("utf-8")


def good_status():
    return status([observation("9f375967-aaaa", {"name": "a", "used_percent": 5, "resets_at": 1792000800})],
                  [{"provider": "claude", "native_thread_id": "11111111-x", "activity_state": "idle_observed",
                    "last_successful_turn_at": "2026-10-13T10:00:00+00:00"}])


def test_a_readable_status_runs_the_hook_and_writes_its_files(tmp_path, monkeypatch):
    raw = encoded(good_status())
    result, out, published = run_hook(tmp_path, monkeypatch, raw)
    assert result == 0 and (out / "capacity-status.json").read_bytes() == raw
    assert sorted(path.name for path in out.iterdir()) == ["capacity-lens.json", "capacity-status.json"]
    assert sorted(path.name for path in published.iterdir()) == ["capacity-lens.json", "lens-limits.txt"]


RUN_FAILURES = {"exit_1": ("status_failed", {"returncode": 1}),
                "timed_out": ("status_timed_out", {"error": subprocess.TimeoutExpired(["status.ps1"], 60)}),
                "not_found": ("status_not_started", {"error": FileNotFoundError(2, "not found", "powershell.exe")}),
                "denied": ("status_not_started", {"error": PermissionError(13, "access denied", "powershell.exe")})}


@pytest.mark.parametrize("case", sorted(RUN_FAILURES))
def test_a_status_run_that_fails_times_out_or_raises_an_oserror_is_refused_and_nothing_is_written(tmp_path,
                                                                                                    monkeypatch, case):
    # The first two left main as a raw RuntimeError or TimeoutExpired with a traceback. Tools' map (Lead 10-16): an
    # OSError out of the run (as when powershell.exe is missing or may not run) was outside that handler.
    reason, failure = RUN_FAILURES[case]
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(good_status()), **failure)
    assert result == f"refused: {reason}" and not out.exists() and not published.exists()


def with_used_percent_digits(digits):
    """A readable status whose one percent is an int literal of that many digits."""
    text = json.dumps(good_status())
    assert text.count('"used_percent": 5') == 1
    return text.replace('"used_percent": 5', '"used_percent": ' + "1" * digits).encode("utf-8")


UNREADABLE = {"int_literal_over_the_digit_limit": with_used_percent_digits(DIGIT_LIMIT + 1) if DIGIT_LIMIT else None,
              "not_utf8": b"\xff" + encoded(good_status()), "not_json": b"{",
              "nested_too_deep": b"[" * 100000 + b"]" * 100000}
# Each case breaks one condition of a valid status. Lead's record on Tools' map (10-16, CAPACITY_I1): the schema case
# lacked execution_allowed=False too, so it passed without the schema check.
NOT_THE_OBSERVER = {"a_list": b"[]", "a_string": b'"status"',
                    "another_schema": encoded(dict(good_status(), schema="other")),
                    "execution_allowed": encoded(dict(good_status(), execution_allowed=True)),
                    "execution_allowed_zero": encoded(dict(good_status(), execution_allowed=0)),
                    "execution_allowed_missing": encoded({key: value for key, value in good_status().items()
                                                          if key != "execution_allowed"})}


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_a_status_json_loads_refuses_is_refused_whole_and_nothing_is_written(tmp_path, monkeypatch, case):
    # RCO1 10-16: json.loads refuses an int literal over the int digit limit before number() sees it, and main() had
    # no handler, so the hook stopped with a traceback instead of a fixed refusal. Tools' map (Lead 10-16): the case
    # had 4301 digits, so it held only under the default limit; it now takes the limit this interpreter has.
    if UNREADABLE[case] is None:
        pytest.skip("this interpreter has no int digit limit (0): json.loads reads an int literal of any length")
    result, out, published = run_hook(tmp_path, monkeypatch, UNREADABLE[case])
    assert result == "refused: status_unreadable" and not out.exists() and not published.exists()


def test_an_int_literal_at_the_digit_limit_is_read_and_its_figure_is_unknown(tmp_path, monkeypatch):
    # The other side of the boundary: json.loads reads it, and an int beyond the float range is UNKNOWN (null).
    if not DIGIT_LIMIT:
        pytest.skip("this interpreter has no int digit limit (0)")
    result, out, published = run_hook(tmp_path, monkeypatch, with_used_percent_digits(DIGIT_LIMIT))
    assert result == 0
    [row] = json.loads((out / "capacity-lens.json").read_text(encoding="ascii"))["limits"]
    assert [thread["used_percent"] for thread in row["threads"]] == [None]


@pytest.mark.parametrize("case", sorted(NOT_THE_OBSERVER))
def test_a_status_that_is_not_the_observer_object_is_refused_whole_and_nothing_is_written(tmp_path, monkeypatch, case):
    result, out, published = run_hook(tmp_path, monkeypatch, NOT_THE_OBSERVER[case])
    assert result == "refused: status_schema_unexpected" and not out.exists() and not published.exists()


def malformed(case):
    value = good_status()
    if case == "observation_not_an_object":
        value["observations"] = ["x"]
    elif case == "observations_not_a_list":
        value["observations"] = 5
    elif case == "quota_windows_not_a_list":
        value["observations"][0]["quota_windows"] = 3
    elif case == "collection_not_an_object":
        value["collection"] = ["codex"]
    elif case == "activity_not_a_list":
        value["native_activity"] = 7
    elif case == "observed_at_missing":
        del value["observed_at"]
    elif case == "observed_at_not_a_time":
        value["observed_at"] = "soon"
    elif case == "last_success_in_month_13":
        value["native_activity"][0]["last_successful_turn_at"] = "2026-13-01T00:00:00+00:00"
    elif case == "last_success_before_year_1_in_utc":  # parses, but has no UTC time
        value["native_activity"][0]["last_successful_turn_at"] = "0001-01-01T00:00:00+05:00"
    elif case == "observed_at_before_year_1_in_utc":  # the status time parses, but has no UTC time (once text-only)
        value["observed_at"] = "0001-01-01T00:00:00+05:00"
    elif case == "observations_false":
        value["observations"] = False
    elif case == "quota_windows_one_object":  # one window object where the list of windows belongs
        value["observations"][0]["quota_windows"] = value["observations"][0]["quota_windows"][0]
    elif case == "window_not_an_object":
        value["observations"][0]["quota_windows"] = ["a"]
    elif case == "window_name_not_a_string":
        value["observations"][0]["quota_windows"][0]["name"] = 5
    elif case == "collection_an_empty_list":
        value["collection"] = []
    elif case == "collection_row_not_an_object":
        value["collection"] = {"codex": "never"}
    elif case == "activity_a_string":
        value["native_activity"] = "idle_observed"
    elif case == "activity_row_not_an_object":
        value["native_activity"] = [None]
    elif case == "observed_at_a_number":  # str(20261013) is an ISO basic date
        value["observed_at"] = 20261013
    elif case == "last_success_false":
        value["native_activity"][0]["last_successful_turn_at"] = False
    elif case == "last_success_empty_text":
        value["native_activity"][0]["last_successful_turn_at"] = ""
    elif case == "first_error_not_a_time":
        value["native_activity"][0]["first_error_at"] = "soon"
    elif case == "collection_success_not_a_time":
        value["collection"] = {"codex": {"last_success": "soon"}}
    elif case == "collection_attempt_a_number":
        value["collection"] = {"codex": {"last_success": None, "last_attempt": 5}}
    return value


MALFORMED = ("observation_not_an_object", "observations_not_a_list", "quota_windows_not_a_list",
             "collection_not_an_object", "activity_not_a_list", "observed_at_missing", "observed_at_not_a_time",
             "last_success_in_month_13", "last_success_before_year_1_in_utc", "observations_false",
             "quota_windows_one_object", "window_not_an_object", "window_name_not_a_string", "collection_an_empty_list",
             "collection_row_not_an_object", "activity_a_string", "activity_row_not_an_object", "observed_at_a_number",
             "last_success_false", "last_success_empty_text", "first_error_not_a_time", "collection_success_not_a_time",
             "collection_attempt_a_number", "observed_at_before_year_1_in_utc")


@pytest.mark.parametrize("case", MALFORMED)
def test_a_status_with_a_list_row_or_time_of_the_wrong_shape_is_refused_whole_and_nothing_is_written(tmp_path,
                                                                                                      monkeypatch,
                                                                                                      case):
    # RCO1 10-16 (optional note): a malformed row or time raised out of main (before any write). Own follow-up: a
    # list given as false, a string or one object, and a row that is not an object, were read as no rows (a view in
    # part); a time given as false or empty text was read as none, and one the lens only shows or only tests for
    # presence was never parsed. Each now refuses whole with a fixed reason that echoes nothing of the status.
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(malformed(case)))
    assert result == "refused: status_shape_unexpected" and not out.exists() and not published.exists()


def test_an_error_in_the_lens_text_is_its_own_never_a_status_of_the_wrong_shape(tmp_path, monkeypatch):
    # Tools' map (Lead 10-16): readable() also caught these errors out of text_lines(), so an error of the lens's own
    # text would have been shown as a status of the wrong shape (none was seen). text_lines() formats only what
    # project() kept; an error there now stops the hook as itself, before anything is written.
    def text_lines(*kept):
        raise TypeError("a lens text error")

    monkeypatch.setattr(lens, "text_lines", text_lines)
    with pytest.raises(TypeError) as raised:
        run_hook(tmp_path, monkeypatch, encoded(good_status()))
    assert raised.value.args == ("a lens text error",)
    assert not (tmp_path / "cycle").exists() and not (tmp_path / "live").exists()


def test_an_absent_or_null_list_is_empty_and_the_hook_still_runs(tmp_path, monkeypatch):
    # The observer gave none: no rows, not a wrong shape (my 729 captured live statuses always gave every list).
    value = good_status()
    value["observations"][0]["quota_windows"], value["native_activity"] = None, None
    del value["collection"]
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(value))
    assert result == 0
    [line] = (published / "lens-limits.txt").read_text(encoding="ascii").splitlines()
    assert line.startswith("capacity (installed observer, read-only) observed 2026-10-13T11:00:00Z")
    bare = good_status()
    del bare["observations"], bare["native_activity"], bare["collection"]
    assert render(bare) == [line]


def test_every_time_the_lens_shows_is_in_utc():
    # The first error and the last attempt were shown as given; the view's first_error_utc held any offset.
    error = {"provider": "claude", "native_thread_id": "22222222-x", "activity_state": "idle_observed",
             "last_successful_turn_at": "2026-10-13T10:00:00+00:00", "error_type": "rate_limited",
             "first_error_at": "2026-10-10T11:00:00+03:00"}
    value = status(activity=[error], collection={"codex": {"last_success": None, "collection_state": "pending",
                                                           "last_attempt": "2026-09-29T23:24:17+03:00"}})
    lines = render(value)
    assert ("ACTIVITY claude thread 22222222: idle_observed; last success 10:00Z (60 min ago); "
            "error rate_limited since 2026-10-10T08:00") in lines
    assert "LIMITS codex: UNKNOWN (observer has no successful collection; state pending, last attempt " \
           "2026-09-29T20:24)" in lines
    [row] = lens.project(value, {})[3]
    assert row["first_error_utc"] == "2026-10-10T08:00:00Z"

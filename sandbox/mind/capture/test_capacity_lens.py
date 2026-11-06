"""Offline cases for the capacity lens: synthetic observer status only, no observer, no process."""

import hashlib
import json
import subprocess  # only its TimeoutExpired class: every status run here is the fake one
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402

NOW = "2026-10-13T11:00:00+00:00"
REAL_CLOCK = getattr(lens, "clock", None)


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    """K2: every case runs on an injected lens clock 30 s after NOW unless it injects its own."""
    monkeypatch.setattr(lens, "clock", lambda: lens.parse_time("2026-10-13T11:00:30+00:00"), raising=False)
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


def test_windows_list_each_observation_never_sum_and_flag_risk_at_80():
    reset = 1792000800  # 2026-10-14T18:00:00Z
    lines = render(status([
        observation("9f375967-aaaa", {"name": "seven_day", "used_percent": 80, "resets_at": reset}),
        observation("8abcb653-bbbb", {"name": "seven_day", "used_percent": 79, "resets_at": reset})]),
        {"9f375967": "fable-5"})
    row = [line for line in lines if line.startswith("LIMITS claude seven_day")]
    assert row == ["LIMITS claude seven_day: RISK fable-5 (declared) 80%, 79%; "
                   "resets 2026-10-14T18:00Z (in 1860 min); "
                   "observer provider_timestamp_unknown, quota unknown, under 1 min old"]


def test_a_window_without_an_observed_reset_says_unknown_and_no_risk_below_80():
    lines = render(status([observation("9f375967-aaaa", {"name": "five_hour", "used_percent": 7})]))
    assert ("LIMITS claude five_hour: 7%; reset UNKNOWN (no readable reset observed); "
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
        "LIMITS claude five_hour: 5%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer UNKNOWN, quota UNKNOWN, oldest age UNKNOWN",
        "LIMITS claude seven_day: RISK 85%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer provider_timestamp_unknown, quota unknown, up to 7 min old",
        "LIMITS codex primary: 40%, 70%; resets 2026-10-14T18:00Z (in 1860 min); "
        "observer fresh, unknown_or_stale, quota observed_headroom, unknown, up to 30 min old"]
    rows = lens.project(value, {})[1][("codex", "primary")]
    assert [(row["freshness"], row["quota_state"], row["observation_age_s"]) for row in rows] == [
        ("fresh", "observed_headroom", 20.0), ("unknown_or_stale", "unknown", 1807.2)]


def test_a_passed_reset_says_passed_and_differing_resets_are_given_per_observation():
    now = int(lens.parse_time(NOW).timestamp())
    lines = render(status([
        observation("9f375967-aaaa", {"name": "five_hour", "used_percent": 90, "resets_at": now},
                    {"name": "seven_day", "used_percent": 50, "resets_at": now + 7200},
                    {"name": "seven_day_opus", "used_percent": 10, "resets_at": now - 3600}),
        observation("8abcb653-bbbb", {"name": "five_hour", "used_percent": 91, "resets_at": now},
                    {"name": "seven_day", "used_percent": 60})]))
    tail = "; observer provider_timestamp_unknown, quota unknown, under 1 min old"
    assert [line for line in lines if line.startswith("LIMITS")] == [
        "LIMITS claude five_hour: RISK 90%, 91%; reset 2026-10-13T11:00Z passed 0 min ago" + tail,
        "LIMITS claude seven_day: 50%, 60%; resets differ by observation: "
        "50% 2026-10-13T13:00Z (in 120 min), 60% UNKNOWN" + tail,
        "LIMITS claude seven_day_opus: 10%; reset 2026-10-13T10:00Z passed 60 min ago" + tail]


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
        "LIMITS claude a: UNKNOWN" + tail, "LIMITS claude b: UNKNOWN" + tail,
        "LIMITS claude c: RISK 81%" + tail, "LIMITS claude d: UNKNOWN" + tail,
        "LIMITS claude e: 1%" + tail]
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
        "LIMITS claude a: UNKNOWN; reset UNKNOWN (no readable reset observed); "
        "observer provider_timestamp_unknown, quota unknown, oldest age UNKNOWN"]
    [row] = lens.project(value, {})[1][("claude", "a")]
    assert (row["used_percent"], row["reset_utc"], row["observation_age_s"]) == (None, None, None)
    assert lens.number(2 ** 1023) and not lens.number(2 ** 1024)  # the float range is the boundary


def test_a_provider_never_collected_is_unknown_with_the_observer_state():
    lines = render(status(collection={"codex": {"last_success": None, "collection_state": "pending_or_interrupted",
                                                "last_attempt": "2026-09-29T20:24:17+00:00"}}))
    assert ("LIMITS codex: UNKNOWN (observer has no successful collection; state pending_or_interrupted, "
            "last attempt 2026-09-29T20:24)") in lines


def test_activity_is_counted_per_provider_and_names_no_thread():
    # Lead v2 (11-06): per-thread ACTIVITY rows exposed session-specific data; the lens now keeps per provider the
    # thread count, the activity states counted, the latest successful turn and the errors by type with the earliest
    # first error. The 12 h idle fold is gone with the per-thread rows.
    def act(thread, state, last, error=None, first=None, provider="claude"):
        return {"provider": provider, "native_thread_id": thread, "activity_state": state,
                "availability_state": "x", "last_successful_turn_at": last, "error_type": error,
                "first_error_at": first}
    value = status(activity=[act("11111111-x", "idle_observed", "2026-10-11T10:00:00+00:00"),
                             act("22222222-x", "idle_observed", "2026-10-10T10:00:00+00:00", "rate_limited",
                                 "2026-10-10T08:00:00+00:00"),
                             act("33333333-x", "work_requested", "2026-10-13T10:50:00+00:00"),
                             act("44444444-x", "idle_observed", None, "rate_limited", "2026-10-09T08:00:00+00:00"),
                             act("55555555-x", "idle_observed", None, "auth_required", None, provider="codex"),
                             act("66666666-x", 5, None, "", None, provider=None),
                             act("77777777-x", "", None, provider="codex")])  # empty text is no label
    lines = [line for line in render(value) if line.startswith("ACTIVITY")]
    assert lines == [
        "ACTIVITY UNKNOWN: threads 1; UNKNOWN x1; no successful turn observed",
        "ACTIVITY claude: threads 4; idle_observed x3, work_requested x1; latest success 2026-10-13T10:50Z "
        "(10 min ago); error rate_limited x2 since 2026-10-09T08:00",
        "ACTIVITY codex: threads 2; UNKNOWN x1, idle_observed x1; no successful turn observed; error auth_required x1"]
    kept = lens.project(value, {})[3]
    assert kept[1] == {"provider": "claude", "threads": 4,
                       "activity_states": [{"state": "idle_observed", "count": 3},
                                           {"state": "work_requested", "count": 1}],
                       "availability_states": [{"state": "x", "count": 4}],
                       "latest_success_utc": "2026-10-13T10:50:00Z", "minutes_since_latest_success": 10,
                       "errors": [{"error_type": "rate_limited", "count": 2,
                                   "first_error_utc": "2026-10-09T08:00:00Z"}]}
    assert not [line for line in render(value) if any(str(n) * 8 in line for n in range(1, 8))]


def test_publish_replaces_the_file_whole_and_leaves_no_temporary_file(tmp_path):
    # How the lens writes into the live dir (main writes its two cycle-dir files directly); it runs the core's
    # sharing retry, so a core without one fails here.
    target = tmp_path / "capacity"
    lens.publish(target, "lens-limits.txt", b"first\n")
    lens.publish(target, "lens-limits.txt", b"second\n")
    assert (target / "lens-limits.txt").read_bytes() == b"second\n"
    assert [path.name for path in target.iterdir()] == ["lens-limits.txt"]


def synthetic_host(tmp_path, monkeypatch):
    """A synthetic system directory with a Windows PowerShell host file (never run); the lens resolves it."""
    system = tmp_path / "System32"
    host = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    host.parent.mkdir(parents=True, exist_ok=True)
    host.write_bytes(b"synthetic host, never run")
    monkeypatch.setattr(lens, "system_directory", lambda: system)
    return host


def run_hook(tmp_path, monkeypatch, raw, returncode=0, error=None):
    """lens.main() over a fake status run (nothing starts) that prints raw and exits with returncode, or raises error:
    its result or refusal, and the two dirs. The one status run must have the lens's exact argv."""
    out, published, runs = tmp_path / "cycle" / "hook-capacity", tmp_path / "live" / "capacity", []
    host = synthetic_host(tmp_path, monkeypatch)

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
    assert runs == [[str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "status.ps1", "-Json"]]
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
    # The raw status is no longer kept in the cycle dir (it carries account, plan, token-count, payload and catalog
    # fields); the view keeps the sha256 of the raw bytes as its source digest.
    assert result == 0 and sorted(path.name for path in out.iterdir()) == ["capacity-lens.json"]
    view = json.loads((out / "capacity-lens.json").read_text(encoding="ascii"))
    assert view["status_sha256"] == hashlib.sha256(raw).hexdigest()
    assert view["status_age"] == {"checked_utc": "2026-10-13T11:00:30Z", "age_s": 30.0, "state": "FRESH"}
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
    assert [item["used_percent"] for item in row["observations"]] == [None]


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
    elif case == "failed_providers_a_string":
        value["failed_providers"] = "codex"
    elif case == "failed_provider_not_a_string":
        value["failed_providers"] = [None]
    elif case == "alerts_one_object":
        value["alerts"] = {"provider": "codex", "state": "auth_required"}
    elif case == "alert_row_not_an_object":
        value["alerts"] = ["auth_required"]
    return value


MALFORMED = ("observation_not_an_object", "observations_not_a_list", "quota_windows_not_a_list",
             "collection_not_an_object", "activity_not_a_list", "observed_at_missing", "observed_at_not_a_time",
             "last_success_in_month_13", "last_success_before_year_1_in_utc", "observations_false",
             "quota_windows_one_object", "window_not_an_object", "window_name_not_a_string", "collection_an_empty_list",
             "collection_row_not_an_object", "activity_a_string", "activity_row_not_an_object", "observed_at_a_number",
             "last_success_false", "last_success_empty_text", "first_error_not_a_time", "collection_success_not_a_time",
             "collection_attempt_a_number", "observed_at_before_year_1_in_utc", "failed_providers_a_string",
             "failed_provider_not_a_string", "alerts_one_object", "alert_row_not_an_object")


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
    assert ("ACTIVITY claude: threads 1; idle_observed x1; latest success 2026-10-13T10:00Z (60 min ago); "
            "error rate_limited x1 since 2026-10-10T08:00") in lines
    assert "LIMITS codex: UNKNOWN (observer has no successful collection; state pending, last attempt " \
           "2026-09-29T20:24)" in lines
    assert ("COLLECTION codex: NO_SUCCESS; state pending; no successful collection; last attempt 2026-09-29T20:24Z; "
            "no observer TTL (staleness not judged)") in lines
    [row] = lens.project(value, {})[3]
    assert row["errors"][0]["first_error_utc"] == "2026-10-10T08:00:00Z"


# Capacity-lens fix v1 (Lead 11-06, from fable-5's inventory): no raw status persisted, collector health explicit.
SENTINELS = ("sentinel-pool-1d7", "sentinelauthctx5e2", "sentinel-account-type", "sentinel-plan-type", "918273645",
             "546372819", "sentinel-rate-payload", "sentinel-model-catalog", "sentinel-install-dir",
             "sentinel-auth-state", "sentinelalertid9c", "sentinel-limitation")


def identity_status():
    """A status whose identity, plan, token-count, payload, catalog, installation, auth and alert-id fields carry
    sentinel values the lens must never write or publish."""
    value = good_status()
    value["observations"][0].update(
        account_pool=SENTINELS[0], auth_context_id=SENTINELS[1], account_type=SENTINELS[2], plan_type=SENTINELS[3],
        usage={"total_input_tokens": int(SENTINELS[4]), "total_output_tokens": int(SENTINELS[5])},
        payload={"rate_limits": {"primary": SENTINELS[6]}}, catalog={"models": [SENTINELS[7]]})
    value.update(installation={"root": SENTINELS[8]}, limitations=[SENTINELS[11]],
                 collection={"claude": {"last_success": "2026-10-13T10:59:00+00:00", "collection_state": "observed",
                                        "auth_state": SENTINELS[9], "freshness_ttl_seconds": None}},
                 alerts=[{"alert_id": SENTINELS[10], "provider": "claude", "native_thread_id": "11111111-x",
                          "state": "rate_limited", "first_observed_at": "2026-10-13T10:00:00+00:00"}],
                 failed_providers=[])
    return value


def test_no_written_or_published_byte_holds_an_identity_plan_token_payload_or_catalog_value(tmp_path, monkeypatch):
    raw = encoded(identity_status())
    assert all(sentinel.encode() in raw for sentinel in SENTINELS)
    result, out, published = run_hook(tmp_path, monkeypatch, raw)
    assert result == 0
    files = sorted(out.iterdir()) + sorted(published.iterdir())
    assert sorted(path.name for path in files) == ["capacity-lens.json", "capacity-lens.json", "lens-limits.txt"]
    for path in files:
        body = path.read_bytes()
        assert [sentinel for sentinel in SENTINELS if sentinel.encode() in body] == [], path.name
    view = json.loads((published / "capacity-lens.json").read_text(encoding="ascii"))
    assert view["status_sha256"] == hashlib.sha256(raw).hexdigest()
    assert view["alerts"] == [{"provider": "claude", "state": "rate_limited", "count": 1}]


HEALTH_NOW = "2026-11-06T18:00:00+00:00"
# (collection row, failed providers, expected health, expected text after "COLLECTION codex: ")
HEALTH = {
    "ok": ({"last_success": "2026-11-06T17:58:00+00:00", "last_attempt": "2026-11-06T17:57:58+00:00",
            "collection_state": "observed", "freshness_ttl_seconds": 300}, [], "OK",
           "OK; state observed; last success 2026-11-06T17:58Z (2 min ago); last attempt 2026-11-06T17:57Z; "
           "observer TTL 300 s"),
    "ttl_exactly_reached": ({"last_success": "2026-11-06T17:55:00+00:00", "collection_state": "observed",
                             "freshness_ttl_seconds": 300}, [], "OK",
                            "OK; state observed; last success 2026-11-06T17:55Z (5 min ago); no attempt recorded; "
                            "observer TTL 300 s"),
    "ttl_exceeded": ({"last_success": "2026-11-06T17:54:59+00:00", "collection_state": "observed",
                      "freshness_ttl_seconds": 300}, [], "STALE",
                     "STALE; state observed; last success 2026-11-06T17:54Z (5 min ago); no attempt recorded; "
                     "observer TTL 300 s"),
    "old_success_failing_attempts": ({"last_success": "2026-11-06T15:00:00+00:00",
                                      "last_attempt": "2026-11-06T17:59:00+00:00",
                                      "collection_state": "pending_or_interrupted", "freshness_ttl_seconds": 300},
                                     [], "STALE",
                                     "STALE; state pending_or_interrupted; last success 2026-11-06T15:00Z "
                                     "(180 min ago); last attempt 2026-11-06T17:59Z (after the last success); "
                                     "observer TTL 300 s"),
    "attempt_after_a_fresh_success": ({"last_success": "2026-11-06T17:58:00+00:00",
                                       "last_attempt": "2026-11-06T17:59:00+00:00", "collection_state": "observed",
                                       "freshness_ttl_seconds": 300}, [], "STALE",
                                      "STALE; state observed; last success 2026-11-06T17:58Z (2 min ago); last attempt "
                                      "2026-11-06T17:59Z (after the last success); observer TTL 300 s"),
    "failed_state": ({"last_success": "2026-11-06T17:58:00+00:00", "collection_state": "failed",
                      "freshness_ttl_seconds": 300}, [], "FAILED",
                     "FAILED; state failed; last success 2026-11-06T17:58Z (2 min ago); no attempt recorded; "
                     "observer TTL 300 s"),
    "failed_provider": ({"last_success": "2026-11-06T17:58:00+00:00", "collection_state": "observed",
                         "freshness_ttl_seconds": 300}, ["codex"], "FAILED",
                        "FAILED; state observed; last success 2026-11-06T17:58Z (2 min ago); no attempt recorded; "
                        "observer TTL 300 s; failed provider"),
    "never_succeeded": ({"last_success": None, "collection_state": "pending_or_interrupted",
                         "freshness_ttl_seconds": 300}, [], "NO_SUCCESS",
                        "NO_SUCCESS; state pending_or_interrupted; no successful collection; no attempt recorded; "
                        "observer TTL 300 s"),
    "no_ttl": ({"last_success": "2026-11-06T16:00:00+00:00", "collection_state": "observed",
                "freshness_ttl_seconds": None}, [], "UNKNOWN",
               "UNKNOWN; state observed; last success 2026-11-06T16:00Z (120 min ago); no attempt recorded; "
               "no observer TTL (staleness not judged)"),
    "ttl_text": ({"last_success": "2026-11-06T16:00:00+00:00", "freshness_ttl_seconds": "300"}, [], "UNKNOWN",
                 "UNKNOWN; state UNKNOWN; last success 2026-11-06T16:00Z (120 min ago); no attempt recorded; "
                 "no observer TTL (staleness not judged)"),
    "ttl_true": ({"last_success": "2026-11-06T16:00:00+00:00", "freshness_ttl_seconds": True}, [], "UNKNOWN",
                 "UNKNOWN; state UNKNOWN; last success 2026-11-06T16:00Z (120 min ago); no attempt recorded; "
                 "no observer TTL (staleness not judged)"),
    "ttl_zero": ({"last_success": "2026-11-06T16:00:00+00:00", "freshness_ttl_seconds": 0}, [], "UNKNOWN",
                 "UNKNOWN; state UNKNOWN; last success 2026-11-06T16:00Z (120 min ago); no attempt recorded; "
                 "no observer TTL (staleness not judged)"),
    # Mutant survivors of the first run: an attempt at the very time of the success is not after it, and a state
    # that is not text is shown as UNKNOWN (null in the view), never as given.
    "attempt_at_the_success_time": ({"last_success": "2026-11-06T17:58:00+00:00",
                                     "last_attempt": "2026-11-06T17:58:00+00:00", "collection_state": "observed",
                                     "freshness_ttl_seconds": 300}, [], "OK",
                                    "OK; state observed; last success 2026-11-06T17:58Z (2 min ago); last attempt "
                                    "2026-11-06T17:58Z; observer TTL 300 s"),
    "state_not_text": ({"last_success": "2026-11-06T17:58:00+00:00", "collection_state": 5,
                        "freshness_ttl_seconds": 300}, [], "OK",
                       "OK; state UNKNOWN; last success 2026-11-06T17:58Z (2 min ago); no attempt recorded; "
                       "observer TTL 300 s"),
}


@pytest.mark.parametrize("case", sorted(HEALTH))
def test_every_collector_has_a_health_line_and_row_from_the_observer_own_figures(case):
    # Inventory gap 2: only a provider that never succeeded was shown; a 3 h old success with failing attempts
    # printed nothing but "observer stale". Health is FAILED (observer state failed or a failed provider), NO_SUCCESS,
    # STALE (an attempt after the last success, or a success older than the observer's own TTL), UNKNOWN (no usable
    # TTL: the age is shown, staleness is not judged) or OK.
    row, failed, health, text = HEALTH[case]
    value = dict(status(collection={"codex": row}), observed_at=HEALTH_NOW, failed_providers=failed)
    lines = render(value)
    assert [line for line in lines if line.startswith("COLLECTION")] == ["COLLECTION codex: " + text]
    kept = lens.project(value, {})[2]["codex"]
    assert kept["health"] == health and kept["failed_provider"] == (failed == ["codex"])
    assert kept["freshness_ttl_s"] == (300 if row["freshness_ttl_seconds"] == 300 else None)
    assert set(kept) == {"health", "collection_state", "last_success_utc", "minutes_since_success",
                         "last_attempt_utc", "attempt_after_success", "freshness_ttl_s", "failed_provider"}


def test_a_failed_provider_without_a_collection_row_is_still_shown_failed():
    value = dict(status(), observed_at=HEALTH_NOW, failed_providers=["claude"])
    assert [line for line in render(value) if line.startswith("COLLECTION")] == [
        "COLLECTION claude: FAILED; state UNKNOWN; no successful collection; no attempt recorded; "
        "no observer TTL (staleness not judged); failed provider"]


def test_alerts_are_counted_by_provider_and_state_and_nothing_else_of_them_is_kept():
    def alert(provider, state):
        return {"alert_id": "a" * 32, "provider": provider, "native_thread_id": "33333333-x", "state": state,
                "first_observed_at": "2026-11-06T17:00:00+00:00"}
    value = dict(status(), observed_at=HEALTH_NOW,
                 alerts=[alert("codex", "auth_required"), alert("claude", "rate_limited"),
                         alert("claude", "rate_limited"), alert("claude", 7)])
    kept = lens.project(value, {})[4]
    assert kept == [{"provider": "claude", "state": "UNKNOWN", "count": 1},
                    {"provider": "claude", "state": "rate_limited", "count": 2},
                    {"provider": "codex", "state": "auth_required", "count": 1}]
    assert "ALERTS: 4 (claude UNKNOWN x1, claude rate_limited x2, codex auth_required x1)" in render(value)
    assert not [line for line in render(dict(value, alerts=[])) if line.startswith("ALERTS")]


# Capacity-lens fix v2 (Lead 11-06): no thread identifier in any written or published byte.
CANARY_THREADS = ("c0ffee11-aaaa-4bbb-8ccc-000000000001", "deadbe22-aaaa-4bbb-8ccc-000000000002",
                  "faded033-aaaa-4bbb-8ccc-000000000003", "beaded44-aaaa-4bbb-8ccc-000000000004")


def keys_of(value):
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in keys_of(item)}
    if isinstance(value, list):
        return {key for item in value for key in keys_of(item)}
    return set()


def test_no_written_or_published_byte_names_a_thread_and_provider_signals_stay(tmp_path, monkeypatch):
    reset = 1792000800  # 2026-10-14T18:00:00Z
    value = status([observation(CANARY_THREADS[0], {"name": "seven_day", "used_percent": 31, "resets_at": reset}),
                    observation(CANARY_THREADS[1], {"name": "seven_day", "used_percent": 33, "resets_at": reset})],
                   [{"provider": "claude", "native_thread_id": CANARY_THREADS[2], "activity_state": "idle_observed",
                     "last_successful_turn_at": "2026-10-13T10:00:00+00:00", "error_type": "rate_limited",
                     "first_error_at": "2026-10-13T09:00:00+00:00"}])
    value["alerts"] = [{"alert_id": "b" * 32, "provider": "claude", "native_thread_id": CANARY_THREADS[3],
                        "state": "rate_limited", "first_observed_at": "2026-10-13T09:00:00+00:00"}]
    raw = encoded(value)
    result, out, published = run_hook(tmp_path, monkeypatch, raw)
    assert result == 0
    for path in sorted(out.iterdir()) + sorted(published.iterdir()):
        body = path.read_bytes()
        assert [thread for thread in CANARY_THREADS if thread[:8].encode() in body] == [], path.name
    view = json.loads((published / "capacity-lens.json").read_text(encoding="ascii"))
    assert not keys_of(view) & {"thread", "threads_by_id", "native_thread_id", "alert_id", "first_observed_at"}
    [row] = view["limits"]
    assert (row["provider"], row["window"]) == ("claude", "seven_day")
    assert [(item["used_percent"], item["reset_utc"], item["lane"]) for item in row["observations"]] == [
        (31, "2026-10-14T18:00:00Z", None), (33, "2026-10-14T18:00:00Z", None)]
    assert view["activity"] == [{"provider": "claude", "threads": 1,
                                 "activity_states": [{"state": "idle_observed", "count": 1}],
                                 "availability_states": [{"state": "UNKNOWN", "count": 1}],
                                 "latest_success_utc": "2026-10-13T10:00:00Z", "minutes_since_latest_success": 60,
                                 "errors": [{"error_type": "rate_limited", "count": 1,
                                             "first_error_utc": "2026-10-13T09:00:00Z"}]}]
    lines = (published / "lens-limits.txt").read_text(encoding="ascii").splitlines()
    assert "LIMITS claude seven_day: 31%, 33%; resets 2026-10-14T18:00Z (in 1860 min); observer " \
           "provider_timestamp_unknown, quota unknown, under 1 min old" in lines


# PR 2041 review K1 (Lead 11-06 19:42Z): every observer string the lens copies is a known provider or a short safe
# label (lowercase words joined by underscores); anything else, e-mail-, path- or token-like, reads UNKNOWN.
UNSAFE = ("ops.user" + chr(64) + "example.com", "C:/Users/someone/secret.txt",
          "C:" + chr(92) + "Users" + chr(92) + "vault", "ghp_A1b2C3d4E5f6G7h8I9j0kLmN",
          "deadbeefcafebabe0123456789abcdef", "qwertyuiopasdfghjklzxcvbnmqwertyuiop",
          "Bearer abcdef", "../../etc/passwd", "seven_day ", "a1b2c3d4e5f6")


def unsafe_status(label):
    value = status([observation("11111111-a", {"name": label, "used_percent": 12, "resets_at": 1792000800},
                                provider=label, freshness=label, quota=label)],
                   [{"provider": label, "native_thread_id": "22222222-b", "activity_state": label,
                     "availability_state": label, "last_successful_turn_at": "2026-10-13T10:00:00+00:00",
                     "error_type": label, "first_error_at": "2026-10-13T09:00:00+00:00"}],
                   collection={label: {"last_success": "2026-10-13T10:59:00+00:00", "collection_state": label,
                                       "freshness_ttl_seconds": 300}})
    value.update(failed_providers=[label], alerts=[{"alert_id": "c" * 32, "provider": label,
                                                    "native_thread_id": "33333333-c", "state": label}])
    return value


@pytest.mark.parametrize("label", UNSAFE)
def test_an_unsafe_observer_label_never_reaches_an_output_byte_and_reads_unknown(tmp_path, monkeypatch, label):
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(unsafe_status(label)))
    assert result == 0
    for path in sorted(out.iterdir()) + sorted(published.iterdir()):
        body = path.read_bytes()
        assert label.encode() not in body and json.dumps(label)[1:-1].encode() not in body, path.name
    lines = (published / "lens-limits.txt").read_text(encoding="ascii").splitlines()
    assert lines[1:] == [
        "LIMITS UNKNOWN: 1 unrecognized provider observations, figures not shown",
        "COLLECTION UNKNOWN: 1 unrecognized provider rows and 1 unrecognized failed providers, figures not shown",
        "ALERTS: 1 (UNKNOWN UNKNOWN x1)",
        "ACTIVITY UNKNOWN: threads 1; UNKNOWN x1; latest success 2026-10-13T10:00Z (60 min ago); error UNKNOWN x1 "
        "since 2026-10-13T09:00"]


def test_known_providers_and_short_safe_labels_pass_and_an_unknown_provider_does_not():
    value = status([observation("11111111-a", {"name": "seven_day_opus", "used_percent": 3}, provider="codex",
                                freshness="unknown_or_stale", quota="observed_headroom"),
                    observation("22222222-b", {"name": "five_hour", "used_percent": 4}, provider="gemini")],
                   collection={"claude": {"last_success": None, "collection_state": "pending_or_interrupted"}})
    lines = render(value)
    assert [line.split(";")[0] for line in lines if line.startswith(("LIMITS", "COLLECTION"))] == [
        "LIMITS codex seven_day_opus: 3%",
        "LIMITS UNKNOWN: 1 unrecognized provider observations, figures not shown",
        "LIMITS claude: UNKNOWN (observer has no successful collection", "COLLECTION claude: NO_SUCCESS"]
    assert "observer unknown_or_stale, quota observed_headroom" in lines[1]


# PR 2041 review K2: the age of the whole status against the lens's own UTC clock (injected here).
CLOCK_CASES = {
    "same_second": ("2026-10-13T11:00:00+00:00", 0.0, "FRESH", "status age under 1 min, FRESH"),
    "fresh": ("2026-10-13T11:00:30+00:00", 30.0, "FRESH", "status age under 1 min, FRESH"),
    "at_the_limit": ("2026-10-13T11:02:00+00:00", 120.0, "FRESH", "status age 2 min, FRESH"),
    "just_over": ("2026-10-13T11:02:01+00:00", 121.0, "STALE", "status age 2 min, STALE"),
    "hours_old": ("2026-10-13T14:00:00+00:00", 10800.0, "STALE", "status age 180 min, STALE"),
    "one_second_ahead": ("2026-10-13T10:59:59+00:00", -1.0, "FUTURE",
                         "status time under 1 min ahead of the lens clock, FUTURE (invalid)"),
    "ten_min_ahead": ("2026-10-13T10:50:00+00:00", -600.0, "FUTURE",
                      "status time 10 min ahead of the lens clock, FUTURE (invalid)"),
}


@pytest.mark.parametrize("case", sorted(CLOCK_CASES))
def test_the_whole_status_age_is_measured_against_the_lens_clock(monkeypatch, case):
    checked, age, state, shown = CLOCK_CASES[case]
    monkeypatch.setattr(lens, "clock", lambda: lens.parse_time(checked))
    assert lens.project(status(), {})[5] == {"checked_utc": lens.utc(lens.parse_time(checked)), "age_s": age,
                                             "state": state}
    assert render(status())[0].startswith(
        f"capacity (installed observer, read-only) observed 2026-10-13T11:00:00Z ({shown}); ")


def test_the_lens_clock_is_the_real_utc_clock():
    import datetime as dt
    assert REAL_CLOCK is not None
    checked = REAL_CLOCK()
    assert checked.utcoffset() == dt.timedelta(0)
    assert abs((checked - dt.datetime.now(dt.timezone.utc)).total_seconds()) < 5


# RCO2 residuals (Lead 11-06 20:17Z): no last-wins row for unrecognized providers, no future status, stale lines say so.
def test_unrecognized_provider_rows_are_counted_never_shown_as_one_provider():
    value = dict(status(collection={"gemini": {"last_success": "2026-10-13T10:59:00+00:00",
                                               "collection_state": "observed", "freshness_ttl_seconds": 300},
                                    "mistral": {"last_success": None, "collection_state": "failed"},
                                    "codex": {"last_success": "2026-10-13T10:59:00+00:00",
                                              "collection_state": "observed", "freshness_ttl_seconds": 300}}),
                 failed_providers=["gemini", "llama"])
    lines = [line for line in render(value) if line.startswith(("COLLECTION", "LIMITS"))]
    assert lines == [
        "COLLECTION codex: OK; state observed; last success 2026-10-13T10:59Z (1 min ago); no attempt recorded; "
        "observer TTL 300 s",
        "COLLECTION UNKNOWN: 2 unrecognized provider rows and 2 unrecognized failed providers, figures not shown"]
    kept = lens.project(value, {})
    assert sorted(kept[2]) == ["codex"]
    assert kept[6] == {"observations": 0, "collection_rows": 2, "failed_providers": 2}
    view = lens.view_of(b"{}", lens.readable(value, {}))
    assert view["unrecognized_providers"] == {"observations": 0, "collection_rows": 2, "failed_providers": 2}
    malformed = dict(value, collection={"gemini": {"last_success": "soon"}})
    with pytest.raises(lens.LensRefused):
        lens.readable(malformed, {})


def test_a_future_status_is_refused_whole_and_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(lens, "clock", lambda: lens.parse_time("2026-10-13T10:59:59+00:00"))
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(good_status()))
    assert result == "refused: status_time_future" and not out.exists() and not published.exists()


def test_every_figure_line_of_a_stale_status_says_so():
    monkeypatch_clock = lens.clock
    try:
        lens.clock = lambda: lens.parse_time("2026-10-13T14:00:00+00:00")
        value = dict(good_status(), collection={"codex": {"last_success": "2026-10-13T10:59:00+00:00",
                                                          "collection_state": "observed",
                                                          "freshness_ttl_seconds": 300}})
        lines = lens.readable(value, {})[-1]
        view = lens.view_of(b"{}", lens.readable(value, {}))
    finally:
        lens.clock = monkeypatch_clock
    assert "(status age 180 min, STALE)" in lines[0] and not lines[0].endswith(" (status STALE)")
    assert len(lines) == 4 and all(line.endswith(" (status STALE)") for line in lines[1:])
    assert [row["status_state"] for row in view["limits"]] == ["STALE"]


# CR1 (RCO2 on PR 2044): the status host is the exact System32 Windows PowerShell, never a name the OS searches for
# in the current directory or PATH, where a planted file of that name could run.
def test_a_planted_powershell_in_the_current_directory_or_path_is_never_the_status_host(tmp_path, monkeypatch):
    planted = tmp_path / "planted"
    planted.mkdir()
    for name in ("powershell.exe", "powershell", "powershell.bat", "powershell.cmd"):
        (planted / name).write_bytes(b"planted, must never run")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", str(planted))
    monkeypatch.setenv("SystemRoot", str(planted))
    monkeypatch.setenv("WINDIR", str(planted))
    result, out, published = run_hook(tmp_path, monkeypatch, encoded(good_status()))
    assert result == 0  # run_hook asserted that the one run used the synthetic System32 host by its full path


@pytest.mark.parametrize("case", ["missing", "directory", "link", "relative", "no_system_directory", "no_windll"])
def test_a_missing_or_irregular_status_host_is_refused_before_any_run(tmp_path, monkeypatch, case):
    system = tmp_path / "System32"
    host = system / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    host.parent.mkdir(parents=True)
    if case == "directory":
        host.mkdir()
    elif case == "link":
        target = tmp_path / "elsewhere.exe"
        target.write_bytes(b"synthetic")
        try:
            host.symlink_to(target)
        except OSError:
            pytest.skip("this account may not create symbolic links")
    elif case == "relative":
        host.write_bytes(b"synthetic host")
        monkeypatch.chdir(tmp_path)
        system = Path("System32")

    def directory():
        if case == "no_system_directory":
            raise OSError("GetSystemDirectoryW failed")
        if case == "no_windll":
            raise AttributeError("module 'ctypes' has no attribute 'windll'")
        return system

    runs = []
    monkeypatch.setattr(lens, "system_directory", directory)
    monkeypatch.setattr(lens, "subprocess", types.SimpleNamespace(run=lambda *args, **kwargs: runs.append(args),
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(lens.LensRefused, match="^status_host_missing$"):
        lens.read_status(Path("status.ps1"))
    assert runs == []


def test_a_status_host_that_is_a_reparse_point_is_refused_even_when_it_reads_as_a_regular_file(tmp_path,
                                                                                                 monkeypatch):
    # An app execution alias or another reparse point can read as a regular file; only its attribute shows it.
    host, real, runs = synthetic_host(tmp_path, monkeypatch), lens.os.lstat, []

    def lstat(path, *args, **kwargs):
        info = real(path, *args, **kwargs)
        return types.SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400) if Path(path) == host else info

    monkeypatch.setattr(lens.os, "lstat", lstat)
    monkeypatch.setattr(lens, "subprocess", types.SimpleNamespace(run=lambda *args, **kwargs: runs.append(args),
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(lens.LensRefused, match="^status_host_missing$"):
        lens.read_status(Path("status.ps1"))
    assert runs == []


@pytest.mark.skipif(sys.platform != "win32", reason="the System32 Windows PowerShell host exists only on Windows")
def test_the_real_status_host_is_the_system32_windows_powershell_whatever_the_environment_says(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    monkeypatch.setenv("WINDIR", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    host = lens.powershell_host()
    assert host.is_absolute() and host.is_file() and not host.is_relative_to(tmp_path)
    assert [part.lower() for part in host.parts[-4:]] == ["system32", "windowspowershell", "v1.0", "powershell.exe"]


@pytest.mark.skipif(sys.platform == "win32", reason="off Windows only")
def test_off_windows_there_is_no_status_host():
    with pytest.raises(lens.LensRefused, match="^status_host_missing$"):
        lens.powershell_host()


# CR3: observations of different unrecognized providers are counted, never shown as one provider's figures.
def test_observations_of_different_unrecognized_providers_are_counted_never_combined():
    value = status([observation("11111111-a", {"name": "five_hour", "used_percent": 4}, provider="gemini"),
                    observation("22222222-b", {"name": "five_hour", "used_percent": 97}, provider="mistral"),
                    observation("33333333-c", {"name": "five_hour", "used_percent": 5}, provider="codex")])
    lines = [line for line in render(value) if line.startswith("LIMITS")]
    assert [line.split(";")[0] for line in lines] == [
        "LIMITS codex five_hour: 5%", "LIMITS UNKNOWN: 2 unrecognized provider observations, figures not shown"]
    kept = lens.project(value, {})
    assert list(kept[1]) == [("codex", "five_hour")] and kept[6]["observations"] == 2
    view = lens.view_of(b"{}", lens.readable(value, {}))
    assert [row["provider"] for row in view["limits"]] == ["codex"]
    assert view["unrecognized_providers"]["observations"] == 2
    unnamed = dict(value, observations=value["observations"] + [{"provider": "llama", "quota_windows": [{}]}])
    with pytest.raises(lens.LensRefused):  # an unrecognized provider's figures are still shape-checked
        lens.readable(unnamed, {})

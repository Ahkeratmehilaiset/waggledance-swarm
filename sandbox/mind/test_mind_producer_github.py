"""Lead's slice mind-producer-github-sandbox-20261019 (operator item 4a/b, 10-19): the capture producer's own cycle
captures and binds the GitHub files the S1 chain carries to the real JOIN. Before this slice the producer wrote only
context.json, so every live cycle loaded as no GitHub and every PR abstained (RCO1 14:27: its capture_sha256 also
hashes backfill rows, so a raw list could never link). Now, beside the context, one cycle binds in its last_ok:
gh-pr-list.json (the raw bytes gh gave for the list the context was made from; context.capture_sha256 is their
sha256), gh-open-status.json (the raw bytes of one gh pr list of the open PRs' heads, merge states and check rollups)
and observed-status.json (the status derived from them, as capture/capture_round.py derives it, list_sha256 = the raw
bytes' sha256), each as a (file, sha256) pair. The three are bound together or not at all: a failing or unreadable
status read, an unreadable required-check set, a list whose rows were backfilled one by one, an open PR whose head
differs between the two reads, or a status read outside the context's currency window leaves them unbound (UNKNOWN),
and a write that fails refuses the cycle before its commit. No owed work is made up and no all-PR completeness is
claimed. The cases run the producer's own candidate_cycle (its functions from the module file, in the namespace
test_mind_capture_producer_state.py builds) with a fake bridge (the step-1 PR1905 events) and a fake gh, then the
chain's freeze and the real JOIN. Synthetic only: no gh, network, data disk, producer start or live feed.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_mind_producer_github.py -q -p no:cacheprovider
"""
import datetime
import hashlib
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_turn as jt  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (step-1 fixtures: PR1905 at head B, one empty build_tools slot)
import test_mind_capture_producer_state as tps  # noqa: E402  (the producer namespace and its fake bridge)
import test_mind_chain_real_join as rj  # noqa: E402  (the chain driver loaded from its file)

DRIVER = rj.DRIVER
LANES, LEARN_REF = rj.LANES, rj.LEARN_REF
POINTER = {"context.json": ("context_file", "context_sha256"), "observed-status.json": ("status_file", "status_sha256"),
           "gh-pr-list.json": ("pr_list_file", "pr_list_sha256"),
           "gh-open-status.json": ("open_status_file", "open_status_sha256")}  # literal: never a module's map
GITHUB_RAW = ("observed-status.json", "gh-pr-list.json", "gh-open-status.json")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def gh_bytes(value):
    """gh's own output form is not canonical: indented, CRLF line ends, a final line end."""
    return json.dumps(value, indent=2).replace("\n", "\r\n").encode("utf-8") + b"\r\n"


ROLLUP = [{"__typename": "CheckRun", "completedAt": "2026-10-13T17:39:09Z", "conclusion": "SUCCESS", "name": name,
           "status": "COMPLETED", "detailsUrl": "https://example.invalid/run"} for name in tlv.REQUIRED]
LISTING = gh_bytes([{"headRefName": tlv.BRANCH, "headRefOid": tlv.NEW, "number": tlv.NUMBER, "state": "OPEN"}])
OPEN = gh_bytes([{"headRefOid": tlv.NEW, "mergeStateStatus": "CLEAN", "number": tlv.NUMBER,
                  "statusCheckRollup": ROLLUP}])
REQUIRED = json.dumps(tlv.REQUIRED).encode("utf-8") + b"\n"


class Gh:
    """Answers the producer's gh calls from fixed bytes (None: gh exits non-zero) and records each call."""

    def __init__(self, listing=LISTING, open_status=OPEN, required=REQUIRED, rows=None):
        self.listing, self.open_status, self.required, self.rows, self.calls = listing, open_status, required, \
            rows or {}, []

    def __call__(self, argv, **kwargs):
        assert argv[0] == "gh" and kwargs.get("timeout")
        call = argv[1:]
        self.calls.append(call)
        if call[:2] == ["pr", "list"]:
            data = self.listing if call[call.index("--state") + 1] == "all" else self.open_status
        elif call[:2] == ["pr", "view"]:
            fields = call[call.index("--json") + 1]
            data = tps.pr_view(tlv.NEW, *tlv.AUTHORS) if fields == "headRefOid,commits" else self.rows.get(call[2])
        elif call[0] == "api":
            data = self.required
        else:
            raise AssertionError(call)
        return tps.answer(1, b"") if data is None else tps.answer(0, data)

    def open_read(self):
        return any(call[:2] == ["pr", "list"] and "open" in call for call in self.calls)


def producer(tmp_path, gh):
    """The producer's candidate_cycle over a live dir inside a .codex-audit root: the fake bridge holds the PR1905
    events, every other start is the fake gh."""
    live = tmp_path / ".codex-audit" / "live"
    for name in ("cycles", "files-cache"):
        (live / name).mkdir(parents=True)
    bridge = tps.PrBridge(tlv.base_events())
    space, _ = tps.load(bridge=bridge, live=live)
    run = lambda argv, **kwargs: gh(argv, **kwargs) if argv[0] == "gh" else bridge(argv, **kwargs)  # noqa: E731
    space["subprocess"] = types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired)
    return space, live


def commit(space, live):
    rec = {"cycle": 1, "started_utc": "s", "reader": space["READER"]}
    last_ok = space["candidate_cycle"](rec, None, {})
    space["atomic_write"](live / "latest.json", space["canonical"](space["latest_object"](last_ok)))
    return last_ok, rec


def chain(tmp_path, live):
    frozen = tmp_path / ".codex-audit" / "frozen"
    result = DRIVER.run_committed_chain(live, frozen)
    cycle, = fz.load_frozen(frozen)["cycles"]
    return result, cycle, frozen


def test_a_producer_cycle_binds_the_raw_gh_lists_and_the_derived_status_the_lane_view_links(tmp_path):
    space, live = producer(tmp_path, Gh())
    last_ok, rec = commit(space, live)
    folder = live / "cycles" / last_ok["cycle_dir"]
    for name, (file_key, sha_key) in POINTER.items():
        assert last_ok[file_key] == f"cycles/{last_ok['cycle_dir']}/{name}"
        assert last_ok[sha_key] == digest((folder / name).read_bytes())
    assert (folder / "gh-pr-list.json").read_bytes() == LISTING  # byte-exact, never re-serialized
    assert (folder / "gh-open-status.json").read_bytes() == OPEN
    context = json.loads((folder / "context.json").read_bytes())
    status = json.loads((folder / "observed-status.json").read_bytes())
    assert context["capture_sha256"] == digest(LISTING) and status["list_sha256"] == digest(OPEN)
    assert status["schema"] == "wd.bridge-mind-gh-status.v1" and status["authority"] == "none"
    assert status["required_contexts"] == tlv.REQUIRED and status["mentions_captured"] is False
    assert status["prs"] == {str(tlv.NUMBER): {
        "head": tlv.NEW, "merge_state": "CLEAN", "required_results": {name: ["SUCCESS"] for name in tlv.REQUIRED},
        "missing_required": [], "all_required_success": True}}
    observed = [datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
                for value in (context["observed_utc"], status["observed_utc"])]
    assert datetime.timedelta(0) <= observed[1] - observed[0] <= datetime.timedelta(minutes=15)
    found = rec["github_status"]
    assert (found["status"], found["open_prs"], found["observed_utc"]) == ("observed", 1, status["observed_utc"])
    assert space["committed_last_ok"](last_ok)


def test_the_producer_cycle_reaches_the_real_join_with_usable_github_and_one_owed_vote(tmp_path):
    space, live = producer(tmp_path, Gh())
    commit(space, live)
    result, cycle, _ = chain(tmp_path, live)
    assert cycle["github"] is not None and cycle["nulls"] == []
    assert result["join_calls_completed"] == len(LANES)
    for lane, card in zip(LANES, result["cards"]):
        expected = jt.turn_case(lane, cycle, learn_ref=LEARN_REF, envelope=None, receipts=None)
        assert card["text"] == expected["card"] and card["github_usable"] is True
        assert card["coverage"] == "UNKNOWN"
    view = jt.turn_case("codex-tools-1", cycle, learn_ref=LEARN_REF, envelope=None, receipts=None)["view"]
    owed = {lane: found for lane, found in tlv.lines_for(view).items() if found}
    assert list(owed) == ["codex-tools-1"] and len(owed["codex-tools-1"]) == 1
    assert f"build vote on PR{tlv.NUMBER} at {tlv.NEW[:12]} is the only empty slot" in owed["codex-tools-1"][0]


def _moved(minutes):
    def setup(space, gh):
        real = space["utc_now"]

        def clock(seconds=False):
            now = real(seconds)
            if not gh.open_read():
                return now
            moved = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=minutes)
            return moved.strftime("%Y-%m-%dT%H:%M:%SZ") if seconds else moved.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        space["utc_now"] = clock
    return setup


_later = _moved(20)


def open_row(head=tlv.NEW, number=tlv.NUMBER, rollup=None, **extra):
    return dict({"headRefOid": head, "mergeStateStatus": "CLEAN", "number": number,
                 "statusCheckRollup": ROLLUP if rollup is None else rollup}, **extra)


def check(name, conclusion):
    return {"__typename": "CheckRun", "completedAt": "2026-10-13T17:39:09Z", "conclusion": conclusion, "name": name,
            "status": "COMPLETED", "detailsUrl": "https://example.invalid/run"}


SECOND_LISTED = [{"headRefName": tlv.BRANCH, "headRefOid": tlv.NEW, "number": tlv.NUMBER, "state": "OPEN"},
                 {"headRefName": tlv.SECOND_BRANCH, "headRefOid": tlv.SECOND_HEAD, "number": tlv.SECOND,
                  "state": "MERGED"}]


@pytest.mark.parametrize("rollup, reason, results, missing", [
    ([check(tlv.REQUIRED[0], "FAILURE")] + ROLLUP[2:], "required_check_missing",
     {tlv.REQUIRED[0]: ["FAILURE"], **{name: ["SUCCESS"] for name in tlv.REQUIRED[2:]}}, [tlv.REQUIRED[1]]),
    ([check(tlv.REQUIRED[0], "FAILURE")] + ROLLUP[1:], "required_check_not_success",
     {tlv.REQUIRED[0]: ["FAILURE"], **{name: ["SUCCESS"] for name in tlv.REQUIRED[1:]}}, []),
    ([check(tlv.REQUIRED[0], "FAILURE")] + ROLLUP, "required_check_duplicate",
     {tlv.REQUIRED[0]: ["FAILURE", "SUCCESS"], **{name: ["SUCCESS"] for name in tlv.REQUIRED[1:]}}, []),
], ids=["failed_and_missing", "failed", "rerun_mixed"])
def test_a_required_check_that_failed_or_is_missing_is_reported_so_and_owes_no_vote(
        tmp_path, rollup, reason, results, missing):
    # RCO2 14:57 T1: every other fixture is all-green, so the derived all_required_success and missing_required were
    # pinned only for success; here the status says what GitHub said, and the lane view still abstains on the PR
    space, live = producer(tmp_path, Gh(open_status=gh_bytes([open_row(rollup=rollup)])))
    last_ok, rec = commit(space, live)
    assert rec["github_status"]["status"] == "observed"
    status = json.loads((live / last_ok["status_file"]).read_bytes())
    assert status["prs"] == {str(tlv.NUMBER): {"head": tlv.NEW, "merge_state": "CLEAN", "required_results": results,
                                                "missing_required": missing, "all_required_success": False}}
    result, cycle, _ = chain(tmp_path, live)
    assert cycle["github"] is not None and all(card["github_usable"] is True for card in result["cards"])
    tlv.assert_abstains(jt.turn_case("codex-tools-1", cycle, learn_ref=LEARN_REF, envelope=None, receipts=None)["view"],
                        reason)


@pytest.mark.parametrize("gh, setup, reason", [
    (Gh(open_status=None), None, "gh_status_unavailable"),
    (Gh(open_status=b"{not json"), None, "gh_status_unexpected"),
    (Gh(required=None), None, "required_set_unknown"),
    (Gh(required=b"[]"), None, "required_set_unknown"),
    (Gh(open_status=gh_bytes([{"headRefOid": tlv.OLD, "mergeStateStatus": "CLEAN", "number": tlv.NUMBER,
                                "statusCheckRollup": ROLLUP}])), None, "head_conflict"),
    (Gh(open_status=gh_bytes([])), None, "head_conflict"),
    # two merged rows reach the patched limit of 2, so PR1905 is read on its own; the open list (one row) stays
    # below it, so a backfilled list handed on as raw would reach the link check, not the open-list limit
    (Gh(listing=gh_bytes([{"headRefName": f"lane-a/other-{n}", "headRefOid": str(n) * 40, "number": n,
                           "state": "MERGED"} for n in (7, 8)]),
        rows={str(tlv.NUMBER): json.dumps({"headRefName": tlv.BRANCH, "headRefOid": tlv.NEW, "number": tlv.NUMBER,
                                            "state": "OPEN"}).encode("utf-8")}),
     lambda space, gh: space.update(LIST_LIMIT=2), "list_backfilled"),
    (Gh(), _later, "status_not_current"),
    (Gh(), _moved(-20), "status_not_current"),
    (Gh(listing=None), None, "context_unknown"),
    (Gh(), lambda space, gh: space.update(GH_BUDGET=0), "gh_budget_spent"),
    (Gh(), lambda space, gh: space.update(LIST_LIMIT=1), "gh_status_truncated"),
    (Gh(open_status=gh_bytes([open_row(labels=[])])), None, "gh_status_unexpected"),
    (Gh(open_status=gh_bytes([open_row(), open_row()])), None, "gh_status_unexpected"),
    (Gh(listing=gh_bytes(SECOND_LISTED),
        open_status=gh_bytes([open_row(), open_row(tlv.SECOND_HEAD, tlv.SECOND)])), None, "head_conflict"),
], ids=["status_read_fails", "status_not_json", "required_unreadable", "required_empty", "head_conflict",
        "open_pr_missing", "list_backfilled", "status_stale", "status_before_context", "context_unknown",
        "budget_spent", "open_list_at_limit", "open_row_other_fields", "open_row_repeated", "merged_pr_open_again"])
def test_a_status_that_is_missing_failing_stale_or_in_conflict_leaves_the_github_files_unbound(
        tmp_path, gh, setup, reason):
    space, live = producer(tmp_path, gh)
    if setup is not None:
        setup(space, gh)
    last_ok, rec = commit(space, live)
    assert rec["github_status"]["status"] == "UNKNOWN" and rec["github_status"]["reason"] == reason
    for name in GITHUB_RAW:
        file_key, sha_key = POINTER[name]
        assert last_ok[file_key] is None and last_ok[sha_key] is None
        assert not (live / "cycles" / last_ok["cycle_dir"] / name).exists()
    result, cycle, _ = chain(tmp_path, live)
    assert cycle["github"] is None and all(card["github_usable"] is False for card in result["cards"])
    view = jt.turn_case("codex-tools-1", cycle, learn_ref=LEARN_REF, envelope=None, receipts=None)["view"]
    assert not any(tlv.lines_for(view).values())


def test_prewrite_refuses_a_github_status_whose_raw_lists_do_not_link_before_any_write(tmp_path):
    space, live = producer(tmp_path, Gh())
    last_ok, _ = commit(space, live)
    folder = live / "cycles" / last_ok["cycle_dir"]
    capture = json.loads((folder / "capture.json").read_bytes())
    manifest = (folder / "detail-manifest.txt").read_bytes()
    context = json.loads((folder / "context.json").read_bytes())
    state = json.loads((folder / "state.json").read_bytes())
    status = json.loads((folder / "observed-status.json").read_bytes())
    payloads = space["prewrite"](capture, manifest, context, state, (status, LISTING, OPEN))
    assert (payloads["gh-pr-list.json"], payloads["gh-open-status.json"]) == (LISTING, OPEN)
    assert payloads["observed-status.json"] == (folder / "observed-status.json").read_bytes()
    for github in ((status, LISTING + b" ", OPEN), (status, LISTING, OPEN + b" ")):
        before = sorted(path.name for path in folder.iterdir())
        with tps.refused(space["CaptureRefused"], "candidate_invalid"):
            space["prewrite"](capture, manifest, context, state, github)
        assert sorted(path.name for path in folder.iterdir()) == before  # nothing is written


def test_a_github_file_write_that_fails_refuses_the_cycle_before_any_pointer(tmp_path):
    space, live = producer(tmp_path, Gh())
    real = space["write_new"]

    def failing(path, data):
        if path.name == "gh-open-status.json":
            raise OSError("synthetic")
        return real(path, data)

    space["write_new"] = failing
    with tps.refused(space["CaptureRefused"], "candidate_unwritable"):
        space["candidate_cycle"]({"cycle": 1, "started_utc": "s", "reader": space["READER"]}, None, {})
    assert not (live / "latest.json").exists()

"""Lead's slice mind-github-join-sandbox-20261019 (operator item 4a/b, 10-19): the S1 chain carries the GitHub capture a
committed cycle binds, from the producer's pointer through the freeze to the REAL JOIN. Before this slice the freeze
nulled every GitHub file but context.json and the JOIN refused any usable GitHub view, so the real lane view abstained
on every PR (D19's measured gap; RCO1's 14:08:42Z points R1-R3). Each GitHub file the committed pointer binds
(context_file/context_sha256, status_file/status_sha256, pr_list_file/pr_list_sha256 and
open_status_file/open_status_sha256, each pair both null or both set) must be copied byte-exact, so the raw gh list
digests the lane view links still match, and a usable capture gives the cards JOIN writes for the same cycle held in
memory. Missing, stale, unlinked or head-mismatched input stays UNKNOWN (every such PR abstains; no owed work is made
up), and a pointer whose GitHub pair is half set, misplaced or names other bytes is refused before any output.
Synthetic gh-output fixtures only (the step-1 PR1905 fixtures, raw lists as gh prints them): no producer, gh,
network, data disk or live feed; JOIN with envelope=None and receipts=None as the driver calls it, so a pass here is
JOIN only and claims nothing about the producer's capture, its timing or utility.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_mind_chain_github.py -q -p no:cacheprovider
"""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[1]), str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import join_turn as jt  # noqa: E402
import lane_view_frozen as fz  # noqa: E402
import test_lane_view as tlv  # noqa: E402  (step-1 fixtures: the real core writes the snapshot rows)
import test_mind_chain_real_join as rj  # noqa: E402  (D19's committed core cycle and its driver load)
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

DRIVER = rj.DRIVER
LANES, CYCLE, LEARN_REF = rj.LANES, rj.CYCLE, rj.LEARN_REF
# literal: never the module's map
POINTER = {"context.json": ("context_file", "context_sha256"), "observed-status.json": ("status_file", "status_sha256"),
           "gh-pr-list.json": ("pr_list_file", "pr_list_sha256"),
           "gh-open-status.json": ("open_status_file", "open_status_sha256")}
ALL = tuple(POINTER)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def gh_raw(value):
    """A raw gh --json output, NOT the canonical form: indented, CRLF line ends and a final line end, so a
    re-serialization or a line-end change anywhere on the way breaks the digests the lane view links."""
    return json.dumps(value, indent=2).replace("\n", "\r\n").encode("utf-8") + b"\r\n"


def github_files(github):
    """The four GitHub files of one cycle and the github object the frozen loader builds from them: the derived objects
    canonical through the core, the raw lists as gh prints them. A derived file linked to its list (the fixture links
    the canonical form) is linked to the raw bytes instead; a case that broke the link keeps it broken."""
    github = copy.deepcopy(github)
    pr_list, open_status = gh_raw(github["pr_list"]), gh_raw(github["open_status"])
    if github["context"]["capture_sha256"] == digest(tlv.lv.canonical(github["pr_list"]).encode("utf-8")):
        github["context"]["capture_sha256"] = digest(pr_list)
    if github["status"]["list_sha256"] == digest(tlv.lv.canonical(github["open_status"]).encode("utf-8")):
        github["status"]["list_sha256"] = digest(open_status)
    files = {"context.json": core.canonical_bytes(github["context"]),
             "observed-status.json": core.canonical_bytes(github["status"]),
             "gh-pr-list.json": pr_list, "gh-open-status.json": open_status}
    loaded = {"context": github["context"], "status": github["status"], "pr_list": github["pr_list"],
              "open_status": github["open_status"], "pr_list_sha256": digest(pr_list),
              "open_status_sha256": digest(open_status)}
    return files, loaded


def github_cycle(audit, github, *, bind=ALL):
    """D19's committed core cycle with the four GitHub files in its cycle dir and the names in ``bind`` bound in its
    committed pointer, as a producer that captured them would commit them; and the same cycle held in memory."""
    events, snapshot, live = rj.core_cycle(audit)
    files, loaded = github_files(github)
    latest = json.loads((live / "latest.json").read_bytes())
    for name, raw in files.items():
        (live / "cycles" / CYCLE / name).write_bytes(raw)
        if name in bind:
            file_key, sha_key = POINTER[name]
            latest["last_ok"].update({file_key: f"cycles/{CYCLE}/{name}", sha_key: digest(raw)})
    (live / "latest.json").write_bytes(rj.encoded(latest))
    case = {"cycle": CYCLE, "snapshot": snapshot, "github": loaded if set(bind) == set(ALL) else None,
            "bodies": {event_id(row): row for row in events}, "as_of_utc": tlv.AS_OF}
    return live, files, case


def manifest_files(frozen):
    return json.loads((frozen / "manifest.json").read_bytes())["cycles"][0]["files"]


def turn(lane, case):
    return jt.turn_case(lane, case, learn_ref=LEARN_REF, envelope=None, receipts=None)


def test_bound_github_files_are_frozen_byte_exact_and_listed_for_the_loader(tmp_path):
    audit = tmp_path / ".codex-audit"
    live, files, case = github_cycle(audit, tlv.github_of())
    frozen = audit / "frozen"
    binding = DRIVER.freeze_committed_cycle(live, frozen)
    listed = manifest_files(frozen)
    for name, raw in files.items():
        assert (frozen / CYCLE / name).read_bytes() == raw  # byte-exact: CRLF and indentation kept
        assert listed[name] == digest(raw) == binding["observed_file_sha256"][name]
        assert binding["pointer_bound_raw_files"][name] == digest(raw)
    assert binding["join_validated"] is False and binding["coverage"] == "UNKNOWN"
    assert binding["github_complete"] is False  # the transport never certifies GitHub; the lane view decides use
    cycle, = fz.load_frozen(frozen)["cycles"]
    assert cycle["nulls"] == [] and cycle["github"] == case["github"] and cycle["as_of_utc"] == tlv.AS_OF


def test_a_usable_capture_reaches_the_real_join_and_names_the_one_owed_vote(tmp_path):
    audit = tmp_path / ".codex-audit"
    live, _, case = github_cycle(audit, tlv.github_of())
    result = DRIVER.run_committed_chain(live, audit / "frozen")
    assert result["join_calls_completed"] == len(LANES) == len(result["cards"])
    for lane, card in zip(LANES, result["cards"]):
        expected = turn(lane, case)
        assert expected["view"]["github"]["usable"] is True and expected["view"]["coverage"] == "UNKNOWN"
        assert card["lane"] == lane and card["text"] == expected["card"]
        assert card["sha256"] == digest(expected["card"].encode("utf-8")) and card["bytes"] <= 2048
        assert card["github_usable"] is True and card["coverage"] == "UNKNOWN"
        assert card["as_of_utc"] == expected["as_of_utc"] == tlv.AS_OF
    view = turn("codex-tools-1", case)["view"]
    owed = {lane: found for lane, found in tlv.lines_for(view).items() if found}
    assert list(owed) == ["codex-tools-1"] and len(owed["codex-tools-1"]) == 1
    assert f"build vote on PR{tlv.NUMBER} at {tlv.NEW[:12]} is the only empty slot" in owed["codex-tools-1"][0]
    blind = turn("codex-tools-1", dict(case, github=None))  # what the chain gave before this slice
    assert result["cards"][LANES.index("codex-tools-1")]["text"] != blind["card"]
    assert not any(tlv.lines_for(blind["view"]).values())


@pytest.mark.parametrize("github, bind, reason, refused", [
    (tlv._status_stale(tlv.github_of()), ALL, "github_capture_missing", "github_not_current"),
    (tlv.github_of(observed="2026-10-13T17:40:00Z"), ALL, "github_capture_missing", "context_stale"),
    (tlv._status_unlinked(tlv.github_of()), ALL, "github_capture_missing", "raw_lists_not_linked"),
    (tlv._pr_list_head_differs(tlv.github_of()), ALL, "head_ambiguous", None),
    (tlv._list_head_differs(tlv.github_of()), ALL, "head_ambiguous", None),
    (tlv.github_of(tlv.SECOND_HEAD, number=tlv.SECOND, branch=tlv.SECOND_BRANCH), ALL, "github_pr_missing", None),
    (tlv.github_of(), ALL[:3], "github_capture_missing", "github_absent"),
], ids=["status_stale", "context_stale", "status_unlinked", "list_head_disagrees", "status_head_disagrees",
        "pr_not_captured", "open_status_not_bound"])
def test_missing_stale_unlinked_or_mismatched_github_stays_unknown(tmp_path, github, bind, reason, refused):
    audit = tmp_path / ".codex-audit"
    live, files, case = github_cycle(audit, github, bind=bind)
    frozen = audit / "frozen"
    result = DRIVER.run_committed_chain(live, frozen)
    assert {name: manifest_files(frozen)[name] for name in ALL} == {
        name: digest(files[name]) if name in bind else None for name in ALL}
    cycle, = fz.load_frozen(frozen)["cycles"]
    assert cycle["github"] == case["github"] and cycle["as_of_utc"] == tlv.AS_OF
    for lane, card in zip(LANES, result["cards"]):
        expected = turn(lane, case)
        assert card["text"] == expected["card"] and card["github_usable"] is (refused is None)
        view = expected["view"]
        tlv.assert_abstains(view, reason)
        assert view["github"] == ({"usable": False, "reason": refused} if refused else dict(view["github"], usable=True))


@pytest.mark.parametrize("change, reason", [
    (lambda ok: ok.update(pr_list_sha256=None), "github_binding"),
    (lambda ok: ok.update(status_file=None), "github_binding"),
    (lambda ok: ok.update(open_status_file=f"cycles/{CYCLE}/run/gh-open-status.json"), "github_binding"),
    (lambda ok: ok.update(open_status_sha256=7), "github_binding"),
    (lambda ok: ok.update(pr_list_sha256=digest(b"another list")), "github_digest"),
    (lambda ok: ok.update(status_sha256=digest(b"another status")), "github_digest"),
], ids=["list_digest_missing", "status_file_missing", "open_status_misplaced", "open_status_digest_not_text",
        "list_bytes_differ", "status_bytes_differ"])
def test_a_half_bound_misplaced_or_changed_github_pointer_is_refused_before_output(tmp_path, change, reason):
    audit = tmp_path / ".codex-audit"
    live, _, _ = github_cycle(audit, tlv.github_of())
    latest = json.loads((live / "latest.json").read_bytes())
    change(latest["last_ok"])
    (live / "latest.json").write_bytes(rj.encoded(latest))
    frozen = audit / "frozen"
    with pytest.raises(DRIVER.ChainRefusal) as refused:
        DRIVER.freeze_committed_cycle(live, frozen)
    assert refused.value.primary_reason == reason and not refused.value.cleanup_failed
    assert not frozen.exists() and not list(audit.glob(".frozen.staging-*"))


def test_a_usable_view_without_every_github_file_frozen_is_still_a_join_invariant_refusal(tmp_path, monkeypatch):
    # Only a frozen copy holding all four pointer-bound files may give a usable GitHub view; JOIN never makes one up.
    audit = tmp_path / ".codex-audit"
    live, _, _ = github_cycle(audit, tlv.github_of(), bind=ALL[:3])
    frozen = audit / "frozen"
    binding = DRIVER.freeze_committed_cycle(live, frozen)
    joiner = DRIVER._join_module()
    real = joiner.turn_case

    def claims_usable(*args, **kwargs):
        made = real(*args, **kwargs)
        made["view"]["github"] = {"usable": True}
        return made

    monkeypatch.setattr(joiner, "turn_case", claims_usable)
    with pytest.raises(DRIVER.ChainRefusal, match="^join_invariant$"):
        DRIVER.join_frozen_cycle(frozen, expected_binding_sha256=digest(rj.encoded(binding)))

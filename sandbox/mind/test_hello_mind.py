"""Tests of Hello Mind v0.1 (Lead round 9/10, decided by fable-5 as diamond round D3): one public synthetic fixture
through the real frozen loader, JOIN turn, turn writer and outcome adapter. Every expected text below is written from
the module sources it names (lane_view.action_line, join_card.see_line and hear_line), never taken from a report.

run: C:/Python/project2-master/.venv/Scripts/python.exe -B -m pytest sandbox/mind/test_hello_mind.py -q
"""

import hashlib
import json
import pathlib
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
for _path in (str(HERE), str(HERE / "step1")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
import hello_mind as hm  # noqa: E402
import join_outcome_adapter as jo  # noqa: E402
import join_turn as jt  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

LANE = "codex-tools-1"
AS_OF = "2026-10-18T12:00:00Z"
BRANCH = "hello-mind/synthetic-pr-1"
HEAD_B = hashlib.sha1(b"hello mind head B").hexdigest()
PUBLICATION = "run\\reflected.publication.json"
SEAMS = ["pins", "fixture", "load", "turn", "write_turn", "judge"]
FIXTURE_FILES = {"capture.json", "context.json", "observed-status.json", "gh-pr-list.json", "gh-open-status.json",
                 PUBLICATION}
PINS = {"join_turn": "join-turn-v0.3", "join_card": "join-card-v0.6",
        "outcome_adapter": "wd.mind-join-outcome-adapter.v0.1", "adapter_turn": "join-turn-v0.3",
        "frozen_manifest": "wd.mind-frozen-cycles.v1", "turn_schema": "wd.mind-join-turn.v0"}
UNKNOWN_RECEIPTS = "requested=UNKNOWN delivered=UNKNOWN read=UNKNOWN answered=UNKNOWN"
GATE = re.compile(r"(?i)consensus|changes_requested|rco_pass|auto_merge|rule12|autosign|receipt|merg|event_taxonomy")
FORBIDDEN_MODULES = ("faiss", "numpy", "torch", "sentence_transformers", "openai", "anthropic", "requests", "httpx",
                     "urllib3", "socket", "http.client", "urllib.request", "subprocess")
CLOSURE = """
import sys
from pathlib import Path
repo = Path(sys.argv[1]).resolve()
sys.path[:0] = [str(repo), str(repo / "sandbox" / "mind"), str(repo / "sandbox" / "mind" / "step1")]
import hello_mind
for module in list(sys.modules.values()):
    path = Path(getattr(module, "__file__", None) or "/").resolve()
    if path.is_relative_to(repo):
        print("FILE", path.relative_to(repo).as_posix())
for name in sorted(sys.modules):
    print("MODULE", name)
"""


@pytest.fixture
def audit(tmp_path):
    root = tmp_path / ".codex-audit"
    root.mkdir()
    return root


def refs():
    """The content refs of the fixture's two credited votes, recomputed from the fixture's own event rows."""
    rows = {(row["agent"], row["type"]): row for row in hm.fixture_events()}
    return event_id(rows[("codex-lead-1", "decision")]), event_id(rows[("claude-rco-1", "decision")])


def tree(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def report_of(out):
    return json.loads((out / "report.json").read_text(encoding="utf-8"))


def seams(report):
    return [(seam["name"], seam["status"]) for seam in report["seams"]]


class ReadSpy:
    def __init__(self, monkeypatch):
        self.paths = []
        real = pathlib.Path.read_bytes

        def spy(path):
            self.paths.append(Path(path).resolve())
            return real(path)

        monkeypatch.setattr(pathlib.Path, "read_bytes", spy)

    def under(self, root):
        root = Path(root).resolve()
        return [path for path in self.paths if path.is_relative_to(root)]


# --- pins: the producer and consumer pair this module was written for ------------------------------------------------

def test_the_pins_are_the_pair_hello_mind_was_written_for():
    assert hm.PINS == PINS
    assert hm.live_pins() == PINS


@pytest.mark.parametrize("module, name, value", [(jo, "TURN_VERSION", "join-turn-v0.4"),
                                                  (jt, "VERSION", "join-turn-v0.4"),
                                                  (jt, "TURN_SCHEMA", "wd.mind-join-turn.v1")])
def test_an_unknown_component_pair_stops_before_any_output(audit, monkeypatch, module, name, value):
    monkeypatch.setattr(module, name, value)
    with pytest.raises(hm.HelloMindError) as caught:
        hm.run(audit / "hello")
    assert str(caught.value) == hm.PAIR
    assert not (audit / "hello").exists()


def test_the_chain_checks_the_pins_again_before_its_first_write(audit, monkeypatch):
    out = audit / "hello"
    out.mkdir()
    frozen = hm.write_fixture(out / "frozen")
    monkeypatch.setattr(jo, "TURN_VERSION", "join-turn-v0.4")
    with pytest.raises(hm.HelloMindError) as caught:
        hm.chain(out, frozen)
    assert str(caught.value) == hm.PAIR and sorted(path.name for path in out.iterdir()) == ["frozen"]


# --- the real chain, positive control ---------------------------------------------------------------------------------

def test_the_real_chain_shows_one_source_referenced_action_line(audit):
    out = audit / "hello"
    report = hm.run(out)
    lead, rco = refs()
    head12 = HEAD_B[:12]
    card = (out / "turn" / "card.txt").read_text(encoding="utf-8")
    lines = card.split("\n")
    assert lines[2] == (f"SEE pr1@{head12} empty=build_tools credited=build_lead:{lead[:12]},claude-rco-1:{rco[:12]} "
                        "ci=2/2; abstained 0 (UNKNOWN reasons in the view), coverage complete")
    view = json.loads((out / "turn" / "view.json").read_text(encoding="utf-8"))
    assert view["lanes"][LANE]["actions"] == [
        f"build vote on PR1 at {head12} is the only empty slot (build_tools, task {BRANCH}); filled at this head: "
        f"build_lead {lead[:16]}, claude-rco-1 {rco[:16]}; GitHub 2/2 at {head12}; waiting since "
        "2026-10-18T11:00:01Z; route operator manual merge; the vote is still required"]
    assert card.startswith(f"JOIN {LANE} as_of={AS_OF} now={AS_OF} join-card-v0.6; advisory, not a gate")
    assert report == report_of(out)
    assert seams(report) == [(name, "ok") for name in SEAMS]
    assert report["outcome"] == "ACCEPTED_CARD_UTILITY_UNKNOWN" and report["utility"] == "UNKNOWN"
    assert report["authority"] == "none" and report["frozen_as_of"] == AS_OF and report["lane"] == LANE
    assert report["card_utf8_bytes"] == len((out / "turn" / "card.txt").read_bytes()) <= 2048
    outcome = json.loads((out / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["card"] == "accepted" and outcome["exposure"] == "SHOWN_LINES_KNOWN" and outcome["shown_k"] == 1
    assert [row["pr"] for row in outcome["rows"]] == [1]
    assert "SUCCESSFUL_ACTION" not in (out / "report.json").read_text(encoding="utf-8")


def test_without_receipts_hear_is_unknown_no_source_never_none(audit):
    out = audit / "hello"
    hm.run(out)
    delivery = json.loads((out / "turn" / "delivery.json").read_text(encoding="utf-8"))
    [row] = [row for row in delivery["ledger"] if row["lane"] == LANE]
    assert row["receipts"] == {kind: {"state": "UNKNOWN", "reason": "no_source"}
                               for kind in ("requested", "delivered", "read", "answered")}
    hear = (out / "turn" / "card.txt").read_text(encoding="utf-8").split("\n")[1]
    assert f"first pr=1 slot=build_tools wait_min=59 since=2026-10-18T11:00:01Z {UNKNOWN_RECEIPTS} id=" in hear
    assert "NONE" not in hear


def test_report_digests_are_the_bytes_on_disk(audit):
    out = audit / "hello"
    report = hm.run(out)
    frozen = out / "frozen"
    assert report["fixture"]["manifest_sha256"] == hashlib.sha256((frozen / "manifest.json").read_bytes()).hexdigest()
    assert set(report["fixture"]["files"]) == FIXTURE_FILES
    for name, digest in report["fixture"]["files"].items():
        assert digest == hashlib.sha256(frozen.joinpath("hello-1", *name.split("\\")).read_bytes()).hexdigest()
    for name in ("view.json", "delivery.json", "card.txt"):
        assert report["turn_outputs"][name] == hashlib.sha256((out / "turn" / name).read_bytes()).hexdigest()
    assert report["outcome_sha256"] == hashlib.sha256((out / "outcome.json").read_bytes()).hexdigest()
    assert report["code_sha256"] == {name: hashlib.sha256((jt.REPO / name).read_bytes()).hexdigest()
                                     for name in hm.HELLO_CODE}
    assert report["frozen_root"] == "frozen" and str(audit) not in (out / "report.json").read_text(encoding="utf-8")


def test_the_fixture_bytes_are_the_same_in_two_directories(audit):
    first, second = hm.write_fixture(audit / "a"), hm.write_fixture(audit / "b")
    assert tree(first) == tree(second)
    manifest = json.loads((first / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "wd.mind-frozen-cycles.v1" and PUBLICATION in manifest["cycles"][0]["files"]
    assert (first / "hello-1" / "run" / "reflected.publication.json").is_file()


# --- null capture versus null publication: two different cases --------------------------------------------------------

def test_a_pruned_capture_is_never_read_and_claims_no_complete_inventory(audit, monkeypatch):
    out = audit / "hello"
    spy = ReadSpy(monkeypatch)
    report = hm.run(out, capture=False)
    assert not (out / "frozen" / "hello-1" / "capture.json").exists()
    assert not [path for path in spy.paths if path.name == "capture.json"]
    load = next(seam for seam in report["seams"] if seam["name"] == "load")
    assert load["status"] == "ok" and load["nulls"] == ["capture.json"] and load["captures"] == 0
    assert report["event_inventory"] == "UNKNOWN_capture_pruned"
    card = (out / "turn" / "card.txt").read_text(encoding="utf-8")
    assert card.split("\n")[2].startswith("SEE no PR with your build_tools slot empty; abstained 1 ")
    assert report["utility"] == "UNKNOWN"


def test_a_full_capture_names_its_own_event_count_only(audit):
    report = hm.run(audit / "hello")
    assert report["event_inventory"] == f"fixture_events_{len(hm.fixture_events())}"


@pytest.mark.parametrize("change", ["null", "missing"])
def test_a_null_or_missing_publication_refuses_before_any_cycle_file_is_read(audit, monkeypatch, change):
    out = audit / "hello"
    out.mkdir()
    frozen = hm.write_fixture(out / "frozen")
    manifest = json.loads((frozen / "manifest.json").read_text(encoding="utf-8"))
    files = manifest["cycles"][0]["files"]
    if change == "null":
        files[PUBLICATION] = None
    else:
        del files[PUBLICATION]
    (frozen / "manifest.json").write_bytes(core.canonical_bytes(manifest))
    spy = ReadSpy(monkeypatch)
    report = hm.chain(out, frozen)
    assert spy.under(frozen / "hello-1") == []
    assert seams(report) == [("pins", "ok"), ("fixture", "ok"), ("load", "refused"), ("turn", "not_run"),
                             ("write_turn", "not_run"), ("judge", "not_run")]
    assert report["seams"][2]["reason"] == "a manifest cycle has no publication digest"
    assert report["outcome"] == "REFUSED" and not (out / "turn").exists() and not (out / "outcome.json").exists()


def test_one_wrong_fixture_digest_refuses_the_load_seam_and_no_pass_is_invented(audit):
    out = audit / "hello"
    out.mkdir()
    frozen = hm.write_fixture(out / "frozen")
    target = frozen / "hello-1" / "context.json"
    data = bytearray(target.read_bytes())
    data[-2] ^= 1
    target.write_bytes(bytes(data))
    report = hm.chain(out, frozen)
    assert seams(report)[2:] == [("load", "refused"), ("turn", "not_run"), ("write_turn", "not_run"),
                                 ("judge", "not_run")]
    assert report["seams"][2]["reason"] == "sha256 mismatch: hello-1/context.json"
    assert report["outcome"] == "REFUSED" and report["utility"] == "UNKNOWN"
    assert not (out / "turn").exists() and target.read_bytes() == bytes(data)  # the fixture evidence stays


# --- the real re-render, the output directory -------------------------------------------------------------------------

def test_a_changed_and_rehashed_card_is_still_refused_by_the_real_rerender(audit):
    out = audit / "hello"
    hm.run(out)
    turn = out / "turn"
    before = tree(out)
    card = (turn / "card.txt").read_bytes().replace(b"empty=build_tools", b"empty=build_lead", 1)
    (turn / "card.txt").write_bytes(card)
    manifest = json.loads((turn / "turn.json").read_text(encoding="utf-8"))
    manifest["outputs"]["card.txt"] = hashlib.sha256(card).hexdigest()
    (turn / "turn.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    result = hm.judge_turn(turn)
    assert result["card"] == "refused: card_not_rendered_from_turn" and result["exposure"] == "UNKNOWN"
    after = tree(out)
    assert set(after) == set(before)  # nothing removed, nothing added
    assert {name for name in before if before[name] != after[name]} == {"turn/card.txt", "turn/turn.json"}


def test_the_same_output_directory_twice_keeps_the_first_bytes(audit):
    out = audit / "hello"
    hm.run(out)
    before = tree(out)
    with pytest.raises(hm.HelloMindError) as caught:
        hm.run(out)
    assert str(caught.value) == hm.EXISTS
    assert tree(out) == before


def test_a_frozen_fixture_outside_the_output_directory_is_refused_before_any_write(audit):
    out, other = audit / "hello", audit / "other"
    out.mkdir()
    frozen = hm.write_fixture(other / "frozen")
    with pytest.raises(hm.HelloMindError) as caught:
        hm.chain(out, frozen)
    assert str(caught.value) == hm.NOT_INSIDE and list(out.iterdir()) == []


def test_a_guarded_word_in_the_report_refuses_it_before_it_is_written(audit, monkeypatch):
    monkeypatch.setattr(hm, "SCOPE", "P" + "RIVATE" + "_MARKER")  # composed; the decoded guard reveals it
    out = audit / "hello"
    with pytest.raises(hm.ws.WorkspaceError):
        hm.run(out)
    assert not (out / "report.json").exists()


def test_an_output_whose_codex_audit_root_does_not_exist_is_refused_before_any_write(tmp_path):
    # the nearest .codex-audit ancestor is the output root (join_card_delivery.output_refusal); a missing one refuses
    # wherever pytest's own basetemp lies, also inside another .codex-audit directory
    with pytest.raises(hm.HelloMindError) as caught:
        hm.run(tmp_path / "plain" / ".codex-audit" / "hello")
    assert str(caught.value) == "the output root is not an existing .codex-audit directory"
    assert not (tmp_path / "plain").exists()


# --- import closure, CLI ----------------------------------------------------------------------------------------------

def test_the_import_closure_is_hello_code_with_no_gate_provider_or_model_module():
    run = subprocess.run([sys.executable, "-B", "-I", "-c", CLOSURE, str(jt.REPO)], capture_output=True, text=True,
                         check=True)
    files = {line[5:] for line in run.stdout.splitlines() if line.startswith("FILE ")}
    modules = {line[7:] for line in run.stdout.splitlines() if line.startswith("MODULE ")}
    assert files == set(hm.HELLO_CODE)
    assert not [name for name in hm.HELLO_CODE if GATE.search(name)]
    assert not [name for name in FORBIDDEN_MODULES if name in modules]


def test_the_cli_prints_the_card_then_refuses_the_same_directory(audit, capsys):
    out = audit / "hello"
    assert hm.main(["--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed == (out / "turn" / "card.txt").read_text(encoding="utf-8")
    assert hm.main(["--out", str(out)]) == 2
    assert capsys.readouterr().out == f"refused: {hm.EXISTS}\n"
    with pytest.raises(SystemExit) as caught:
        hm.main([])
    assert caught.value.code == 2


def test_the_cli_reports_a_refused_seam_with_exit_2(audit, capsys, monkeypatch):
    monkeypatch.setattr(jo, "judge", lambda *args, **kwargs: {"card": "refused: input_unreadable"})
    out = audit / "hello"
    assert hm.main(["--out", str(out)]) == 2
    assert capsys.readouterr().out == "refused: judge: input_unreadable\n"
    assert report_of(out)["outcome"] == "REFUSED" and seams(report_of(out))[-1] == ("judge", "refused")

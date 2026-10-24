"""Pure-text regression for the RCO requester-closeout note in .agent-bridge/BRIDGE_PROTOCOL.md (Lead S3 B7).

Since #1924 the writer's requester closeout (-CloseRequestEventJson) is a message. Rule 6 of the protocol still
lists decision among the closeout forms readers accept, which is accurate history. But the merge gates read a
recognized RCO's decision as a review verdict by its status, so a closeout status there is that RCO's block.
These tests read the document as text only: no production import, helper, PowerShell or subprocess.
"""
from __future__ import annotations

import re
from pathlib import Path

PROTOCOL = Path(__file__).resolve().parents[2] / ".agent-bridge" / "BRIDGE_PROTOCOL.md"
NOTE = "RCO requester closeout (normative):"
VERSIONS = "Requester closeout writer versions:"
NEXT = "`done/request` is still request-like work."


def _rule6() -> str:
    text = PROTOCOL.read_text(encoding="utf-8")
    start = text.index("6. Opinions require replies.")
    return " ".join(text[start:text.index("7. Alternate review loops.", start)].split())


def _part(marker: str, end: str) -> str:
    rule = _rule6()
    assert rule.count(marker) == 1, f"rule 6 has no single {marker!r} bullet"
    start = rule.index(marker)
    return rule[start:rule.index(end, start)]


def test_the_generic_requester_closeout_vocabulary_is_kept() -> None:
    rule = _rule6()
    for sentence in (
        "If the original requester later proves the request is obsolete, it may close the request with the same "
        "`task_id`. For `done`, `release`, and `decision`, closeout statuses are `done`, `closed`,",
        "For `message`, only `closed`, `superseded`, `cancelled`/`canceled`, and their underscore-suffixed forms "
        "close a request.",
        "Status tools report requester closeout as `closed`, not as an answer from the target agent.",
    ):
        assert sentence in rule, sentence
    assert rule.index("Status tools report requester closeout") < rule.index(NOTE) < rule.index(VERSIONS) \
        < rule.index(NEXT)


def test_an_rco_closes_its_own_request_with_a_message_never_a_decision() -> None:
    note = _part(NOTE, VERSIONS)
    for phrase in (
        "a recognized RCO that closes its own obsolete request writes the closeout as `message` with "
        "`superseded` or `cancelled`, never as `decision`.",
        "The `decision` forms above stay valid history for readers,",
        "the merge gates read a recognized RCO's `decision` as a review verdict by its status, not as closeout "
        "metadata:",
        "is that RCO's block, and a veto after its own exact-head `rco_pass`;",
        "an approval status such as `approved` would read in the peer gate as its approval.",
    ):
        assert phrase in note, phrase


def test_a_closeout_never_lifts_a_veto_or_creates_authority() -> None:
    note = _part(NOTE, VERSIONS)
    for phrase in (
        "A closeout is request metadata only: it neither lifts the original `finding` or veto, nor creates, "
        "replaces or renews any `rco_pass` or build vote.",
        "Only the same RCO's own clearance keeps its existing meaning",
    ):
        assert phrase in note, phrase


def test_the_note_separates_the_main_writer_from_the_installed_bundle() -> None:
    versions = _part(VERSIONS, NEXT)
    for phrase in (
        "on current main, `Write-AgentEvent.ps1 -CloseRequestEventJson` accepts only `-Type message` with "
        "`-Status superseded` or `cancelled`",
        "has no `-CloseRequestEventJson` parameter at all;",
        "lanes get the route only through a separately approved normal bundle install.",
        "Do not hand-write a closure event to work around a missing or older parameter.",
    ):
        assert phrase in versions, phrase


def test_the_note_carries_no_key_nonce_or_live_example() -> None:
    region = _part(NOTE, NEXT)
    assert not re.search(r"\b[0-9a-fA-F]{16,}\b", region), region
    for token in ("-Token", "-Nonce", "-RequestEventJson '", "nonce=", "token="):
        assert token not in region, token

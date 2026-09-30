"""S2 bundle lists (fable-5 2026-09-30): the legacy claim/done writers take the v2 runtime-root mutex through
BridgeV2QueueMutex.ps1, which ClaimLeaseHeartbeat.ps1 loads before any claim mutation and which itself dot-sources
BridgeNamedMutex.ps1. The installed 7779 bundle has no BridgeV2QueueMutex.ps1, and none of the three required-file
lists named it, so a bundle without it passed deploy and Tools boot and then refused every claim. Every list that
requires ClaimLeaseHeartbeat.ps1 must require the whole helper chain, so a missing helper is refused at deploy
(Deploy-WdRebootBundle.ps1), in the fleet manifest (wd-fleet.json) and at Tools boot (start-wd-tools-consumer.ps1).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
BIN = ROOT / ".agent-bridge" / "bin"
BIN_PREFIX = "tools-bootstrap/.agent-bridge/bin/"
CHAIN = ("ClaimLeaseHeartbeat.ps1", "BridgeV2QueueMutex.ps1", "BridgeNamedMutex.ps1")
DOT_SOURCED = re.compile(r"\.\s*\(Join-Path \$PSScriptRoot '([A-Za-z0-9_.-]+\.ps1)'\)")


def _block(text: str, opener: str) -> list[str]:
    start = text.index(opener)
    return re.findall(r"'([^']+)'", text[start:text.index(")) {", start)])


def _fleet() -> list[str]:
    manifest = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    return [name.removeprefix(BIN_PREFIX) for name in manifest["deployment"]["required_bundle_files"]
            if name.startswith(BIN_PREFIX)]


def _deployer() -> list[str]:
    text = (REBOOT / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    return [name.removeprefix(BIN_PREFIX) for name in _block(text, "foreach ($required in @(")
            if name.startswith(BIN_PREFIX)]


def _tools_consumer() -> list[str]:
    text = (REBOOT / "start-wd-tools-consumer.ps1").read_text(encoding="utf-8")
    return _block(text, "foreach ($requiredLeaf in @(")


LISTS = {"wd-fleet.json": _fleet, "Deploy-WdRebootBundle.ps1": _deployer,
         "start-wd-tools-consumer.ps1": _tools_consumer}


@pytest.mark.parametrize("list_name", sorted(LISTS))
def test_every_required_list_names_the_whole_claim_helper_chain(list_name: str) -> None:
    listed = LISTS[list_name]()
    assert "ClaimLeaseHeartbeat.ps1" in listed          # the premise: this list guards the claim helpers
    missing = [helper for helper in CHAIN if helper not in listed]
    assert missing == [], f"{list_name} does not require {missing}"


@pytest.mark.parametrize("list_name", sorted(LISTS))
def test_every_helper_the_chain_dot_sources_is_required_where_the_chain_is(list_name: str) -> None:
    # Derived from the sources, so a helper a later change loads (lazily or not) cannot be left off a list.
    listed = LISTS[list_name]()
    for script in CHAIN:
        source = (BIN / script).read_text(encoding="utf-8-sig")
        for helper in DOT_SOURCED.findall(source):
            assert (BIN / helper).is_file(), f"{script} dot-sources a missing {helper}"
            assert helper in listed, f"{script} loads {helper}, which {list_name} does not require"


def test_the_listed_chain_exists_and_names_one_file_each() -> None:
    lowered = {helper.lower() for helper in CHAIN}
    for helper in CHAIN:
        assert (BIN / helper).is_file(), helper
    for list_name, read in LISTS.items():
        listed = read()
        variants = [name for name in listed if name.lower() in lowered and name not in CHAIN]
        assert variants == [], f"{list_name} spells a chain helper in another case: {variants}"
        for helper in CHAIN:
            assert listed.count(helper) <= 1, f"{list_name} lists {helper} twice"

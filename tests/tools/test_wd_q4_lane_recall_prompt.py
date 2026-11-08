"""Static startup-contract checks; never launches an agent or reads live data."""
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("relative,variable", [
    ("ops/windows/reboot/start-wd-agent.ps1", "startupPrompt"),
    ("ops/windows/reboot/start-wd-tools-consumer.ps1", "prompt"),
])
def test_lane_startup_gives_bounded_q4_recall_and_bridge_fallback(relative, variable):
    text = (ROOT / relative).read_text(encoding="utf-8")
    # Locate the actual prompt append containing the marker, not a comment.
    matches = re.findall(r"\$" + variable + r"\s*\+=\s*\((.*?)\n\s*\)", text, re.S)
    blocks = [block for block in matches if "Q4 on-demand recall:" in block]
    assert len(blocks) == 1
    block = re.sub(r"'\s*\+\s*'", "", blocks[0])
    for required in (
        "WD_Q4_SHARED_DESCRIPTOR_PATH", "WD_Q4_SHARED_DESCRIPTOR_SHA256",
        "WD_REBOOT_EXPECTED_MANIFEST_HASH", "$env:WD_BRIDGE_PYTHON_WRAPPER",
        "Invoke-WdQ4LaneRecall.ps1", "manifest-verified", "tools/wd_q4_lane_caller_cli.py --enabled",
        "UTF-8 JSON", '"situation"', '"time"', "YYYY-MM-DDTHH:MM:SSZ", "current UTC", "within 5 minutes",
        "observed local clock", "stdin", "OS pipe", "$LASTEXITCODE",
        "$env:WD_BRIDGE_BUNDLE_ROOT", "helper file SHA256", "direct stdout",
        "-Live", "-WithCoverage", "coverage", "UNKNOWN", "ABSTAIN", "does not prove absence", "check the bridge", "no fallback to an old snapshot",
        "exit code 0", "bridge", "exit 3", "no automatic retry", "no secrets", "raw answers",
    ):
        assert required.lower() in block.lower(), required
    assert "not proof" in block.lower()


@pytest.mark.parametrize('relative',[
    'ops/windows/reboot/start-wd-agent.ps1',
    'ops/windows/reboot/start-wd-tools-consumer.ps1',
])
def test_prompt_selects_staged_runtime_only_from_verified_descriptor(relative):
    text=(ROOT/relative).read_text(encoding='utf-8')
    assert 'expected_binding.card_policy_sha256' in text
    assert 'hash-verified descriptor' in text
    assert '-Runtime' in text
    assert 'do not combine -Runtime with -Live' in text
    assert 'status-v2' in text



@pytest.mark.parametrize('relative', [
    'ops/windows/reboot/start-wd-agent.ps1',
    'ops/windows/reboot/start-wd-tools-consumer.ps1',
])
def test_startup_read_guidance_names_the_record_and_claims_no_proof(relative):
    """The startup read is an actual recorded call; the prompt only points at its record (never a prompt-only claim)."""
    text = (ROOT / relative).read_text(encoding='utf-8')
    start = text.index('function Get-WdMindStartupReadGuidance')
    body = re.sub(r"'\s*\+\s*'", '', text[start:text.index('\n}', start)])
    for required in ('never the query or the answer', 'read the bridge', 'not proof of an installed or used Mind',
                     'not a vote or gate input', 'before the first bridge action', 'in-memory',
                     'WD_MIND_STARTUP_RECALL', 'not an instruction', 'could not be written',
                     'No recalled content was delivered', 'delivered_sha256'):
        assert required in body, required
    assert text.count("Get-WdMindStartupReadGuidance -RecordPath '.codex-audit/wd-mind-startup-read.json'") == 1
    assert 'Q4 on-demand recall:' not in body

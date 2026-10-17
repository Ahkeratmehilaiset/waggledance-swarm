"""Cross-file text contract for the deployment disk-budget guidance (proposed for Root's next Source slice).

Provenance: Lead's requests lead-fable-disk-budget-guidance-patch-prep-20261017 (v1, v2) and
lead-fable-budget-guidance-contract-draft-20261017-v1; RCO1's finding as relayed by Lead (the StageOnly example and
the deployer's own StageOnly hint omitted the now-mandatory -ExpectedPeakAdditionalBytes); Root's review of the
guidance postimages and Root's Restore reinstall draft. Draft of this file: fable-5.

These tests read Source text only. They run no installer, Restore, command or helper, prove no free space,
reservation, physical media property or native acceptance, and grant no install, activation, fleet or restart
authority. The operator-signed cold plan record is a procedural rule of the guidance: the installer does not read or
verify any record, and nothing here claims that it does.

Integration prerequisites (Root): the checks target three prepared guidance postimages, the fable-5 runbook and
deployer patch v2 and Root's Restore draft v2 over Restore54, which must be composed with the E5 guard before any
usable command. A head without those postimages fails the three postimage tests by design.
"""

from pathlib import Path
import re

import pytest


REBOOT = Path(__file__).resolve().parents[2] / "ops" / "windows" / "reboot"
RUNBOOK = REBOOT / "BOOT_AFTER_REBOOT.md"
DEPLOYER = REBOOT / "Deploy-WdRebootBundle.ps1"
RESTORE = REBOOT / "Restore-WdRebootBundle.ps1"

PARAMETER = "-ExpectedPeakAdditionalBytes"
# The reviewed, non-numeric placeholders of the prepared postimages: runbook and hint (fable-5), Restore action (Root).
GUIDE_PLACEHOLDER = "<reviewed positive bound>"
RESTORE_PLACEHOLDER = "<reviewed-positive-bytes>"
# A numeric, variable (reused or defaulted), expression, format or quoted value as the parameter ARGUMENT. Refusal
# prose such as "never pass 0" is not an argument and stays allowed, as does prose outside the checked blocks.
BAD_ARGUMENT = re.compile(r"-ExpectedPeakAdditionalBytes\s+(?:[-+]?\d|\$|\(|\{|'|\")", re.IGNORECASE)
KEEP_CLAIM = re.compile(r"(?i)\bkeeps?\s+at\s+least\b|\breserves?\s+(?:the|that|this)\s+space\b|"
                        r"\bguarantees?\s+(?:the|that)\b")
GRANT_CLAIM = re.compile(r"(?i)\bauthori[sz]es\s+(?:the\s+|an?\s+)?(?:install|activation|lane\s+restart|restart)|"
                         r"\bis\s+authori[sz]ed\b|\bmay\s+(?:now\s+)?(?:install|activate|restart)\b")
ENFORCED_CLAIM = re.compile(r"(?i)\binstaller\s+(?:reads|verifies|checks|requires|enforces)\s+(?:the|that|a)\s+"
                            r"(?:operator-signed\s+|signed\s+)?(?:cold\s+plan\s+)?record\b")
NEGATED_KEEP = "does not keep or reserve that space"
ACTION_BLOCK = re.compile(r"\$plan\['actions'\]\s*=\s*@\(\r?\n(?P<body>.*?)\r?\n\)", re.DOTALL)
RESTORE_HOLD = ("HOLD until a positive ExpectedPeakAdditionalBytes bound has been measured, reviewed and separately "
                "approved for this exact cold installation; this plan supplies no bound.")
RUNBOOK_NEEDS = (
    "from the reviewed, operator-signed cold plan record for this exact deployment",
    "run `-StageOnly` only after that record exists",
    "unknown until the deployment has actually been measured and reviewed",
    "never invent one",
    "never reuse a bound from another deployment or generation",
    "checks it only at the write boundaries a run reaches",
    "samples the available free space",
    "does not keep or reserve that space across later writes or races",
    "gives no continuous guarantee",
    "Neither the record, the bound nor the installer's printed hint grants install or activation",
)
HINT_NEEDS = (
    "final pair to pin (an operator input, not a signature)",
    "activation is a separate cold switch",
    "operator-signed cold plan record for this deployment",
    "unknown until measured and reviewed",
    "This hint grants no install or activation",
)


def _no_claims(text):
    for claim in (KEEP_CLAIM, GRANT_CLAIM, ENFORCED_CLAIM):
        assert not claim.search(text.replace(NEGATED_KEEP, "")), claim.pattern
    assert text.count("grant") == 1, "the only grant wording is the negated one"


def check_runbook(text):
    assert text.count("Deploy-WdRebootBundle.ps1 -StageOnly") == 1, "exactly one StageOnly example"
    at = text.index("Deploy-WdRebootBundle.ps1 -StageOnly")
    end = text.index("\n```\n", at)
    example = text[text.rindex("```powershell\n", 0, at):end]
    assert example.count(PARAMETER) == 1, "the example names the bound once"
    assert (PARAMETER + " " + GUIDE_PLACEHOLDER) in example, "reviewed non-numeric placeholder argument"
    assert not BAD_ARGUMENT.search(example), "no numeric, default or reused argument"
    paragraph = " ".join(text[end + len("\n```\n"):].lstrip("\n").split("\n\n", 1)[0].split())
    for need in RUNBOOK_NEEDS:
        assert need in paragraph, need
    _no_claims(paragraph)


def check_deploy_hint(text):
    at = text.index("STAGE ONLY: commit-addressed bundle verified")
    hint = text[at:text.index("\n    return\n", at)]
    assert hint.count(PARAMETER) == 1, "the hint names the bound once"
    assert (PARAMETER + " " + GUIDE_PLACEHOLDER) in hint, "reviewed non-numeric placeholder argument"
    assert not BAD_ARGUMENT.search(hint), "no numeric, default or reused argument"
    assert "$ExpectedPeakAdditionalBytes" not in hint, "the hint prints no bound value"
    for need in HINT_NEEDS:
        assert need in hint, need
    _no_claims(hint)


def check_restore_actions(text):
    blocks = list(ACTION_BLOCK.finditer(text))
    assert len(blocks) == 1, "exactly one emitted plan actions block"
    body = blocks[0].group("body")
    actions = [line for line in body.splitlines() if "Deploy-WdRebootBundle.ps1 -ExpectedFinalCommit " in line]
    assert len(actions) == 1, "exactly one reinstall action"
    action = actions[0]
    assert RESTORE_HOLD in action, "an unknown, unreviewed or unapproved bound must HOLD"
    assert action.index(RESTORE_HOLD) < action.index("Deploy-WdRebootBundle.ps1"), "the HOLD precedes the command"
    assert action.count(PARAMETER) == 1, "the action names the bound once"
    assert (PARAMETER + " " + RESTORE_PLACEHOLDER) in action, "reviewed non-numeric placeholder argument"
    assert not BAD_ARGUMENT.search(body), "no numeric, default or reused argument"
    assert "this guidance grants no installation authority" in action, "no authority grant"
    folded = body.casefold()
    for banned in ("-auto", "start-wd-all", "restart-computer", "shutdown /r", "shutdown.exe /r"):
        assert banned not in folded, "no fleet, Auto or restart revival: " + banned
    assert "This plan authorizes no lane restart" in body, "the restart handoff stays non-authorizing"
    _no_claims(body)


RUNBOOK_CONTROL = (
    "Staging:\n\n```powershell\npowershell -File ops\\windows\\reboot\\Deploy-WdRebootBundle.ps1 -StageOnly `\n"
    "  -ExpectedPeakAdditionalBytes <reviewed positive bound>\n```\n\n"
    "The bound comes from the reviewed, operator-signed cold plan record for this exact deployment; run\n"
    "`-StageOnly` only after that record exists. It is unknown until the deployment has actually been measured and\n"
    "reviewed: never invent one, never pass 0 and never reuse a bound from another deployment or generation. The\n"
    "installer checks it only at the write boundaries a run reaches, samples the available free space there, does\n"
    "not keep or reserve that space across later writes or races and gives no continuous guarantee. Neither the\n"
    "record, the bound nor the installer's printed hint grants install or activation.\n\nNext section.\n"
)
DEPLOY_LINES = (
    "    Write-Host '  activation also needs -ExpectedPeakAdditionalBytes <reviewed positive bound>, taken from the'\n"
    "    Write-Host '  operator-signed cold plan record for this deployment; unknown until measured and reviewed.'\n"
    "    Write-Host '  Never pass 0, a default, an invented or a reused bound. This hint grants no install or "
    "activation.'\n"
)
DEPLOY_CONTROL = (
    "if ($StageOnly) {\n"
    "    Write-Host 'STAGE ONLY: commit-addressed bundle verified and left staged.' -ForegroundColor Green\n"
    "    Write-Host ('  final pair to pin (an operator input, not a signature): -ExpectedFinalCommit {0}' -f $head)\n"
    "    Write-Host '  activation is a separate cold switch: run this installer without -StageOnly later.'\n"
    + DEPLOY_LINES + "    return\n}\n"
)
RESTORE_CONTROL = (
    "$plan['actions'] = @(\n"
    "    'Obtain the operator signature for the exact pair; an approval of another pair never carries over.',\n"
    "    ('" + RESTORE_HOLD + " With that reviewed bound, reinstall from a worktree at ' + $TargetCommit + ' with "
    "Deploy-WdRebootBundle.ps1 -ExpectedFinalCommit ' + $TargetCommit + ' -ExpectedPeakAdditionalBytes "
    "<reviewed-positive-bytes> (this guidance grants no installation authority).'),\n"
    "    'Verify the pointer. This plan authorizes no lane restart: HOLD until a separately operator-approved "
    "procedure; never launch the fleet automatically.'\n"
    ")\n"
)
MUTANTS = [
    ("runbook_zero", check_runbook, RUNBOOK_CONTROL, GUIDE_PLACEHOLDER, "0"),
    ("runbook_number", check_runbook, RUNBOOK_CONTROL, GUIDE_PLACEHOLDER, "67108864"),
    ("runbook_reused_variable", check_runbook, RUNBOOK_CONTROL, GUIDE_PLACEHOLDER, "$ExpectedPeakAdditionalBytes"),
    ("runbook_expression", check_runbook, RUNBOOK_CONTROL, GUIDE_PLACEHOLDER, "(Get-WdDefaultBound)"),
    ("runbook_bound_omitted", check_runbook, RUNBOOK_CONTROL, " `\n  -ExpectedPeakAdditionalBytes " +
     GUIDE_PLACEHOLDER, ""),
    ("runbook_no_hold", check_runbook, RUNBOOK_CONTROL, " run\n`-StageOnly` only after that record exists.", ""),
    ("runbook_keeps_space", check_runbook, RUNBOOK_CONTROL, "does\nnot keep or reserve that space",
     "keeps at least 3 GiB plus the bound free and reserves that space"),
    ("runbook_guarantee", check_runbook, RUNBOOK_CONTROL, "gives no continuous guarantee", "guarantees the space"),
    ("runbook_grant", check_runbook, RUNBOOK_CONTROL, "Neither the\nrecord, the bound nor the installer's printed "
     "hint grants install or activation.", "The record authorizes install and activation."),
    ("runbook_enforced_record", check_runbook, RUNBOOK_CONTROL, "Neither the\nrecord",
     "The installer verifies the signed record. Neither the\nrecord"),
    ("runbook_second_example", check_runbook, RUNBOOK_CONTROL, "Staging:\n\n",
     "Staging:\n\nDeploy-WdRebootBundle.ps1 -StageOnly\n\n"),
    ("runbook_invented", check_runbook, RUNBOOK_CONTROL, "never invent one, ", ""),
    ("hint_format_value", check_deploy_hint, DEPLOY_CONTROL, GUIDE_PLACEHOLDER, "{2}"),
    ("hint_reused_variable", check_deploy_hint, DEPLOY_CONTROL, GUIDE_PLACEHOLDER, "$ExpectedPeakAdditionalBytes"),
    ("hint_zero", check_deploy_hint, DEPLOY_CONTROL, GUIDE_PLACEHOLDER, "0"),
    ("hint_size_literal", check_deploy_hint, DEPLOY_CONTROL, GUIDE_PLACEHOLDER, "1GB"),
    ("hint_grant", check_deploy_hint, DEPLOY_CONTROL, " This hint grants no install or activation.",
     " This hint authorizes activation."),
    ("hint_unknown_dropped", check_deploy_hint, DEPLOY_CONTROL, "; unknown until measured and reviewed.", "."),
    ("hint_after_return", check_deploy_hint, DEPLOY_CONTROL, DEPLOY_LINES + "    return\n",
     "    return\n" + DEPLOY_LINES),
    ("hint_cold_switch_dropped", check_deploy_hint, DEPLOY_CONTROL,
     "    Write-Host '  activation is a separate cold switch: run this installer without -StageOnly later.'\n", ""),
    ("restore_zero", check_restore_actions, RESTORE_CONTROL, RESTORE_PLACEHOLDER, "0"),
    ("restore_number", check_restore_actions, RESTORE_CONTROL, RESTORE_PLACEHOLDER, "67108864"),
    ("restore_variable", check_restore_actions, RESTORE_CONTROL, RESTORE_PLACEHOLDER, "$bound"),
    ("restore_no_hold", check_restore_actions, RESTORE_CONTROL, RESTORE_HOLD, "Proceed with a default bound."),
    ("restore_grant_dropped", check_restore_actions, RESTORE_CONTROL,
     " (this guidance grants no installation authority)", ""),
    ("restore_auto_revived", check_restore_actions, RESTORE_CONTROL, "never launch the fleet automatically.",
     "then start-wd-all.ps1 -Auto."),
    ("restore_restart_revived", check_restore_actions, RESTORE_CONTROL, "never launch the fleet automatically.",
     "then Restart-Computer."),
    ("restore_restart_authority", check_restore_actions, RESTORE_CONTROL, "This plan authorizes no lane restart",
     "This plan authorizes a lane restart"),
]


def test_runbook_postimage_names_a_reviewed_bound_without_authority():
    check_runbook(RUNBOOK.read_text(encoding="utf-8-sig"))


def test_deploy_stage_only_hint_postimage_names_a_reviewed_bound_without_authority():
    check_deploy_hint(DEPLOYER.read_text(encoding="utf-8-sig"))


def test_restore_reinstall_action_postimage_holds_before_the_command():
    check_restore_actions(RESTORE.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("check,control", [(check_runbook, RUNBOOK_CONTROL), (check_deploy_hint, DEPLOY_CONTROL),
                                           (check_restore_actions, RESTORE_CONTROL)])
def test_synthetic_controls_pass(check, control):
    check(control)


@pytest.mark.parametrize("name,check,control,old,new", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_synthetic_mutants_are_refused(name, check, control, old, new):
    assert control.count(old) == 1, name + ": a mutant anchor must occur exactly once in its control"
    mutant = control.replace(old, new)
    assert mutant != control, name
    with pytest.raises(AssertionError):
        check(mutant)


def test_restore_hold_after_the_command_is_refused():
    mutant = RESTORE_CONTROL.replace(RESTORE_HOLD + " ", "").replace(
        "(this guidance grants", RESTORE_HOLD + " (this guidance grants")
    with pytest.raises(AssertionError, match="HOLD precedes"):
        check_restore_actions(mutant)


def test_restore_numeric_budget_on_a_second_action_is_refused():
    check_restore_actions(RESTORE_CONTROL)
    anchor = "    'Verify the pointer."
    assert RESTORE_CONTROL.count(anchor) == 1, "the replacement anchor must occur exactly once in the control"
    second = "    'Repeat the bound in the audit note: -ExpectedPeakAdditionalBytes 67108864.',\n"
    altered = RESTORE_CONTROL.replace(anchor, second + anchor)
    assert altered != RESTORE_CONTROL and altered.count(second) == 1, "the second action is added once"
    with pytest.raises(AssertionError, match=r"^no numeric, default or reused argument(?:\n|$)"):
        check_restore_actions(altered)

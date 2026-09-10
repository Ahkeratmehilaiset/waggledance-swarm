# SPDX-License-Identifier: BUSL-1.1
"""Doc-vs-lock truth for ``docs/release/RELEASE_READINESS.md``.

The readiness summary is what operators read before a stable promotion, so
its statements about the dependency lock must be true of the lock that is
actually in the tree. On 2026-09-10 the lock was regenerated from the
declared dependencies (``safetensors==0.8.0`` final, no ``diffusers``), yet
the "Accepted lock exceptions" section -- prose written in May 2026 -- still
presented ``safetensors==0.8.0rc0`` (with ``diffusers==0.38.0`` as the cause)
as the lock's one active pre-release exception. This module pins the doc to
the lock so that cannot recur silently:

1. the ACTIVE exception list must equal the set of pre-release exact pins in
   ``requirements.lock.txt`` (both directions: no undocumented pre-release
   pin, no documented exception that the lock no longer carries);
2. superseded exceptions may stay only under an explicitly dated HISTORICAL
   heading, and the current lock facts the section states must be true;
3. the rule that new pre-release pins need a fresh documented exception is
   retained;
4. the May-2026 mainline snapshot (fixed HEAD ``6d2e59b``, measured claims)
   is labelled as a historical snapshot, not presented as current status.

Lock parsing keeps every exact ``(name, version)`` variant -- the lock
legitimately pins the same name more than once behind platform markers (the
torch pair, the operator cu12 floors and their exact resolutions) -- so a
pre-release behind one marker can never hide behind a later final pin of the
same name. A requirement line that does not parse fails the test instead of
being skipped: a truth guard must not fall open on malformed input.

Reads two files; no network, no resolver, no git.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "release" / "RELEASE_READINESS.md"
LOCK = ROOT / "requirements.lock.txt"

SECTION = "## Accepted lock exceptions"
ACTIVE_HEADING = "### Active exceptions"
FACTS_HEADING = "### Current lock facts"
HISTORICAL_HEADING = "### Historical exceptions"
PIN_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9._-]*)==([^`\s]+)`")
ABSENT_RE = re.compile(r"`([A-Za-z0-9][A-Za-z0-9._-]*)` is not in the lock")
HISTORICAL_LABEL_RE = re.compile(r"historical snapshot", re.IGNORECASE)
MAY_SNAPSHOT_HEAD = "6d2e59b"


def _lock_exact_pins() -> set[tuple[str, Version]]:
    """Every exact ``(canonical name, version)`` pin in the lock, all marker
    variants included. Malformed requirement lines are an error."""
    pins: set[tuple[str, Version]] = set()
    for number, raw in enumerate(LOCK.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue  # blank / comment / pip option line (e.g. --extra-index-url)
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:  # pragma: no cover - fail closed
            raise AssertionError(f"requirements.lock.txt line {number} does not parse: {exc}")
        for spec in requirement.specifier:
            if spec.operator != "==":
                continue
            try:
                pins.add((canonicalize_name(requirement.name), Version(spec.version)))
            except InvalidVersion as exc:  # pragma: no cover - fail closed
                raise AssertionError(f"requirements.lock.txt line {number}: bad version: {exc}")
    assert pins, "requirements.lock.txt yielded no exact pins"
    return pins


def _section(text: str, heading: str, *, level: str) -> str:
    """Return the body of ``heading`` up to the next heading of the same level."""
    assert heading in text, f"heading missing: {heading}"
    body = text[text.index(heading) + len(heading):]
    stop = re.search(rf"^{re.escape(level)} ", body, flags=re.MULTILINE)
    return body[: stop.start()] if stop else body


def _documented_pins(fragment: str) -> set[tuple[str, Version]]:
    return {
        (canonicalize_name(name), Version(version))
        for name, version in PIN_RE.findall(fragment)
    }


def _doc() -> str:
    return DOC.read_text(encoding="utf-8")


def test_active_lock_exceptions_equal_prerelease_pins_in_lock() -> None:
    """Contract 1: the active list and the lock's pre-release pins agree,
    over every marker variant."""
    section = _section(_doc(), SECTION, level="##")
    active = _section(section, ACTIVE_HEADING, level="###")
    assert HISTORICAL_HEADING in section, "historical exceptions subsection missing"

    documented = _documented_pins(active)
    lock = _lock_exact_pins()
    prerelease = {pin for pin in lock if pin[1].is_prerelease}

    undocumented = sorted(f"{n}=={v}" for n, v in prerelease - documented)
    assert not undocumented, f"pre-release pins in the lock without an exception: {undocumented}"
    spurious = sorted(f"{n}=={v}" for n, v in documented - prerelease)
    assert not spurious, f"spurious active exceptions not in the prerelease lock pins: {spurious}"
    if not prerelease:
        assert re.search(r"\bnone\b", active, flags=re.IGNORECASE), (
            "with no pre-release pins the active list must say so explicitly"
        )


def test_current_lock_facts_stated_in_the_section_are_true() -> None:
    """Contract 2: every active exception or current fact states a real pin,
    and every package claimed absent has no exact pin under any marker."""
    section = _section(_doc(), SECTION, level="##")
    active = _section(section, ACTIVE_HEADING, level="###")
    facts = _section(section, FACTS_HEADING, level="###")
    current = active + facts
    lock = _lock_exact_pins()
    names = {name for name, _ in lock}

    stated = _documented_pins(current)
    stale = sorted(f"{n}=={v}" for n, v in stated - lock)
    assert not stale, f"current subsections state pins the lock does not carry: {stale}"
    for name in ABSENT_RE.findall(current):
        assert canonicalize_name(name) not in names, f"{name} is claimed absent but is pinned"


@pytest.mark.parametrize("pin", ["safetensors==0.8.0", "example-lib==1.0rc1"])
def test_active_exceptions_reject_spurious_pin(monkeypatch: pytest.MonkeyPatch, pin: str) -> None:
    """Neither a real final pin nor an absent prerelease is an active exception."""
    text = _doc().replace(
        ACTIVE_HEADING,
        ACTIVE_HEADING + f"\n\n* Active exception: `{pin}`.\n",
        1,
    )
    monkeypatch.setitem(globals(), "_doc", lambda: text)
    with pytest.raises(AssertionError, match="spurious active exceptions"):
        test_active_lock_exceptions_equal_prerelease_pins_in_lock()


def test_superseded_exception_is_dated_history_not_active() -> None:
    """Contract 2b: the May-2026 safetensors/diffusers exception is carried
    only as a dated, superseded historical entry; the active subsection may
    mention the names as closed facts but must not state those pins."""
    section = _section(_doc(), SECTION, level="##")
    active = _section(section, ACTIVE_HEADING, level="###")
    historical = _section(section, HISTORICAL_HEADING, level="###")

    assert ("safetensors", Version("0.8.0rc0")) not in _documented_pins(active)
    assert not any(name == "diffusers" for name, _ in _documented_pins(active))
    assert "`safetensors==0.8.0rc0`" in historical and "`diffusers==0.38.0`" in historical
    assert "#581" in historical, "the PR #581 cause must be preserved"
    assert re.search(r"2026-05", historical), "the historical exception must be dated"
    assert re.search(r"superseded", historical, flags=re.IGNORECASE)


def test_rule_for_new_prerelease_pins_is_retained() -> None:
    """Contract 3."""
    section = _section(_doc(), SECTION, level="##")
    assert re.search(
        r"New pre-release pins require a fresh\s+documented exception", section
    ), "the fresh-documented-exception rule must stay"
    for point in ("cause", "why-not-stable", "why-not-downgrade", "vulnerability surface"):
        assert point in section, f"rule must keep the four required points ({point})"


def _enclosing_block(lines: list[str], index: int) -> str:
    """Text from the nearest preceding heading or bullet start through ``index``."""
    start = index
    while start > 0 and not (lines[start].startswith("#") or lines[start].startswith("* ")):
        start -= 1
    return "\n".join(lines[start: index + 1])


def test_may_2026_snapshot_is_labelled_historical() -> None:
    """Contract 4: the fixed May-2026 HEAD and measured numbers are snapshots."""
    text = _doc()
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if MAY_SNAPSHOT_HEAD not in line:
            continue
        block = _enclosing_block(lines, index)
        heading = next(
            (lines[j] for j in range(index, -1, -1) if lines[j].startswith("#")), ""
        )
        assert HISTORICAL_LABEL_RE.search(block) or HISTORICAL_LABEL_RE.search(heading), (
            f"line {index + 1} presents {MAY_SNAPSHOT_HEAD} without a historical-snapshot label"
        )
    measured = re.search(r"^## .*Measured Claims.*$", text, flags=re.MULTILINE)
    assert measured and HISTORICAL_LABEL_RE.search(measured.group(0)), (
        "the measured-claims heading must be labelled a historical snapshot"
    )
    substrate = re.search(r"^## v3\.13\.0 Substrate Landing.*$", text, flags=re.MULTILINE)
    assert substrate and HISTORICAL_LABEL_RE.search(substrate.group(0))

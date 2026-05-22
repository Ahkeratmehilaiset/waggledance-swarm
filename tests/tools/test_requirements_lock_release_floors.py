# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[2]
LOCKED_RELEASE_FLOORS = {
    "aiohttp": Version("3.13.4"),
    "pytest": Version("9.0.3"),
}
LOW_RISK_OSV_FIXED_FLOORS = {
    "cryptography": Version("46.0.7"),
    "diffusers": Version("0.38.0"),
    "gitpython": Version("3.1.50"),
    "idna": Version("3.15"),
    "lxml": Version("6.1.0"),
    "nltk": Version("3.9.4"),
    "pygments": Version("2.20.0"),
    "pypdf": Version("6.10.2"),
    "python-dotenv": Version("1.2.2"),
    "requests": Version("2.33.0"),
    "urllib3": Version("2.7.0"),
}
BIG_JUMP_OSV_FIXED_FLOORS = {
    "pillow": Version("12.1.1"),
    "pyarrow": Version("23.0.1"),
    "starlette": Version("1.0.1"),
    "streamlit": Version("1.54.0"),
}


def _requirements(path: Path) -> dict[str, Requirement]:
    requirements: dict[str, Requirement] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        requirements[canonicalize_name(requirement.name)] = requirement
    return requirements


def _lock_pins(path: Path) -> dict[str, Version]:
    pins: dict[str, Version] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        try:
            requirement = Requirement(line)
        except InvalidRequirement:
            continue
        exact_versions = [
            spec.version
            for spec in requirement.specifier
            if spec.operator == "=="
        ]
        if len(exact_versions) == 1:
            pins[canonicalize_name(requirement.name)] = Version(exact_versions[0])
    return pins


def test_release_lock_satisfies_security_floor_bumps() -> None:
    primary = _requirements(ROOT / "requirements.txt")
    ci = _requirements(ROOT / "requirements-ci.txt")
    lock = _lock_pins(ROOT / "requirements.lock.txt")

    for package, floor in LOCKED_RELEASE_FLOORS.items():
        name = canonicalize_name(package)
        assert name in primary
        assert name in ci
        assert primary[name].specifier.contains(floor, prereleases=True)
        assert ci[name].specifier.contains(floor, prereleases=True)
        assert lock[name] >= floor


def test_release_lock_uses_low_risk_osv_fixed_versions() -> None:
    lock = _lock_pins(ROOT / "requirements.lock.txt")

    for package, floor in LOW_RISK_OSV_FIXED_FLOORS.items():
        name = canonicalize_name(package)
        assert lock[name] >= floor


def test_release_lock_uses_big_jump_osv_fixed_versions() -> None:
    lock = _lock_pins(ROOT / "requirements.lock.txt")

    for package, floor in BIG_JUMP_OSV_FIXED_FLOORS.items():
        name = canonicalize_name(package)
        assert lock[name] >= floor

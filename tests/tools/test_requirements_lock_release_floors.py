# SPDX-License-Identifier: BUSL-1.1
"""Release-lock contract for ``requirements.lock.txt``.

History. Until 2026-09-10 the lock was a developer-environment ``pip freeze``
(319 pins, April 2026) that carried unrelated tooling (keras/tensorflow,
streamlit, playwright, gitpython, nltk, pypdf, ...) and lagged on the packages
the product actually ships. The operator-authorized ``lock_strategy``
(docs/operator_inbox/torch-cuda-vs-cpu.yaml, scope_updates 2026-08-26:
"regenerate the stable lock from declared release dependencies rather than
carrying unrelated developer-environment pip-freeze packages") is now applied:
the lock is resolved from ``pyproject.toml`` ``[project.dependencies]`` plus the
``[dev]`` extra (pytest stays in the lock for the CI/dev workflow), with the
torch build fixed by the existing marker pair and cu126 extra index.

Contract enforced here:

1. every declared core and ``[dev]`` direct dependency is pinned in the lock and
   the pin satisfies the declared specifier -- no missing runtime dependency;
2. security floors from earlier OSV remediations are RETAINED for every package
   that is still shipped, plus the 2026-09-10 fixed-version floors;
3. packages that were in the old freeze only through unrelated developer tooling
   are asserted ABSENT -- their historical floors/pins are kept in
   ``DESCOPED_DEV_FREEZE_PACKAGES`` so that re-introducing one is a deliberate
   change to this file, never silent drift (fail closed);
4. the torch marker pair and the cu126 extra index line are preserved exactly,
   the no-fix and unused blocklists are unchanged, every entry is an exact pin.

Cross-platform completeness (2026-09-10 correction). The unconditional pins
were resolved on Windows; ``pip --platform`` selects wheel tags but evaluates
environment markers on the HOST, so Linux-only conditional dependencies are not
visible to such a run. The lock therefore carries an explicit Linux-only
section, taken from a target-marker-aware Linux resolution of the lock itself:
on non-Windows the torch marker pair plus the operator-authorized nvidia-*-cu12
floors make pip select the plain PyPI ``torch==2.13.0`` build (the cu126 wheel
pins ``nvidia-cudnn-cu12==9.10.2.21``, below the authorized floor, so pip
backtracks -- a property of the previous lock too), and that build's recursive
CUDA-13/triton chain, the exact resolutions of the cu12 floors and ``uvloop``
from ``uvicorn[standard]`` are pinned with the declaring packages' own markers
(contract 5 below). Remaining limits: the Linux evidence is preview evidence
(vendored-marker override on a Windows host; native Linux CI is the
authoritative check) and macOS is not covered.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "requirements.lock.txt"
PYPROJECT_PATH = ROOT / "pyproject.toml"

# Floors that must hold in pyproject/requirements-ci AND the lock (unchanged).
LOCKED_RELEASE_FLOORS = {
    "aiohttp": Version("3.13.4"),
    "pytest": Version("9.0.3"),
}
# Earlier OSV remediation floors for packages the regenerated lock still ships.
LOW_RISK_OSV_FIXED_FLOORS = {
    "idna": Version("3.15"),
    "lxml": Version("6.1.0"),
    "pygments": Version("2.20.0"),
    "python-dotenv": Version("1.2.2"),
    "requests": Version("2.33.0"),
    "urllib3": Version("2.7.0"),
}
BIG_JUMP_OSV_FIXED_FLOORS = {
    "starlette": Version("1.0.1"),
    # GHSA-rrmf-rvhw-rf47: fixed in torch 2.13.0 (bumped from 2.11.0,
    # operator-authorized dependency remediation 2026-08-26).
    "torch": Version("2.13.0"),
}
# 2026-09-10 lock regeneration: of the 20 OSV-affected packages in the old
# freeze only these five are inside the declared closure; fixed versions were
# read from the OSV primary records (api.osv.dev/v1/vulns/<id>) and the
# regenerated lock resolves above every one of them.
OSV_2026_09_FIXED_FLOORS = {
    # GHSA-cq5v-8q36-5273 (3.14.3), GHSA-mfx4-hv73-q22v / GHSA-mq44-7p77-q5h7
    # (3.14.2) and their PYSEC-2026-3545/3546/3547 aliases.
    "aiohttp": Version("3.14.3"),
    # PYSEC-2026-2132.
    "click": Version("8.3.3"),
    # GHSA-h35f-9h28-mq5c / PYSEC-2026-3447 (sdist MANIFEST.in bypass).
    "setuptools": Version("83.0.0"),
    # GHSA-82w8-qh3p-5jfq / PYSEC-2026-249 (1.3.1) and the earlier 1.1.0 /
    # 1.3.0 fixes (GHSA-wqp7-x3pw-xc5r, GHSA-x746-7m8f-x49c, GHSA-jp82-jpqv-5vv3).
    "starlette": Version("1.3.1"),
    # GHSA-xrqw-3rrv-vx5w (5.10.0); GHSA-29pf-2h5f-8g72 / PYSEC-2026-2289 (5.3.0);
    # GHSA-fgcw-684q-jj6r = PYSEC-2026-2290 = CVE-2026-5241 (5.5.0).
    "transformers": Version("5.10.0"),
}
LOCK_CONSISTENCY_FLOORS = {
    "safetensors": Version("0.8.0rc0"),
}
# De-scoped from the lock on 2026-09-10: present in the old developer freeze
# only through unrelated tooling and NOT in the declared closure. The value is
# the historical floor (or exact pin) that applied while the package was
# carried, kept for context; re-adding any of these to the lock must come with
# a deliberate edit here (and its own OSV check).
DESCOPED_DEV_FREEZE_PACKAGES = {
    # former LOW_RISK_OSV_FIXED_FLOORS entries
    "cryptography": Version("46.0.7"),
    "diffusers": Version("0.38.0"),
    "gitpython": Version("3.1.50"),
    "nltk": Version("3.9.4"),
    "pypdf": Version("6.10.2"),
    # former BIG_JUMP_OSV_FIXED_FLOORS entries
    "pillow": Version("12.2.0"),
    "pyarrow": Version("23.0.1"),
    "streamlit": Version("1.54.0"),
    # former LOCK_CONSISTENCY_PINS exact pin (streamlit/cachetools cap)
    "cachetools": Version("6.2.6"),
    # other OSV-affected freeze leftovers that dropped out with regeneration
    # (no floor was ever recorded for them; None = historical version unknown
    # to this contract, absence is the only requirement)
    "accelerate": None,
    "datasets": None,
    "h2": None,
    "httplib2": None,
    "keras": None,
    "msgpack": None,
    "pyasn1": None,
    "pydantic-settings": None,
    "soupsieve": None,
    "tensorflow": None,
    "tornado": None,
}
UNUSED_INCOMPATIBLE_LOCK_BLOCKLIST = {
    "moviepy",
    "pipwin",
    "pyjsparser",
    "pyprind",
    "pysmartdl",
}
NO_FIX_OSV_BLOCKLIST = {
    # chromadb: 5 advisories with no fixed release as of 1.5.9 (PyPI latest
    # 2026-08-26); de-scoped from the stable default to the [chroma] extra.
    "chromadb",
    "deep-translator",
    "js2py",
    "paramiko",
}
# Torch contract (A2 cu126 refresh, operator decision pack): exactly this
# marker pair, this extra index, and none of the optional torch family.
TORCH_EXTRA_INDEX_LINE = "--extra-index-url https://download.pytorch.org/whl/cu126"
TORCH_MARKER_PAIR = {
    'sys_platform == "win32"': Version("2.13.0+cu126"),
    'sys_platform != "win32"': Version("2.13.0"),
}
TORCH_FAMILY_REQUIRED_ABSENT = {"torchvision", "torchaudio", "torchao", "xformers"}
# Operator-authorized CUDA dependency floors for the Linux torch path (operator
# decision pack scope_updates 2026-05-27: PR #694 nvidia-cublas-cu12, PR #696
# nvidia-cudnn-cu12; Phase 16F/R22.5 lock note). They are lower bounds with a
# platform marker, not exact pins, because the non-Windows torch line resolves
# its own nvidia-* runtime set at install time; the floors keep that set at or
# above the remediated versions. Retained verbatim through the 2026-09-10
# regeneration: removing or loosening one is a deliberate change here.
AUTHORIZED_CONDITIONAL_FLOORS = {
    "nvidia-cublas-cu12": (">=12.9.2.10", 'sys_platform != "win32"'),
    "nvidia-cuda-runtime-cu12": (">=12.6", 'sys_platform != "win32"'),
    "nvidia-cudnn-cu12": (">=9.23.2.1", 'sys_platform != "win32"'),
}
# Contract 5: Linux-only recursive dependencies, pinned with the declaring
# package's own marker. Membership is the target-marker-aware Linux resolution
# of this lock (plain PyPI torch 2.13.0 -> CUDA 13 runtime + triton; the cu12
# floors -> their exact resolutions; uvicorn[standard] -> uvloop). Any entry
# that carries a platform marker must be listed here, in the torch pair or in
# the operator floors: an unlisted conditional pin is a contract violation.
_LINUX = 'platform_system == "Linux"'
_LINUX_PY = 'platform_system == "Linux" and python_version < "3.15"'
_NOT_WIN = 'sys_platform != "win32"'
LINUX_ONLY_PINS = {
    # torch's own Linux-only declarations (PyPI 2.13.0 build)
    "cuda-toolkit": _LINUX,
    "nvidia-cudnn-cu13": _LINUX,
    "nvidia-cusparselt-cu13": _LINUX,
    "nvidia-nccl-cu13": _LINUX,
    "nvidia-nvshmem-cu13": _LINUX,
    "cuda-bindings": _LINUX_PY,
    "triton": _LINUX_PY,
    # pulled by cuda-bindings / cuda-toolkit[<components>]
    "cuda-pathfinder": _LINUX_PY,
    "nvidia-cublas": _LINUX,
    "nvidia-cuda-cupti": _LINUX,
    "nvidia-cuda-nvrtc": _LINUX,
    "nvidia-cuda-runtime": _LINUX,
    "nvidia-cufft": _LINUX,
    "nvidia-cufile": _LINUX,
    "nvidia-curand": _LINUX,
    "nvidia-cusolver": _LINUX,
    "nvidia-cusparse": _LINUX,
    "nvidia-nvjitlink": _LINUX,
    "nvidia-nvtx": _LINUX,
    # exact resolutions of the operator cu12 floors (same marker as the floors)
    "nvidia-cublas-cu12": _NOT_WIN,
    "nvidia-cuda-runtime-cu12": _NOT_WIN,
    "nvidia-cudnn-cu12": _NOT_WIN,
    "nvidia-cuda-nvrtc-cu12": _NOT_WIN,
    # uvicorn[standard] extra member, marker copied from uvicorn's metadata
    "uvloop": (
        'sys_platform != "win32" and sys_platform != "cygwin" '
        'and platform_python_implementation != "PyPy"'
    ),
}
# uvicorn[standard] members that are unconditional (or Windows-only) and so
# must simply be pinned; uvloop is covered by LINUX_ONLY_PINS.
UVICORN_STANDARD_UNCONDITIONAL = {
    "httptools", "python-dotenv", "pyyaml", "watchfiles", "websockets", "colorama",
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


def _lock_lines(path: Path) -> tuple[list[str], list[Requirement]]:
    """Return (option lines, requirement lines) of the lock, comments stripped."""
    options: list[str] = []
    requirements: list[Requirement] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-"):
            options.append(line)
            continue
        requirements.append(Requirement(line))
    return options, requirements


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


def _declared_direct_dependencies() -> dict[str, Requirement]:
    """Core ``[project.dependencies]`` plus the ``[dev]`` extra of pyproject."""
    project = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]
    declared = list(project["dependencies"]) + list(
        project["optional-dependencies"]["dev"]
    )
    return {
        canonicalize_name(requirement.name): requirement
        for requirement in map(Requirement, declared)
    }


def test_release_lock_pins_every_declared_direct_dependency() -> None:
    """Contract 1: no declared core/[dev] dependency may be missing from the
    lock, and each pin must satisfy the declared specifier (floors in
    pyproject.toml can never be silently undercut by the lock)."""
    lock = _lock_pins(LOCK_PATH)
    declared = _declared_direct_dependencies()

    assert declared, "pyproject.toml declares no dependencies?"
    missing = sorted(name for name in declared if name not in lock)
    assert not missing, f"declared dependencies missing from the lock: {missing}"
    for name, requirement in declared.items():
        assert requirement.specifier.contains(lock[name], prereleases=True), (
            f"{name}=={lock[name]} does not satisfy declared {requirement}"
        )


def test_release_lock_satisfies_security_floor_bumps() -> None:
    primary = _requirements(ROOT / "requirements.txt")
    ci = _requirements(ROOT / "requirements-ci.txt")
    lock = _lock_pins(LOCK_PATH)

    for package, floor in LOCKED_RELEASE_FLOORS.items():
        name = canonicalize_name(package)
        assert name in primary
        assert name in ci
        assert primary[name].specifier.contains(floor, prereleases=True)
        assert ci[name].specifier.contains(floor, prereleases=True)
        assert lock[name] >= floor


def test_release_lock_retains_osv_fixed_floors_for_shipped_packages() -> None:
    """Contract 2: every retained floor names a package that is still shipped
    (absence is a failure, not a skip) and the pin is at or above the floor."""
    lock = _lock_pins(LOCK_PATH)

    for table in (
        LOW_RISK_OSV_FIXED_FLOORS,
        BIG_JUMP_OSV_FIXED_FLOORS,
        OSV_2026_09_FIXED_FLOORS,
        LOCK_CONSISTENCY_FLOORS,
    ):
        for package, floor in table.items():
            name = canonicalize_name(package)
            assert name in lock, f"{package} has a security floor but is not in the lock"
            assert lock[name] >= floor, f"{package}=={lock[name]} is below floor {floor}"


def test_release_lock_excludes_descoped_dev_freeze_packages() -> None:
    """Contract 3: the developer-freeze leftovers stay out of the lock."""
    lock = _lock_pins(LOCK_PATH)

    present = sorted(
        package for package in DESCOPED_DEV_FREEZE_PACKAGES
        if canonicalize_name(package) in lock
    )
    assert not present, (
        f"de-scoped developer-freeze packages are back in the lock: {present}; "
        "re-adding one requires a deliberate edit of DESCOPED_DEV_FREEZE_PACKAGES"
    )


def test_descoped_and_retained_floor_tables_are_disjoint() -> None:
    retained = (
        set(LOW_RISK_OSV_FIXED_FLOORS)
        | set(BIG_JUMP_OSV_FIXED_FLOORS)
        | set(OSV_2026_09_FIXED_FLOORS)
        | set(LOCK_CONSISTENCY_FLOORS)
        | set(LOCKED_RELEASE_FLOORS)
    )
    overlap = {canonicalize_name(p) for p in retained} & {
        canonicalize_name(p) for p in DESCOPED_DEV_FREEZE_PACKAGES
    }
    assert not overlap, f"package both retained and de-scoped: {sorted(overlap)}"


def test_release_lock_excludes_no_fix_vulnerable_packages() -> None:
    lock = _lock_pins(LOCK_PATH)

    for package in NO_FIX_OSV_BLOCKLIST:
        assert canonicalize_name(package) not in lock


def test_release_lock_excludes_unused_incompatible_packages() -> None:
    lock = _lock_pins(LOCK_PATH)

    for package in UNUSED_INCOMPATIBLE_LOCK_BLOCKLIST:
        assert canonicalize_name(package) not in lock


def test_release_lock_preserves_torch_marker_contract() -> None:
    """Contract 4a: exactly the A2 cu126 torch marker pair, the cu126 extra
    index once, and none of the optional torch family."""
    options, requirements = _lock_lines(LOCK_PATH)

    assert options.count(TORCH_EXTRA_INDEX_LINE) == 1, options
    assert all(option == TORCH_EXTRA_INDEX_LINE for option in options), options

    torch_lines = {
        str(requirement.marker): requirement
        for requirement in requirements
        if canonicalize_name(requirement.name) == "torch"
    }
    assert set(torch_lines) == set(TORCH_MARKER_PAIR), sorted(torch_lines)
    for marker, expected in TORCH_MARKER_PAIR.items():
        requirement = torch_lines[marker]
        exact = [s.version for s in requirement.specifier if s.operator == "=="]
        assert exact == [str(expected)], (marker, str(requirement.specifier))

    names = {canonicalize_name(requirement.name) for requirement in requirements}
    assert not (names & TORCH_FAMILY_REQUIRED_ABSENT), sorted(
        names & TORCH_FAMILY_REQUIRED_ABSENT
    )


def test_release_lock_retains_operator_authorized_conditional_floors() -> None:
    """Contract 2b: the operator-authorized nvidia-* CUDA floors for the Linux
    torch path are present verbatim (specifier and marker)."""
    _, requirements = _lock_lines(LOCK_PATH)
    floors = {}
    exacts = {}
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        if name not in AUTHORIZED_CONDITIONAL_FLOORS:
            continue
        operators = [spec.operator for spec in requirement.specifier]
        if operators == [">="]:
            assert name not in floors, f"duplicate floor line: {requirement}"
            floors[name] = requirement
        elif operators == ["=="]:
            assert name not in exacts, f"duplicate exact pin: {requirement}"
            exacts[name] = requirement
        else:
            raise AssertionError(f"unexpected specifier for a floor package: {requirement}")

    for package, (specifier, marker) in AUTHORIZED_CONDITIONAL_FLOORS.items():
        name = canonicalize_name(package)
        assert name in floors, f"operator-authorized floor missing from the lock: {package}"
        requirement = floors[name]
        assert str(requirement.specifier) == specifier, (package, str(requirement.specifier))
        assert str(requirement.marker) == marker, (package, str(requirement.marker))
        # The exact Linux resolution of the floor must sit at or above it.
        assert name in exacts, f"floor {package} has no exact resolution pinned"
        pinned = Version(next(iter(exacts[name].specifier)).version)
        assert requirement.specifier.contains(pinned, prereleases=True), (
            f"{package}=={pinned} does not satisfy operator floor {specifier}"
        )


def test_release_lock_entries_are_exact_and_unique() -> None:
    """Contract 4b: every requirement line is a single exact ``==`` pin (the
    only non-exact lines are the operator-authorized conditional floors, each
    paired with its exact resolution) and no package appears twice, except the
    torch marker pair and those floor/exact pairs."""
    _, requirements = _lock_lines(LOCK_PATH)
    floor_names = {canonicalize_name(p) for p in AUTHORIZED_CONDITIONAL_FLOORS}

    seen: dict[str, int] = {}
    for requirement in requirements:
        name = canonicalize_name(requirement.name)
        operators = [spec.operator for spec in requirement.specifier]
        if name in floor_names:
            assert operators in ([">="], ["=="]), f"floor package line must be a floor or an exact pin: {requirement}"
        else:
            assert operators == ["=="], f"not an exact pin: {requirement}"
        assert not requirement.extras, f"extras do not belong in a lock: {requirement}"
        seen[name] = seen.get(name, 0) + 1
    duplicates = sorted(name for name, count in seen.items() if count > 1)
    assert duplicates == sorted(["torch", *floor_names]), duplicates
    assert seen["torch"] == len(TORCH_MARKER_PAIR)
    for name in floor_names:
        assert seen[name] == 2, f"{name}: expected exactly one floor and one exact pin"


def test_release_lock_pins_linux_only_dependencies_with_declaring_markers() -> None:
    """Contract 5: every Linux-only recursive dependency of the selected torch
    build (plus the cu12 floor resolutions and uvloop) is an exact pin carrying
    the declaring package's marker, and no other conditional entry exists."""
    _, requirements = _lock_lines(LOCK_PATH)
    floor_names = {canonicalize_name(p) for p in AUTHORIZED_CONDITIONAL_FLOORS}
    conditional: dict[str, Requirement] = {}
    for requirement in requirements:
        if requirement.marker is None:
            continue
        name = canonicalize_name(requirement.name)
        if name == "torch":
            continue
        operators = [spec.operator for spec in requirement.specifier]
        if name in floor_names and operators == [">="]:
            continue  # the operator floor line itself, covered by contract 2b
        assert name not in conditional, f"duplicate conditional pin: {requirement}"
        conditional[name] = requirement

    expected = {canonicalize_name(p): m for p, m in LINUX_ONLY_PINS.items()}
    unexpected = sorted(set(conditional) - set(expected))
    assert not unexpected, f"conditional pins not covered by the contract: {unexpected}"
    missing = sorted(set(expected) - set(conditional))
    assert not missing, f"Linux-only dependencies missing from the lock: {missing}"
    for name, marker in expected.items():
        requirement = conditional[name]
        operators = [spec.operator for spec in requirement.specifier]
        assert operators == ["=="], f"Linux-only dependency must be an exact pin: {requirement}"
        assert str(requirement.marker) == marker, (name, str(requirement.marker))

    # Unconditional entries must never carry a Linux-only package: that would
    # break a Windows install with a manylinux-only wheel.
    unconditional = {
        canonicalize_name(r.name) for r in requirements if r.marker is None
    }
    leaked = sorted(unconditional & set(expected))
    assert not leaked, f"Linux-only packages pinned without a marker: {leaked}"


def test_release_lock_covers_uvicorn_standard_extra() -> None:
    """The declared ``uvicorn[standard]`` extra is honoured by the lock: its
    unconditional members are pinned and uvloop is pinned for non-Windows."""
    lock = _lock_pins(LOCK_PATH)
    declared = _declared_direct_dependencies()
    assert "standard" in declared["uvicorn"].extras

    missing = sorted(
        member for member in UVICORN_STANDARD_UNCONDITIONAL
        if canonicalize_name(member) not in lock
    )
    assert not missing, f"uvicorn[standard] members missing from the lock: {missing}"
    assert "uvloop" in lock and "uvloop" in {
        canonicalize_name(p) for p in LINUX_ONLY_PINS
    }

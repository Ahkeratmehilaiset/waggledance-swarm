from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

from tools import release_evidence_runtime as runtime


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = ROOT / "tools" / "release_evidence_runtime.py"
PRODUCER_RELPATH = "tools/fake_release_producer.py"
OUTPUT_RELPATH = runtime.CANONICAL_OUTPUTS["axis_b_hex_aligned_eval"]
OLD_BYTES = b'{"schema_version":"stale-release-evidence"}\n'


class _CtypesCall:
    def __init__(self, function: object) -> None:
        self.function = function
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self.function(*args)  # type: ignore[operator]


def _fixture_git_executable() -> Path:
    if os.name == "nt":
        return Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
    return Path("/usr/bin/git")


def _fixture_git_environment() -> dict[str, str]:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
    }
    if os.name == "nt":
        windows = os.environ.get("SystemRoot", r"C:\Windows")
        environment["SystemRoot"] = windows
        environment["WINDIR"] = windows
    return environment


def _git(
    repo: Path,
    *arguments: str,
    stdin: bytes | None = None,
    check: bool = True,
) -> bytes:
    completed = subprocess.run(
        [os.fspath(_fixture_git_executable()), *arguments],
        cwd=repo,
        env=_fixture_git_environment(),
        input=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        close_fds=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"fixture git failed ({completed.returncode}): "
            f"{completed.stderr.decode('utf-8', errors='replace')}"
        )
    return completed.stdout


def _git_path(repo: Path, name: str) -> Path:
    rendered = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", name)
    return Path(rendered.decode("utf-8").strip())


def _raw_sha1(object_type: str, content: bytes) -> str:
    header = f"{object_type} {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _producer_spec() -> runtime.ProducerSpec:
    return runtime.ProducerSpec(
        producer_id="axis_b_hex_aligned_eval",
        producer_relpath=PRODUCER_RELPATH,
        canonical_output_relpath=OUTPUT_RELPATH,
        argv_contract=runtime.SealedArgvContract("empty"),
        allowed_hold_reason_sets=frozenset(
            {
                frozenset({"axis_b_not_pass"}),
                frozenset({"axis_b_quality_below_floor"}),
                frozenset({"axis_b_not_pass", "axis_b_quality_below_floor"}),
            }
        ),
    )


def _make_release_repo(
    tmp_path: Path,
    *,
    producer_bytes: bytes = b"VALUE = 7\n",
    with_output: bool = True,
    object_format: str = "sha1",
) -> tuple[Path, runtime.RepositorySnapshot, runtime.ProducerSpec, Path]:
    repo = tmp_path / "release-repo"
    repo.mkdir()
    init = ["init", "--initial-branch=main"]
    if object_format != "sha1":
        init.append(f"--object-format={object_format}")
    init.append(".")
    _git(repo, *init)
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "config", "core.filemode", "true")
    runtime_path = repo / runtime.RUNTIME_RELPATH
    producer_path = repo / PRODUCER_RELPATH
    output_path = repo / PurePosixPath(OUTPUT_RELPATH)
    runtime_path.parent.mkdir(parents=True)
    output_path.parent.mkdir(parents=True)
    (repo / ".gitattributes").write_bytes(b"* -text\n")
    runtime_path.write_bytes(RUNTIME_SOURCE.read_bytes())
    producer_path.write_bytes(producer_bytes)
    if with_output:
        output_path.write_bytes(OLD_BYTES)
    paths = [".gitattributes", runtime.RUNTIME_RELPATH, PRODUCER_RELPATH]
    if with_output:
        paths.append(OUTPUT_RELPATH)
    _git(repo, "add", "--", *paths)
    _git(
        repo,
        "-c",
        "user.name=release-test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    snapshot = runtime.capture_repository_snapshot(
        startup_cwd=repo,
        required_files=(runtime.RUNTIME_RELPATH, PRODUCER_RELPATH),
    )
    return repo, snapshot, _producer_spec(), output_path


def _make_unfrozen_repo(
    tmp_path: Path,
    *,
    producer_bytes: bytes,
    object_format: str = "sha1",
) -> tuple[Path, Path]:
    repo = tmp_path / "release-repo"
    repo.mkdir()
    init = ["init", "--initial-branch=main"]
    if object_format != "sha1":
        init.append(f"--object-format={object_format}")
    init.append(".")
    _git(repo, *init)
    _git(repo, "config", "core.autocrlf", "false")
    runtime_path = repo / runtime.RUNTIME_RELPATH
    producer_path = repo / PRODUCER_RELPATH
    runtime_path.parent.mkdir(parents=True)
    (repo / ".gitattributes").write_bytes(b"* -text\n")
    runtime_path.write_bytes(RUNTIME_SOURCE.read_bytes())
    producer_path.write_bytes(producer_bytes)
    _git(
        repo,
        "add",
        "--",
        ".gitattributes",
        runtime.RUNTIME_RELPATH,
        PRODUCER_RELPATH,
    )
    _git(
        repo,
        "-c",
        "user.name=release-test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    return repo, producer_path


def _capture(repo: Path) -> runtime.RepositorySnapshot:
    return runtime.capture_repository_snapshot(
        startup_cwd=repo,
        required_files=(runtime.RUNTIME_RELPATH, PRODUCER_RELPATH),
    )


def _pass_outcome() -> runtime.ProducerOutcome:
    return runtime.ProducerOutcome(
        status="pass",
        reason_codes=(),
        evidence={"evaluated": 140, "quality": 1.0},
        findings=(),
    )


def _hold_outcome(*reasons: str) -> runtime.ProducerOutcome:
    ordered = tuple(sorted(reasons))
    return runtime.ProducerOutcome(
        status="hold_nonpass",
        reason_codes=ordered,
        evidence=None,
        findings=tuple(
            {"reason_code": reason, "details": {"complete": True}}
            for reason in ordered
        ),
    )


def _frozen_producer_source(
    status: str,
    *,
    stdout_line: str | None = None,
) -> bytes:
    if status == "pass":
        outcome = (
            "ProducerOutcome(status='pass', reason_codes=(), "
            "evidence={'evaluated': 140, 'quality': 1.0}, findings=())"
        )
    else:
        outcome = (
            "ProducerOutcome(status='hold_nonpass', "
            "reason_codes=('axis_b_not_pass',), evidence=None, "
            "findings=({'reason_code': 'axis_b_not_pass', "
            "'details': {'complete': True}},))"
        )
    diagnostic = "" if stdout_line is None else f"    print({stdout_line!r}, flush=True)\n"
    source = f"""from tools.release_evidence_runtime import ProducerOutcome, ProducerSpec, SealedArgvContract

PRODUCER_SPEC = ProducerSpec(
    producer_id='axis_b_hex_aligned_eval',
    producer_relpath='{PRODUCER_RELPATH}',
    canonical_output_relpath='{OUTPUT_RELPATH}',
    argv_contract=SealedArgvContract('empty'),
    allowed_hold_reason_sets=frozenset((
        frozenset(('axis_b_not_pass',)),
        frozenset(('axis_b_quality_below_floor',)),
        frozenset(('axis_b_not_pass', 'axis_b_quality_below_floor')),
    )),
)

def produce(snapshot, argv):
    assert argv == ()
{diagnostic}    
    return {outcome}
"""
    return source.encode("utf-8")


def _frozen_exit_producer_source(exit_code: int | None) -> bytes:
    if exit_code is None:
        body = "raise ImportError('deterministic producer crash')"
    else:
        body = f"__import__('os')._exit({exit_code})"
    source = _frozen_producer_source("pass").decode("utf-8")
    prefix, _separator, _old_body = source.partition("def produce(snapshot, argv):\n")
    return (prefix + f"def produce(snapshot, argv):\n    {body}\n").encode("utf-8")


def _independent_envelope(
    snapshot: runtime.RepositorySnapshot,
    spec: runtime.ProducerSpec,
    *,
    status: str = "pass",
) -> dict[str, object]:
    reasons: list[str]
    findings: list[dict[str, object]]
    evidence: dict[str, object] | None
    if status == "pass":
        reasons = []
        findings = []
        evidence = {"evaluated": 140, "quality": 1.0}
    else:
        reasons = ["axis_b_not_pass"]
        findings = [
            {
                "reason_code": "axis_b_not_pass",
                "details": {"complete": True},
            }
        ]
        evidence = None
    return {
        "schema_version": runtime.ENVELOPE_SCHEMA_VERSION,
        "producer_id": spec.producer_id,
        "status": status,
        "reason_codes": reasons,
        "source": {
            "commit": snapshot.head,
            "tree": snapshot.tree,
            "index_sha256": snapshot.index_sha256,
            "text_normalization": runtime.SOURCE_NORMALIZATION,
            "files": [
                {
                    "path": item.relpath,
                    "mode": item.mode,
                    "blob_oid": item.oid,
                    "sha256": item.sha256,
                }
                for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
            ],
        },
        "evidence": evidence,
        "findings": findings,
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _reserved_residue(parent: Path) -> list[Path]:
    return sorted(parent.glob(".wdre*"))


def _independent_identity(identity: runtime.FileIdentity) -> dict[str, object]:
    file_id: int | str
    if type(identity.file_id) is bytes:
        file_id = base64.b64encode(identity.file_id).decode("ascii")
    else:
        assert type(identity.file_id) is int
        file_id = identity.file_id
    return {
        "scheme": identity.scheme,
        "volume": identity.volume,
        "file_id": file_id,
        "size": identity.size,
        "mtime_ns": identity.mtime_ns,
        "nlink": identity.nlink,
    }


def _independent_receipt(
    *,
    nonce: str,
    frame_sha256: str,
    private_exit: int,
    status: str,
    output: Path | None,
) -> dict[str, object]:
    if output is None:
        output_name = output_sha256 = output_identity = None
    else:
        content, identity = runtime._read_plain_file(output)
        output_name = output.name
        output_sha256 = "sha256:" + hashlib.sha256(content).hexdigest()
        output_identity = _independent_identity(identity)
    return {
        "schema": "waggledance.release_evidence_child_receipt.v1",
        "nonce": nonce,
        "frame_sha256": frame_sha256,
        "private_exit": private_exit,
        "status": status,
        "canonical_output": output_name,
        "canonical_sha256": output_sha256,
        "canonical_identity": output_identity,
    }


def _prepare_transaction(
    *,
    snapshot: runtime.RepositorySnapshot,
    spec: runtime.ProducerSpec,
    output: Path,
    outcome: runtime.ProducerOutcome,
    nonce: str,
    promote: bool,
) -> tuple[tuple[bytes, runtime.FileIdentity] | None, dict[str, str], bytes]:
    prestate = runtime._read_optional_plain(output)
    envelope = runtime.build_completion_envelope(
        snapshot=snapshot,
        spec=spec,
        outcome=outcome,
    )
    new_content = runtime.serialize_completion_envelope(envelope)
    names = runtime._transaction_names(output.name, nonce)
    with runtime._open_directory_leases(output.parent) as leases:
        lease = leases[-1]
        candidate_identity = runtime._dir_write_exclusive(
            lease,
            names["candidate"],
            new_content,
        )
        descriptor = runtime._transaction_descriptor(
            target=output,
            nonce=nonce,
            names=names,
            prestate=prestate,
            new_content=new_content,
            new_identity=candidate_identity,
            snapshot=snapshot,
            spec=spec,
            status=outcome.status,
            public_exit=(
                runtime.EXIT_PASS
                if outcome.status == "pass"
                else runtime.EXIT_HOLD_NONPASS
            ),
        )
        runtime._dir_write_exclusive(
            lease,
            names["descriptor"],
            runtime._canonical_json_bytes(descriptor),
        )
        lease.flush()
        if promote:
            if prestate is None:
                runtime._cas_promote_absent(
                    lease,
                    output.name,
                    names["candidate"],
                    expected_candidate=(new_content, candidate_identity),
                )
            else:
                runtime._cas_exchange_existing(
                    lease,
                    output.name,
                    names["candidate"],
                    names["backup"],
                    expected_target=prestate,
                    expected_candidate=(new_content, candidate_identity),
                )
            lease.flush()
    return prestate, names, new_content


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute/path",
        "C:/absolute/path",
        "../escape",
        "a/../escape",
        "a\\b.py",
        "a:b.py",
        "a//b.py",
        "a/./b.py",
        "a/trailing. ",
        "a/trailing.",
        "a/CON.txt",
        "a/com1",
        "a/\x01b",
        "a/\ud800b",
    ],
)
def test_repo_relpath_rejects_cross_platform_aliases(value: str) -> None:
    with pytest.raises(runtime.EvidenceIntegrityError):
        runtime.validate_repo_relpath(value)


def test_repo_relpath_list_rejects_exact_and_casefold_duplicates() -> None:
    with pytest.raises(runtime.EvidenceIntegrityError, match="duplicate_repository_path"):
        runtime.validate_repo_relpath_list(("a/b.py", "a/b.py"))
    with pytest.raises(runtime.EvidenceIntegrityError, match="repository_path_alias"):
        runtime.validate_repo_relpath_list(("a/b.py", "A/B.py"))
    assert runtime.validate_repo_relpath("a/b.py") == "a/b.py"


def test_sealed_argv_contracts_are_closed_and_exact() -> None:
    empty = runtime.SealedArgvContract("empty")
    exact = runtime.SealedArgvContract("exact", ("--release-evidence",))
    sources = runtime.SealedArgvContract("source_pairs")
    assert runtime.validate_sealed_argv((), empty) == ()
    assert runtime.validate_sealed_argv(("--release-evidence",), exact) == (
        "--release-evidence",
    )
    assert runtime.validate_sealed_argv(
        ("--source", "one.jsonl", "--source", "two.log"), sources
    ) == ("--source", "one.jsonl", "--source", "two.log")
    for invalid in (("--help",), ("--version",), ("-h",), ("unknown",)):
        with pytest.raises(runtime.EvidenceIntegrityError, match="invalid_sealed_argv"):
            runtime.validate_sealed_argv(invalid, empty)
    for invalid in (
        ("--source",),
        ("--source=x",),
        ("--source", "--help"),
        ("--source", ""),
        ("--source", "x", "--source", "x"),
    ):
        with pytest.raises(runtime.EvidenceIntegrityError):
            runtime.validate_sealed_argv(invalid, sources)


def test_release_selector_never_falls_malformed_release_token_to_general_mode() -> None:
    assert runtime.p1_release_mode_requested(("--release-evidence",)) is True
    assert runtime.p1_release_mode_requested(("--release-evidence=yes",)) is True
    assert runtime.p1_release_mode_requested(("--out-dir", "scratch")) is False


def test_integrity_boundary_accepts_only_exact_public_ints() -> None:
    assert runtime.run_integrity_boundary(lambda: 0) == runtime.EXIT_PASS
    assert runtime.run_integrity_boundary(lambda: 1) == runtime.EXIT_HOLD_NONPASS
    for invalid in (True, False, 0.0, 1.0, 40, 41, 42, None):
        assert runtime.run_integrity_boundary(lambda invalid=invalid: invalid) == 2

    def explode() -> int:
        raise KeyboardInterrupt

    assert runtime.run_integrity_boundary(explode) == runtime.EXIT_INTEGRITY


def test_fail_stop_can_never_be_collapsed_to_public_integrity_exit() -> None:
    def blocked() -> int:
        raise runtime.EvidenceFailStop()

    with pytest.raises(runtime.EvidenceFailStop):
        runtime.run_integrity_boundary(blocked)


def test_run_sealed_producer_rejects_argv_before_git_temp_or_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touched: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> object:
        touched.append("side-effect")
        raise AssertionError("side effect reached before argv rejection")

    monkeypatch.setattr(runtime, "capture_repository_snapshot", forbidden)
    monkeypatch.setattr(runtime, "create_isolation_prefix", forbidden)
    result = runtime.run_sealed_producer(
        executing_file=tmp_path / "not-read.py",
        raw_argv=("--help",),
        bootstrap_spec=_producer_spec(),
        required_files=(),
    )
    assert type(result) is int and result == runtime.EXIT_INTEGRITY
    assert touched == []


def test_fresh_environment_is_enumerated_and_drops_hidden_git_python_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = {
        "PATH": "C:/attacker",
        "HOME": "C:/attacker",
        "GIT_DIR": "C:/attacker/repo",
        "GIT_WORK_TREE": "C:/attacker/tree",
        "GIT_INDEX_FILE": "C:/attacker/index",
        "GIT_OBJECT_DIRECTORY": "C:/attacker/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "C:/attacker/alternate",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "filter.evil.smudge",
        "GIT_CONFIG_VALUE_0": "attacker",
        "PYTHONPATH": "C:/attacker/python",
        "PYTHONHOME": "C:/attacker/python",
        "HTTPS_PROXY": "http://attacker.invalid",
        "SSL_CERT_FILE": "C:/attacker.pem",
    }
    for key, value in poisoned.items():
        monkeypatch.setenv(key, value)
    environment = runtime.fresh_process_environment()
    assert not (set(poisoned) & set(environment))
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert environment["GIT_NO_LAZY_FETCH"] == "1"
    assert environment["TZ"] == "UTC"


def test_unknown_child_environment_extra_fails_without_silent_scrubbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = runtime.fresh_process_environment()
    environment["WD_UNKNOWN_RELEASE_EVIDENCE_EXTRA"] = "must-not-be-scrubbed"
    monkeypatch.setattr(runtime.os, "environ", environment)
    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^child_environment_not_fresh$",
    ):
        runtime.scrub_and_validate_child_environment()
    assert environment["WD_UNKNOWN_RELEASE_EVIDENCE_EXTRA"] == (
        "must-not-be-scrubbed"
    )


def test_trusted_git_is_fixed_direct_engine_not_launcher_or_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "C:/attacker")
    selected = runtime._trusted_git_executable()
    assert selected.is_absolute()
    if os.name == "nt":
        assert selected == Path(r"C:\Program Files\Git\mingw64\bin\git.exe")
    else:
        assert selected == Path("/usr/bin/git")


def test_trusted_git_binding_covers_every_link_inside_protected_prefix() -> None:
    binding = runtime._bind_trusted_git()
    assert binding.path == runtime._trusted_git_literal()
    assert binding.sha256.startswith("sha256:")
    assert len(binding.aliases) == binding.identity.nlink
    if os.name == "nt":
        protected = Path(r"C:\Program Files\Git\mingw64")
        assert len(binding.aliases) >= 1
        for alias in binding.aliases:
            Path(alias).resolve(strict=True).relative_to(protected.resolve(strict=True))
        assert os.path.normcase(os.fspath(binding.path)) in {
            os.path.normcase(alias) for alias in binding.aliases
        }
    else:
        info = os.stat(binding.path)
        assert info.st_uid == 0
        assert info.st_mode & 0o022 == 0


def test_trusted_git_command_prefix_forbids_lazy_fetch_and_path_filters() -> None:
    executable = runtime._trusted_git_literal()
    arguments = runtime._git_base_argv(executable)
    assert arguments[0] == os.fspath(executable)
    assert "--no-replace-objects" in arguments
    assert "--no-lazy-fetch" in arguments
    assert "--literal-pathspecs" in arguments
    assert all("filter" not in argument.casefold() for argument in arguments)
    assert all("fetch" not in argument.casefold() or argument == "--no-lazy-fetch" for argument in arguments)


def test_windows_process_image_query_retries_fresh_state_without_path_inference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queried = r"\Device\HarddiskVolume7\trusted\kernel-reported-python.exe"
    inferred = tmp_path / "inferred-from-command-line.exe"
    inferred.write_bytes(b"inferred")
    attempts: list[tuple[object, int, int]] = []
    sleeps: list[float] = []

    class Query:
        argtypes: object = None
        restype: object = None

        def __call__(
            self,
            _handle: object,
            _flags: object,
            buffer: object,
            size_pointer: object,
        ) -> bool:
            size = size_pointer._obj  # type: ignore[attr-defined]
            attempts.append((buffer, int(size.value), int(_flags)))
            if len(attempts) < 3:
                size.value = 7
                return False
            buffer.value = queried  # type: ignore[attr-defined]
            size.value = len(queried)
            return True

    class Kernel32:
        def __init__(self) -> None:
            self.QueryFullProcessImageNameW = Query()

    class Process:
        _handle = 31337
        pid = 31337
        args = (os.fspath(inferred),)

    kernel32 = Kernel32()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: kernel32,
        raising=False,
    )
    monkeypatch.setattr(runtime.ctypes, "set_last_error", lambda _value: None, raising=False)
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    def reject_path_interpretation(*_args: object, **_kwargs: object) -> Path:
        raise AssertionError("opaque native image must not enter path normalization")

    monkeypatch.setattr(runtime, "_absolute_lexical", reject_path_interpretation)

    observed = runtime._process_image_path(Process())  # type: ignore[arg-type]

    assert observed == queried
    assert observed != os.fspath(inferred)
    assert len(attempts) == 3
    assert len({id(buffer) for buffer, _size, _flags in attempts}) == 3
    assert [size for _buffer, size, _flags in attempts] == [32768, 32768, 32768]
    assert [flags for _buffer, _size, flags in attempts] == [1, 1, 1]
    assert sleeps == [0.002, 0.002]


def test_windows_process_image_query_persistent_failure_has_exact_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inferred = tmp_path / "inferred-existing.exe"
    inferred.write_bytes(b"must not be inferred")
    attempts: list[tuple[object, int, int]] = []
    sleeps: list[float] = []

    class Query:
        argtypes: object = None
        restype: object = None

        def __call__(
            self,
            _handle: object,
            _flags: object,
            buffer: object,
            size_pointer: object,
        ) -> bool:
            size = size_pointer._obj  # type: ignore[attr-defined]
            attempts.append((buffer, int(size.value), int(_flags)))
            size.value = 1
            return False

    class Kernel32:
        def __init__(self) -> None:
            self.QueryFullProcessImageNameW = Query()

    class Process:
        _handle = 4242
        pid = 4242
        args = (os.fspath(inferred),)

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: Kernel32(),
        raising=False,
    )
    monkeypatch.setattr(runtime.ctypes, "set_last_error", lambda _value: None, raising=False)
    monkeypatch.setattr(runtime.time, "sleep", sleeps.append)

    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^process_image_unavailable$",
    ) as caught:
        runtime._process_image_path(Process())  # type: ignore[arg-type]

    assert caught.value.reason_code == "process_image_unavailable"
    assert caught.value.phase == "isolation"
    assert len(attempts) == 4
    assert len({id(buffer) for buffer, _size, _flags in attempts}) == 4
    assert [size for _buffer, size, _flags in attempts] == [32768] * 4
    assert [flags for _buffer, _size, flags in attempts] == [1] * 4
    assert sleeps == [0.002] * 3


@pytest.mark.parametrize(
    ("value", "reported_length"),
    [
        ("", 0),
        (r"C:\trusted.exe", len(r"C:\trusted.exe")),
        (
            r"\Device\HarddiskVolume9\trusted.exe",
            len(r"\Device\HarddiskVolume9\trusted.exe") + 1,
        ),
    ],
)
def test_windows_process_image_query_rejects_malformed_success(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
    reported_length: int,
) -> None:
    class Query:
        argtypes: object = None
        restype: object = None

        def __call__(
            self,
            _handle: object,
            flags: object,
            buffer: object,
            size_pointer: object,
        ) -> bool:
            assert int(flags) == 1
            buffer.value = value  # type: ignore[attr-defined]
            size_pointer._obj.value = reported_length  # type: ignore[attr-defined]
            return True

    class Kernel32:
        def __init__(self) -> None:
            self.QueryFullProcessImageNameW = Query()

    class Process:
        _handle = 6161

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(
        runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: Kernel32(),
        raising=False,
    )
    monkeypatch.setattr(runtime.ctypes, "set_last_error", lambda _value: None, raising=False)

    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^process_image_unavailable$",
    ) as caught:
        runtime._process_image_path(Process())  # type: ignore[arg-type]

    assert caught.value.phase == "isolation"


def test_windows_expected_native_image_uses_independent_handle_and_nt_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"trusted")
    native = r"\Device\HarddiskVolume9\trusted.exe"
    opened: list[tuple[object, ...]] = []
    queried: list[tuple[object, ...]] = []
    closed: list[object] = []

    def create_file(*args: object) -> int:
        opened.append(args)
        return 31337

    def final_path(
        handle: object,
        buffer: object,
        size: object,
        flags: object,
    ) -> int:
        queried.append((handle, size, flags))
        buffer.value = native  # type: ignore[attr-defined]
        return len(native)

    def close_handle(handle: object) -> bool:
        closed.append(handle)
        return True

    class Kernel32:
        CreateFileW = _CtypesCall(create_file)
        GetFinalPathNameByHandleW = _CtypesCall(final_path)
        CloseHandle = _CtypesCall(close_handle)

    monkeypatch.setattr(
        runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: Kernel32(),
        raising=False,
    )

    observed = runtime._windows_expected_native_image(executable)

    assert observed == native
    assert opened == [
        (
            os.fspath(executable),
            0x80000000,
            0x00000001,
            None,
            3,
            0x00200000,
            None,
        )
    ]
    assert queried == [(31337, 32768, 0x2)]
    assert closed == [31337]


@pytest.mark.parametrize(
    ("mode", "reason", "expected_closes"),
    [
        ("open", "windows_expected_native_path_open_failed", 0),
        ("empty", "windows_expected_native_path_unavailable", 1),
        ("empty_close", "windows_expected_native_path_unavailable", 1),
        ("oversize", "windows_expected_native_path_unavailable", 1),
        ("length", "windows_expected_native_path_unavailable", 1),
        ("drive", "windows_expected_native_path_unavailable", 1),
        ("close", "windows_expected_native_path_close_failed", 1),
    ],
)
def test_windows_expected_native_image_failures_are_closed_and_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    reason: str,
    expected_closes: int,
) -> None:
    from ctypes import wintypes

    executable = tmp_path / "trusted.exe"
    executable.write_bytes(b"trusted")
    closed: list[object] = []

    def create_file(*_args: object) -> int:
        if mode == "open":
            return wintypes.HANDLE(-1).value
        return 4242

    def final_path(
        _handle: object,
        buffer: object,
        _size: object,
        flags: object,
    ) -> int:
        assert flags == 0x2
        if mode in {"empty", "empty_close"}:
            return 0
        if mode == "oversize":
            return 32768
        value = (
            r"C:\trusted.exe"
            if mode == "drive"
            else r"\Device\HarddiskVolume9\trusted.exe"
        )
        buffer.value = value  # type: ignore[attr-defined]
        return len(value) + (1 if mode == "length" else 0)

    def close_handle(handle: object) -> bool:
        closed.append(handle)
        return mode not in {"close", "empty_close"}

    class Kernel32:
        CreateFileW = _CtypesCall(create_file)
        GetFinalPathNameByHandleW = _CtypesCall(final_path)
        CloseHandle = _CtypesCall(close_handle)

    monkeypatch.setattr(
        runtime.ctypes,
        "WinDLL",
        lambda *_args, **_kwargs: Kernel32(),
        raising=False,
    )

    with pytest.raises(runtime.EvidenceIntegrityError, match=f"^{reason}$") as caught:
        runtime._windows_expected_native_image(executable)

    assert caught.value.reason_code == reason
    assert caught.value.phase == "isolation"
    assert len(closed) == expected_closes


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process handles only")
def test_windows_native_process_image_survives_short_process_exit() -> None:
    executable = runtime._trusted_git_literal()
    expected = runtime._windows_expected_native_image(executable)
    for _attempt in range(5):
        with subprocess.Popen(
            [os.fspath(executable), "version"],
            executable=os.fspath(executable),
            env=_fixture_git_environment(),
            shell=False,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as process:
            stdout, _stderr = process.communicate(timeout=10)
            assert process.returncode == 0
            assert stdout.startswith(b"git version ")
            observed = runtime._process_image_path(process)
            assert type(observed) is str
            assert os.path.normcase(observed) == os.path.normcase(expected)


def test_run_git_preserves_process_image_error_and_kills_and_reaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "bound-git.exe"
    executable.write_bytes(b"bound executable")
    content, identity = runtime._read_plain_file(
        executable,
        require_single_link=False,
    )
    binding = runtime._ExecutableBinding(
        path=executable,
        identity=identity,
        sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        aliases=(os.fspath(executable),),
    )
    image_error = runtime.EvidenceIntegrityError(
        "process_image_unavailable",
        phase="isolation",
    )

    class Process:
        returncode: int | None = None
        killed = False
        reaped = False
        communicate_calls = 0

        def poll(self) -> int | None:
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            self.communicate_calls += 1
            self.reaped = True
            return b"", b""

    process = Process()
    monkeypatch.setattr(runtime, "_bind_trusted_git", lambda: binding)
    popen_kwargs: dict[str, object] = {}

    def popen(*_args: object, **kwargs: object) -> Process:
        popen_kwargs.update(kwargs)
        return process

    monkeypatch.setattr(runtime.subprocess, "Popen", popen)

    def fail_image(_process: object) -> Path:
        raise image_error

    monkeypatch.setattr(runtime, "_process_image_path", fail_image)

    with pytest.raises(runtime.EvidenceIntegrityError) as caught:
        runtime._run_git(executable, tmp_path, "status", "--porcelain=v1")

    assert caught.value is image_error
    assert caught.value.reason_code == "process_image_unavailable"
    assert caught.value.phase == "isolation"
    assert process.killed is True
    assert process.reaped is True
    assert process.communicate_calls == 1
    assert popen_kwargs["executable"] == os.fspath(executable)


@pytest.mark.parametrize("failure", ["mismatch", "changed"])
def test_run_git_preserves_image_failure_for_already_exited_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    executable = tmp_path / "bound-git.exe"
    executable.write_bytes(b"bound executable")
    content, identity = runtime._read_plain_file(executable, require_single_link=False)
    binding = runtime._ExecutableBinding(
        path=executable,
        identity=identity,
        sha256="sha256:" + hashlib.sha256(content).hexdigest(),
        aliases=(os.fspath(executable),),
    )
    expected_native = r"\Device\HarddiskVolume9\bound-git.exe"

    class Process:
        returncode = 0
        killed = False
        communicate_calls = 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            self.killed = True
            raise OSError("an exited process must not be killed")

        def communicate(self, *_args: object, **_kwargs: object) -> tuple[bytes, bytes]:
            self.communicate_calls += 1
            return b"", b""

    process = Process()
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "_bind_trusted_git", lambda: binding)
    monkeypatch.setattr(
        runtime,
        "_windows_expected_native_image",
        lambda _path: expected_native,
    )
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        runtime,
        "_process_image_path",
        lambda _process: (
            r"\Device\HarddiskVolume9\other.exe"
            if failure == "mismatch"
            else expected_native
        ),
    )
    if failure == "changed":
        changed_identity = replace(identity, size=identity.size + 1)
        monkeypatch.setattr(
            runtime,
            "_read_plain_file",
            lambda *_args, **_kwargs: (content, changed_identity),
        )

    reason = (
        "trusted_git_process_image_mismatch"
        if failure == "mismatch"
        else "trusted_git_process_image_changed"
    )
    with pytest.raises(runtime.EvidenceIntegrityError, match=f"^{reason}$") as caught:
        runtime._run_git(executable, tmp_path, "version")

    assert caught.value.reason_code == reason
    assert caught.value.phase == "git"
    assert process.killed is False
    assert process.communicate_calls == 1


def test_snapshot_binds_exact_raw_head_blob_oid_sha256_and_index(tmp_path: Path) -> None:
    _repo, snapshot, _spec, _output = _make_release_repo(tmp_path)
    producer = snapshot.blob(PRODUCER_RELPATH)
    assert producer.content == b"VALUE = 7\n"
    assert producer.oid == _raw_sha1("blob", producer.content)
    assert producer.sha256 == "sha256:" + hashlib.sha256(producer.content).hexdigest()
    assert len(snapshot.head) == 40
    assert len(snapshot.tree) == 40
    assert snapshot.index_sha256.startswith("sha256:")
    assert "crlf" not in runtime.SOURCE_NORMALIZATION.casefold()


@pytest.mark.parametrize("bad_source", [b"VALUE = 7\r\n", b"VALUE = 7\r"])
def test_snapshot_rejects_every_carriage_return_without_normalization(
    tmp_path: Path,
    bad_source: bytes,
) -> None:
    repo, _producer = _make_unfrozen_repo(tmp_path, producer_bytes=bad_source)
    with pytest.raises(runtime.EvidenceIntegrityError, match="source_contains_cr"):
        _capture(repo)


def test_snapshot_rejects_lfs_pointer_instead_of_loading_filtered_content(
    tmp_path: Path,
) -> None:
    pointer = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + b"a" * 64 + b"\nsize 7\n"
    )
    repo, _producer = _make_unfrozen_repo(tmp_path, producer_bytes=pointer)
    with pytest.raises(runtime.EvidenceIntegrityError, match="lfs"):
        _capture(repo)


def test_snapshot_rejects_source_filter_attribute_without_running_filter(
    tmp_path: Path,
) -> None:
    repo, _snapshot, _spec, _output = _make_release_repo(tmp_path)
    attributes = repo / ".gitattributes"
    attributes.write_text(
        f"{PRODUCER_RELPATH} filter=evil\n",
        encoding="utf-8",
        newline="\n",
    )
    _git(repo, "add", "--", ".gitattributes")
    _git(
        repo,
        "-c",
        "user.name=release-test",
        "-c",
        "user.email=release-test@example.invalid",
        "commit",
        "-m",
        "hostile attribute",
    )
    marker = tmp_path / "filter-ran"
    _git(repo, "config", "filter.evil.smudge", f"echo unsafe > {marker}")
    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^(tracked_source_attributes_forbidden|git_driver_or_promisor_forbidden)$",
    ):
        _capture(repo)
    assert not marker.exists()


def test_snapshot_rejects_local_object_alternates(tmp_path: Path) -> None:
    repo, _snapshot, _spec, _output = _make_release_repo(tmp_path)
    alternates = _git_path(repo, "objects") / "info" / "alternates"
    alternates.parent.mkdir(parents=True, exist_ok=True)
    alternates.write_text(os.fspath(tmp_path / "attacker-objects"), encoding="utf-8")
    with pytest.raises(runtime.EvidenceIntegrityError, match="alternates"):
        _capture(repo)


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_snapshot_rejects_required_index_hidden_flags(
    tmp_path: Path,
    flag: str,
) -> None:
    repo, _snapshot, _spec, _output = _make_release_repo(tmp_path)
    _git(repo, "update-index", flag, "--", PRODUCER_RELPATH)
    with pytest.raises(runtime.EvidenceIntegrityError, match="index"):
        _capture(repo)


def test_snapshot_rejects_non_100644_required_entry(tmp_path: Path) -> None:
    del tmp_path
    oid = "1" * 40
    raw = f"100755 {oid} 0\t{PRODUCER_RELPATH}\0".encode("ascii")
    with pytest.raises(runtime.EvidenceIntegrityError, match="mode"):
        runtime._parse_stage_record(raw, PRODUCER_RELPATH)


def test_snapshot_rejects_corrupt_raw_object_without_fetch_or_filter_fallback(
    tmp_path: Path,
) -> None:
    repo, _snapshot, _spec, _output = _make_release_repo(tmp_path)
    stage = _git(repo, "ls-files", "--stage", "--", PRODUCER_RELPATH).decode("ascii")
    oid = stage.split()[1]
    object_path = _git_path(repo, "objects") / oid[:2] / oid[2:]
    assert object_path.is_file()
    object_path.chmod(stat.S_IREAD | stat.S_IWRITE)
    object_path.write_bytes(b"not-a-zlib-git-object")
    with pytest.raises(runtime.EvidenceIntegrityError):
        _capture(repo)


def test_snapshot_rejects_corrupt_index_checksum(tmp_path: Path) -> None:
    repo, _snapshot, _spec, _output = _make_release_repo(tmp_path)
    index = _git_path(repo, "index")
    content = bytearray(index.read_bytes())
    content[-1] ^= 0x01
    index.write_bytes(content)
    with pytest.raises(runtime.EvidenceIntegrityError, match="index"):
        _capture(repo)


def test_snapshot_rejects_non_sha1_object_repository(tmp_path: Path) -> None:
    try:
        repo, _producer = _make_unfrozen_repo(
            tmp_path,
            producer_bytes=b"VALUE = 7\n",
            object_format="sha256",
        )
    except AssertionError as exc:
        pytest.skip(f"fixture Git lacks sha256 repository support: {exc}")
    with pytest.raises(runtime.EvidenceIntegrityError, match="object_format"):
        _capture(repo)


def test_snapshot_revalidation_detects_source_index_lock_and_untracked_drift(
    tmp_path: Path,
) -> None:
    repo, snapshot, _spec, _output = _make_release_repo(tmp_path)
    (repo / PRODUCER_RELPATH).write_bytes(b"VALUE = 8\n")
    with pytest.raises(runtime.EvidenceIntegrityError):
        runtime.revalidate_repository_snapshot(snapshot)
    (repo / PRODUCER_RELPATH).write_bytes(b"VALUE = 7\n")
    snapshot.index_lock_path.write_bytes(b"")
    with pytest.raises(runtime.EvidenceIntegrityError, match="index_lock"):
        runtime.revalidate_repository_snapshot(snapshot)
    snapshot.index_lock_path.unlink()
    (repo / "untracked.txt").write_text("drift", encoding="utf-8")
    with pytest.raises(runtime.EvidenceIntegrityError, match="worktree"):
        runtime.revalidate_repository_snapshot(snapshot)


def test_verified_source_executes_exact_frozen_bytes_not_checkout_import(
    tmp_path: Path,
) -> None:
    _repo, snapshot, _spec, _output = _make_release_repo(tmp_path)
    namespace = runtime.exec_verified_source(
        snapshot,
        relpath=PRODUCER_RELPATH,
        module_name="_waggledance_test_frozen_producer",
    )
    assert namespace["VALUE"] == 7


def test_completion_envelope_is_closed_canonical_and_nonempty(tmp_path: Path) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    envelope = runtime.build_completion_envelope(
        snapshot=snapshot,
        spec=spec,
        outcome=_pass_outcome(),
    )
    assert set(envelope) == {
        "schema_version",
        "producer_id",
        "status",
        "reason_codes",
        "source",
        "evidence",
        "findings",
    }
    assert envelope["schema_version"] == runtime.ENVELOPE_SCHEMA_VERSION
    assert envelope["status"] == "pass"
    assert envelope["evidence"]
    assert envelope["source"]["commit"] == snapshot.head
    assert envelope["source"]["tree"] == snapshot.tree
    assert envelope["source"]["index_sha256"] == snapshot.index_sha256
    assert [item["path"] for item in envelope["source"]["files"]] == sorted(
        (runtime.RUNTIME_RELPATH, PRODUCER_RELPATH)
    )
    encoded = runtime.serialize_completion_envelope(envelope)
    assert encoded.endswith(b"\n") and not encoded.endswith(b"\n\n")
    assert json.loads(encoded) == envelope
    assert encoded == runtime.serialize_completion_envelope(envelope)


def test_parent_completion_validator_accepts_independently_constructed_exact_bytes(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    for status in ("pass", "hold_nonpass"):
        envelope = _independent_envelope(snapshot, spec, status=status)
        raw = _canonical_json(envelope)
        assert runtime._validate_completion_bytes(raw, snapshot, spec) == envelope


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_top_key",
        "missing_top_key",
        "empty_pass_evidence",
        "stale_commit",
        "source_extra_key",
        "reason_bool",
        "status_bool",
        "files_reversed",
    ],
)
def test_parent_completion_validator_rejects_open_or_wrong_binding(
    tmp_path: Path,
    mutation: str,
) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    envelope = _independent_envelope(snapshot, spec)
    if mutation == "extra_top_key":
        envelope["unexpected"] = True
    elif mutation == "missing_top_key":
        del envelope["findings"]
    elif mutation == "empty_pass_evidence":
        envelope["evidence"] = {}
    elif mutation == "stale_commit":
        source = envelope["source"]
        assert isinstance(source, dict)
        source["commit"] = "0" * 40
    elif mutation == "source_extra_key":
        source = envelope["source"]
        assert isinstance(source, dict)
        source["unexpected"] = True
    elif mutation == "reason_bool":
        envelope["reason_codes"] = [True]
    elif mutation == "status_bool":
        envelope["status"] = True
    elif mutation == "files_reversed":
        source = envelope["source"]
        assert isinstance(source, dict)
        files = source["files"]
        assert isinstance(files, list)
        source["files"] = list(reversed(files))
    with pytest.raises(runtime.EvidenceIntegrityError):
        runtime._validate_completion_bytes(_canonical_json(envelope), snapshot, spec)


def test_parent_completion_validator_rejects_duplicate_keys_and_noncanonical_json(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    envelope = _independent_envelope(snapshot, spec)
    canonical = _canonical_json(envelope)
    duplicate = canonical[:-2] + b',"status":"pass"}\n'
    with pytest.raises(runtime.EvidenceIntegrityError, match="completion_json_invalid"):
        runtime._validate_completion_bytes(duplicate, snapshot, spec)
    noncanonical = json.dumps(envelope, sort_keys=False, indent=2).encode("utf-8") + b"\n"
    with pytest.raises(runtime.EvidenceIntegrityError, match="completion_not_canonical"):
        runtime._validate_completion_bytes(noncanonical, snapshot, spec)


def test_parent_hold_validator_rejects_duplicate_or_empty_findings(tmp_path: Path) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    envelope = _independent_envelope(snapshot, spec, status="hold_nonpass")
    findings = envelope["findings"]
    assert isinstance(findings, list)
    findings.append(
        {
            "reason_code": "axis_b_not_pass",
            "details": {"duplicate": True},
        }
    )
    with pytest.raises(runtime.EvidenceIntegrityError):
        runtime._validate_completion_bytes(_canonical_json(envelope), snapshot, spec)


def test_child_frame_is_closed_deterministic_and_binds_frozen_bytes(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate = runtime._read_optional_plain(output)
    interpreter = runtime._bind_current_interpreter()
    frame = runtime._build_child_frame(
        snapshot=snapshot,
        spec=spec,
        validated_argv=(),
        canonical_prestate=prestate,
        nonce="1" * 32,
        interpreter_binding=interpreter,
    )
    assert set(frame) == {
        "schema",
        "nonce",
        "argv",
        "required_files",
        "spec",
        "snapshot",
        "interpreter",
        "environment",
        "canonical_prestate",
    }
    assert frame["schema"] == "waggledance.release_evidence_child_frame.v1"
    assert frame["nonce"] == "1" * 32
    assert frame["argv"] == []
    assert frame["interpreter"] == interpreter
    blobs = frame["snapshot"]["blobs"]
    assert isinstance(blobs, list)
    decoded = {
        item["path"]: base64.b64decode(item["content_b64"], validate=True)
        for item in blobs
    }
    assert decoded == {
        item.relpath: item.content
        for item in sorted(snapshot.tracked_blobs, key=lambda item: item.relpath)
    }
    assert runtime._canonical_json_bytes(frame) == runtime._canonical_json_bytes(frame)


def test_frame_identity_decoder_rejects_bool_as_integer(tmp_path: Path) -> None:
    _repo, snapshot, _spec, _output = _make_release_repo(tmp_path)
    identity = _independent_identity(snapshot.index_identity)
    for field in ("volume", "size", "mtime_ns", "nlink"):
        forged = dict(identity)
        forged[field] = True
        with pytest.raises(runtime.EvidenceIntegrityError, match="file_identity_invalid"):
            runtime._identity_from_mapping(forged)
    if type(identity["file_id"]) is int:
        forged = dict(identity)
        forged["file_id"] = False
        with pytest.raises(runtime.EvidenceIntegrityError, match="file_identity_invalid"):
            runtime._identity_from_mapping(forged)


def test_isolated_loader_is_constant_and_has_closed_private_protocol() -> None:
    loader = runtime._ISOLATED_CHILD_LOADER
    assert type(loader) is str and loader
    assert "tools.release_evidence_runtime" in loader
    assert "sys.modules['tools.release_evidence_runtime']" in loader
    assert "sys.path" in loader
    assert "os._exit(code)" in loader
    assert "-m" not in loader
    assert "importlib" not in loader
    assert {runtime.PRIVATE_EXIT_PASS, runtime.PRIVATE_EXIT_HOLD, runtime.PRIVATE_EXIT_INTEGRITY} == {
        40,
        41,
        42,
    }


def test_child_receipt_validator_accepts_independent_private_pass_and_integrity(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    prestate = runtime._read_optional_plain(output)
    assert runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    ) == runtime.EXIT_PASS
    nonce = "2" * 32
    frame_sha256 = "sha256:" + "3" * 64
    pass_receipt = _independent_receipt(
        nonce=nonce,
        frame_sha256=frame_sha256,
        private_exit=runtime.PRIVATE_EXIT_PASS,
        status="pass",
        output=output,
    )
    assert runtime._validate_child_receipt(
        _canonical_json(pass_receipt),
        expected_nonce=nonce,
        expected_frame_sha256=frame_sha256,
        private_exit=runtime.PRIVATE_EXIT_PASS,
        snapshot=snapshot,
        spec=spec,
        canonical_prestate=prestate,
    ) == runtime.EXIT_PASS
    integrity_receipt = _independent_receipt(
        nonce=nonce,
        frame_sha256=frame_sha256,
        private_exit=runtime.PRIVATE_EXIT_INTEGRITY,
        status="integrity",
        output=None,
    )
    assert runtime._validate_child_receipt(
        _canonical_json(integrity_receipt),
        expected_nonce=nonce,
        expected_frame_sha256=frame_sha256,
        private_exit=runtime.PRIVATE_EXIT_INTEGRITY,
        snapshot=snapshot,
        spec=spec,
        canonical_prestate=prestate,
    ) == runtime.EXIT_INTEGRITY


def test_child_receipt_validator_rejects_forged_or_raw_process_results(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    prestate = runtime._read_optional_plain(output)
    assert runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    ) == runtime.EXIT_PASS
    nonce = "4" * 32
    frame_sha256 = "sha256:" + "5" * 64
    for mutation in (
        "extra_key",
        "wrong_nonce",
        "wrong_frame",
        "raw_public_exit",
        "bool_private_exit",
        "wrong_status",
        "wrong_output",
        "wrong_digest",
        "bool_identity",
    ):
        receipt = _independent_receipt(
            nonce=nonce,
            frame_sha256=frame_sha256,
            private_exit=runtime.PRIVATE_EXIT_PASS,
            status="pass",
            output=output,
        )
        supplied_exit: object = runtime.PRIVATE_EXIT_PASS
        if mutation == "extra_key":
            receipt["unexpected"] = True
        elif mutation == "wrong_nonce":
            receipt["nonce"] = "6" * 32
        elif mutation == "wrong_frame":
            receipt["frame_sha256"] = "sha256:" + "7" * 64
        elif mutation == "raw_public_exit":
            receipt["private_exit"] = 0
            supplied_exit = 0
        elif mutation == "bool_private_exit":
            receipt["private_exit"] = True
            supplied_exit = True
        elif mutation == "wrong_status":
            receipt["status"] = "hold_nonpass"
        elif mutation == "wrong_output":
            receipt["canonical_output"] = "other.json"
        elif mutation == "wrong_digest":
            receipt["canonical_sha256"] = "sha256:" + "0" * 64
        elif mutation == "bool_identity":
            identity = receipt["canonical_identity"]
            assert isinstance(identity, dict)
            identity["size"] = True
        with pytest.raises(runtime.EvidenceIntegrityError):
            runtime._validate_child_receipt(
                _canonical_json(receipt),
                expected_nonce=nonce,
                expected_frame_sha256=frame_sha256,
                private_exit=supplied_exit,  # type: ignore[arg-type]
                snapshot=snapshot,
                spec=spec,
                canonical_prestate=prestate,
            )


def test_child_receipt_cannot_be_reused_after_same_bytes_are_republished(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    prestate = runtime._read_optional_plain(output)
    assert runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    ) == runtime.EXIT_PASS
    nonce = "8" * 32
    frame_sha256 = "sha256:" + "9" * 64
    first = _independent_receipt(
        nonce=nonce,
        frame_sha256=frame_sha256,
        private_exit=runtime.PRIVATE_EXIT_PASS,
        status="pass",
        output=output,
    )
    first_raw = _canonical_json(first)
    replacement = output.parent / "same-bytes-new-identity.tmp"
    replacement.write_bytes(output.read_bytes())
    os.replace(replacement, output)
    with pytest.raises(runtime.EvidenceIntegrityError, match="receipt_binding"):
        runtime._validate_child_receipt(
            first_raw,
            expected_nonce=nonce,
            expected_frame_sha256=frame_sha256,
            private_exit=runtime.PRIVATE_EXIT_PASS,
            snapshot=snapshot,
            spec=spec,
            canonical_prestate=prestate,
        )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("pass", runtime.EXIT_PASS),
        ("hold_nonpass", runtime.EXIT_HOLD_NONPASS),
    ],
)
def test_real_isolated_constant_loader_private_receipt_and_public_mapping(
    tmp_path: Path,
    status: str,
    expected: int,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source(status),
    )
    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )
    assert type(result) is int and result == expected
    envelope = runtime._validate_completion_bytes(output.read_bytes(), snapshot, spec)
    assert envelope["status"] == status


def test_isolated_child_launch_uses_explicit_bound_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, snapshot, spec, _output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    original_popen = runtime.subprocess.Popen
    captured: list[tuple[object, object]] = []

    def capture_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        captured.append((args[0], kwargs.get("executable")))
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(runtime.subprocess, "Popen", capture_popen)

    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )

    assert result == runtime.EXIT_PASS
    interpreter = runtime._bind_current_interpreter()["path"]
    child_calls = [
        executable
        for command, executable in captured
        if type(command) is list
        and command
        and command[0] == interpreter
        and "-I" in command
    ]
    assert child_calls == [interpreter]


def test_producer_stdout_cannot_contaminate_dedicated_child_receipt(
    tmp_path: Path,
) -> None:
    diagnostic = "producer diagnostic on ordinary stdout; not a receipt"
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source(
            "pass",
            stdout_line=diagnostic,
        ),
    )
    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )
    assert type(result) is int and result == runtime.EXIT_PASS
    envelope = runtime._validate_completion_bytes(output.read_bytes(), snapshot, spec)
    assert envelope["status"] == "pass"


def test_post_child_interpreter_binding_failure_aborts_deferred_commit_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    old_prestate = runtime._read_optional_plain(output)
    assert old_prestate is not None
    original_bind = runtime._bind_current_interpreter
    bind_calls = 0
    observed_committed_child = False

    def fail_only_after_child() -> dict[str, object]:
        nonlocal bind_calls, observed_committed_child
        bind_calls += 1
        if bind_calls == 2:
            candidate = output.read_bytes()
            observed_committed_child = (
                candidate != OLD_BYTES and bool(_reserved_residue(output.parent))
            )
            runtime._validate_completion_bytes(candidate, snapshot, spec)
            raise runtime.EvidenceIntegrityError(
                "python_process_image_unavailable",
                phase="isolation",
            )
        return original_bind()

    monkeypatch.setattr(runtime, "_bind_current_interpreter", fail_only_after_child)

    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )

    assert type(result) is int and result == runtime.EXIT_INTEGRITY
    assert bind_calls == 2
    assert observed_committed_child is True
    assert runtime._read_optional_plain(output) == old_prestate
    assert output.read_bytes() == OLD_BYTES
    assert not _reserved_residue(output.parent)


def test_post_child_binding_failure_with_ambiguous_recovery_fail_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    old_prestate = runtime._read_optional_plain(output)
    assert old_prestate is not None
    original_bind = runtime._bind_current_interpreter
    original_unlink = runtime._dir_unlink_known
    bind_calls = 0
    post_child = False

    def fail_only_after_child() -> dict[str, object]:
        nonlocal bind_calls, post_child
        bind_calls += 1
        if bind_calls == 2:
            runtime._validate_completion_bytes(output.read_bytes(), snapshot, spec)
            assert _reserved_residue(output.parent)
            post_child = True
            raise runtime.EvidenceIntegrityError(
                "python_process_image_unavailable",
                phase="isolation",
            )
        return original_bind()

    def fail_abort_cleanup(
        parent_lease: object,
        name: str,
        content: bytes,
    ) -> None:
        if post_child and name.startswith(".wdre."):
            raise runtime.EvidenceIntegrityError(
                "injected_abort_cleanup_ambiguity",
                phase="durability",
            )
        original_unlink(parent_lease, name, content)

    monkeypatch.setattr(runtime, "_bind_current_interpreter", fail_only_after_child)
    monkeypatch.setattr(runtime, "_dir_unlink_known", fail_abort_cleanup)

    with pytest.raises(runtime.EvidenceFailStop):
        runtime.ensure_isolated_once(
            snapshot=snapshot,
            executing_file=repo / PRODUCER_RELPATH,
            producer_relpath=PRODUCER_RELPATH,
            validated_argv=(),
            bootstrap_spec=spec,
        )

    assert bind_calls == 2
    assert post_child is True
    assert runtime._read_optional_plain(output) == old_prestate
    assert _reserved_residue(output.parent)


@pytest.mark.parametrize("raw_exit", [0, 1, None])
def test_real_child_raw_conventional_exit_or_crash_can_never_be_completion(
    tmp_path: Path,
    raw_exit: int | None,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_exit_producer_source(raw_exit),
    )
    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )
    assert type(result) is int and result == runtime.EXIT_INTEGRITY
    assert output.read_bytes() == OLD_BYTES
    assert not _reserved_residue(output.parent)
    envelope = _independent_envelope(snapshot, spec, status="hold_nonpass")
    findings = envelope["findings"]
    assert isinstance(findings, list) and isinstance(findings[0], dict)
    findings[0]["details"] = {}
    with pytest.raises(runtime.EvidenceIntegrityError):
        runtime._validate_completion_bytes(_canonical_json(envelope), snapshot, spec)


def test_completion_matrix_accepts_only_exact_pass_or_complete_hold(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    assert runtime.build_completion_envelope(
        snapshot=snapshot,
        spec=spec,
        outcome=_pass_outcome(),
    )["status"] == "pass"
    assert runtime.build_completion_envelope(
        snapshot=snapshot,
        spec=spec,
        outcome=_hold_outcome("axis_b_not_pass"),
    )["status"] == "hold_nonpass"
    invalid = [
        runtime.ProducerOutcome("unknown", (), {"complete": True}, ()),
        runtime.ProducerOutcome("pass", (), {}, ()),
        runtime.ProducerOutcome("pass", ("axis_b_not_pass",), {"complete": True}, ()),
        runtime.ProducerOutcome("hold_nonpass", (), None, ()),
        runtime.ProducerOutcome(
            "hold_nonpass",
            ("axis_b_not_pass",),
            None,
            ({"reason_code": "axis_b_not_pass", "details": {}},),
        ),
        runtime.ProducerOutcome(
            "hold_nonpass",
            ("axis_b_not_pass",),
            None,
            (
                {"reason_code": "axis_b_not_pass", "details": {"one": True}},
                {"reason_code": "axis_b_not_pass", "details": {"two": True}},
            ),
        ),
        runtime.ProducerOutcome(
            "hold_nonpass",
            ("axis_b_not_pass",),
            None,
            (
                {
                    "reason_code": "axis_b_quality_below_floor",
                    "details": {"complete": True},
                },
            ),
        ),
        runtime.ProducerOutcome(
            "hold_nonpass",
            ("axis_b_not_pass", "not_reviewed"),
            None,
            (
                {"reason_code": "axis_b_not_pass", "details": {"complete": True}},
                {"reason_code": "not_reviewed", "details": {"complete": True}},
            ),
        ),
    ]
    for outcome in invalid:
        with pytest.raises(runtime.EvidenceIntegrityError):
            runtime.build_completion_envelope(
                snapshot=snapshot,
                spec=spec,
                outcome=outcome,
            )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_evidence_is_integrity_not_hold(
    tmp_path: Path,
    value: float,
) -> None:
    _repo, snapshot, spec, _output = _make_release_repo(tmp_path)
    outcome = runtime.ProducerOutcome("pass", (), {"metric": value}, ())
    with pytest.raises(runtime.EvidenceIntegrityError, match="nonfinite"):
        runtime.build_completion_envelope(snapshot=snapshot, spec=spec, outcome=outcome)


@pytest.mark.parametrize(
    ("with_output", "outcome", "expected"),
    [
        (True, "pass", runtime.EXIT_PASS),
        (False, "pass", runtime.EXIT_PASS),
        (True, "hold", runtime.EXIT_HOLD_NONPASS),
        (False, "hold", runtime.EXIT_HOLD_NONPASS),
    ],
)
def test_isolated_publication_existing_or_absent_commits_exact_completion(
    tmp_path: Path,
    with_output: bool,
    outcome: str,
    expected: int,
) -> None:
    status = "pass" if outcome == "pass" else "hold_nonpass"
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source(status),
        with_output=with_output,
    )
    result = runtime.ensure_isolated_once(
        snapshot=snapshot,
        executing_file=repo / PRODUCER_RELPATH,
        producer_relpath=PRODUCER_RELPATH,
        validated_argv=(),
        bootstrap_spec=spec,
    )
    assert type(result) is int and result == expected
    validated = runtime._validate_completion_bytes(output.read_bytes(), snapshot, spec)
    assert validated["status"] == status


def test_direct_nonisolated_publication_is_not_an_exported_api() -> None:
    assert "publish_completion" not in runtime.__all__
    assert not hasattr(runtime, "publish_completion")


def test_isolated_publication_rejects_hardlinked_target_without_mutating_other_name(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    outside = tmp_path / "outside.json"
    output.unlink()
    outside.write_bytes(OLD_BYTES)
    os.link(outside, output)
    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^transaction_file_not_plain$",
    ) as caught:
        runtime.ensure_isolated_once(
            snapshot=snapshot,
            executing_file=repo / PRODUCER_RELPATH,
            producer_relpath=PRODUCER_RELPATH,
            validated_argv=(),
            bootstrap_spec=spec,
        )
    assert caught.value.phase == "durability"
    assert outside.read_bytes() == OLD_BYTES
    assert output.read_bytes() == OLD_BYTES
    assert not _reserved_residue(output.parent)


def test_isolated_publication_rejects_dangling_target_without_following_it(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    output.unlink()
    outside = tmp_path / "does-not-exist.json"
    try:
        output.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    with pytest.raises(
        runtime.EvidenceIntegrityError,
        match="^transaction_file_not_plain$",
    ) as caught:
        runtime.ensure_isolated_once(
            snapshot=snapshot,
            executing_file=repo / PRODUCER_RELPATH,
            producer_relpath=PRODUCER_RELPATH,
            validated_argv=(),
            bootstrap_spec=spec,
        )
    assert caught.value.phase == "durability"
    assert output.is_symlink()
    assert not outside.exists()
    assert not _reserved_residue(output.parent)


@pytest.mark.parametrize(
    ("reason", "phase", "remapped"),
    [
        ("file_not_plain_single_link", "path", True),
        ("plain_file_open_failed", "path", False),
    ],
)
def test_windows_transaction_reader_remaps_only_not_plain_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reason: str,
    phase: str,
    remapped: bool,
) -> None:
    source = runtime.EvidenceIntegrityError(
        reason,
        phase=phase,
        recovery_paths=("opaque",),
    )

    class Lease:
        path = tmp_path

        @staticmethod
        def revalidate() -> None:
            return None

    monkeypatch.setattr(runtime.os, "name", "nt")
    monkeypatch.setattr(runtime, "_dir_lstat", lambda *_args: os.stat_result((0,) * 10))

    def fail_read(*_args: object, **_kwargs: object) -> tuple[bytes, runtime.FileIdentity]:
        raise source

    monkeypatch.setattr(runtime, "_read_plain_file", fail_read)

    with pytest.raises(runtime.EvidenceIntegrityError) as caught:
        runtime._dir_read_plain(Lease(), "target.json")

    if remapped:
        assert caught.value is not source
        assert caught.value.reason_code == "transaction_file_not_plain"
        assert caught.value.phase == "durability"
        assert caught.value.recovery_paths == ("opaque",)
        assert caught.value.__cause__ is source
    else:
        assert caught.value is source
        assert caught.value.reason_code == reason
        assert caught.value.phase == phase


def test_isolated_publication_index_lock_blocks_before_transaction_residue(
    tmp_path: Path,
) -> None:
    repo, snapshot, spec, output = _make_release_repo(
        tmp_path,
        producer_bytes=_frozen_producer_source("pass"),
    )
    snapshot.index_lock_path.write_bytes(b"busy")
    with pytest.raises(runtime.EvidenceFailStop):
        runtime.ensure_isolated_once(
            snapshot=snapshot,
            executing_file=repo / PRODUCER_RELPATH,
            producer_relpath=PRODUCER_RELPATH,
            validated_argv=(),
            bootstrap_spec=spec,
        )
    assert output.read_bytes() == OLD_BYTES
    assert not _reserved_residue(output.parent)

def test_cas_existing_atomically_captures_actual_old_without_copy(tmp_path: Path) -> None:
    parent = tmp_path / "cas"
    parent.mkdir()
    target_name = "canonical.json"
    candidate_name = ".wdre-candidate.json"
    backup_name = ".wdre-backup.json"
    old = b"old\n"
    new = b"new\n"
    with runtime._open_directory_leases(parent) as leases:
        lease = leases[-1]
        old_identity = runtime._dir_write_exclusive(lease, target_name, old)
        candidate_identity = runtime._dir_write_exclusive(lease, candidate_name, new)
        runtime._cas_exchange_existing(
            lease,
            target_name,
            candidate_name,
            backup_name,
            expected_target=(old, old_identity),
            expected_candidate=(new, candidate_identity),
        )
        promoted = runtime._dir_read_plain(lease, target_name)
        displaced = runtime._dir_read_plain(lease, backup_name)
        assert promoted == (new, candidate_identity)
        assert displaced == (old, old_identity)
        assert runtime._dir_read_plain(lease, candidate_name) is None


def test_cas_absent_is_atomic_noreplace_and_preserves_candidate_identity(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "cas"
    parent.mkdir()
    target_name = "canonical.json"
    candidate_name = ".wdre-candidate.json"
    new = b"new\n"
    with runtime._open_directory_leases(parent) as leases:
        lease = leases[-1]
        candidate_identity = runtime._dir_write_exclusive(lease, candidate_name, new)
        runtime._cas_promote_absent(
            lease,
            target_name,
            candidate_name,
            expected_candidate=(new, candidate_identity),
        )
        assert runtime._dir_read_plain(lease, target_name) == (new, candidate_identity)
        assert runtime._dir_read_plain(lease, candidate_name) is None


def test_cas_absent_refuses_foreign_target_without_overwrite(tmp_path: Path) -> None:
    parent = tmp_path / "cas"
    parent.mkdir()
    target_name = "canonical.json"
    candidate_name = ".wdre-candidate.json"
    with runtime._open_directory_leases(parent) as leases:
        lease = leases[-1]
        candidate_identity = runtime._dir_write_exclusive(lease, candidate_name, b"new\n")
        foreign_identity = runtime._dir_write_exclusive(lease, target_name, b"foreign\n")
        with pytest.raises(
            runtime.EvidenceIntegrityError,
            match="^candidate_binding_invalid$",
        ):
            runtime._cas_promote_absent(
                lease,
                target_name,
                candidate_name,
                expected_candidate=(b"new\n", candidate_identity),
            )
        assert runtime._dir_read_plain(lease, target_name) == (b"foreign\n", foreign_identity)
        assert runtime._dir_read_plain(lease, candidate_name) == (
            b"new\n",
            candidate_identity,
        )


def test_cas_rechecks_candidate_link_count_at_the_promotion_boundary(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "cas"
    parent.mkdir()
    target_name = "canonical.json"
    candidate_name = ".wdre-candidate.json"
    backup_name = ".wdre-backup.json"
    outside = tmp_path / "candidate-outside-link"
    with runtime._open_directory_leases(parent) as leases:
        lease = leases[-1]
        old_identity = runtime._dir_write_exclusive(lease, target_name, b"old\n")
        candidate_identity = runtime._dir_write_exclusive(
            lease,
            candidate_name,
            b"new\n",
        )
        os.link(parent / candidate_name, outside)
        with pytest.raises(runtime.EvidenceIntegrityError, match="candidate"):
            runtime._cas_exchange_existing(
                lease,
                target_name,
                candidate_name,
                backup_name,
                expected_target=(b"old\n", old_identity),
                expected_candidate=(b"new\n", candidate_identity),
            )
        assert runtime._dir_read_plain(lease, target_name) == (b"old\n", old_identity)
        linked = runtime._dir_read_plain(
            lease,
            candidate_name,
            require_single_link=False,
        )
        assert linked is not None and linked[0] == b"new\n" and linked[1].nlink == 2
        assert outside.read_bytes() == b"new\n"


def test_cas_never_promotes_candidate_swapped_after_expected_binding(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "cas"
    parent.mkdir()
    target_name = "canonical.json"
    candidate_name = ".wdre-candidate.json"
    backup_name = ".wdre-backup.json"
    with runtime._open_directory_leases(parent) as leases:
        lease = leases[-1]
        target_identity = runtime._dir_write_exclusive(lease, target_name, b"old\n")
        candidate_identity = runtime._dir_write_exclusive(
            lease,
            candidate_name,
            b"new\n",
        )
        replacement = parent / "foreign-candidate.tmp"
        replacement.write_bytes(b"foreign candidate after caller check\n")
        os.replace(replacement, parent / candidate_name)
        with pytest.raises(runtime.EvidenceIntegrityError, match="candidate_binding"):
            runtime._cas_exchange_existing(
                lease,
                target_name,
                candidate_name,
                backup_name,
                expected_target=(b"old\n", target_identity),
                expected_candidate=(b"new\n", candidate_identity),
            )
        assert runtime._dir_read_plain(lease, target_name) == (
            b"old\n",
            target_identity,
        )
        assert runtime._dir_read_plain(lease, backup_name) is None
        assert runtime._dir_read_plain(lease, candidate_name)[0] == (
            b"foreign candidate after caller check\n"
        )


def test_path_resolver_blocks_unknown_residue_from_any_prior_nonce_without_mutation(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate = runtime._read_optional_plain(output)
    stale_names = runtime._transaction_names(output.name, "a" * 32)
    stale = output.parent / stale_names["candidate"]
    stale.write_bytes(b"unknown prior process residue")
    before = {path.name: path.read_bytes() for path in _reserved_residue(output.parent)}
    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    )
    assert state == "blocked"
    assert output.read_bytes() == OLD_BYTES
    assert {path.name: path.read_bytes() for path in _reserved_residue(output.parent)} == before


def test_path_resolver_aborts_exact_prepared_prior_nonce_and_cleans_all_residue(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, _names, _new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="b" * 32,
        promote=False,
    )
    assert _reserved_residue(output.parent)
    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    )
    assert state == "aborted"
    assert runtime._read_optional_plain(output) == prestate
    assert not _reserved_residue(output.parent)


@pytest.mark.parametrize("prefer_abort", [False, True])
def test_path_resolver_recovers_exact_promoted_prior_nonce(
    tmp_path: Path,
    prefer_abort: bool,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, _names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="c" * 32,
        promote=True,
    )
    assert output.read_bytes() == new_content
    assert _reserved_residue(output.parent)
    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=prefer_abort,
    )
    assert state == ("aborted" if prefer_abort else "committed")
    if prefer_abort:
        assert runtime._read_optional_plain(output) == prestate
    else:
        assert output.read_bytes() == new_content
        runtime._validate_completion_bytes(new_content, snapshot, spec)
    assert not _reserved_residue(output.parent)


def test_path_resolver_blocks_foreign_race_without_restoring_unbound_backup(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="d" * 32,
        promote=False,
    )
    foreign = b"foreign concurrent writer\n"
    with runtime._open_directory_leases(output.parent) as leases:
        lease = leases[-1]
        runtime._dir_unlink_known(lease, output.name, OLD_BYTES)
        foreign_identity = runtime._dir_write_exclusive(lease, output.name, foreign)
        candidate = runtime._dir_read_plain(lease, names["candidate"])
        assert candidate is not None
        runtime._cas_exchange_existing(
            lease,
            output.name,
            names["candidate"],
            names["backup"],
            expected_target=(foreign, foreign_identity),
            expected_candidate=candidate,
        )
        lease.flush()
    assert output.read_bytes() == new_content
    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    )
    assert state == "blocked"
    assert output.read_bytes() == new_content
    assert runtime._read_optional_plain(output) != prestate
    assert _reserved_residue(output.parent)


def test_path_resolver_rejects_swapped_backup_before_restore(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="1" * 32,
        promote=True,
    )
    assert prestate is not None
    backup = output.parent / names["backup"]
    assert backup.read_bytes() == OLD_BYTES
    replacement = output.parent / "foreign-backup.tmp"
    foreign = b"foreign backup swapped before restore\n"
    replacement.write_bytes(foreign)
    os.replace(replacement, backup)

    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    )

    assert state == "blocked"
    assert output.read_bytes() == new_content
    assert backup.read_bytes() == foreign
    assert (output.parent / names["descriptor"]).exists()


def test_path_resolver_blocks_hardlinked_candidate_and_never_touches_other_link(
    tmp_path: Path,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="e" * 32,
        promote=False,
    )
    outside = tmp_path / "candidate-outside-link"
    os.link(output.parent / names["candidate"], outside)
    state = runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    )
    assert state == "blocked"
    assert output.read_bytes() == OLD_BYTES
    assert outside.read_bytes() == new_content
    assert _reserved_residue(output.parent)


def test_postcommit_cleanup_fault_stays_committed_and_residue_is_recoverable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="f" * 32,
        promote=True,
    )
    original = runtime._dir_unlink_known

    def fail_cleanup(
        parent_lease: object,
        name: str,
        content: bytes,
    ) -> None:
        if name in {names["candidate"], names["backup"], names["descriptor"]}:
            raise runtime.EvidenceIntegrityError(
                "injected_cleanup_failure",
                phase="durability",
            )
        original(parent_lease, name, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "_dir_unlink_known", fail_cleanup)
        state = runtime._resolve_path_transaction_step(
            target=output,
            snapshot=snapshot,
            spec=spec,
            expected_prestate=prestate,
            prefer_abort=False,
        )
    assert state == "committed"
    assert output.read_bytes() == new_content
    assert _reserved_residue(output.parent)
    assert runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=False,
    ) == "committed"
    assert output.read_bytes() == new_content
    assert not _reserved_residue(output.parent)


def test_postcommit_backup_deleted_then_descriptor_unlink_fault_blocks_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="2" * 32,
        promote=True,
    )
    original = runtime._dir_unlink_known
    backup_deleted = False

    def delete_backup_then_fail_descriptor(
        parent_lease: object,
        name: str,
        content: bytes,
    ) -> None:
        nonlocal backup_deleted
        if name == names["backup"]:
            original(parent_lease, name, content)
            backup_deleted = True
            return
        if name == names["descriptor"] and backup_deleted:
            raise runtime.EvidenceIntegrityError(
                "injected_descriptor_unlink_failure",
                phase="durability",
            )
        original(parent_lease, name, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(
            runtime,
            "_dir_unlink_known",
            delete_backup_then_fail_descriptor,
        )
        state = runtime._resolve_path_transaction_step(
            target=output,
            snapshot=snapshot,
            spec=spec,
            expected_prestate=prestate,
            prefer_abort=False,
        )

    assert backup_deleted is True
    assert state == "blocked"
    assert output.read_bytes() == new_content
    assert not (output.parent / names["backup"]).exists()
    assert (output.parent / names["descriptor"]).exists()
    assert runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=False,
    ) == "blocked"
    assert output.read_bytes() == new_content
    assert _reserved_residue(output.parent) == [
        output.parent / names["descriptor"]
    ]


def test_precommit_cleanup_fault_blocks_until_exact_abort_is_proven(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate, names, _new_content = _prepare_transaction(
        snapshot=snapshot,
        spec=spec,
        output=output,
        outcome=_pass_outcome(),
        nonce="0" * 32,
        promote=False,
    )
    original = runtime._dir_unlink_known

    def fail_candidate_cleanup(
        parent_lease: object,
        name: str,
        content: bytes,
    ) -> None:
        if name == names["candidate"]:
            raise runtime.EvidenceIntegrityError(
                "injected_precommit_cleanup_failure",
                phase="durability",
            )
        original(parent_lease, name, content)

    with monkeypatch.context() as scoped:
        scoped.setattr(runtime, "_dir_unlink_known", fail_candidate_cleanup)
        state = runtime._resolve_path_transaction_step(
            target=output,
            snapshot=snapshot,
            spec=spec,
            expected_prestate=prestate,
            prefer_abort=True,
        )
    assert state == "blocked"
    assert runtime._read_optional_plain(output) == prestate
    assert _reserved_residue(output.parent)
    assert runtime._resolve_path_transaction_step(
        target=output,
        snapshot=snapshot,
        spec=spec,
        expected_prestate=prestate,
        prefer_abort=True,
    ) == "aborted"
    assert runtime._read_optional_plain(output) == prestate
    assert not _reserved_residue(output.parent)


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor contract")
def test_posix_directory_lease_identity_ignores_own_entry_mtime_changes(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "a" / "b"
    parent.mkdir(parents=True)
    with runtime._open_directory_leases(parent) as leases:
        parent_lease = leases[-1]
        descriptor = os.open(
            "candidate",
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=parent_lease.descriptor,
        )
        os.close(descriptor)
        parent_lease.revalidate()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor contract")
def test_posix_lease_detects_ancestor_name_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "a" / "b"
    parent.mkdir(parents=True)
    with pytest.raises(runtime.EvidenceIntegrityError, match="directory_lease"):
        with runtime._open_directory_leases(parent) as leases:
            moved = tmp_path / "a-moved"
            (tmp_path / "a").rename(moved)
            (tmp_path / "a" / "b").mkdir(parents=True)
            leases[-1].revalidate()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory-descriptor contract")
def test_posix_held_resolver_never_reopens_canonical_by_absolute_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _repo, snapshot, spec, output = _make_release_repo(tmp_path)
    prestate = runtime._read_optional_plain(output)

    def forbidden_absolute_read(_path: Path) -> object:
        raise AssertionError("held resolver reopened an absolute path")

    with runtime._open_directory_leases(output.parent) as leases:
        with monkeypatch.context() as scoped:
            scoped.setattr(runtime, "_read_optional_plain", forbidden_absolute_read)
            state = runtime._resolve_path_transaction_held(
                parent_lease=leases[-1],
                target=output,
                snapshot=snapshot,
                spec=spec,
                expected_prestate=prestate,
                prefer_abort=True,
            )
    assert state == "aborted"


def test_producer_spec_is_not_mutable_or_open_ended() -> None:
    spec = _producer_spec()
    with pytest.raises((AttributeError, TypeError)):
        spec.producer_id = "other"  # type: ignore[misc]
    with pytest.raises(ValueError):
        replace(spec, canonical_output_relpath="docs/runs/arbitrary.json")

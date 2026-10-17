"""Synthetic transport tests, NOT a producer/JOIN/production acceptance run."""
import base64
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import types

import pytest


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def driver():
    spec = importlib.util.spec_from_file_location("s1_transport_under_test",
                                                 Path(__file__).with_name("mind_chain_driver.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def finished(tmp_path):
    audit = tmp_path / ".codex-audit"
    live = audit / "live"
    cycle = "20261017T080000Z"
    folder = live / "cycles" / cycle
    (folder / "run").mkdir(parents=True)
    cursor_object = {"file_identity": "synthetic-file-1", "generation": "synthetic-log-1", "offset": 123}
    cursor = "bc1." + base64.urlsafe_b64encode(encoded(cursor_object)).decode().rstrip("=")
    # Deliberately only a transport-shaped snapshot, NOT core semantic evidence.
    snapshot = {"authority": "none", "scope": "bounded_projection_not_gate",
                "source": {"as_of_utc": "2026-10-17T08:00:00Z"}}
    state = {"schema": "wd.bridge-mind-producer-state.v1", "generation": 1,
             "cursor": cursor, "source": {"file_identity": "synthetic-file-1", "log_generation": "synthetic-log-1"},
             "window": [], "window_sha256": digest(b"")}
    payloads = {
        "capture.json": encoded({"events": []}),
        "state.json": encoded(state),
        "detail-manifest.txt": b"synthetic-only\n",
        "run/reflected.publication.json": encoded({"schema": "wd.bridge-mind-publication.v1",
                                                   "snapshot": snapshot, "snapshot_sha256": digest(encoded(snapshot))}),
        "run/board.txt": b"UNKNOWN\n",
        "run/receipt.json": encoded({"publication": {"snapshot_sha256": digest(encoded(snapshot))}}),
    }
    for name, raw in payloads.items():
        (folder / name).write_bytes(raw)
    last_ok = {"cycle_dir": cycle, "generation": 1, "observed_utc": "2026-10-17T08:00:00Z",
               "capture_file": f"cycles/{cycle}/capture.json", "capture_sha256": digest(payloads["capture.json"]),
               "state_file": f"cycles/{cycle}/state.json", "state_sha256": digest(payloads["state.json"]),
               "context_file": None, "context_sha256": None,
               "publication_file": f"cycles/{cycle}/run/reflected.publication.json",
               "board_file": f"cycles/{cycle}/run/board.txt", "receipt_file": f"cycles/{cycle}/run/receipt.json",
               "snapshot_sha256": digest(encoded(snapshot)), "board_text_sha256": digest(payloads["run/board.txt"]),
               "reader": "synthetic-compact-reader", "counts": {}, "global_question": "UNKNOWN", "measures": {}}
    latest = {"schema": "wd.bridge-mind-shadow-latest.v2", "authority": "none",
              "scope": "shadow_observation_not_gate", "committed_utc": "2026-10-17T08:00:00Z",
              "last_ok": last_ok, "verify": "synthetic-only", "report_to": {"type": "message", "task_id": "synthetic"},
              "advisory": "observation only"}
    (live / "latest.json").write_bytes(encoded(latest))
    return {"audit": audit, "live": live, "out": audit / "frozen", "cycle": cycle,
            "folder": folder, "latest": latest, "payloads": payloads}


def put_latest(case, latest):
    (case["live"] / "latest.json").write_bytes(encoded(latest))


def test_freeze_preserves_bytes_cursor_identity_and_unknown(driver, finished):
    result = driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert result["authority"] == "none"
    assert result["scope"] == "transport_seals_not_join_validation"
    assert result["join_validated"] is False
    assert result["source"]["file_identity"] == "synthetic-file-1"
    assert result["source"]["log_generation"] == "synthetic-log-1"
    assert result["producer_generation"] == 1
    assert result["cursor"].startswith("bc1.")
    manifest = json.loads((finished["out"] / "manifest.json").read_bytes())
    assert manifest["schema"] == "wd.mind-frozen-cycles.v1"
    entry, = manifest["cycles"]
    assert entry["cycle"] == finished["cycle"]
    assert all(entry["files"][name] is None for name in
               ("context.json", "observed-status.json", "gh-pr-list.json", "gh-open-status.json"))
    for name, raw in finished["payloads"].items():
        assert (finished["out"] / finished["cycle"] / name).read_bytes() == raw
    assert (finished["live"] / "latest.json").read_bytes() == encoded(finished["latest"])


@pytest.mark.parametrize("change,reason", [
    ("v1", "latest_schema"), ("authority", "authority"), ("escape", "cycle_name"),
    ("path", "cycle_path"), ("context_pair", "context_binding"),
    ("capture_digest", "capture_digest"), ("snapshot_digest", "snapshot_digest"),
    ("state_digest", "state_digest"), ("generation", "state_generation"),
    ("cursor", "cursor"), ("identity", "source_identity"),
])
def test_refuses_inconsistent_commit_before_output(driver, finished, change, reason):
    latest = copy.deepcopy(finished["latest"])
    ok = latest["last_ok"]
    if change == "v1": latest["schema"] = "wd.bridge-mind-shadow-latest.v1"
    elif change == "authority": latest["authority"] = "gate"
    elif change == "escape": ok["cycle_dir"] = "../outside"
    elif change == "path": ok["capture_file"] = "../outside.json"
    elif change == "context_pair": ok["context_sha256"] = "a" * 64
    elif change == "capture_digest": ok["capture_sha256"] = "a" * 64
    elif change == "snapshot_digest": ok["snapshot_sha256"] = "a" * 64
    elif change == "state_digest": ok["state_sha256"] = "a" * 64
    else:
        state = json.loads(finished["payloads"]["state.json"])
        if change == "generation": state["generation"] = True
        elif change == "cursor": state["cursor"] = "bc1.not-json"
        elif change == "identity": state["source"]["file_identity"] = "another-file"
        raw = encoded(state)
        (finished["folder"] / "state.json").write_bytes(raw)
        ok["state_sha256"] = digest(raw)
    put_latest(finished, latest)
    with pytest.raises(driver.ChainRefusal, match="^" + reason + "$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()


def test_refuses_changed_pointer_without_publishing_or_deleting_source(driver, finished, monkeypatch):
    original = driver._read
    count = 0
    def race(path):
        nonlocal count
        if path.name == "latest.json":
            count += 1
            if count == 2:
                changed = copy.deepcopy(finished["latest"])
                changed["committed_utc"] = "2026-10-17T08:00:01Z"
                return encoded(changed)
        return original(path)
    monkeypatch.setattr(driver, "_read", race)
    with pytest.raises(driver.ChainRefusal, match="^source_changed$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()
    assert (finished["folder"] / "capture.json").exists()


def test_refuses_duplicate_json_keys(driver, finished):
    (finished["live"] / "latest.json").write_bytes(b'{"schema":1,"schema":2}')
    with pytest.raises(driver.ChainRefusal, match="^json_invalid$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()


def test_refuses_existing_output_and_keeps_it(driver, finished):
    finished["out"].mkdir()
    marker = finished["out"] / "owner.txt"
    marker.write_bytes(b"untouched")
    with pytest.raises(driver.ChainRefusal, match="^output_exists$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert marker.read_bytes() == b"untouched"


def test_refuses_outside_audit_and_nested_live(driver, finished, tmp_path):
    # tmp_path itself may already be below the runner's audit root. Test root
    # admission directly so this negative case can never write outside it.
    with pytest.raises(driver.ChainRefusal, match="^audit_root$"):
        driver._root(Path(tmp_path.anchor) / "synthetic-not-an-audit-root", exists=False)
    target = finished["live"] / "nested"
    with pytest.raises(driver.ChainRefusal, match="^root_overlap$"):
        driver.freeze_committed_cycle(finished["live"], target)
    assert not target.exists()


def test_reparse_flag_is_refused_without_link_privilege(driver, finished, monkeypatch):
    original = driver._is_reparse
    monkeypatch.setattr(driver, "_is_reparse", lambda path: path.name == "capture.json" or original(path))
    with pytest.raises(driver.ChainRefusal, match="^reparse_path$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()


def test_privacy_guard_refuses_before_any_output(driver, finished):
    raw = encoded({"events": [{"text": "PRIVATE" + "_MARKER"}]})
    (finished["folder"] / "capture.json").write_bytes(raw)
    latest = copy.deepcopy(finished["latest"])
    latest["last_ok"]["capture_sha256"] = digest(raw)
    put_latest(finished, latest)
    with pytest.raises(driver.ChainRefusal, match="^privacy_guard$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()


def test_unknown_github_files_stay_null_even_with_context(driver, finished):
    raw = encoded({"observed_utc": "2026-10-17T08:00:00Z", "prs": {}})
    (finished["folder"] / "context.json").write_bytes(raw)
    latest = copy.deepcopy(finished["latest"])
    latest["last_ok"].update(context_file=f"cycles/{finished['cycle']}/context.json", context_sha256=digest(raw))
    put_latest(finished, latest)
    driver.freeze_committed_cycle(finished["live"], finished["out"])
    manifest = json.loads((finished["out"] / "manifest.json").read_bytes())
    files = manifest["cycles"][0]["files"]
    assert files["context.json"] == digest(raw)
    assert all(files[name] is None for name in ("observed-status.json", "gh-pr-list.json", "gh-open-status.json"))


def test_byte_bound_refuses_before_output(driver, finished, monkeypatch):
    monkeypatch.setattr(driver, "MAX_FILE_BYTES", 32)
    with pytest.raises(driver.ChainRefusal, match="^file_bound$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()


def test_nonfinite_numeric_literal_is_fixed_refusal(driver):
    with pytest.raises(driver.ChainRefusal, match="^json_invalid$"):
        driver._json(b'{"overflow": 1e999}')


def test_write_failure_leaves_no_published_freeze(driver, finished, monkeypatch):
    def refuse_sync(_):
        raise PermissionError("synthetic, not a native sharing-window claim")
    monkeypatch.setattr(driver.os, "fsync", refuse_sync)
    with pytest.raises(driver.ChainRefusal, match="^output_unavailable$"):
        driver.freeze_committed_cycle(finished["live"], finished["out"])
    assert not finished["out"].exists()
    assert not list(finished["audit"].glob(".frozen.staging-*"))


def test_executable_mutant_skipping_source_digest_is_killed(driver, finished):
    """MUTANT_SKIP_SOURCE_DIGEST executes the changed implementation, not a label."""
    source = Path(driver.__file__).read_text(encoding="utf-8")
    anchor = "if name in files and _sha(files[name]) != ok.get(key):"
    assert source.count(anchor) == 1
    mutant = types.ModuleType("s1_skip_digest_mutant")
    exec(compile(source.replace(anchor, "if False:"), "<MUTANT_SKIP_SOURCE_DIGEST>", "exec"), mutant.__dict__)
    latest = copy.deepcopy(finished["latest"])
    latest["last_ok"]["capture_sha256"] = "a" * 64
    put_latest(finished, latest)
    def contract(module, target):
        refused = False
        try:
            module.freeze_committed_cycle(finished["live"], target)
        except module.ChainRefusal as error:
            refused = str(error) == "capture_digest"
        assert refused, "MUTANT_SKIP_SOURCE_DIGEST survived"
    contract(driver, finished["audit"] / "control")
    with pytest.raises(AssertionError, match="MUTANT_SKIP_SOURCE_DIGEST survived"):
        contract(mutant, finished["audit"] / "mutant")

"""Pure synthetic recovery evidence; does not read or modify live paths."""
import copy
import hashlib
import json

import pytest


OLD = "a" * 40
NEW = "b" * 40
NEW_MANIFEST = "C" * 64
STORE = r"C:\synthetic\old-store"
OLD_BUNDLE = STORE + "\\" + OLD
MACHINE = r"C:\synthetic\machine"
NAMES = ["start-wd-all.ps1", "new-only.ps1", "WD_REBOOT_STATE_CURRENT.json"]


def encoded(obj):
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest().upper()


def example():
    manifest = encoded({"source_commit": OLD, "files": {"launcher.ps1": "D" * 64}})
    state = encoded({"schema_version": 1, "source_commit": OLD, "active_bundle": OLD_BUNDLE,
                     "manifest_sha256": sha(manifest), "final_commit": OLD,
                     "final_manifest_sha256": sha(manifest)})
    files = {"start-wd-all.ps1": b"previous wrapper", "WD_REBOOT_STATE_CURRENT.json": state}
    inventory = {
        "schema": "wd.reboot-backup-inventory.v1", "new_commit": NEW,
        "new_manifest_sha256": NEW_MANIFEST, "machine_root": MACHINE,
        "prior_state": {"state_sha256": sha(state), "source_commit": OLD, "bundle_store": STORE,
                        "active_bundle": OLD_BUNDLE, "manifest_sha256": sha(manifest)},
        "files": [{"name": name, "present": name in files,
                   "sha256": sha(files[name]) if name in files else None} for name in NAMES],
    }
    inventory_bytes = encoded(inventory)
    return dict(inventory_bytes=inventory_bytes, expected_inventory_sha256=sha(inventory_bytes),
                expected_new_commit=NEW, expected_new_manifest_sha256=NEW_MANIFEST,
                expected_previous_commit=OLD, expected_previous_manifest_sha256=sha(manifest),
                expected_names=NAMES, backup_bytes=files, prior_manifest_bytes=manifest)


def assess(**kwargs):
    from tools.wd_reboot_restore_inventory_assess import assess_inventory
    return assess_inventory(**kwargs)


def change_inventory(inputs, callback):
    doc = json.loads(inputs["inventory_bytes"])
    callback(doc)
    inputs["inventory_bytes"] = encoded(doc)
    inputs["expected_inventory_sha256"] = sha(inputs["inventory_bytes"])


def test_real_schema_synthetic_success_is_evidence_only():
    inputs = example()
    before = copy.deepcopy(inputs)
    result = assess(**inputs)
    assert result["status"] == "BACKUP_CONSISTENT"
    assert result["authority_effect"] == "none"
    assert result["restore_performed"] is False
    assert result["prior_bundle_files_verified"] is False
    assert result["reparse_points_checked"] is False
    assert result["process_liveness_verified"] is False
    assert result["prior_wrapper_state_compatibility_verified"] is False
    assert result["transaction_file_set_verified"] is False
    assert result["state_final_pair_consistent"] is True
    assert inputs == before


@pytest.mark.parametrize("damage", ["inventory_pin", "missing_backup", "changed_backup", "extra_backup",
                                    "previous_manifest", "manifest_commit", "state_pin", "state_commit",
                                    "new_commit", "new_manifest", "old_commit", "old_manifest", "no_prior",
                                    "state_path_escape", "wrong_store", "missing_name", "duplicate_name",
                                    "bad_name", "present_not_bool", "absent_with_hash", "duplicate_json"])
def test_uncertain_or_inconsistent_evidence_never_authorizes(damage):
    i = example()
    if damage == "inventory_pin": i["expected_inventory_sha256"] = "0" * 64
    elif damage == "missing_backup": del i["backup_bytes"]["start-wd-all.ps1"]
    elif damage == "changed_backup": i["backup_bytes"]["start-wd-all.ps1"] = b"changed"
    elif damage == "extra_backup": i["backup_bytes"]["new-only.ps1"] = b"unexpected"
    elif damage == "previous_manifest": i["prior_manifest_bytes"] += b" "
    elif damage == "manifest_commit":
        i["prior_manifest_bytes"] = encoded({"source_commit": NEW})
        i["expected_previous_manifest_sha256"] = sha(i["prior_manifest_bytes"])
        change_inventory(i, lambda d: d["prior_state"].update(manifest_sha256=i["expected_previous_manifest_sha256"]))
    elif damage == "state_pin": change_inventory(i, lambda d: d["prior_state"].update(state_sha256="0" * 64))
    elif damage == "state_commit":
        state = json.loads(i["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"])
        state["source_commit"] = NEW
        raw = encoded(state)
        i["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"] = raw
        def update(d):
            d["prior_state"]["state_sha256"] = sha(raw)
            next(row for row in d["files"] if row["name"] == "WD_REBOOT_STATE_CURRENT.json")["sha256"] = sha(raw)
        change_inventory(i, update)
    elif damage == "new_commit": i["expected_new_commit"] = "0" * 40
    elif damage == "new_manifest": i["expected_new_manifest_sha256"] = "0" * 64
    elif damage == "old_commit": i["expected_previous_commit"] = "0" * 40
    elif damage == "old_manifest": i["expected_previous_manifest_sha256"] = "0" * 64
    elif damage == "no_prior": change_inventory(i, lambda d: d.update(prior_state=None))
    elif damage == "state_path_escape": change_inventory(i, lambda d: d["prior_state"].update(active_bundle=STORE+"\\..\\escape\\"+OLD))
    elif damage == "wrong_store": change_inventory(i, lambda d: d["prior_state"].update(bundle_store=r"C:\synthetic\different"))
    elif damage == "missing_name": i["expected_names"] = NAMES[:-1]
    elif damage == "duplicate_name": change_inventory(i, lambda d: d["files"].append(dict(d["files"][0])))
    elif damage == "bad_name": change_inventory(i, lambda d: d["files"][0].update(name="../escape"))
    elif damage == "present_not_bool": change_inventory(i, lambda d: d["files"][0].update(present=1))
    elif damage == "absent_with_hash": change_inventory(i, lambda d: d["files"][1].update(sha256="0" * 64))
    elif damage == "duplicate_json":
        i["inventory_bytes"] = b'{"schema":"one","schema":"two"}'
        i["expected_inventory_sha256"] = sha(i["inventory_bytes"])
    result = assess(**i)
    assert result["status"] == "HOLD", (damage, result)
    assert result["authority_effect"] == "none" and result["restore_performed"] is False


def test_assessment_performs_no_file_or_process_io(monkeypatch):
    from tools.wd_reboot_restore_inventory_assess import assess_inventory
    import builtins
    import subprocess
    inputs = example()
    def forbidden(*args, **kwargs):
        raise AssertionError("I/O not allowed")
    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert assess_inventory(**inputs)["status"] == "BACKUP_CONSISTENT"


@pytest.mark.parametrize("field,value", [
    ("inventory_bytes", None), ("expected_inventory_sha256", None),
    ("expected_new_commit", None), ("expected_previous_commit", NEW),
    ("expected_new_manifest_sha256", 12), ("expected_previous_manifest_sha256", "x" * 64),
    ("expected_names", None), ("backup_bytes", None), ("prior_manifest_bytes", None),
])
def test_invalid_untrusted_inputs_fail_closed(field, value):
    inputs = example()
    inputs[field] = value
    result = assess(**inputs)
    assert result["status"] == "HOLD"
    assert result["authority_effect"] == "none"


@pytest.mark.parametrize("field,value", [
    ("machine_root", r"D:\synthetic\machine"),
    ("machine_root", r"C:\synthetic\..\escape"),
    ("machine_root", r"\\server\share"),
])
def test_unsafe_inventory_paths_fail_closed(field, value):
    inputs = example()
    change_inventory(inputs, lambda d: d.update({field: value}))
    assert assess(**inputs)["status"] == "HOLD"


@pytest.mark.parametrize("damage", ["missing_both", "missing_commit", "missing_hash", "wrong_commit",
                                    "wrong_hash", "uppercase_commit", "lowercase_hash", "lowercase_state_hash",
                                    "nonstring_commit", "nonstring_hash", "duplicate_final_commit"])
def test_invalid_state_final_pair_holds_even_when_backup_is_repinned(damage):
    inputs = example()
    state = json.loads(inputs["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"])
    if damage in ("missing_both", "missing_commit"): del state["final_commit"]
    if damage in ("missing_both", "missing_hash"): del state["final_manifest_sha256"]
    if damage == "wrong_commit": state["final_commit"] = NEW
    if damage == "wrong_hash": state["final_manifest_sha256"] = "0" * 64
    if damage == "uppercase_commit": state["final_commit"] = OLD.upper()
    if damage == "lowercase_hash": state["final_manifest_sha256"] = state["final_manifest_sha256"].lower()
    if damage == "lowercase_state_hash": state["manifest_sha256"] = state["manifest_sha256"].lower()
    if damage == "nonstring_commit": state["final_commit"] = 1
    if damage == "nonstring_hash": state["final_manifest_sha256"] = 1
    raw = encoded(state)
    if damage == "duplicate_final_commit":
        raw = raw[:-1] + b',"final_commit":"' + OLD.encode() + b'"}'
    inputs["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"] = raw
    def update(d):
        d["prior_state"]["state_sha256"] = sha(raw)
        next(row for row in d["files"] if row["name"] == "WD_REBOOT_STATE_CURRENT.json")["sha256"] = sha(raw)
    change_inventory(inputs, update)
    result = assess(**inputs)
    assert result["status"] == "HOLD", (damage, result)
    assert result["state_final_pair_consistent"] is False
    assert result["prior_wrapper_state_compatibility_verified"] is False
    assert result["authority_effect"] == "none"


def test_prior_manifest_and_state_allow_utf8_bom_without_changing_hashed_bytes():
    inputs = example()
    manifest = b"\xef\xbb\xbf" + inputs["prior_manifest_bytes"]
    manifest_hash = sha(manifest)
    inputs["prior_manifest_bytes"] = manifest
    inputs["expected_previous_manifest_sha256"] = manifest_hash
    state = json.loads(inputs["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"])
    state["manifest_sha256"] = state["final_manifest_sha256"] = manifest_hash
    state_bytes = b"\xef\xbb\xbf" + encoded(state)
    inputs["backup_bytes"]["WD_REBOOT_STATE_CURRENT.json"] = state_bytes
    def update(d):
        d["prior_state"]["manifest_sha256"] = manifest_hash
        d["prior_state"]["state_sha256"] = sha(state_bytes)
        next(row for row in d["files"] if row["name"] == "WD_REBOOT_STATE_CURRENT.json")["sha256"] = sha(state_bytes)
    change_inventory(inputs, update)
    assert assess(**inputs)["status"] == "BACKUP_CONSISTENT"

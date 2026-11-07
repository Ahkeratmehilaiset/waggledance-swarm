"""Initial real P1 card input builder: default OFF, hash-pinned spec, exclusive audit outputs.

Synthetic only: a throwaway local git repository, the tracked fleet and card configs copied into the test dir, and a
synthetic prefix pin receipt. Nothing reads a bridge log, publishes a card or touches an installed bundle.
"""
import hashlib
import importlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-11-07T14:00:00Z"
UNTIL = "2026-11-08T14:00:00Z"
_CONFIGS = {"policy": "shared_card_non_review_policy.json", "public_policy": "shared_card_public_status_policy.json",
            "card_policy": "shared_card_writer_policy.json", "vocabulary": "shared_card_vocabulary.json",
            "path_allowlist": "shared_card_paths.json"}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def write(path, value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": str(path), "sha256": sha(raw)}


def tree(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(Path(root).rglob("*"))}


@pytest.fixture
def tmp_path(tmp_path_factory):
    root = tmp_path_factory.mktemp("initial-card-inputs") / ".codex-audit"
    root.mkdir()
    return root


@pytest.fixture
def mod():
    return importlib.import_module("tools.wd_q4_initial_card_inputs")


@pytest.fixture
def repo(tmp_path):
    """Synthetic repository: main = c1 <- c2 <- c3; side = c1 <- s1 (never reachable from c3)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    git = str(Path(git).resolve())
    path = tmp_path / "repo"
    path.mkdir()
    env = {"GIT_AUTHOR_NAME": "synthetic", "GIT_AUTHOR_EMAIL": "synthetic.invalid", "GIT_COMMITTER_NAME": "synthetic",
           "GIT_COMMITTER_EMAIL": "synthetic.invalid", "GIT_CONFIG_GLOBAL": "NUL", "GIT_CONFIG_NOSYSTEM": "1",
           "SYSTEMROOT": os.environ.get("SYSTEMROOT", "")}

    def run(*args):
        return subprocess.run([git, "-C", str(path), *args], env=env, check=True, capture_output=True,
                              text=True).stdout.strip()
    run("init", "-q", "-b", "main")
    commits = []
    for name in ("c1", "c2", "c3"):
        run("commit", "-q", "--allow-empty", "-m", name)
        commits.append(run("rev-parse", "HEAD"))
    run("checkout", "-q", "-b", "side", commits[0])
    run("commit", "-q", "--allow-empty", "-m", "s1")
    side = run("rev-parse", "HEAD")
    run("checkout", "-q", "main")
    return {"git": git, "path": path, "commits": commits, "side": side}


@pytest.fixture
def base(tmp_path, repo):
    configs = {name: write(tmp_path / "configs" / file, (ROOT / "configs/mind" / file).read_bytes())
               for name, file in _CONFIGS.items()}
    fleet = write(tmp_path / "fleet" / "wd-fleet.json", (ROOT / "ops/windows/reboot/wd-fleet.json").read_bytes())
    git = {"executable": repo["git"], "sha256": sha(Path(repo["git"]).read_bytes()), "repository": str(repo["path"])}
    return {"configs": configs, "fleet": fleet, "git": git, "repo": repo, "tmp": tmp_path}


def publish_spec(base, **changes):
    c1, c2, c3 = base["repo"]["commits"]
    spec = {"schema": "wd.q4-initial-card-inputs-spec.v1", "phase": "publish", "now_utc": NOW,
            "fleet": base["fleet"], "configs": base["configs"], "git": base["git"],
            "anchor": {"ref": "refs/heads/main", "commit": c3},
            "cards": [{"pr": 1951, "head": c2, "author": "codex-lead-1", "valid_until_utc": UNTIL},
                      {"pr": 1950, "head": c1, "author": "codex-lead-1", "valid_until_utc": UNTIL}]}
    spec.update(changes)
    return spec


def run_spec(mod, base, spec, name):
    out = base["tmp"] / ("out-" + name)
    out.mkdir()
    pin = write(base["tmp"] / "specs" / (name + ".json"), spec)
    return out, pin, lambda: mod.build(enabled=True, spec_path=pin["path"], spec_sha256=pin["sha256"], output_root=str(out))


def test_off_touches_nothing(mod):
    class Untouchable:
        def __getattribute__(self, _):
            raise AssertionError("OFF read an input")
    assert mod.build(spec_path=Untouchable()) == {"status": "OFF"}
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.wd_q4_initial_card_inputs"], cwd=ROOT,
                          capture_output=True, timeout=30)
    assert proc.returncode == 0 and json.loads(proc.stdout) == {"status": "OFF"}


DRAFTS = {"roster.json", "approved-git-anchors.json", "claim-draft-pr1951.json", "claim-draft-pr1950.json"}


def test_publish_default_writes_only_unverified_drafts_never_a_publish_request(mod, base):
    """Reachability from a pinned anchor commit proves neither a merge nor green checks, and no independently
    verifiable authority for either exists here, so the default output is a non-publishable draft."""
    from tools import wd_q4_public_card_publish_cli as publish_cli
    assert mod._MERGED_GREEN_AUTHORITY is None
    out, _, build = run_spec(mod, base, publish_spec(base), "drafts")
    result = build()
    assert result["status"] == "WRITTEN" and result["phase"] == "publish"
    files = tree(out)
    manifest = json.loads(files["inputs-manifest.json"])
    assert set(manifest["files"]) == DRAFTS and set(files) == DRAFTS | {"inputs-manifest.json"}
    assert manifest["verified_claims"] == 0
    draft = json.loads(files["claim-draft-pr1951.json"])
    assert draft == {"schema": "wd.q4-initial-card-claim-draft.v1", "truth": "UNVERIFIED", "publishable": False,
                     "pr": 1951, "head": base["repo"]["commits"][1], "author": "codex-lead-1",
                     "valid_until_utc": UNTIL,
                     "observed": {"anchor": {"refs/heads/main": base["repo"]["commits"][2]},
                                  "head_reachable_from_anchor_commit": True},
                     "missing_evidence": ["checks_green_by_verifiable_authority",
                                          "pr_merged_by_verifiable_authority"]}
    assert "claim" not in draft and b"green" not in files["claim-draft-pr1951.json"].replace(b"checks_green", b"")
    with pytest.raises(publish_cli.Refused):
        publish_cli.run(enabled=True, request_path=str(out / "claim-draft-pr1951.json"),
                        request_sha256=manifest["files"]["claim-draft-pr1951.json"])


def test_only_one_unset_authority_assignment_exists_in_the_module():
    source = (ROOT / "tools/wd_q4_initial_card_inputs.py").read_text(encoding="ascii")
    assert source.count("_MERGED_GREEN_AUTHORITY =") == 1 and "_MERGED_GREEN_AUTHORITY = None" in source
    assert "setattr" not in source and "os.environ" not in source and "getenv" not in source and "globals()" not in source


@pytest.mark.parametrize("verdict", [False, None, 1, "yes", {"merged": True}, [True]])
def test_test_seam_verdict_other_than_true_still_yields_drafts(mod, base, monkeypatch, verdict):
    monkeypatch.setattr(mod, "_MERGED_GREEN_AUTHORITY", lambda **kwargs: verdict)
    out, _, build = run_spec(mod, base, publish_spec(base), "verdict")
    build()
    assert set(json.loads((out / "inputs-manifest.json").read_bytes())["files"]) == DRAFTS


def test_test_seam_error_refuses_before_any_write(mod, base, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("authority unavailable")
    monkeypatch.setattr(mod, "_MERGED_GREEN_AUTHORITY", broken)
    out, _, build = run_spec(mod, base, publish_spec(base), "seam-error")
    with pytest.raises(mod.Refused):
        build()
    assert list(out.iterdir()) == []


def test_publish_requests_only_through_the_test_seam_authority(mod, base, monkeypatch):
    from tools import wd_q4_public_card_publish_cli as publish_cli
    from tools import wd_q4_roster_from_fleet as roster_mod
    asked = []
    monkeypatch.setattr(mod, "_MERGED_GREEN_AUTHORITY", lambda **kwargs: asked.append(kwargs) or True)
    out, _, build = run_spec(mod, base, publish_spec(base), "publish")
    result = build()
    assert result["status"] == "WRITTEN" and result["phase"] == "publish"
    files = tree(out)
    manifest = json.loads(files["inputs-manifest.json"])
    assert sha(files["inputs-manifest.json"]) == result["manifest_sha256"]
    assert set(manifest["files"]) == {"roster.json", "approved-git-anchors.json",
                                      "publish-request-pr1951.json", "publish-request-pr1950.json"}
    assert manifest["verified_claims"] == 2
    anchor = {"refs/heads/main": base["repo"]["commits"][2]}
    assert asked == [{"pr": 1951, "head": base["repo"]["commits"][1], "anchor": anchor},
                     {"pr": 1950, "head": base["repo"]["commits"][0], "anchor": anchor}]
    assert all(sha(files[name]) == pin for name, pin in manifest["files"].items())
    fleet_raw = Path(base["fleet"]["path"]).read_bytes()
    assert files["roster.json"] == roster_mod.build_roster_snapshot(fleet_raw, sha(fleet_raw), enabled=True)[0]
    assert json.loads(files["approved-git-anchors.json"]) == {
        "schema": "wd.q4-approved-git-anchors.v1", "anchors": {"refs/heads/main": base["repo"]["commits"][2]}}
    request = json.loads(files["publish-request-pr1951.json"])
    assert request["claim"] == {"v": "1", "kind": "merged", "subject": {"pr": 1951}, "outcome": "green", "next": "none",
                                "valid_until_utc": UNTIL, "tags": [], "head": base["repo"]["commits"][1]}
    assert request["inputs"]["roster"] == {"path": str(out / "roster.json"), "sha256": manifest["files"]["roster.json"]}
    seen = []
    monkeypatch.setattr(publish_cli.publisher, "publish", lambda **kwargs: seen.append(kwargs) or {"status": "X"})
    with pytest.raises(publish_cli.Unknown):  # shape accepted by the real CLI; the synthetic answer is not a receipt
        publish_cli.run(enabled=True, request_path=str(out / "publish-request-pr1951.json"),
                        request_sha256=manifest["files"]["publish-request-pr1951.json"])
    assert seen[0]["claim"] == request["claim"] and seen[0]["roster_bytes"] == files["roster.json"]


def _broken(base, fault):
    c1, c2, c3 = base["repo"]["commits"]
    spec = publish_spec(base)
    card = spec["cards"][0]
    if fault == "head_unreachable":
        card["head"] = base["repo"]["side"]
    elif fault == "head_absent":
        card["head"] = "f" * 40
    elif fault == "anchor_commit_absent":
        spec["anchor"]["commit"] = "e" * 40
    elif fault == "anchor_ref_only":
        spec["anchor"] = {"ref": "refs/heads/main"}
    elif fault == "anchor_bad_ref":
        spec["anchor"]["ref"] = "main"
    elif fault == "anchor_short_commit":
        spec["anchor"]["commit"] = c3[:12]
    elif fault == "git_pin":
        spec["git"] = dict(spec["git"], sha256="0" * 64)
    elif fault == "fleet_pin":
        spec["fleet"] = dict(spec["fleet"], sha256="0" * 64)
    elif fault == "config_pin":
        spec["configs"] = dict(spec["configs"], vocabulary=dict(spec["configs"]["vocabulary"], sha256="0" * 64))
    elif fault == "config_missing":
        spec["configs"] = {k: v for k, v in spec["configs"].items() if k != "path_allowlist"}
    elif fault == "author_not_writer":
        card["author"] = "claude-rco-1"
    elif fault == "author_unknown":
        card["author"] = "someone-else"
    elif fault == "duplicate_pr":
        spec["cards"][1]["pr"] = 1951
    elif fault == "no_cards":
        spec["cards"] = []
    elif fault == "five_cards":
        spec["cards"] = [dict(card, pr=card["pr"] + i) for i in range(5)]
    elif fault == "until_over_week":
        card["valid_until_utc"] = "2026-11-15T14:00:00Z"
    elif fault == "until_too_soon":
        card["valid_until_utc"] = "2026-11-07T14:01:00Z"
    elif fault == "until_not_minute":
        card["valid_until_utc"] = "2026-11-08T14:00:30Z"
    elif fault == "card_extra_key":
        card["tags"] = ["mind"]
    elif fault == "pr_not_int":
        card["pr"] = "1951"
    elif fault == "spec_extra_key":
        spec["deploy"] = True
    elif fault == "wrong_schema":
        spec["schema"] = "wd.q4-initial-card-inputs-spec.v2"
    elif fault == "now_not_minute":
        spec["now_utc"] = "2026-11-07T14:00:01Z"
    return spec


@pytest.mark.parametrize("fault", ["head_unreachable", "head_absent", "anchor_commit_absent", "anchor_ref_only",
                                   "anchor_bad_ref", "anchor_short_commit", "git_pin", "fleet_pin", "config_pin",
                                   "config_missing", "author_not_writer", "author_unknown", "duplicate_pr", "no_cards",
                                   "five_cards", "until_over_week", "until_too_soon", "until_not_minute",
                                   "card_extra_key", "pr_not_int", "spec_extra_key", "wrong_schema", "now_not_minute"])
def test_publish_phase_refuses_before_any_write(mod, base, fault):
    out, _, build = run_spec(mod, base, _broken(base, fault), fault)
    with pytest.raises(mod.Refused):
        build()
    assert list(out.iterdir()) == []


@pytest.mark.parametrize("fault", ["not_audit", "nonempty", "missing", "spec_pin", "relative"])
def test_output_root_and_spec_pin_refuse_before_any_write(mod, base, fault):
    spec = publish_spec(base)
    pin = write(base["tmp"] / "specs" / "output.json", spec)
    out = base["tmp"] / "out-output"
    out.mkdir()
    if fault == "not_audit":
        out = base["tmp"].parent / "plain-out"
        out.mkdir()
    if fault == "nonempty":
        (out / "left.txt").write_bytes(b"x")
    if fault == "missing":
        out = base["tmp"] / "absent"
    spec_pin = "0" * 64 if fault == "spec_pin" else pin["sha256"]
    root = "relative-out" if fault == "relative" else str(out)
    before = tree(out) if out.exists() else None
    with pytest.raises(mod.Refused):
        mod.build(enabled=True, spec_path=pin["path"], spec_sha256=spec_pin, output_root=root)
    assert (tree(out) if out.exists() else None) == before


def test_interrupt_after_first_write_is_unknown_and_dir_is_not_reused(mod, base, monkeypatch):
    out, pin, build = run_spec(mod, base, publish_spec(base), "interrupt")
    real = mod._write_new
    calls = []

    def flaky(path, data):
        calls.append(path)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return real(path, data)
    monkeypatch.setattr(mod, "_write_new", flaky)
    with pytest.raises(mod.Unknown):
        build()
    assert len(list(out.iterdir())) == 1
    monkeypatch.setattr(mod, "_write_new", real)
    with pytest.raises(mod.Refused):
        build()


def _mutate_after_first_write(mod, monkeypatch, action):
    """Run the change once after the first output write; a failing change is recorded, never turned into UNKNOWN."""
    real = mod._write_new
    calls, failures = [], []

    def first(path, data):
        calls.append(path)
        real(path, data)
        if len(calls) == 1:
            try:
                action()
            except BaseException as error:  # noqa: B036 - reported by the caller's assertion
                failures.append(error)
    monkeypatch.setattr(mod, "_write_new", first)
    return failures


def _junction_copy(directory):
    """Replace a dir by a junction to a byte-identical copy: same bytes, but no longer the pinned plain path."""
    copy = directory.parent / (directory.name + "-copy")
    shutil.copytree(directory, copy)

    def writable(func, path, _):  # git object files are read-only on Windows
        os.chmod(path, stat.S_IWRITE)
        func(path)
    shutil.rmtree(directory, **({"onexc": writable} if sys.version_info >= (3, 12) else {"onerror": writable}))
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(directory), str(copy)], capture_output=True)
    if made.returncode != 0:
        pytest.skip("junction unavailable")


@pytest.mark.parametrize("target", ["vocabulary", "policy", "fleet", "spec", "configs_junction", "repo_junction",
                                    "extra_output"])
def test_publish_input_changed_after_first_write_is_unknown(mod, base, monkeypatch, target):
    out, pin, build = run_spec(mod, base, publish_spec(base), "late-" + target)
    path = {"vocabulary": base["configs"]["vocabulary"]["path"], "policy": base["configs"]["policy"]["path"],
            "fleet": base["fleet"]["path"], "spec": pin["path"]}.get(target)
    if target == "configs_junction":
        def action():
            _junction_copy(Path(base["configs"]["vocabulary"]["path"]).parent)
    elif target == "repo_junction":
        def action():
            _junction_copy(Path(base["git"]["repository"]))
    elif target == "extra_output":
        def action():
            (out / "extra.json").write_bytes(b"{}")
    else:
        def action():
            Path(path).write_bytes(Path(path).read_bytes() + b" ")
    failures = _mutate_after_first_write(mod, monkeypatch, action)
    with pytest.raises(mod.Unknown):
        build()
    assert failures == []


def test_input_changed_before_first_write_refuses_with_nothing_written(mod, base, monkeypatch):
    vocabulary = Path(base["configs"]["vocabulary"]["path"])

    def mutating(**kwargs):  # runs after the inputs were read and before the pre-write recheck
        vocabulary.write_bytes(vocabulary.read_bytes() + b" ")
        return False
    monkeypatch.setattr(mod, "_MERGED_GREEN_AUTHORITY", mutating)
    out, _, build = run_spec(mod, base, publish_spec(base), "early-change")
    with pytest.raises(mod.Refused):
        build()
    assert list(out.iterdir()) == []


# --- candidate phase ------------------------------------------------------------------------------------------------

@pytest.fixture
def published(mod, base):
    out, _, build = run_spec(mod, base, publish_spec(base), "published")
    assert build()["status"] == "WRITTEN"
    manifest = json.loads((out / "inputs-manifest.json").read_bytes())
    return {"roster": {"path": str(out / "roster.json"), "sha256": manifest["files"]["roster.json"]},
            "anchors": {"path": str(out / "approved-git-anchors.json"),
                        "sha256": manifest["files"]["approved-git-anchors.json"]}}


def candidate_spec(base, published, parent, **changes):
    from tools import wd_q4_lane_link as link
    from sandbox.mind.step2 import shared_card_projection as projection
    tmp = base["tmp"]
    prefix = write(tmp / "pins" / "prefix-pin.json", {
        "schema": "wd.q4-private-bridge-prefix-pin.v1", "cursor": {"offset": 4096, "file_identity": "synthetic-id",
                                                                   "generation": None},
        "prefix_sha256": "a" * 64, "source_authentication": "UNKNOWN", "freshness": "UNKNOWN",
        "completeness": "UNKNOWN", "scope": "bounded_observed_prefix"})
    plan = {key: "synthetic-" + key for key in link._PUBLIC_PLAN_SCHEMAS["wd.q4-public-wrapper-plan.v1"]}
    plan["schema"] = "wd.q4-public-wrapper-plan.v1"
    options = {"expected_bundle": "C:/synthetic/bundle", "bundle_store_root": "C:/synthetic/store",
               "active_bundle_root": "C:/synthetic/active", "expected_state_sha256": "b" * 64,
               "timeout_seconds": 60}
    spec = {"schema": "wd.q4-initial-card-inputs-spec.v1", "phase": "candidate", "now_utc": NOW,
            "configs": base["configs"], "roster": published["roster"], "approved_anchors": published["anchors"],
            "git": base["git"], "source_path": str(tmp / "synthetic-source" / "events.jsonl"),
            "prefix_pin": prefix,
            "source_anchor": {"root": str(parent / ".codex-audit" / "anchors" / "a1"),
                              "generation_sha256": "c" * 64, "binding_sha256": "d" * 64},
            "expected_card_module_sha256": projection.module_sha256(),
            "encoder": {"model": "synthetic"}, "trusted_model_root": str(tmp / "models"),
            "base_plan": write(tmp / "plans" / "base-plan.json", plan),
            "fixed_template": write(tmp / "plans" / "fixed-template.json", {"synthetic": True}),
            "composer_options": write(tmp / "plans" / "composer-options.json", options),
            "shared_stage_root": str(parent / "live"), "generation": "1" * 40,
            "generation_root": str(parent / "run-1"),
            "audit_output_root": str(parent / ".codex-audit" / "runs" / "run-1")}
    spec.update(changes)
    return spec


@pytest.fixture
def parent(tmp_path, monkeypatch):
    from tools import wd_q4_card_audit_orchestrator as orchestrator
    root = tmp_path / "generations"
    monkeypatch.setattr(orchestrator, "_GENERATION_PARENT", root)
    return root


def test_candidate_phase_writes_orchestrator_shaped_requests(mod, base, published, parent):
    from tools import wd_q4_real_card_decision_cli as dc
    from tools import wd_q4_real_card_snapshot_cli as io
    out, _, build = run_spec(mod, base, candidate_spec(base, published, parent), "candidate")
    result = build()
    assert result["status"] == "WRITTEN" and result["phase"] == "candidate"
    files = tree(out)
    manifest = json.loads(files["inputs-manifest.json"])
    assert set(manifest["files"]) == {"decision-request.json", "snapshot-template.json", "finish-template.json",
                                      "candidate-request.json"}
    decision = json.loads(files["decision-request.json"])
    template = json.loads(files["snapshot-template.json"])
    finish = json.loads(files["finish-template.json"])
    request = json.loads(files["candidate-request.json"])
    assert set(decision) == dc._KEYS and decision["mode"] == "real"
    assert decision["source_mode"] == template["source_mode"] == "operator_authorized_source"
    assert set(decision["inputs"]) == dc._INPUTS
    assert decision["inputs"]["roster"] == published["roster"] and decision["inputs"]["anchors"] == published["anchors"]
    assert decision["cursor"] == {"offset": 4096, "file_identity": "synthetic-id", "generation": None}
    assert decision["prefix_sha256"] == "a" * 64 and decision["anchor"]["root"] == str(parent / ".codex-audit/anchors/a1")
    assert set(template) == io._KEYS and template["generation_name"] == "candidate"
    for key in ("vocabulary", "path_allowlist", "policy", "roster", "card_policy"):
        assert template["inputs"][key] == decision["inputs"][key]
    assert "decision" not in template["inputs"]  # the orchestrator adds its own decision output
    assert {k: template["git"][k] for k in ("executable", "sha256", "repository")} == decision["git"]
    assert template["git"]["anchors"] == {"refs/heads/main": base["repo"]["commits"][2]}
    assert template["source_path"] == decision["source_path"]
    assert set(finish) == {"schema", "base_plan", "fixed_template", "composer_options"}
    for key in ("policy", "roster", "public_policy"):
        assert finish["base_plan"][key + "_path"] == decision["inputs"][key]["path"]
        assert finish["base_plan"][key + "_sha256"] == decision["inputs"][key]["sha256"]
    assert finish["composer_options"]["shared_stage_root"] == str(parent / "live")
    assert finish["composer_options"]["generation"] == "1" * 40
    assert set(request) == {"schema", "decision", "snapshot_template", "output_root", "finish", "generation_root"}
    assert request["schema"] == "wd.q4-card-real-candidate-request.v1"
    assert request["generation_root"] == str(parent / "run-1")
    for key, name in (("decision", "decision-request.json"), ("snapshot_template", "snapshot-template.json"),
                      ("finish", "finish-template.json")):
        assert request[key] == {"path": str(out / name), "sha256": manifest["files"][name]}


@pytest.mark.parametrize("fault", ["module_pin", "prefix_schema", "prefix_generation", "anchor_outside_anchors",
                                   "anchor_dot_id", "run_dot", "run_outside_parent", "audit_not_under_parent_audit",
                                   "audit_in_anchors", "shared_in_audit", "generation_bad", "options_preset_shared",
                                   "options_missing_state", "plan_schema", "plan_extra_key", "roster_pin",
                                   "anchors_doc_pin", "builder_output_in_audit", "audit_in_builder_output",
                                   "source_relative", "encoder_not_object"])
def test_candidate_phase_refuses_before_any_write(mod, base, published, parent, fault):
    tmp = base["tmp"]
    changes = {}
    if fault == "module_pin":
        changes["expected_card_module_sha256"] = "0" * 64
    elif fault == "prefix_schema":
        changes["prefix_pin"] = write(tmp / "pins" / "bad-prefix.json", {"schema": "other"})
    elif fault == "prefix_generation":
        raw = json.loads(Path(candidate_spec(base, published, parent)["prefix_pin"]["path"]).read_bytes())
        raw["cursor"]["generation"] = "g1"
        changes["prefix_pin"] = write(tmp / "pins" / "gen-prefix.json", raw)
    elif fault == "anchor_outside_anchors":
        changes["source_anchor"] = {"root": str(parent / ".codex-audit" / "other" / "a1"),
                                    "generation_sha256": "c" * 64, "binding_sha256": "d" * 64}
    elif fault == "anchor_dot_id":
        changes["source_anchor"] = {"root": str(parent / ".codex-audit" / "anchors" / ".a1"),
                                    "generation_sha256": "c" * 64, "binding_sha256": "d" * 64}
    elif fault == "run_dot":
        changes["generation_root"] = str(parent / ".run-1")
    elif fault == "run_outside_parent":
        changes["generation_root"] = str(tmp / "elsewhere" / "run-1")
    elif fault == "audit_not_under_parent_audit":
        changes["audit_output_root"] = str(parent / "run-audit")
    elif fault == "audit_in_anchors":
        changes["audit_output_root"] = str(parent / ".codex-audit" / "anchors" / "a1" / "out")
    elif fault == "shared_in_audit":
        changes["shared_stage_root"] = str(parent / ".codex-audit" / "live")
    elif fault == "generation_bad":
        changes["generation"] = "G" * 40
    elif fault == "options_preset_shared":
        options = json.loads(Path(candidate_spec(base, published, parent)["composer_options"]["path"]).read_bytes())
        options["shared_stage_root"] = str(parent / "other-live")
        changes["composer_options"] = write(tmp / "plans" / "preset-options.json", options)
    elif fault == "options_missing_state":
        options = json.loads(Path(candidate_spec(base, published, parent)["composer_options"]["path"]).read_bytes())
        options.pop("expected_state_sha256")
        changes["composer_options"] = write(tmp / "plans" / "short-options.json", options)
    elif fault == "plan_schema":
        plan = json.loads(Path(candidate_spec(base, published, parent)["base_plan"]["path"]).read_bytes())
        plan["schema"] = "wd.q4-public-wrapper-plan.v3"
        changes["base_plan"] = write(tmp / "plans" / "v3-plan.json", plan)
    elif fault == "plan_extra_key":
        plan = json.loads(Path(candidate_spec(base, published, parent)["base_plan"]["path"]).read_bytes())
        plan["card_decision_path"] = "x"
        changes["base_plan"] = write(tmp / "plans" / "extra-plan.json", plan)
    elif fault == "roster_pin":
        changes["roster"] = dict(published["roster"], sha256="0" * 64)
    elif fault == "anchors_doc_pin":
        changes["approved_anchors"] = dict(published["anchors"], sha256="0" * 64)
    elif fault == "source_relative":
        changes["source_path"] = "events.jsonl"
    elif fault == "encoder_not_object":
        changes["encoder"] = "synthetic"
    spec = candidate_spec(base, published, parent, **changes)
    if fault in ("builder_output_in_audit", "audit_in_builder_output"):
        audit = Path(spec["audit_output_root"])
        # Both are direct children of a .codex-audit dir, so only the overlap check can refuse them.
        out = audit / ".codex-audit" / "builder" if fault == "builder_output_in_audit" else audit.parent
        assert out.parent.name == ".codex-audit" and not audit.exists()
        out.mkdir(parents=True)
        pin = write(tmp / "specs" / "in-audit.json", spec)
        with pytest.raises(mod.Refused):
            mod.build(enabled=True, spec_path=pin["path"], spec_sha256=pin["sha256"], output_root=str(out))
        assert list(out.iterdir()) == []
        return
    out, _, build = run_spec(mod, base, spec, "cand-" + fault)
    with pytest.raises(mod.Refused):
        build()
    assert list(out.iterdir()) == []


def test_write_new_never_replaces_an_existing_file(mod, tmp_path):
    target = tmp_path / "taken.json"
    target.write_bytes(b"first")
    with pytest.raises(FileExistsError):
        mod._write_new(target, b"second")
    assert target.read_bytes() == b"first"


@pytest.mark.parametrize("target", ["roster", "approved_anchors", "prefix_pin", "base_plan", "fixed_template",
                                    "composer_options", "card_policy"])
def test_candidate_input_changed_after_first_write_is_unknown(mod, base, published, parent, monkeypatch, target):
    spec = candidate_spec(base, published, parent)
    path = Path((spec["configs"] if target == "card_policy" else spec)[target]["path"])
    out, _, build = run_spec(mod, base, spec, "late-c-" + target)
    failures = _mutate_after_first_write(mod, monkeypatch, lambda: path.write_bytes(path.read_bytes() + b" "))
    with pytest.raises(mod.Unknown):
        build()
    assert failures == []


def test_cli_maps_refusal_and_prints_only_sanitized_status(mod, base, tmp_path):
    spec = publish_spec(base)
    pin = write(tmp_path / "specs" / "cli.json", spec)
    out = tmp_path / "out-cli"
    out.mkdir()
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.wd_q4_initial_card_inputs", "--enabled", "--spec",
                           pin["path"], "--spec-sha256", "0" * 64, "--output-root", str(out)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2 and proc.stdout == "" and json.loads(proc.stderr) == {"status": "REFUSED"}
    proc = subprocess.run([sys.executable, "-B", "-m", "tools.wd_q4_initial_card_inputs", "--enabled", "--spec",
                           pin["path"], "--spec-sha256", pin["sha256"], "--output-root", str(out)], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    value = json.loads(proc.stdout)
    assert set(value) == {"status", "phase", "manifest_sha256"} and value["status"] == "WRITTEN"
    assert str(tmp_path) not in proc.stdout + proc.stderr

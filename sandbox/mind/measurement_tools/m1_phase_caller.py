"""Unwired M1-P2 phase envelope. No data/native GO is supplied by this file.

Parent CLI runs one stage only, with a hash-bound typed Lead record and a NEW
externally published DESIGN. Typed records are NOT signatures: the supervising
lane must verify their exact live bridge origin/session before supplying them.
Never construct a DESIGN or GO here. Never emit private stdout, gold or errors.

The synthetic tests mock Popen and all measured code. They do not establish
native execution, package closure, OS containment, hard disk quotas or utility.
The live budget monitor is cooperative, not an OS write quota; a future native
GO must accept that limitation or supply an approved hard containment layer.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

DESIGN_SCHEMA = "wd.m1-p2-phase-design.v1"
GO_SCHEMA = "wd.m1-p2-phase-go.v1"
TASK = "codex-lead-1/m1-p2-phase-caller-tools-20261019"
LEAD_UUID = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
LEAD_SESSION = "wd-reboot-20261018T031647Z"
MEASURED_COMMIT = "6cf81dbbefe2b0e19a4c8306100beb30784ac4e4"
CACHE_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
PHASES = ("history", "candidates", "anchors", "build", "materialize", "prepare",
          "prepass", "commit", "arms", "grade")
PINS = {
    "goldset": "daaecd7402ffc052cf7175ce4aece4a7133ecacbf9024e6f507bca4865801ea6",
    "grader": "074b443657daf72797b66994fd6afa5f2f4dd9384c26d3b955e429f451740117",
    "comparator": "a1667a27da015dcaed0f97960bbede3281dab3540018106d2458879db213579a",
    "blind": "2011c5dca6b5e8f8880a7117911136d446faf5fa7d87faf24dbb6c032408b862",
}
REQUIRED_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "FAISS_OPT_LEVEL": "GENERIC", "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1", "PYTHONNOUSERSITE": "1"}
MAX_BUDGET = 64 * 1024**2
FLOOR = 3 * 1024**3
FIXED_REASONS = {"refused", "stage_failed", "timeout", "cleanup_failed", "budget",
                 "disk_floor", "consumed", "outputs", "pins", "go", "prior",
                 "publication", "path", "design", "worker_exit", "worker_intent"}


class Refused(ValueError):
    """Always a fixed code, never an underlying exception or data."""


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def hex64(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def file_hash(path):
    out = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            out.update(block)
    return out.hexdigest()


def safe_path(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            info = part.lstat()
            need(not part.is_symlink() and not getattr(part, "is_junction", lambda: False)()
                 and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT), "path")
    return path


def local(root, name):
    need(isinstance(name, str) and not Path(name).is_absolute(), "path")
    path = safe_path(root / name)
    need(path.is_relative_to(root) and path != root and path.resolve().is_relative_to(root.resolve()), "path")
    return path


def path_hash(path):
    path = safe_path(path)
    if path.is_file():
        return file_hash(path)
    need(path.is_dir(), "outputs")
    rows = []
    for leaf in sorted(path.rglob("*")):
        safe_path(leaf)
        if leaf.is_file():
            rows.append([leaf.relative_to(path).as_posix(), leaf.stat().st_size, file_hash(leaf)])
    return digest(rows)


def load_json(path):
    def unique(pairs):
        out = {}
        for key, value in pairs:
            need(key not in out, "design")
            out[key] = value
        return out
    path = safe_path(path)
    need(path.stat().st_size <= MAX_BUDGET, "budget")
    return json.loads(path.read_bytes(), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refused("design")))


def free_bytes(path):
    path = Path(path)
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


def used_bytes(root):
    if not root.exists():
        return 0
    total = 0
    for path in [root, *root.rglob("*")]:
        safe_path(path)
        if path.is_file():
            total += path.stat().st_size
    return total


def budget(config, extra=0):
    root = Path(config["root"])
    need(used_bytes(root) + extra <= config["budget_bytes"], "budget")
    need(free_bytes(root) >= FLOOR + config["budget_bytes"] + extra, "disk_floor")


def write_new(path, value, config):
    raw = canonical(value)
    budget(config, len(raw))
    path = safe_path(path)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    need(path.read_bytes() == raw, "outputs")


def validate_design(path, expected):
    need(hex64(expected) and file_hash(path) == expected, "design")
    c = load_json(path)
    need(c.get("schema") == DESIGN_SCHEMA and c.get("source_commit") == MEASURED_COMMIT, "design")
    need(isinstance(c.get("run_id"), str) and re.fullmatch("[A-Za-z0-9_-]{1,100}", c["run_id"]), "design")
    root, cwd = safe_path(c["root"]), safe_path(c["cwd"])
    need(cwd == Path.cwd().absolute() and root.is_relative_to(cwd / ".codex-audit")
         and root != cwd / ".codex-audit", "path")
    need(os.name != "nt" or root.drive.upper() == "C:", "path")
    need(type(c.get("budget_bytes")) is int and 0 < c["budget_bytes"] <= MAX_BUDGET, "budget")
    need(type(c.get("timeout_s")) is int and 0 < c["timeout_s"] <= 1800, "design")
    need(type(c.get("case_limit")) is int and 0 < c["case_limit"] <= 10000, "design")
    need(c.get("caller_sha256") == file_hash(__file__), "pins")
    need(file_hash(c["python"]) == c["python_sha256"] and Path(c["python"]).is_absolute(), "pins")
    need(set(c["helpers"]) == set(PINS), "pins")
    for key, wanted in PINS.items():
        row = c["helpers"][key]
        need(row["sha256"] == wanted and file_hash(safe_path(row["path"])) == wanted, "pins")
    for group in ("source_files", "cache_files", "runtime_files"):
        need(isinstance(c.get(group), dict) and bool(c[group]), "pins")
        for source, sha in c[group].items():
            need(Path(source).is_absolute() and hex64(sha) and file_hash(safe_path(source)) == sha, "pins")
    binding = c["binding"]
    need(binding["source_commit"] == MEASURED_COMMIT and binding["source_files"] == c["source_files"], "pins")
    need(binding["encoder"]["revision"] == CACHE_REVISION, "pins")
    for key, value in (("guard_band_s", 1.0), ("bm25_k1", 1.2), ("bm25_b", .75)):
        need(type(binding["rule"].get(key)) in (int, float) and binding["rule"][key] == value, "pins")
    need(all(c["environment"].get(k) == v for k, v in REQUIRED_ENV.items()), "design")
    need(set(c["environment"]) <= set(REQUIRED_ENV) | {"USERPROFILE", "SystemRoot", "HF_HOME"}, "design")
    return c


def validate_go(c, design_sha, go, expected):
    need(isinstance(go, dict) and hex64(expected) and digest(go) == expected, "go")
    need(go.get("schema") == GO_SCHEMA and go.get("agent") == "codex-lead-1"
         and go.get("agent_uuid") == LEAD_UUID and go.get("session_id") == LEAD_SESSION
         and go.get("task_id") == TASK and hex64(go.get("event_ref")), "go")
    need(go.get("design_sha256") == design_sha and go.get("run_id") == c["run_id"]
         and go.get("execute") is True and go.get("phase") in PHASES, "go")
    try:
        start = dt.datetime.fromisoformat(go["issued_at_utc"])
        end = dt.datetime.fromisoformat(go["expires_at_utc"])
        now = dt.datetime.now(dt.timezone.utc)
        need(start.utcoffset() == end.utcoffset() == dt.timedelta(0)
             and start <= now < end and end - start <= dt.timedelta(hours=1), "go")
    except (KeyError, ValueError, TypeError, AttributeError):
        raise Refused("go") from None


def validate_native_config(c, phase):
    if phase not in ("prepare", "prepass", "arms"):
        return
    # No mutable home/cache discovery or environment fallback for native work.
    profile = safe_path(c.get("environment", {}).get("USERPROFILE", "."))
    need(Path(c.get("cache_root", ".")).is_absolute()
         and Path(c.get("environment", {}).get("USERPROFILE", ".")).is_absolute(), "pins")
    cache = safe_path(c["cache_root"])
    need(cache == profile / ".cache/huggingface/hub/models--intfloat--multilingual-e5-small"
         and c["environment"].get("HF_HOME") == str(cache.parent.parent), "pins")
    ref = cache / "refs/main"
    need(str(ref) in c["cache_files"] and ref.read_text(encoding="ascii").strip() == CACHE_REVISION, "pins")
    need(c["binding"]["encoder"].get("device") == "cpu"
         and c["binding"]["encoder"].get("torch_threads") == 1, "pins")


def validate_prior(c, go, design_sha):
    root = Path(c["root"])
    phase = go["phase"]
    expected = PHASES[:PHASES.index(phase)]
    priors = go.get("prior_receipts")
    need(isinstance(priors, dict) and set(priors) == set(expected), "prior")
    if expected:
        owner = load_json(root / "OWNER.json")
        need(owner == {"design_sha256": design_sha, "run_id": c["run_id"]}, "prior")
    for previous in expected:
        receipt = root / ".phases" / previous / "RESULT.json"
        need(hex64(priors[previous]) and file_hash(receipt) == priors[previous], "prior")
        row = load_json(receipt)
        need(row.get("status") == "COMPLETE" and row.get("phase") == previous
             and row.get("design_sha256") == design_sha and row.get("run_id") == c["run_id"], "prior")
        need(row.get("prior_receipts") == {p: priors[p] for p in PHASES[:PHASES.index(previous)]}, "prior")
        need(bool(row.get("outputs")), "prior")
        for name, sha in row["outputs"].items():
            need(hex64(sha) and path_hash(local(root, name)) == sha, "prior")
        for name, sha in row.get("logs", {}).items():
            need(hex64(sha) and file_hash(local(root, name)) == sha, "prior")


def validate_publication(root, go, reader=load_json):
    # Do NOT call reader on cases.json/gold/nonce. These are metadata-only files.
    pub = go.get("arms_publication")
    required = ("journal/arms/arm_A.json", "journal/arms/arm_B.json",
                "journal/arms/RESULT.json", "RUN_MANIFEST.json")
    need(isinstance(pub, dict) and set(pub) == set(required)
         and hex64(go.get("arms_review_event")), "publication")
    for name in required:
        need(hex64(pub[name]) and path_hash(local(root, name)) == pub[name], "publication")
    manifest = reader(root / "RUN_MANIFEST.json")
    need(manifest.get("design_sha256") == go["design_sha256"], "publication")
    need(manifest.get("arm_files") == {p: pub[p] for p in required[:2]}, "publication")


def phase_argv(design, design_sha, go_path, go_sha, phase):
    need(phase in PHASES, "go")
    c = load_json(design)
    return [c["python"], "-I", "-B", str(Path(__file__).absolute()), "worker",
            "--design", str(Path(design).absolute()), "--design-sha256", design_sha,
            "--go", str(Path(go_path).absolute()), "--go-sha256", go_sha, "--phase", phase]


def launch(argv, c, stage):
    """Owned direct child only; no shell/PID search. Not descendant containment."""
    env = dict(c["environment"])
    env["SystemRoot"] = env.get("SystemRoot", "C:\\Windows")
    # Future native DESIGN must explicitly bind USERPROFILE/HF_HOME as needed.
    env["TEMP"] = env["TMP"] = str(Path(c["root"]) / "tmp")
    child = None
    def deadline():
        if c.get("deadline_utc"):
            need(dt.datetime.now(dt.timezone.utc) < dt.datetime.fromisoformat(c["deadline_utc"]), "timeout")
    try:
        deadline()
        with (stage / "stdout.log").open("xb") as out, (stage / "stderr.log").open("xb") as err:
            child = subprocess.Popen(argv, cwd=c["cwd"], env=env, stdin=subprocess.DEVNULL,
                                     stdout=out, stderr=err, shell=False)
            started = time.monotonic()
            while child.poll() is None:
                deadline()
                need(time.monotonic() - started < c["timeout_s"], "timeout")
                budget(c)
                time.sleep(.025)
            need(child.returncode == 0, "worker_exit")
    finally:
        if child is not None:
            try:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
            except BaseException:
                raise Refused("cleanup_failed") from None


def cases(c):
    root = Path(c["root"])
    path = root / "caseset/blind.jsonl"
    need(path.stat().st_size <= c["budget_bytes"], "budget")
    rows = [json.loads(line) for line in path.read_bytes().splitlines() if line]
    need(len(rows) <= c["case_limit"], "budget")
    ids = set()
    for row in rows:
        need(set(row) == {"id", "time", "situation"} and isinstance(row["id"], str)
             and re.fullmatch("[A-Za-z0-9_-]{1,80}", row["id"]) and row["id"] not in ids, "outputs")
        ids.add(row["id"])
    return rows


def outputs(phase, c):
    fixed = {
        "history": ["ops.jsonl", "history.json"], "candidates": ["candidates.jsonl"],
        "anchors": ["anchors.json"],
        "build": ["caseset/blind.jsonl", "caseset/cases.pre.json", "caseset/snapshots"],
        "materialize": ["MATERIALIZED.json"], "prepare": ["PREPARED.json"],
        "prepass": ["journal/prepass/guard.json", "journal/prepass/applicable.json", "journal/prepass/RESULT.json"],
        "commit": ["audit.json", "caseset/cases.json", "caseset/commitments.json", "caseset/RECEIPT.json"],
        "arms": ["journal/arms/arm_A.json", "journal/arms/arm_B.json", "journal/arms/RESULT.json", "RUN_MANIFEST.json"],
        "grade": ["m1_result.json"],
    }
    names = fixed[phase][:]
    if phase in ("materialize", "prepare"):
        suffixes = ["lessons"] if phase == "materialize" else ["corpus", "vectors", "memory"]
        names += [f"prepared/{row['id']}/{suffix}" for row in cases(c) for suffix in suffixes]
    return names


def _execute(design, design_sha, go, expected_go_sha, *, launcher=None, go_path=None):
    c = validate_design(design, design_sha)
    validate_go(c, design_sha, go, expected_go_sha)
    validate_prior(c, go, design_sha)
    validate_native_config(c, go["phase"])
    root, phase = Path(c["root"]), go["phase"]
    if phase == "grade":
        validate_publication(root, go)
    budget(c)
    validate_go(c, design_sha, go, expected_go_sha)  # expensive pins/prior reads may have passed expiry
    if phase == "history":
        # This atomic mkdir IS the consumed latch and the first side effect.
        # Even a crash before OWNER/INTENT stays consumed: never adopt a root.
        try:
            root.mkdir(exist_ok=False)
        except FileExistsError:
            raise Refused("consumed") from None
        (root / ".phases").mkdir()
        write_new(root / "OWNER.json", {"design_sha256": design_sha, "run_id": c["run_id"]}, c)
    stage = root / ".phases" / phase
    try:
        stage.mkdir(exist_ok=False)
    except FileExistsError:
        raise Refused("consumed") from None
    try:
        intent = {"phase": phase, "design_sha256": design_sha, "go_sha256": expected_go_sha,
                  "run_id": c["run_id"], "prior_receipts": go["prior_receipts"]}
        write_new(stage / "INTENT.json", intent, c)
        # Canonical copy, bound to the exact typed input, never source text.
        retained_go = stage / "GO.json"
        write_new(retained_go, go, c)
        argv = phase_argv(design, design_sha, retained_go, expected_go_sha, phase)
        write_new(stage / "ARGV.json", argv, c)
        # Frozen helpers contain ordinary writes; do not let them adopt old
        # files. This check is after the latch, before any helper call.
        need(all(not local(root, name).exists() for name in outputs(phase, c)), "consumed")
        if phase == "build":
            need(not (root / "caseset").exists(), "consumed")
        (root / "tmp").mkdir(exist_ok=True)
        (launcher or launch)(argv, dict(c, deadline_utc=go["expires_at_utc"]), stage)
        budget(c)
        validate_design(design, design_sha)
        validate_go(c, design_sha, go, expected_go_sha)
        bound = {name: path_hash(local(root, name)) for name in outputs(phase, c)}
        logs = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(stage.glob("*.log"))}
        result = dict(intent, status="COMPLETE", outputs=bound, argv_sha256=digest(argv),
                      logs=logs, evidence_class="EXECUTION_ENVELOPE_NOT_INDEPENDENT")
        write_new(stage / "RESULT.json", result, c)
        return result
    except BaseException as exc:
        reason = str(exc) if isinstance(exc, Refused) and str(exc) in FIXED_REASONS else "stage_failed"
        try:
            write_new(stage / "VOID.json", {"status": "VOID", "reason": reason}, c)
        except BaseException:
            # INTENT/root remains consumed even if no receipt can be written.
            reason = "stage_failed"
        raise Refused(reason) from None


def execute(design, design_sha, go, expected_go_sha, *, launcher=None, go_path=None):
    try:
        return _execute(design, design_sha, go, expected_go_sha, launcher=launcher, go_path=go_path)
    except Refused:
        raise
    except Exception:
        # Missing metadata, unreadable files and malformed types stay fixed-code
        # refusals even when they happen BEFORE a consumed latch can be written.
        raise Refused("refused") from None


def module_from(path, name):
    path = Path(path)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def source_module(c, relative, name):
    path = safe_path(Path(c["source_root"]) / relative)
    need(str(path) in c["source_files"] and file_hash(path) == c["source_files"][str(path)], "pins")
    sys.path.insert(0, str(path.parent))
    return module_from(path, name)


def native_runner(c, design_sha):
    root = Path(c["root"])
    comp = module_from(c["helpers"]["comparator"]["path"], "bm25_comparator")
    blind = module_from(c["helpers"]["blind"]["path"], "m1_blind_runner")
    need(sys.modules["bm25_comparator"] is comp, "pins")
    v21 = source_module(c, "sandbox/mind/remember/remember_v21.py", "m1_p2_v21")
    items = [blind.BlindCase(row["id"], row["situation"], row["time"],
                            file_hash(root / "caseset/snapshots" / ("snap-" + row["id"] + ".json")))
             for row in cases(c)]
    prepared = load_json(root / "PREPARED.json")
    factory = blind.prepared_native_factory(v21, c["binding"], prepared)
    return blind.BlindRunner(c["binding"], factory, blind.FileJournal(root / "journal", c["budget_bytes"]),
                             design_sha), items


def dispatch(c, design_sha, go):
    """Only the worker calls this, AFTER parent intent and all stage checks.

    Commit is the preregistered mechanical sealed selection/nonce tool; the
    supervisor never receives its gold. Only grade may load the sealed grader.
    """
    root, phase = Path(c["root"]), go["phase"]
    h = c["helpers"]
    if phase in ("history", "candidates", "anchors", "build", "materialize", "commit"):
        goldset = module_from(h["goldset"]["path"], "m1_p2_goldset")
        base = ["--memory-dir", c["memory_dir"]]
        args = {
            "history": base + ["--transcripts-root", c["transcripts_root"], "--ops", str(root / "ops.jsonl"),
                               "--status", str(root / "history.json")],
            "candidates": base + ["--ops", str(root / "ops.jsonl"), "--out", str(root / "candidates.jsonl")],
            "anchors": base + ["--candidates", str(root / "candidates.jsonl"), "--transcripts-root",
                               c["transcripts_root"], "--out", str(root / "anchors.json")],
            "build": base + ["--ops", str(root / "ops.jsonl"), "--candidates", str(root / "candidates.jsonl"),
                             "--anchors", str(root / "anchors.json"), "--out", str(root / "caseset"),
                             "--budget-root", str(root)],
            "commit": ["--out", str(root / "caseset"), "--guard", str(root / "journal/prepass/guard.json"),
                       "--applicable", str(root / "journal/prepass/applicable.json"), "--audit", str(root / "audit.json")],
        }
        if phase == "materialize":
            manifest = {}
            for row in cases(c):
                budget(c)
                dest = root / "prepared" / row["id"] / "lessons"
                result = goldset.materialize(root / "caseset", row["id"], dest)
                need(result["snapshot_sha256"] == file_hash(root / "caseset/snapshots" / ("snap-" + row["id"] + ".json")), "outputs")
                manifest[row["id"]] = result
            write_new(root / "MATERIALIZED.json", manifest, c)
        else:
            if phase == "commit":
                need(go.get("anchor_audit") == {"disputed": []}, "go")
                write_new(root / "audit.json", go["anchor_audit"], c)
            need(goldset.main([phase, *args[phase]]) == 0, "worker_exit")
    elif phase == "prepare":
        corpus = source_module(c, "sandbox/mind/step2/corpus.py", "corpus")
        encode = source_module(c, "sandbox/mind/step2/encode.py", "encode")
        memory = source_module(c, "sandbox/mind/step2/memory.py", "memory")
        module_from(h["comparator"]["path"], "bm25_comparator")
        blind = module_from(h["blind"]["path"], "m1_blind_runner")
        # It imports the EXACT registered comparator, never a second B run.
        manifest = {}
        for row in cases(c):
            budget(c)
            folder = root / "prepared" / row["id"]
            need(all(not (folder / suffix).exists() for suffix in ("corpus", "vectors", "memory")), "consumed")
            corpus.build(folder / "lessons", [], corpus.parse_utc(row["time"]), folder / "corpus")
            encode.build(folder / "corpus", folder / "vectors")
            built = memory.build(folder / "corpus", folder / "vectors", folder / "memory")
            need(built["encoder"] == c["binding"]["encoder"], "pins")
            manifest[row["id"]] = {"snapshot_sha256": file_hash(root / "caseset/snapshots" / ("snap-" + row["id"] + ".json")),
                                   "binding_sha256": blind.digest(c["binding"]), "memory_dir": str(folder / "memory"),
                                   "memory_sha256": blind.tree_sha256(folder / "memory")}
        write_new(root / "PREPARED.json", manifest, c)
    elif phase in ("prepass", "arms"):
        runner, items = native_runner(c, design_sha)
        if phase == "prepass":
            runner.prepass(items)
        else:
            result = runner.arms(items, runner.journal.completed("prepass"), load_json(root / "caseset/commitments.json"))
            arm_files = {p: file_hash(root / p) for p in ("journal/arms/arm_A.json", "journal/arms/arm_B.json")}
            write_new(root / "RUN_MANIFEST.json", {"design_sha256": design_sha,
                "binding_sha256": digest(c["binding"]), "arm_files": arm_files,
                "arm_A_sha256": result["arm_A_sha256"], "arm_B_sha256": result["arm_B_sha256"],
                "go_sha256": digest(go), "prior_receipts": go["prior_receipts"]}, c)
    elif phase == "grade":
        validate_publication(root, go)
        grader = module_from(h["grader"]["path"], "m1_p2_grader")
        result = grader.grade(root / "caseset/cases.json", root / "caseset/commitments.json",
                              root / "journal/arms/arm_A.json", root / "journal/arms/arm_B.json")
        write_new(root / "m1_result.json", result, c)


def worker(design, design_sha, go, go_sha, phase):
    c = validate_design(design, design_sha)
    validate_go(c, design_sha, go, go_sha)
    need(go["phase"] == phase, "worker_intent")
    root = Path(c["root"])
    intent = load_json(root / ".phases" / phase / "INTENT.json")
    need(intent == {"phase": phase, "design_sha256": design_sha, "go_sha256": go_sha,
                    "run_id": c["run_id"], "prior_receipts": go["prior_receipts"]}, "worker_intent")
    need(not (root / ".phases" / phase / "RESULT.json").exists()
         and not (root / ".phases" / phase / "VOID.json").exists()
         and not (root / ".phases" / phase / "WORKER_CONSUMED.json").exists(), "consumed")
    validate_prior(c, go, design_sha)
    validate_native_config(c, phase)
    budget(c)
    # Exclusive worker start prevents replay of a retained ARGV/GO after a crash.
    write_new(root / ".phases" / phase / "WORKER_CONSUMED.json", {"go_sha256": go_sha}, c)
    sys.path.insert(0, str(Path(c["helpers"]["comparator"]["path"]).parent))
    stage = root / ".phases" / phase
    with (stage / "helper.stdout.log").open("x", encoding="utf-8") as out:
        with (stage / "helper.stderr.log").open("x", encoding="utf-8") as err:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                dispatch(c, design_sha, go)
            out.flush()
            err.flush()
            os.fsync(out.fileno())
            os.fsync(err.fileno())


def main(argv=None, stream=None):
    stream = sys.stdout if stream is None else stream
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("command", choices=("execute", "worker", "plan"))
    parser.add_argument("--design", required=True)
    parser.add_argument("--design-sha256", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--go-sha256", required=True)
    parser.add_argument("--phase", choices=PHASES)
    args = parser.parse_args(argv)
    try:
        go = load_json(args.go)
        if args.command == "execute":
            execute(args.design, args.design_sha256, go, args.go_sha256, go_path=args.go)
        elif args.command == "worker":
            # Measured helpers may print aggregate data. Keep even that local.
            worker(args.design, args.design_sha256, go, args.go_sha256, args.phase)
        else:
            c = validate_design(args.design, args.design_sha256)
            validate_go(c, args.design_sha256, go, args.go_sha256)
            phase = go["phase"]
            plans = {phase: phase_argv(args.design, args.design_sha256, args.go, args.go_sha256, phase)}
            json.dump({"status": "PLAN_ONLY", "phase_argv": plans}, stream, sort_keys=True)
            stream.write("\n")
            return 0
        json.dump({"status": "COMPLETE", "phase": go["phase"]}, stream)
        stream.write("\n")
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, Refused) and str(exc) in FIXED_REASONS else "refused"
        json.dump({"status": "NOT_READY", "reason": reason}, stream)
        stream.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

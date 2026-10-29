"""Synthetic-only tests: no frozen Q4 import, model, case, gold or bridge access."""

import ast
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[2] / "sandbox/mind/measurement_tools/m0_lite_hash_judge.py"
WHEN = "2026-01-02T03:04:05.000000Z"


@pytest.fixture
def judge():
    assert SOURCE.is_file(), "MISSING_SPEC_HASH_JUDGE"
    spec = importlib.util.spec_from_file_location("toy_m0_lite_hash_judge", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expected(case_id="p", negative=False, **changes):
    row = dict(id=case_id, expected_ref=None if negative else "toy-lesson",
               expect_abstain=negative, situation_time_utc=WHEN)
    return dict(row, **changes)


def output(case_id="p", status="ANSWER", **changes):
    row = dict(id=case_id, status=status, abstain=status == "ABSTAIN",
               crash=status == "CRASH", degraded=status == "DEGRADED",
               fallback=status == "DEGRADED",
               results=[result()] if status in ("ANSWER", "DEGRADED") else [])
    return dict(row, **changes)


def result(ref="toy-lesson", score=1, **changes):
    return dict(dict(ref=ref, score=score, time_utc=WHEN), **changes)


def raw_inputs(outputs, expectations):
    raw = ("\n".join(json.dumps(row, ensure_ascii=False) for row in outputs) + "\n").encode()
    sealed = json.dumps({"expected": expectations}, ensure_ascii=False).encode()
    return raw, sealed


def run(judge, outputs, expectations):
    return json.loads(judge.judge_bytes(*raw_inputs(outputs, expectations)))


@pytest.mark.parametrize("status,pos,neg", [
    ("ANSWER", "HIT", "MISS"), ("ABSTAIN", "MISS", "ABSTAIN_OK"),
    ("DEGRADED", "DEGRADED_MISS", "DEGRADED_MISS"),
    ("REFUSED", "ERROR", "ERROR"), ("CRASH", "ERROR", "ERROR"),
])
def test_complete_status_map_and_fixed_denominators(judge, status, pos, neg):
    report = run(judge, [output("p", status), output("n", status)],
                 [expected("p"), expected("n", True)])
    rows = {row["id"]: row for row in report["per_case"]}
    assert (rows["p"]["judgment"], rows["n"]["judgment"]) == (pos, neg)
    assert report["fixed_counts"] == {"positives": 1, "negatives": 1}
    assert report["metrics"]["recall_at_3"] == (1 if pos == "HIT" else 0)
    assert report["metrics"]["abstain_specificity"] == (1 if neg == "ABSTAIN_OK" else 0)
    assert report["metrics"]["error_count"] == (2 if pos == "ERROR" else 0)


def test_identical_input_bytes_give_identical_canonical_receipt(judge):
    inputs = raw_inputs([output(), output("n", "ABSTAIN")], [expected(), expected("n", True)])
    first = judge.judge_bytes(*inputs)
    assert first == judge.judge_bytes(*inputs)
    value = json.loads(first)
    assert first == json.dumps(value, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode()
    assert value["inputs"] == {"outputs_sha256": hashlib.sha256(inputs[0]).hexdigest(),
                               "expected_sha256": hashlib.sha256(inputs[1]).hexdigest()}
    assert value["spec_git_blob"] == "aaecab189e770b723a1474575b2d8018be414357"
    assert value["h1_pass"] is True


def test_order_dedup_then_truncate_without_ref_normalization(judge):
    rows = [result("z", 8), result("z", 9), result("toy-lesson", 7), result("a", 9)]
    report = run(judge, [output(results=rows)], [expected()])
    assert report["per_case"][0]["ordered_refs"] == ["a", "z", "toy-lesson"]
    assert report["per_case"][0]["judgment"] == "HIT"
    report = run(judge, [output(results=[result("e\u0301"), result("TOY-LESSON")])],
                 [expected(expected_ref="\u00e9")])
    assert report["per_case"][0]["judgment"] == "MISS"


def test_missing_score_sorts_after_any_supplied_score(judge):
    unscored = result()
    del unscored["score"]
    report = run(judge, [output(results=[unscored, result("c", -100),
                                         result("b", 0), result("a", 0)])], [expected()])
    assert report["per_case"][0]["ordered_refs"] == ["a", "b", "c"]
    assert report["per_case"][0]["judgment"] == "MISS"


@pytest.mark.parametrize("score", [True, False, None, "1", [], {}, float("nan"),
                                  float("inf"), -float("inf")])
def test_bad_score_is_error_not_ordering_fallback(judge, score):
    report = run(judge, [output(results=[result(score=score)])], [expected()])
    assert report["per_case"][0]["judgment"] == "ERROR"
    assert report["metrics"]["recall_at_3"] == 0


@pytest.mark.parametrize("ref", [None, 1, True, [], {}, "\ud800"])
def test_non_utf8_string_reference_is_error(judge, ref):
    # ASCII JSON escaping preserves a malformed surrogate as bytes for the reader to reject.
    raw = json.dumps(output(results=[result(ref=ref)]), ensure_ascii=True).encode() + b"\n"
    sealed = json.dumps({"expected": [expected()]}).encode()
    assert json.loads(judge.judge_bytes(raw, sealed))["per_case"][0]["judgment"] == "ERROR"


@pytest.mark.parametrize("field,value", [
    ("status", "UNKNOWN"), ("status", None), ("abstain", 0), ("abstain", True),
    ("crash", True), ("crash", 0), ("degraded", True), ("degraded", "false"),
    ("fallback", True), ("results", None), ("results", {}),
])
def test_status_types_and_flag_contradictions_fail_closed(judge, field, value):
    assert run(judge, [output(**{field: value})], [expected()])["per_case"][0]["judgment"] == "ERROR"


@pytest.mark.parametrize("field", ["status", "abstain", "crash", "degraded", "results"])
def test_missing_output_field_is_error(judge, field):
    row = output()
    del row[field]
    assert run(judge, [row], [expected()])["per_case"][0]["judgment"] == "ERROR"


@pytest.mark.parametrize("field", ["expected_ref", "expect_abstain", "situation_time_utc"])
def test_missing_expected_field_is_error(judge, field):
    row = expected()
    del row[field]
    assert run(judge, [output()], [row])["per_case"][0]["judgment"] == "ERROR"


@pytest.mark.parametrize("changes", [
    {"expect_abstain": "false"}, {"expect_abstain": 0}, {"expected_ref": 1},
    {"expected_ref": None}, {"expect_abstain": True},
])
def test_malformed_or_contradictory_expected_is_error(judge, changes):
    assert run(judge, [output()], [expected(**changes)])["per_case"][0]["judgment"] == "ERROR"


@pytest.mark.parametrize("time", [None, 1, "yesterday", "2026-01-02T03:04:05",
                                 "2026-01-02T03:04:05.0000001Z"])
@pytest.mark.parametrize("where", ["expected", "result"])
def test_malformed_or_naive_clock_is_error(judge, time, where):
    exp = expected(situation_time_utc=time) if where == "expected" else expected()
    out = output(results=[result(time_utc=time)]) if where == "result" else output()
    report = run(judge, [out], [exp])
    assert report["fixed_counts"]["positives"] == 1
    assert report["per_case"][0]["judgment"] == "ERROR"


def test_instant_comparison_and_canonical_utc_time(judge):
    report = run(judge, [output(results=[result(time_utc="2026-01-02T05:04:05+02:00")])], [expected()])
    assert report["per_case"][0]["judgment"] == "HIT"
    assert report["per_case"][0]["result_times_utc"] == [WHEN]
    assert report["per_case"][0]["situation_time_utc"] == WHEN


def test_future_result_outside_top_three_still_leaks(judge):
    future = result("fourth", -1, time_utc="2026-01-02T03:04:05.000001Z")
    report = run(judge, [output(results=[result(), result("a"), result("b"), future])], [expected()])
    assert report["per_case"][0]["judgment"] == "MISS"
    assert report["per_case"][0]["leak"] is True
    assert report["metrics"]["leak_count"] == 1
    assert report["h1_pass"] is False


@pytest.mark.parametrize("which", ["outputs", "expected"])
def test_duplicate_ids_error_without_denominator_shrink(judge, which):
    outs = [output(), output("n", "ABSTAIN")]
    exps = [expected(), expected("n", True)]
    (outs if which == "outputs" else exps).append((output() if which == "outputs" else expected()))
    report = run(judge, outs, exps)
    assert report["fixed_counts"] == {"positives": 2 if which == "expected" else 1, "negatives": 1}
    assert {r["id"]: r["judgment"] for r in report["per_case"]} == {"n": "ABSTAIN_OK", "p": "ERROR"}
    assert report["metrics"]["recall_at_3"] == 0


def test_missing_and_extra_ids_are_errors_never_new_denominators(judge):
    report = run(judge, [output("extra")], [expected()])
    assert report["fixed_counts"] == {"positives": 1, "negatives": 0}
    assert report["metrics"]["error_count"] == 2
    assert report["metrics"]["abstain_specificity"] is None
    assert all(r["judgment"] == "ERROR" for r in report["per_case"])


@pytest.mark.parametrize("raw,sealed", [
    (b"{\n", b'{"expected":[]}'), (b"\xff\n", b'{"expected":[]}'),
    (b'{}\n', b'{"expected":[],"expected":[]}'),
    (b'{"id":"p","id":"q"}\n', b'{"expected":[]}'),
    (b"", b"[]"), (b"", b'{"expected":{}}'),
    (b"\n", b'{"expected":[]}'),
])
def test_malformed_files_refuse_instead_of_producing_a_passing_judge(judge, raw, sealed):
    with pytest.raises(judge.JudgeRefusal):
        judge.judge_bytes(raw, sealed)


def test_empty_inputs_have_null_metrics_and_no_h1_pass(judge):
    report = json.loads(judge.judge_bytes(b"", b'{"expected":[]}'))
    assert report["metrics"]["recall_at_3"] is None
    assert report["metrics"]["abstain_specificity"] is None
    assert report["h1_pass"] is False


def test_cli_creates_exclusive_audit_outputs_only(judge, tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    raw, sealed = raw_inputs([output()], [expected()])
    outputs, expectations = audit / "toy.outputs.jsonl", audit / "toy.expected.json"
    outputs.write_bytes(raw)
    expectations.write_bytes(sealed)
    out = audit / "new-judge"
    argv = ["--outputs", str(outputs), "--expected", str(expectations), "--out", str(out)]
    assert judge.main(argv) == 0
    body = (out / "judge.json").read_bytes()
    assert (out / "judge.sha256").read_bytes() == (hashlib.sha256(body).hexdigest() + "\n").encode()
    assert body == judge.judge_bytes(raw, sealed)
    with pytest.raises(judge.JudgeRefusal, match="output_exists"):
        judge.main(argv)
    assert (out / "judge.json").read_bytes() == body
    # pytest basetemp is itself audited: tmp_path is NOT a non-audit target.
    # This lexically non-audit target never writes: a removed guard hits the
    # intercepted mkdir and still fails. No read-root/pin/guard is widened.
    def forbidden_mkdir(*args, **kwargs):
        pytest.fail("NON_AUDIT_MKDIR_ATTEMPTED")

    monkeypatch.setattr(Path, "mkdir", forbidden_mkdir)
    non_audit = Path(tmp_path.anchor) / "wd-m0-lite-non-audit-toy"
    with pytest.raises(judge.JudgeRefusal, match="audit_output_required"):
        judge.main(argv[:-1] + [str(non_audit)])


def test_module_has_no_q4_model_network_process_or_bridge_imports():
    assert SOURCE.is_file(), "MISSING_SPEC_HASH_JUDGE"
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert imports <= {"__future__", "argparse", "collections", "datetime", "hashlib",
                       "json", "math", "pathlib", "re", "stat"}


def test_cli_classifies_expected_before_reading_output(judge, tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    outputs, expectations = audit / "toy.outputs.jsonl", audit / "toy.expected.json"
    raw, sealed = raw_inputs([output()], [expected()])
    outputs.write_bytes(raw)
    expectations.write_bytes(sealed)
    classified = []
    real_class, real_read = judge._class, Path.read_bytes

    def classify(row):
        classified.append(row["id"])
        return real_class(row)

    def read(path):
        if path == outputs:
            assert "p" in classified, "EXPECTED_NOT_CLASSIFIED_BEFORE_OUTPUT_READ"
        return real_read(path)

    monkeypatch.setattr(judge, "_class", classify)
    monkeypatch.setattr(Path, "read_bytes", read)
    assert judge.main(["--outputs", str(outputs), "--expected", str(expectations),
                       "--out", str(audit / "new-judge")]) == 0


@pytest.mark.parametrize("filename,reason", [
    ("judge.json", "judge_readback_mismatch"),
    ("judge.sha256", "sha_readback_mismatch"),
])
def test_cli_verifies_exact_written_bytes(judge, tmp_path, monkeypatch, filename, reason):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    outputs, expectations = audit / "toy.outputs.jsonl", audit / "toy.expected.json"
    raw, sealed = raw_inputs([output()], [expected()])
    outputs.write_bytes(raw)
    expectations.write_bytes(sealed)
    out = audit / "new-judge"
    real_open = Path.open

    class CorruptingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def write(self, value):
            return self.stream.write(value + b"CORRUPTED_TOY_WRITE")

        def __exit__(self, *args):
            self.stream.close()

    def open_file(path, mode="r", *args, **kwargs):
        stream = real_open(path, mode, *args, **kwargs)
        return CorruptingWriter(stream) if path == out / filename and mode == "xb" else stream

    monkeypatch.setattr(Path, "open", open_file)
    with pytest.raises(judge.JudgeRefusal, match=reason):
        judge.main(["--outputs", str(outputs), "--expected", str(expectations), "--out", str(out)])
    assert b"CORRUPTED_TOY_WRITE" in (out / filename).read_bytes()
    if filename == "judge.json":
        assert not (out / "judge.sha256").exists(), "No digest after failed judge readback"


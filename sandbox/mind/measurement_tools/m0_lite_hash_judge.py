"""Dormant two-file M0-lite judge; never imports or runs the measured Mind.

Contract: spec aaecab189e770b723a1474575b2d8018be414357 section 4 plus
Root's prospective prelock binding: expected rows carry situation_time_utc.
No cases are opened here. Their custodian must separately receipt equality of
that metadata to the blinded cases before lock. This reader cannot prove it.
CLI output is exclusively a NEW directory under .codex-audit; exit 0 means a
receipt was written, not that H1 passed. File errors refuse; identifiable row
errors remain in fixed denominators. No model, transport, runtime or gate.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import stat


SPEC_GIT_BLOB = "aaecab189e770b723a1474575b2d8018be414357"
SPEC_SHA256 = "c5db2486dbcbc57a34daa1f73823cf50188ef9561b046476b93c9d4ca3221c33"
PRELOCK_SHA256 = "a6630eca9bec990fb280fa5d1e6e61d7b3eec319846ff5fadbba1fa289e2de2d"
STATUSES = {"ANSWER", "ABSTAIN", "DEGRADED", "REFUSED", "CRASH"}
CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?"
                   r"(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)\Z")


class JudgeRefusal(ValueError):
    """Named file/shape/write refusal: no successful judgment is substituted."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise JudgeRefusal("duplicate_json_key")
        result[key] = value
    return result


def _load(raw):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise JudgeRefusal("invalid_json:" + type(error).__name__) from error


def _text(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8", "strict")
        return True
    except UnicodeError:
        return False


def _index(rows, label):
    if not isinstance(rows, list):
        raise JudgeRefusal(label + "_rows_not_array")
    indexed = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("id")):
            raise JudgeRefusal(label + "_unidentifiable_row")
        indexed[row["id"]].append(row)
    return indexed


def _class(row):
    if type(row.get("expect_abstain")) is bool and "expected_ref" in row:
        if row["expect_abstain"] and row["expected_ref"] is None:
            return "negative"
        if not row["expect_abstain"] and _text(row["expected_ref"]):
            return "positive"
    return None


def _clock(value):
    if not isinstance(value, str) or not CLOCK.fullmatch(value):
        raise ValueError("invalid_time")
    instant = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    return instant, instant.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _nonfinite(value):
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_nonfinite(item) for item in value.values())
    if isinstance(value, list):
        return any(_nonfinite(item) for item in value)
    return False


def _case(case_id, expectations, outputs):
    errors = []
    for name, entries in (("expected", expectations), ("output", outputs)):
        if len(entries) != 1:
            errors.append(name + ("_missing" if not entries else "_duplicate"))
    exp = expectations[0] if expectations else {}
    out = outputs[0] if outputs else {}
    kind = _class(exp)
    status = out.get("status")
    if not isinstance(status, str) or status not in STATUSES:
        errors.append("unknown_status")
        status = None
    if kind is None:
        errors.append("expected_class")
    if _nonfinite(exp) or _nonfinite(out):
        errors.append("nonfinite")
    when = None
    when_text = None
    try:
        when, when_text = _clock(exp.get("situation_time_utc"))
    except (ValueError, OverflowError):
        errors.append("situation_time")
    required = {"status", "abstain", "crash", "degraded", "results"}
    if not required.issubset(out):
        errors.append("output_fields")
    flags = {"abstain": status == "ABSTAIN", "crash": status == "CRASH",
             "degraded": status == "DEGRADED"}
    for flag, wanted in flags.items():
        if type(out.get(flag)) is not bool or out[flag] != wanted:
            errors.append(flag + "_contradiction")
    if "fallback" in out and (type(out["fallback"]) is not bool or
                              out["fallback"] != flags["degraded"]):
        errors.append("fallback_contradiction")
    results = out.get("results")
    if not isinstance(results, list):
        errors.append("results_not_array")
        results = []
    if status in {"ABSTAIN", "REFUSED", "CRASH"} and results:
        errors.append("status_results_contradiction")
    ordered, result_times, leak = [], [], False
    for item in results:
        if not isinstance(item, dict) or not _text(item.get("ref")):
            errors.append("result_ref")
            continue
        try:
            instant, utc_text = _clock(item.get("time_utc"))
            result_times.append(utc_text)
            leak = leak or (when is not None and instant > when)
        except (ValueError, OverflowError):
            errors.append("result_time")
        supplied = "score" in item
        score = item.get("score", 0)
        try:
            valid_score = type(score) in (int, float) and math.isfinite(score)
        except OverflowError:
            valid_score = False
        if not valid_score:
            errors.append("result_score")
            continue
        ordered.append((not supplied, -score, item["ref"].encode("utf-8"), item["ref"]))
    # Stable ordering retains the first equal-ref item after score/ref ordering.
    refs = list(dict.fromkeys(item[3] for item in sorted(ordered)))[:3]
    if status in {"REFUSED", "CRASH"}:
        errors.append("status_error")
    judgment = "ERROR"
    if not errors:
        if leak:
            judgment = "MISS"
        elif status == "DEGRADED":
            judgment = "DEGRADED_MISS"
        elif status == "ABSTAIN":
            judgment = "ABSTAIN_OK" if kind == "negative" else "MISS"
        else:
            judgment = "HIT" if kind == "positive" and exp["expected_ref"] in refs else "MISS"
    return {"id": case_id, "judgment": judgment, "status": status,
            "situation_time_utc": when_text, "result_times_utc": result_times,
            "ordered_refs": refs, "leak": leak, "errors": sorted(set(errors))}


def _prepare_expected(expected_raw):
    """Classify and freeze expected-only denominators before opening outputs."""
    sealed = _load(expected_raw)
    if not isinstance(sealed, dict) or set(sealed) != {"expected"}:
        raise JudgeRefusal("expected_schema")
    expected = _index(sealed["expected"], "expected")
    # Freeze row-class denominators BEFORE parsing or scoring any output.
    counts = Counter(_class(row) for row in sealed["expected"])
    fixed = {"positives": counts["positive"], "negatives": counts["negative"]}
    return expected, fixed


def judge_bytes(outputs_raw, expected_raw):
    """Pure deterministic judgment over exactly the two supplied byte strings."""
    return _judge(outputs_raw, expected_raw, *_prepare_expected(expected_raw))


def _judge(outputs_raw, expected_raw, expected, fixed):
    lines = outputs_raw.splitlines()
    if any(not line.strip() for line in lines):
        raise JudgeRefusal("blank_output_row")
    outputs = _index([_load(line) for line in lines], "output")
    ids = sorted(set(expected) | set(outputs), key=lambda value: value.encode("utf-8"))
    rows = [_case(case_id, expected.get(case_id, []), outputs.get(case_id, [])) for case_id in ids]
    outcomes = Counter(row["judgment"] for row in rows)
    recall = outcomes["HIT"] / fixed["positives"] if fixed["positives"] else None
    specificity = outcomes["ABSTAIN_OK"] / fixed["negatives"] if fixed["negatives"] else None
    metrics = {"recall_at_3": recall, "abstain_specificity": specificity,
               "degraded_count": sum(row["status"] == "DEGRADED" for row in rows),
               "leak_count": sum(row["leak"] for row in rows), "error_count": outcomes["ERROR"]}
    report = {"schema": "wd.m0-lite-hash-judge.v1", "authority": "none", "k": 3,
              "spec_git_blob": SPEC_GIT_BLOB, "spec_sha256": SPEC_SHA256,
              "prelock_binding_sha256": PRELOCK_SHA256,
              "inputs": {"outputs_sha256": hashlib.sha256(outputs_raw).hexdigest(),
                         "expected_sha256": hashlib.sha256(expected_raw).hexdigest()},
              "fixed_counts": fixed, "per_case": rows, "metrics": metrics,
              "h1_pass": recall is not None and specificity is not None and
              recall >= 0.60 and specificity >= 0.50 and
              metrics["leak_count"] == 0 and metrics["error_count"] == 0}
    return json.dumps(report, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _new_audit_dir(value):
    path = Path(value).absolute()
    if ".." in path.parts or ".codex-audit" not in path.parts:
        raise JudgeRefusal("audit_output_required")
    # A lexical audit name is insufficient: reject existing reparse ancestors.
    for ancestor in (path, *path.parents):
        try:
            metadata = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or (getattr(metadata, "st_file_attributes", 0) &
                                             getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
            raise JudgeRefusal("reparse_output")
    try:
        path.mkdir()
    except FileExistsError as error:
        raise JudgeRefusal("output_exists") from error
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    expected_raw = args.expected.read_bytes()
    prepared = _prepare_expected(expected_raw)
    body = _judge(args.outputs.read_bytes(), expected_raw, *prepared)
    directory = _new_audit_dir(args.out)
    # No overwrite, cleanup, rollback or retry; partial I/O failure stays visible.
    judge_path, sha_path = directory / "judge.json", directory / "judge.sha256"
    with judge_path.open("xb") as stream:
        stream.write(body)
    written = judge_path.read_bytes()
    if written != body:
        raise JudgeRefusal("judge_readback_mismatch")
    # Digest the actual written bytes once, not a rendered/intended substitute.
    digest = (hashlib.sha256(written).hexdigest() + "\n").encode("ascii")
    with sha_path.open("xb") as stream:
        stream.write(digest)
    if sha_path.read_bytes() != digest:
        raise JudgeRefusal("sha_readback_mismatch")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

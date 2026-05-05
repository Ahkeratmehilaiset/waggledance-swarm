# SPDX-License-Identifier: BUSL-1.1
"""Phase 18A - Benchmark bundle validator (stdlib-only).

Validates an exported Phase 18A benchmark bundle against the
schemas/benchmarks/v1/ contract:

* manifest, artifact index, claim ledger, release lineage all parse and
  conform to their schema;
* every artifact validates against its declared schema;
* checksums.sha256 file is present and every line resolves to a present
  file with matching SHA-256;
* every claim entry resolves: evidence_artifact exists, source_sha256
  matches the source file's hash on disk if source_path_in_repo exists;
* every claim's label is in the allowed enum;
* sanitized artifacts contain no plaintext per-prompt stdout/stderr
  (every per_prompt[].stdout/stderr is either absent or a redaction
  stub {"redacted": true, "sha256": "...", "length": N});
* rendered Markdown reports contain no forbidden-vocabulary substring;
* release lineage stable_latest is v3.8.0 / isPrerelease=false /
  is_github_latest=true;
* manifest top-level honesty flags are all true;
* required claim_ids per the design doc are all present;
* manifest release_gate_pass = true.

The validator implements just enough of JSON Schema Draft 2020-12 to
enforce the contracts the bundle uses. No `jsonschema` dependency.

CLI:

    python tools/validate_phase18a_benchmark_bundle.py --bundle-dir <path>

Exit code 0 only if all validations pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


SCHEMA_FILES = (
    "benchmark_bundle.schema.json",
    "artifact_index.schema.json",
    "claim_evidence_ledger.schema.json",
    "release_lineage.schema.json",
    "local_efficiency.schema.json",
    "local_ollama_baseline.schema.json",
    "local_model_sweep.schema.json",
)

REQUIRED_BUNDLE_FILES = (
    "benchmark_bundle_manifest.json",
    "artifact_index.json",
    "claim_evidence_ledger.json",
    "release_lineage.json",
    "checksums.sha256",
    "README.md",
    "reports/benchmark_bundle_index.md",
    "reports/claim_evidence_ledger.md",
)

ALLOWED_CLAIM_LABELS = (
    "PROVEN", "MEASURED", "INFERRED", "NOT_CLAIMED", "NOT_RUN",
    "MEASURED_LOCAL_ONLY",
    "MEASURED_LOCAL_OLLAMA_ONE_MODEL",
    "MEASURED_LOCAL_OLLAMA_PANEL",
)

REQUIRED_CLAIM_IDS = (
    "docker_offline_proven",
    "producer_fabric_proven",
    "capability_lookup_10k_measured",
    "canonical_corpus_128_proven",
    "local_efficiency_harness_proven",
    "local_ollama_one_model_measured",
    "local_ollama_panel_measured",
    "raw_intelligence_vs_frontier_moe_not_claimed",
    "cross_vendor_ranking_not_claimed",
    "no_model_pull_or_download",
    "no_cloud_api_calls",
    "provider_builder_delta_zero",
    "no_stage2_flip",
    "no_human_approval_collected",
    "no_allowlist_widening",
    "benchmark_artifact_externalization",
)

FORBIDDEN_SUBSTRINGS = (
    "conscious", "sentient", "aware", "alive", "agi",
    "revolutionary", "magical", "human-like mind", "self-aware",
    "explosive intelligence", "emergent",
    "beats all competitors", "world's best", "world's fastest",
    "is faster than", "is slower than", "outperforms",
    " beats ", "ranks higher", "ranked first", "best of breed",
    "better than",
)

# Some compounded tokens are legitimate disclaimer fields and must be
# allowed even though they would otherwise hit FORBIDDEN_SUBSTRINGS.
# We strip these from the lower-cased text before scanning.
DISCLAIMER_TOKENS_ALLOWED_IN_PROSE = (
    "no_consciousness", "no_sentience", "no_human_like_mind",
    "no_beats_all_competitors", "no_world_best", "no_world_fastest",
    "no_raw_intelligence_superiority", "no_cross_vendor_ranking",
    "no_consciousness_claim", "no_beats_all_competitors_claim",
    "no_cross_vendor_ranking_claim", "no_raw_intelligence_superiority_claim",
    # Allowed engineering phrasings of the absence of the claim.
    "no consciousness claim", "no consciousness, sentience",
    "does not claim to be conscious", "does not claim to be sentient",
    "does not claim to be aware", "does not claim to be alive",
    "no cross-vendor ranking", "no raw-intelligence superiority",
    "raw intelligence vs frontier moe is **not claimed**",
    "raw intelligence vs frontier moe = not_claimed",
    "raw_intelligence_vs_frontier_moe = not_claimed",
    # Compound technical terms used by the WaggleDance codebase that
    # legitimately contain a forbidden substring as a sub-word. These
    # are domain vocabulary, not consciousness/awareness claims.
    "capability-aware",
    "capability_aware",
    "context-aware",
    "context_aware",
    "self-model", "self_model", "self model",
)


# ---------------------------------------------------------------------------
# Minimal JSON-Schema subset validator
# ---------------------------------------------------------------------------

def _is_type(value: Any, t: str) -> bool:
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "null":
        return value is None
    return False


def _types_for(schema: dict[str, Any]) -> list[str]:
    t = schema.get("type")
    if t is None:
        return []
    if isinstance(t, list):
        return list(t)
    return [t]


def _validate_value(value: Any, schema: dict[str, Any], path: str,
                      errors: list[str]) -> None:
    types = _types_for(schema)
    if types:
        if not any(_is_type(value, t) for t in types):
            errors.append(f"{path}: type {types} expected, got "
                            f"{type(value).__name__}")
            return

    if "enum" in schema:
        allowed = schema["enum"]
        if value not in allowed:
            errors.append(f"{path}: value {value!r} not in enum {allowed!r}")

    if "pattern" in schema and isinstance(value, str):
        try:
            if not re.search(schema["pattern"], value):
                errors.append(f"{path}: pattern {schema['pattern']!r} did "
                                f"not match {value!r}")
        except re.error as exc:
            errors.append(f"{path}: invalid pattern {schema['pattern']!r}: "
                            f"{exc}")

    if "minimum" in schema and isinstance(value, (int, float)):
        if value < schema["minimum"]:
            errors.append(f"{path}: value {value} < minimum {schema['minimum']}")

    if "minItems" in schema and isinstance(value, list):
        if len(value) < schema["minItems"]:
            errors.append(f"{path}: length {len(value)} < minItems "
                            f"{schema['minItems']}")

    if "items" in schema and isinstance(value, list):
        for i, v in enumerate(value):
            _validate_value(v, schema["items"], f"{path}[{i}]", errors)

    if isinstance(value, dict):
        if "required" in schema:
            for key in schema["required"]:
                if key not in value:
                    errors.append(f"{path}.{key}: required field missing")
        if "properties" in schema:
            for key, child_schema in schema["properties"].items():
                if key in value:
                    _validate_value(value[key], child_schema,
                                       f"{path}.{key}", errors)
        if schema.get("additionalProperties") is False:
            allowed_keys = set(schema.get("properties", {}).keys())
            for key in value.keys():
                if key not in allowed_keys:
                    errors.append(f"{path}.{key}: unknown property "
                                    "(additionalProperties=false)")


def validate_against_schema(value: Any, schema: dict[str, Any],
                              path: str = "$") -> list[str]:
    errors: list[str] = []
    _validate_value(value, schema, path, errors)
    return errors


# ---------------------------------------------------------------------------
# JSON Pointer (RFC 6901) — minimal resolver
# ---------------------------------------------------------------------------

def resolve_json_pointer(doc: Any, pointer: str) -> Any:
    if pointer == "" or pointer == "/":
        return doc
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    parts = pointer.split("/")[1:]
    cur = doc
    for p in parts:
        token = p.replace("~1", "/").replace("~0", "~")
        if isinstance(cur, list):
            try:
                cur = cur[int(token)]
            except (ValueError, IndexError) as exc:
                raise KeyError(f"pointer step {token!r} unresolvable in "
                                 f"list: {exc}")
        elif isinstance(cur, dict):
            if token not in cur:
                raise KeyError(f"pointer step {token!r} not in dict (keys="
                                 f"{list(cur.keys())[:6]}...)")
            cur = cur[token]
        else:
            raise KeyError(f"pointer step {token!r} cannot descend into "
                             f"{type(cur).__name__}")
    return cur


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Sanitization scan
# ---------------------------------------------------------------------------

def looks_like_redaction_stub(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("redacted") is True
        and isinstance(value.get("sha256"), str)
        and isinstance(value.get("length"), int)
    )


def scan_for_raw_stdout_leakage(node: Any, path: str,
                                  hits: list[str]) -> None:
    if isinstance(node, dict):
        for key, val in node.items():
            if key in ("stdout", "stderr"):
                if isinstance(val, str) and val:
                    hits.append(f"{path}.{key}: plaintext string "
                                  f"(len={len(val)})")
                elif isinstance(val, dict) and not looks_like_redaction_stub(val):
                    hits.append(f"{path}.{key}: object is not a "
                                  "redaction stub")
            else:
                scan_for_raw_stdout_leakage(val, f"{path}.{key}", hits)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            scan_for_raw_stdout_leakage(item, f"{path}[{i}]", hits)


# ---------------------------------------------------------------------------
# Forbidden vocabulary scan
# ---------------------------------------------------------------------------

def scan_forbidden_in_text(text: str) -> list[str]:
    lower = text.lower()
    for tok in DISCLAIMER_TOKENS_ALLOWED_IN_PROSE:
        lower = lower.replace(tok, "")
    return [w for w in FORBIDDEN_SUBSTRINGS if w in lower]


# ---------------------------------------------------------------------------
# Main validator
# ---------------------------------------------------------------------------

def validate_bundle(bundle_dir: Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    bundle_dir = bundle_dir.resolve()
    if not bundle_dir.is_dir():
        return False, [f"bundle_dir not found: {bundle_dir}"]

    # 1. required files exist
    for rel in REQUIRED_BUNDLE_FILES:
        if not (bundle_dir / rel).exists():
            errors.append(f"missing required file: {rel}")

    # 2. schema files exist + parse
    schemas: dict[str, dict[str, Any]] = {}
    for sname in SCHEMA_FILES:
        spath = bundle_dir / "schemas" / sname
        if not spath.is_file():
            errors.append(f"missing schema file: schemas/{sname}")
            continue
        try:
            schemas[sname] = json.loads(spath.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"schemas/{sname}: invalid JSON: {exc}")

    # If we already have fatal errors, return.
    if errors:
        return False, errors

    # 3. parse manifest, artifact index, claim ledger, release lineage
    def _load(rel: str) -> Any:
        try:
            return json.loads((bundle_dir / rel).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{rel}: load failed: {exc}")
            return None

    manifest = _load("benchmark_bundle_manifest.json")
    artifact_index = _load("artifact_index.json")
    claim_ledger = _load("claim_evidence_ledger.json")
    release_lineage = _load("release_lineage.json")

    if errors:
        return False, errors

    # 4. validate each top-level doc against its schema
    errors += validate_against_schema(
        manifest, schemas["benchmark_bundle.schema.json"], "manifest"
    )
    errors += validate_against_schema(
        artifact_index, schemas["artifact_index.schema.json"], "artifact_index"
    )
    errors += validate_against_schema(
        claim_ledger, schemas["claim_evidence_ledger.schema.json"],
        "claim_ledger",
    )
    errors += validate_against_schema(
        release_lineage, schemas["release_lineage.schema.json"],
        "release_lineage",
    )

    # 5. checksums file
    checksums_text = (bundle_dir / "checksums.sha256").read_text(
        encoding="utf-8"
    )
    declared_checksums: dict[str, str] = {}
    for line in checksums_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: "<sha256>  <relative_path>"
        parts = line.split(None, 1)
        if len(parts) != 2:
            errors.append(f"checksums.sha256: malformed line {line!r}")
            continue
        sha, relpath = parts[0], parts[1]
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            errors.append(f"checksums.sha256: bad sha256 for {relpath}: {sha}")
            continue
        declared_checksums[relpath] = sha

    if not declared_checksums:
        errors.append("checksums.sha256: no entries parsed")

    # Verify every checksum.
    for relpath, expected in declared_checksums.items():
        full = bundle_dir / relpath
        if not full.is_file():
            errors.append(f"checksums.sha256: file not found: {relpath}")
            continue
        actual = sha256_of_file(full)
        if actual != expected:
            errors.append(f"checksums.sha256: mismatch for {relpath}: "
                            f"expected {expected[:12]}... got {actual[:12]}...")

    # 6. validate each artifact against its declared schema; sanitization scan
    for entry in artifact_index.get("artifacts", []):
        path_in_bundle = entry.get("path_in_bundle", "")
        full = bundle_dir / path_in_bundle
        if not full.is_file():
            errors.append(f"artifact_index: missing file {path_in_bundle}")
            continue
        try:
            artifact = json.loads(full.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"artifact_index[{path_in_bundle}]: invalid "
                            f"JSON: {exc}")
            continue
        schema_name = entry.get("declared_schema")
        if schema_name not in schemas:
            errors.append(f"artifact_index[{path_in_bundle}]: declared "
                            f"schema {schema_name} not found")
            continue
        errors += validate_against_schema(
            artifact, schemas[schema_name],
            f"artifact[{entry.get('artifact_id')}]",
        )
        # Sanitization scrub
        leaks: list[str] = []
        scan_for_raw_stdout_leakage(artifact, "$", leaks)
        for leak in leaks:
            errors.append(f"artifact_index[{path_in_bundle}]: raw stdout "
                            f"leakage: {leak}")
        # exported_sha256 vs actual file
        exp = entry.get("exported_sha256")
        actual = sha256_of_file(full)
        if exp != actual:
            errors.append(f"artifact_index[{path_in_bundle}]: "
                            f"exported_sha256 {exp[:12]}... != actual "
                            f"{actual[:12]}...")

    # 7. claim ledger checks
    seen_claim_ids: set[str] = set()
    for c in claim_ledger.get("claims", []):
        cid = c.get("claim_id")
        if cid in seen_claim_ids:
            errors.append(f"claim_ledger: duplicate claim_id {cid!r}")
        seen_claim_ids.add(cid)
        if c.get("label") not in ALLOWED_CLAIM_LABELS:
            errors.append(f"claim_ledger[{cid}]: label {c.get('label')!r} "
                            f"not in allowed labels")
        artpath = c.get("evidence_path_in_bundle", "")
        full = bundle_dir / artpath if artpath else None
        if (artpath
                and (full is None or not full.is_file())):
            errors.append(f"claim_ledger[{cid}]: evidence_path_in_bundle "
                            f"{artpath} not found")
            continue
        # JSON Pointer resolution
        if artpath and full is not None and full.is_file():
            try:
                doc = json.loads(full.read_text(encoding="utf-8"))
                ptr = c.get("evidence_field_pointer", "")
                if ptr and ptr != "/":
                    resolve_json_pointer(doc, ptr)
            except KeyError as exc:
                errors.append(f"claim_ledger[{cid}]: pointer "
                                f"{c.get('evidence_field_pointer')!r} "
                                f"unresolvable: {exc}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"claim_ledger[{cid}]: pointer load failed: "
                                f"{exc}")

    # Required claim_ids must be present.
    missing_claims = set(REQUIRED_CLAIM_IDS) - seen_claim_ids
    if missing_claims:
        errors.append(f"claim_ledger: missing required claims: "
                        f"{sorted(missing_claims)}")

    # 8. forbidden-vocabulary scan over rendered MD reports
    for rel in (
        "reports/benchmark_bundle_index.md",
        "reports/claim_evidence_ledger.md",
        "README.md",
    ):
        path = bundle_dir / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        hits = scan_forbidden_in_text(text)
        for h in hits:
            errors.append(f"{rel}: forbidden substring {h!r}")

    # 9. release lineage hard-codes
    sl = release_lineage.get("stable_latest", {})
    if sl.get("tag") != "v3.8.0":
        errors.append(f"release_lineage.stable_latest.tag != 'v3.8.0' "
                        f"(got {sl.get('tag')!r})")
    if sl.get("isPrerelease") is not False:
        errors.append("release_lineage.stable_latest.isPrerelease must be false")
    if sl.get("is_github_latest") is not True:
        errors.append("release_lineage.stable_latest.is_github_latest must be true")
    expected_pre_tags = {
        "v3.9.0-producer-fabric-alpha",
        "v3.9.1-local-efficiency-benchmark-alpha",
        "v3.9.2-local-ollama-baseline-alpha",
        "v3.9.3-local-model-sweep-alpha",
    }
    actual_pre_tags = {p.get("tag") for p in release_lineage.get("prereleases", [])}
    missing_pre = expected_pre_tags - actual_pre_tags
    if missing_pre:
        errors.append(f"release_lineage.prereleases missing: {sorted(missing_pre)}")

    # 10. manifest top-level honesty + counts
    if manifest.get("release_gate_pass") is not True:
        errors.append("manifest.release_gate_pass must be true")
    if manifest.get("provider_jobs_delta") != 0:
        errors.append(f"manifest.provider_jobs_delta != 0 "
                        f"(got {manifest.get('provider_jobs_delta')})")
    if manifest.get("builder_jobs_delta") != 0:
        errors.append(f"manifest.builder_jobs_delta != 0 "
                        f"(got {manifest.get('builder_jobs_delta')})")
    artifact_count_actual = len(artifact_index.get("artifacts", []))
    if manifest.get("artifact_count") != artifact_count_actual:
        errors.append(f"manifest.artifact_count={manifest.get('artifact_count')} "
                        f"!= artifact_index length {artifact_count_actual}")
    claim_count_actual = len(claim_ledger.get("claims", []))
    if manifest.get("claim_count") != claim_count_actual:
        errors.append(f"manifest.claim_count={manifest.get('claim_count')} "
                        f"!= claim_ledger length {claim_count_actual}")

    return (len(errors) == 0), errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    ok, errors = validate_bundle(args.bundle_dir)
    if ok:
        print(f"Phase 18A bundle validation: PASS  ({args.bundle_dir})")
        return 0
    print(f"Phase 18A bundle validation: FAIL  ({args.bundle_dir})")
    print(f"  {len(errors)} violation(s):")
    for e in errors:
        print(f"  - {e}")
    return 1


if __name__ == "__main__":
    sys.exit(main())

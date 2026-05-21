from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.run_v12_supervisor_demo_pack import build_demo_pack
from tools.verify_v12_demo_pack_artifact_manifest import verify_artifact_manifest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "verify_v12_demo_pack_artifact_manifest.py"


def _run_verify(manifest: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(manifest), *extra],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_pack(tmp_path: Path) -> Path:
    out_dir = tmp_path / "demo-pack"
    build_demo_pack(out_dir=out_dir, now_utc=_fixed_now())
    return out_dir / "demo_pack_artifact_manifest.json"


def test_verifies_generated_demo_pack_manifest(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)

    report = verify_artifact_manifest(manifest)

    assert report["ok"] is True
    assert report["manifest"] == "<redacted>"
    assert report["pack_dir"] == "<redacted>"
    assert report["manifest_version"] == "wd.v12.supervisor_demo_pack.artifact_manifest.v0"
    assert report["demo_version"] == "wd.v12.supervisor_demo_pack.v0"
    assert report["file_count"] > 0
    assert report["verified_file_count"] == report["file_count"]
    assert report["errors"] == []


def test_cli_reports_json_without_leaking_paths(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)

    result = _run_verify(manifest, "--json")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["ok"] is True
    assert report["verified_file_count"] == report["file_count"]
    assert str(tmp_path) not in result.stdout
    assert "demo_pack_artifact_manifest.json" not in result.stdout


def test_cli_detects_tampered_artifact(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)
    summary = manifest.parent / "summary.md"
    summary.write_text(summary.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "sha256 mismatch: summary.md" in result.stderr
    assert "size_bytes mismatch: summary.md" in result.stderr


def test_cli_detects_missing_artifact(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)
    (manifest.parent / "rival_local_check_matrix.md").unlink()

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "missing file: rival_local_check_matrix.md" in result.stderr


def test_cli_detects_unlisted_extra_artifact(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)
    (manifest.parent / "extra.txt").write_text("not in manifest\n", encoding="utf-8")

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "unexpected file not listed: extra.txt" in result.stderr


def test_cli_rejects_manifest_path_traversal(tmp_path: Path) -> None:
    manifest = _build_pack(tmp_path)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../outside.txt"
    _write_json(manifest, payload)

    result = _run_verify(manifest)

    assert result.returncode == 1
    assert "unsafe relative path" in result.stderr


def _fixed_now() -> datetime:
    return datetime(2026, 5, 20, 18, 50, tzinfo=timezone.utc)

"""Synthetic check that the default-off v3 publisher's local imports ship together.

Package membership alone is not installation, activation or evidence of a live
publisher. No production runtime or bridge data is read by this test.
"""
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REQUIRED = (
    "tools/wd_mind_public_publish_v3_cli.py",
    "tools/wd_mind_public_publish_cli.py",
    "tools/wd_mind_public_session.py",
    "tools/wd_mind_public_session_cli.py",
    "tools/wd_mind_public_series.py",
    "sandbox/mind/step2/shared_bridge_snapshot.py",
    "sandbox/mind/step2/shared_capture_adapter.py",
    "sandbox/mind/step2/status_metadata_producer.py",
    "sandbox/mind/step2/shared_projection.py",
    "sandbox/mind/step2/reader_slot.py",
    "sandbox/mind/mind_capture_producer.py",
    "sandbox/mind/step2/corpus.py",
    "tools/bridge_compact_view.py",
    "waggledance/core/bridge_log_reader.py",
)


def test_v3_publisher_and_mandatory_local_imports_are_packaged_once():
    definition = json.loads((ROOT / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    files = definition["python_files"]
    assert isinstance(files, list)
    missing_or_duplicate = {item: files.count(item) for item in REQUIRED if files.count(item) != 1}
    absent_sources = [item for item in REQUIRED if not (ROOT / item).is_file()]
    assert missing_or_duplicate == {} and absent_sources == [], (missing_or_duplicate, absent_sources)

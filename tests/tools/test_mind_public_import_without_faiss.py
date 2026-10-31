"""Public announcement must not require model libraries at import time."""
import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_isolated_stdlib_import_and_synthetic_announcement():
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from sandbox.mind.step2 import shared_public_pointer_announce as ann
from sandbox.mind.step2 import shared_public_producer as pub
assert 'faiss' not in sys.modules and 'numpy' not in sys.modules
assert pub.publish_public_generation() == {'status': 'OFF'}
artifacts = {key: 'b' * 64 for key in ('descriptor_sha256','memory_sha256','projection_sha256','receipt_sha256')}
report = pub._report('PUBLISHED_GENESIS',generation='gen-a',pointer_sha256='a'*64,artifacts=artifacts,records=1,public_policy_sha256='c'*64)
payload = ann.announcement_payload(report, 'd'*64, enabled=True)
assert payload['generation']=='gen-a' and payload['authenticity']=='UNKNOWN'
assert ann._generation_ok('gen-a') and not ann._generation_ok('..') and not ann._generation_ok('UPPER')
"""
    run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code, str(ROOT)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr


def test_checkpoint_name_validation_matches_builder_without_importing_builder():
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint

    source = (ROOT / "sandbox/mind/step2/shared_index_builder.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    patterns = [ast.literal_eval(node.value.args[0]) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "MEMORY_NAME" for target in node.targets)
                and isinstance(node.value, ast.Call)]
    assert len(patterns) == 1
    # Builder uses fullmatch, so an explicit end anchor is equivalent.
    assert checkpoint.NAME.pattern == patterns[0] + r"\Z"

"""The fixed C2 input validator must actually ship with the reboot bundle."""
import json
import subprocess
import sys

from test_card_package_closure import staged  # noqa: F401


def test_staged_bundle_can_import_fixed_backend_without_activating_it(staged):
    script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
              "from tools.wd_q4_staged_fixed_backend import validate_card_input;"
              "print(json.dumps(validate_card_input(None,None,None)))")
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(staged)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'OFF'}

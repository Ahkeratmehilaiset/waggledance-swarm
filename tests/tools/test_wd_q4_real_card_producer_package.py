"""The reboot bundle contains the default-off real-format card producer."""
import json
import subprocess
import sys

from test_card_package_closure import staged  # noqa: F401


def test_bundle_imports_real_card_producer_without_model_or_live_data(staged):
    script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
              "from tools.wd_q4_real_card_snapshot_producer import produce;"
              "print(json.dumps(produce()))")
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(staged)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'OFF'}

"""A staged reboot package can import the default-off card plan writer."""
import json
import subprocess
import sys

from test_card_package_closure import staged  # noqa: F401


def test_staged_package_imports_the_card_plan_writer_without_live_data(staged):
    script = ("import json,sys;sys.path.insert(0,sys.argv[1]);"
              "from tools.wd_q4_public_card_plan_writer import write_card_plan;"
              "print(json.dumps(write_card_plan()))")
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', script, str(staged)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'status': 'OFF'}

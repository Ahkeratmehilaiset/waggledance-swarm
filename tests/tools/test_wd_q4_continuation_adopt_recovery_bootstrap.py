import json
import subprocess
import sys
from pathlib import Path

BOOTSTRAP=Path('tools/wd_q4_continuation_adopt_recovery_bootstrap.py').resolve()

def test_default_off_in_isolated_process():
    p=subprocess.run([sys.executable,'-I','-S','-B',str(BOOTSTRAP)],capture_output=True,text=True,timeout=15)
    assert p.returncode==0 and json.loads(p.stdout)=={'status':'OFF'} and not p.stderr

def test_unpinned_config_refused_before_module_import(tmp_path):
    p=subprocess.run([sys.executable,'-I','-S','-B',str(BOOTSTRAP),'--enable','--config',str(tmp_path/'missing'),
      '--sha256','a'*64,'--manifest-sha256','b'*64,'--runtime',str(tmp_path/'missing2'),'--runtime-sha256','c'*64],
      capture_output=True,text=True,timeout=15)
    assert p.returncode==2 and json.loads(p.stdout)=={'status':'REFUSED'} and not p.stderr

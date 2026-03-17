# Legacy script-style test files that should not be collected by pytest.
# These files use custom test runners (OK/FAIL functions) and are run
# via `python tools/waggle_backup.py --tests-only` instead.
from pathlib import Path

_dir = Path(__file__).parent
collect_ignore = [
    str(_dir / "test_all.py"),
    str(_dir / "test_normalizer.py"),
]

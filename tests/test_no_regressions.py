"""Regression guard tests — verify critical fixes stay fixed."""
from pathlib import Path


def test_requirements_has_sklearn():
    reqs = Path("requirements.txt").read_text()
    assert "scikit-learn" in reqs


def test_requirements_has_prometheus():
    reqs = Path("requirements.txt").read_text()
    assert "prometheus-client" in reqs


def test_requirements_has_joblib():
    reqs = Path("requirements.txt").read_text()
    assert "joblib" in reqs


def test_no_raw_eval_anywhere():
    """No raw eval() in production code (outside tests and safe_eval itself).

    Excludes:
      - safe_eval() calls (our sandboxed evaluator)
      - model.eval() (PyTorch evaluation mode)
      - words containing 'eval' like 'retrieval', 'evaluate'
      - Lines with # noqa comments
    """
    import re
    production_dirs = [Path("core"), Path("waggledance")]
    violations = []
    for d in production_dirs:
        if not d.exists():
            continue
        for py_file in d.rglob("*.py"):
            if "safe_eval" in py_file.name:
                continue
            if "test_" in py_file.name:
                continue
            for i, line in enumerate(py_file.read_text(errors="ignore").splitlines(), 1):
                if "# noqa" in line:
                    continue
                # Match standalone eval( — not safe_eval(, .eval(, or *eval( inside words
                if re.search(r'(?<!\w)(?<!safe_)(?<!\.)eval\(', line):
                    violations.append(f"{py_file}:{i}: {line.strip()}")
    assert not violations, f"Raw eval() found:\n" + "\n".join(violations)


def test_hivemind_not_primary():
    """hivemind.py must not be the primary entry point."""
    entry = Path("start_waggledance.py")
    if entry.exists():
        content = entry.read_text()
        assert "hivemind" not in content.lower(), "start_waggledance.py still references hivemind"

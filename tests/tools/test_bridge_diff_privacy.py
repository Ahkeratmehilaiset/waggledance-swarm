import pytest

from tools.bridge_diff_privacy import find_diff_private_marker
from tools.pr_status_snapshot import build_pr_status_snapshot
from tests.tools.test_pr_status_snapshot import _runner


def python_diff(statement, path="tools/example.py"):
    return f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1 @@\n+{statement}\n"


@pytest.mark.parametrize("statement", [
    'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")',
    '        if any(marker in rendered for marker in ("PRIVATE_MARKER", "_DO_NOT_LEAK")):',
    '    row["message"] = "PRIVATE_MARKER"',
    '    assert "PRIVATE_MARKER" not in captured.out + captured.err',
    '    for marker in PRIVATE_MARKERS:',
])
def test_known_public_statements(statement):
    diff = python_diff(statement)
    assert find_diff_private_marker(diff) is None
    _, runner = _runner(diff_text=diff)
    assert build_pr_status_snapshot(pr_number=479, runner=runner)["diff_text"] == diff


@pytest.mark.parametrize("statement", [
    'message = "PRIVATE_MARKER sample data"',
    '# PRIVATE_MARKER',
    '# PRIVATE_MARKERS',
    'value = "PRIVATE_MARKERS"',
    'row["message"] = "PRIVATE_MARKER" # sample data',
    'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK", "sample data")',
    'value = "_DO_NOT_LEAK"',
])
def test_other_marker_data_still_refused(statement):
    assert find_diff_private_marker(python_diff(statement)) is not None


def test_non_python_and_incomplete_diff_refused():
    statement = 'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")'
    assert find_diff_private_marker(python_diff(statement, "docs/example.md"))
    assert find_diff_private_marker("+" + statement + "\n")
    assert find_diff_private_marker(python_diff(statement).replace("+1 @@", "+1,2 @@"))


def test_metadata_stays_strict():
    from tools.pr_status_snapshot import _assert_no_private_markers, PrStatusSnapshotError
    with pytest.raises(PrStatusSnapshotError):
        _assert_no_private_markers({"title": 'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")'})

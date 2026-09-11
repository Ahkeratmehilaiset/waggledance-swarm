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
    '@pytest.mark.parametrize("marker", builder.PRIVATE_MARKERS)',
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


@pytest.mark.parametrize("statement", [
    "PRIVATE_MARKERS",
    "    PRIVATE_MARKERS",
    "for marker in PRIVATE_MARKERS: # not the exact public statement",
])
def test_only_complete_enumerated_statements_are_public(statement):
    assert find_diff_private_marker(python_diff(statement)) is not None


def test_diff_shaped_metadata_is_not_source_code():
    from tools.pr_status_snapshot import _assert_no_private_markers, PrStatusSnapshotError
    with pytest.raises(PrStatusSnapshotError):
        _assert_no_private_markers({"title": "+ for marker in PRIVATE_MARKERS:\n"})


def test_non_python_and_incomplete_diff_refused():
    statement = 'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")'
    assert find_diff_private_marker(python_diff(statement, "docs/example.md"))
    assert find_diff_private_marker("+" + statement + "\n")
    assert find_diff_private_marker(python_diff(statement).replace("+1 @@", "+1,2 @@"))


def test_metadata_stays_strict():
    from tools.pr_status_snapshot import _assert_no_private_markers, PrStatusSnapshotError
    with pytest.raises(PrStatusSnapshotError):
        _assert_no_private_markers({"title": 'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")'})


def test_downstream_charter_still_requires_review():
    from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate
    from tests.tools.test_idle_consensus_auto_merge import _status, HEAD, BASE

    diff = python_diff('PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")')
    report = evaluate_auto_merge_gate(
        pr_status=_status(diff_text=diff),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id="idle-consensus-001",
        receipt_bundle_path="docs/receipts/manifest.json",
    )
    assert report["decision"] == "operator_review_required"
    assert report["diff_gate"]["allowed"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False


def test_removed_and_context_statements_are_scanned():
    declaration = 'PRIVATE_MARKERS = ("PRIVATE_MARKER", "_DO_NOT_LEAK")'
    header = "diff --git a/tools/example.py b/tools/example.py\n--- a/tools/example.py\n+++ b/tools/example.py\n"
    assert find_diff_private_marker(header + f"@@ -1 +0,0 @@\n-{declaration}\n") is None
    assert find_diff_private_marker(header + f"@@ -1 +1 @@\n {declaration}\n") is None
    assert find_diff_private_marker(header + '@@ -1 +0,0 @@\n-message = "PRIVATE_MARKER sample"\n')

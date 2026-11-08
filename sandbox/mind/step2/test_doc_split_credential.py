"""Synthetic regression checks for credential fragments spanning document sections."""

import pytest

from sandbox.mind.step2 import shared_content_projection as cp
from sandbox.mind.step2 import shared_doc_projection as dp
from sandbox.mind.step2 import test_shared_doc_projection as fixture


SYNTHETIC_RUN = "GhiJkl789" * 5


@pytest.mark.parametrize("separator", ["\n\n# Heading\n", "\n\n", "\r\n\r\n# Heading\r\n"])
def test_mixed_secret_split_across_document_section_refuses_whole_document(separator):
    document = "Status update\n" + SYNTHETIC_RUN[:20] + separator + SYNTHETIC_RUN[20:] + "\nDone"
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen(document, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"
    assert SYNTHETIC_RUN not in str(exc.value)


def test_explicit_secret_in_path_refuses_whole_document():
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen("See docs/sk-AbcDef123456789/data.py for details.", dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


def test_assignment_across_heading_refuses_whole_document():
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen("api_key:\n\n# Heading\n" + SYNTHETIC_RUN, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


def test_ordinary_useful_text_across_heading_is_retained():
    dp._doc_screen("Status update\n\n# Next steps\nWork continues in the repository.", dp.MAX_DOC_BYTES)


@pytest.mark.parametrize("document", [
    "Status update 2026-11-08.\n\n# Next steps\nWork continues in the repository.",
    "The first task is ready.\n\n# Later\n- A second task is queued.",
    "abcd1234abcd1234\n\n# Heading\nabcd5678abcd5678",
    "AbcDef12345678\n\n# Heading\nQwerty0987654",
    "Useful work\u2028\u2028# Next\u2028More ordinary work",
])
def test_split_detector_keeps_representative_nonsecret_text(document):
    assert dp._split_credential(document) is False


@pytest.mark.parametrize("linebreak", ["\u2028", "\u2029", "\u0085", "\v", "\f"])
def test_split_credential_with_unicode_linebreaks_is_refused(linebreak):
    document = SYNTHETIC_RUN[:20] + linebreak * 2 + "# Heading" + linebreak + SYNTHETIC_RUN[20:]
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen(document, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


@pytest.mark.parametrize("punctuation", [".", ",", ";", ":"])
def test_split_credential_with_trailing_fragment_punctuation_is_refused(punctuation):
    document = SYNTHETIC_RUN[:20] + punctuation + "\n\n# Heading\n" + SYNTHETIC_RUN[20:]
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen(document, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


@pytest.mark.parametrize("marker", ["- ", "* ", "1. "])
def test_split_credential_with_list_marker_is_refused(marker):
    document = SYNTHETIC_RUN[:20] + "\n\n# Heading\n" + marker + SYNTHETIC_RUN[20:]
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen(document, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


@pytest.mark.parametrize("boundaries", [(14, 27), (11, 22, 33)])
def test_credential_split_into_three_or_four_fragments_is_refused(boundaries):
    offsets = (0,) + boundaries + (len(SYNTHETIC_RUN),)
    fragments = [SYNTHETIC_RUN[a:b] for a, b in zip(offsets, offsets[1:])]
    document = "\n\n# Heading\n".join(fragments)
    with pytest.raises(cp._Drop) as exc:
        dp._doc_screen(document, dp.MAX_DOC_BYTES)
    assert exc.value.token == "credential"


def test_split_credential_drops_document_before_any_record_is_built(tmp_path):
    bad = "Before\n" + SYNTHETIC_RUN[:20] + "\n\n# Heading\n" + SYNTHETIC_RUN[20:] + "\nAfter"
    good = "Status update\n\n# Next steps\nWork continues in the repository."
    root, commit = fixture.make_repo(tmp_path, {"docs/bad.md": bad.encode(), "docs/good.md": good.encode()})
    raw, sha = fixture.policy(commit, ["docs/bad.md", "docs/good.md"])
    projection = fixture.build(root, raw, sha)
    assert projection["refused_docs"] == {"credential": 1}
    assert projection["records"]
    assert {record["meta"]["path"] for record in projection["records"]} == {"docs/good.md"}
    assert SYNTHETIC_RUN[:20] not in repr(projection)
    assert SYNTHETIC_RUN[20:] not in repr(projection)

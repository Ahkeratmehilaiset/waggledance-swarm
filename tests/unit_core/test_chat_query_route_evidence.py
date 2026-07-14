import pytest

from waggledance.core.magma.chat_query_route_evidence import (
    NORMALIZATION_VERSION,
    QUERY_DIGEST_DOMAIN,
    canonical_query_digest,
)
from waggledance.core.magma.chat_served_per_query_coverage import (
    NORMALIZATION_VERSION as COVERAGE_NORMALIZATION_VERSION,
)


def test_canonical_query_digest_has_pinned_contract_and_golden_vector() -> None:
    assert NORMALIZATION_VERSION == "wd.chat_query_normalization.v1"
    assert QUERY_DIGEST_DOMAIN == "wd.chat_query_route_evidence.query_digest.v1"
    assert NORMALIZATION_VERSION == COVERAGE_NORMALIZATION_VERSION
    assert canonical_query_digest("  Cafe\u0301 Hive  ") == (
        "sha256:785a11d0df4e7c9fd91fc63608666e4b02913bd0d525fcf9cd7f011371944f48"
    )


def test_canonical_query_digest_preserves_existing_normalization_boundaries() -> None:
    expected = canonical_query_digest("Caf\u00e9 Hive")

    assert canonical_query_digest("  Cafe\u0301 Hive  ") == expected
    assert canonical_query_digest("Caf\u00e9\tHive") != expected
    assert canonical_query_digest("caf\u00e9 Hive") != expected
    assert canonical_query_digest("Caf\u00e9 Hives") != expected


def test_canonical_query_digest_rejects_non_string_input() -> None:
    with pytest.raises(TypeError):
        canonical_query_digest(None)  # type: ignore[arg-type]

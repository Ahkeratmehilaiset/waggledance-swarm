"""
Regression gate: Migration tests — alias registry + canonical ID.

Validates that legacy agent IDs resolve correctly through the
alias registry and that canonical IDs are consistent.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from waggledance.core.capabilities.aliasing import AliasRegistry, AgentAlias


class TestAliasMigration:
    @pytest.fixture
    def registry(self):
        agents = [
            AgentAlias(
                legacy_id="beekeeper",
                canonical="agent:cottage_beekeeper",
                aliases=("beekeeper", "mehiläishoitaja", "bee_keeper"),
                profiles=("cottage",),
            ),
            AgentAlias(
                legacy_id="heating",
                canonical="agent:home_heating",
                aliases=("heating", "lämmitys"),
                profiles=("home", "cottage"),
            ),
        ]
        return AliasRegistry(agents)

    def test_resolve_legacy_id(self, registry):
        assert registry.resolve("beekeeper") == "agent:cottage_beekeeper"

    def test_resolve_alias(self, registry):
        assert registry.resolve("mehiläishoitaja") == "agent:cottage_beekeeper"

    def test_resolve_unknown(self, registry):
        assert registry.resolve("unknown_agent") is None

    def test_list_by_profile(self, registry):
        cottage = registry.by_profile("cottage")
        assert len(cottage) == 2

    def test_canonical_resolves(self, registry):
        assert registry.resolve("agent:cottage_beekeeper") == "agent:cottage_beekeeper"


class TestLegacyDataCompatibility:
    def test_legacy_converter_import(self):
        from waggledance.core.learning.legacy_converter import LegacyConverter
        converter = LegacyConverter()
        assert converter is not None

    def test_convert_empty_pairs(self):
        from waggledance.core.learning.legacy_converter import LegacyConverter
        converter = LegacyConverter()
        cases = converter.convert_training_pairs([])
        assert cases == []

    def test_convert_qa_pair(self):
        from waggledance.core.learning.legacy_converter import LegacyConverter
        converter = LegacyConverter()
        # convert_training_pairs expects List[Tuple[str, str, float, str]]
        # each tuple: (question, answer, confidence, source)
        pairs = [("What is varroa?", "A bee parasite", 0.9, "user")]
        cases = converter.convert_training_pairs(pairs)
        assert len(cases) == 1

    def test_compatibility_layer_import(self):
        from waggledance.core.autonomy.compatibility import CompatibilityLayer
        compat = CompatibilityLayer()
        assert compat is not None

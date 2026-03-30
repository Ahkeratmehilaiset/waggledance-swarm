"""Regression tests for solver routing coverage improvements.

Covers:
- Subtraction arithmetic pattern detection
- Full-word unit conversion (fahrenheit/celsius)
- No false positives (negative temperatures, greetings, domain queries)
"""

import pytest
from waggledance.core.reasoning.solver_router import SolverRouter
from core.math_solver import MathSolver


class TestSubtractionIntent:
    """Subtraction with whitespace must route to math, not retrieval."""

    @pytest.mark.parametrize("query,expected", [
        ("256 - 89", "math"),
        ("What is 100 - 37?", "math"),
    ])
    def test_subtraction_routes_to_math(self, query, expected):
        assert SolverRouter.classify_intent(query) == expected

    def test_negative_temperature_not_math(self):
        """'-5 astetta' must NOT be classified as subtraction."""
        intent = SolverRouter.classify_intent("Onko -5 astetta pakkasriski?")
        assert intent == "thermal"

    def test_negative_celsius_not_math(self):
        """'-10 celsius' must NOT be classified as subtraction."""
        intent = SolverRouter.classify_intent("What is the frost risk at -10 celsius?")
        assert intent == "thermal"

    def test_subtraction_solve(self):
        assert MathSolver.solve("256 - 89") == "167"

    def test_finnish_subtraction_solve(self):
        result = MathSolver.solve("5 miinus 3")
        assert result == "2"


class TestUnitConversion:
    """Full-word fahrenheit/celsius must be parsed by MathSolver."""

    def test_fahrenheit_to_celsius_full_word(self):
        result = MathSolver.solve("100 fahrenheit celsiuksina")
        assert result is not None
        assert "37.8" in result

    def test_fahrenheit_to_celsius_finnish(self):
        result = MathSolver.solve("Paljonko on 100 fahrenheit celsiuksina?")
        assert result is not None
        assert "37.8" in result

    def test_celsius_to_fahrenheit_full_word(self):
        result = MathSolver.solve("100 celsius fahrenheit")
        assert result is not None
        assert "212" in result

    def test_short_form_c_to_f(self):
        result = MathSolver.solve("100C to F")
        assert result is not None
        assert "212" in result

    def test_short_form_f_to_c(self):
        result = MathSolver.solve("100F to C")
        assert result is not None
        assert "37.8" in result

    def test_celsius_to_fahrenheitiksi(self):
        result = MathSolver.solve("20 celsius fahrenheitiksi")
        assert result is not None
        assert "68" in result

    def test_f_celsiukseksi(self):
        result = MathSolver.solve("212F celsiukseksi")
        assert result is not None
        assert "100" in result


class TestNoFalsePositives:
    """Ensure no regression on existing intent classification."""

    def test_greeting_stays_chat(self):
        assert SolverRouter.classify_intent("Moi") == "chat"
        assert SolverRouter.classify_intent("Hello") == "chat"

    def test_domain_knowledge_stays_retrieval(self):
        assert SolverRouter.classify_intent("What is WaggleDance?") == "retrieval"

    def test_addition_still_math(self):
        assert SolverRouter.classify_intent("What is 15 + 27?") == "math"

    def test_multiplication_still_math(self):
        assert SolverRouter.classify_intent("Paljonko on 3 * 17?") == "math"

    def test_thermal_still_thermal(self):
        assert SolverRouter.classify_intent("Is 35 degrees too hot for a beehive?") == "thermal"

    def test_division_still_math(self):
        assert SolverRouter.classify_intent("Calculate 100 / 4") == "math"

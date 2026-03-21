"""Tests for core.math_solver — extracted from memory_engine.py (v1.17.0)."""

import unittest

from core.math_solver import MathSolver


class TestMathSolverIsMath(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertTrue(MathSolver.is_math("calculate 2+3"))

    def test_finnish_trigger(self):
        self.assertTrue(MathSolver.is_math("laske 5*3"))

    def test_unit_conversion(self):
        self.assertTrue(MathSolver.is_math("100C to fahrenheit"))

    def test_finnish_operator(self):
        self.assertTrue(MathSolver.is_math("5 kertaa 3"))

    def test_not_math_text(self):
        self.assertFalse(MathSolver.is_math("hello world"))

    def test_function_call(self):
        self.assertTrue(MathSolver.is_math("calculate sqrt(16)"))

    def test_empty_after_trigger_removal(self):
        self.assertFalse(MathSolver.is_math("calculate"))


class TestMathSolverSolve(unittest.TestCase):
    def test_addition(self):
        self.assertEqual(MathSolver.solve("calculate 2+3"), "5")

    def test_multiplication(self):
        self.assertEqual(MathSolver.solve("laske 6*7"), "42")

    def test_float_result(self):
        result = MathSolver.solve("calculate 10/3")
        self.assertIn("3.33", result)

    def test_integer_result_from_float(self):
        self.assertEqual(MathSolver.solve("calculate 10/2"), "5")

    def test_sqrt(self):
        self.assertEqual(MathSolver.solve("calculate sqrt(16)"), "4")

    def test_power(self):
        self.assertEqual(MathSolver.solve("calculate 2^10"), "1024")

    def test_celsius_to_fahrenheit(self):
        result = MathSolver.solve("100C to fahrenheit")
        self.assertEqual(result, "212.0°F")

    def test_fahrenheit_to_celsius(self):
        result = MathSolver.solve("32F to celsius")
        self.assertEqual(result, "0.0°C")

    def test_kg_to_lbs(self):
        result = MathSolver.solve("1 kg to lbs")
        self.assertIn("2.2", result)

    def test_finnish_kertaa(self):
        self.assertEqual(MathSolver.solve("5 kertaa 3"), "15")

    def test_finnish_neliojuuri(self):
        self.assertEqual(MathSolver.solve("neliojuuri 25"), "5")

    def test_invalid_expression(self):
        result = MathSolver.solve("calculate hello world")
        self.assertIsNone(result)

    def test_safe_names_only(self):
        """eval should not allow dangerous builtins."""
        result = MathSolver.solve("calculate __import__('os').system('echo hack')")
        self.assertIsNone(result)


class TestMathSolverUnicodeOps(unittest.TestCase):
    def test_multiplication_sign(self):
        self.assertEqual(MathSolver.solve("calculate 3×4"), "12")

    def test_division_sign(self):
        self.assertEqual(MathSolver.solve("calculate 12÷4"), "3")

    def test_comma_decimal(self):
        result = MathSolver.solve("calculate 3,14 * 2")
        self.assertIsNotNone(result)
        self.assertAlmostEqual(float(result), 6.28, places=1)


if __name__ == "__main__":
    unittest.main()

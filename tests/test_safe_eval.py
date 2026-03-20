"""Tests for AST-based safe_eval — covers RCE prevention + math correctness."""
import math
import pytest
from core.safe_eval import safe_eval, SafeEvalError


class TestBasicMath:
    def test_addition(self):
        assert safe_eval("2 + 3", {}) == 5

    def test_variables(self):
        assert safe_eval("x * 2", {"x": 5}) == 10

    def test_functions(self):
        assert safe_eval("sqrt(16)", {}) == 4.0

    def test_complex_formula(self):
        result = safe_eval("max(x, y) + sqrt(z)", {"x": 3, "y": 7, "z": 9})
        assert result == 10.0

    def test_constants(self):
        assert abs(safe_eval("pi", {}) - math.pi) < 1e-10

    def test_boolean(self):
        assert safe_eval("x > 5", {"x": 10}) is True

    def test_conditional(self):
        assert safe_eval("x if x > 0 else -x", {"x": -5}) == 5


class TestRCEPrevention:
    """Every test here MUST raise SafeEvalError."""

    def test_block_import(self):
        with pytest.raises(SafeEvalError):
            safe_eval("__import__('os')", {})

    def test_block_subclass_attack(self):
        with pytest.raises(SafeEvalError):
            safe_eval("().__class__.__bases__[0].__subclasses__()", {})

    def test_block_getattr(self):
        with pytest.raises(SafeEvalError):
            safe_eval("getattr(int, '__subclasses__')()", {})

    def test_block_attribute_access(self):
        with pytest.raises(SafeEvalError):
            safe_eval("''.__class__", {})

    def test_block_double_underscore_name(self):
        with pytest.raises(SafeEvalError):
            safe_eval("__builtins__", {})

    def test_block_double_underscore_in_context(self):
        with pytest.raises(SafeEvalError):
            safe_eval("x.__class__", {"x": 1})

    def test_block_eval_builtin(self):
        with pytest.raises(SafeEvalError):
            safe_eval("eval('1+1')", {})

    def test_block_exec(self):
        with pytest.raises(SafeEvalError):
            safe_eval("exec('import os')", {})

    def test_block_open(self):
        with pytest.raises(SafeEvalError):
            safe_eval("open('/etc/passwd')", {})

    def test_block_compile(self):
        with pytest.raises(SafeEvalError):
            safe_eval("compile('x','','exec')", {})

    def test_block_type_call(self):
        with pytest.raises(SafeEvalError):
            safe_eval("type('X', (), {})", {})


class TestAxiomRegression:
    """Ensure existing YAML axiom formulas still work after migration."""

    def test_heating_formula(self):
        ctx = {"U": 0.17, "A": 120.0, "T_indoor": 21.0, "T_outdoor": -15.0}
        result = safe_eval("U * A * (T_indoor - T_outdoor)", ctx)
        assert abs(result - 734.4) < 0.1

    def test_conditional_risk(self):
        ctx = {"T_outdoor": -25, "wind_speed": 15}
        assert safe_eval("T_outdoor < -20", ctx) is True

    def test_log_formula(self):
        ctx = {"x": 100}
        assert safe_eval("log10(x)", ctx) == 2.0

    def test_nested_max_min(self):
        ctx = {"a": 5, "b": 10, "c": 3}
        assert safe_eval("max(min(a, b), c)", ctx) == 5

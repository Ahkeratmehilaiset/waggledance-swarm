"""AST-based safe expression evaluator — replaces eval() in symbolic_solver.

Whitelist approach: ONLY explicitly allowed node types and function names pass.
Everything else raises SafeEvalError. No blacklist, no __builtins__ tricks.
"""

import ast
import math
from typing import Any, Dict, Optional


class SafeEvalError(Exception):
    """Raised when expression contains forbidden operations."""
    pass


# ── Whitelists (ONLY these are allowed, everything else is blocked) ──

ALLOWED_NODE_TYPES = (
    ast.Expression,
    ast.BinOp, ast.UnaryOp, ast.Compare, ast.BoolOp, ast.IfExp,
    # Operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or,
    # Values
    ast.Constant, ast.Name, ast.Load,
    # Function calls (validated separately)
    ast.Call,
    # Tuple/List for multi-arg functions like max(a, b)
    ast.Tuple, ast.List,
)

# Python <3.8 compat — ast.Num/ast.Str deprecated since 3.8, removed in 3.14
_LEGACY = ()
for _name in ("Num", "Str"):
    _node = getattr(ast, _name, None)
    if _node is not None:
        _LEGACY = _LEGACY + (_node,)
if _LEGACY:
    ALLOWED_NODE_TYPES = ALLOWED_NODE_TYPES + _LEGACY
del _LEGACY, _name, _node

ALLOWED_FUNCTIONS: Dict[str, Any] = {
    "abs": abs, "max": max, "min": min, "round": round, "pow": pow,
    "int": int, "float": float, "bool": bool,
    "log10": math.log10, "log": math.log, "exp": math.exp,
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
}

ALLOWED_CONSTANTS: Dict[str, Any] = {
    "pi": math.pi, "e": math.e,
    "True": True, "False": False,
}


def safe_eval(expr: str, context: Optional[Dict[str, Any]] = None) -> Any:
    """Evaluate a math expression safely using AST whitelist.

    Args:
        expr: Mathematical expression string (e.g. "sqrt(x) + 2 * pi")
        context: Variable name → value mapping

    Returns:
        Evaluation result

    Raises:
        SafeEvalError: If expression contains forbidden operations
        SyntaxError: If expression is not valid Python
    """
    if context is None:
        context = {}

    # 1. Parse to AST
    try:
        tree = ast.parse(expr, mode='eval')
    except SyntaxError as e:
        raise SafeEvalError(f"Syntax error in expression: {e}") from e

    # 2. Walk AST — reject anything not whitelisted
    for node in ast.walk(tree):
        # Block Attribute access entirely (no x.__class__, no "".join, etc.)
        if isinstance(node, ast.Attribute):
            raise SafeEvalError(
                f"Attribute access blocked: .{getattr(node, 'attr', '?')}")

        # Block non-whitelisted node types
        if not isinstance(node, ALLOWED_NODE_TYPES):
            raise SafeEvalError(
                f"Forbidden node type: {type(node).__name__}")

        # Validate function calls — only whitelisted function names
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise SafeEvalError(
                    "Forbidden function call: only simple function names allowed")
            if node.func.id not in ALLOWED_FUNCTIONS:
                raise SafeEvalError(
                    f"Forbidden function: {node.func.id}")

        # Block double-underscore names
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise SafeEvalError(
                f"Double-underscore access blocked: {node.id}")

    # 3. Build safe namespace — context vars + allowed functions + constants
    safe_ns = {}
    safe_ns.update(ALLOWED_CONSTANTS)
    safe_ns.update(ALLOWED_FUNCTIONS)
    safe_ns.update(context)

    # 4. Compile and eval (safe because AST was validated)
    code = compile(tree, "<safe_eval>", "eval")
    return eval(code, {"__builtins__": {}}, safe_ns)

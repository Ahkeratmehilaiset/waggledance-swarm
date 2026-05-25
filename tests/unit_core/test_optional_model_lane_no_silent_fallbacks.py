import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OPTIONAL_MODEL_LANE_FILES = (
    ROOT / "core" / "micro_model.py",
    ROOT / "core" / "opus_mt_adapter.py",
    ROOT / "core" / "translation_proxy.py",
)


def _is_exception_handler(handler: ast.ExceptHandler) -> bool:
    return isinstance(handler.type, ast.Name) and handler.type.id == "Exception"


def test_optional_model_lanes_do_not_silently_swallow_exception() -> None:
    offenders: list[str] = []
    for path in OPTIONAL_MODEL_LANE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if _is_exception_handler(node) and len(node.body) == 1:
                stmt = node.body[0]
                if isinstance(stmt, ast.Pass):
                    offenders.append(f"{path.relative_to(ROOT)}:{stmt.lineno}")

    assert offenders == []

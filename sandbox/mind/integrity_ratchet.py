"""Integrity ratchet for the Mind sandbox (D13; RCO1's R8 proposal, adopted, built and decided by fable-5, so RCO1 is
not independent for it). It counts five known bug classes in every non-test module under sandbox/mind, by AST only:
each file is parsed, never imported or run, and only the standard library is used. ``test_integrity_ratchet.py``
holds this tree's counts in ``ALLOWED``. A file that gains an instance fails that test (a new bug of a known kind),
and so does a file that loses one (``ALLOWED`` is lowered in the same change), so the numbers only go down while the
classes are fixed one change at a time.

The classes come from RCO1's rounds R1, R2 and R5 (see ``CLASSES``). Unlike RCO1's draft, a bool exclusion of the
same subject in the same and/or chain is seen (four of the seven int checks in this tree have one), a pattern ending
in ``\\Z`` is anchored, a MULTILINE pattern (a line scan) is not a ``$`` validation, and ``json.load``, an open in a
"w" mode, module aliases and from-imports count as well.

Heuristic limits: a name is matched by its text within one module, with no flow analysis and no shadowing; a pattern
built at run time, a mode held in a variable, ``**kwargs`` and a bool check on another line are not seen, so a count
may include an instance that is guarded elsewhere (``ALLOWED`` absorbs it) or miss one. A temp-file write followed
by an atomic replace still counts its write. A refinement of a class only ever lowers a count.

usage: python -B -I sandbox/mind/integrity_ratchet.py [--hits]
       (prints ALLOWED's literal for this tree, or one "path:line class" line per instance)
"""
from __future__ import annotations

import ast
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLASSES = {
    "json": "json.loads or json.load without object_pairs_hook: of two equal keys the last silently wins (R1)",
    "dollar": "a pattern ending in an unescaped $ used with match or search, not MULTILINE: a trailing newline "
              "passes (R2)",
    "prefix": "a pattern with no end anchor ($ or \\Z) used with match: only a prefix is checked (R2-B)",
    "int": "isinstance(x, int), or a tuple with int and without bool, with no bool exclusion of the same x in its "
           "own and/or chain: True passes as 1 (R2)",
    "write": "Path.write_text or write_bytes, or an open in a 'w' mode: a crash leaves a cut file under the real name "
             "(R5)",
}
FLAG_CHARS = frozenset("aiLmsux")
MODE_CHARS = frozenset("rwxabt+")
RE_FUNCTIONS = ("compile", "match", "search", "fullmatch")


def _literal(node):
    """The text of a string constant, else None."""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _imports(tree):
    """Names bound by import to the json module, its readers, the re module and its functions (name -> function)."""
    json_modules, json_readers, re_modules, re_functions = set(), set(), set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "json":
                    json_modules.add(alias.asname or "json")
                elif alias.name == "re":
                    re_modules.add(alias.asname or "re")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module in ("json", "re"):
            for alias in node.names:
                bound = alias.asname or alias.name
                if node.module == "json" and alias.name in ("loads", "load"):
                    json_readers.add(bound)
                elif node.module == "re" and alias.name in RE_FUNCTIONS:
                    re_functions[bound] = alias.name
    return json_modules, json_readers, re_modules, re_functions


def _end(pattern):
    """'dollar' for an unescaped final $, 'anchored' for a final \\Z or \\z, else 'open'."""
    body = pattern[:-1]
    escapes = len(body) - len(body.rstrip("\\"))
    if pattern.endswith("$"):
        return "open" if escapes % 2 else "dollar"
    if pattern.endswith(("Z", "z")) and escapes % 2:
        return "anchored"
    return "open"


def _multiline(pattern, flags):
    """True when the flags name re.M or re.MULTILINE, or the pattern opens with a global inline group holding m."""
    for flag in flags:
        for sub in ast.walk(flag):
            if isinstance(sub, ast.Attribute) and sub.attr in ("M", "MULTILINE"):
                return True
    if not pattern.startswith("(?"):
        return False
    group = pattern[2:pattern.find(")")] if ")" in pattern else ""
    return bool(group) and set(group) <= FLAG_CHARS and "m" in group


def _verdict(method, pattern, multiline):
    """The class of one match or search on a known pattern, else None."""
    end = _end(pattern)
    if end == "dollar" and not multiline:
        return "dollar"
    if end == "open" and method == "match":
        return "prefix"
    return None


def _names(node):
    """The plain names in a type expression: one name or a tuple of them."""
    return {item.id for item in (node.elts if isinstance(node, ast.Tuple) else [node]) if isinstance(item, ast.Name)}


def _isinstance_of(node, name):
    """The subject when ``node`` is isinstance(subject, T) and T names ``name``, else None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "isinstance" \
            and len(node.args) == 2 and not node.keywords and name in _names(node.args[1]):
        return node.args[0]
    return None


def _type_is_bool(node, negated):
    """The subject when ``node`` is ``type(subject) is bool`` (negated: ``is not bool``), else None."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1):
        return None
    left, right = node.left, node.comparators[0]
    if isinstance(node.ops[0], ast.IsNot if negated else ast.Is) and isinstance(left, ast.Call) \
            and isinstance(left.func, ast.Name) and left.func.id == "type" and len(left.args) == 1 \
            and isinstance(right, ast.Name) and right.id == "bool":
        return left.args[0]
    return None


def _excludes_bool(operand, subject, negated):
    """True when ``operand`` keeps bool out for ``subject``: beside an int check in an and-chain, ``not
    isinstance(subject, bool)`` or ``type(subject) is not bool``; beside a negated int check in an or-chain,
    ``isinstance(subject, bool)`` or ``type(subject) is bool``."""
    if negated:
        found = _isinstance_of(operand, "bool")
        if found is None:
            found = _type_is_bool(operand, negated=False)
    else:
        found = None
        if isinstance(operand, ast.UnaryOp) and isinstance(operand.op, ast.Not):
            found = _isinstance_of(operand.operand, "bool")
        if found is None:
            found = _type_is_bool(operand, negated=True)
    return found is not None and ast.dump(found) == ast.dump(subject)


def _mode(node):
    """A literal that reads as a file mode ('w', 'rb', 'a+', ...), else None."""
    text = _literal(node)
    return text if text and len(text) <= 3 and set(text) <= MODE_CHARS else None


def hits(source):
    """Every instance of a known class in one module's source (str or bytes): sorted (line, class) pairs."""
    tree = ast.parse(source)
    json_modules, json_readers, re_modules, re_functions = _imports(tree)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def re_function(func):
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in re_modules \
                and func.attr in RE_FUNCTIONS:
            return func.attr
        return re_functions.get(func.id) if isinstance(func, ast.Name) else None

    def compiled_pattern(call):
        """(pattern, multiline) when ``call`` is re.compile of a string constant, else None."""
        if isinstance(call, ast.Call) and re_function(call.func) == "compile" and call.args \
                and _literal(call.args[0]) is not None:
            pattern = _literal(call.args[0])
            flags = call.args[1:] + [k.value for k in call.keywords if k.arg == "flags"]
            return pattern, _multiline(pattern, flags)
        return None

    compiled = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and compiled_pattern(node.value) is not None:
            for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                if isinstance(target, (ast.Name, ast.Attribute)):
                    compiled[ast.unparse(target)] = compiled_pattern(node.value)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        keywords = {k.arg for k in node.keywords}
        if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in json_modules
                and func.attr in ("loads", "load")) or (isinstance(func, ast.Name) and func.id in json_readers):
            if "object_pairs_hook" not in keywords:
                found.append((node.lineno, "json"))
        kind = re_function(func)
        if kind is not None:
            if kind in ("match", "search") and node.args and _literal(node.args[0]) is not None:
                pattern = _literal(node.args[0])
                flags = node.args[2:] + [k.value for k in node.keywords if k.arg == "flags"]
                verdict = _verdict(kind, pattern, _multiline(pattern, flags))
                if verdict:
                    found.append((node.lineno, verdict))
        elif isinstance(func, ast.Attribute) and func.attr in ("match", "search"):
            owner = func.value
            info = compiled_pattern(owner) if isinstance(owner, ast.Call) else (
                compiled.get(ast.unparse(owner)) if isinstance(owner, (ast.Name, ast.Attribute)) else None)
            verdict = _verdict(func.attr, *info) if info else None
            if verdict:
                found.append((node.lineno, verdict))
        subject = _isinstance_of(node, "int")
        if subject is not None and "bool" not in _names(node.args[1]):
            parent = parents.get(node)
            negated = isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.Not)
            chain = parents.get(parent) if negated else parent
            guarded = isinstance(chain, ast.BoolOp) and isinstance(chain.op, ast.Or if negated else ast.And) \
                and any(_excludes_bool(operand, subject, negated) for operand in chain.values)
            if not guarded:
                found.append((node.lineno, "int"))
        if isinstance(func, ast.Attribute) and func.attr in ("write_text", "write_bytes"):
            found.append((node.lineno, "write"))
        name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
        if name == "open":
            modes = [_mode(arg) for arg in node.args[:2]] + [_mode(k.value) for k in node.keywords if k.arg == "mode"]
            if any(mode and "w" in mode for mode in modes):
                found.append((node.lineno, "write"))
    return sorted(found)


def count_source(source):
    """The number of instances per class in one module's source, nonzero classes only, in CLASSES order."""
    counts = Counter(cls for _, cls in hits(source))
    return {cls: counts[cls] for cls in CLASSES if counts[cls]}


def _modules(root):
    """(relative posix path, path) of every module the ratchet reads under ``root``, sorted by that path: test files,
    conftest.py, __pycache__ and hidden directories are skipped."""
    found = {}
    for path in root.rglob("*.py"):
        rel = path.relative_to(root).as_posix()
        parts = rel.split("/")
        if parts[-1].startswith("test_") or parts[-1] == "conftest.py" \
                or any(part.startswith(".") or part == "__pycache__" for part in parts[:-1]):
            continue
        found[rel] = path
    return [(rel, found[rel]) for rel in sorted(found)]


def collect(root=ROOT):
    """The counts of every module under ``root`` that holds an instance: relative posix path -> counts, sorted."""
    out = {}
    for rel, path in _modules(Path(root)):
        counts = count_source(path.read_bytes())
        if counts:
            out[rel] = counts
    return out


def compare(current, allowed):
    """(grown, stale): per file, the classes counted above or below their allowance, as (now, allowed) pairs."""
    grown, stale = {}, {}
    for rel in sorted(set(current) | set(allowed)):
        now, limit = current.get(rel, {}), allowed.get(rel, {})
        for cls in sorted(set(now) | set(limit)):
            pair = (now.get(cls, 0), limit.get(cls, 0))
            if pair[0] > pair[1]:
                grown.setdefault(rel, {})[cls] = pair
            elif pair[0] < pair[1]:
                stale.setdefault(rel, {})[cls] = pair
    return grown, stale


def allowed_literal(current):
    """The text of ALLOWED's dict literal for ``current``: one file per line, sorted, classes in CLASSES order."""
    rows = []
    for rel in sorted(current):
        counts = current[rel]
        if '"' in rel or "\\" in rel or set(counts) - set(CLASSES):
            raise ValueError(f"cannot write {rel!r}: {counts!r}")
        if any(type(n) is not int or n < 1 for n in counts.values()):
            raise ValueError(f"counts must be positive ints: {rel!r}: {counts!r}")
        inner = ", ".join(f'"{cls}": {counts[cls]}' for cls in CLASSES if cls in counts)
        rows.append(f'    "{rel}": {{{inner}}},')
    return "{\n" + "\n".join(rows) + "\n}" if rows else "{}"


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if args == ["--hits"]:
        for rel, path in _modules(ROOT):
            for line, cls in hits(path.read_bytes()):
                print(f"{rel}:{line} {cls}")
        return 0
    if args:
        print("usage: integrity_ratchet.py [--hits]", file=sys.stderr)
        return 2
    print(allowed_literal(collect()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

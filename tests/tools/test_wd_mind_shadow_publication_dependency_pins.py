"""Pure text/AST/hash guard for the A4 publication fixture and probe dependency pins.

PR1918 BC4A1E18 class: a producer or core change can reach main before the publication fixture and probe are
repinned, and CI then shows only indirect fixture refusals. This test recomputes every producer/core dependency
pin from the current checkout and names each stale pin in the fixture and the probe. It reads the four files as
text only: it never imports or runs the fixture, the probe, the producer, the core or PowerShell, and it never
repins anything. A stale pin is fixed by hand in the fixture AND the probe, after review of the new bytes."""
import ast
import hashlib
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = 'tests/windows/wd_mind_shadow_publication_fixture.py'
PROBE = 'tests/windows/wd_mind_shadow_publication_probe.ps1'
# pin key -> (dependency file, its extracted function, the probe variable that admits its bytes)
DEPENDENCIES = {
    'producer_sha256': ('sandbox/mind/mind_capture_producer.py', 'atomic_write', '$producerHash'),
    'core_sha256': ('tools/wd_bridge_mind.py', '_sharing_retry', '$coreHash'),
}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def current_pins(root):
    # The fixture admits exactly two encodings of each Git blob (the CRLF checkout and the LF blob) and pins the
    # function segment of the LF text; recompute all three from whichever checkout this is.
    pins = {}
    for key, (relative, function, _) in DEPENDENCIES.items():
        lf = (root / relative).read_bytes().replace(b'\r\n', b'\n')
        assert b'\r' not in lf, f'{relative}: mixed line endings are no supported encoding'
        text = lf.decode('utf-8')
        nodes = [node for node in ast.parse(text).body if isinstance(node, ast.FunctionDef) and node.name == function]
        assert len(nodes) == 1 and not nodes[0].decorator_list, f'{relative}: no single undecorated top-level {function}'
        pins[key] = {'crlf': sha256(lf.replace(b'\n', b'\r\n')), 'lf': sha256(lf),
                     'function': sha256(ast.get_source_segment(text, nodes[0]).encode('utf-8'))}
    return pins


def fixture_pins(root):
    tables = {}
    for node in ast.parse((root / FIXTURE).read_text(encoding='utf-8')).body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id in ('PINS', 'CANONICAL_PINS', 'FUNCTION_PINS')):
            assert node.targets[0].id not in tables, f'{FIXTURE}: {node.targets[0].id} is assigned twice'
            tables[node.targets[0].id] = ast.literal_eval(node.value)
    assert sorted(tables) == ['CANONICAL_PINS', 'FUNCTION_PINS', 'PINS'], f'{FIXTURE}: a pin table is missing'
    return tables


def probe_pins(root):
    text = (root / PROBE).read_text(encoding='utf-8')
    found = {}
    for relative, function, variable in DEPENDENCIES.values():
        lines = [line for line in text.splitlines() if line.lstrip().startswith(variable + ' = ')]
        assert len(lines) == 1, f'{PROBE}: expected exactly one {variable} assignment'
        assert relative in lines[0], f'{PROBE}: {variable} no longer reads {relative}'
        found[variable] = [value.lower() for value in re.findall(r"'([0-9A-Fa-f]{64})'", lines[0])]
        found[function] = re.findall(r"function_sha256\." + function + r" -cne '([0-9a-f]{64})'", text)
    return found


def stale_pins(root):
    """[(file, pin, message)] for every fixture or probe pin that differs from the current checkout."""
    current, tables, probe = current_pins(root), fixture_pins(root), probe_pins(root)
    stale = []

    def check(file, pin, pinned, expected, what):
        if pinned != expected:
            stale.append((file, pin, f'{file} {pin} pins {pinned!r}; the current {what} is {expected!r}'))

    functions = [function for _, function, _ in DEPENDENCIES.values()]
    for table, keys in (('PINS', list(DEPENDENCIES)), ('CANONICAL_PINS', list(DEPENDENCIES)), ('FUNCTION_PINS', functions)):
        if sorted(tables[table]) != sorted(keys):
            stale.append((FIXTURE, table, f'{FIXTURE} {table} has keys {sorted(tables[table])}, expected {sorted(keys)}'))
    for key, (relative, function, variable) in DEPENDENCIES.items():
        now = current[key]
        check(FIXTURE, f'PINS[{key!r}]', tables['PINS'].get(key), (relative, now['crlf']), f'{relative} CRLF checkout')
        check(FIXTURE, f'CANONICAL_PINS[{key!r}]', tables['CANONICAL_PINS'].get(key), now['lf'], f'{relative} LF blob')
        check(FIXTURE, f'FUNCTION_PINS[{function!r}]', tables['FUNCTION_PINS'].get(function), now['function'],
              f'{relative} {function} segment')
        check(PROBE, variable, probe[variable], [now['crlf'], now['lf']], f'{relative} CRLF and LF pair')
        check(PROBE, f'function_sha256.{function}', probe[function], [now['function']], f'{relative} {function} segment')
    return stale


def test_publication_fixture_and_probe_pin_the_current_producer_and_core():
    stale = stale_pins(ROOT)
    assert not stale, ('Stale A4 publication dependency pins; review the new bytes, then repin the fixture AND the '
                       'probe by hand (never auto-repin):\n' + '\n'.join(message for _, _, message in stale))


def synthetic_root(root, newline=b'\n'):
    # Synthetic copies of the current fixture, probe, producer and core bytes; the checkout itself is never changed.
    for relative in (FIXTURE, PROBE, *(relative for relative, _, _ in DEPENDENCIES.values())):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes().replace(b'\r\n', b'\n').replace(b'\n', newline))
    return root


def pins_named(root):
    return [(file, pin) for file, pin, _ in stale_pins(root)]


def test_the_current_pins_do_not_depend_on_the_checkout_line_endings(tmp_path):
    assert current_pins(synthetic_root(tmp_path / 'lf')) == current_pins(synthetic_root(tmp_path / 'crlf', b'\r\n'))


def test_a_producer_change_outside_atomic_write_names_the_three_producer_byte_pins(tmp_path):
    root = synthetic_root(tmp_path)
    producer = root / DEPENDENCIES['producer_sha256'][0]
    producer.write_bytes(producer.read_bytes() + b'\n# synthetic change outside atomic_write\n')
    assert pins_named(root) == [(FIXTURE, "PINS['producer_sha256']"), (FIXTURE, "CANONICAL_PINS['producer_sha256']"),
                                (PROBE, '$producerHash')]


def test_an_atomic_write_change_like_bc4a1e18_also_names_both_function_pins(tmp_path):
    # BC4A1E18: PR1922 changed the atomic_write body while the fixture and probe still pinned the old segment.
    root = synthetic_root(tmp_path)
    producer = root / DEPENDENCIES['producer_sha256'][0]
    lines = producer.read_bytes().decode('utf-8').split('\n')
    node = [n for n in ast.parse('\n'.join(lines)).body if isinstance(n, ast.FunctionDef) and n.name == 'atomic_write']
    first = node[0].body[0]
    lines.insert(first.lineno - 1, ' ' * first.col_offset + 'pass  # synthetic change inside atomic_write')
    producer.write_bytes('\n'.join(lines).encode('utf-8'))
    assert pins_named(root) == [(FIXTURE, "PINS['producer_sha256']"), (FIXTURE, "CANONICAL_PINS['producer_sha256']"),
                                (FIXTURE, "FUNCTION_PINS['atomic_write']"), (PROBE, '$producerHash'),
                                (PROBE, 'function_sha256.atomic_write')]


def test_a_probe_left_unrepinned_is_named_alone(tmp_path):
    root = synthetic_root(tmp_path)
    probe = root / PROBE
    core_lf = current_pins(root)['core_sha256']['lf'].upper()
    text = probe.read_bytes().decode('utf-8')
    assert text.count(core_lf) == 1
    probe.write_bytes(text.replace(core_lf, '0' * 64).encode('utf-8'))
    assert pins_named(root) == [(PROBE, '$coreHash')]


def test_a_fixture_function_pin_left_stale_is_named_alone(tmp_path):
    root = synthetic_root(tmp_path)
    fixture = root / FIXTURE
    retry = current_pins(root)['core_sha256']['function']
    text = fixture.read_bytes().decode('utf-8')
    assert text.count(retry) == 1
    fixture.write_bytes(text.replace(retry, 'f' * 64).encode('utf-8'))
    assert pins_named(root) == [(FIXTURE, "FUNCTION_PINS['_sharing_retry']")]

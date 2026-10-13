"""Dormant, pure scoring experiment. No I/O, clock, gate, network or authority.

Callers obtain canonical observations separately. Digests identify the frozen
scoring cohort, not truth or reviewer identity. Missing measurements stay
UNKNOWN, never zero. Output contains only fixed labels and validated numbers;
no input paths, answer prose or private captures are echoed.
"""
import math
import re

LANES = frozenset(('codex-lead-1', 'codex-tools-1', 'fable-5', 'claude-rco-1', 'claude-rco-2'))
VARIANTS = frozenset(f'V{i}' for i in range(10))
QUESTIONS = frozenset(f'Q{i}' for i in range(1, 5))


def _digest(value):
    return type(value) is str and re.fullmatch('[a-f0-9]{64}', value) is not None


def _metric(value, integer=False):
    types = (int,) if integer else (int, float)
    return type(value) in types and 0 <= value <= 10**15 and math.isfinite(value)


def _assess(record):
    answers = record.get('answers')
    if type(answers) is not list or len(answers) != 4:
        return None
    ids = [a.get('id') if type(a) is dict else None for a in answers]
    if any(type(i) is not str for i in ids) or frozenset(ids) != QUESTIONS:
        return None
    totals = {'bytes_read': 0, 'canonical_bytes': 0, 'seconds': 0.0, 'wrong': 0, 'critical': 0, 'misses': 0}
    unknown = False
    blocked = False
    for answer in answers:
        for key in ('bytes_read', 'canonical_bytes', 'seconds'):
            value = answer.get(key)
            if not _metric(value, integer=key != 'seconds'):
                unknown = True
            else:
                totals[key] += value
        correct = answer.get('correct')
        wrong = answer.get('wrong_facts')
        refs = answer.get('evidence_refs')
        if type(correct) is not bool or not _metric(wrong, integer=True):
            unknown = True
        else:
            totals['wrong'] += wrong
            blocked |= not correct or wrong != 0
        if type(refs) is not list or not refs or len(refs) > 128 or not all(_digest(r) for r in refs):
            unknown = True
        misses = answer.get('misses')
        if type(misses) is not list or len(misses) > 128:
            unknown = True
            continue
        totals['misses'] += len(misses)
        for miss in misses:
            if type(miss) is not dict or type(miss.get('critical')) is not bool:
                unknown = True
            elif miss['critical']:
                totals['critical'] += 1
                blocked = True
    return totals, unknown, blocked


def tally_round(records, *, capture_sha256, context_sha256, builder_sha256, lanes):
    """Rank one complete five-lane/four-question cohort, advisory only.

Incomplete or duplicate cohorts and unclassified misses cannot win. Canonical
read cost is reported separately from view cost. A winning label says the
caller-supplied observations satisfy this scoring protocol, never gate PASS.
"""
    if (type(records) is not list or len(records) > len(VARIANTS) * len(LANES) or type(lanes) not in (list, tuple)
            or len(lanes) != 5 or any(type(lane) is not str for lane in lanes)
            or frozenset(lanes) != LANES
            or not all(_digest(d) for d in (capture_sha256, context_sha256, builder_sha256))):
        raise ValueError('invalid_scoring_round')
    cohorts = {}
    refused = 0
    for record in records:
        if type(record) is not dict:
            refused += 1
            continue
        variant, lane = record.get('variant'), record.get('lane')
        if type(variant) is not str or variant not in VARIANTS or type(lane) is not str or lane not in LANES:
            refused += 1
            continue
        cohort = cohorts.setdefault(variant, {'lanes': {}, 'unknown': False, 'blocked': False})
        if lane in cohort['lanes']:
            cohort['unknown'] = True
            refused += 1
            continue
        if (record.get('schema') not in ('wd.bridge-mind-tournament-result.v1', 'wd.bridge-mind-tournament-result.v2')
                or record.get('round_capture_sha256') != capture_sha256
                or record.get('round_context_sha256') != context_sha256
                or record.get('round_builder_sha256') != builder_sha256):
            cohort['unknown'] = True
            refused += 1
            continue
        assessed = _assess(record)
        if assessed is None:
            cohort['unknown'] = True
            refused += 1
            continue
        totals, unknown, blocked = assessed
        cohort['lanes'][lane] = totals
        cohort['unknown'] |= unknown
        cohort['blocked'] |= blocked
    table = []
    for variant, cohort in cohorts.items():
        unknown = cohort['unknown'] or frozenset(cohort['lanes']) != LANES
        count = len(cohort['lanes']) * 4
        values = list(cohort['lanes'].values())
        table.append({'variant': variant, 'status': 'UNKNOWN' if unknown else 'BLOCKED' if cohort['blocked'] else 'ELIGIBLE',
                      'lanes': len(values), 'answers': count,
                      'wrong_facts': sum(v['wrong'] for v in values),
                      'critical_misses': sum(v['critical'] for v in values),
                      'misses': sum(v['misses'] for v in values),
                      'mean_bytes': None if unknown else sum(v['bytes_read'] for v in values) / count,
                      'mean_canonical_bytes': None if unknown else sum(v['canonical_bytes'] for v in values) / count,
                      'mean_seconds': None if unknown else sum(v['seconds'] for v in values) / count})
    table.sort(key=lambda row: (row['status'] != 'ELIGIBLE', row['misses'],
                               row['mean_bytes'] if row['mean_bytes'] is not None else math.inf,
                               row['mean_seconds'] if row['mean_seconds'] is not None else math.inf,
                               row['variant']))
    winner = next((row['variant'] for row in table if row['status'] == 'ELIGIBLE'), None)
    return {'schema': 'wd.bridge-mind-tournament-tally.v1', 'authority': 'none',
            'round_capture_sha256': capture_sha256, 'round_context_sha256': context_sha256,
            'round_builder_sha256': builder_sha256, 'winner': winner,
            'refused_records': refused, 'table': table}

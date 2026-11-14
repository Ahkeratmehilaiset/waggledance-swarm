"""Synthetic-first v3 source-checkpoint prototype, not wired into the producer.

Preparation performs one complete reader._verified projection derivation plus
one complete prefix parse/validation to capture page evidence. Later checks
re-validate all input/control/code pins, and run the ORIGINAL prefix protocol
with the same page boundaries and pre/post hashes, identities, generations,
length and cursor checks. Only identical previously validated event bytes and
the immutable pinned projection avoid repeat JSON parsing/semantic derivation.

No check is memoized away. Consumers must retain EVERY existing callback
checkpoint; this prototype does not change the package or query APIs. Prefix
I/O remains quadratic in pages. No lock, immutable-filesystem assumption, ABA
prevention, model runtime or publication authority is provided. In-process
state and code remain caller-trusted, like the existing reader; this is not
an authentication boundary against hostile Python introspection or globals.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

from tools import wd_mind_content_query_readonly as reader


class SourceRecheckRefused(ValueError):
    """Fixed-token refusal without private source/control/exception content."""


def _copy_snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != reader._Q4_SOURCE_KEYS:
        raise ValueError('invalid_snapshot')
    # Validate exact cursor/scalar shapes before copying; deepcopy would execute
    # caller hooks. Clone the frozen cursor too, so object.__setattr__ on the
    # caller's original cannot retarget this checkpoint's identity.
    reader._inputs({key: snapshot[key] for key in reader._INPUT_KEYS})
    return dict(snapshot, messages=dict(snapshot['messages'],
                end_cursor=dataclasses.replace(snapshot['messages']['end_cursor'])))


def _prefix():
    return reader._pinned_dependency('tools.wd_mind_content_prefix_readonly',
        'tools/wd_mind_content_prefix_readonly.py', ('_capture_prefix_proof', '_recheck_prefix'))


def _arguments(snapshot):
    source = snapshot['messages']
    decision = reader._pinned(snapshot['decision_bytes'], snapshot['decision_sha256'])
    return (source['source_path'], source['generation_path'], source['end_cursor'],
            decision['source_prefix']['sha256'], None)


def _code_pin():
    return reader._sha(reader._bounded_source_bytes(Path(__file__), limit=256 * 1024))


class _Checkpoint:
    __slots__ = ('_original', '_snapshot', '_proof', '_code')

    def __init__(self, original, snapshot, proof, code):
        self._original, self._snapshot, self._proof, self._code = original, snapshot, proof, code

    def _check(self):
        snapshot = self._snapshot
        if self._original != snapshot or _code_pin() != self._code:
            raise ValueError('checkpoint_changed')
        # Exact immutable bytes and hash were JSON-validated by _verified at
        # preparation. Preserve its pin-before-control/source ordering here.
        if reader._sha(snapshot['projection_bytes']) != snapshot['projection_sha256']:
            raise ValueError('projection_changed')
        reader._inputs({key: snapshot[key] for key in reader._INPUT_KEYS})
        prefix = _prefix()
        prefix._recheck_prefix(self._proof, *_arguments(snapshot), scope='all_tasks_v1')
        reader._inputs({key: snapshot[key] for key in reader._INPUT_KEYS})
        if self._original != snapshot or _code_pin() != self._code:
            raise ValueError('checkpoint_changed')

    def check(self):
        """Recheck now; returns no records, projection or authorization token."""
        succeeded = False
        try:
            self._check()
            succeeded = True
        except Exception:
            pass
        if not succeeded:
            raise SourceRecheckRefused('content_source_recheck_refused') from None


def prepare_source_recheck(*, enabled=False, snapshot=None):
    """Build private evidence from a complete valid v3 snapshot, or refuse.

    The captured local code digest detects later changes, not initial code
    authenticity. Independent code/source authorization remains the caller's
    responsibility. No projection object supplied by the caller is trusted.
    """
    output = None
    succeeded = False
    try:
        if enabled is not True:
            raise ValueError('disabled')
        code = _code_pin()
        frozen = _copy_snapshot(snapshot)
        if (frozen['approved_tasks_bytes'] != reader._ALL_TASK_SCOPE_BYTES
                or frozen['approved_tasks_sha256'] != reader._ALL_TASK_SCOPE_SHA256):
            raise ValueError('v3_required')
        reader._verified(frozen)
        proof = _prefix()._capture_prefix_proof(*_arguments(frozen), scope='all_tasks_v1')
        reader._inputs({key: frozen[key] for key in reader._INPUT_KEYS})
        if snapshot != frozen or code != _code_pin():
            raise ValueError('snapshot_changed')
        output = _Checkpoint(snapshot, frozen, proof, code)
        succeeded = True
    except Exception:
        pass
    if not succeeded:
        raise SourceRecheckRefused('content_source_recheck_refused') from None
    return output

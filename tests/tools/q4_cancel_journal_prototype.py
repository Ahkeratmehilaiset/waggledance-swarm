"""Test-only in-memory no-start/cleanup model, not a recovery mechanism.

Inputs and witness issuance are simulated, not authenticated observations.
This module never opens or removes a file, owns a Job, clears a marker, permits
a retry, or proves persistence, process cleanup, OS isolation, or safety after
restart. Calls are sequential and do not model real concurrency or atomicity.
It is not wired into any production entrypoint.
"""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

_HEX = re.compile(r'[a-f0-9]{64}\Z')
_FOREIGN = frozenset({'runner_private', 'runner_shared', 'adoption', 'fence', 'private_pending'})


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Binding:
    attempt: str
    reservation_sha256: str
    route_sha256: str
    pointer_sha256: str
    marker_sha256: str
    marker_identity_sha256: str
    previous_record_sha256: str | None
    job_sha256: str

    def __post_init__(self):
        values = asdict(self)
        previous = values.pop('previous_record_sha256')
        if (any(type(value) is not str or _HEX.fullmatch(value) is None for value in values.values())
                or (previous is not None and (type(previous) is not str or _HEX.fullmatch(previous) is None))):
            raise ValueError('invalid_synthetic_binding')

    @classmethod
    def from_reservation(cls, reservation, *, route_sha256, pointer_sha256, marker_sha256,
                         marker_identity_sha256, previous_record_sha256, job_sha256):
        if (type(reservation) is not dict or set(reservation) != {'attempt', 'token'}
                or any(type(value) is not str or _HEX.fullmatch(value) is None for value in reservation.values())):
            raise ValueError('invalid_synthetic_reservation')
        encoded = _raw({'schema': 'wd.q4-runner-reservation.v1', **reservation})
        return cls(attempt=reservation['attempt'], reservation_sha256=_sha(encoded),
                   route_sha256=route_sha256, pointer_sha256=pointer_sha256,
                   marker_sha256=marker_sha256, marker_identity_sha256=marker_identity_sha256,
                   previous_record_sha256=previous_record_sha256, job_sha256=job_sha256)


class _RunnerWitness:
    __slots__ = ('owner', 'binding')

    def __init__(self, owner, binding):
        self.owner, self.binding = owner, binding


class _JobWitness:
    __slots__ = ('owner', 'binding')

    def __init__(self, owner, binding):
        self.owner, self.binding = owner, binding


class SyntheticSession:
    """Cooperative state-model fixture: successful evidence is *not* authorization."""

    def __init__(self, *, enabled=False, binding=None):
        self._enabled = enabled is True
        if enabled is False:
            return  # Do not inspect the binding in the default-off mode.
        if enabled is not True or type(binding) is not Binding:
            raise ValueError('invalid_synthetic_session')
        self._binding = binding
        self._owner = object()
        self._runner = None
        self._job = None
        self._attempted = False
        self._tainted = False
        self._used = False
        self._status = 'UNKNOWN'
        self._foreign = []
        self._journal = []
        self._append('synthetic_started')

    def _append(self, event):
        previous = self._journal[-1]['sha256'] if self._journal else None
        body = {'event': event, 'binding_sha256': _sha(_raw(asdict(self._binding))),
                'previous_sha256': previous}
        self._journal.append({**body, 'sha256': _sha(_raw(body))})

    def runner_create_attempted(self, stage):
        if not self._enabled:
            return
        self._attempted = True
        self._tainted = True
        self._status = 'UNKNOWN'
        self._append('synthetic_create_attempted')

    def runner_no_start(self):
        if not self._enabled:
            return None
        if self._attempted or self._runner is not None or self._tainted or self._used:
            self._tainted = True
            self._status = 'UNKNOWN'
            return None
        self._runner = _RunnerWitness(self._owner, self._binding)
        return self._runner

    def job_cleanup(self, *, outcome, cleanup, active_processes, exit_code):
        if not self._enabled:
            return None
        if (self._runner is None or self._job is not None or self._tainted or self._used
                or outcome != 'completed' or cleanup != 'confirmed'
                or type(active_processes) is not int or active_processes != 0
                or type(exit_code) is not int or exit_code != 2):
            self._tainted = True
            self._status = 'UNKNOWN'
            return None
        self._job = _JobWitness(self._owner, self._binding)
        return self._job

    def observe_foreign(self, label):
        if not self._enabled:
            return
        self._tainted = True
        self._status = 'UNKNOWN'
        if type(label) is str and label in _FOREIGN:
            self._foreign.append(label)
        self._append('synthetic_foreign_state')

    def observed_foreign(self):
        return tuple(self._foreign) if self._enabled else ()

    def interrupt(self):
        if not self._enabled:
            return
        self._tainted = True
        self._status = 'UNKNOWN'
        self._append('synthetic_interrupted')

    def record(self, runner_witness, job_witness):
        if not self._enabled:
            return 'OFF'
        if (self._tainted or self._attempted or self._used
                or type(runner_witness) is not _RunnerWitness or runner_witness is not self._runner
                or type(job_witness) is not _JobWitness or job_witness is not self._job
                or runner_witness.owner is not self._owner or job_witness.owner is not self._owner
                or runner_witness.binding != self._binding or job_witness.binding != self._binding):
            self._tainted = True
            self._status = 'UNKNOWN'
            return 'UNKNOWN'
        self._used = True
        self._append('synthetic_no_start')
        self._append('synthetic_job_cleanup')
        self._status = 'SYNTHETIC_EVIDENCE_ONLY'
        return self._status

    def status(self):
        return self._status if self._enabled else 'OFF'

    def automatic_retry_allowed(self):
        return False

    def export_journal(self):
        return _raw(self._journal) if self._enabled else b''


def recover(raw, *, enabled=False):
    # A synthetic in-memory log cannot prove durable writes or completed Job
    # cleanup across restart, even when its bytes appear complete.
    return 'OFF' if enabled is False else 'UNKNOWN'

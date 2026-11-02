"""Fail-closed fixed-Q4 peer placeholder, not a production transport.

There is currently no independently authenticated runtime/closure and no
bound verified-byte consumer. Never emit READY, read a query, execute caller
code, or launch an interpreter on this path until those inputs exist.
"""


class FixedPeerRefused(ValueError):
    """Fixed refusal without input, path or backend detail."""


def prepare_fixed_q4(control=None, *, trusted_binding=None, enabled=False):
    """Refuse without inspecting inputs: no authorized fixed Q4 peer exists."""
    del control, trusted_binding, enabled
    raise FixedPeerRefused("fixed_peer_unavailable")

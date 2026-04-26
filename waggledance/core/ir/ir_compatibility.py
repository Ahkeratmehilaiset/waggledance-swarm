"""IR compatibility — Phase 9 §G.

ir_compat_version semantics: consumers refuse unknown major versions.
This module exposes the helpers consumers use to decide whether an
incoming IR object is safe to consume.
"""
from __future__ import annotations

from . import IR_COMPAT_VERSION
from .cognition_ir import IRObject


def is_compatible(obj: IRObject,
                       consumer_max_compat_version: int = IR_COMPAT_VERSION,
                       ) -> bool:
    """True iff this consumer can safely interpret `obj`."""
    return 1 <= obj.ir_compat_version <= consumer_max_compat_version


def reason_incompatible(obj: IRObject,
                              consumer_max_compat_version: int = IR_COMPAT_VERSION,
                              ) -> str | None:
    if obj.ir_compat_version < 1:
        return f"ir_compat_version {obj.ir_compat_version} is below 1"
    if obj.ir_compat_version > consumer_max_compat_version:
        return (
            f"ir_compat_version {obj.ir_compat_version} exceeds "
            f"consumer max {consumer_max_compat_version}"
        )
    return None

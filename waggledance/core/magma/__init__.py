"""MAGMA adaptation layer — wraps legacy MAGMA L1-L5 for the autonomy core."""

from waggledance.core.magma.confidence_decay import decayed_confidence

__all__ = ["decayed_confidence"]

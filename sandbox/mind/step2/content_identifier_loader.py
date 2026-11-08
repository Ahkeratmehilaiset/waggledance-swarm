"""Synthetic, explicit-input boundary for the private identifier policy.

No files, environment, persistence or key provisioning are consulted here.
The caller owns input provenance, key protection, and cleanup of its own frames.
This boundary cannot clear foreign frames or references retained elsewhere.
An HMAC checks consistency with the supplied key, not the caller's authority.
"""

from sandbox.mind.step2 import content_identifier_policy as policy


class IdentifierLoaderRefused(ValueError):
    """Fixed refusal with no input or backend exception text."""


def load_identifier_policy(policy_bytes, key, expected_tag):
    """Load only the supplied bytes, key and tag; propagate real interrupts."""
    try:
        return policy.load_policy(policy_bytes, key, expected_tag)
    except Exception:
        pass
    finally:
        policy_bytes = key = expected_tag = None
    # Outside the handler: no suppressed backend exception in __context__.
    raise IdentifierLoaderRefused("identifier_loader_refused") from None

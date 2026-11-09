"""Default-off argument builder for an explicitly authorized session tuple.

This module performs syntactic and internal consistency checks only. The caller
must independently authenticate every input, the entry code and interpreter;
no descriptor or manifest is trusted here. No files, environment variables,
or network are accessed and no query field is accepted. Returned arguments must
be passed as an argument vector, never interpolated into a shell command.
"""

import os
from pathlib import Path
import re


_WORKER = (
    "lane", "bundle-root", "public-plan", "interpreter", "interpreter-sha256", "interpreter-max-bytes",
    "cwd", "timeout-seconds", "max-output-bytes", "fleet", "fleet-sha256", "binding", "binding-sha256",
    "fixed-plan", "fixed-plan-sha256", "snapshot", "snapshot-sha256", "runtime-manifest",
    "runtime-manifest-sha256",
)
_OUTER = (
    "outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
    "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
    "worker-sha256",
)
_BINDING = (
    "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
    "public_policy_sha256", "descriptor_sha256", "projection_sha256",
)
_PATHS = (
    "outer-path", "runtime-root", "runtime-manifest", "bundle-root", "public-plan", "interpreter", "cwd",
    "fleet", "binding", "fixed-plan", "snapshot",
)
_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def build_session_argv(worker, outer, expected_binding, *, enabled=False):
    """Return the exact entry argument vector; this is not authorization or execution."""
    try:
        if enabled is not True:
            raise ValueError()
        if (type(worker) is not dict or set(worker) != set(_WORKER)
                or type(outer) is not dict or set(outer) != set(_OUTER)
                or type(expected_binding) is not dict or set(expected_binding) != set(_BINDING)):
            raise ValueError()
        values = {**worker, **outer}
        values.update(("expected-" + key.replace("_", "-"), expected_binding[key]) for key in _BINDING)
        if any(type(value) is not str or not value or len(value) > 32767
               or any(ord(char) < 32 for char in value) for value in values.values()):
            raise ValueError()
        if values["lane"] not in _LANES:
            raise ValueError()
        for key, value in values.items():
            if (key.endswith("sha256") or key.startswith("expected-")) and not _HEX.fullmatch(value):
                raise ValueError()
        if any(not _IDENTITY.fullmatch(values[key]) for key in ("runtime-generation", "runtime-revision")):
            raise ValueError()
        for key in _PATHS:
            value = Path(values[key])
            if not value.is_absolute() or ".." in value.parts or str(value).startswith(("\\\\", "//")):
                raise ValueError()
        # Only textual path collisions are detected; hard links and aliases
        # cannot be recognized without the filesystem or trusted descriptors.
        pins = {}
        for key in ("outer-path", "interpreter", "fleet", "binding", "fixed-plan", "snapshot", "runtime-manifest"):
            path_key = os.path.normcase(os.path.abspath(values[key]))
            digest = values[("outer-sha256" if key == "outer-path" else key + "-sha256")]
            if path_key in pins and pins[path_key] != digest:
                raise ValueError()
            pins[path_key] = digest
        for key, limit in (("interpreter-max-bytes", 128 * 1024 * 1024), ("timeout-seconds", 1800),
                           ("max-output-bytes", 65536)):
            value = values[key]
            if not value.isascii() or not value.isdecimal() or len(value) > 10 or not 1 <= int(value) <= limit:
                raise ValueError()
        wanted = Path(values["runtime-root"]) / "assets" / "code" / "tools" / "wd_q4_session_outer_synthetic.py"
        if os.path.normcase(os.path.abspath(wanted)) != os.path.normcase(os.path.abspath(values["outer-path"])):
            raise ValueError()
        result = ["--enabled"]
        for key in _WORKER + _OUTER + tuple("expected-" + key.replace("_", "-") for key in _BINDING):
            result.extend(("--" + key, values[key]))
        return result
    except Exception:
        pass
    # The caller owns its inputs and frames. This only avoids retaining them
    # in this function's refusal traceback; it does not promise memory erasure.
    worker = outer = expected_binding = enabled = None
    values = value = key = pins = path_key = digest = wanted = result = limit = None
    raise ValueError("session_tuple_refused") from None

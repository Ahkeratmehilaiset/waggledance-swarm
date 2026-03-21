"""
Disk space guard — warns on low disk, refuses writes when critical.
Used by memory_engine, audit_log, replay_store, cognitive_graph, waggle_backup.
"""

import logging
import shutil
from pathlib import Path

log = logging.getLogger("waggledance.disk_guard")

# Thresholds in MB
WARN_THRESHOLD_MB = 500
REFUSE_THRESHOLD_MB = 100


class DiskSpaceError(OSError):
    """Raised when disk space is critically low and writes are refused."""
    pass


def check_disk_space(path: str = ".", label: str = "") -> dict:
    """
    Check free disk space at the given path.

    Returns dict with:
        free_mb: float — free space in MB
        free_gb: float — free space in GB
        total_gb: float — total space in GB
        status: str — "ok", "warning", or "critical"

    Raises DiskSpaceError if free < REFUSE_THRESHOLD_MB.
    Logs warning if free < WARN_THRESHOLD_MB.
    """
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        # Can't check → allow the operation
        return {"free_mb": -1, "free_gb": -1, "total_gb": -1, "status": "unknown"}

    free_mb = usage.free / (1024 * 1024)
    free_gb = usage.free / (1024 ** 3)
    total_gb = usage.total / (1024 ** 3)

    result = {
        "free_mb": round(free_mb, 1),
        "free_gb": round(free_gb, 2),
        "total_gb": round(total_gb, 2),
        "status": "ok",
    }

    prefix = f"[{label}] " if label else ""

    if free_mb < REFUSE_THRESHOLD_MB:
        result["status"] = "critical"
        msg = f"{prefix}Disk space critically low: {free_mb:.0f} MB free (< {REFUSE_THRESHOLD_MB} MB). Write REFUSED."
        log.error(msg)
        raise DiskSpaceError(msg)

    if free_mb < WARN_THRESHOLD_MB:
        result["status"] = "warning"
        log.warning(f"{prefix}Disk space low: {free_mb:.0f} MB free (< {WARN_THRESHOLD_MB} MB)")

    return result


def get_disk_status(path: str = ".") -> dict:
    """Get disk status without raising (for status endpoints)."""
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free / (1024 * 1024)
        free_gb = usage.free / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        if free_mb < REFUSE_THRESHOLD_MB:
            status = "critical"
        elif free_mb < WARN_THRESHOLD_MB:
            status = "warning"
        else:
            status = "ok"
        return {
            "free_mb": round(free_mb, 1),
            "free_gb": round(free_gb, 2),
            "total_gb": round(total_gb, 2),
            "status": status,
        }
    except OSError:
        return {"free_mb": -1, "free_gb": -1, "total_gb": -1, "status": "unknown"}

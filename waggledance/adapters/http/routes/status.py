"""Health and readiness HTTP routes."""

import platform
import sys
from dataclasses import asdict
from functools import lru_cache

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from waggledance.adapters.http.deps import get_readiness_service

router = APIRouter()


@lru_cache(maxsize=1)
def _resolve_package_version() -> str:
    """Read the installed distribution version (cached for process lifetime).

    Prefers ``importlib.metadata`` (works for wheel installs and editable
    installs alike). Falls back to parsing ``pyproject.toml`` for source
    checkouts that have not been ``pip install``-ed yet. Final fallback is
    the string ``"unknown"`` -- the endpoint must never raise.

    The result is cached because the version cannot change within a process
    lifetime; this keeps ``GET /version`` from re-reading ``pyproject.toml``
    on every scrape in source-checkout installs.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("waggledance-swarm")
        except PackageNotFoundError:
            pass
    except Exception:
        pass
    # Source-checkout fallback: parse pyproject.toml from the repo root.
    try:
        import tomllib
        from pathlib import Path
        # status.py -> routes -> http -> adapters -> waggledance -> repo root
        repo_root = Path(__file__).resolve().parents[4]
        pyproject = repo_root / "pyproject.toml"
        if pyproject.is_file():
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            v = (data.get("project") or {}).get("version")
            if isinstance(v, str) and v:
                return v
    except Exception:
        pass
    return "unknown"


@router.get("/health")
@router.get("/healthz")
async def health():
    """Simple liveness check -- always returns ok.

    ``/healthz`` is the Kubernetes-convention alias so k8s, Nomad and
    docker-compose health probes work out of the box without a
    deployment config rewrite.
    """
    return {"status": "ok"}


@router.get("/ready")
@router.get("/readyz")
async def ready(
    svc=Depends(get_readiness_service),
):
    """Readiness check -- queries all components via ReadinessService.

    ``/readyz`` is the Kubernetes-convention alias (see ``/healthz`` above).
    """
    status = await svc.check()
    code = 200 if status.ready else 503
    return JSONResponse(status_code=code, content=asdict(status))


@router.get("/version")
async def version():
    """Return the running WaggleDance version + interpreter info.

    Public endpoint (mirrors ``/healthz`` exposure) -- deliberately
    returns no secrets, no hostnames, no paths. The shape is stable:
    ``{"name": str, "version": str, "python": str, "platform": str}``.

    Operators use this to confirm which build is actually running after
    a rolling restart without needing shell access to the host.
    """
    return {
        "name": "waggledance-swarm",
        "version": _resolve_package_version(),
        "python": sys.version.split()[0],
        "platform": platform.system().lower(),
    }

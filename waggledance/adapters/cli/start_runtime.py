"""WaggleDance runtime entry point (hexagonal architecture).

Primary entrypoint for the new waggledance/ package.
Legacy entrypoints: main.py, start.py (see ENTRYPOINTS.md).

Usage:
    python -m waggledance.adapters.cli.start_runtime              # production
    python -m waggledance.adapters.cli.start_runtime --stub        # stub mode
    python -m waggledance.adapters.cli.start_runtime --port 9000   # custom port
    python -m waggledance.adapters.cli.start_runtime --log-level debug
"""

import argparse
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _setup_windows_utf8() -> None:
    """Apply Windows UTF-8 fix (same 3-layer approach as main.py).

    Without this, Finnish characters (ae/oe/aa) produce mojibake on
    Windows. The three layers are:

    1. Console code page set to 65001 (UTF-8) via ``chcp.com``.
    2. Environment-level hints for any child Python processes.
    3. Stdout/stderr reconfigured to emit UTF-8 with replacement on
       undecodable bytes.

    Layer 1 previously used ``os.system("chcp 65001 > nul 2>&1")``
    which spawned a ``cmd.exe`` subprocess just to parse the redirect.
    We now call ``chcp.com`` directly via ``subprocess.run`` so Git
    Bash and PowerShell don't unnecessarily fork a cmd shell.
    """
    if sys.platform != "win32":
        return

    try:
        import subprocess  # local import — keeps module import cheap
        subprocess.run(
            ["chcp.com", "65001"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
        )
    except (OSError, FileNotFoundError):
        # chcp.com absent (extremely minimal Windows install) — the
        # env-var + stdout.reconfigure layers below still give us UTF-8
        # for Python output, so this is not fatal.
        pass

    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUNBUFFERED"] = "1"

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


def _ollama_probe_timeout() -> float:
    """Resolve the Ollama probe timeout in seconds.

    Default is 5.0 s (warm local Ollama responds in ~15 ms, so the
    probe has 300x headroom locally; 5 s also gives usable headroom
    for operators who mount Ollama across a WAN / slow bridge).

    Overridable via ``WAGGLE_OLLAMA_PROBE_TIMEOUT`` env var. Invalid
    values silently fall back to the default.
    """
    raw = os.environ.get("WAGGLE_OLLAMA_PROBE_TIMEOUT", "").strip()
    if not raw:
        return 5.0
    try:
        val = float(raw)
    except ValueError:
        return 5.0
    # Guard rails: ignore nonsense like 0, negatives, or multi-minute.
    if val <= 0 or val > 60:
        return 5.0
    return val


def _check_ollama(host: str) -> tuple[bool, str]:
    """Quick Ollama health check.

    Returns ``(ok, reason)`` where ``reason`` is an empty string on
    success or a short operator-readable description of the failure
    mode (``"timed out"``, ``"connection refused"``, etc.). The
    reason is echoed in the startup banner so operators can tell
    apart "Ollama down" / "wrong port" / "slow WAN link".

    Timeout is resolved by ``_ollama_probe_timeout()``.
    """
    import urllib.error
    import urllib.request

    url = f"{host}/api/tags"
    timeout = _ollama_probe_timeout()
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        if resp.status == 200:
            return True, ""
        return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        # Normalise the nested reason into a short string.
        reason = getattr(exc, "reason", None)
        if reason is None:
            return False, "URLError"
        reason_str = str(reason).strip()
        # Windows WinError numeric prefix is noise for operators.
        if "timed out" in reason_str.lower():
            return False, "timed out"
        if "10061" in reason_str or "refused" in reason_str.lower():
            return False, "connection refused"
        return False, reason_str.splitlines()[0][:80]
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"{type(exc).__name__}: {exc}"[:120]


def _resolve_lan_ip() -> str | None:
    """Best-effort LAN IP detection. Returns None on failure."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except Exception:
        pass
    return None


def _print_banner(
    stub: bool, host: str, port: int, log_level: str,
    settings=None,
) -> None:
    """Print startup banner with runtime diagnostics."""
    mode = "STUB" if stub else "PRODUCTION"
    runtime_primary = getattr(settings, "runtime_primary", "waggledance") if settings else "waggledance"
    compat = getattr(settings, "compatibility_mode", False) if settings else False
    profile = getattr(settings, "profile", "?") if settings else "?"
    compat_str = "ON" if compat else "OFF"

    # Build user-facing URLs distinct from bind address
    local_url = f"http://localhost:{port}"
    bind_str = f"{host}:{port}"
    lan_line = ""
    if host in ("0.0.0.0", "::"):
        lan_ip = _resolve_lan_ip()
        if lan_ip:
            lan_line = f"\n  |  LAN URL:   http://{lan_ip}:{port:<17}|"

    print(f"""
  +=============================================+
  |  WaggleDance AI — New Runtime (hexagonal)   |
  +=============================================+
  |  Mode:      {mode:<33}|
  |  Primary:   {runtime_primary:<33}|
  |  Compat:    {compat_str:<33}|
  |  Profile:   {profile:<33}|
  |  Bind:      {bind_str:<33}|
  |  Log level: {log_level:<33}|
  |  Local URL: {local_url:<33}|{lan_line}
  |  Stop:      Ctrl+C                          |
  +=============================================+
""", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments. Extracted for testability."""
    parser = argparse.ArgumentParser(
        description="WaggleDance AI runtime (hexagonal architecture)",
    )
    parser.add_argument(
        "--stub",
        action="store_true",
        default=False,
        help="Start in stub mode (no Ollama/ChromaDB needed)",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="Bind address (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Listen port (default: 8000)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="warning",
        choices=["debug", "info", "warning", "error", "critical"],
        help="Logging level (default: warning)",
    )
    parser.add_argument(
        "--check-autonomy",
        action="store_true",
        default=False,
        help="Run cutover validation and exit (does not start the server)",
    )
    return parser.parse_args(argv)


def _install_windows_proactor_filter() -> None:
    """Suppress benign WinError 10054 noise from asyncio ProactorEventLoop.

    On Windows, the Proactor event loop emits noisy ConnectionResetError
    (WinError 10054) tracebacks during normal connection teardown.  These
    are harmless but pollute stderr.

    We use a logging.Filter on the 'asyncio' logger because uvicorn creates
    its own event loop (bypassing custom event loop policies).
    """
    if sys.platform != "win32":
        return

    class _Win10054Filter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            msg = record.getMessage()
            if "10054" in msg and "ConnectionResetError" in msg:
                return False  # suppress
            exc = record.exc_info
            if exc and exc[1] and isinstance(exc[1], ConnectionResetError):
                if "10054" in str(exc[1]):
                    return False
            return True

    logging.getLogger("asyncio").addFilter(_Win10054Filter())


def main(argv: list[str] | None = None) -> None:
    """Build and run the WaggleDance application."""
    _setup_windows_utf8()
    _install_windows_proactor_filter()

    args = parse_args(argv)

    # --check-autonomy: validate and exit without starting the server
    if args.check_autonomy:
        from waggledance.tools.validate_cutover import run_validation

        success = run_validation()
        sys.exit(0 if success else 1)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    if not args.stub:
        from waggledance.adapters.config.settings_loader import WaggleSettings

        settings = WaggleSettings.from_env()
        ollama_host = settings.ollama_host

        ok, reason = _check_ollama(ollama_host)
        if not ok:
            logger.warning(
                "Ollama probe failed at %s: %s", ollama_host, reason or "unknown"
            )
            print(f"\n  WARNING: Ollama probe failed at {ollama_host}")
            if reason:
                print(f"  Reason: {reason}")
            print("  Production mode requires Ollama with models:")
            print("    phi4-mini, llama3.2:1b, nomic-embed-text, all-minilm")
            print("  Use --stub for testing without Ollama.")
            print(
                "  The probe timeout can be raised via WAGGLE_OLLAMA_PROBE_TIMEOUT "
                "(seconds).\n"
            )
    else:
        from waggledance.adapters.config.settings_loader import WaggleSettings

        settings = WaggleSettings.from_env()

    # Log runtime diagnostics
    diag = settings.runtime_diagnostics()
    logger.info("Runtime diagnostics: %s", diag)

    _print_banner(args.stub, args.host, args.port, args.log_level, settings=settings)

    from waggledance.bootstrap.container import Container

    container = Container(settings=settings, stub=args.stub)
    app = container.build_app()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()

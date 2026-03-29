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

    Without this, Finnish characters (ae/oe/aa) produce mojibake on Windows.
    """
    if sys.platform != "win32":
        return

    os.system("chcp 65001 > nul 2>&1")
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


def _check_ollama(host: str) -> bool:
    """Quick Ollama health check. Returns True if reachable."""
    try:
        import urllib.request

        url = f"{host}/api/tags"
        req = urllib.request.urlopen(url, timeout=3)
        return req.status == 200
    except Exception:
        return False


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
    are harmless but pollute stderr.  We install a narrow exception handler
    that silences only this specific case and re-raises everything else.
    """
    if sys.platform != "win32":
        return

    import asyncio

    _orig_handler = asyncio.get_event_loop_policy

    class _QuietProactorPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            _orig_exc_handler = loop.get_exception_handler()

            def _filter(loop, context):
                exc = context.get("exception")
                if isinstance(exc, ConnectionResetError) and "10054" in str(exc):
                    return  # suppress benign WinError 10054
                if _orig_exc_handler:
                    _orig_exc_handler(loop, context)
                else:
                    loop.default_exception_handler(context)

            loop.set_exception_handler(_filter)
            return loop

    asyncio.set_event_loop_policy(_QuietProactorPolicy())


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

        if not _check_ollama(ollama_host):
            logger.warning("Ollama not reachable at %s", ollama_host)
            print(f"\n  WARNING: Ollama is not running at {ollama_host}")
            print("  Production mode requires Ollama with models:")
            print("    phi4-mini, llama3.2:1b, nomic-embed-text, all-minilm")
            print("  Use --stub for testing without Ollama.\n")
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

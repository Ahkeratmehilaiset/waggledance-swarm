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
    print(f"""
  +=============================================+
  |  WaggleDance AI — New Runtime (hexagonal)   |
  +=============================================+
  |  Mode:      {mode:<33}|
  |  Primary:   {runtime_primary:<33}|
  |  Compat:    {compat_str:<33}|
  |  Profile:   {profile:<33}|
  |  Listen:    {host}:{port:<24}|
  |  Log level: {log_level:<33}|
  |  Dashboard: http://{host}:{port:<17}|
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Build and run the WaggleDance application."""
    _setup_windows_utf8()

    args = parse_args(argv)

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

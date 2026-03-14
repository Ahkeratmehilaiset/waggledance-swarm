"""Clean entry point for WaggleDance runtime.

Usage:
    python -m waggledance.adapters.cli.start_runtime          # production
    python -m waggledance.adapters.cli.start_runtime --stub    # stub mode
"""

import sys

import uvicorn

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container


def main():
    """Build and run the WaggleDance application."""
    settings = WaggleSettings.from_env()
    stub = "--stub" in sys.argv
    container = Container(settings=settings, stub=stub)
    app = container.build_app()
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""WaggleDance entry point — delegates to hexagonal runtime.

Usage:
    python start_waggledance.py                            # interactive
    python start_waggledance.py --preset=raspberry-pi-iot  # one-command IoT
    python start_waggledance.py --preset=cottage-full      # full cottage
    python start_waggledance.py --preset=factory-production # factory
"""
from waggledance.adapters.cli.start_runtime import main

if __name__ == "__main__":
    main()

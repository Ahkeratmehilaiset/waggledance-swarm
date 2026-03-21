#!/usr/bin/env python3
"""WaggleDance entry point with preset support.

Usage:
    python start_waggledance.py                          # interactive
    python start_waggledance.py --preset=raspberry-pi-iot  # one-command IoT
    python start_waggledance.py --preset=cottage-full      # full cottage
    python start_waggledance.py --preset=factory-production # factory
"""
import argparse
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None


def main():
    parser = argparse.ArgumentParser(description="WaggleDance Swarm AI")
    parser.add_argument("--preset", choices=["raspberry-pi-iot", "cottage-full", "factory-production"],
                        help="Hardware preset profile")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stub", action="store_true")
    args = parser.parse_args()

    if args.preset:
        preset_path = Path("configs/presets") / f"{args.preset}.yaml"
        if not preset_path.exists():
            print(f"Preset not found: {preset_path}")
            sys.exit(1)
        if yaml is None:
            print("PyYAML required for preset support: pip install pyyaml")
            sys.exit(1)
        with open(preset_path) as f:
            preset = yaml.safe_load(f)
        print(f"WaggleDance starting with preset: {args.preset}")
        print(f"   Profile: {preset.get('profile')}, Agents: {preset.get('agents_max')}")

    # Build argv for runtime, stripping --preset which it doesn't understand
    runtime_argv = []
    if args.port != 8000:
        runtime_argv += ["--port", str(args.port)]
    if args.stub:
        runtime_argv.append("--stub")

    from waggledance.adapters.cli.start_runtime import main as runtime_main
    runtime_main(runtime_argv if runtime_argv else None)


if __name__ == "__main__":
    main()

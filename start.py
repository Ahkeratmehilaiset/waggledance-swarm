#!/usr/bin/env python3
"""
WaggleDance Launcher — Choose Stub or Production mode.

DEPRECATED: This launcher uses the legacy runtime (hivemind.py + main.py).
For the new hexagonal architecture, use:
    python -m waggledance.adapters.cli.start_runtime [--stub] [--port PORT]
See ENTRYPOINTS.md for details.

Usage:
    python start.py              -> interactive menu
    python start.py --stub       -> start stub backend + React dev server
    python start.py --production -> start full HiveMind (requires Ollama)
"""
import argparse
import subprocess
import sys
import os
import time

import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(ROOT, "configs", "settings.yaml")
PROFILES = ["gadget", "cottage", "home", "factory"]


def get_current_profile() -> str:
    """Read current profile from settings.yaml."""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        return cfg.get("profile", "cottage")
    except Exception:
        return "cottage"


def set_profile(profile: str):
    """Write profile to settings.yaml."""
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        content = f.read()
    # Replace existing profile line
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("profile:"):
            lines[i] = f"profile: {profile}  # gadget | cottage | home | factory"
            break
    else:
        lines.insert(0, f"profile: {profile}  # gadget | cottage | home | factory")
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def select_profile():
    """Interactive profile selection."""
    current = get_current_profile()
    descs = {
        "gadget": "ESP32/RPi Zero — 3-5 critical agents only",
        "cottage": "Cottage environment — outdoor, forest, lake, fire",
        "home": "Urban home — IoT, energy, traffic, smart home",
        "factory": "Factory — production lines, quality, safety, maintenance",
    }
    print("\n  ╔══════════════════════════════════════════╗")
    print("  ║   Agent Profile Selection                 ║")
    print("  ╠══════════════════════════════════════════╣")
    for i, p in enumerate(PROFILES, 1):
        marker = " ◀" if p == current else ""
        print(f"  ║  {i}. {p:<10} {descs[p]:<28}{marker} ║")
    print("  ╚══════════════════════════════════════════╝")
    print(f"\n  Current profile: {current}")
    choice = input(f"  Select profile [1-4] (Enter = keep {current}): ").strip()
    if choice in ("1", "2", "3", "4"):
        new_profile = PROFILES[int(choice) - 1]
        if new_profile != current:
            set_profile(new_profile)
            print(f"  ✅ Profile changed: {current} → {new_profile}")
        else:
            print(f"  Profile unchanged: {current}")
        return new_profile
    return current


def check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return req.status == 200
    except Exception:
        return False


def _print_deprecation_warning(mode: str) -> None:
    """Print deprecation notice for legacy modes."""
    print(f"\n  NOTE: Legacy {mode} mode. For the new runtime, use:")
    print("    python -m waggledance.adapters.cli.start_runtime [--stub]")
    print("  See ENTRYPOINTS.md for details.\n")


def start_new_runtime():
    """Start the new hexagonal runtime via start_runtime.py."""
    print("\n  Starting WaggleDance — New Runtime (hexagonal)...")
    print("  Server:    http://localhost:8000")
    print("  Use --stub for testing without Ollama.\n")

    cmd = [sys.executable, "-m", "waggledance.adapters.cli.start_runtime"]

    # Pass through --stub if the user wants stub mode
    resp = input("  Start in stub mode? [y/N]: ").strip().lower()
    if resp == "y":
        cmd.append("--stub")

    proc = subprocess.Popen(cmd, cwd=ROOT)

    print("  Server running. Press Ctrl+C to stop.\n")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        proc.terminate()


def start_stub():
    """Start backend stub (port 8000) + React dev server (port 5173)."""
    _print_deprecation_warning("STUB")
    print("  Starting WaggleDance in STUB mode...")
    print("  Backend:   http://localhost:8000  (standalone, no Ollama)")
    print("  Dashboard: http://localhost:5173  (React + Vite)\n")

    backend_proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app",
         "--host", "0.0.0.0", "--port", "8000", "--reload"],
        cwd=ROOT,
    )
    time.sleep(1)

    # Check if npm/npx is available for Vite
    dashboard_dir = os.path.join(ROOT, "dashboard")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    dashboard_proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=dashboard_dir,
    )

    print("  Both servers running. Press Ctrl+C to stop.\n")
    try:
        backend_proc.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        backend_proc.terminate()
        dashboard_proc.terminate()


def start_production():
    """Start full HiveMind via main.py (requires Ollama)."""
    _print_deprecation_warning("PRODUCTION")
    if not check_ollama():
        print("\n  WARNING: Ollama is not running at localhost:11434")
        print("  Production mode requires Ollama with these models:")
        print("    - phi4-mini, llama3.2:1b, nomic-embed-text, all-minilm")
        resp = input("\n  Continue anyway? [y/N]: ").strip().lower()
        if resp != "y":
            print("  Aborted. Start Ollama first, then retry.")
            return

    print("\n  Starting WaggleDance in PRODUCTION mode...")
    print("  HiveMind:  http://localhost:8000  (full system)")
    print("  Dashboard: http://localhost:5173  (React + Vite)\n")

    hivemind_proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "main.py")],
        cwd=ROOT,
    )
    time.sleep(2)

    dashboard_dir = os.path.join(ROOT, "dashboard")
    npm_cmd = "npm.cmd" if sys.platform == "win32" else "npm"
    dashboard_proc = subprocess.Popen(
        [npm_cmd, "run", "dev"],
        cwd=dashboard_dir,
    )

    print("  Both servers running. Press Ctrl+C to stop.\n")
    try:
        hivemind_proc.wait()
    except KeyboardInterrupt:
        print("\n  Shutting down...")
        hivemind_proc.terminate()
        dashboard_proc.terminate()


def interactive_menu():
    """Show interactive mode selection."""
    current_profile = get_current_profile()
    print()
    print("  +======================================+")
    print("  |   WaggleDance Launcher               |")
    print("  +======================================+")
    print("  |  1. STUB mode (legacy)               |")
    print("  |     No Ollama needed                 |")
    print("  |     Real GPU stats, demo data        |")
    print("  |                                      |")
    print("  |  2. PRODUCTION mode (legacy)         |")
    print("  |     Requires Ollama + 4 models       |")
    print("  |     Full HiveMind, real AI           |")
    print("  |                                      |")
    print("  |  3. NEW RUNTIME (recommended)        |")
    print("  |     Hexagonal architecture            |")
    print("  |     Ports & adapters, DI container   |")
    print("  |                                      |")
    print("  |  4. Change PROFILE                   |")
    print(f"  |     Current: {current_profile:<24}|")
    print("  +======================================+")

    ollama_status = "running" if check_ollama() else "NOT running"
    print(f"\n  Ollama: {ollama_status}")
    print(f"  Profile: {current_profile}")

    choice = input("\n  Select [1/2/3/4]: ").strip()
    if choice == "2":
        start_production()
    elif choice == "3":
        start_new_runtime()
    elif choice == "4":
        select_profile()
        interactive_menu()  # Return to menu after profile change
    else:
        start_stub()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WaggleDance Launcher")
    parser.add_argument("--stub", action="store_true", help="Start in stub mode (legacy)")
    parser.add_argument("--production", "--prod", action="store_true",
                        help="Start in production mode (legacy)")
    parser.add_argument("--new-runtime", action="store_true",
                        help="Start the new hexagonal runtime (recommended)")
    parser.add_argument("--profile", choices=PROFILES,
                        help="Set active profile before starting")
    args = parser.parse_args()

    if args.profile:
        set_profile(args.profile)
        print(f"  Profile set to: {args.profile}")

    if args.new_runtime:
        start_new_runtime()
    elif args.stub:
        start_stub()
    elif args.production:
        start_production()
    else:
        interactive_menu()

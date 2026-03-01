#!/usr/bin/env python3
"""
WaggleDance Launcher — Choose Stub or Production mode.

Usage:
    python start.py              → interactive menu
    python start.py --stub       → start stub backend + React dev server
    python start.py --production → start full HiveMind (requires Ollama)
"""
import argparse
import subprocess
import sys
import os
import time

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_ollama() -> bool:
    """Check if Ollama is running."""
    try:
        import urllib.request
        req = urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        return req.status == 200
    except Exception:
        return False


def start_stub():
    """Start backend stub (port 8000) + React dev server (port 5173)."""
    print("\n  Starting WaggleDance in STUB mode...")
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
    print()
    print("  ╔══════════════════════════════════════╗")
    print("  ║   WaggleDance Launcher               ║")
    print("  ╠══════════════════════════════════════╣")
    print("  ║  1. STUB mode                        ║")
    print("  ║     No Ollama needed                 ║")
    print("  ║     Real GPU stats, demo data        ║")
    print("  ║     For dashboard development        ║")
    print("  ║                                      ║")
    print("  ║  2. PRODUCTION mode                  ║")
    print("  ║     Requires Ollama + 4 models       ║")
    print("  ║     Full HiveMind, real AI           ║")
    print("  ║     ChromaDB, agents, learning       ║")
    print("  ╚══════════════════════════════════════╝")

    ollama_status = "running" if check_ollama() else "NOT running"
    print(f"\n  Ollama: {ollama_status}")

    choice = input("\n  Select mode [1/2]: ").strip()
    if choice == "2":
        start_production()
    else:
        start_stub()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WaggleDance Launcher")
    parser.add_argument("--stub", action="store_true", help="Start in stub mode")
    parser.add_argument("--production", "--prod", action="store_true",
                        help="Start in production mode")
    args = parser.parse_args()

    if args.stub:
        start_stub()
    elif args.production:
        start_production()
    else:
        interactive_menu()

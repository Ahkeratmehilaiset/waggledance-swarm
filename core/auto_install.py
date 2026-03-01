"""Auto-install missing dependencies at startup."""
import importlib
import importlib.util
import subprocess
import sys
import os
import logging

log = logging.getLogger("waggledance.auto_install")


def _safe_print(msg: str) -> None:
    """Print with fallback for consoles that can't handle Unicode/emoji."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))

REQUIRED = [
    # Core
    ("chromadb",       "chromadb"),
    ("fastapi",        "fastapi"),
    ("uvicorn",        "uvicorn"),
    ("yaml",           "pyyaml"),
    ("psutil",         "psutil"),
    ("httpx",          "httpx"),
    ("aiosqlite",      "aiosqlite"),
    ("websockets",     "websockets"),
    # Translation (Opus-MT)
    ("transformers",   "transformers"),
    ("torch",          "torch"),
    ("sentencepiece",  "sentencepiece"),
]

OPTIONAL = [
    ("libvoikko",  "libvoikko",          "Finnish lemmatization — falling back to suffix-stripping"),
    ("duckduckgo_search", "duckduckgo-search", "Web search for Oracle agent — web search disabled"),
]


def _can_import(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _pip_install(pip_name: str, quiet: bool = True) -> bool:
    cmd = [sys.executable, "-m", "pip", "install", pip_name]
    if quiet:
        cmd.append("-q")
    # --break-system-packages for distro Pythons (Debian 12+, etc.)
    cmd.append("--break-system-packages")
    try:
        subprocess.check_call(
            cmd,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
        )
        return True
    except Exception:
        return False


def _check_voikko_dict() -> None:
    """If libvoikko importable, check that dict folder exists."""
    if not _can_import("libvoikko"):
        return
    # Search paths mirroring core/normalizer.py _init_voikko()
    search = [
        os.environ.get("VOIKKO_DICTIONARY_PATH"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "voikko"),
    ]
    if sys.platform == "win32":
        search += [r"U:\project\voikko", r"C:\voikko"]
    else:
        search += ["/usr/lib/voikko", "/usr/share/voikko"]

    for p in search:
        if p and os.path.isdir(p) and os.path.isdir(os.path.join(p, "5")):
            return  # dict found

    _safe_print("  ⚠️  Voikko dictionary not found.")
    _safe_print("      Download from https://www.puimula.org/htp/testing/voikko-snapshot-v5/ ")
    if sys.platform == "win32":
        _safe_print("      Extract to U:\\project\\voikko\\5\\ or C:\\voikko\\5\\")
    else:
        _safe_print("      Or: apt-get install voikko-fi")
    _safe_print("      Finnish lemmatization will use fallback.")


def _check_vcruntime() -> None:
    """On Windows, warn if Visual C++ runtime is likely missing."""
    if sys.platform != "win32":
        return
    # vcruntime140.dll ships with Python on Windows; only warn if truly absent
    try:
        import ctypes
        ctypes.cdll.LoadLibrary("vcruntime140.dll")
    except OSError:
        _safe_print("  ⚠️  Visual C++ runtime (vcruntime140.dll) not found.")
        _safe_print("      Some packages may fail. Install from:")
        _safe_print("      https://aka.ms/vs/17/release/vc_redist.x64.exe")


def _suggest_apt(pip_name: str) -> None:
    """On Linux, suggest apt packages when pip install fails."""
    if sys.platform == "win32":
        return
    apt_map = {
        "libvoikko": "libvoikko-dev python3-libvoikko voikko-fi",
        "psutil": "python3-psutil",
    }
    if pip_name in apt_map:
        _safe_print(f"      Try: sudo apt-get install {apt_map[pip_name]}")


def ensure_dependencies() -> None:
    """Check and auto-install missing packages. Call before any project imports."""
    missing_req = []
    installed_req = []
    optional_warnings = []

    # --- Required packages ---
    for import_name, pip_name in REQUIRED:
        if _can_import(import_name):
            continue
        # Auto-install
        if _pip_install(pip_name):
            installed_req.append(pip_name)
        else:
            missing_req.append(pip_name)
            if sys.platform != "win32":
                _suggest_apt(pip_name)

    # --- Optional packages ---
    for import_name, pip_name, warn_msg in OPTIONAL:
        if _can_import(import_name):
            continue
        if _pip_install(pip_name):
            installed_req.append(pip_name)
        else:
            if warn_msg:
                optional_warnings.append((pip_name, warn_msg))
            _suggest_apt(pip_name)

    # --- Windows VC++ check ---
    _check_vcruntime()

    # --- Voikko dictionary check ---
    _check_voikko_dict()

    # --- Summary ---
    if installed_req:
        _safe_print(f"  📦 Auto-installed: {', '.join(installed_req)}")

    if missing_req:
        _safe_print(f"  ❌ MISSING required packages (install manually): {', '.join(missing_req)}")
        sys.exit(1)

    if optional_warnings:
        parts = [f"{name} ({msg})" for name, msg in optional_warnings]
        _safe_print(f"  ⚠️  Optional: {'; '.join(parts)}")
    elif not installed_req:
        _safe_print("  ✅ All dependencies OK")
    else:
        _safe_print("  ✅ All dependencies OK (after auto-install)")

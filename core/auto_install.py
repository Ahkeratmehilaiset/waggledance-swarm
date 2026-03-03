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
    """If libvoikko importable, check that dict folder exists with real data."""
    if not _can_import("libvoikko"):
        return

    project_voikko = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "voikko")

    # Search paths mirroring core/normalizer.py _init_voikko()
    search = [
        os.environ.get("VOIKKO_DICTIONARY_PATH"),
        project_voikko,
    ]
    if sys.platform == "win32":
        search += [r"U:\project\voikko", r"C:\voikko"]
    else:
        search += ["/usr/lib/voikko", "/usr/share/voikko"]

    for p in search:
        if not p or not os.path.isdir(p):
            continue
        mor_dir = os.path.join(p, "5", "mor-standard")
        if os.path.isdir(mor_dir):
            vfst_files = [f for f in os.listdir(mor_dir) if f.endswith(".vfst")]
            if vfst_files:
                return  # dict found with real data

    # Dictionary incomplete or missing — try auto-download
    _safe_print("  Voikko dictionary not found or incomplete.")
    _safe_print("      Attempting auto-download...")
    try:
        import urllib.request
        import zipfile
        import tempfile
        url = "https://www.puimula.org/htp/testing/voikko-snapshot-v5/dict.zip"
        os.makedirs(project_voikko, exist_ok=True)
        tmp_zip = os.path.join(tempfile.gettempdir(), "voikko_dict.zip")
        urllib.request.urlretrieve(url, tmp_zip)
        with zipfile.ZipFile(tmp_zip, "r") as zf:
            zf.extractall(project_voikko)
        os.unlink(tmp_zip)
        _safe_print("      Voikko dictionary downloaded and installed.")
    except Exception as e:
        _safe_print(f"      Auto-download failed: {e}")
        _safe_print("      Download manually from: "
                     "https://www.puimula.org/htp/testing/voikko-snapshot-v5/")
        if sys.platform == "win32":
            _safe_print(f"      Extract to: {project_voikko}")
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

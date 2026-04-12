"""Launch dedicated gauntlet server on port 8002 with ephemeral API key.

The key is written to %TEMP%\\waggle_gauntlet_8002.key so the test
harness can read it.  Never printed to stdout/stderr.
"""
import os, sys, re, secrets, subprocess, tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

# Load non-secret vars from .env
env = os.environ.copy()
env_file = os.path.join(ROOT, ".env")
if os.path.isfile(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if m:
                k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
                if k != "WAGGLE_API_KEY" and v:
                    env[k] = v

# Ephemeral API key
KEY_FILE = os.path.join(tempfile.gettempdir(), "waggle_gauntlet_8002.key")
key = ""
if os.path.isfile(KEY_FILE):
    with open(KEY_FILE, "r") as f:
        key = f.read().strip()
if not key:
    key = "gnt_" + secrets.token_hex(16)
    with open(KEY_FILE, "w", encoding="utf-8") as f:
        f.write(key)
env["WAGGLE_API_KEY"] = key

print(f"gauntlet-server: key len={len(key)} source={'reused' if os.path.isfile(KEY_FILE) else 'new'}", flush=True)
print(f"gauntlet-server: starting on port 8002 from {ROOT}", flush=True)

env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"
env["OLLAMA_KEEP_ALIVE"] = "24h"
env["OLLAMA_MAX_LOADED_MODELS"] = "4"

subprocess.run(
    [sys.executable, os.path.join(ROOT, "start_waggledance.py"), "--port", "8002"],
    cwd=ROOT,
    env=env,
)

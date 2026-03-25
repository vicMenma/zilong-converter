# @title ⚡ Zilong Converter Bot — Colab Launcher
# @markdown ## Credentials
# @markdown
# @markdown **Recommended:** Add secrets via the 🔑 icon in the left panel:
# @markdown - `API_ID`, `API_HASH`, `BOT_TOKEN`

API_ID    = 0   # @param {type:"integer"}
API_HASH  = ""  # @param {type:"string"}
BOT_TOKEN = ""  # @param {type:"string"}

# Optional: channel ID for storing large files (BIN_CHANNEL pattern)
BIN_CHANNEL = 0  # @param {type:"integer"}

# Optional: yt-dlp cookies.txt path (for age-restricted content)
COOKIES_FILE = ""  # @param {type:"string"}

# GitHub personal access token — required if repo is private
GITHUB_TOKEN = ""  # @param {type:"string"}

import os, sys, subprocess, shutil, time, glob
from datetime import datetime

REPO_NAME = "zilong-converter"
BASE_DIR  = "/content/zilong-converter"


def _log(level: str, msg: str):
    icons = {"INFO": "ℹ️", "OK": "✅", "WARN": "⚠️", "ERR": "❌", "STEP": "🔧"}
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {icons.get(level, '')} {msg}", flush=True)


def _secret(name: str) -> str:
    try:
        from google.colab import userdata
        val = userdata.get(name)
        if val:
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get(name, "").strip()


print("⚡ Zilong Converter Bot — Colab Launcher")
print("─" * 50)
_log("STEP", "Resolving credentials…")

if not API_ID:
    try: API_ID = int(_secret("API_ID"))
    except: API_ID = 0
if not API_HASH:  API_HASH  = _secret("API_HASH")
if not BOT_TOKEN: BOT_TOKEN = _secret("BOT_TOKEN")
if not BIN_CHANNEL:
    try: BIN_CHANNEL = int(_secret("BIN_CHANNEL") or 0)
    except: BIN_CHANNEL = 0
if not COOKIES_FILE: COOKIES_FILE = _secret("COOKIES_FILE")
if not GITHUB_TOKEN: GITHUB_TOKEN = _secret("GITHUB_TOKEN")

errors = []
if not API_ID:    errors.append("API_ID is required")
if not API_HASH:  errors.append("API_HASH is required")
if not BOT_TOKEN: errors.append("BOT_TOKEN is required")
if errors:
    print()
    for e in errors: print(f"  ❌ {e}")
    print()
    raise SystemExit("Fill in credentials and run again.")

_log("OK", f"Credentials loaded  (API_ID={API_ID})")
if BIN_CHANNEL:  _log("OK", f"BIN_CHANNEL set ({BIN_CHANNEL})")
if COOKIES_FILE: _log("OK", f"Cookies file: {COOKIES_FILE}")
if GITHUB_TOKEN: _log("OK", "GitHub token set — will clone private repo")

# ── System packages ───────────────────────────────────────────────
_log("STEP", "Installing system packages…")
subprocess.run(
    "apt-get update -qq && "
    "apt-get install -y -qq ffmpeg 2>/dev/null",
    shell=True, capture_output=True,
)
_log("OK", "System packages ready (ffmpeg)")

# ── Clone repo ────────────────────────────────────────────────────
_log("STEP", "Cloning repository…")
if os.path.exists(BASE_DIR):
    shutil.rmtree(BASE_DIR)

if GITHUB_TOKEN:
    REPO_URL = f"https://{GITHUB_TOKEN}@github.com/vicMenma/{REPO_NAME}.git"
else:
    REPO_URL = f"https://github.com/vicMenma/{REPO_NAME}.git"

r = subprocess.run(
    ["git", "clone", "--depth=1", REPO_URL, BASE_DIR],
    capture_output=True, text=True
)
if r.returncode != 0:
    err_clean = r.stderr.replace(GITHUB_TOKEN, "***") if GITHUB_TOKEN else r.stderr
    raise SystemExit(f"❌ Clone failed:\n{err_clean[:300]}")
_log("OK", f"Cloned {REPO_NAME} → {BASE_DIR}")

# ── Python packages ───────────────────────────────────────────────
_log("STEP", "Installing Python packages…")
subprocess.run(
    [sys.executable, "-m", "pip", "uninstall", "-q", "-y", "pyrogram"],
    capture_output=True,
)
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q",
     "-r", f"{BASE_DIR}/requirements.txt"],
    check=True,
)
_log("OK", "Python packages installed")

# ── Write .env ────────────────────────────────────────────────────
env_lines = [
    f"API_ID={API_ID}",
    f"API_HASH={API_HASH}",
    f"BOT_TOKEN={BOT_TOKEN}",
    f"BIN_CHANNEL={BIN_CHANNEL}",
    f"COOKIES_FILE={COOKIES_FILE}",
    "WORK_DIR=/tmp/zilong_work",
]

with open(f"{BASE_DIR}/.env", "w") as f:
    f.write("\n".join(env_lines))

# Clean stale sessions
for sf in glob.glob(os.path.join(BASE_DIR, "*.session*")):
    try: os.remove(sf)
    except OSError: pass

os.makedirs("/tmp/zilong_work", exist_ok=True)
_log("OK", "Environment configured (.env written)")

os.chdir(BASE_DIR)

# ── Colab keep-alive ──────────────────────────────────────────────
_log("STEP", "Activating Colab keep-alive…")
try:
    from IPython.display import display, Javascript
    display(Javascript('''
    function ColabKeepAlive() {
        document.querySelector("#top-toolbar .colab-connect-button")?.click();
        document.querySelector("colab-connect-button")?.shadowRoot
            ?.querySelector("#connect")?.click();
        document.querySelector("#ok")?.click();
    }
    setInterval(ColabKeepAlive, 60000);
    console.log("Colab keep-alive: clicking connect every 60s");
    '''))
    _log("OK", "JS keep-alive injected (clicks connect every 60s)")
except Exception:
    _log("WARN", "Not in Colab notebook — JS keep-alive skipped")

import threading

def _heartbeat():
    while True:
        time.sleep(300)
        ts = datetime.now().strftime("%H:%M")
        print(f"[{ts}] 💓", end="", flush=True)

_hb = threading.Thread(target=_heartbeat, daemon=True)
_hb.start()
_log("OK", "Heartbeat thread started (every 5 min)")

# ── Bot runner with auto-restart ──────────────────────────────────
_log("OK", "Starting bot…\n" + "─" * 50)

MAX_RESTARTS = 50
restart_count = 0

_bot_env = {**os.environ, "PYTHONUNBUFFERED": "1"}
for line in env_lines:
    if "=" in line:
        k, _, v = line.partition("=")
        _bot_env[k] = v

while restart_count < MAX_RESTARTS:
    t_start = datetime.now()
    proc = subprocess.Popen(
        [sys.executable, "-u", "bot.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True, bufsize=1,
        env=_bot_env,
    )
    for line in proc.stdout:
        print(line, end="", flush=True)
    proc.wait()

    elapsed = (datetime.now() - t_start).seconds
    if proc.returncode == 0:
        _log("OK", "Bot stopped cleanly.")
        break

    if elapsed > 300:
        restart_count = 0

    restart_count += 1
    _log("WARN", f"Crashed (exit={proc.returncode}) after {elapsed}s  [{restart_count}/{MAX_RESTARTS}]")
    if restart_count >= MAX_RESTARTS:
        _log("ERR", "Too many restarts — stopping.")
        break
    wait = min(5 * restart_count, 30)
    _log("WARN", f"Restarting in {wait}s…")
    time.sleep(wait)

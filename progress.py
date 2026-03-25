"""
progress.py — Rich progress panel matching Zilong_multiusage style.

Exposes:
  status_bar(msg, label, pct, speed, eta, done, total, engine)
  upload_progress_cb(status_msg, label)   → Pyrogram progress callback
  download_progress_cb(status_msg, label) → same
  sysINFO()                               → CPU / RAM / Disk footer strip
"""

import os
import time
import psutil
from datetime import datetime


# ── Shared timing gate ────────────────────────────────────────────────────────
_last_edit: dict[int, float] = {}   # msg_id → last edit timestamp

def _time_over(msg_id: int, interval: float = 3.0) -> bool:
    now = time.time()
    if now - _last_edit.get(msg_id, 0) >= interval:
        _last_edit[msg_id] = now
        return True
    return False


# ── Visual helpers ────────────────────────────────────────────────────────────
BAR_LEN = 12

def _bar(pct: float) -> str:
    filled = int(min(pct, 100) / 100 * BAR_LEN)
    return "█" * filled + "░" * (BAR_LEN - filled)

def _speed_emoji(speed_str: str) -> str:
    if "GiB" in speed_str or "TiB" in speed_str:
        return "🚀"
    if "MiB" in speed_str:
        try:
            val = float(speed_str.split()[0])
            if val >= 50: return "⚡"
            if val >= 10: return "🔥"
        except Exception:
            pass
        return "🏃"
    return "🐢"

def _size(b: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TiB"

def _fmt_time(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s   = divmod(rem, 60)
    if h:  return f"{h}h {m}m {s}s"
    if m:  return f"{m}m {s}s"
    return f"{s}s"


# ── sysINFO footer ────────────────────────────────────────────────────────────
def sysINFO() -> str:
    cpu  = psutil.cpu_percent()
    ram  = psutil.Process(os.getpid()).memory_info().rss
    disk = psutil.disk_usage("/")
    return (
        "\n\n──────────────────\n"
        f"🖥  CPU   <code>[{_bar(cpu)}]</code> <b>{cpu:.0f}%</b>\n"
        f"💾  RAM   <code>{_size(ram)}</code>\n"
        f"💿  Free  <code>{_size(disk.free)}</code>"
    )


# ── Core status bar ────────────────────────────────────────────────────────────
async def status_bar(
    msg,            # Pyrogram Message to edit
    header: str,    # top section (file name, mode, etc.)
    pct: float,
    speed: str,
    eta: str,
    done: str,
    total: str,
    engine: str,
    elapsed: str,
    force: bool = False,
):
    if not force and not _time_over(msg.id):
        return
    s_ico = _speed_emoji(speed)
    text = (
        f"{header}"
        f"\n<code>[{_bar(pct)}]</code>  <b>{pct:.1f}%</b>\n"
        "──────────────────\n"
        f"{s_ico}  <b>Speed</b>     <code>{speed}</code>\n"
        f"⚙️  <b>Engine</b>    <code>{engine}</code>\n"
        f"⏳  <b>ETA</b>       <code>{eta}</code>\n"
        f"🕰  <b>Elapsed</b>   <code>{elapsed}</code>\n"
        f"✅  <b>Done</b>      <code>{done}</code>\n"
        f"📦  <b>Total</b>     <code>{total}</code>"
        f"{sysINFO()}"
    )
    try:
        await msg.edit_text(text)
    except Exception:
        pass


# ── Pyrogram upload/download progress callback ────────────────────────────────
def make_transfer_cb(status_msg, header: str, engine: str, total_bytes: int = 0):
    """
    Returns an async progress callback compatible with Pyrogram's
    client.download_media(..., progress=cb) and send_video(..., progress=cb).
    """
    start_time = [datetime.now()]
    last_bytes = [0]

    async def cb(current: int, total: int):
        if total == 0:
            return
        now     = datetime.now()
        elapsed = (now - start_time[0]).total_seconds()
        delta_b = current - last_bytes[0]
        last_bytes[0] = current

        pct     = current / total * 100
        speed_b = delta_b / max(elapsed, 0.1) if elapsed > 0 else 0
        # use running average speed for ETA
        avg_spd = current / max(elapsed, 1)
        eta_s   = (total - current) / max(avg_spd, 1)

        await status_bar(
            msg     = status_msg,
            header  = header,
            pct     = pct,
            speed   = f"{_size(speed_b)}/s",
            eta     = _fmt_time(eta_s),
            done    = _size(current),
            total   = _size(total),
            engine  = engine,
            elapsed = _fmt_time(elapsed),
        )

    return cb


# ── yt-dlp progress callback ──────────────────────────────────────────────────
def make_ytdlp_cb(status_msg, header: str):
    """
    Returns an async callable(pct, speed_str, eta_str) for downloader.py.
    """
    start_time = [datetime.now()]

    async def cb(pct: float, speed: str, eta: str):
        elapsed = (datetime.now() - start_time[0]).total_seconds()
        await status_bar(
            msg     = status_msg,
            header  = header,
            pct     = pct,
            speed   = speed,
            eta     = eta,
            done    = f"{pct:.1f}%",
            total   = "100%",
            engine  = "yt-dlp 🏮",
            elapsed = _fmt_time(elapsed),
        )

    return cb

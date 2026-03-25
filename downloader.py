"""
downloader.py — yt-dlp wrapper for downloading video from URL.
Supports any site yt-dlp supports (YouTube, Twitter, direct links, etc.)
"""

import asyncio
import os
import re
from config import Config

async def download_video(url: str, work_dir: str, progress_cb=None) -> str:
    """
    Download best quality video (max 1080p) to work_dir.
    Returns local file path.
    progress_cb(percent: int, speed: str, eta: str) — optional async callable.
    """
    out_template = os.path.join(work_dir, "input.%(ext)s")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
    ]

    if Config.COOKIES_FILE and os.path.exists(Config.COOKIES_FILE):
        cmd += ["--cookies", Config.COOKIES_FILE]

    if progress_cb:
        cmd += ["--newline"]   # one line per progress update

    cmd.append(url)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    async for raw_line in proc.stdout:
        line = raw_line.decode(errors="replace").strip()
        if progress_cb and "[download]" in line:
            # parse:  [download]  42.3% of 312.50MiB at 1.50MiB/s ETA 02:10
            m = re.search(
                r"(\d+\.?\d*)%.*?at\s+([\d.]+\w+/s).*?ETA\s+(\S+)", line
            )
            if m:
                await progress_cb(float(m.group(1)), m.group(2), m.group(3))

    await proc.wait()

    # Find the downloaded file
    for f in os.listdir(work_dir):
        if f.startswith("input.") and not f.endswith(".part"):
            return os.path.join(work_dir, f)

    raise RuntimeError("yt-dlp finished but no output file found.")

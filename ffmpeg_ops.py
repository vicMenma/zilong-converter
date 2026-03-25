"""
ffmpeg_ops.py — All FFmpeg wrappers with live progress callbacks.
"""

import asyncio
import os
import re
import shutil
import json
import time
from pathlib import Path


# ── FFmpeg progress line parser ────────────────────────────────────────────────
_FFMPEG_RE = re.compile(
    r"frame=\s*(\d+).*?fps=\s*([\d.]+).*?size=\s*([\d.]+\w+).*?"
    r"time=([\d:\.]+).*?bitrate=\s*([\S]+).*?speed=\s*([\S]+)"
)

def _parse_ffmpeg(line: str) -> dict | None:
    m = _FFMPEG_RE.search(line)
    if not m:
        return None
    return {
        "frame":   int(m.group(1)),
        "fps":     float(m.group(2) or 0),
        "size":    m.group(3),
        "time":    m.group(4),
        "bitrate": m.group(5),
        "speed":   m.group(6),
    }

def _ts_to_s(ts: str) -> float:
    try:
        p = ts.split(":")
        return int(p[0]) * 3600 + int(p[1]) * 60 + float(p[2])
    except Exception:
        return 0.0

def _fmt(secs: float) -> str:
    s = int(secs)
    h, r = divmod(s, 3600); m, s2 = divmod(r, 60)
    return (f"{h}h {m}m {s2}s" if h else f"{m}m {s2}s" if m else f"{s2}s")


# ── Silent runner (ffprobe, pass-1) ───────────────────────────────────────────
async def _run(cmd: list[str], cwd: str = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


# ── Live-progress runner ───────────────────────────────────────────────────────
async def _run_progress(
    cmd: list[str],
    duration: float,
    progress_cb=None,
    cwd: str = None,
) -> tuple[int, str]:
    """
    Streams FFmpeg stderr line by line.
    Calls progress_cb(pct, fps, speed, size, bitrate, eta, elapsed) every ~3s.
    Returns (returncode, full_stderr).
    """
    # inject -stats_period 2 for more frequent updates (FFmpeg >= 4.4)
    try:
        idx = cmd.index("ffmpeg") + 1
    except ValueError:
        idx = 1
    cmd = cmd[:idx] + ["-stats_period", "2"] + cmd[idx:]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,  # merge so we catch everything
        cwd=cwd,
    )

    buf       = []
    last_cb   = 0.0
    t_start   = time.time()

    async for raw in proc.stdout:
        line = raw.decode(errors="replace").strip()
        if line:
            buf.append(line)

        if progress_cb and duration > 0:
            parsed = _parse_ffmpeg(line)
            if parsed:
                now = time.time()
                if now - last_cb >= 3.0:
                    last_cb  = now
                    elapsed  = now - t_start
                    done_s   = _ts_to_s(parsed["time"])
                    pct      = min(done_s / duration * 100, 99.0)
                    try:
                        speed_x = float(parsed["speed"].rstrip("x") or 0)
                    except Exception:
                        speed_x = 0
                    remaining = (duration - done_s) / speed_x if speed_x > 0 else 0
                    try:
                        await progress_cb(
                            pct     = pct,
                            fps     = parsed["fps"],
                            speed   = parsed["speed"],
                            size    = parsed["size"],
                            bitrate = parsed["bitrate"],
                            eta     = _fmt(remaining),
                            elapsed = _fmt(elapsed),
                        )
                    except Exception:
                        pass

    await proc.wait()
    return proc.returncode, "\n".join(buf)


# ── Probe ──────────────────────────────────────────────────────────────────────
async def probe_video(path: str) -> dict:
    rc, out, _ = await _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path
    ])
    if rc != 0:
        raise RuntimeError("ffprobe failed — is the file a valid video?")
    data       = json.loads(out)
    duration   = float(data["format"].get("duration", 0))
    size_bytes = int(data["format"].get("size", 0))
    width = height = 0
    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and width == 0:
            width  = s.get("width", 0)
            height = s.get("height", 0)
        if s.get("codec_type") == "audio":
            has_audio = True
    return {"duration": duration, "width": width, "height": height,
            "has_audio": has_audio, "size_bytes": size_bytes}


# ── Subtitle normalisation ─────────────────────────────────────────────────────
def _vtt_to_srt(content: str) -> str:
    lines = content.splitlines()
    out, idx, i = [], 1, 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith(("WEBVTT", "NOTE", "STYLE")):
            i += 1; continue
        if "-->" in line:
            ts = re.sub(r"\s+(align|position|size|line|vertical):\S+", "", line.replace(".", ","))
            out += [str(idx), ts]; idx += 1; i += 1
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i]); i += 1
            out += block + [""]
        else:
            i += 1
    return "\n".join(out)

def _txt_to_srt(content: str) -> str:
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    out = []
    for idx, line in enumerate(lines, 1):
        s, e = (idx-1)*3, idx*3
        def ts(x): h,r=divmod(x,3600); m,s2=divmod(r,60); return f"{h:02}:{m:02}:{s2:02},000"
        out += [str(idx), f"{ts(s)} --> {ts(e)}", line, ""]
    return "\n".join(out)

def normalise_subtitle(src_path: str, work_dir: str) -> str:
    ext     = Path(src_path).suffix.lower()
    base    = Path(src_path).stem
    content = Path(src_path).read_text(encoding="utf-8", errors="replace")
    if ext in (".srt", ".ass", ".ssa"):
        dst = os.path.join(work_dir, Path(src_path).name)
        shutil.copy2(src_path, dst); return dst
    if ext == ".vtt":
        dst = os.path.join(work_dir, base + ".srt")
        Path(dst).write_text(_vtt_to_srt(content), encoding="utf-8"); return dst
    if ext == ".txt":
        dst = os.path.join(work_dir, base + ".srt")
        Path(dst).write_text(_txt_to_srt(content), encoding="utf-8"); return dst
    raise ValueError(f"Unsupported subtitle format: {ext}")


# ── Resolution map ─────────────────────────────────────────────────────────────
RESOLUTION_MAP = {
    "4K":    2160,
    "1080p": 1080,
    "720p":  720,
    "480p":  480,
    "360p":  360,
    "240p":  240,
}


# ── Burn-in subtitles (HARD) ───────────────────────────────────────────────────
async def burn_subtitles(
    video_path:  str,
    sub_path:    str,
    output_path: str,
    progress_cb=None,
    extra_vf:    str = None,
    duration:    float = 0,
) -> str:
    ext      = Path(sub_path).suffix.lower()
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")
    sub_f    = f"ass={safe_sub}" if ext in (".ass", ".ssa") else f"subtitles={safe_sub}"
    vf       = sub_f if not extra_vf else f"{extra_vf},{sub_f}"
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "copy", output_path
    ]
    rc, err = await _run_progress(cmd, duration, progress_cb)
    if rc != 0:
        raise RuntimeError(f"Subtitle burn failed:\n{err[-800:]}")
    return output_path


# ── Soft-mux subtitles (embed track, no re-encode) ────────────────────────────
async def mux_subtitles(
    video_path:  str,
    sub_path:    str,
    output_path: str,
    progress_cb=None,
    duration:    float = 0,
) -> str:
    out       = output_path if output_path.endswith(".mkv") else output_path.replace(".mp4", ".mkv")
    ext       = Path(sub_path).suffix.lower()
    sub_codec = "ass" if ext in (".ass", ".ssa") else "srt"
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path, "-i", sub_path,
        "-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec,
        "-map", "0:v", "-map", "0:a?", "-map", "1:s",
        out
    ]
    rc, err = await _run_progress(cmd, duration, progress_cb)
    if rc != 0:
        raise RuntimeError(f"Subtitle mux failed:\n{err[-800:]}")
    return out


# ── 2-pass CBR: target file size ──────────────────────────────────────────────
async def compress_to_size(
    video_path:   str,
    target_mb:    float,
    output_path:  str,
    audio_kbps:   int = 128,
    scale_height: int = None,
    progress_cb=None,
    duration:     float = 0,
) -> str:
    if not duration:
        info = await probe_video(video_path)
        duration = info["duration"]
    if duration <= 0:
        raise ValueError("Could not read video duration.")

    target_bits = target_mb * 1024 * 1024 * 8
    audio_bits  = audio_kbps * 1000 * duration
    video_bits  = target_bits - audio_bits
    if video_bits <= 0:
        raise ValueError(f"Target size too small for {duration:.0f}s of audio alone.")
    video_kbps = int(video_bits / duration / 1000)

    vf_args  = ["-vf", f"scale=-2:{scale_height}"] if scale_height else []
    work_dir = os.path.dirname(output_path)

    # Pass 1 (silent — no useful progress output)
    pass1 = [
        "ffmpeg", "-y", "-i", video_path, *vf_args,
        "-c:v", "libx264", "-b:v", f"{video_kbps}k",
        "-pass", "1", "-an", "-f", "null", "/dev/null"
    ]
    rc, err = await _run_progress(pass1, duration, None, cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"2-pass (pass 1) failed:\n{err[-800:]}")

    # Pass 2 (with live progress)
    pass2 = [
        "ffmpeg", "-y", "-i", video_path, *vf_args,
        "-c:v", "libx264", "-b:v", f"{video_kbps}k",
        "-pass", "2",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        output_path
    ]
    rc, err = await _run_progress(pass2, duration, progress_cb, cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"2-pass (pass 2) failed:\n{err[-800:]}")

    for f in ["ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"]:
        try: os.remove(os.path.join(work_dir, f))
        except: pass

    return output_path


# ── CRF + scale: target resolution ────────────────────────────────────────────
async def compress_to_res(
    video_path:  str,
    res_label:   str,
    output_path: str,
    crf:         int = 23,
    preset:      str = "medium",
    progress_cb=None,
    duration:    float = 0,
) -> str:
    height = RESOLUTION_MAP.get(res_label)
    if not height:
        raise ValueError(f"Unknown resolution: {res_label}. Use one of {list(RESOLUTION_MAP)}")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "copy", output_path
    ]
    rc, err = await _run_progress(cmd, duration, progress_cb)
    if rc != 0:
        raise RuntimeError(f"Resolution compress failed:\n{err[-800:]}")
    return output_path


# ── Combined: burn subs + compress ────────────────────────────────────────────
async def burn_sub_and_compress(
    video_path:   str,
    sub_path:     str,
    output_path:  str,
    res_label:    str = None,
    target_mb:    float = None,
    crf:          int = 23,
    progress_cb=None,
    duration:     float = 0,
) -> str:
    ext      = Path(sub_path).suffix.lower()
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")
    sub_f    = f"ass={safe_sub}" if ext in (".ass", ".ssa") else f"subtitles={safe_sub}"
    scale_h  = RESOLUTION_MAP.get(res_label) if res_label else None

    if target_mb:
        tmp = output_path + ".burned.mp4"
        vf  = f"scale=-2:{scale_h},{sub_f}" if scale_h else sub_f

        async def cb_burn(**kw):
            if progress_cb:
                await progress_cb(pct=kw["pct"] / 2,
                                  **{k: v for k, v in kw.items() if k != "pct"})

        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "copy", tmp
        ]
        rc, err = await _run_progress(cmd, duration, cb_burn)
        if rc != 0:
            raise RuntimeError(f"Sub burn stage failed:\n{err[-800:]}")

        async def cb_compress(**kw):
            if progress_cb:
                await progress_cb(pct=50 + kw["pct"] / 2,
                                  **{k: v for k, v in kw.items() if k != "pct"})

        result = await compress_to_size(tmp, target_mb, output_path,
                                        progress_cb=cb_compress, duration=duration)
        os.remove(tmp)
        return result
    else:
        vf = f"scale=-2:{scale_h},{sub_f}" if scale_h else sub_f
        cmd = [
            "ffmpeg", "-y", "-i", video_path, "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "copy", output_path
        ]
        rc, err = await _run_progress(cmd, duration, progress_cb)
        if rc != 0:
            raise RuntimeError(f"Combined encode failed:\n{err[-800:]}")
        return output_path

"""
ffmpeg_ops.py — All FFmpeg wrappers for the converter bot.

Operations:
  probe_video()       → duration, resolution, has_audio
  convert_subtitle()  → .vtt / .txt → .srt (normalise to SRT before burn)
  burn_subtitles()    → hard-code subs into video stream
  mux_subtitles()     → embed as soft subtitle track (mkv/mp4)
  compress_to_size()  → 2-pass encode targeting a file size (MB)
  compress_to_res()   → scale + CRF encode to target resolution
  compress_to_res_and_size() → scale first, then 2-pass to hit MB target
"""

import asyncio
import os
import re
import shutil
import json
from pathlib import Path

# ── Helper: run subprocess ─────────────────────────────────────────────────────
async def _run(cmd: list[str], cwd: str = None) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout.decode(), stderr.decode()


# ── Probe ──────────────────────────────────────────────────────────────────────
async def probe_video(path: str) -> dict:
    """Returns dict: duration(s), width, height, has_audio, size_bytes"""
    rc, out, _ = await _run([
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams", "-show_format",
        path
    ])
    if rc != 0:
        raise RuntimeError("ffprobe failed — is the file a valid video?")
    data = json.loads(out)
    duration = float(data["format"].get("duration", 0))
    size_bytes = int(data["format"].get("size", 0))
    width = height = 0
    has_audio = False
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and width == 0:
            width  = s.get("width", 0)
            height = s.get("height", 0)
        if s.get("codec_type") == "audio":
            has_audio = True
    return {
        "duration":   duration,
        "width":      width,
        "height":     height,
        "has_audio":  has_audio,
        "size_bytes": size_bytes,
    }


# ── Subtitle normalisation ─────────────────────────────────────────────────────
def _vtt_to_srt(content: str) -> str:
    """Basic WebVTT → SRT conversion."""
    lines = content.splitlines()
    out, idx = [], 1
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # skip WEBVTT header / NOTE / STYLE blocks
        if line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
            i += 1
            continue
        # timestamp line
        if "-->" in line:
            ts = line.replace(".", ",")   # VTT uses dots, SRT uses commas
            # remove cue settings after timestamp
            ts = re.sub(r"\s+(align|position|size|line|vertical):\S+", "", ts)
            out.append(str(idx))
            out.append(ts)
            idx += 1
            i += 1
            block = []
            while i < len(lines) and lines[i].strip():
                block.append(lines[i])
                i += 1
            out.extend(block)
            out.append("")
        else:
            i += 1
    return "\n".join(out)


def _txt_to_srt(content: str) -> str:
    """
    Plain TXT: assume each non-empty line is dialogue.
    Creates 3-second subtitles one after another.
    """
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    out = []
    for idx, line in enumerate(lines, 1):
        start_s = (idx - 1) * 3
        end_s   = idx * 3
        def ts(s):
            h, rem = divmod(s, 3600)
            m, s2  = divmod(rem, 60)
            return f"{h:02}:{m:02}:{s2:02},000"
        out.append(str(idx))
        out.append(f"{ts(start_s)} --> {ts(end_s)}")
        out.append(line)
        out.append("")
    return "\n".join(out)


def normalise_subtitle(src_path: str, work_dir: str) -> str:
    """
    Convert .vtt / .txt → .srt in work_dir.
    .srt and .ass pass through unchanged (copy to work_dir).
    Returns path to the normalised file.
    """
    ext = Path(src_path).suffix.lower()
    base = Path(src_path).stem
    content = Path(src_path).read_text(encoding="utf-8", errors="replace")

    if ext in (".srt", ".ass", ".ssa"):
        dst = os.path.join(work_dir, Path(src_path).name)
        shutil.copy2(src_path, dst)
        return dst

    if ext == ".vtt":
        srt_content = _vtt_to_srt(content)
        dst = os.path.join(work_dir, base + ".srt")
        Path(dst).write_text(srt_content, encoding="utf-8")
        return dst

    if ext == ".txt":
        srt_content = _txt_to_srt(content)
        dst = os.path.join(work_dir, base + ".srt")
        Path(dst).write_text(srt_content, encoding="utf-8")
        return dst

    raise ValueError(f"Unsupported subtitle format: {ext}")


# ── Subtitle burn-in (HARD) ────────────────────────────────────────────────────
async def burn_subtitles(
    video_path: str,
    sub_path:   str,
    output_path: str,
    progress_cb=None,
    extra_vf:   str = None,   # e.g. "scale=-2:720" to combine with sub burn
) -> str:
    """
    Hard-code subtitles into the video using the subtitles/ass filter.
    ASS/SSA → uses `ass=` filter (preserves all styling).
    SRT/other → uses `subtitles=` filter (basic style).
    """
    ext = Path(sub_path).suffix.lower()
    # FFmpeg subtitle filter paths must have colons escaped on Windows; fine on Linux
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")

    if ext in (".ass", ".ssa"):
        sub_filter = f"ass={safe_sub}"
    else:
        sub_filter = f"subtitles={safe_sub}"

    vf = sub_filter if not extra_vf else f"{extra_vf},{sub_filter}"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "18", "-preset", "medium",
        "-c:a", "copy",
        output_path
    ]
    rc, _, err = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"Subtitle burn failed:\n{err[-800:]}")
    return output_path


# ── Subtitle soft-mux (SOFT) ───────────────────────────────────────────────────
async def mux_subtitles(
    video_path:  str,
    sub_path:    str,
    output_path: str,
) -> str:
    """
    Embed subtitle as a selectable stream (no re-encode of video/audio).
    Output should be .mkv for universal sub codec support.
    """
    out = output_path if output_path.endswith(".mkv") else output_path.replace(".mp4", ".mkv")
    ext = Path(sub_path).suffix.lower()
    sub_codec = "ass" if ext in (".ass", ".ssa") else "srt"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", sub_path,
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", sub_codec,
        "-map", "0:v",
        "-map", "0:a?",
        "-map", "1:s",
        out
    ]
    rc, _, err = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"Subtitle mux failed:\n{err[-800:]}")
    return out


# ── Compress: target file size (2-pass CBR) ────────────────────────────────────
async def compress_to_size(
    video_path:   str,
    target_mb:    float,
    output_path:  str,
    audio_kbps:   int = 128,
    scale_height: int = None,   # optional: also scale resolution
) -> str:
    """
    2-pass H.264 encode to hit ~target_mb file size.
    """
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

    vf_args = []
    if scale_height:
        vf_args = ["-vf", f"scale=-2:{scale_height}"]

    work_dir = os.path.dirname(output_path)

    # Pass 1
    pass1 = [
        "ffmpeg", "-y", "-i", video_path,
        *vf_args,
        "-c:v", "libx264", "-b:v", f"{video_kbps}k",
        "-pass", "1", "-an", "-f", "null", "/dev/null"
    ]
    rc, _, err = await _run(pass1, cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"2-pass (pass 1) failed:\n{err[-800:]}")

    # Pass 2
    pass2 = [
        "ffmpeg", "-y", "-i", video_path,
        *vf_args,
        "-c:v", "libx264", "-b:v", f"{video_kbps}k",
        "-pass", "2",
        "-c:a", "aac", "-b:a", f"{audio_kbps}k",
        output_path
    ]
    rc, _, err = await _run(pass2, cwd=work_dir)
    if rc != 0:
        raise RuntimeError(f"2-pass (pass 2) failed:\n{err[-800:]}")

    # Clean up 2-pass log files
    for f in ["ffmpeg2pass-0.log", "ffmpeg2pass-0.log.mbtree"]:
        try: os.remove(os.path.join(work_dir, f))
        except: pass

    return output_path


# ── Compress: target resolution (CRF) ─────────────────────────────────────────
RESOLUTION_MAP = {
    "4K":   2160,
    "1080p": 1080,
    "720p":  720,
    "480p":  480,
    "360p":  360,
    "240p":  240,
}

async def compress_to_res(
    video_path:  str,
    res_label:   str,   # "1080p", "720p", etc.
    output_path: str,
    crf:         int = 23,
    preset:      str = "medium",
) -> str:
    """
    Scale to target resolution + CRF encode. Fast single-pass.
    """
    height = RESOLUTION_MAP.get(res_label)
    if not height:
        raise ValueError(f"Unknown resolution: {res_label}. Use one of {list(RESOLUTION_MAP)}")

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", f"scale=-2:{height}",
        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
        "-c:a", "copy",
        output_path
    ]
    rc, _, err = await _run(cmd)
    if rc != 0:
        raise RuntimeError(f"Resolution compress failed:\n{err[-800:]}")
    return output_path


# ── Combined: subtitle burn + compress ────────────────────────────────────────
async def burn_sub_and_compress(
    video_path:   str,
    sub_path:     str,
    output_path:  str,
    res_label:    str = None,   # optional resolution target
    target_mb:    float = None, # optional size target (2-pass after burn)
    crf:          int = 23,
) -> str:
    """
    If only resolution or CRF: single-pass with combined vf filter.
    If target_mb: two-stage (burn first → then 2-pass compress).
    """
    ext = Path(sub_path).suffix.lower()
    safe_sub = sub_path.replace("\\", "/").replace(":", "\\:")
    sub_filter = f"ass={safe_sub}" if ext in (".ass", ".ssa") else f"subtitles={safe_sub}"

    scale_height = RESOLUTION_MAP.get(res_label) if res_label else None

    if target_mb:
        # Stage 1: burn subs (with optional scale) → temp file
        tmp = output_path + ".burned.mp4"
        vf = f"scale=-2:{scale_height},{sub_filter}" if scale_height else sub_filter
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "copy",
            tmp
        ]
        rc, _, err = await _run(cmd)
        if rc != 0:
            raise RuntimeError(f"Sub burn stage failed:\n{err[-800:]}")
        # Stage 2: 2-pass to hit size
        result = await compress_to_size(tmp, target_mb, output_path)
        os.remove(tmp)
        return result
    else:
        # Single pass
        vf = f"scale=-2:{scale_height},{sub_filter}" if scale_height else sub_filter
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-c:a", "copy",
            output_path
        ]
        rc, _, err = await _run(cmd)
        if rc != 0:
            raise RuntimeError(f"Combined encode failed:\n{err[-800:]}")
        return output_path

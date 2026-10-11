"""Video compositing module using yt-dlp partial downloads and ffmpeg compositing."""

from __future__ import annotations

import logging
import math
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from src.config import config

logger = logging.getLogger(__name__)


def escape_ffmpeg_filter_path(path: Path) -> str:
    """Escapes file paths for ffmpeg filter arguments (subtitles and fontsdir).

    Handles both POSIX and Windows path delimiters and colons.
    """
    posix_path = str(path.resolve()).replace("\\", "/")
    # Escape colons (e.g., C:/ -> C\:/)
    escaped = posix_path.replace(":", "\\:").replace("'", "\\'")
    return escaped


def has_audio_stream(video_path: Path) -> bool:
    """Checks whether the media file contains at least one audio stream."""
    if not shutil.which("ffprobe"):
        return True

    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a",
            "-show_entries", "stream=index",
            "-of", "csv=p=0",
            str(video_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return bool(result.stdout.strip())
    except Exception:
        return True


def download_background_clip(
    target_duration: float,
    output_path: Path,
    max_retries: int = 3,
) -> Path:
    """Downloads a random segment from the 1-hour satisfying restocking YouTube video.

    Uses yt-dlp with `--download-sections "*START-END"` to download only the needed
    slice (e.g. 180-195s) rather than the entire 1-hour file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    total_seconds = config.background_video_total_seconds
    # Safety margin so we don't exceed the 1-hour mark
    max_start = max(10, int(total_seconds - target_duration - 30))

    for attempt in range(1, max_retries + 1):
        # Pick random start offset
        start_sec = random.randint(15, max_start)
        end_sec = int(start_sec + math.ceil(target_duration) + 3)  # 3s buffer

        logger.info(
            "Downloading YouTube section %d-%ds (attempt %d/%d)...",
            start_sec,
            end_sec,
            attempt,
            max_retries,
        )

        # Temporary file path for this attempt
        temp_download = output_path.parent / f"raw_bg_{attempt}.mp4"
        if temp_download.exists():
            temp_download.unlink()

        # Prepare cookies if provided
        cookies_arg = []
        if config.youtube_cookies:
            cookies_file = output_path.parent / "yt_cookies.txt"
            cookies_file.write_text(config.youtube_cookies, encoding="utf-8")
            cookies_arg = ["--cookies", str(cookies_file)]

        # Cycle through different client extractors on each retry
        client_extractors = [
            "youtube:player_client=ios",
            "youtube:player_client=android",
            "youtube:player_client=mweb",
        ]
        extractor_choice = client_extractors[(attempt - 1) % len(client_extractors)]

        cmd = [
            "yt-dlp",
            "--download-sections", f"*{start_sec}-{end_sec}",
            "--force-keyframes-at-cuts",
            "--extractor-args", extractor_choice,
            "-f", "bv*[height<=1080]+ba/b[height<=1080]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-check-certificates",
            "--geo-bypass",
            "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "--retries", "3",
            "--socket-timeout", "20",
        ] + cookies_arg + [
            "-o", str(temp_download),
            config.background_video_url,
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            if temp_download.exists() and temp_download.stat().st_size > 10240:
                if output_path.exists():
                    output_path.unlink()
                temp_download.rename(output_path)
                logger.info("Successfully downloaded background video segment: %s", output_path)
                return output_path
        except subprocess.CalledProcessError as e:
            logger.warning(
                "yt-dlp download failed on attempt %d: %s | stderr: %s",
                attempt,
                e,
                e.stderr[-300:] if e.stderr else "N/A",
            )
            if temp_download.exists():
                temp_download.unlink()
            time.sleep(2 * attempt)
        except Exception as e:
            logger.warning("yt-dlp unexpected error on attempt %d: %s", attempt, e)
            if temp_download.exists():
                temp_download.unlink()
            time.sleep(2 * attempt)

    # If YouTube was blocked (e.g. datacenter IP challenge), try fallback direct video if configured
    if config.background_video_fallback_url:
        logger.info("Attempting download from background video fallback URL: %s", config.background_video_fallback_url)
        try:
            cmd = [
                "yt-dlp",
                "-f", "best[ext=mp4]/best",
                "-o", str(output_path),
                config.background_video_fallback_url,
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            if output_path.exists() and output_path.stat().st_size > 10240:
                return output_path
        except Exception as e:
            logger.warning("Fallback video download failed: %s", e)

    # If all downloads failed, generate an animated satisfying dynamic gradient video
    logger.warning("YouTube download was blocked by anti-bot IP challenge. Generating dynamic animated satisfying background.")
    return generate_procedural_background(target_duration, output_path)


def generate_procedural_background(duration: float, output_path: Path) -> Path:
    """Generates an animated, satisfying 1080x1920 dynamic spiral visual background using ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required but not installed.")

    logger.info("Creating animated satisfying spiral background with duration %.1fs...", duration)
    # Use ffmpeg's animated gradient filter with soothing ASMR ambient hum
    filter_graph = (
        f"gradients=s={config.video_width}x{config.video_height}:"
        f"type=spiral:speed=0.025:nb_colors=4:"
        f"c0=0x111e38:c1=0x1e3c72:c2=0x2a5298:c3=0x0f2027:r={config.video_fps}"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", filter_graph,
        "-f", "lavfi",
        "-i", f"anoisesrc=d={duration + 2}:c=pink:r=44100:a=0.01",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-t", str(duration + 1),
        str(output_path),
    ]

    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        # Fallback to standard color source if gradients filter not supported in this ffmpeg build
        logger.info("Gradients filter fallback to color source...")
        cmd_fallback = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", f"color=c=0x162032:s={config.video_width}x{config.video_height}:d={duration + 2}:r={config.video_fps}",
            "-f", "lavfi",
            "-i", f"anullsrc=r=44100:cl=stereo:d={duration + 2}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-t", str(duration + 1),
            str(output_path),
        ]
        subprocess.run(cmd_fallback, check=True, capture_output=True)

    return output_path


def render_final_video(
    background_video: Path,
    voiceover_audio: Path,
    subtitles_ass: Path,
    output_video: Path,
    duration: float,
) -> Path:
    """Composites the final vertical TikTok video using ffmpeg.

    Applies:
    - 9:16 vertical crop & scale: 1080x1920, 30fps
    - Burn-in ASS dynamic subtitles using bundled fonts
    - Mixes TTS voiceover as primary audio + background ASMR at -22dB
    - Exports optimized fast-start H.264 MP4
    """
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg executable not found in system PATH.")

    output_video.parent.mkdir(parents=True, exist_ok=True)
    if output_video.exists():
        output_video.unlink()

    # Prepare escaped subtitle and font paths
    escaped_subtitles = escape_ffmpeg_filter_path(subtitles_ass)
    escaped_fontsdir = escape_ffmpeg_filter_path(config.fonts_dir)

    # Check whether background video has audio
    bg_has_audio = has_audio_stream(background_video)

    # Video filter chain: scale -> crop -> 30fps -> subtitles
    video_filter = (
        f"[0:v]scale={config.video_width}:{config.video_height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={config.video_width}:{config.video_height}:"
        f"(in_w-{config.video_width})/2:(in_h-{config.video_height})/2,"
        f"fps={config.video_fps},"
        f"subtitles='{escaped_subtitles}':fontsdir='{escaped_fontsdir}'[v]"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-i", str(background_video),
        "-i", str(voiceover_audio),
    ]

    if bg_has_audio:
        # Audio filter: duck background audio to -22dB, mix with full voiceover
        audio_filter = (
            f"[0:a]volume={config.bg_audio_volume_db}[bg_audio];"
            f"[1:a]volume=1.0[voice];"
            f"[voice][bg_audio]amix=inputs=2:duration=first:dropout_transition=2[a]"
        )
        filter_complex = f"{video_filter};{audio_filter}"
        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
        ])
    else:
        # Only use voiceover audio
        cmd.extend([
            "-filter_complex", video_filter,
            "-map", "[v]",
            "-map", "1:a",
        ])

    cmd.extend([
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{duration:.3f}",
        "-movflags", "+faststart",
        str(output_video),
    ])

    logger.info("Executing ffmpeg compositing command...")
    start_time = time.time()

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        logger.error("ffmpeg stdout:\n%s", proc.stdout)
        logger.error("ffmpeg stderr:\n%s", proc.stderr)
        raise RuntimeError(f"ffmpeg video rendering failed with code {proc.returncode}")

    render_duration = time.time() - start_time
    file_size_mb = output_video.stat().st_size / (1024 * 1024)
    logger.info(
        "Successfully rendered final video in %.1fs: %s (%.2f MB)",
        render_duration,
        output_video,
        file_size_mb,
    )
    return output_video

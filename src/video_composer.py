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


def normalize_video_url(url: str) -> str:
    """Normalizes video URLs (e.g., Hugging Face blob -> resolve for direct streaming)."""
    clean_url = url.strip()
    if "huggingface.co" in clean_url and "/blob/" in clean_url:
        clean_url = clean_url.replace("/blob/", "/resolve/")
    return clean_url


def probe_video_duration(url_or_path: str) -> Optional[float]:
    """Probes the total duration in seconds of a remote video URL or local file."""
    # 1. Try ffprobe (fastest on remote files via HTTP Range)
    if shutil.which("ffprobe"):
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                url_or_path,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout.strip():
                val = float(res.stdout.strip())
                if val > 1.0:
                    logger.info("ffprobe detected duration: %.2fs for %s", val, url_or_path[:80])
                    return val
        except Exception as e:
            logger.debug("ffprobe duration check failed: %s", e)

    # 2. Try yt-dlp metadata
    if shutil.which("yt-dlp"):
        try:
            cmd = ["yt-dlp", "--print", "duration", "--no-warnings", "--no-playlist", url_or_path]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            if res.returncode == 0 and res.stdout.strip():
                val = float(res.stdout.strip().split("\n")[0])
                if val > 1.0:
                    logger.info("yt-dlp detected duration: %.2fs for %s", val, url_or_path[:80])
                    return val
        except Exception as e:
            logger.debug("yt-dlp duration check failed: %s", e)

    return None


def extract_random_clip_from_url(
    url: str,
    target_duration: float,
    output_path: Path,
) -> bool:
    """Extracts a random section matching target_duration from a long video URL (e.g. Hugging Face).

    Uses fast remote HTTP range seeking with ffmpeg, falling back to yt-dlp download sections.
    """
    stream_url = normalize_video_url(url)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Probe total duration or fallback to configured total seconds
    probed_duration = probe_video_duration(stream_url)
    total_seconds = probed_duration if (probed_duration and probed_duration > 30) else float(config.background_video_total_seconds)

    clip_duration = math.ceil(target_duration) + 3  # Add 3s safety buffer
    max_start = max(0, int(total_seconds - clip_duration - 15))
    start_sec = random.randint(10, max_start) if max_start > 15 else 0
    end_sec = int(start_sec + clip_duration)

    logger.info(
        "Extracting random section (%ds to %ds, duration %.1fs) from fallback URL: %s",
        start_sec,
        end_sec,
        target_duration,
        stream_url[:80],
    )

    temp_clip = output_path.parent / f"temp_remote_clip_{int(time.time())}_{random.randint(100, 999)}.mp4"
    if temp_clip.exists():
        temp_clip.unlink()

    # Strategy A: Fast remote HTTP Range seeking via ffmpeg
    if shutil.which("ffmpeg"):
        logger.info("Strategy A: Seeking remote stream with ffmpeg Range requests at offset %ds...", start_sec)
        cmd_ffmpeg = [
            "ffmpeg",
            "-y",
            "-ss", str(start_sec),
            "-i", stream_url,
            "-t", str(clip_duration),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            "-avoid_negative_ts", "make_zero",
            str(temp_clip),
        ]
        try:
            res = subprocess.run(cmd_ffmpeg, capture_output=True, text=True, timeout=90)
            if res.returncode == 0 and temp_clip.exists() and temp_clip.stat().st_size > 51200:
                if output_path.exists():
                    output_path.unlink()
                temp_clip.rename(output_path)
                logger.info("Successfully extracted random section using ffmpeg stream seeking: %s", output_path)
                return True
            logger.warning("ffmpeg stream seeking did not produce valid video: stderr: %s", res.stderr[-200:] if res.stderr else "N/A")
        except Exception as e:
            logger.warning("ffmpeg stream seeking failed: %s", e)
            if temp_clip.exists():
                temp_clip.unlink()

    # Strategy B: yt-dlp partial section download
    if shutil.which("yt-dlp"):
        logger.info("Strategy B: Downloading section %d-%ds using yt-dlp...", start_sec, end_sec)
        cmd_ytdlp = [
            "yt-dlp",
            "--download-sections", f"*{start_sec}-{end_sec}",
            "--force-keyframes-at-cuts",
            "-f", "best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--no-playlist",
            "--no-check-certificates",
            "--retries", "3",
            "--socket-timeout", "20",
            "-o", str(temp_clip),
            stream_url,
        ]
        try:
            res = subprocess.run(cmd_ytdlp, capture_output=True, text=True, timeout=120)
            if res.returncode == 0 and temp_clip.exists() and temp_clip.stat().st_size > 51200:
                if output_path.exists():
                    output_path.unlink()
                temp_clip.rename(output_path)
                logger.info("Successfully extracted random section using yt-dlp download-sections: %s", output_path)
                return True
            logger.warning("yt-dlp download-sections failed: stderr: %s", res.stderr[-200:] if res.stderr else "N/A")
        except Exception as e:
            logger.warning("yt-dlp download-sections failed: %s", e)
            if temp_clip.exists():
                temp_clip.unlink()

    # Strategy C: Download full video to local cache then slice locally
    cached_source = output_path.parent / "fallback_source_full.mp4"
    if not (cached_source.exists() and cached_source.stat().st_size > 1048576):
        logger.info("Strategy C: Downloading full fallback video for local slicing...")
        try:
            cmd_dl = [
                "yt-dlp",
                "-f", "best[ext=mp4]/best",
                "-o", str(cached_source),
                stream_url,
            ]
            subprocess.run(cmd_dl, check=True, capture_output=True, timeout=300)
        except Exception as e:
            logger.warning("Failed to download fallback full video: %s", e)

    if cached_source.exists() and cached_source.stat().st_size > 1048576 and shutil.which("ffmpeg"):
        logger.info("Slicing random section locally from cached fallback video...")
        local_duration = probe_video_duration(str(cached_source)) or total_seconds
        local_max_start = max(0, int(local_duration - clip_duration - 10))
        local_start = random.randint(10, local_max_start) if local_max_start > 15 else 0
        cmd_slice = [
            "ffmpeg",
            "-y",
            "-ss", str(local_start),
            "-i", str(cached_source),
            "-t", str(clip_duration),
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-c:a", "aac",
            str(temp_clip),
        ]
        try:
            subprocess.run(cmd_slice, check=True, capture_output=True)
            if temp_clip.exists() and temp_clip.stat().st_size > 51200:
                if output_path.exists():
                    output_path.unlink()
                temp_clip.rename(output_path)
                logger.info("Successfully sliced random section locally: %s", output_path)
                return True
        except Exception as e:
            logger.warning("Local slicing failed: %s", e)
            if temp_clip.exists():
                temp_clip.unlink()

    return False


def download_background_clip(
    target_duration: float,
    output_path: Path,
    bg_style: Optional[str] = None,
    max_retries: int = 3,
) -> Path:
    """Downloads a random segment matching target_duration from a gameplay/ASMR background video.

    Picks footage matching bg_style (minecraft, subway_surfers, gta, satisfying, beamng, or auto).
    Prioritizes BACKGROUND_VIDEO_FALLBACK_URL if provided, then queries YouTube preset pool,
    and falls back to dynamic procedural generation if all network sources are unavailable.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Check if user configured BACKGROUND_VIDEO_FALLBACK_URL (e.g. Hugging Face / direct stream)
    if config.background_video_fallback_url:
        logger.info(
            "BACKGROUND_VIDEO_FALLBACK_URL detected. Extracting random section matching %.1fs...",
            target_duration,
        )
        if extract_random_clip_from_url(config.background_video_fallback_url, target_duration, output_path):
            return output_path
        logger.warning("Fallback URL extraction failed. Trying YouTube extraction presets...")

    # 2. Select background visual style and candidate video URLs
    style_key = bg_style if (bg_style and bg_style != "auto") else config.bg_style
    if not style_key or style_key == "auto" or style_key not in config.background_video_presets:
        style_key = random.choice(list(config.background_video_presets.keys()))

    logger.info("Selected background footage style: '%s'", style_key)
    candidate_urls = list(config.background_video_presets.get(style_key, []))
    if config.background_video_url and config.background_video_url not in candidate_urls:
        candidate_urls.append(config.background_video_url)
    random.shuffle(candidate_urls)

    total_seconds = config.background_video_total_seconds
    max_start = max(10, int(total_seconds - target_duration - 30))

    # 3. Iterate through candidate URLs in the selected style
    for url_idx, video_url in enumerate(candidate_urls[:max_retries]):
        start_sec = random.randint(15, max_start)
        end_sec = int(start_sec + math.ceil(target_duration) + 3)

        logger.info(
            "Downloading '%s' section %d-%ds from %s (attempt %d/%d)...",
            style_key,
            start_sec,
            end_sec,
            video_url,
            url_idx + 1,
            max_retries,
        )

        temp_download = output_path.parent / f"raw_bg_{url_idx + 1}.mp4"
        if temp_download.exists():
            temp_download.unlink()

        cookies_arg = []
        if config.youtube_cookies:
            cookies_file = output_path.parent / "yt_cookies.txt"
            cookies_file.write_text(config.youtube_cookies, encoding="utf-8")
            cookies_arg = ["--cookies", str(cookies_file)]

        client_extractors = [
            "youtube:player_client=ios",
            "youtube:player_client=android",
            "youtube:player_client=mweb",
        ]
        extractor_choice = client_extractors[url_idx % len(client_extractors)]

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
            video_url,
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            if temp_download.exists() and temp_download.stat().st_size > 10240:
                if output_path.exists():
                    output_path.unlink()
                temp_download.rename(output_path)
                logger.info("Successfully downloaded YouTube background video segment: %s", output_path)
                return output_path
        except subprocess.CalledProcessError as e:
            logger.warning(
                "yt-dlp YouTube download failed for %s on attempt %d: %s | stderr: %s",
                video_url,
                url_idx + 1,
                e,
                e.stderr[-300:] if e.stderr else "N/A",
            )
            if temp_download.exists():
                temp_download.unlink()
            time.sleep(2)
        except Exception as e:
            logger.warning("yt-dlp YouTube unexpected error on attempt %d: %s", url_idx + 1, e)
            if temp_download.exists():
                temp_download.unlink()
            time.sleep(2)

    # 4. If YouTube was blocked and fallback URL wasn't tried yet:
    if config.background_video_fallback_url:
        logger.info("Retrying extraction from BACKGROUND_VIDEO_FALLBACK_URL...")
        if extract_random_clip_from_url(config.background_video_fallback_url, target_duration, output_path):
            return output_path

    # 5. If all downloads failed, generate an animated satisfying dynamic gradient video
    logger.warning("YouTube & fallback downloads blocked. Generating dynamic animated satisfying background.")
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


def download_background_music(
    music_url: Optional[str] = None,
    output_path: Optional[Path] = None,
    max_retries: int = 2,
) -> Path:
    """Provides quiet background music from bundled asset, custom web URL, or ambient generator.

    Ensures the pipeline always has a clean, quiet background music track.
    """
    if output_path is None:
        output_path = config.temp_dir / "background_music.mp3"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and output_path.stat().st_size > 10240:
        return output_path

    # Check if bundled music asset exists FIRST (100% reliable, zero network dependency)
    bundled_music = config.project_root / "assets" / "background_music.mp3"

    # If a custom non-default URL is passed, attempt downloading that custom track
    is_custom_url = (
        music_url
        and str(music_url).strip()
        and str(music_url).strip() != "https://www.tiktok.com/music/son-original-7306937893019274016"
        and not str(music_url).strip().startswith("https://www.tiktok.com/music/")
    )

    if is_custom_url and shutil.which("yt-dlp"):
        for attempt in range(1, max_retries + 1):
            logger.info("Downloading custom background music from %s (attempt %d/%d)...", music_url, attempt, max_retries)
            cmd = [
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                "--no-playlist",
                "--no-check-certificates",
                "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "--socket-timeout", "15",
                "-o", str(output_path),
                str(music_url),
            ]
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if res.returncode == 0 and output_path.exists() and output_path.stat().st_size > 10240:
                    logger.info("Successfully downloaded custom background music: %s", output_path)
                    return output_path
                logger.warning("yt-dlp music download failed: %s", res.stderr[-200:] if res.stderr else "N/A")
            except Exception as e:
                logger.warning("Error downloading custom background music: %s", e)

    # Use bundled authentic TikTok piano music asset
    if bundled_music.exists() and bundled_music.stat().st_size > 10240:
        shutil.copy2(bundled_music, output_path)
        logger.info("Using bundled authentic background music asset from %s (%d bytes)", bundled_music, bundled_music.stat().st_size)
        return output_path

    # If download fails, generate soothing, quiet ambient background music using ffmpeg
    logger.info("Generating quiet ambient background music using ffmpeg synthesizer...")
    return generate_quiet_ambient_music(duration=180.0, output_path=output_path)


def generate_quiet_ambient_music(duration: float, output_path: Path) -> Path:
    """Generates a gentle, soothing ambient harmonic soundscape using ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required for ambient music generation.")

    codec = ["-c:a", "libmp3lame"] if output_path.suffix.lower() == ".mp3" else ["-c:a", "aac"]
    cmd = [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=220:duration={duration + 10}",
        "-af", "volume=0.03",
        *codec,
        "-b:a", "128k",
        str(output_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except Exception:
        # Fallback to anullsrc silent audio if sine fails
        cmd_alt = [
            "ffmpeg",
            "-y",
            "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", str(duration + 10),
            *codec,
            str(output_path),
        ]
        subprocess.run(cmd_alt, check=True, capture_output=True)

    return output_path


def render_final_video(
    background_video: Path,
    voiceover_audio: Path,
    subtitles_ass: Path,
    output_video: Path,
    duration: float,
    background_music: Optional[Path] = None,
) -> Path:
    """Composites the final vertical TikTok video using ffmpeg.

    Applies:
    - 9:16 vertical crop & scale: 1080x1920, 30fps
    - Burn-in ASS dynamic subtitles centered in the middle of the screen
    - Mixes TTS voiceover as primary audio + quiet background music ducked at -26dB
    - Completely removes all sounds from the background video
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

    # Video filter chain: scale -> crop -> 30fps -> subtitles
    video_filter = (
        f"[0:v]scale={config.video_width}:{config.video_height}:"
        f"force_original_aspect_ratio=increase,"
        f"crop={config.video_width}:{config.video_height}:"
        f"(in_w-{config.video_width})/2:(in_h-{config.video_height})/2,"
        f"fps={config.video_fps},"
        f"subtitles='{escaped_subtitles}':fontsdir='{escaped_fontsdir}'[v]"
    )

    has_music = bool(background_music and background_music.exists())
    if has_music:
        # Audio filter: duck quiet background music, mix with full voiceover
        # Note: background video sound [0:a] is completely removed/discarded!
        # normalize=0 ensures voice stays at 100% full volume and music is mixed at exact target level
        audio_filter = (
            f"[2:a]volume={config.bg_music_volume_db}[bg_music];"
            f"[1:a]volume=1.0[voice];"
            f"[voice][bg_music]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]"
        )
        filter_complex = f"{video_filter};{audio_filter}"
        cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop", "-1",
            "-i", str(background_video),
            "-i", str(voiceover_audio),
            "-stream_loop", "-1",
            "-i", str(background_music),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "[a]",
        ]
    else:
        # Clean voiceover audio only (allows adding native TikTok trending sound in-app)
        filter_complex = f"{video_filter}"
        cmd = [
            "ffmpeg",
            "-y",
            "-stream_loop", "-1",
            "-i", str(background_video),
            "-i", str(voiceover_audio),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "1:a",
        ]

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

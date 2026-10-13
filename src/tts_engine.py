"""Text-to-Speech Engine using edge-tts with precise word-level boundary offsets."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import edge_tts
from src.config import config

logger = logging.getLogger(__name__)


@dataclass
class WordTiming:
    """Represents the start and end timestamps (in seconds) for a single spoken word."""

    word: str
    start: float  # seconds
    end: float    # seconds


@dataclass
class TTSResult:
    """Result of speech synthesis containing audio file path and word timings."""

    audio_path: Path
    duration_seconds: float
    word_timings: List[WordTiming]
    voice_used: str
    metadata_json_path: Path


def select_voice(gender: str) -> Tuple[str, str]:
    """Returns (primary_voice, fallback_voice) matching the given gender."""
    if gender.lower() == "female":
        return config.voice_female_primary, config.voice_female_fallback
    return config.voice_male_primary, config.voice_male_fallback


def get_voices_to_try(gender: str) -> List[str]:
    """Returns prioritized candidate list of neural Edge-TTS voices for the narrator gender."""
    if gender.lower() == "female":
        candidates = [
            config.voice_female_primary,
            config.voice_female_fallback,
            "en-US-AvaNeural",
            "en-US-EmmaNeural",
            "en-US-MichelleNeural",
            "en-GB-SoniaNeural",
        ]
    else:
        candidates = [
            config.voice_male_primary,
            config.voice_male_fallback,
            "en-US-BrianNeural",
            "en-US-AndrewNeural",
            "en-US-EricNeural",
            "en-GB-RyanNeural",
        ]
    # Deduplicate while preserving priority order
    seen = set()
    result = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _synthesize_with_gtts(text: str, gender: str, output_audio: Path) -> bool:
    """Fallback speech synthesis using Google Translate TTS (gTTS).

    High-reliability HTTPS synthesis that does not use WebSockets and bypasses 403 blocks.
    """
    try:
        from gtts import gTTS

        tld = "co.uk" if gender.lower() == "female" else "com"
        logger.info("Attempting gTTS fallback synthesis (tld: %s)...", tld)
        tts = gTTS(text=text, lang="en", tld=tld, slow=False)
        tts.save(str(output_audio))

        if output_audio.exists() and output_audio.stat().st_size > 1024:
            logger.info("gTTS fallback synthesis succeeded (%d bytes)", output_audio.stat().st_size)
            return True
        logger.warning("gTTS audio file was empty or missing.")
        return False
    except Exception as e:
        logger.warning("gTTS synthesis failed: %s", e)
        return False


def get_audio_duration_ffprobe(audio_path: Path) -> Optional[float]:
    """Uses ffprobe to obtain exact audio duration in seconds if ffprobe is installed."""
    if not shutil.which("ffprobe"):
        return None

    try:
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning("Could not read duration via ffprobe: %s", e)
        return None


async def _synthesize_edge_tts(
    text: str, voice: str, output_audio: Path
) -> List[WordTiming]:
    """Synthesizes text using edge-tts and streams WordBoundary events."""
    word_timings: List[WordTiming] = []

    communicate = edge_tts.Communicate(
        text=text, voice=voice, rate="+0%", pitch="+0Hz", boundary="WordBoundary"
    )

    with open(output_audio, "wb") as f_out:
        async for chunk in communicate.stream():
            chunk_type = chunk.get("type")
            if chunk_type == "audio":
                f_out.write(chunk.get("data", b""))
            elif chunk_type == "WordBoundary":
                # Offset and duration are provided in 100-nanosecond ticks
                offset_ticks = chunk.get("offset", 0)
                duration_ticks = chunk.get("duration", 0)
                word_text = chunk.get("text", "").strip()

                if word_text:
                    start_sec = offset_ticks / 10_000_000.0
                    end_sec = (offset_ticks + duration_ticks) / 10_000_000.0
                    word_timings.append(
                        WordTiming(word=word_text, start=start_sec, end=end_sec)
                    )

    return word_timings


def estimate_word_timings_from_text(text: str, total_duration: float) -> List[WordTiming]:
    """Fallback estimator that calculates word timings proportionally across total duration."""
    raw_words = text.split()
    if not raw_words or total_duration <= 0:
        return []

    # Assign weights based on character length with extra weight for punctuation pauses
    weights = []
    for w in raw_words:
        base_weight = max(1, len(w))
        if w[-1] in ".!?":
            base_weight += 4
        elif w[-1] in ",;:":
            base_weight += 2
        weights.append(base_weight)

    total_weight = sum(weights)
    current_time = 0.1  # Initial speech onset offset
    available_duration = max(1.0, total_duration - 0.4)

    timings: List[WordTiming] = []
    for w, weight in zip(raw_words, weights):
        word_dur = (weight / total_weight) * available_duration
        timings.append(
            WordTiming(
                word=w,
                start=round(current_time, 3),
                end=round(current_time + word_dur, 3),
            )
        )
        current_time += word_dur

    return timings


def generate_voiceover(
    text: str,
    gender: str,
    output_dir: Path,
    filename_prefix: str = "voiceover",
) -> TTSResult:
    """Generates voiceover audio and timestamp metadata with automated fallback voices.

    Args:
        text: Text to synthesize.
        gender: 'male' or 'female'.
        output_dir: Directory where the output audio and metadata will be saved.
        filename_prefix: Base filename for output assets.

    Returns:
        TTSResult containing audio path, duration, word timings, and metadata path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_path = output_dir / f"{filename_prefix}.mp3"
    metadata_path = output_dir / f"{filename_prefix}_timings.json"

    voices_to_try = get_voices_to_try(gender)

    last_error: Optional[Exception] = None
    successful_voice = ""
    word_timings: List[WordTiming] = []

    # 1. Primary Strategy: Microsoft Edge-TTS with WordBoundary streaming
    for voice in voices_to_try:
        try:
            logger.info("Synthesizing voiceover with edge-tts voice '%s'...", voice)
            word_timings = asyncio.run(_synthesize_edge_tts(text, voice, audio_path))
            if audio_path.exists() and audio_path.stat().st_size > 1024:
                successful_voice = voice
                logger.info(
                    "Edge-TTS synthesis succeeded (%d words, %d bytes)",
                    len(word_timings),
                    audio_path.stat().st_size,
                )
                break
        except Exception as e:
            logger.warning("Synthesis failed with voice '%s': %s", voice, e)
            last_error = e

    # 2. Resilient Fallback Strategy: Google Translate TTS (gTTS)
    if not successful_voice:
        logger.warning(
            "All Edge-TTS voices failed (last error: %s). Activating gTTS fallback engine...",
            last_error,
        )
        if _synthesize_with_gtts(text, gender, audio_path):
            successful_voice = f"gTTS-en-{'female' if gender.lower() == 'female' else 'male'}"
            word_timings = []  # Will be generated via proportional estimator below
        else:
            raise RuntimeError(
                f"All voice synthesis attempts failed for gender '{gender}'. Last error: {last_error}"
            )

    # Determine total duration
    duration = get_audio_duration_ffprobe(audio_path)
    if not duration:
        # Fallback to last word timing plus 0.4s buffer
        if word_timings:
            duration = word_timings[-1].end + 0.5
        else:
            duration = 180.0

    # If no WordBoundary events were emitted, use proportional timing estimation
    if not word_timings:
        logger.info("Using proportional word timing estimation across %.2fs duration.", duration)
        word_timings = estimate_word_timings_from_text(text, duration)

    # Save word timings metadata
    metadata = {
        "voice": successful_voice,
        "gender": gender,
        "duration_seconds": round(duration, 3),
        "total_words": len(word_timings),
        "words": [asdict(w) for w in word_timings],
    }
    with open(metadata_path, "w", encoding="utf-8") as f_meta:
        json.dump(metadata, f_meta, indent=2)

    return TTSResult(
        audio_path=audio_path,
        duration_seconds=duration,
        word_timings=word_timings,
        voice_used=successful_voice,
        metadata_json_path=metadata_path,
    )

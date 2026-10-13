"""Dynamic TikTok-style subtitle generator producing animated .ass subtitles."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List

from src.config import config
from src.tts_engine import WordTiming

logger = logging.getLogger(__name__)


@dataclass
class SubtitleChunk:
    """A small chunk of 1 to 3 words displayed together on screen."""

    words: List[WordTiming]

    @property
    def start(self) -> float:
        return self.words[0].start if self.words else 0.0

    @property
    def end(self) -> float:
        return self.words[-1].end if self.words else 0.0


def format_ass_time(seconds: float) -> str:
    """Formats seconds into ASS timestamp format: H:MM:SS.cs (centiseconds)."""
    if seconds < 0:
        seconds = 0.0

    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))

    if centis >= 100:
        centis = 0
        secs += 1
    if secs >= 60:
        secs = 0
        minutes += 1
    if minutes >= 60:
        minutes = 0
        hours += 1

    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def build_chunks(
    word_timings: List[WordTiming], max_words: int = 3, max_chars: int = 22
) -> List[SubtitleChunk]:
    """Groups consecutive words into short, punchy 1-3 word chunks.

    Breaks on:
    - Maximum word limit (2-3 words)
    - Punctuation (. ! ? , ;)
    - Natural speech pauses (>0.35s gap between words)
    """
    chunks: List[SubtitleChunk] = []
    current_words: List[WordTiming] = []

    for i, wt in enumerate(word_timings):
        current_words.append(wt)
        clean_word = wt.word.strip()

        # Check conditions to end current chunk
        has_break_punctuation = bool(clean_word and clean_word[-1] in ".!?,;:")
        reached_word_limit = len(current_words) >= max_words
        reached_char_limit = sum(len(w.word) for w in current_words) >= max_chars

        # Check pause gap to next word
        has_speech_pause = False
        if i + 1 < len(word_timings):
            next_start = word_timings[i + 1].start
            if next_start - wt.end > 0.35:
                has_speech_pause = True

        if (
            has_break_punctuation
            or reached_word_limit
            or reached_char_limit
            or has_speech_pause
            or i == len(word_timings) - 1
        ):
            chunks.append(SubtitleChunk(words=list(current_words)))
            current_words.clear()

    if current_words:
        chunks.append(SubtitleChunk(words=list(current_words)))

    return chunks


def generate_ass_subtitles(
    word_timings: List[WordTiming],
    output_ass_path: Path,
    highlight_color: str = config.highlight_color_ass,
    primary_color: str = config.primary_color_ass,
    outline_color: str = config.outline_color_ass,
    font_name: str = config.font_name,
    font_size: int = config.font_size,
    alignment: int = config.subtitle_alignment,
    margin_v: int = config.subtitle_margin_v,
    outline_width: int = config.outline_width,
    shadow: int = config.shadow_depth,
    story_title: Optional[str] = None,
    category_tag: Optional[str] = None,
) -> Path:
    """Generates an Advanced SubStation Alpha (.ass) subtitle file with word-by-word highlights.

    Subtitles are centered directly in the middle of the screen (Alignment 5) for high-engagement viewing.
    Optionally renders an eye-catching viral hook header badge for the opening seconds.
    """
    output_ass_path.parent.mkdir(parents=True, exist_ok=True)
    chunks = build_chunks(word_timings, max_words=3, max_chars=20)

    header = f"""[Script Info]
Title: TikTok Viral Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: {config.video_width}
PlayResY: {config.video_height}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_color},&H000000FF&,{outline_color},&H80000000&,-1,0,0,0,100,100,1.5,0,1,{outline_width},{shadow},{alignment},40,40,{margin_v},1
Style: HeaderHook,{font_name},46,&H00FFFFFF&,&H000000FF&,{outline_color},&H80000000&,-1,0,0,0,100,100,1.5,0,1,4,2,8,60,60,200,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    dialogue_lines: List[str] = []

    # Optional viral hook header for opening seconds
    if story_title:
        tag_str = f"[ {category_tag.upper()} ]" if category_tag else "[ r/AskReddit ]"
        clean_title = story_title.strip()
        if len(clean_title) > 85:
            clean_title = clean_title[:82] + "..."
        words = clean_title.split()
        if len(words) > 7:
            mid = len(words) // 2
            clean_title = " ".join(words[:mid]) + "\\N" + " ".join(words[mid:])

        hook_dur = 5.0
        if word_timings:
            hook_dur = min(6.0, max(3.5, word_timings[-1].end))
        start_hook = format_ass_time(0.0)
        end_hook = format_ass_time(hook_dur)
        hook_line = (
            f"Dialogue: 1,{start_hook},{end_hook},HeaderHook,,0,0,0,,"
            f"{{\\c{highlight_color}\\b1}}{tag_str}{{\\r\\c&H00FFFFFF&\\b1}}\\N{clean_title}"
        )
        dialogue_lines.append(hook_line)

    for chunk in chunks:
        num_words = len(chunk.words)
        if num_words == 0:
            continue

        for active_idx, active_word in enumerate(chunk.words):
            sub_start = active_word.start

            # The sub-interval ends when the next word starts, or when the chunk ends
            if active_idx + 1 < num_words:
                sub_end = chunk.words[active_idx + 1].start
            else:
                sub_end = chunk.end

            # Ensure valid non-negative time slice
            if sub_end <= sub_start:
                sub_end = sub_start + 0.15

            start_str = format_ass_time(sub_start)
            end_str = format_ass_time(sub_end)

            # Assemble words with the active word highlighted
            parts = []
            for idx, wt in enumerate(chunk.words):
                clean_w = wt.word.upper().strip()
                if not clean_w:
                    continue

                if idx == active_idx:
                    # Highlighted active word with bright color and slight scale pop
                    parts.append(
                        f"{{\\c{highlight_color}\\fscx106\\fscy106}}{clean_w}{{\\r\\c{primary_color}}}"
                    )
                else:
                    # Regular word
                    parts.append(clean_w)

            line_text = " ".join(parts)
            dialogue_line = (
                f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{line_text}"
            )
            dialogue_lines.append(dialogue_line)

    full_content = header + "\n".join(dialogue_lines) + "\n"

    with open(output_ass_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    logger.info(
        "Generated ASS subtitles with %d dialogue events at '%s'",
        len(dialogue_lines),
        output_ass_path,
    )
    return output_ass_path

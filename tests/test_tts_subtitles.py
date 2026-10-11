"""Unit tests for subtitles generation and timing calculations."""

from pathlib import Path
from src.tts_engine import WordTiming
from src.subtitles import (
    format_ass_time,
    build_chunks,
    generate_ass_subtitles,
)


def test_format_ass_time():
    assert format_ass_time(0.0) == "0:00:00.00"
    assert format_ass_time(1.25) == "0:00:01.25"
    assert format_ass_time(65.5) == "0:01:05.50"
    assert format_ass_time(3661.12) == "1:01:01.12"


def test_build_chunks():
    timings = [
        WordTiming(word="I", start=0.0, end=0.2),
        WordTiming(word="could", start=0.2, end=0.5),
        WordTiming(word="not.", start=0.5, end=0.9),  # Punctuation ends chunk
        WordTiming(word="believe", start=1.0, end=1.4),
        WordTiming(word="it", start=1.4, end=1.7),
    ]

    chunks = build_chunks(timings, max_words=3)
    assert len(chunks) == 2
    assert [w.word for w in chunks[0].words] == ["I", "could", "not."]
    assert [w.word for w in chunks[1].words] == ["believe", "it"]
    assert chunks[0].start == 0.0
    assert chunks[0].end == 0.9


def test_generate_ass_subtitles(tmp_path: Path):
    timings = [
        WordTiming(word="HELLO", start=0.0, end=0.5),
        WordTiming(word="TIKTOK", start=0.5, end=1.0),
        WordTiming(word="WORLD", start=1.0, end=1.5),
    ]
    output_ass = tmp_path / "test.ass"
    result_path = generate_ass_subtitles(
        word_timings=timings,
        output_ass_path=output_ass,
    )

    assert result_path.exists()
    content = result_path.read_text(encoding="utf-8")
    assert "[Script Info]" in content
    assert "[V4+ Styles]" in content
    assert "[Events]" in content
    assert "Dialogue:" in content
    # Verify highlight color tag is used
    assert "\\c&H0000FFFF&" in content
    assert "HELLO" in content
    assert "TIKTOK" in content
    assert "WORLD" in content

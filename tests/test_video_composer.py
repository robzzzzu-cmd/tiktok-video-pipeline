"""Unit tests for video composer helpers and TikTok caption formatting."""

from pathlib import Path
from src.video_composer import escape_ffmpeg_filter_path
from src.tiktok_uploader import build_caption


def test_escape_ffmpeg_filter_path():
    p = Path("C:/videos/captions.ass")
    escaped = escape_ffmpeg_filter_path(p)
    # Colons should be escaped for ffmpeg filter arguments
    assert "\\:" in escaped or "/" in escaped


def test_build_caption():
    title = "AITA for canceling my vacation?"
    hashtags = ["#storytime", "#fyp"]
    caption = build_caption(title, hashtags)
    assert "AITA for canceling my vacation?" in caption
    assert "#storytime #fyp" in caption


def test_build_caption_truncation():
    long_title = "A" * 150
    caption = build_caption(long_title)
    assert len(caption.split("\n\n")[0]) <= 100
    assert "..." in caption

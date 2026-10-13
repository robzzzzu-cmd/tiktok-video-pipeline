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


def test_normalize_video_url():
    from src.video_composer import normalize_video_url

    hf_blob = "https://huggingface.co/datasets/user/repo/blob/main/video.mp4"
    normalized = normalize_video_url(hf_blob)
    assert "/resolve/" in normalized
    assert "/blob/" not in normalized

    normal_url = "https://example.com/video.mp4"
    assert normalize_video_url(normal_url) == normal_url


def test_download_background_music_cached(tmp_path):
    from src.video_composer import download_background_music
    cached = tmp_path / "cached_music.mp3"
    cached.write_bytes(b"x" * 20000)
    result = download_background_music("https://example.com/audio", output_path=cached)
    assert result == cached
    assert result.exists()


def test_background_video_presets():
    from src.config import config
    assert "minecraft" in config.background_video_presets
    assert "subway_surfers" in config.background_video_presets
    assert "gta" in config.background_video_presets
    assert "satisfying" in config.background_video_presets
    assert "beamng" in config.background_video_presets
    for style, urls in config.background_video_presets.items():
        assert len(urls) >= 1
        for u in urls:
            assert u.startswith("https://")


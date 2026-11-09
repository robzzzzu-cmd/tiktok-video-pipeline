"""Unit tests for the Dark-Mode Reddit Card generator."""

from pathlib import Path
from PIL import Image

from src.reddit_card import generate_reddit_card, _wrap_text, _load_font


def test_wrap_text():
    font = _load_font(None, 30)
    text = "AITA for telling my mother in law that she cannot move into our new house?"
    lines = _wrap_text(text, font, max_width=500)
    assert len(lines) >= 2
    reconstructed = " ".join(lines)
    assert "mother in law" in reconstructed


def test_generate_reddit_card_part1(tmp_path: Path):
    out = tmp_path / "test_card_p1.png"
    res = generate_reddit_card(
        title="AITA for canceling my sister's wedding venue after what she did?",
        subreddit="r/AITAH",
        part_num=1,
        category="aitah",
        output_path=out,
    )
    assert res.exists()
    assert res.stat().st_size > 5000  # Non-empty PNG

    with Image.open(res) as img:
        assert img.size == (1080, 1920)
        assert img.mode == "RGBA"


def test_generate_reddit_card_part2(tmp_path: Path):
    out = tmp_path / "test_card_p2.png"
    res = generate_reddit_card(
        title="My Sister's Wedding Drama [Part 2]",
        subreddit="r/relationship_advice",
        part_num=2,
        category="confession",
        output_path=out,
    )
    assert res.exists()
    with Image.open(res) as img:
        assert img.size == (1080, 1920)
        assert img.mode == "RGBA"

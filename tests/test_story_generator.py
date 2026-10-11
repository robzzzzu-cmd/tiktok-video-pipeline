"""Unit tests for story generation, text cleaning, and gender detection."""

from src.story_generator import (
    clean_text_for_narration,
    detect_narrator_gender,
    trim_to_target_words,
    get_curated_fallback_story,
)


def test_clean_text_markdown_and_links():
    raw = "Check [this link](https://reddit.com) for context. **Bold** and *italic* text."
    cleaned = clean_text_for_narration(raw)
    assert "https://reddit.com" not in cleaned
    assert "this link" in cleaned
    assert "**" not in cleaned
    assert "*" not in cleaned


def test_clean_text_acronym_expansions():
    raw = "AITA for telling my MIL that my SIL and BF are not invited? WIBTA if I left?"
    cleaned = clean_text_for_narration(raw)
    assert "Am I the asshole" in cleaned
    assert "mother-in-law" in cleaned
    assert "sister-in-law" in cleaned
    assert "boyfriend" in cleaned
    assert "Would I be the asshole" in cleaned


def test_clean_text_strips_edits():
    raw = "This is my initial post.\n\nEdit: Thanks for the gold! More info here.\nUpdate: We broke up."
    cleaned = clean_text_for_narration(raw)
    assert "initial post" in cleaned
    assert "Thanks for the gold" not in cleaned
    assert "We broke up" not in cleaned


def test_detect_narrator_gender_explicit_tags():
    assert detect_narrator_gender("I (24F) have a sister who hates me.", "AITA?") == "female"
    assert detect_narrator_gender("I (28M) decided to move out last night.", "AITA?") == "male"
    assert detect_narrator_gender("Me [22f] and my friend [23m] went out.", "Story") == "female"
    assert detect_narrator_gender("I am a 30 year old man with two dogs.", "Help") == "male"
    assert detect_narrator_gender("I am a 25 year old woman with an issue.", "Advice") == "female"


def test_detect_narrator_gender_relationship_inference():
    assert detect_narrator_gender("My husband told me he doesn't want to go.", "") == "female"
    assert detect_narrator_gender("My wife left for a business trip.", "") == "male"


def test_trim_to_target_words():
    words = [f"word{i}" for i in range(100)]
    text = " ".join(words) + ". Extra sentence here."
    trimmed = trim_to_target_words(text, min_words=20, max_words=50)
    assert len(trimmed.split()) <= 55


def test_curated_fallback_stories():
    story_f = get_curated_fallback_story("female", category="inheritance")
    assert story_f.narrator_gender == "female"
    assert story_f.word_count > 150
    assert story_f.title != ""

    story_m = get_curated_fallback_story("male", category="love")
    assert story_m.narrator_gender == "male"
    assert story_m.word_count > 150
    assert story_m.title != ""


def test_curated_fallback_all_categories():
    for cat in ["love", "inheritance", "entitled", "aitah"]:
        story_f = get_curated_fallback_story("female", category=cat)
        assert story_f.word_count > 100
        story_m = get_curated_fallback_story("male", category=cat)
        assert story_m.word_count > 100


def test_clean_story_title():
    from src.story_generator import clean_story_title
    # Preserves AITA and WIBTA prefixes for aitah category
    assert "Am I the asshole for" in clean_story_title("AITA for walking out of my sister's wedding?", category="aitah")
    assert clean_story_title("[UPDATE] My roommate stole my car") == "My roommate stole my car"
    assert "Would I be the asshole if" in clean_story_title("WIBTA if I told my parents the truth?", category="aitah")
    # Cleanly strips AITA prefixes for non-AITAH categories (love, inheritance, entitled)
    assert "Walking out" in clean_story_title("AITA for walking out of my sister's wedding?", category="love")
    assert "My sister sold" in clean_story_title("AITA for my sister sold our house?", category="inheritance")


def test_fetch_from_huggingface_reddit():
    from src.story_generator import fetch_from_huggingface_reddit
    story = fetch_from_huggingface_reddit()
    # If network is available, it returns an authentic story
    if story:
        assert story.word_count >= 100
        assert story.source == "reddit_dataset"
        assert story.narrator_gender in ("male", "female")

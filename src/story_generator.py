"""Story generation and ingestion module (Reddit public JSON / PRAW & Google Gemini)."""

from __future__ import annotations

import logging
import random
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import requests
from src.config import config

logger = logging.getLogger(__name__)


@dataclass
class Story:
    """Represents an ingested or generated story."""

    title: str
    body: str
    narrator_gender: str  # 'male' or 'female'
    source: str           # 'reddit', 'gemini', or 'fallback'
    word_count: int = 0
    subreddit: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def full_narration_text(self) -> str:
        """Returns the full text to be read by TTS (title + body)."""
        clean_title = self.title.strip()
        # Add period if title does not end in punctuation
        if clean_title and not clean_title[-1] in ".!?":
            clean_title += "."
        return f"{clean_title} {self.body.strip()}".strip()


# Common Reddit abbreviations to expand for natural TTS pronunciation
ACRONYM_MAP = {
    r"\bAITA\b": "Am I the asshole",
    r"\bWIBTA\b": "Would I be the asshole",
    r"\bNTA\b": "Not the asshole",
    r"\bYTA\b": "You are the asshole",
    r"\bESH\b": "Everyone sucks here",
    r"\bNAH\b": "No assholes here",
    r"\bOP\b": "original poster",
    r"\bMIL\b": "mother-in-law",
    r"\bFIL\b": "father-in-law",
    r"\bSIL\b": "sister-in-law",
    r"\bBIL\b": "brother-in-law",
    r"\bBF\b": "boyfriend",
    r"\bGF\b": "girlfriend",
    r"\bSO\b": "significant other",
    r"\bDH\b": "dear husband",
    r"\bDW\b": "dear wife",
    r"\bTLDR\b": "too long didn't read",
    r"\bTL;DR\b": "too long didn't read",
    r"\bTIFU\b": "today I messed up",
}


def clean_text_for_narration(text: str) -> str:
    """Cleans raw story text for seamless, natural speech synthesis."""
    if not text:
        return ""

    # Remove markdown links [text](url) -> text
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)

    # Remove URLs
    text = re.sub(r"https?://\S+", "", text)

    # Remove bold/italics markdown asterisks and tildes
    text = re.sub(r"[*_~]{1,3}", "", text)

    # Remove quote blocks (> Quote)
    text = re.sub(r"^\s*>\s*.*$", "", text, flags=re.MULTILINE)

    # Remove 'Edit:', 'Update:', and 'TL;DR' trailing notes
    text = re.split(r"\n+\s*(?:EDIT|Edit|UPDATE|Update|TL;?DR)\b.*", text, flags=re.IGNORECASE)[0]

    # Expand common Reddit acronyms
    for pattern, replacement in ACRONYM_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Normalize multiple newlines and spaces
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def detect_narrator_gender(text: str, title: str = "") -> str:
    """Detects whether the primary narrator is Male or Female based on story markers.

    Inspects tags such as '[24M]', '(28F)', '25F', 'I (26F)', 'I am a 25 year old woman'.
    Defaults to a random 50/50 choice if undetectable.
    """
    combined = f"{title} {text[:800]}"

    # 1. Exact age/gender pattern: e.g. [24M], (28F), 22M, 30F
    # Match patterns like I (24M), I am a 24F, Me [25F]
    explicit_female = re.search(
        r"(?:(?:I|me|my)\s*(?:am|\'m)?\s*(?:a\s+)?)?[\[\(]?\b(?:\d{2})\s*([Ff])\b[\]\)]?",
        combined,
    )
    explicit_male = re.search(
        r"(?:(?:I|me|my)\s*(?:am|\'m)?\s*(?:a\s+)?)?[\[\(]?\b(?:\d{2})\s*([Mm])\b[\]\)]?",
        combined,
    )

    if explicit_female and not explicit_male:
        logger.info("Detected female narrator from explicit age/gender tag.")
        return "female"
    if explicit_male and not explicit_female:
        logger.info("Detected male narrator from explicit age/gender tag.")
        return "male"

    # 2. Phrasal declarations: 'I am a woman', 'I'm a 26 year old girl', 'as a guy'
    if re.search(r"\b(?:I\s+am|I\'m)\s+a\s+(?:woman|girl|female|mother|mom|wife)\b", combined, re.I):
        logger.info("Detected female narrator from declaration phrase.")
        return "female"
    if re.search(r"\b(?:I\s+am|I\'m)\s+a\s+(?:man|guy|male|father|dad|husband)\b", combined, re.I):
        logger.info("Detected male narrator from declaration phrase.")
        return "male"

    # 3. Contextual relationship markers
    # 'My husband / boyfriend' (majority female narrator in relationship advice subreddits)
    if re.search(r"\bmy\s+(?:husband|fiancé|boyfriend)\b", combined, re.I) and not re.search(r"\bmy\s+(?:wife|fiancée|girlfriend)\b", combined, re.I):
        logger.info("Inferred female narrator from partner reference.")
        return "female"
    if re.search(r"\bmy\s+(?:wife|fiancée|girlfriend)\b", combined, re.I) and not re.search(r"\bmy\s+(?:husband|fiancé|boyfriend)\b", combined, re.I):
        logger.info("Inferred male narrator from partner reference.")
        return "male"

    # 4. Ambiguous: randomly select
    chosen = random.choice(["male", "female"])
    logger.info("Narrator gender ambiguous; randomly selected: %s", chosen)
    return chosen


def trim_to_target_words(text: str, min_words: int, max_words: int) -> str:
    """Trims a long story to fit comfortably within the ~3 minute video target word count."""
    words = text.split()
    if len(words) <= max_words:
        return text

    # Slice at max_words
    trimmed = " ".join(words[:max_words])

    # Find the last sentence end (. ! ?) so we end cleanly
    last_punct = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
    if last_punct > 100:
        return trimmed[: last_punct + 1]
    return trimmed + "..."


def fetch_from_reddit_json(subreddit: str) -> Optional[Story]:
    """Fetches a trending dramatic story using Reddit public JSON endpoint."""
    url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit=30"
    headers = {"User-Agent": config.reddit_user_agent}

    try:
        response = requests.get(url, headers=headers, timeout=12)
        if response.status_code != 200:
            logger.warning(
                "Reddit JSON endpoint for r/%s returned status %d",
                subreddit,
                response.status_code,
            )
            return None

        data = response.json()
        children = data.get("data", {}).get("children", [])

        for item in children:
            post = item.get("data", {})
            if post.get("stickied") or post.get("over_18") is None:
                continue

            selftext = post.get("selftext", "")
            title = post.get("title", "")

            if not selftext or len(selftext) < 500:
                continue

            # Clean and evaluate word count
            cleaned_body = clean_text_for_narration(selftext)
            words = cleaned_body.split()
            word_count = len(words)

            # Target ~3 minute narration (approx 420-560 words)
            if word_count < config.target_words_min:
                continue

            # If slightly longer, trim to target
            final_body = trim_to_target_words(
                cleaned_body, config.target_words_min, config.target_words_max
            )
            final_words = len(final_body.split())

            gender = detect_narrator_gender(final_body, title)

            return Story(
                title=clean_text_for_narration(title),
                body=final_body,
                narrator_gender=gender,
                source="reddit",
                word_count=final_words,
                subreddit=subreddit,
                tags=[f"r/{subreddit}", "redditstories"],
            )

    except Exception as e:
        logger.error("Error fetching Reddit stories via JSON: %s", e)

    return None


def fetch_from_reddit_praw(subreddit: str) -> Optional[Story]:
    """Fetches a story using PRAW if credentials are provided."""
    if not (config.reddit_client_id and config.reddit_client_secret):
        return None

    try:
        import praw

        reddit = praw.Reddit(
            client_id=config.reddit_client_id,
            client_secret=config.reddit_client_secret,
            user_agent=config.reddit_user_agent,
        )

        sub = reddit.subreddit(subreddit)
        for post in sub.hot(limit=30):
            if post.stickied:
                continue

            cleaned_body = clean_text_for_narration(post.selftext)
            words = cleaned_body.split()
            if len(words) < config.target_words_min:
                continue

            final_body = trim_to_target_words(
                cleaned_body, config.target_words_min, config.target_words_max
            )
            gender = detect_narrator_gender(final_body, post.title)

            return Story(
                title=clean_text_for_narration(post.title),
                body=final_body,
                narrator_gender=gender,
                source="reddit_praw",
                word_count=len(final_body.split()),
                subreddit=subreddit,
            )
    except Exception as e:
        logger.warning("PRAW fetch failed: %s", e)

    return None


def generate_with_gemini(gender_override: Optional[str] = None) -> Optional[Story]:
    """Generates an engaging, viral Reddit-style story using Google Gemini API (`google-genai`)."""
    if not config.gemini_api_key:
        logger.info("No GEMINI_API_KEY provided; skipping Gemini story generation.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.gemini_api_key)

        prompt_gender = gender_override or random.choice(["male", "female"])
        gender_tag = "26F" if prompt_gender == "female" else "28M"

        prompt = f"""
You are an award-winning creative storyteller specializing in viral, dramatic Reddit storytime posts (like r/AITAH, r/relationship_advice).

Write a compelling, realistic, suspenseful, first-person story.
RULES:
1. TARGET LENGTH: Exactly 460 to 510 words (this will be spoken in a 3-minute video at ~160 WPM).
2. NARRATOR: The narrator is {prompt_gender.upper()}, tagged explicitly in the opening line as "I ({gender_tag})".
3. TONE: Natural conversational storytelling with an intense hook, mounting tension, and a satisfying or shocking confrontation.
4. NO META REMARKS: Do not include intros like "Here is the story:" or markdown asterisks.
5. FORMAT: Provide ONLY the Title on line 1, followed by a blank line, and then the complete story narrative.
"""

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.85,
                max_output_tokens=1200,
            ),
        )

        text = response.text.strip()
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        if len(lines) >= 2:
            title = lines[0].replace("#", "").replace("**", "").strip()
            body = " ".join(lines[1:]).strip()
        else:
            title = "A confession I never thought I would have to make"
            body = text

        cleaned_body = clean_text_for_narration(body)
        final_body = trim_to_target_words(
            cleaned_body, config.target_words_min, config.target_words_max
        )
        detected_gender = gender_override or detect_narrator_gender(final_body, title)

        return Story(
            title=title,
            body=final_body,
            narrator_gender=detected_gender,
            source="gemini",
            word_count=len(final_body.split()),
            tags=["gemini", "ai_storytime"],
        )

    except Exception as e:
        logger.error("Gemini generation failed: %s", e)

    return None


def get_curated_fallback_story(gender_override: Optional[str] = None) -> Story:
    """Curated, high-retention fallback story to guarantee pipeline reliability."""
    gender = gender_override or random.choice(["male", "female"])

    if gender == "female":
        title = "AITA for walking out of my sister's wedding after seeing the seating chart?"
        body = (
            "I (27F) have always had a rocky relationship with my older sister, Chloe. "
            "Growing up, our parents treated her like royalty while I was expected to blend into the wallpaper. "
            "When Chloe announced her engagement last year, I genuinely wanted to put the past behind us. "
            "I spent months helping her organize floral arrangements, drove six hours to pick up her custom veil, "
            "and even contributed fifteen hundred dollars toward her dream catering company when she went over budget. "
            "Fast forward to the wedding day yesterday. The ceremony was gorgeous, but as everyone walked into the grand ballroom "
            "for the reception, people started crowding around the master seating display. "
            "I searched for my name at Table 1 alongside our parents and grandparents, but I was not there. "
            "I checked Table 2 with the bridal party. Nothing. "
            "Finally, my cousin tapped my shoulder with a look of pure second-hand embarrassment and pointed towards the far back corner. "
            "Tucked away right behind the industrial kitchen swinging doors, next to the emergency fire extinguisher, "
            "was a small fold-out table labeled 'Vendors and Extra Guests.' "
            "Sitting right there was my name card. "
            "Sitting at the table with our immediate family was Chloe's ex-boyfriend, whom she had invited as an honored guest. "
            "When I confronted Chloe in the dressing room, she laughed and said, 'Space was tight, and you are family, so you understand.' "
            "I took a deep breath, handed her back my corsage, and walked straight out to my car. "
            "Now my phone is blowing up with family calling me selfish for causing a scene. Am I the asshole?"
        )
    else:
        title = "AITA for locking my brother out of my apartment after finding what he did in my kitchen?"
        body = (
            "I (29M) bought my first two-bedroom condo six months ago after saving aggressively for seven straight years. "
            "My younger brother, Mark (25M), recently graduated and needed a temporary place to stay while interviewing for jobs. "
            "I told him he could stay in my spare room rent-free for three weeks under two clear conditions: "
            "keep the shared spaces clean, and never throw parties while I was working seventy-hour hospital night shifts. "
            "Last night, I returned home at 7 AM completely exhausted after back-to-back emergency rotations. "
            "The moment I opened my front door, the smell of burnt sugar and scorched electronics hit me. "
            "I walked into my kitchen and found the entire marble countertop covered in scorched grease, shattered glass from my blender, "
            "and multiple broken light fixtures. "
            "Mark had invited nine of his college buddies over at 3 AM to shoot amateur viral food challenges for social media. "
            "They had tried deep-frying whole candy bars in an open aluminum pot, which caught fire and scorched my cabinets. "
            "Mark was passed out on the sofa surrounded by trash, completely indifferent. "
            "I packed his bags immediately, set them outside the hallway door, changed the digital lock code, and went to sleep. "
            "My mother called me in tears this afternoon, saying Mark is sleeping in his car and that brothers always forgive each other. "
            "Am I the asshole for kicking him out?"
        )

    cleaned_body = clean_text_for_narration(body)
    return Story(
        title=title,
        body=cleaned_body,
        narrator_gender=gender,
        source="fallback",
        word_count=len(cleaned_body.split()),
        tags=["fallback", "redditstories"],
    )


def get_story(
    source: str = "auto",
    gender_override: Optional[str] = None,
    preferred_subreddit: Optional[str] = None,
) -> Story:
    """Master entry point to retrieve or generate a story following the specified strategy.

    Args:
        source: 'auto', 'reddit', or 'gemini'.
        gender_override: force 'male' or 'female' narrator.
        preferred_subreddit: specific subreddit name to query.
    """
    logger.info("Fetching story with strategy source='%s'", source)

    # 1. Direct Gemini request if specified
    if source == "gemini":
        gemini_story = generate_with_gemini(gender_override)
        if gemini_story:
            return gemini_story
        logger.warning("Gemini generation failed; attempting Reddit fallback.")

    # 2. Reddit Ingestion
    subreddits = [preferred_subreddit] if preferred_subreddit else config.subreddits
    random.shuffle(subreddits)

    for sub in subreddits:
        # Try PRAW if available
        story = fetch_from_reddit_praw(sub)
        if story:
            if gender_override:
                story.narrator_gender = gender_override
            return story

        # Fallback to public JSON endpoint
        story = fetch_from_reddit_json(sub)
        if story:
            if gender_override:
                story.narrator_gender = gender_override
            return story

    # 3. Gemini fallback if Reddit was attempted or auto
    if source in ("auto", "reddit") and config.gemini_api_key:
        logger.info("Attempting Gemini generation as fallback...")
        gemini_story = generate_with_gemini(gender_override)
        if gemini_story:
            return gemini_story

    # 4. Reliable built-in fallback story
    logger.warning("Using curated fallback story to maintain 100% pipeline uptime.")
    return get_curated_fallback_story(gender_override)

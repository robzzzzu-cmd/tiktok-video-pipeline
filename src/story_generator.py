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

    # Remove trailing forum boilerplate endings so the video concludes on the dramatic punchline
    text = re.sub(
        r"\s*(?:Am I the asshole\??|AITA\??|Would I be the asshole\??|So,\s*Reddit,\s*am I in the wrong\??|What do you guys think\??|What should I do\??)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    # Normalize multiple newlines and spaces
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def clean_story_title(title: str) -> str:
    """Cleans raw Reddit titles into natural viral story hooks for TikTok narration.

    Preserves AITA prefixes (e.g. 'AITA for...') while removing clutter tags like [UPDATE].
    """
    if not title:
        return ""

    # Remove reddit prefix tags: [UPDATE], Update:, (Update), [Repost], [TW], [Trigger Warning]
    title = re.sub(r"^\[(?:UPDATE|Repost|TW|Trigger Warning)\]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:UPDATE|Update):\s*", "", title)
    title = re.sub(r"^(?:Cross-posted|Crosspost|X-post)[:\-–]\s*", "", title, flags=re.IGNORECASE)

    # Note: As requested, we DO NOT remove AITA / Am I the asshole from the start!

    title = title.strip()
    if title:
        title = title[0].upper() + title[1:]

    return clean_text_for_narration(title)


def detect_narrator_gender(text: str, title: str = "") -> str:
    """Detects whether the primary narrator is Male or Female based on story markers.

    Inspects tags such as '[24M]', '(28F)', '25F', 'I (26F)', 'I am a 25 year old woman'.
    Defaults to a random 50/50 choice if undetectable.
    """
    combined = f"{title} {text[:800]}"

    # 1. Narrator explicit first-person tag: e.g. "I (24F)", "Me [22f]", "myself [25F]", "I am a 24F"
    first_person_match = re.search(
        r"\b(?:I|me|myself)\s*(?:am|\'m)?\s*(?:a\s+)?[\[\(]?\s*(?:\d{2})\s*([MFmf])\s*[\]\)]?",
        combined,
        re.I,
    )
    if first_person_match:
        gender_code = first_person_match.group(1).upper()
        chosen = "female" if gender_code == "F" else "male"
        logger.info("Detected %s narrator from direct first-person tag.", chosen)
        return chosen

    # 2. General isolated age/gender pattern if only one exists
    all_f = re.findall(r"[\[\(]\s*(?:\d{2})\s*[Ff]\s*[\]\)]", combined)
    all_m = re.findall(r"[\[\(]\s*(?:\d{2})\s*[Mm]\s*[\]\)]", combined)
    if all_f and not all_m:
        logger.info("Detected female narrator from solitary age/gender tag.")
        return "female"
    if all_m and not all_f:
        logger.info("Detected male narrator from solitary age/gender tag.")
        return "male"

    # 3. Phrasal declarations: 'I am a woman', 'I am a 30 year old man', 'I'm a 26 year old girl', 'as a guy'
    female_phrase = re.search(
        r"\b(?:(?:I\s+am|I\'m|as)\s+a\s+(?:\d{1,2}\s*(?:[-\s]?year[-\s]?old\s+)?)?(?:woman|girl|female|mother|mom|wife)|I\s+am\s+female)\b",
        combined,
        re.I,
    )
    male_phrase = re.search(
        r"\b(?:(?:I\s+am|I\'m|as)\s+a\s+(?:\d{1,2}\s*(?:[-\s]?year[-\s]?old\s+)?)?(?:man|guy|male|father|dad|husband)|I\s+am\s+male)\b",
        combined,
        re.I,
    )
    if female_phrase and not male_phrase:
        logger.info("Detected female narrator from declaration phrase.")
        return "female"
    if male_phrase and not female_phrase:
        logger.info("Detected male narrator from declaration phrase.")
        return "male"

    # 4. Contextual relationship markers
    # 'My husband / boyfriend' (majority female narrator in relationship advice subreddits)
    if re.search(r"\bmy\s+(?:husband|fiancé|boyfriend)\b", combined, re.I) and not re.search(r"\bmy\s+(?:wife|fiancée|girlfriend)\b", combined, re.I):
        logger.info("Inferred female narrator from partner reference.")
        return "female"
    if re.search(r"\bmy\s+(?:wife|fiancée|girlfriend)\b", combined, re.I) and not re.search(r"\bmy\s+(?:husband|fiancé|boyfriend)\b", combined, re.I):
        logger.info("Inferred male narrator from partner reference.")
        return "male"

    # 5. Ambiguous: randomly select
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
    """Fetches a trending dramatic story using Reddit public JSON endpoints."""
    # Alternates between hot and top feeds to find the highest-quality viral drama
    endpoints = [
        f"https://www.reddit.com/r/{subreddit}/hot.json?limit=35",
        f"https://www.reddit.com/r/{subreddit}/top.json?t=month&limit=35",
    ]
    random.shuffle(endpoints)
    headers = {"User-Agent": config.reddit_user_agent}

    for url in endpoints:
        try:
            response = requests.get(url, headers=headers, timeout=12)
            if response.status_code != 200:
                logger.warning(
                    "Reddit JSON endpoint for r/%s returned status %d",
                    subreddit,
                    response.status_code,
                )
                continue

            data = response.json()
            children = data.get("data", {}).get("children", [])

            for item in children:
                post = item.get("data", {})
                if post.get("stickied") or post.get("over_18") is None:
                    continue

                selftext = post.get("selftext", "")
                raw_title = post.get("title", "")

                if not selftext or len(selftext) < 450:
                    continue

                # Filter out meta or pinned discussions
                if any(w in raw_title.lower() for w in ["megathread", "weekly discussion", "meta:", "rules update"]):
                    continue

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
                cleaned_title = clean_story_title(raw_title)
                gender = detect_narrator_gender(final_body, cleaned_title)

                logger.info(
                    "Found Reddit story from r/%s: '%s' (%d words, gender: %s)",
                    subreddit,
                    cleaned_title,
                    final_words,
                    gender,
                )

                return Story(
                    title=cleaned_title,
                    body=final_body,
                    narrator_gender=gender,
                    source="reddit",
                    word_count=final_words,
                    subreddit=subreddit,
                    tags=[f"r/{subreddit}", "redditstories"],
                )

        except Exception as e:
            logger.warning("Error fetching Reddit stories via JSON from r/%s: %s", subreddit, e)

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
        for post in sub.hot(limit=35):
            if post.stickied:
                continue

            cleaned_body = clean_text_for_narration(post.selftext)
            words = cleaned_body.split()
            if len(words) < config.target_words_min:
                continue

            final_body = trim_to_target_words(
                cleaned_body, config.target_words_min, config.target_words_max
            )
            cleaned_title = clean_story_title(post.title)
            gender = detect_narrator_gender(final_body, cleaned_title)

            return Story(
                title=cleaned_title,
                body=final_body,
                narrator_gender=gender,
                source="reddit_praw",
                word_count=len(final_body.split()),
                subreddit=subreddit,
                tags=[f"r/{subreddit}", "redditstories"],
            )
    except Exception as e:
        logger.warning("PRAW fetch failed: %s", e)

    return None


STORY_CATEGORIES = {
    "love": {
        "name": "Love & Relationships",
        "subreddits": ["relationship_advice", "dating_advice", "confession", "TrueOffMyChest"],
        "keywords": ["husband", "wife", "boyfriend", "girlfriend", "fiancé", "fiancée", "cheating", "dating", "marriage", "wedding", "in love", "proposal"],
        "gemini_theme": "A high-stakes love, dating, or relationship story: uncovering a secret about a partner, infidelity confession, wedding drama, or a life-changing romantic decision.",
    },
    "inheritance": {
        "name": "Family Inheritance & Wills",
        "subreddits": ["familydrama", "EntitledPeople", "TrueOffMyChest", "pettyrevenge"],
        "keywords": ["inheritance", "will", "estate", "passed away", "trust fund", "house", "parents left", "property", "heir", "executor"],
        "gemini_theme": "A dramatic family inheritance battle: parents leaving the entire estate or house to the golden child, secret wills, or greedy relatives demanding the narrator's trust fund.",
    },
    "entitled": {
        "name": "Entitled People & Roommate Karma",
        "subreddits": ["EntitledPeople", "pettyrevenge", "tifu", "TrueOffMyChest"],
        "keywords": ["entitled", "roommate", "car", "towed", "stole", "police", "boss", "landlord"],
        "gemini_theme": "An entitled family member, roommate, or in-law demanding the narrator's car, money, or belongings, resulting in immediate humiliating karma, towing, or legal payback.",
    },
    "aitah": {
        "name": "Classic Am I The Asshole",
        "subreddits": ["AITAH", "AmItheAsshole"],
        "keywords": ["aita", "asshole", "am i the asshole"],
        "gemini_theme": "A classic moral dilemma where the narrator made a controversial but justified decision, dividing their family, starting with 'AITA for...'.",
    },
}


def fetch_from_huggingface_reddit(category: str = "auto") -> Optional[Story]:
    """Fetches authentic viral Reddit stories from the 270,000+ public dataset.

    Filters the dataset for category keywords (love, inheritance, entitled, or aitah).
    """
    cat_key = category if category in STORY_CATEGORIES else random.choice(list(STORY_CATEGORIES.keys()))
    cat_info = STORY_CATEGORIES[cat_key]
    keywords = cat_info["keywords"]

    total_entries = 270000

    for _ in range(3):
        random_offset = random.randint(100, total_entries - 50)
        url = (
            f"https://datasets-server.huggingface.co/rows?"
            f"dataset=OsamaBsher/AITA-Reddit-Dataset&config=default&split=train&"
            f"offset={random_offset}&length=25"
        )

        try:
            logger.info("Searching 270k+ public database for '%s' stories (offset %d)...", cat_info["name"], random_offset)
            response = requests.get(url, headers={"User-Agent": config.reddit_user_agent}, timeout=15)
            if response.status_code != 200:
                continue

            data = response.json()
            rows = data.get("rows", [])

            for item in rows:
                row = item.get("row", {})
                selftext = row.get("text", "")
                raw_title = row.get("title", "")
                score = row.get("score", 0)

                if not selftext or len(selftext) < 400:
                    continue

                combined_lower = f"{raw_title.lower()} {selftext[:1200].lower()}"

                # Match keywords if not generic aitah
                if cat_key != "aitah":
                    if not any(kw in combined_lower for kw in keywords):
                        continue

                cleaned_body = clean_text_for_narration(selftext)
                words = cleaned_body.split()
                if len(words) < config.target_words_min:
                    continue

                final_body = trim_to_target_words(
                    cleaned_body, config.target_words_min, config.target_words_max
                )
                final_words = len(final_body.split())
                cleaned_title = clean_story_title(raw_title)
                gender = detect_narrator_gender(final_body, cleaned_title)

                logger.info(
                    "Retrieved authentic Reddit %s story: '%s' (score: %s, %d words)",
                    cat_key,
                    cleaned_title,
                    score,
                    final_words,
                )
                return Story(
                    title=cleaned_title,
                    body=final_body,
                    narrator_gender=gender,
                    source="reddit_dataset",
                    word_count=final_words,
                    subreddit=cat_info["subreddits"][0],
                    tags=[f"r/{cat_info['subreddits'][0]}", "redditstories", cat_key],
                )

        except Exception as e:
            logger.warning("Failed to fetch from Reddit public dataset: %s", e)

    return None


def generate_with_gemini(
    gender_override: Optional[str] = None,
    category: str = "auto",
) -> Optional[Story]:
    """Generates an engaging, viral Reddit-style story using Google Gemini API (`google-genai`)."""
    if not config.gemini_api_key:
        logger.info("No GEMINI_API_KEY provided; skipping Gemini story generation.")
        return None

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=config.gemini_api_key)

        cat_key = category if category in STORY_CATEGORIES else random.choice(list(STORY_CATEGORIES.keys()))
        cat_info = STORY_CATEGORIES[cat_key]

        prompt_gender = gender_override or random.choice(["male", "female"])
        gender_tag = "26F" if prompt_gender == "female" else "28M"

        format_instruction = (
            "NARRATIVE FORMAT: Title should start with 'AITA for...' or 'Am I the asshole for...'"
            if cat_key == "aitah"
            else "NARRATIVE FORMAT: DO NOT use 'Am I the asshole?' or AITA format. Make it a gripping personal storytime."
        )

        prompt = f"""
You are an award-winning creative storyteller specializing in viral, dramatic Reddit storytime scripts ({cat_info['name']}).

THEME: {cat_info['gemini_theme']}

CRITICAL RULES:
1. TARGET LENGTH: Exactly 460 to 510 words (this will be spoken in a 3-minute video at ~160 WPM).
2. NARRATOR: The narrator is {prompt_gender.upper()}, tagged explicitly in the opening line as "I ({gender_tag})" or "I [{gender_tag}]".
3. TONE & PACING:
   - High suspense with an intense conversational hook in sentence 1.
   - Escalating tension and drama.
   - Satisfying confrontation, karma, or twist ending.
4. {format_instruction}
5. NO META REMARKS: Do not include intros like "Here is the story:" or markdown asterisks.
6. FORMAT: Provide ONLY the Title on line 1, followed by a blank line, and then the complete story narrative.
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
            raw_title = lines[0].replace("#", "").replace("**", "").strip()
            body = " ".join(lines[1:]).strip()
        else:
            raw_title = "A confession I never thought I would have to make"
            body = text

        cleaned_title = clean_story_title(raw_title)
        cleaned_body = clean_text_for_narration(body)
        final_body = trim_to_target_words(
            cleaned_body, config.target_words_min, config.target_words_max
        )
        detected_gender = gender_override or detect_narrator_gender(final_body, cleaned_title)

        return Story(
            title=cleaned_title,
            body=final_body,
            narrator_gender=detected_gender,
            source="gemini",
            word_count=len(final_body.split()),
            tags=["gemini", "ai_storytime", cat_key],
        )

    except Exception as e:
        logger.error("Gemini generation failed: %s", e)

    return None


def get_curated_fallback_story(
    gender_override: Optional[str] = None,
    category: str = "auto",
) -> Story:
    """Curated, high-retention fallback stories across diverse viral genres (love, inheritance, entitled, aitah)."""
    cat_key = category if category in STORY_CATEGORIES else random.choice(list(STORY_CATEGORIES.keys()))
    target_gender = gender_override or random.choice(["male", "female"])

    story_database = {
        "love": {
            "female": {
                "title": "I found a second phone hidden in my fiancé's car three weeks before our wedding",
                "body": (
                    "I (26F) have been with my fiancé, Liam (29M), for four years. Our wedding was scheduled for next month, "
                    "and we had already sent out invitations to two hundred guests. Last Thursday, Liam asked me to grab his gym bag "
                    "from the trunk of his car. While digging around the spare tire compartment, my hand brushed against a small black "
                    "velvet pouch. Inside was a fully charged burner phone with no passcode. When I opened the messages, my stomach dropped. "
                    "There were hundreds of text messages spanning six months between Liam and my maid of honor, Sophie. "
                    "They had been coordinating secret hotel meetups during his supposed late-night accounting shifts. "
                    "Liam had even sent her pictures of the honeymoon resort in Bali we had booked, telling her he wished he was taking her instead. "
                    "I took timestamped photos of every conversation, placed the phone back exactly as I found it, and walked inside. "
                    "Instead of screaming immediately, I waited for our rehearsal dinner two nights later with both of our families present. "
                    "When Liam stood up to give a toast thanking everyone, I tapped my champagne glass and asked to play a special slideshow. "
                    "I projected screenshots of their messages onto the banquet screen, handed Liam his engagement ring, and turned to Sophie. "
                    "I told everyone that since they were so passionate about our honeymoon, they could pay the vendor cancellation fees together. "
                    "The room erupted into total chaos. Liam was sobbing, Sophie tried to flee to the parking lot, and my dad escorted me out. "
                    "It broke my heart, but I dodged a lifetime of betrayal."
                ),
            },
            "male": {
                "title": "My girlfriend's parents demanded a fifty-thousand-dollar dowry, so I called off the proposal",
                "body": (
                    "I (28M) had been dating my girlfriend, Maya (27F), for three years. I bought a custom diamond ring and planned to propose "
                    "during our anniversary dinner. Last weekend, Maya's parents invited me to their house for what they called a customary pre-engagement blessing. "
                    "When I arrived, her father slid a notarized financial agreement across the mahogany table. "
                    "He informed me that in order to receive their blessing to marry Maya, I had to pay a fifty-thousand-dollar cash dowry to her family "
                    "and sign over fifty percent of the title on the house I purchased on my own before meeting her. "
                    "When I looked at Maya in shock, expecting her to be outraged, she simply looked down at her manicured nails and said, "
                    "'My parents raised me with high standards, and if you truly love me, you will prove your financial commitment to our family.' "
                    "I realized in that exact second that this was an orchestrated shakedown. Maya had never loved me for who I was; she and her parents "
                    "viewed me as an ATM. "
                    "I took a deep breath, slid the contract back across the table, and said, 'Congratulations, you just saved me fifty thousand dollars.' "
                    "I took the engagement ring out of my pocket, placed it back into my jacket, and walked out the front door. "
                    "Maya has been spamming my phone with crying voice notes claiming it was just a traditional test and that she never meant it. "
                    "Her parents sent angry emails threatening legal action for breach of promise. "
                    "I returned the ring for a full refund, blocked their numbers, and never looked back."
                ),
            },
        },
        "inheritance": {
            "female": {
                "title": "My parents secretly gave my late grandmother's house to my brother, so I called the county tax assessor",
                "body": (
                    "I (27F) was deeply close to my late grandmother, who passed away eighteen months ago. Before she passed, she repeatedly told our "
                    "family that her historic lakeside cottage was to be split equally between me and my older brother, Jason (30M). "
                    "My parents, who were appointed co-executors of the estate, assured me that probate was taking longer than expected due to legal backlogs. "
                    "Last weekend, I drove past the lake house and was shocked to see moving vans and custom remodeling crews outside. "
                    "Jason was standing on the porch, directing workers while drinking beer. When I confronted him, he grinned and said, "
                    "'Mom and Dad transferred the deed entirely to my name last month as an early wedding gift. You can visit on holidays if you RSVP.' "
                    "I immediately drove to the county courthouse and pulled the public property records. "
                    "To my horror, my parents had filed fraudulent estate conveyance documents, declaring Jason as the sole surviving heir while omitting my name. "
                    "Furthermore, to avoid paying estate transfer taxes, they had assessed the market value at fifty thousand dollars instead of its true "
                    "market appraisal of six hundred and fifty thousand dollars. "
                    "I didn't argue with my parents. Instead, I hired an estate litigation attorney and filed a formal audit request with the state revenue department. "
                    "The county tax assessor launched an immediate fraud investigation, placed an injunction on the property deed, and hit my parents with "
                    "seventy-five thousand dollars in tax penalties. Jason was forced to halt construction, and probate court has frozen all family accounts. "
                    "My mother called me weeping, accusing me of tearing the family apart over money. "
                    "I told her she was the one who stole from her own daughter."
                ),
            },
            "male": {
                "title": "My sister demanded my college trust fund after blowing her inheritance on luxury vacations",
                "body": (
                    "I (25M) was fortunate that our grandfather left me a fifty-thousand-dollar educational trust fund when he passed away, "
                    "while leaving my older sister, Ashley (28F), a separate inheritance of thirty thousand dollars in jewelry and cash. "
                    "By working two part-time jobs and earning scholarships, I graduated debt-free and kept thirty-five thousand dollars preserved for a home deposit. "
                    "Ashley, on the other hand, blew through her entire inheritance within eighteen months on luxury designer bags and expensive trips to Dubai. "
                    "Six months ago, Ashley got engaged and insisted on booking a lavish French castle venue with celebrity photographers. "
                    "When the invoices arrived sixty thousand dollars over budget, my parents called an urgent family meeting. "
                    "My father informed me that because grandfather's money was meant to benefit the entire family, I was expected to transfer thirty thousand "
                    "dollars from my savings to pay off Ashley's wedding vendors. "
                    "When I refused, Ashley threw a glass against the wall, calling me a selfish brother who cared more about bank numbers than her big day. "
                    "My mother gave me a forty-eight-hour ultimatum to wire the money or be uninvited from the wedding. "
                    "What none of them realized was that Ashley's wealthy fiancé had no idea about this scheme; Ashley had told him our parents were paying. "
                    "I emailed the spreadsheet directly to her fiancé with a polite note explaining I would not be liquidating my savings for their castle. "
                    "Her fiancé was completely mortified. He confronted Ashley that night, canceled the overseas venue, and postponed the wedding indefinitely. "
                    "Now my family calls me a monster, but I protected my life savings."
                ),
            },
        },
        "entitled": {
            "female": {
                "title": "My family went on vacation without me using my savings, so I canceled their villa",
                "body": (
                    "I (26F) have been planning our annual family trip to Hawaii for eight months. Every year, my parents and my sister, Jessica, "
                    "claim they are too stressed to organize anything, so the entire burden falls on me. Last year, Jessica convinced everyone to set up a shared "
                    "travel savings account under my bank profile, into which I contributed nearly three thousand dollars of my own bonus money. "
                    "Two days ago, I logged into my banking app to finalize the balance for the oceanfront beach house rental in Maui. "
                    "To my absolute horror, the balance was zeroed out. Jessica had used her card access to withdraw all four thousand five hundred dollars in cash. "
                    "I rushed to my parents' house, only to find it empty. On the counter was an itinerary for three tickets to Honolulu: for my mom, dad, and Jessica. "
                    "My name was nowhere on the booking. Taped next to the tickets was a note: 'Decided we needed an intimate family getaway without your micromanaging. "
                    "Thanks for reserving the villa!' "
                    "They had stolen my money, boarded a red-eye flight, and expected to check into the luxury villa booked exclusively under my name and credit card. "
                    "I called the villa owner immediately, explained the fraud, and canceled the reservation for a full refund credited back to my personal card. "
                    "Then, I reported the unauthorized cash withdrawal to my bank and froze the account. "
                    "Ten hours later, my father called screaming that they were stranded on the curb in the pouring rain with their luggage, locked out of the villa. "
                    "Jessica sent frantic messages demanding emergency hotel money because every resort was fully booked for peak season. "
                    "I texted back a screenshot of the fraud report, switched my phone to Do Not Disturb, and slept peacefully."
                ),
            },
            "male": {
                "title": "My entitled roommate stole my car for a date, so I had it towed and reported it",
                "body": (
                    "I (27M) bought my dream car last summer: a restored classic coupe that I spent four years saving for. "
                    "Three months ago, my college buddy Dave moved into my spare bedroom while searching for work. From day one, Dave felt entitled to everything, "
                    "from my groceries to my clothes, but the one non-negotiable rule was that he was never, under any circumstances, to touch my car keys. "
                    "Last Friday, I heard Dave bragging on speakerphone to a date about the vintage ride he was going to pick her up in. "
                    "Thirty minutes later, when I walked down to the garage, my parking spot was empty. "
                    "Dave had sneaked into my bedroom while I was showering, grabbed the spare key from my dresser, and driven off. "
                    "When I called him, loud club music was blaring. He yelled, 'Chill out bro, I'm just borrowing it to look good for a couple hours,' and hung up. "
                    "My car has a hidden GPS tracking tag in the trunk. I opened the app and saw he was parked outside an exclusive VIP lounge downtown "
                    "in a red curb zone right in front of a fire hydrant. "
                    "Instead of begging him for my keys, I called city parking enforcement and local impound directly, gave them the exact coordinates, "
                    "and requested an immediate unauthorized-driver tow. "
                    "Within twenty-five minutes, the flatbed tow truck hauled my car away into a secured impound facility. "
                    "At 1 AM, Dave called in hysterics. His date had walked out to an empty street, left him in humiliation, and the impound fee was seven hundred dollars. "
                    "When he finally got home in the morning, his bags were waiting on the porch."
                ),
            },
        },
        "aitah": {
            "female": {
                "title": "AITA for walking out of my sister's wedding after seeing the seating chart?",
                "body": (
                    "I (27F) have always had a rocky relationship with my older sister, Chloe. Growing up, our parents treated her like royalty while I was "
                    "expected to blend into the wallpaper. When Chloe announced her engagement last year, I genuinely wanted to put the past behind us. "
                    "I spent months helping her organize floral arrangements, drove six hours to pick up her custom veil, and contributed fifteen hundred "
                    "dollars toward her catering company when she went over budget. "
                    "Fast forward to the wedding day yesterday. The ceremony was gorgeous, but as everyone walked into the grand ballroom for the reception, "
                    "people started crowding around the master seating display. I searched for my name at Table 1 alongside our parents and grandparents, but I was not there. "
                    "I checked Table 2 with the bridal party. Nothing. "
                    "Finally, my cousin tapped my shoulder with a look of pure second-hand embarrassment and pointed towards the far back corner. "
                    "Tucked away right behind the industrial kitchen swinging doors, next to the emergency fire extinguisher, was a small fold-out table labeled 'Vendors and Extra Guests.' "
                    "Sitting right there was my name card. Sitting at the head table with our immediate family was Chloe's ex-boyfriend, whom she had invited as an honored guest. "
                    "When I confronted Chloe in the dressing room, she laughed and said, 'Space was tight, and you are family, so you understand.' "
                    "I took a deep breath, handed her back my corsage, and walked straight out to my car. "
                    "Now my phone is blowing up with family calling me selfish for causing a scene. Am I the asshole?"
                ),
            },
            "male": {
                "title": "AITA for locking my brother out of my apartment after finding what he did in my kitchen?",
                "body": (
                    "I (29M) bought my first two-bedroom condo six months ago after saving aggressively for seven straight years. "
                    "My younger brother, Mark (25M), recently graduated and needed a temporary place to stay while interviewing for jobs. "
                    "I told him he could stay in my spare room rent-free for three weeks under two clear conditions: keep the shared spaces clean, "
                    "and never throw parties while I was working seventy-hour hospital night shifts. "
                    "Last night, I returned home at 7 AM completely exhausted after back-to-back emergency rotations. The moment I opened my front door, "
                    "the smell of burnt sugar and scorched electronics hit me. I walked into my kitchen and found the entire marble countertop covered in scorched grease, "
                    "shattered glass from my blender, and multiple broken light fixtures. "
                    "Mark had invited nine of his college buddies over at 3 AM to shoot amateur viral food challenges for social media. "
                    "They had tried deep-frying whole candy bars in an open aluminum pot, which caught fire and scorched my custom cabinets. "
                    "Mark was passed out on the sofa surrounded by trash, completely indifferent. "
                    "I packed his bags immediately, set them outside the hallway door, changed the digital lock code, and went to sleep. "
                    "My mother called me in tears this afternoon, saying Mark is sleeping in his car and that brothers always forgive each other. "
                    "Am I the asshole for kicking him out?"
                ),
            },
        },
    }

    category_dict = story_database.get(cat_key, story_database["aitah"])
    chosen = category_dict[target_gender]

    cleaned_title = clean_story_title(chosen["title"])
    cleaned_body = clean_text_for_narration(chosen["body"])

    return Story(
        title=cleaned_title,
        body=cleaned_body,
        narrator_gender=target_gender,
        source="fallback",
        word_count=len(cleaned_body.split()),
        tags=["fallback", "redditstories", cat_key],
    )


def get_story(
    source: str = "auto",
    gender_override: Optional[str] = None,
    preferred_subreddit: Optional[str] = None,
    category: Optional[str] = None,
) -> Story:
    """Master entry point to retrieve or generate a story following the specified strategy.

    Supports diverse viral categories:
    - 'love': Love & Relationship drama
    - 'inheritance': Family inheritance & estate battles
    - 'entitled': Entitled people & roommate karma
    - 'aitah': Classic AITAH moral conflicts

    Args:
        source: 'auto', 'reddit', or 'gemini'.
        gender_override: force 'male' or 'female' narrator.
        preferred_subreddit: specific subreddit name to query.
        category: category key or 'auto'.
    """
    # 1. Determine Story Category
    if category and category != "auto" and category in STORY_CATEGORIES:
        selected_cat = category
    elif config.story_category and config.story_category != "auto" and config.story_category in STORY_CATEGORIES:
        selected_cat = config.story_category
    else:
        # Rotate dynamically across all diverse categories
        selected_cat = random.choice(list(STORY_CATEGORIES.keys()))

    cat_info = STORY_CATEGORIES[selected_cat]
    logger.info("Selected story category: '%s' (%s)", selected_cat, cat_info["name"])

    # 2. Prioritize Google Gemini if explicitly requested OR in auto mode when API key is provided
    # This guarantees fresh, category-specific stories without getting blocked by Reddit IP challenges
    if source == "gemini" or (source == "auto" and config.gemini_api_key):
        logger.info("Generating %s story using Google Gemini API...", selected_cat)
        gemini_story = generate_with_gemini(gender_override=gender_override, category=selected_cat)
        if gemini_story:
            return gemini_story
        logger.warning("Gemini generation failed; falling back to Reddit ingestion.")

    # 3. Live Reddit Ingestion for subreddits matching the chosen category
    subreddits = [preferred_subreddit] if preferred_subreddit else cat_info["subreddits"]
    for sub in subreddits:
        # Try PRAW if credentials exist
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

    # 4. Autonomous 270,000+ Reddit Dataset filtered by category keywords
    story = fetch_from_huggingface_reddit(category=selected_cat)
    if story:
        if gender_override:
            story.narrator_gender = gender_override
        return story

    # 5. Gemini fallback if configured and not yet tried
    if config.gemini_api_key:
        logger.info("Attempting Gemini generation as fallback...")
        gemini_story = generate_with_gemini(gender_override=gender_override, category=selected_cat)
        if gemini_story:
            return gemini_story

    # 6. Curated Fallback Story in the requested category
    logger.info("Using curated fallback story for category '%s'", selected_cat)
    return get_curated_fallback_story(gender_override=gender_override, category=selected_cat)

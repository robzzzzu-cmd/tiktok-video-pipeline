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
    """Cleans raw Reddit titles into natural viral story hooks for TikTok narration."""
    if not title:
        return ""

    # Remove reddit prefix tags: [UPDATE], Update:, (Update), [Repost], [AITA], [TW]
    title = re.sub(r"^\[(?:UPDATE|Repost|TW|Trigger Warning|AITA|WIBTA)\]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:UPDATE|Update):\s*", "", title)

    # Clean AITA / WIBTA phrasing into natural viral storytime hooks
    title = re.sub(r"^(?:AITA|Am I the asshole)\s+(?:for|if I|because I)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:WIBTA|Would I be the asshole)\s+(?:for|if I|because I)\s+", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:AITA|WIBTA|TIFU)\s*[:\-–]\s*", "", title, flags=re.IGNORECASE)

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


def fetch_from_huggingface_reddit() -> Optional[Story]:
    """Fetches authentic viral Reddit stories from the 270,000+ public dataset.

    Requires NO Reddit API keys, NO secrets, and NO authentication.
    """
    total_entries = 270000
    random_offset = random.randint(100, total_entries - 20)
    url = (
        f"https://datasets-server.huggingface.co/rows?"
        f"dataset=OsamaBsher/AITA-Reddit-Dataset&config=default&split=train&"
        f"offset={random_offset}&length=10"
    )

    try:
        logger.info("Fetching real viral Reddit stories from 270k+ public database (offset %d)...", random_offset)
        response = requests.get(url, headers={"User-Agent": config.reddit_user_agent}, timeout=15)
        if response.status_code != 200:
            logger.warning("HuggingFace dataset endpoint returned HTTP %d", response.status_code)
            return None

        data = response.json()
        rows = data.get("rows", [])

        for item in rows:
            row = item.get("row", {})
            selftext = row.get("text", "")
            raw_title = row.get("title", "")
            score = row.get("score", 0)

            if not selftext or len(selftext) < 400:
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

            logger.info("Retrieved authentic Reddit story: '%s' (score: %s, %d words)", cleaned_title, score, final_words)
            return Story(
                title=cleaned_title,
                body=final_body,
                narrator_gender=gender,
                source="reddit_dataset",
                word_count=final_words,
                subreddit="AITAH",
                tags=["r/AITAH", "redditstories"],
            )

    except Exception as e:
        logger.warning("Failed to fetch from Reddit public dataset: %s", e)
        logger.warning("Failed to fetch from Reddit public dataset: %s", e)

    return None


VIRAL_DRAMA_PROMPTS = [
    {
        "category": "family_vacation_or_fund_betrayal",
        "concept": "My family secretly went on our annual vacation without me using my vacation savings / my sister took my vacation fund, so I took immediate action / canceled the luxury villa reservation in my name.",
    },
    {
        "category": "entitled_roommate_or_relative",
        "concept": "An entitled roommate or sibling taking the narrator's car or savings without permission to impress someone, leading to immediate towing, legal consequences, or being locked out.",
    },
    {
        "category": "petty_revenge_and_malicious_compliance",
        "concept": "A narrator using clever housing codes, legal contracts, or undeniable receipts to take satisfying petty revenge against an arrogant landlord or boss who tried to steal from them.",
    },
    {
        "category": "wedding_or_family_drama",
        "concept": "An entitled bride, sister, or mother-in-law demanding the narrator's savings or attempting to humiliate the narrator, resulting in instant public exposure and cancelled plans.",
    },
    {
        "category": "inheritance_and_favoritism",
        "concept": "Parents secretly handing family assets to the golden child who blew it all, then expecting the narrator to bail them out, only for the narrator to walk away and cut contact.",
    },
]


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
        theme = random.choice(VIRAL_DRAMA_PROMPTS)

        prompt = f"""
You are an award-winning creative storyteller specializing in viral, dramatic Reddit storytime scripts (in the style of r/TrueOffMyChest, r/EntitledPeople, r/pettyrevenge, and r/relationship_advice).

THEME: {theme["concept"]}

CRITICAL RULES:
1. TARGET LENGTH: Exactly 460 to 510 words (this will be spoken in a 3-minute video at ~160 WPM).
2. NARRATOR: The narrator is {prompt_gender.upper()}, tagged explicitly in the opening line as "I ({gender_tag})".
3. TONE & PACING:
   - High suspense with an intense conversational hook in sentence 1.
   - Escalating tension and drama.
   - Satisfying confrontation, karma, or twist ending.
4. NO AITA FORMAT: Do NOT say "Am I the asshole?" or frame this as an AITA post. This is a dramatic personal storytime / revenge tale.
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
            tags=["gemini", "ai_storytime", theme["category"]],
        )

    except Exception as e:
        logger.error("Gemini generation failed: %s", e)

    return None


def get_curated_fallback_story(gender_override: Optional[str] = None) -> Story:
    """Curated, high-retention fallback stories across diverse viral genres (vacation betrayal, roommate karma, petty revenge)."""
    target_gender = gender_override or random.choice(["male", "female"])

    female_stories = [
        {
            "title": "My family went on vacation without me using my savings, so I canceled their villa",
            "body": (
                "I (26F) have been planning our annual family trip to Hawaii for eight months. Every year, my parents "
                "and my older sister, Jessica, claim they are too stressed to organize anything, so the entire burden "
                "falls on me. Last year, Jessica convinced everyone to set up a shared travel savings account under my "
                "banking profile, into which I contributed nearly three thousand dollars of my own hard-earned bonus money. "
                "Two days ago, I logged into my banking app to finalize the balance for the oceanfront beach house rental "
                "I had reserved in Maui. To my absolute horror, the account balance was completely zeroed out. Jessica had "
                "used her secondary card access to withdraw all four thousand five hundred dollars in cash. "
                "I immediately drove over to our parents' house in a panic, thinking we had been hacked. When I walked through "
                "the front door, the house was completely empty. On the kitchen counter was a printed flight itinerary for "
                "three tickets to Honolulu: one for my mom, one for my dad, and one for Jessica. My name was nowhere on the booking. "
                "Taped next to the tickets was a sticky note from Jessica: 'Decided we needed a real intimate immediate family getaway "
                "this year without your micromanaging. Thanks for taking care of the villa reservation!' "
                "My hands were shaking with pure disbelief and fury. They had literally stolen three thousand dollars of my money, "
                "boarded a red-eye flight, and expected to check into the luxury four-bedroom villa that was booked exclusively "
                "under my name and credit card. "
                "I sat at the kitchen counter, took a slow, deep breath, and called the villa property manager directly. "
                "I explained that an emergency had occurred and requested an immediate cancellation with a full refund credited "
                "directly to my personal card. Because the owner knew me from previous bookings, they waived the late fee and refunded "
                "the entire seven thousand dollar reservation balance to my account. "
                "Then, I called my bank's fraud department, reported the unauthorized cash withdrawal made by Jessica, and froze the shared account. "
                "Ten hours later, my phone began ringing uncontrollably. It was 11 PM in Hawaii. My father was screaming into the voicemail "
                "that they were stranded on the curb outside the locked gates in the pouring rain with all their luggage, and the security guard "
                "refused to let them in because there was no active reservation under our family name. "
                "Jessica sent twenty furious text messages demanding that I send them money for an emergency hotel because every resort on "
                "the island was fully booked for peak season. "
                "I texted back a single screenshot of the fraud report and the sticky note she left on the counter, then switched my phone to "
                "Do Not Disturb and slept like a baby."
            ),
        },
        {
            "title": "My sister demanded my college savings for her dream wedding, so I exposed her lies",
            "body": (
                "I (25F) was fortunate that our grandfather left me a fifty-thousand-dollar educational trust fund when he passed away, "
                "while leaving my older sister, Ashley, a separate inheritance of thirty thousand dollars in cash. By working two part-time "
                "jobs and earning academic scholarships, I managed to graduate debt-free and preserved thirty-five thousand dollars for my future home. "
                "Ashley, on the other hand, blew through her entire inheritance within eighteen months on luxury designer bags and expensive vacations. "
                "Six months ago, Ashley got engaged to a wealthy corporate lawyer named Daniel. Instead of planning a sensible wedding, "
                "Ashley insisted on booking a lavish castle venue in France, ordering custom designer dresses, and hiring celebrity caterers. "
                "When the invoices arrived, Ashley was sixty thousand dollars over budget. "
                "Last Sunday, my parents invited me over for what they called an urgent family council. When I arrived, Ashley was sitting at "
                "the table with a printed spreadsheet. My father cleared his throat and told me that because grandfather's money was meant to benefit "
                "the family, they expected me to transfer thirty thousand dollars from my savings to pay off Ashley's wedding vendors. "
                "When I refused, Ashley slammed her fists on the table, calling me a selfish sister who cared more about numbers in a bank account "
                "than her happiness. My mother chimed in, saying Daniel's family would look down on us if the wedding was cheap. "
                "They gave me a forty-eight-hour ultimatum to transfer the funds or be cut off from the family. "
                "What none of them realized was that Daniel had no idea about this scheme. Ashley had told him that her parents were paying their share. "
                "The next morning, I sent an email directly to Daniel, attaching the spreadsheet Ashley gave me along with a polite note explaining that "
                "I would not be liquidating my savings to fund their French castle. "
                "Daniel was completely horrified. It turned out Ashley had lied to him about everything. He confronted her that evening, canceled the "
                "overseas venue, and postponed the entire wedding indefinitely. "
                "Now my family calls me a villain, but I protected my life savings."
            ),
        },
        {
            "title": "Walking out of my sister's wedding after seeing the seating chart",
            "body": (
                "I (27F) have always had a rocky relationship with my older sister, Chloe. Growing up, our parents treated her like royalty "
                "while I was expected to blend into the wallpaper. When Chloe announced her engagement last year, I genuinely wanted to put the past "
                "behind us. I spent months helping her organize floral arrangements, drove six hours to pick up her custom veil, and contributed "
                "fifteen hundred dollars toward her dream catering company when she went over budget. "
                "Fast forward to the wedding day yesterday. The ceremony was gorgeous, but as everyone walked into the grand ballroom for the reception, "
                "people started crowding around the master seating display. I searched for my name at Table 1 alongside our parents and grandparents, "
                "but I was not there. I checked Table 2 with the bridal party. Nothing. "
                "Finally, my cousin tapped my shoulder with a look of pure second-hand embarrassment and pointed towards the far back corner. "
                "Tucked away right behind the industrial kitchen swinging doors, next to the emergency fire extinguisher, was a small fold-out table "
                "labeled 'Vendors and Extra Guests.' Sitting right there was my name card. "
                "Sitting at the head table with our immediate family was Chloe's ex-boyfriend, whom she had invited as an honored guest. "
                "When I confronted Chloe in the dressing room, she laughed and said, 'Space was tight, and you are family, so you understand.' "
                "I took a deep breath, handed her back my corsage, and walked straight out to my car. "
                "Now my phone is blowing up with family calling me selfish for causing a scene, but I have zero regrets."
            ),
        },
    ]

    male_stories = [
        {
            "title": "My entitled roommate stole my car for a date, so I had it towed and reported it",
            "body": (
                "I (27M) bought my dream car last summer: a meticulously restored midnight blue classic coupe that I spent four years saving for. "
                "Three months ago, my college buddy Dave moved into my spare bedroom while searching for work. From day one, Dave felt entitled to "
                "everything in my apartment, from my groceries to my clothes, but the one non-negotiable rule I made crystal clear was that he was "
                "never, under any circumstances, to touch my car keys. "
                "Last Friday, I was working late on a deadline at my home office desk. Around 8 PM, I heard Dave bragging on speakerphone to some girl "
                "about the vintage ride he was going to pick her up in for their first date. Thirty minutes later, when I walked downstairs to the garage, "
                "my parking spot was completely empty. "
                "My spare key was gone from my dresser drawer. Dave had sneaked into my bedroom while I was in the shower and driven off to impress his date. "
                "I called his phone immediately. When he finally answered, loud club music was blaring in the background. He yelled, 'Chill out bro, I'm just "
                "borrowing it for a couple hours to look good. Don't be a buzzkill,' and hung up. "
                "My car has a hidden GPS tracking tag inside the trunk. I opened the app and saw that he was parked outside an exclusive VIP lounge downtown "
                "in a commercial red curb zone right in front of a fire hydrant. "
                "Instead of driving down there to beg him for my keys, I called the city parking enforcement and the local impound company directly. "
                "I gave them the exact coordinates, explained an unauthorized driver took the vehicle without permission, and requested an immediate tow. "
                "Within twenty-five minutes, the flatbed tow truck arrived and hauled my car away into a secured impound facility. "
                "Around 1 AM, Dave finally called me back in total hysterics. He and his date had walked out of the club to find the street empty. Dave had "
                "no money for an Uber, his date left him stranded on the sidewalk in embarrassment, and the impound fee was seven hundred and fifty dollars. "
                "When he finally got home in the morning, his bags were waiting on the porch."
            ),
        },
        {
            "title": "My brother trashed my new condo for a social media stunt, so I changed the locks",
            "body": (
                "I (29M) bought my first two-bedroom condo six months ago after saving aggressively for seven straight years. "
                "My younger brother, Mark (25M), recently graduated and needed a temporary place to stay while interviewing for jobs. "
                "I told him he could stay in my spare room rent-free for three weeks under two clear conditions: keep the shared spaces clean, "
                "and never throw parties while I was working seventy-hour hospital night shifts. "
                "Last night, I returned home at 7 AM completely exhausted after back-to-back emergency rotations. The moment I opened my front door, "
                "the smell of burnt sugar and scorched electronics hit me. I walked into my kitchen and found the entire marble countertop covered "
                "in scorched grease, shattered glass from my blender, and multiple broken light fixtures. "
                "Mark had invited nine of his college buddies over at 3 AM to shoot amateur viral food challenges for social media. "
                "They had tried deep-frying whole candy bars in an open aluminum pot, which caught fire and scorched my custom cabinets. "
                "Mark was passed out on the sofa surrounded by trash, completely indifferent. "
                "I packed his bags immediately, set them outside the hallway door, changed the digital lock code, and went to sleep. "
                "My mother called me in tears this afternoon, saying Mark is sleeping in his car and that brothers always forgive each other. "
                "I told her that respect is earned, and Mark can find another hotel."
            ),
        },
        {
            "title": "My landlord kept my three thousand dollar deposit, so I cost him twenty thousand",
            "body": (
                "I (28M) rented a two-bedroom apartment from an arrogant private landlord named Richard for three straight years. "
                "During my entire tenancy, I paid my rent five days early every month and took immaculate care of the place. "
                "When I bought my first house and gave my standard sixty-day notice to vacate, I spent an entire weekend scrubbing every baseboard, "
                "deep-cleaning the carpets, and taking over two hundred timestamped photos documenting the pristine condition of the apartment. "
                "Two weeks later, Richard sent me an itemized letter claiming my entire three thousand dollar security deposit plus an additional "
                "eight hundred dollars for routine painting and normal carpet wear. When I offered to show him my timestamped move-out video, "
                "he laughed and told me good luck fighting it in court because judges don't care about tenants. "
                "That arrogant remark was his biggest mistake. "
                "I researched our municipal housing codes and discovered that in our city, landlords who withhold deposits in bad faith are liable "
                "for triple damages. Furthermore, Richard had never registered the basement unit as a legal rental suite. "
                "I hired a tenant attorney on contingency and filed a formal lawsuit for nine thousand dollars while submitting an official housing "
                "inspection request. The city inspector cited Richard for nine major building violations, issued six thousand dollars in fines, and ordered "
                "him to halt renting the basement. "
                "Richard's attorney begged for an immediate settlement. Richard was forced to write me a cashier's check for nine thousand dollars, while "
                "the repairs cost him over twenty thousand. Never let an arrogant bully take advantage of you."
            ),
        },
    ]

    selected_list = female_stories if target_gender == "female" else male_stories
    chosen = random.choice(selected_list)

    cleaned_title = clean_story_title(chosen["title"])
    cleaned_body = clean_text_for_narration(chosen["body"])

    return Story(
        title=cleaned_title,
        body=cleaned_body,
        narrator_gender=target_gender,
        source="fallback",
        word_count=len(cleaned_body.split()),
        tags=["fallback", "redditstories", "viral_drama"],
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

    # 3. Autonomous Reddit Dataset (270,000+ real posts, requires NO API keys or credentials)
    story = fetch_from_huggingface_reddit()
    if story:
        if gender_override:
            story.narrator_gender = gender_override
        return story

    # 4. Gemini fallback if configured
    if source in ("auto", "reddit") and config.gemini_api_key:
        logger.info("Attempting Gemini generation as fallback...")
        gemini_story = generate_with_gemini(gender_override)
        if gemini_story:
            return gemini_story

    # 5. Reliable built-in curated story
    logger.warning("Using curated fallback story to maintain 100% pipeline uptime.")
    return get_curated_fallback_story(gender_override)

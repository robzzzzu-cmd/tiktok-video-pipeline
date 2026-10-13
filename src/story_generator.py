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
    r"\bTIL\b": "today I learned",
    r"\bELI5\b": "explain like I am five",
}


def clean_text_for_narration(text: str, category: str = "auto") -> str:
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
        if category in ("love", "inheritance", "entitled", "revenge", "confession", "workplace") and pattern in (r"\bAITA\b", r"\bWIBTA\b"):
            text = re.sub(pattern, "in the wrong", text, flags=re.IGNORECASE)
        elif category in ("love", "inheritance", "entitled", "revenge", "confession", "workplace") and pattern == r"\bNTA\b":
            text = re.sub(pattern, "in the right", text, flags=re.IGNORECASE)
        elif category in ("love", "inheritance", "entitled", "revenge", "confession", "workplace") and pattern in (r"\bYTA\b", r"\bESH\b"):
            text = re.sub(pattern, "in the wrong", text, flags=re.IGNORECASE)
        else:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # Remove trailing forum boilerplate endings so the video concludes on the dramatic punchline
    text = re.sub(
        r"\s*(?:Am I the asshole\??|AITA\??|Would I be the asshole\??|So,\s*Reddit,\s*am I in the wrong\??|What do you guys think\??|What should I do\??)\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )

    if category in ("love", "inheritance", "entitled", "revenge", "confession", "workplace"):
        # Remove any lingering "Am I the asshole" phrases anywhere in the narrative
        text = re.sub(r"\b(?:Am I the asshole|would I be the asshole)\b\??", "am I in the wrong", text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:an asshole|the asshole)\b", "in the wrong", text, flags=re.IGNORECASE)

    # Normalize multiple newlines and spaces
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def clean_story_title(title: str, category: str = "auto") -> str:
    """Cleans raw Reddit titles into natural viral story hooks for TikTok narration.

    Preserves AITA prefixes when category is 'aitah'.
    Strips AITA/TIFU/TIL prefixes when category is not 'aitah'.
    """
    if not title:
        return ""

    # Remove reddit prefix tags: [UPDATE], Update:, (Update), [Repost], [TW], [Trigger Warning]
    title = re.sub(r"^\[(?:UPDATE|Repost|TW|Trigger Warning)\]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^(?:UPDATE|Update):\s*", "", title)
    title = re.sub(r"^(?:Cross-posted|Crosspost|X-post)[:\-–]\s*", "", title, flags=re.IGNORECASE)

    # If the story is explicitly in a non-AITAH category, strip any leading AITA tags
    if category != "aitah":
        title = re.sub(
            r"^(?:AITA|WIBTA|Am I the [aA]sshole|Would I be the [aA]sshole)\s+(?:for|if|to|because)?\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"^(?:TIFU\s+by|TIFU\s+for|TIFU)[:\s]*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"^(?:TIL\s+that|TIL|Today I learned\s+that|Today I learned)[:\s]*",
            "",
            title,
            flags=re.IGNORECASE,
        )

    title = title.strip()
    if title:
        title = title[0].upper() + title[1:]

    return clean_text_for_narration(title, category=category)


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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.reddit.com/",
    }

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
        "subreddits": ["relationship_advice", "dating_advice", "confession", "TrueOffMyChest", "survivinginfidelity"],
        "keywords": [
            "husband", "wife", "boyfriend", "girlfriend", "fiancé", "fiancée", "cheating",
            "dating", "marriage", "wedding", "in love", "proposal", "burner phone", "gym bag",
            "private investigator", "dowry", "rehearsal dinner", "retreat", "affair", "concierge"
        ],
        "gemini_theme": "A high-stakes love, dating, or relationship story: uncovering a secret about a partner (burner phone in gym bag, private investigator uncovering secret lifestyle, surprise dowry demand at rehearsal dinner, or wedding betrayal).",
    },
    "inheritance": {
        "name": "Family Inheritance & Wills",
        "subreddits": ["familydrama", "EntitledPeople", "TrueOffMyChest", "pettyrevenge", "legaladvice"],
        "keywords": [
            "inheritance", "will", "estate", "passed away", "trust fund", "house", "parents left",
            "property", "heir", "executor", "vacation fund", "sister's kids", "land", "cottage",
            "tax assessor", "deed", "probate", "selling land"
        ],
        "gemini_theme": "A dramatic family inheritance battle: family vacation fund theft ($8,000 sister demand and father selling land), college trust fund drama, grandmother's estate tax dispute, or greedy relatives demanding the narrator's inheritance.",
    },
    "entitled": {
        "name": "Entitled People & Roommate Karma",
        "subreddits": ["EntitledPeople", "pettyrevenge", "tifu", "TrueOffMyChest", "MaliciousCompliance"],
        "keywords": [
            "entitled", "roommate", "car", "towed", "stole", "police", "boss", "landlord",
            "contractor", "proprietary", "source code", "logistics", "deposit", "villa", "neighbor",
            "code enforcement", "operations froze"
        ],
        "gemini_theme": "An entitled person karma story: independent software contractor fired after refusing to hand over proprietary logistics source code (distribution network freeze), entitled neighbor towing classic car, roommate stealing a car, or landlord keeping a deposit.",
    },
    "revenge": {
        "name": "Sweet & Petty Revenge",
        "subreddits": ["pettyrevenge", "ProRevenge", "MaliciousCompliance", "tifu"],
        "keywords": [
            "revenge", "karma", "malicious compliance", "fired", "audit", "payback",
            "landlord", "tenant", "boss", "demolish", "property line", "survey", "commission"
        ],
        "gemini_theme": "A satisfying revenge or malicious compliance story: corporate boss attempting to steal commission getting caught by an audit, or neighbor property dispute resulting in demolishing an encroachment.",
    },
    "confession": {
        "name": "Shocking Confessions & Secrets",
        "subreddits": ["confession", "TrueOffMyChest", "offmychest"],
        "keywords": [
            "confession", "secret", "never told", "guilt", "hidden", "dna",
            "discovered", "truth", "swapped", "whistleblower", "chemicals", "culvert"
        ],
        "gemini_theme": "A shocking confession or dark secret: commercial DNA test revealing being switched at birth, or a safety inspector blowing the whistle on illegal toxic dumping.",
    },
    "aitah": {
        "name": "Classic Am I The Asshole",
        "subreddits": ["AITAH", "AmItheAsshole"],
        "keywords": ["aita", "asshole", "am i the asshole", "wibta"],
        "gemini_theme": "A classic moral dilemma where the narrator made a controversial but justified decision, dividing their family, starting with 'AITA for...'.",
    },
    "facts": {
        "name": "Mind-Blowing Facts & Disturbing Curiosities",
        "subreddits": ["todayilearned", "damnthatsinteresting", "AskReddit", "oddlyterrifying"],
        "keywords": [
            "fact", "learned", "science", "ocean", "space", "brain", "body", "historical",
            "psychological", "creepy", "discovered", "theory", "phenomenon", "ancient", "mystery"
        ],
        "gemini_theme": "A rapid-fire countdown of 4 to 5 mind-blowing, unsettling, or unbelievable real-world facts (deep ocean mysteries, disturbing psychology, bizarre medical anomalies, or surreal space phenomena) that leave the viewer shocked. Fast-paced, intense hooks, conversational delivery.",
    },
    "scary": {
        "name": "True Horror, Paranormal & Glitch in the Matrix",
        "subreddits": ["nosleep", "Glitch_in_the_Matrix", "LetsNotMeet", "scarystories", "Ghoststories"],
        "keywords": [
            "creepy", "scary", "woods", "cabin", "night", "stalker", "glitch", "mimic",
            "footsteps", "unexplained", "watcher", "window", "shadow", "voice", "horror"
        ],
        "gemini_theme": "A spine-chilling, atmospheric true-style horror or glitch-in-the-matrix encounter: working the graveyard night shift, living isolated in a cabin hearing one's own voice outside, or an impossible glitch in reality that cannot be rationalized. Escalating dread with a lingering terrifying ending.",
    },
    "showerthoughts": {
        "name": "Mind-Bending Thoughts & Deep Epiphanies",
        "subreddits": ["Showerthoughts", "AskReddit", "Existentialism"],
        "keywords": [
            "thought", "realize", "perspective", "brain", "time", "humanity", "simulation",
            "universe", "existential", "conscious", "paradox", "reality"
        ],
        "gemini_theme": "A series of 4 mind-bending shower thoughts, philosophical paradoxes, and perspective-shifting epiphanies that break the viewer's brain and make them question everyday life and reality.",
    },
    "wild_history": {
        "name": "Unbelievable Historical Events & Survival Stories",
        "subreddits": ["HistoryAnecdotes", "TrueCrime", "UnresolvedMysteries", "nonononoyes"],
        "keywords": [
            "history", "survived", "heist", "impossible", "miracle", "war", "island",
            "coincidence", "pilot", "escape", "legend", "century", "expedition"
        ],
        "gemini_theme": "An insane, unbelievable true historical survival story or audacious heist that sounds like fiction but actually happened (e.g. an impossible wilderness survival miracle, wild military coincidence, or genius trickery).",
    },
    "workplace": {
        "name": "Unhinged Customers & Workplace Chaos",
        "subreddits": ["TalesFromRetail", "IDontWorkHereLady", "talesfromtechsupport", "pettyrevenge"],
        "keywords": [
            "manager", "customer", "karen", "store", "cashier", "fired", "demanded",
            "police", "shift", "retail", "refund", "receipt", "corporate"
        ],
        "gemini_theme": "A wildly entertaining workplace or retail encounter where an unhinged, entitled customer tries to get an employee fired, only to face instant public embarrassment or police karma.",
    },
}


def fetch_from_huggingface_reddit(category: str = "auto") -> Optional[Story]:
    """Fetches authentic viral Reddit stories across multi-million public datasets.

    Searches across:
    - SocialGrep/one-million-reddit-confessions (1,000,000 authentic confession and relationship posts)
    - OsamaBsher/AITA-Reddit-Dataset (270,000+ authentic Reddit drama, inheritance, entitled, and moral stories)
    Filters by category keywords and sanitizes text for seamless storytime narration.
    """
    cat_key = category if category in STORY_CATEGORIES else random.choice(list(STORY_CATEGORIES.keys()))
    cat_info = STORY_CATEGORIES[cat_key]
    keywords = cat_info["keywords"]

    # Choose primary and secondary dataset targets based on the category
    dataset_targets = []
    if cat_key in ("love", "confession"):
        dataset_targets.append(("SocialGrep/one-million-reddit-confessions", 1000000, "selftext", "title"))
        dataset_targets.append(("OsamaBsher/AITA-Reddit-Dataset", 270000, "text", "title"))
    elif cat_key == "aitah":
        dataset_targets.append(("OsamaBsher/AITA-Reddit-Dataset", 270000, "text", "title"))
    else:  # inheritance, entitled, revenge
        dataset_targets.append(("OsamaBsher/AITA-Reddit-Dataset", 270000, "text", "title"))
        dataset_targets.append(("SocialGrep/one-million-reddit-confessions", 1000000, "selftext", "title"))

    for ds_name, total_entries, text_field, title_field in dataset_targets:
        for _ in range(4):
            random_offset = random.randint(100, max(150, total_entries - 120))
            url = (
                f"https://datasets-server.huggingface.co/rows?"
                f"dataset={ds_name}&config=default&split=train&"
                f"offset={random_offset}&length=50"
            )

            try:
                logger.info(
                    "Searching %s for '%s' stories (offset %d)...",
                    ds_name,
                    cat_info["name"],
                    random_offset,
                )
                response = requests.get(url, headers={"User-Agent": config.reddit_user_agent}, timeout=15)
                if response.status_code != 200:
                    continue

                data = response.json()
                rows = data.get("rows", [])

                for item in rows:
                    row = item.get("row", {})
                    selftext = row.get(text_field, "")
                    raw_title = row.get(title_field, "")
                    score = row.get("score", 0)

                    if not selftext or len(selftext) < 400:
                        continue

                    combined_lower = f"{raw_title.lower()} {selftext[:1500].lower()}"

                    # Match keywords if not generic aitah
                    if cat_key != "aitah":
                        if not any(kw.lower() in combined_lower for kw in keywords):
                            continue

                    cleaned_body = clean_text_for_narration(selftext, category=cat_key)
                    words = cleaned_body.split()
                    if len(words) < config.target_words_min:
                        continue

                    final_body = trim_to_target_words(
                        cleaned_body, config.target_words_min, config.target_words_max
                    )
                    final_words = len(final_body.split())
                    cleaned_title = clean_story_title(raw_title, category=cat_key)
                    gender = detect_narrator_gender(final_body, cleaned_title)

                    logger.info(
                        "Retrieved authentic Reddit %s story: '%s' (score: %s, %d words, gender: %s)",
                        cat_key,
                        cleaned_title,
                        score,
                        final_words,
                        gender,
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
                logger.warning("Failed to fetch from %s: %s", ds_name, e)

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

        if cat_key == "facts":
            narrator_rule = f"The voice narrator is {prompt_gender.upper()} delivering a rapid-fire, highly engaging curiosity presentation to the viewer."
            format_instruction = "NARRATIVE FORMAT: Title should be a high-engagement curiosity hook (e.g. 'Disturbing psychological facts that will keep you awake tonight'). Body should deliver 4 captivating facts conversationally, building in suspense."
        elif cat_key == "showerthoughts":
            narrator_rule = f"The voice narrator is {prompt_gender.upper()} presenting mind-bending concepts directly to the viewer."
            format_instruction = "NARRATIVE FORMAT: Title should be a brain-breaking epiphany hook (e.g. 'Shower thoughts that break your brain'). Body should deliver 4 deep, perspective-shifting epiphanies."
        elif cat_key == "scary":
            narrator_rule = f"The narrator is {prompt_gender.upper()}, tagged in opening line as 'I ({gender_tag})' or 'I [{gender_tag}]'."
            format_instruction = "NARRATIVE FORMAT: Title should be a chilling encounter hook. Body should be an atmospheric true horror encounter with escalating dread and a terrifying conclusion."
        elif cat_key == "wild_history":
            narrator_rule = f"The narrator is {prompt_gender.upper()} recounting an unbelievable true historical event or survival tale."
            format_instruction = "NARRATIVE FORMAT: Title should be an unbelievable history hook. Body should tell an extraordinary true survival or historical feat."
        elif cat_key == "workplace":
            narrator_rule = f"The narrator is {prompt_gender.upper()}, tagged in opening line as 'I ({gender_tag})' or 'I [{gender_tag}]'."
            format_instruction = "NARRATIVE FORMAT: Title should be an unhinged customer/manager hook. Body should describe an outrageous workplace encounter with epic karma."
        elif cat_key == "aitah":
            narrator_rule = f"The narrator is {prompt_gender.upper()}, tagged explicitly in the opening line as 'I ({gender_tag})' or 'I [{gender_tag}]'."
            format_instruction = "NARRATIVE FORMAT: Title should start with 'AITA for...' or 'Am I the asshole for...'"
        else:
            narrator_rule = f"The narrator is {prompt_gender.upper()}, tagged explicitly in the opening line as 'I ({gender_tag})' or 'I [{gender_tag}]'."
            format_instruction = "NARRATIVE FORMAT: DO NOT use 'Am I the asshole?' or AITA format. Make it a gripping personal storytime."

        prompt = f"""
You are an award-winning creative storyteller specializing in viral TikTok scripts ({cat_info['name']}).

THEME: {cat_info['gemini_theme']}

CRITICAL RULES:
1. TARGET LENGTH: Exactly 460 to 510 words (this will be spoken in a 3-minute video at ~160 WPM).
2. NARRATOR: {narrator_rule}
3. TONE & PACING:
   - High suspense with an intense conversational hook in sentence 1.
   - Escalating tension and interest.
   - Satisfying conclusion or punchline.
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
            "female": [
                {
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
                {
                    "title": "My husband of five years asked for an open marriage out of nowhere, so I hired a private investigator",
                    "body": (
                        "I (28F) had been married to my husband, Julian (30M), for five years. We owned a beautiful suburban home and talked about starting a family. "
                        "Two months ago, over casual Sunday morning coffee, Julian dropped an absolute bombshell. He looked at me with a rehearsed, calm expression "
                        "and suggested that we transition into an open marriage. He claimed that monogamy was outdated and that exploring other connections would "
                        "bring us closer together emotionally. My heart sank into my stomach. When someone brings up an open marriage completely unprovoked after years "
                        "of happy commitment, they almost certainly already have a specific person in mind. Instead of bursting into tears or screaming, I took a slow "
                        "sip of coffee, smiled faintly, and said, 'If that is what you truly need to feel fulfilled, I am open to exploring ground rules.' "
                        "His face lit up with arrogant relief. The very next morning, I visited a premier divorce attorney and retained a top-rated private investigator. "
                        "Within seventy-two hours, the investigator delivered a digital dossier that knocked the wind out of me. Julian had already been having an affair "
                        "for four months with his twenty-three-year-old marketing assistant, Chloe. He had leased a luxury downtown apartment under a corporate shell company "
                        "and had been taking her on weekend trips using his expense account. He only asked for an open marriage so he could openly bring her around our "
                        "circle of friends without feeling guilty. Equipped with bank statements, timestamped hotel photographs, and flight receipts, my attorney prepared "
                        "airtight fault-based divorce filings under our state's infidelity laws. Next Friday, Julian came home whistling, packing an overnight duffle bag "
                        "and cheerfully telling me he had a networking retreat scheduled for the weekend. As he reached for the front doorknob, I handed him a manila envelope. "
                        "He smiled, assuming it was a sweet note. When he slid out the legal papers and the high-resolution photos of him and Chloe at the airport, all color "
                        "drained from his face. His knees literally buckled against the entryway wall. I told him he had thirty minutes to take whatever fit into his duffle "
                        "bag and leave our house. Under our prenuptial agreement, documented infidelity forfeited his share of our home equity. Julian sobbed on the porch, "
                        "begging for marriage counseling and insisting Chloe meant nothing to him. I closed the door, locked the deadbolt, and changed the security codes. "
                        "Three months later, our divorce was finalized. I kept the house, bought out his remaining pension share, and reclaimed my self-worth."
                    ),
                },
            ],
            "male": [
                {
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
                {
                    "title": "My girlfriend told me she was on a quiet girls' retreat, but the hotel concierge called my phone by mistake",
                    "body": (
                        "I (28M) had been dating my girlfriend, Clara (27F), for nearly three years. We had just started browsing real estate listings together. "
                        "Last month, Clara told me that she and three of her closest college girlfriends were taking a four-day girls' wellness retreat to Aspen, Colorado, "
                        "to celebrate her friend's promotion. I drove her to the airport, paid for her round-trip tickets, and told her to enjoy a relaxing, uninterrupted weekend. "
                        "On Saturday evening, while I was cooking dinner at home, my cell phone rang with a Colorado area code. The voice on the line was polite and professional: "
                        "'Good evening Mr. Miller, this is the concierge at the St. Regis Aspen. We are preparing the private anniversary champagne and strawberry tasting "
                        "you booked for Mrs. Miller in Suite 402, and we just needed to confirm your preferred dinner reservation time at the steakhouse.' I froze. "
                        "I had not booked any suite, and Clara and I were not married. I calmly kept my voice steady and said, 'Could you remind me which credit card was put on "
                        "file for that booking?' The concierge read out the last four digits of an American Express card that did not belong to me or Clara. "
                        "I asked for the registered guest name, and the concierge replied, 'The reservation is under Mr. Bradley Vance and Clara Miller.' "
                        "Bradley was Clara's wealthy ex-boyfriend who she insisted had been completely out of her life for two years. My hands began to shake with quiet rage. "
                        "I thanked the concierge, confirmed the dinner reservation time for 8 PM, and hung up. Instead of blowing up Clara's phone while she was skiing, "
                        "I called my brother. Together, we packed every single item Clara owned into cardboard boxes: her clothes, makeup, shoes, and vanity mirrors. "
                        "We stacked all twenty boxes neatly in her parents' driveway with a note explaining she had moved out. When Clara landed back at the airport on Monday "
                        "afternoon, she called me asking why I wasn't at the terminal to pick her up. I told her to ask Bradley for a ride, and mentioned that the St. Regis "
                        "champagne looked lovely on her Instagram story. The dead silence on the line was deafening. She began stammering excuses, but I ended the call "
                        "and blocked her on everything. It hurt like hell, but discovering the truth saved me from buying a house with someone who never valued my loyalty."
                    ),
                },
            ],
        },
        "inheritance": {
            "female": [
                {
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
                {
                    "title": "My sister sold our late mother's heirloom jewelry behind my back, so I called the auction house",
                    "body": (
                        "I (28F) lost my mother two years ago after a brave battle with cancer. In her handwritten will, she explicitly designated that her "
                        "vintage jewelry collection, including an Edwardian sapphire necklace handed down through four generations, was to be split fifty-fifty "
                        "between me and my younger sister, Vanessa (25F). Vanessa was always irresponsible with money, racking up massive credit card debts on fast fashion. "
                        "Three months ago, Vanessa got engaged and announced she was having a six-figure celebrity-style wedding at a vineyard. "
                        "When I asked my father when we could schedule our appointment with the estate appraisal attorney to divide mother's safety deposit box, "
                        "he looked evasive and muttered that Vanessa had taken the lockbox keys for safekeeping. "
                        "Alarm bells rang immediately. I called the downtown vault repository and learned Vanessa had cleaned out the entire safe three weeks prior. "
                        "I logged onto high-end local luxury estate auction websites and found the exact sapphire necklace listed as Lot 402 with an opening bid "
                        "of eighteen thousand dollars, consigned under Vanessa's name. Vanessa had secretly sold our mother's most sentimental treasures to fund "
                        "a bespoke designer wedding dress and custom ice sculptures. "
                        "I didn't confront Vanessa with a heated phone call. Instead, I sent the notarized probate will, mother's insurance appraisals, and high-resolution "
                        "family photos directly to the auction house's legal counsel. The auction house immediately flagged the lot as contested stolen property, canceled "
                        "the sale, and impounded the jewelry in their secure vault. Then, my attorney filed emergency conversion charges against Vanessa in probate court. "
                        "Vanessa broke down in tears when the court ordered her to return all items within forty-eight hours or face criminal charges. "
                        "Her fiancé was so disgusted by her dishonesty that he called off the engagement entirely. "
                        "My mother's legacy is safely back in my possession, and Vanessa learned that greed always carries a heavy price."
                    ),
                },
            ],
            "male": [
                {
                    "title": "My family excluded me from our annual vacation after I refused to pay eight thousand dollars for my sister's kids",
                    "body": (
                        "I (27M) work as a software engineer and have always been responsible with my finances. Every summer, my extended family organizes an extravagant "
                        "group vacation. My grandmother originally set up a modest family travel trust before she passed away, but my parents and my older sister, Emily (31F), "
                        "quickly depleted the remaining funds over the years. Four months ago, our family group chat started buzzing with plans for a two-week resort getaway "
                        "in Maui. My father called me directly and casually dropped a bombshell: he told me that because Emily had three young kids and her husband was 'between jobs', "
                        "the family had decided I should cover eight thousand dollars for Emily's flights, rental SUV, and adjoining suites. "
                        "When I politely laughed and said I could barely justify spending that much on my own travel, my father's tone turned icy. He told me that family sacrifices "
                        "for family, and that if I wasn't willing to contribute my fair share, I was being selfish and greedy. "
                        "Two days later, I was removed from the family vacation WhatsApp group. My mother texted me saying that until I apologized and transferred the money to Emily, "
                        "I was officially disinvited from the trip. They assumed I would feel isolated and cave to their demands. "
                        "Instead, I took the eight thousand dollars they demanded, added my own vacation budget, and booked a first-class solo journey across the Amalfi Coast in Italy. "
                        "I stayed in cliffside boutique hotels, ate handmade pasta overlooking the Mediterranean, and posted gorgeous photos on my Instagram. "
                        "Meanwhile, their Hawaii trip was an unmitigated disaster. It turns out Emily had secretly borrowed five thousand dollars from our aunt under the pretense "
                        "that I was reimbursing her later. On day four of their trip, during breakfast at the resort, our aunt publicly confronted Emily about the missing money. "
                        "An explosive screaming match broke out in the hotel lobby, with accusations flying across three generations. Emily broke down and admitted she had spent "
                        "the travel trust on personal designer shopping. When they returned home, my father was so humiliated that he had to sell a parcel of undeveloped rural land "
                        "he had owned for fifteen years just to settle Emily's unpaid resort debts and pay back our aunt. "
                        "A week later, my father called me, completely defeated. He admitted Emily had lied to everyone and apologized for ostracizing me. "
                        "I told him I loved him, but that my wallet would never be an open bank account for my sister's entitlement again. "
                        "They haven't gone on a family vacation since, and I've never had more peace in my life."
                    ),
                },
                {
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
            ],
        },
        "entitled": {
            "female": [
                {
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
                {
                    "title": "My entitled landlord tried to keep my three-thousand-dollar deposit, so I called code enforcement",
                    "body": (
                        "I (26F) lived in a two-bedroom apartment for three years while finishing graduate school. I always paid rent five days early and kept "
                        "the place in immaculate condition. Last month, I purchased my first starter townhouse and gave my landlord, Mr. Henderson, sixty days of formal notice. "
                        "During our final walkthrough, Henderson brought out a white glove and a clipboard. He claimed there was 'invisible dust' behind the refrigerator, "
                        "normal scuffs on the baseboards, and that the original carpeting from 2014 was slightly worn. Three days later, he sent me an itemized invoice "
                        "stating that my entire three-thousand-dollar security deposit was forfeited, plus I allegedly owed him an additional four hundred dollars for deep cleaning. "
                        "When I politely requested receipts and reminded him that state law prohibits deducting for standard wear and tear, he sneered over email: "
                        "'Sue me if you want, college girl. Lawyers cost more than deposits.' "
                        "What Henderson didn't realize was that during my three years there, I had documented dozens of building safety violations he refused to fix. "
                        "The building had unpermitted basement partitions rented out illegally to six tenants, blocked fire exits, and uninspected water boilers. "
                        "Instead of arguing with Henderson, I sent a formal fifty-page complaint packet with photos and timestamps to the city department of buildings, "
                        "the local fire marshal, and the county housing authority. Within seventy-two hours, three city inspectors showed up with clipboards and red tape. "
                        "They shut down his illegal basement apartments immediately, issued forty-five thousand dollars in municipal fines, and ordered emergency repairs. "
                        "Henderson called me in an absolute panic, practically begging to wire my full three thousand dollars back with interest if I retracted the report. "
                        "I told him city safety isn't negotiable. The check cleared the next morning, and he spent his entire summer dealing with building inspectors."
                    ),
                },
            ],
            "male": [
                {
                    "title": "I refused to hand over my proprietary logistics system — they fired me immediately, and their entire operations froze",
                    "body": (
                        "I (29M) am an independent software architect specializing in warehouse routing and automated freight logistics. "
                        "Three years ago, a regional distribution company hired me as an external contractor to build a custom dispatch algorithm. "
                        "In my initial contract, Section 4 explicitly stated that all core source infrastructure and proprietary routing algorithms remained "
                        "my intellectual property, while the client was granted an operating license that terminated upon cessation of my consulting contract. "
                        "Over thirty-six months, my system reduced their delivery delays by forty percent and saved them nearly two million dollars annually. "
                        "Last month, a new Vice President of Operations was brought in. On his second day, he called me into the executive boardroom. "
                        "The VP slammed a transfer agreement onto the desk and demanded that I hand over all root GitHub repositories and encryption keys without extra compensation. "
                        "He sneered that contractors don't get to hold company assets hostage. I calmly pointed out Section 4 of our signed agreement, explaining that while "
                        "they had access to user dashboards, the underlying engine was licensed, not owned. The VP turned purple with rage. "
                        "He pointed at security and yelled, 'You are terminated effective immediately. We will reverse-engineer your code by Friday, and our legal team "
                        "will sue you into bankruptcy.' Security escorted me to the parking lot before I could even log out. "
                        "What the arrogant VP failed to realize was that my system relied on dynamic daily API calibration certificates that updated automatically every "
                        "forty-eight hours based on live GPS traffic telemetry. Exactly forty-eight hours after they locked my credentials, the system license expired. "
                        "At 6 AM on Monday morning, over four hundred freight trucks across six fulfillment centers were stranded with blank dispatch screens. "
                        "Their internal IT team had zero access to the encrypted routing models. Drivers could not print manifests, perishable cargo sat on loading bays, "
                        "and national supermarket clients threatened millions of dollars in penalties. By noon, the CEO himself called me, practically pleading on speakerphone. "
                        "I informed the CEO that my emergency reactivation rate was twenty-five thousand dollars upfront, plus a three-year mandatory enterprise SLA at triple "
                        "my previous retainer. The CEO wired the funds within twenty minutes. The VP was dismissed three weeks later, and I protected my life's work."
                    ),
                },
                {
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
            ],
        },
        "revenge": {
            "female": [
                {
                    "title": "My corporate boss tried to steal my fifty-thousand-dollar commission, so I triggered a forensic audit",
                    "body": (
                        "I (28F) work as a senior enterprise account executive for a national medical supply company. "
                        "For nine months, I worked eighty-hour weeks closing a multi-million-dollar contract with a hospital network. "
                        "The deal carried a fifty-thousand-dollar performance commission explicitly outlined in my compensation plan. "
                        "Two weeks before the commission payout, my Vice President, Marcus (45M), called me into his corner office. "
                        "With a smug grin, Marcus informed me that because he had attended the final thirty-minute signing lunch, "
                        "company policy dictated that he was credited as the primary closer, reducing my payout to a ten percent split. "
                        "When I showed him my written calendar logs and ninety client emails, he smirked and said, 'Take it up with HR if you want, "
                        "but remember who writes your annual performance review.' "
                        "Instead of filing an emotional complaint with HR, I remembered Marcus's obsession with company expense accounts. "
                        "Over the past year, Marcus had submitted dozens of lavish five-star dinners, luxury weekend car rentals, and golf resort packages, "
                        "claiming they were 'client entertainment' for the hospital executives on my deal. "
                        "I called the hospital's procurement director, verified they had never attended any of those dinners, and compiled a comprehensive "
                        "dossier of falsified receipts and conflicting dates. I submitted the documentation directly to the chief financial officer "
                        "and external compliance auditor. Within forty-eight hours, corporate security escorted Marcus out of the building. "
                        "The audit uncovered over one hundred and twenty thousand dollars in fabricated expenses. "
                        "The company restored my full fifty-thousand-dollar commission with an apology bonus, and Marcus was dismissed immediately."
                    ),
                },
            ],
            "male": [
                {
                    "title": "My entitled neighbor tried to claim my driveway and called the cops, so I ordered a land survey",
                    "body": (
                        "I (29M) bought my first suburban home two years ago with a detached two-car garage and a paved side driveway. "
                        "From the week I moved in, my neighbor, Frank (58M), treated my driveway as his personal overflow parking. "
                        "He parked his boat trailer, his son's truck, and his lawnmowers across my property line. "
                        "Last month, I finally hired a contractor to install a cedar privacy fence along the boundary. "
                        "The moment my contractor began digging post holes, Frank stormed over screaming, threatening workers with a shovel, "
                        "and called the police claiming I was trespassing on his grandfathered easement. "
                        "The responding officers told us it was a civil boundary dispute and halted the fence installation until a certified boundary survey was conducted. "
                        "Frank stood in his yard grinning, bragging to other neighbors that he was going to take me to court and take half my yard. "
                        "I hired a licensed civil engineering firm to conduct a precision GPS boundary survey of the property pins. "
                        "Three days later, the surveyor handed me the finalized blueprint with a stunned look on his face. "
                        "Not only was my entire driveway undeniably on my deed, but Frank's two-story brick garage and concrete patio had been built four feet "
                        "over my legal property line thirty years ago without permits. "
                        "My attorney sent Frank a certified demand letter: he had thirty days to demolish the encroachment or purchase the four-foot strip "
                        "at seventy-five thousand dollars cash. Frank broke down in tears, begging me not to force him to demolish his garage. "
                        "I had my privacy fence built right up to the true pin, Frank had to pay twenty thousand dollars to alter his roofline, "
                        "and he has not made eye contact with me since."
                    ),
                },
            ],
        },
        "confession": {
            "female": [
                {
                    "title": "A commercial DNA test revealed my best friend and I were switched at birth",
                    "body": (
                        "I (26F) have been inseparable best friends with Maya (26F) since our first day of kindergarten. "
                        "Our families lived three blocks apart, and our parents always joked that we looked more like each other's sisters than our actual families. "
                        "For Christmas last year, Maya's brother gifted everyone in both families popular direct-to-consumer DNA ancestry kits for fun. "
                        "We spit into the plastic tubes, mailed them off, and forgot about them for six weeks. "
                        "On a Tuesday afternoon, my phone blew up with frantic calls from Maya. She was hyperventilating so hard she could barely speak. "
                        "When I opened my portal results, my entire world turned upside down: Maya's parents were listed as my biological mother and father "
                        "with a ninety-nine point nine percent match, while my parents were Maya's biological parents. "
                        "We were born on the exact same morning, ninety minutes apart, at a small community hospital that has since closed down. "
                        "In the chaos of a severe winter blizzard that night, a tired maternity nurse had accidentally switched our hospital bassinets. "
                        "For twenty-six years, both sets of parents had lovingly raised each other's biological daughters without the slightest suspicion. "
                        "When we gathered both families around our living room table to break the news, the room was completely silent for five full minutes "
                        "before both of our mothers burst into uncontrollable tears. "
                        "It felt like the ground beneath our lives had shattered. But after months of counseling, both families agreed not to let it divide us. "
                        "Maya and I are now legally recognized as sisters, and we celebrate holidays together as one giant, unconventional family."
                    ),
                },
            ],
            "male": [
                {
                    "title": "I was the safety inspector who uncovered my company dumping chemicals — here is why I blew the whistle",
                    "body": (
                        "I (31M) worked for four years as an environmental compliance specialist for a regional manufacturing plant. "
                        "My job was to ensure our industrial cleaning solvents and wastewater met state environmental protection standards before treatment. "
                        "Six months ago, during an unscheduled late-night facility inspection, I noticed an unmapped PVC bypass valve concealed behind the storage silos. "
                        "The valve was connected to an automated pump that activated at 2 AM every Tuesday and Thursday, routing untreated chemical sludge directly "
                        "into the municipal storm drainage culvert leading straight to the local river. "
                        "When I confronted our plant director with water toxicity logs, he slammed his office door and told me to delete the inspection file immediately. "
                        "He offered me a thirty-thousand-dollar promotion bonus to look the other way, warning me that whistleblowers never work in the sector again. "
                        "I couldn't sleep for two weeks thinking about the families and farms downstream. "
                        "Instead of deleting the file, I wore a covert pinhole camera, gathered certified water sample vials, and documented eighteen separate bypass discharge events. "
                        "I handed the complete digital evidence drive to federal EPA investigators and the Department of Justice. "
                        "Federal marshals raided the facility the following month. The company was hit with twenty-two million dollars in environmental criminal fines, "
                        "the plant director was indicted, and I was granted federal whistleblower protection. "
                        "I lost my job at the plant, but I protected thousands of people in my hometown, and I would do it again in a heartbeat."
                    ),
                },
            ],
        },
        "aitah": {
            "female": [
                {
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
            ],
            "male": [
                {
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
            ],
        },
        "facts": {
            "female": [
                {
                    "title": "Four disturbing psychological facts that will completely change how you see people",
                    "body": (
                        "Here are four unsettling psychological facts that most people go their entire lives without realizing. "
                        "Number one: the illusion of moral superiority. Studies show that over eighty-five percent of people believe they are more ethical, empathetic, and fair than the average person. "
                        "Psychologically, this means almost everyone who harms or betrays someone else genuinely convinces their own brain that they are the victim in the situation. "
                        "Number two: the macabre curiosity instinct. Your brain is hardwired to seek out disturbing information, horror stories, and gruesome details not because you are twisted, "
                        "but because evolutionary survival programming forces your subconscious to study potential threats so it can prepare for them. "
                        "Number three: false memory implantation. Neuroscientists have proven that within just two weeks of gentle conversational suggestion, "
                        "nearly thirty percent of adults can be led to vividly remember complex childhood traumatic events that never actually happened, complete with sensory memories of smells and sights. "
                        "And number four: the bystander paralysis effect. If you collapse in a crowded subway station with a hundred onlookers, "
                        "you are statistically far less likely to receive immediate help than if you collapse in an empty alleyway with only a single stranger present. "
                        "In a crowd, every individual brain assumes someone else has already called for help, leaving you completely alone in plain sight."
                    ),
                },
            ],
            "male": [
                {
                    "title": "Unsettling ocean facts that prove we know nothing about what lives down there",
                    "body": (
                        "Here are four chilling deep ocean facts that explain why marine biologists fear the abyss far more than outer space. "
                        "Number one: over eighty percent of the ocean remains completely unmapped, unobserved, and unexplored by human eyes. "
                        "We have far more high-resolution photographic maps of the surface of Mars and the dark side of the Moon than we do of our own planet's seabed. "
                        "Number two: the Mariana Trench is nearly thirty-six thousand feet deep. If you dropped Mount Everest into the Challenger Deep, "
                        "its snow-capped peak would still be buried under more than seven thousand feet of crushing, pitch-black water. "
                        "At the bottom, the atmospheric pressure exceeds one thousand atmospheres, which is equivalent to having the weight of fifty jumbo jets stacked directly on top of your skull. "
                        "Number three: the deepest living creatures survive in absolute freezing blackness where sunlight has not penetrated for billions of years. "
                        "Many of these organisms do not rely on photosynthesis; instead, they consume toxic superheated sulfur vents and exhibit biological immortality, with lifespans stretching for centuries. "
                        "And number four: ocean acoustic anomalies. In 1997, oceanic hydrophones detected an ultra-low frequency sound dubbed The Bloop that echoed across three thousand miles of open Pacific water. "
                        "While scientists later attributed it to massive Antarctic icequakes, deep sea sonar stations regularly pick up unexplained biological frequency echoes moving at impossible depths that no known whale or squid could ever survive."
                    ),
                },
            ],
        },
        "scary": {
            "female": [
                {
                    "title": "I heard my own voice whispering outside my cabin window at three in the morning",
                    "body": (
                        "I (26F) rented a secluded timber cabin in upstate New York last autumn to finish writing my graduate thesis. "
                        "The property sat four miles off the nearest paved county road, surrounded by dense pine forest. "
                        "On my fourth night, around 2:45 AM, a fierce rainstorm knocked out the electrical grid, plunging the cabin into total pitch darkness. "
                        "I lit three utility candles and curled up on the living room sofa with a book. Just after 3:15 AM, as the wind began to settle, "
                        "I heard quiet footsteps crunching slowly across the gravel driveway right outside my front porch. My heart leaped into my throat. "
                        "I blew out the candles and froze in complete silence. The footsteps circled the perimeter of the cabin and stopped directly beneath the bedroom window. "
                        "Then came three soft, deliberate knuckles tapping against the glass pane. Ten seconds later, a voice whispered from the darkness outside: "
                        "'Could you unlock the deadbolt? It is cold out here.' The terrifying part was not the request itself. It was the voice. It sounded identical to me. "
                        "The pitch, the subtle cadence, even the distinct breathing pause between words matched my own voice perfectly. "
                        "I grabbed my car keys and kitchen knife, locked myself in the pantry, and called county dispatch with my hands violently trembling. "
                        "When the deputies arrived forty minutes later, they found deep bare footprints circling the exterior of the cabin three full times, leading directly into the thick woods. "
                        "I packed everything into my car and drove away without looking back."
                    ),
                },
            ],
            "male": [
                {
                    "title": "The graveyard night shift at the old hospital was supposed to be empty until the third floor elevator called",
                    "body": (
                        "I (28M) worked as an overnight security officer for an abandoned municipal hospital slated for demolition in downtown Chicago. "
                        "My shift ran from 11 PM to 7 AM, and my sole duty was ensuring trespassers and metal thieves did not break into the fenced compound. "
                        "The entire seven-story medical wing had been gutted two years prior: no beds, no medicines, and no running water. "
                        "Only emergency exit lights and industrial power feeds to the basement remained active. "
                        "At approximately 3:30 AM last Tuesday, the master security annunciator panel in the ground floor guard shack let out a sharp electronic chime. "
                        "On the digital display board, the central elevator car indicator switched from 'STANDBY' to 'ACTIVE'. "
                        "I watched the glowing numbers climb slowly from the basement to the third floor, which had once housed the pediatric palliative ward. "
                        "The chime rang again as the mechanical doors opened on three, remained open for forty-five seconds, and then closed. "
                        "Then, the elevator started descending straight down toward the ground floor lobby where I was sitting. "
                        "I drew my heavy flashlight and unclipped my radio, staring down the dark terrazzo hallway toward the elevator shaft. "
                        "When the car arrived with a mechanical thud and the stainless-steel doors slid open, the car was completely empty. "
                        "But as I approached the threshold to reset the circuit breaker, the linoleum floor was covered in wet, muddy footprint impressions walking out of the elevator into the lobby, "
                        "stopping exactly three feet in front of where I was standing. The footprints were small, like a child's bare feet. I clocked out right then and never returned."
                    ),
                },
            ],
        },
        "showerthoughts": {
            "female": [
                {
                    "title": "Four shower thoughts that will completely break your brain",
                    "body": (
                        "Here are four mind-bending shower thoughts that will genuinely alter how you perceive reality today. "
                        "Number one: you have never actually seen your own physical face with your own eyes. "
                        "You have only ever seen mirrored reflections, camera pixels, and still photographic prints, meaning every other person you have ever passed on the street has a more direct visual observation of your true physical appearance than you will ever have in your entire lifetime. "
                        "Number two: every atom of iron coursing through the red blood cells inside your veins right now was forged in the violent core collapse of an exploding supernova star billions of years ago. "
                        "You are not just living in the universe; you are the universe experiencing itself in a conscious human form. "
                        "Number three: the concept of an expiration date. When you drink a bottle of water, you are consuming liquid that has cycled through Earth's atmosphere, oceans, and dinosaur digestive tracts for over four billion years without spoiling, "
                        "yet we assign a plastic bottle an expiration date purely because the container itself will break down before the water ever does. "
                        "And number four: our modern digital existence. We carry miniature glowing glass rectangles in our pockets that contain access to the entirety of human knowledge, art, and philosophy gathered over ten thousand years of civilization, "
                        "yet humanity predominantly uses them to argue with anonymous strangers and watch videos of dancing pets. If an ancient philosopher woke up today, they would be simultaneously awestruck and utterly bewildered."
                    ),
                },
            ],
            "male": [
                {
                    "title": "Mind-bending thoughts that prove time and memory are absolute illusions",
                    "body": (
                        "Here are four philosophical shower thoughts that prove human perception is far weirder than you think. "
                        "Number one: the past and future do not exist anywhere in the physical universe. "
                        "The past is nothing more than electrical patterns stored in biological brain tissue, and the future is purely a conceptual projection of your imagination. "
                        "The only physical reality that ever actually exists anywhere across the cosmic fabric is the exact microsecond happening right now. "
                        "Number two: when you look up into the night sky at the stars, you are literally staring millions of years into the ancient past. "
                        "The light reaching your retinas from distant galaxies began traveling across space before human beings even discovered fire. "
                        "Some of those bright stars died millions of years ago, but their final light show is only reaching your backyard tonight. "
                        "Number three: your brain made the decision to watch this video roughly three hundred milliseconds before your conscious mind even felt like it made a choice. "
                        "Neuroscience experiments repeatedly show that subconscious neurological readiness potentials fire before your internal monologue claims ownership of a decision, raising the unsettling question of who is truly steering the wheel. "
                        "And number four: you will be the last person to ever know you have died. You remember your very first childhood memory, but because consciousness ceases at the moment of passing, you will never consciously register the final moment your life concludes. "
                        "Cherish the present, because it is literally all you will ever have."
                    ),
                },
            ],
        },
        "wild_history": {
            "female": [
                {
                    "title": "The unbelievable true story of the woman who fell thirty-three thousand feet from an airplane and survived",
                    "body": (
                        "In January 1972, a twenty-two-year-old flight attendant named Vesna Vulovic was working aboard JAT Flight 367 flying at thirty-three thousand feet over Czechoslovakia. "
                        "Mid-flight, a terrorist briefcase bomb detonated in the cargo compartment, instantly tearing the twin-engine aircraft into pieces in the freezing sky. "
                        "While every other passenger and crew member was pulled into the air or killed in the explosion, Vesna became pinned beneath a heavy food cart inside a fractured section of the tail fuselage. "
                        "That fuselage section plummeted six miles through the freezing atmosphere, hurtling toward the ground at terminal velocity. "
                        "Miraculously, the wreckage crashed into a heavily sloped, snow-covered pine mountain ridge in the village of Srbska Kamenice. "
                        "The angle of the slope, combined with dozens of thick evergreen branches and feet of soft fresh powder, acted like an immense natural deceleration cushion. "
                        "When a local villager who had served as a combat medic in World War Two arrived at the smoking crash site, he heard faint groaning beneath the metal sheets. "
                        "Vesna had suffered a fractured skull, two crushed vertebrae, and multiple broken ribs, but she was alive. "
                        "Doctors placed her in an induced coma for twenty-seven days, and despite severe temporary paralysis, she made a near-total recovery and lived another forty-four years, "
                        "earning the Guinness World Record for the highest fall survived without a parachute."
                    ),
                },
            ],
            "male": [
                {
                    "title": "The insane true story of the man who survived two atomic bombs in three days",
                    "body": (
                        "In August 1945, Tsutomu Yamaguchi was a twenty-nine-year-old naval engineer on a business trip in Hiroshima for Mitsubishi Heavy Industries. "
                        "On the morning of August 6th, while walking toward the docks to catch his return train home, an American B-29 bomber dropped the first atomic weapon, Little Boy, directly over the city center, less than two miles from where he stood. "
                        "The blinding flash tore his eardrums, severely burned his torso, and threw him into an irrigation ditch. "
                        "Despite his horrific burns, Yamaguchi bandaged his wounds, spent the night in an air raid shelter, and boarded the last evacuee train back to his hometown: Nagasaki. "
                        "On the morning of August 9th, heavily bandaged and limping, he reported to his supervisor at the Nagasaki Mitsubishi office to deliver his engineering report. "
                        "While his boss was openly laughing at him, accusing him of making up a story about a single bomb destroying an entire city, a second blinding flash lit up the room. "
                        "The Fat Man atomic bomb had detonated two miles from their office. The blast shattered every window, tore the reinforced concrete walls off the building, and blew Yamaguchi across the hallway. "
                        "Once again, he survived. Yamaguchi not only lived through both nuclear detonations in seventy-two hours, but he lived to the age of ninety-three as an outspoken global advocate for international peace, "
                        "officially recognized as the only verified double-atomic bomb survivor in human history."
                    ),
                },
            ],
        },
        "workplace": {
            "female": [
                {
                    "title": "An entitled customer threw hot coffee on our counter and demanded I be fired, so the district attorney stepped in",
                    "body": (
                        "I (26F) worked as an assistant store manager at an upscale boutique bakery in Boston. "
                        "Last Saturday morning during our busiest rush, an affluent woman in designer sunglasses marched straight past the twenty-person queue to the register. "
                        "She slammed down a custom three-tiered cake box from our competitor down the street and aggressively demanded that we give her an immediate fifty-dollar cash refund because the frosting was melting in her warm car. "
                        "When I politely explained that we could not refund items purchased from a completely different competitor, she went into a blind rage. "
                        "She began screaming obscenities, knocking over our promotional acrylic displays, and hurled a steaming hot caramel latte directly across the service counter, splashing scalding liquid over my arms and uniform. "
                        "She smirked and shouted, 'You will be working a minimum-wage dumpster for the rest of your pathetic life once I call the owner!' "
                        "What she did not realize was that the quiet customer standing directly behind her in line, whose coat had also been splattered with hot espresso, was the deputy district attorney for our county. "
                        "The customer immediately produced his official credentials, instructed our security guard to seal the front doors, and called the precinct commander directly. "
                        "Two squad cars arrived within four minutes. The woman was arrested on the spot for felony battery with a dangerous weapon and disorderly conduct, weeping in handcuffs as shoppers clapped. "
                        "Her husband later tried to offer me five thousand dollars to drop the charges; I declined, and she was sentenced to one hundred hours of mandatory community service cleaning city trash."
                    ),
                },
            ],
            "male": [
                {
                    "title": "An entitled customer tried to scam our luxury electronics store and got his own car towed from the fire lane",
                    "body": (
                        "I (28M) managed a high-end consumer audio boutique in Dallas. "
                        "Last Friday evening, a man in an expensive suit parked his luxury German sports car diagonally across our designated emergency red fire lane right in front of our entrance, blocking the handicapped ramp. "
                        "He walked in holding an empty box for a twelve-hundred-dollar reference turntable and demanded a full cash refund, claiming the device was defective. "
                        "When I asked for his receipt or loyalty account number, he sneered and said receipts were for poor people, demanding I open the cash register immediately or he would have his personal attorney sue our parent company into bankruptcy. "
                        "When I opened the box to inspect the serial number, the unit inside was a fifteen-year-old scratched plastic radio stuffed with packaging foam. "
                        "He had bought our new system elsewhere and was trying to pull a return scam with broken junk. "
                        "I smiled and said, 'Let me step into the back office to authorize a manager payout for this amount.' "
                        "While in the back office, I did not authorize any cash; instead, I called the non-emergency municipal towing dispatch and the city code enforcement marshal. "
                        "Within eight minutes, a heavy-duty flatbed tow truck backed up to the fire lane, hooked his sports car by the rear axles, and hoisted it into the air for blocking the hydrants. "
                        "When the scammer saw his car disappearing down the boulevard through the storefront window, he sprinted into the parking lot screaming in pure panic. "
                        "He was hit with a five-hundred-dollar impound fee, a six-hundred-dollar fire violation citation, and our corporate security banned him from every branch in North America."
                    ),
                },
            ],
        },
    }

    category_dict = story_database.get(cat_key, story_database["aitah"])
    gender_stories = category_dict[target_gender]
    chosen = random.choice(gender_stories) if isinstance(gender_stories, list) else gender_stories

    cleaned_title = clean_story_title(chosen["title"], category=cat_key)
    cleaned_body = clean_text_for_narration(chosen["body"], category=cat_key)

    return Story(
        title=cleaned_title,
        body=cleaned_body,
        narrator_gender=target_gender,
        source="curated_library",
        word_count=len(cleaned_body.split()),
        tags=["curated", "viral_storytime", cat_key],
    )


def get_story(
    source: str = "auto",
    gender_override: Optional[str] = None,
    preferred_subreddit: Optional[str] = None,
    category: Optional[str] = None,
) -> Story:
    """Master entry point to retrieve or generate a story following the specified strategy.

    Supports diverse viral categories:
    - 'love': Love & Relationship drama (cheating, secret phones, wedding confessions)
    - 'inheritance': Family inheritance & estate battles (wills, trusts, vacation drama)
    - 'entitled': Entitled people & roommate karma (logistics systems, towing cars, deposit payback)
    - 'revenge': Sweet & petty revenge, corporate audits, property encroachments
    - 'confession': Shocking confessions, birth switches, whistleblower disclosures
    - 'aitah': Classic AITAH moral conflicts
    - 'facts': Mind-blowing real-world facts & dark curiosities (science, psychology, ocean)
    - 'scary': True horror, paranormal & glitch in the matrix encounters
    - 'showerthoughts': Mind-bending shower thoughts & brain-breaking epiphanies
    - 'wild_history': Insane true survival stories & historical feats
    - 'workplace': Unhinged customer Karen encounters & workplace karma

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
        logger.warning("Gemini generation failed; falling back to alternative sources.")

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

    # 4. Multi-Million Public Reddit Datasets (filtered by category keywords)
    logger.info("Searching public Reddit datasets for authentic '%s' stories...", selected_cat)
    dataset_story = fetch_from_huggingface_reddit(category=selected_cat)
    if dataset_story:
        if gender_override:
            dataset_story.narrator_gender = gender_override
        return dataset_story

    # 5. Gemini fallback if configured and not yet tried
    if config.gemini_api_key:
        logger.info("Attempting Gemini generation as fallback...")
        gemini_story = generate_with_gemini(gender_override=gender_override, category=selected_cat)
        if gemini_story:
            return gemini_story

    # 6. Curated Fallback Story in the requested category
    logger.info("Using curated viral story library for category '%s'", selected_cat)
    return get_curated_fallback_story(gender_override=gender_override, category=selected_cat)

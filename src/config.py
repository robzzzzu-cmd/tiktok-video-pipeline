"""Configuration management for the TikTok video pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class PipelineConfig(BaseModel):
    """Pipeline configuration parameters loaded from environment or defaults."""

    # Paths
    project_root: Path = Field(default=PROJECT_ROOT)
    output_dir: Path = Field(default=PROJECT_ROOT / "output")
    fonts_dir: Path = Field(default=PROJECT_ROOT / "fonts")
    temp_dir: Path = Field(default=PROJECT_ROOT / "temp")

    # API Keys & Secrets
    gemini_api_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY")
    )
    reddit_client_id: Optional[str] = Field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_ID")
    )
    reddit_client_secret: Optional[str] = Field(
        default_factory=lambda: os.getenv("REDDIT_CLIENT_SECRET")
    )
    reddit_user_agent: str = Field(
        default_factory=lambda: os.getenv(
            "REDDIT_USER_AGENT", "Mozilla/5.0 (compatible; TikTokStoryBot/1.0)"
        )
    )

    # TikTok Credentials
    tiktok_access_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("TIKTOK_ACCESS_TOKEN")
    )
    tiktok_client_key: Optional[str] = Field(
        default_factory=lambda: os.getenv("TIKTOK_CLIENT_KEY")
    )
    tiktok_client_secret: Optional[str] = Field(
        default_factory=lambda: os.getenv("TIKTOK_CLIENT_SECRET")
    )
    tiktok_refresh_token: Optional[str] = Field(
        default_factory=lambda: os.getenv("TIKTOK_REFRESH_TOKEN")
    )
    tiktok_session_id: Optional[str] = Field(
        default_factory=lambda: os.getenv("TIKTOK_SESSION_ID")
    )

    # YouTube Background Video
    background_video_url: str = Field(
        default_factory=lambda: os.getenv(
            "BACKGROUND_VIDEO_URL", "https://www.youtube.com/watch?v=9or33zyZJgM"
        )
    )
    background_video_total_seconds: int = Field(
        default_factory=lambda: int(os.getenv("BACKGROUND_VIDEO_TOTAL_SECONDS", "3600"))
    )
    youtube_cookies: Optional[str] = Field(
        default_factory=lambda: os.getenv("YOUTUBE_COOKIES")
    )
    background_video_fallback_url: Optional[str] = Field(
        default_factory=lambda: os.getenv("BACKGROUND_VIDEO_FALLBACK_URL")
    )
    background_music_url: str = Field(
        default_factory=lambda: os.getenv(
            "BACKGROUND_MUSIC_URL",
            "https://www.tiktok.com/music/son-original-7306937893019274016",
        )
    )
    bg_music_volume_db: str = Field(
        default_factory=lambda: os.getenv("BG_MUSIC_VOLUME_DB", "-26dB")
    )

    # Story & Voice Settings
    story_source: str = Field(
        default_factory=lambda: os.getenv("STORY_SOURCE", "auto")  # 'auto', 'reddit', 'gemini'
    )
    story_category: str = Field(
        default_factory=lambda: os.getenv("STORY_CATEGORY", "auto")  # 'auto', 'love', 'inheritance', 'entitled', 'aitah'
    )
    subreddits: List[str] = Field(
        default_factory=lambda: [
            "TrueOffMyChest",
            "EntitledPeople",
            "pettyrevenge",
            "confession",
            "relationship_advice",
            "familydrama",
            "tifu",
            "offmychest",
            "MaliciousCompliance",
            "AITAH",
        ]
    )
    target_words_min: int = Field(
        default_factory=lambda: int(os.getenv("TARGET_WORDS_MIN", "450"))
    )
    target_words_max: int = Field(
        default_factory=lambda: int(os.getenv("TARGET_WORDS_MAX", "520"))
    )

    # Voice Profiles
    voice_male_primary: str = "en-US-ChristopherNeural"
    voice_male_fallback: str = "en-US-GuyNeural"
    voice_female_primary: str = "en-US-JennyNeural"
    voice_female_fallback: str = "en-US-AriaNeural"

    # Subtitle Styles (ASS)
    font_name: str = "Montserrat ExtraBold"
    font_size: int = 76
    subtitle_alignment: int = 5                # 5 = Middle-Center (dead center of the screen)
    subtitle_margin_v: int = 0                 # 0 offset from middle
    primary_color_ass: str = "&H00FFFFFF&"     # White (AABBGGRR)
    outline_color_ass: str = "&H00000000&"     # Black
    highlight_color_ass: str = "&H0000FFFF&"   # Bright Yellow (BB=00, GG=FF, RR=FF)
    outline_width: int = 5
    shadow_depth: int = 2

    # Video Compositing
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    bg_audio_volume_db: str = "-22dB"

    # Publishing Metadata
    hashtags: List[str] = Field(
        default_factory=lambda: [
            "#storytime",
            "#redditstories",
            "#asmr",
            "#restock",
            "#satisfying",
            "#fyp",
        ]
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


# Global default configuration instance
config = PipelineConfig()

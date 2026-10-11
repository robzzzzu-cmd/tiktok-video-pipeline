"""Main CLI entry point orchestrating the entire TikTok automated video pipeline."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional

from src.config import config
from src.story_generator import get_story
from src.tts_engine import generate_voiceover
from src.subtitles import generate_ass_subtitles
from src.video_composer import download_background_clip, render_final_video
from src.tiktok_uploader import publish_to_tiktok

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("tiktok_pipeline")


def run_pipeline(
    source: str = "auto",
    gender: Optional[str] = None,
    subreddit: Optional[str] = None,
    dry_run: bool = False,
    skip_upload: bool = False,
    keep_temp: bool = False,
) -> Path:
    """Runs the end-to-end video generation and auto-publishing workflow."""
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    temp_run_dir = config.temp_dir / f"run_{timestamp_str}"
    temp_run_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    final_video_path = config.output_dir / f"tiktok_video_{timestamp_str}.mp4"
    summary_path = config.output_dir / "latest_run_summary.json"

    logger.info("=========================================================")
    logger.info("Starting Automated TikTok Storytime Pipeline")
    logger.info("Run ID: %s | Source: %s | Dry Run: %s", timestamp_str, source, dry_run)
    logger.info("=========================================================")

    try:
        # Step 1: Story Ingestion
        logger.info("[Step 1/5] Ingesting / generating viral dramatic story...")
        story = get_story(
            source=source,
            gender_override=gender if gender != "auto" else None,
            preferred_subreddit=subreddit,
        )
        logger.info("Story Title: '%s'", story.title)
        logger.info(
            "Narrator Gender: %s | Words: %d | Source: %s",
            story.narrator_gender,
            story.word_count,
            story.source,
        )

        narration_text = story.full_narration_text()

        # Step 2: Text-to-Speech Engine
        logger.info("[Step 2/5] Synthesizing voiceover and word boundary timings...")
        tts_result = generate_voiceover(
            text=narration_text,
            gender=story.narrator_gender,
            output_dir=temp_run_dir,
            filename_prefix=f"voiceover_{timestamp_str}",
        )
        logger.info(
            "Voiceover generated: %.2f seconds using voice '%s' (%d words tracked)",
            tts_result.duration_seconds,
            tts_result.voice_used,
            len(tts_result.word_timings),
        )

        # Step 3: Dynamic ASS Subtitle Creation
        logger.info("[Step 3/5] Generating TikTok animated word-highlight subtitles...")
        ass_path = temp_run_dir / f"captions_{timestamp_str}.ass"
        generate_ass_subtitles(
            word_timings=tts_result.word_timings,
            output_ass_path=ass_path,
        )
        logger.info("Subtitles written to %s", ass_path)

        # Step 4: Background Video Download (yt-dlp partial section)
        logger.info("[Step 4/5] Extracting matching random background segment from YouTube...")
        raw_bg_path = temp_run_dir / f"background_{timestamp_str}.mp4"
        download_background_clip(
            target_duration=tts_result.duration_seconds,
            output_path=raw_bg_path,
        )

        # Step 5: Video Compositing (ffmpeg)
        logger.info("[Step 5/5] Compositing final vertical 1080x1920 video with ffmpeg...")
        render_final_video(
            background_video=raw_bg_path,
            voiceover_audio=tts_result.audio_path,
            subtitles_ass=ass_path,
            output_video=final_video_path,
            duration=tts_result.duration_seconds,
        )
        logger.info("Final video successfully rendered: %s", final_video_path)

        # Step 6: TikTok Auto-Publishing
        upload_result = {"status": "skipped"}
        if not skip_upload:
            logger.info("Publishing generated video to TikTok...")
            upload_result = publish_to_tiktok(
                video_path=final_video_path,
                title=story.title,
                dry_run=dry_run,
            )
            logger.info("Publish result: %s", upload_result)
        else:
            logger.info("Upload skipped via --skip-upload flag.")

        # Save run summary
        summary = {
            "timestamp": timestamp_str,
            "status": "completed",
            "story": {
                "title": story.title,
                "gender": story.narrator_gender,
                "word_count": story.word_count,
                "source": story.source,
                "subreddit": story.subreddit,
            },
            "audio": {
                "voice": tts_result.voice_used,
                "duration_seconds": round(tts_result.duration_seconds, 2),
                "words_aligned": len(tts_result.word_timings),
            },
            "video_path": str(final_video_path),
            "video_size_bytes": final_video_path.stat().st_size,
            "upload": upload_result,
        }
        with open(summary_path, "w", encoding="utf-8") as f_sum:
            json.dump(summary, f_sum, indent=2)

        logger.info("Run summary saved to %s", summary_path)
        logger.info("=========================================================")
        logger.info("Pipeline completed successfully!")
        logger.info("=========================================================")
        return final_video_path

    finally:
        # Cleanup temp directory if not explicitly preserved
        if not keep_temp and temp_run_dir.exists():
            logger.info("Cleaning up temporary run directory %s...", temp_run_dir)
            shutil.rmtree(temp_run_dir, ignore_errors=True)


def parse_arguments() -> argparse.Namespace:
    """Parses command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Automated TikTok Video Generation and Publishing Pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--source",
        choices=["auto", "reddit", "gemini"],
        default="auto",
        help="Story generation source.",
    )
    parser.add_argument(
        "--gender",
        choices=["auto", "male", "female"],
        default="auto",
        help="Narrator voice gender. If 'auto', detects from story content.",
    )
    parser.add_argument(
        "--subreddit",
        type=str,
        default=None,
        help="Optional specific subreddit to fetch story from.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Execute the full pipeline and simulate TikTok upload without actual posting.",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Render video but skip publishing completely.",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="Preserve temporary audio, subtitle, and raw clip files.",
    )
    parser.add_argument(
        "--upload-only",
        type=str,
        default=None,
        help="Upload an existing video file directly to TikTok without generating a new one.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI execution entrypoint."""
    args = parse_arguments()

    if args.upload_only:
        video_path = Path(args.upload_only)
        if not video_path.exists():
            logger.error("Specified video file does not exist: %s", video_path)
            sys.exit(1)
        logger.info("Uploading existing video: %s", video_path)
        result = publish_to_tiktok(video_path, title="Viral Storytime", dry_run=args.dry_run)
        logger.info("Upload result: %s", result)
        sys.exit(0)

    try:
        run_pipeline(
            source=args.source,
            gender=args.gender,
            subreddit=args.subreddit,
            dry_run=args.dry_run,
            skip_upload=args.skip_upload,
            keep_temp=args.keep_temp,
        )
    except Exception as e:
        logger.exception("Pipeline failed with unhandled exception: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Main CLI entry point orchestrating the entire TikTok automated video pipeline."""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import shutil
import sys
import time
from pathlib import Path
from typing import Optional

from src.config import config
from src.story_generator import get_story, split_story_into_two_parts
from src.tts_engine import generate_voiceover
from src.subtitles import generate_ass_subtitles
from src.reddit_card import generate_reddit_card
from src.video_composer import (
    download_background_clip,
    download_background_music,
    render_final_video,
)
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
    category: str = "auto",
    bg_style: str = "auto",
    privacy: str = "public",
    length: str = "multipart",
    include_bg_music: bool = False,
    fail_on_upload_error: bool = False,
    dry_run: bool = False,
    skip_upload: bool = False,
    keep_temp: bool = False,
) -> Path:
    """Runs the end-to-end video generation and auto-publishing workflow."""
    if privacy:
        config.tiktok_privacy_level = "SELF_ONLY" if privacy.lower() in ("self_only", "self", "private", "draft") else "PUBLIC_TO_EVERYONE"
    if length:
        config.video_length = length
        if length in ("multipart", "multi_part", "two_part", "twopart"):
            config.video_length = "multipart"
            config.target_words_min = 750
            config.target_words_max = 950
        elif length == "short":
            config.target_words_min = 180
            config.target_words_max = 240
        elif length == "full":
            config.target_words_min = 450
            config.target_words_max = 520
    if include_bg_music is not None:
        config.include_background_music = include_bg_music
    if fail_on_upload_error:
        config.fail_on_upload_error = True

    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    temp_run_dir = config.temp_dir / f"run_{timestamp_str}"
    temp_run_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = config.output_dir / "latest_run_summary.json"

    logger.info("=========================================================")
    logger.info("Starting Automated TikTok Storytime Pipeline")
    logger.info(
        "Run ID: %s | Source: %s | Category: %s | Style: %s | Length: %s | Privacy: %s",
        timestamp_str,
        source,
        category,
        bg_style,
        config.video_length,
        config.tiktok_privacy_level,
    )
    logger.info("=========================================================")

    try:
        # Step 1: Story Ingestion
        logger.info("[Step 1/5] Ingesting / generating viral dramatic story...")
        story = get_story(
            source=source,
            gender_override=gender if gender != "auto" else None,
            preferred_subreddit=subreddit,
            category=category,
        )
        logger.info("Story Title: '%s'", story.title)
        logger.info(
            "Narrator Gender: %s | Words: %d | Source: %s",
            story.narrator_gender,
            story.word_count,
            story.source,
        )

        matched_cat = story.tags[-1] if (story.tags and story.tags[-1] in config.category_colors) else category
        highlight_color = config.category_colors.get(matched_cat, config.highlight_color_ass)
        category_label = story.subreddit if story.subreddit else matched_cat.upper()

        bg_music_path = None
        if config.include_background_music:
            logger.info("Downloading quiet background music...")
            bg_music_path = temp_run_dir / f"bg_music_{timestamp_str}.mp3"
            download_background_music(
                music_url=config.background_music_url,
                output_path=bg_music_path,
            )
        else:
            logger.info("Background music mixing is disabled (clean voiceover ready for native TikTok trending sound).")

        if config.video_length == "multipart":
            logger.info("Multi-part mode enabled: splitting story into Part 1 and Part 2 (>=2 mins each)...")
            part1_narration, part2_narration = split_story_into_two_parts(
                title=story.title,
                body=story.body,
                min_part1_words=config.min_part_words,
            )

            parts_spec = [
                (1, f"{story.title} [Part 1]", f"{category_label} • PART 1", part1_narration),
                (2, f"{story.title} [Part 2]", f"{category_label} • PART 2", part2_narration),
            ]

            rendered_parts = []
            for p_num, p_title, p_badge, p_narration in parts_spec:
                logger.info("=========================================================")
                logger.info("Processing Video Part %d/2: '%s'", p_num, p_title)
                logger.info("=========================================================")
                logger.info("[Part %d - Step 2/5] Synthesizing voiceover...", p_num)
                tts_result = generate_voiceover(
                    text=p_narration,
                    gender=story.narrator_gender,
                    output_dir=temp_run_dir,
                    filename_prefix=f"voiceover_{timestamp_str}_part{p_num}",
                )
                logger.info(
                    "Part %d Voiceover generated: %.2f seconds using voice '%s' (%d words tracked)",
                    p_num,
                    tts_result.duration_seconds,
                    tts_result.voice_used,
                    len(tts_result.word_timings),
                )

                # Calculate authentic Reddit Card visual hook duration based on title/intro narration
                if p_num == 1:
                    title_words = len(story.title.split())
                    card_dur = tts_result.word_timings[title_words - 1].end if (tts_result.word_timings and len(tts_result.word_timings) >= title_words) else 3.6
                else:
                    intro_words = 8  # 'Part two. Picking up right where we left off.'
                    card_dur = tts_result.word_timings[intro_words - 1].end if (tts_result.word_timings and len(tts_result.word_timings) >= intro_words) else 3.2
                card_dur = max(2.5, min(5.5, float(card_dur)))

                logger.info("[Part %d - Step 2.5/5] Generating Dark-Mode Reddit Card hook graphic...", p_num)
                card_path = temp_run_dir / f"reddit_card_{timestamp_str}_part{p_num}.png"
                generate_reddit_card(
                    title=story.title,
                    subreddit=story.subreddit or f"r/{matched_cat}",
                    part_num=p_num,
                    category=matched_cat,
                    output_path=card_path,
                )

                logger.info("[Part %d - Step 3/5] Generating TikTok subtitles (synchronized after Reddit Card fade)...", p_num)
                ass_path = temp_run_dir / f"captions_{timestamp_str}_part{p_num}.ass"
                generate_ass_subtitles(
                    word_timings=tts_result.word_timings,
                    output_ass_path=ass_path,
                    highlight_color=highlight_color,
                    story_title=p_title,
                    category_tag=p_badge,
                    skip_until_seconds=card_dur,
                    show_header_badge=False,
                )
                logger.info("Part %d subtitles written to %s", p_num, ass_path)

                logger.info("[Part %d - Step 4/5] Extracting '%s' background segment...", p_num, bg_style)
                raw_bg_path = temp_run_dir / f"background_{timestamp_str}_part{p_num}.mp4"
                download_background_clip(
                    target_duration=tts_result.duration_seconds,
                    output_path=raw_bg_path,
                    bg_style=bg_style,
                )

                logger.info("[Part %d - Step 5/5] Compositing vertical 1080x1920 video with Reddit Card hook...", p_num)
                part_video_path = config.output_dir / f"tiktok_video_{timestamp_str}_part{p_num}.mp4"
                render_final_video(
                    background_video=raw_bg_path,
                    voiceover_audio=tts_result.audio_path,
                    subtitles_ass=ass_path,
                    output_video=part_video_path,
                    duration=tts_result.duration_seconds,
                    background_music=bg_music_path,
                    reddit_card_image=card_path,
                    reddit_card_duration=card_dur,
                )
                logger.info("Part %d video rendered successfully: %s", p_num, part_video_path)

                rendered_parts.append({
                    "part_num": p_num,
                    "title": p_title,
                    "video_path": part_video_path,
                    "duration": round(tts_result.duration_seconds, 2),
                    "tts_result": tts_result,
                })

            # Step 6: Multi-Part Publishing
            published_parts_summary = []
            for idx, p_info in enumerate(rendered_parts):
                p_num = p_info["part_num"]
                p_video = p_info["video_path"]
                p_title = p_info["title"]
                upload_res = {"status": "skipped"}
                if not skip_upload:
                    if idx > 0:
                        logger.info("Waiting 15 seconds before uploading Part %d to prevent TikTok concurrency rate limits...", p_num)
                        time.sleep(15)
                    logger.info("Publishing Part %d to TikTok: '%s'...", p_num, p_title)
                    upload_res = publish_to_tiktok(
                        video_path=p_video,
                        title=p_title,
                        dry_run=dry_run,
                    )
                    logger.info("Part %d publish result: %s", p_num, upload_res)
                    has_creds = bool(config.tiktok_access_token or config.tiktok_session_id or config.tiktok_cookies)
                    if not dry_run and has_creds and upload_res.get("status") not in ("success", "simulated"):
                        fail_reason = upload_res.get("reason", "unknown")
                        fail_msg = upload_res.get("message", "Upload failed")
                        print(f"::error title=TikTok Upload Part {p_num} Failed::[{fail_reason}] {fail_msg}")
                        if config.fail_on_upload_error:
                            raise RuntimeError(f"TikTok upload failed for Part {p_num} ({fail_reason}): {fail_msg}")

                published_parts_summary.append({
                    "part": p_num,
                    "title": p_title,
                    "video_path": str(p_video),
                    "video_size_bytes": p_video.stat().st_size if p_video.exists() else 0,
                    "duration_seconds": p_info["duration"],
                    "upload": upload_res,
                })

            overall_status = "completed"
            if not skip_upload and any(p["upload"].get("status") not in ("success", "simulated", "skipped") for p in published_parts_summary):
                overall_status = "upload_failed"

            summary = {
                "timestamp": timestamp_str,
                "status": overall_status,
                "category": matched_cat,
                "bg_style": bg_style,
                "length_mode": "multipart",
                "story": {
                    "title": story.title,
                    "gender": story.narrator_gender,
                    "total_words": story.word_count,
                    "source": story.source,
                    "subreddit": story.subreddit,
                },
                "parts": published_parts_summary,
            }
            with open(summary_path, "w", encoding="utf-8") as f_sum:
                json.dump(summary, f_sum, indent=2)

            logger.info("Multi-part run summary saved to %s", summary_path)
            logger.info("=========================================================")
            logger.info("Both Part 1 and Part 2 generated and published successfully!")
            logger.info("=========================================================")
            return rendered_parts[0]["video_path"]

        else:
            # Single-video pipeline mode (short or full)
            final_video_path = config.output_dir / f"tiktok_video_{timestamp_str}.mp4"
            narration_text = story.full_narration_text()

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

            title_words = len(story.title.split())
            card_dur = tts_result.word_timings[title_words - 1].end if (tts_result.word_timings and len(tts_result.word_timings) >= title_words) else 3.6
            card_dur = max(2.5, min(5.5, float(card_dur)))

            logger.info("[Step 2.5/5] Generating Dark-Mode Reddit Card hook graphic...")
            card_path = temp_run_dir / f"reddit_card_{timestamp_str}.png"
            generate_reddit_card(
                title=story.title,
                subreddit=story.subreddit or f"r/{matched_cat}",
                part_num=1,
                category=matched_cat,
                output_path=card_path,
            )

            logger.info("[Step 3/5] Generating TikTok animated word-highlight subtitles...")
            ass_path = temp_run_dir / f"captions_{timestamp_str}.ass"
            generate_ass_subtitles(
                word_timings=tts_result.word_timings,
                output_ass_path=ass_path,
                highlight_color=highlight_color,
                story_title=story.title,
                category_tag=category_label,
                skip_until_seconds=card_dur,
                show_header_badge=False,
            )
            logger.info("Subtitles written to %s (highlight color: %s)", ass_path, highlight_color)

            logger.info("[Step 4/5] Extracting random '%s' background segment...", bg_style)
            raw_bg_path = temp_run_dir / f"background_{timestamp_str}.mp4"
            download_background_clip(
                target_duration=tts_result.duration_seconds,
                output_path=raw_bg_path,
                bg_style=bg_style,
            )

            logger.info("[Step 5/5] Compositing final vertical 1080x1920 video with Reddit Card hook...")
            render_final_video(
                background_video=raw_bg_path,
                voiceover_audio=tts_result.audio_path,
                subtitles_ass=ass_path,
                output_video=final_video_path,
                duration=tts_result.duration_seconds,
                background_music=bg_music_path,
                reddit_card_image=card_path,
                reddit_card_duration=card_dur,
            )
            logger.info("Final video successfully rendered: %s", final_video_path)

            upload_result = {"status": "skipped"}
            if not skip_upload:
                logger.info("Publishing generated video to TikTok...")
                upload_result = publish_to_tiktok(
                    video_path=final_video_path,
                    title=story.title,
                    dry_run=dry_run,
                )
                logger.info("Publish result: %s", upload_result)
                has_creds = bool(config.tiktok_access_token or config.tiktok_session_id or config.tiktok_cookies)
                if not dry_run and has_creds and upload_result.get("status") not in ("success", "simulated"):
                    fail_reason = upload_result.get("reason", "unknown")
                    fail_msg = upload_result.get("message", "Upload failed")
                    print(f"::error title=TikTok Upload Failed::[{fail_reason}] {fail_msg}")
                    if config.fail_on_upload_error:
                        raise RuntimeError(f"TikTok upload failed ({fail_reason}): {fail_msg}")

            run_status = "completed" if upload_result.get("status") in ("success", "simulated", "skipped") else "upload_failed"
            summary = {
                "timestamp": timestamp_str,
                "status": run_status,
                "category": matched_cat,
                "bg_style": bg_style,
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
                "video_size_bytes": final_video_path.stat().st_size if final_video_path.exists() else 0,
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
        "--category",
        choices=[
            "auto", "love", "inheritance", "entitled", "revenge", "confession",
            "aitah", "facts", "scary", "showerthoughts", "wild_history", "workplace"
        ],
        default="auto",
        help="Story category: love, inheritance, entitled, revenge, confession, aitah, facts, scary, showerthoughts, wild_history, workplace.",
    )
    parser.add_argument(
        "--bg-style",
        choices=["auto", "minecraft", "subway_surfers", "gta", "satisfying", "beamng"],
        default="auto",
        help="Background gameplay style: 'minecraft', 'subway_surfers', 'gta', 'satisfying', 'beamng', or 'auto'.",
    )
    parser.add_argument(
        "--privacy",
        choices=["public", "self_only"],
        default="public",
        help="TikTok upload privacy: 'public' (Direct publication to feed, hands-free) or 'self_only'.",
    )
    parser.add_argument(
        "--length",
        choices=["multipart", "short", "full"],
        default="multipart",
        help="Story length mode: 'multipart' (Part 1 & Part 2 >=2m each with cliffhanger CTA and auto-publishing both parts), 'short' (~60-80s), or 'full' (~3.5m).",
    )
    parser.add_argument(
        "--include-bg-music",
        action="store_true",
        help="Mix background music into the MP4 (disabled by default for native in-app sound addition).",
    )
    parser.add_argument(
        "--fail-on-upload-error",
        action="store_true",
        help="Raise an error and fail the pipeline job if TikTok upload fails.",
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
            category=args.category,
            bg_style=args.bg_style,
            privacy=args.privacy,
            length=args.length,
            include_bg_music=args.include_bg_music,
            fail_on_upload_error=args.fail_on_upload_error,
            dry_run=args.dry_run,
            skip_upload=args.skip_upload,
            keep_temp=args.keep_temp,
        )
    except Exception as e:
        logger.exception("Pipeline failed with unhandled exception: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()

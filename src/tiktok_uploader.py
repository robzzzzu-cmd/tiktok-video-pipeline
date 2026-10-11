"""Automated TikTok publishing module supporting Official Content Posting API & Playwright fallback."""

from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Dict, Optional

import requests
from src.config import config

logger = logging.getLogger(__name__)


def build_caption(title: str, hashtags: Optional[list] = None) -> str:
    """Builds a TikTok caption combining the title and viral hashtags (keeping within length limits)."""
    tags = hashtags or config.hashtags
    tags_str = " ".join(tags)
    # Maximum TikTok caption length is typically 2200 characters, but 150-200 chars is ideal
    clean_title = title.strip()
    if len(clean_title) > 100:
        clean_title = clean_title[:97] + "..."
    return f"{clean_title}\n\n{tags_str}"


# -----------------------------------------------------------------------------
# Method A: TikTok Official Content Posting API (v2)
# -----------------------------------------------------------------------------

def refresh_tiktok_access_token() -> Optional[str]:
    """Refreshes the TikTok OAuth user access token using client credentials if configured."""
    if not (config.tiktok_client_key and config.tiktok_client_secret and config.tiktok_refresh_token):
        return None

    url = "https://open.tiktokapis.com/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "client_key": config.tiktok_client_key,
        "client_secret": config.tiktok_client_secret,
        "grant_type": "refresh_token",
        "refresh_token": config.tiktok_refresh_token,
    }

    try:
        response = requests.post(url, headers=headers, data=data, timeout=15)
        res_json = response.json()
        new_token = res_json.get("data", {}).get("access_token")
        if new_token:
            logger.info("Successfully refreshed TikTok access token.")
            config.tiktok_access_token = new_token
            return new_token
        logger.warning("Token refresh response error: %s", res_json)
    except Exception as e:
        logger.error("Failed to refresh TikTok access token: %s", e)

    return None


def upload_via_tiktok_api(video_path: Path, title: str) -> bool:
    """Uploads video using the TikTok Official Content Posting API v2."""
    if not config.tiktok_access_token:
        logger.info("No TIKTOK_ACCESS_TOKEN provided; skipping API upload method.")
        return False

    token = config.tiktok_access_token
    file_size = video_path.stat().st_size
    caption = build_caption(title)

    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    payload = {
        "post_info": {
            "title": caption,
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "disable_duet": False,
            "disable_stitch": False,
            "disable_comment": False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source": "FILE_UPLOAD",
            "video_size": file_size,
            "chunk_size": file_size,
            "total_chunk_count": 1,
        },
    }

    try:
        logger.info("Initializing upload with TikTok Official API...")
        res = requests.post(init_url, headers=headers, json=payload, timeout=20)
        res_data = res.json()

        # Check for expired token error
        error_info = res_data.get("error", {})
        if error_info.get("code") in ("access_token_invalid", "token_expired"):
            new_token = refresh_tiktok_access_token()
            if new_token:
                headers["Authorization"] = f"Bearer {new_token}"
                res = requests.post(init_url, headers=headers, json=payload, timeout=20)
                res_data = res.json()

        data = res_data.get("data")
        if not data or "upload_url" not in data:
            logger.warning("TikTok API init returned error: %s", res_data)
            return False

        upload_url = data["upload_url"]
        publish_id = data.get("publish_id")
        logger.info("TikTok upload initialized. Publish ID: %s", publish_id)

        # Upload the video file chunk
        with open(video_path, "rb") as f_vid:
            video_bytes = f_vid.read()

        upload_headers = {
            "Content-Type": "video/mp4",
            "Content-Range": f"bytes 0-{file_size - 1}/{file_size}",
            "Content-Length": str(file_size),
        }
        put_res = requests.put(upload_url, headers=upload_headers, data=video_bytes, timeout=120)
        if put_res.status_code not in (200, 201, 206):
            logger.warning("TikTok chunk upload failed with HTTP %d: %s", put_res.status_code, put_res.text)
            return False

        logger.info("Successfully uploaded video chunk to TikTok.")

        # Poll publish status
        if publish_id:
            status_url = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
            status_headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
            }
            status_payload = {"publish_id": publish_id}

            for _ in range(6):
                time.sleep(5)
                st_res = requests.post(status_url, headers=status_headers, json=status_payload, timeout=15)
                st_data = st_res.json().get("data", {})
                status_str = st_data.get("status")
                logger.info("Publish status for %s: %s", publish_id, status_str)
                if status_str in ("PUBLISH_COMPLETE", "SUCCESS"):
                    return True
                if status_str in ("FAILED", "PUBLISH_FAILED"):
                    logger.error("TikTok publish failed: %s", st_data.get("fail_reason"))
                    return False

        return True

    except Exception as e:
        logger.error("TikTok API upload failed with exception: %s", e)
        return False


# -----------------------------------------------------------------------------
# Method B: Playwright Headless Browser Automation (Fallback)
# -----------------------------------------------------------------------------

def upload_via_playwright(video_path: Path, title: str) -> bool:
    """Uploads video using Playwright headless browser with session cookies."""
    if not config.tiktok_session_id:
        logger.info("No TIKTOK_SESSION_ID provided; skipping Playwright upload.")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is not installed; skipping browser fallback.")
        return False

    caption = build_caption(title)
    session_id = config.tiktok_session_id.strip()

    logger.info("Starting headless Playwright upload with TikTok session cookie...")
    screenshot_dir = config.temp_dir / "playwright_debug"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )

            # Set TikTok session cookies
            context.add_cookies([
                {
                    "name": "sessionid",
                    "value": session_id,
                    "domain": ".tiktok.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                },
                {
                    "name": "sessionid_ss",
                    "value": session_id,
                    "domain": ".tiktok.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                },
            ])

            page = context.new_page()
            upload_url = "https://www.tiktok.com/creator-center/upload"
            logger.info("Navigating to %s...", upload_url)
            page.goto(upload_url, wait_until="networkidle", timeout=45000)

            # Check if logged in or redirected to login
            if "login" in page.url.lower():
                logger.error("Session cookie appears expired; redirected to login page: %s", page.url)
                page.screenshot(path=str(screenshot_dir / "login_redirect.png"))
                browser.close()
                return False

            # Find file upload input element
            logger.info("Attaching video file: %s", video_path)
            file_input = page.wait_for_selector('input[type="file"]', timeout=30000)
            if not file_input:
                logger.error("Could not find file input element on page.")
                page.screenshot(path=str(screenshot_dir / "missing_file_input.png"))
                browser.close()
                return False

            file_input.set_input_files(str(video_path))
            logger.info("Video file selected. Waiting for upload to process...")

            # Wait for upload indicator / caption editor
            page.wait_for_timeout(8000)

            # Locate caption editor container (supports both contenteditable and input/textarea)
            caption_selector = (
                'div[contenteditable="true"], div.DraftEditor-root, textarea[placeholder*="caption"]'
            )
            caption_el = page.query_selector(caption_selector)
            if caption_el:
                logger.info("Entering caption and hashtags...")
                caption_el.click()
                # Clear existing text if any
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                caption_el.fill(caption)
            else:
                logger.warning("Could not find dedicated caption element; trying generic input.")

            page.wait_for_timeout(5000)

            # Locate and click Post button
            post_button = (
                page.query_selector('button:has-text("Post")')
                or page.query_selector('button[data-e2e="post_video_button"]')
                or page.query_selector('div.btn-post button')
            )

            if post_button:
                logger.info("Clicking Post button...")
                post_button.click()
                page.wait_for_timeout(10000)
                page.screenshot(path=str(screenshot_dir / "after_post.png"))
                logger.info("Post button clicked successfully.")
                browser.close()
                return True
            else:
                logger.warning("Post button not found directly.")
                page.screenshot(path=str(screenshot_dir / "missing_post_button.png"))
                browser.close()
                return False

    except Exception as e:
        logger.error("Playwright upload failed: %s", e)
        return False


# -----------------------------------------------------------------------------
# Master Publishing Router
# -----------------------------------------------------------------------------

def publish_to_tiktok(video_path: Path, title: str, dry_run: bool = False) -> Dict[str, str]:
    """Publishes video to TikTok using the configured primary method with automated fallback.

    Args:
        video_path: Local path to the rendered video file.
        title: Video title used for caption and hashtags.
        dry_run: When True, simulates upload without calling TikTok APIs or browser.

    Returns:
        Dict detailing upload status and method used.
    """
    if dry_run:
        logger.info("[DRY-RUN] Simulating TikTok upload for '%s'. No network calls made.", video_path.name)
        return {
            "status": "simulated",
            "method": "dry_run",
            "video_path": str(video_path),
            "caption": build_caption(title),
        }

    # 1. Try Official TikTok API (Method A)
    if config.tiktok_access_token:
        logger.info("Attempting TikTok Official Content Posting API (Method A)...")
        if upload_via_tiktok_api(video_path, title):
            return {
                "status": "success",
                "method": "official_api",
                "video_path": str(video_path),
            }
        logger.warning("Method A failed; falling back to Method B (Playwright)...")

    # 2. Try Playwright Session Automation (Method B)
    if config.tiktok_session_id:
        logger.info("Attempting TikTok Playwright Browser Automation (Method B)...")
        if upload_via_playwright(video_path, title):
            return {
                "status": "success",
                "method": "playwright_session",
                "video_path": str(video_path),
            }

    # No valid publish credentials found or both failed
    logger.warning("No TikTok credentials configured or uploads failed. Video is saved locally in %s", video_path)
    return {
        "status": "skipped_or_unauthenticated",
        "method": "none",
        "video_path": str(video_path),
        "note": "Configure TIKTOK_ACCESS_TOKEN or TIKTOK_SESSION_ID in secrets to enable auto-posting.",
    }

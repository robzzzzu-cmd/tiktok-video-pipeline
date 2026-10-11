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
    """Builds a TikTok caption combining the story title as description and viral hashtags."""
    tags = hashtags or config.hashtags
    tags_str = " ".join(tags)
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
            # Remove navigator.webdriver flag
            context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            # Set TikTok authentication cookies
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
                {
                    "name": "sid_tt",
                    "value": session_id,
                    "domain": ".tiktok.com",
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                },
            ])

            page = context.new_page()
            upload_url = "https://www.tiktok.com/tiktokstudio/upload"
            logger.info("Navigating to %s...", upload_url)
            try:
                page.goto(upload_url, wait_until="networkidle", timeout=50000)
            except Exception as nav_e:
                logger.info("Page navigation completed with status: %s", nav_e)

            page.screenshot(path=str(screenshot_dir / "01_page_loaded.png"))

            # Check if redirected to login
            if "login" in page.url.lower():
                logger.error("Session cookie appears expired or invalid; redirected to login: %s", page.url)
                page.screenshot(path=str(screenshot_dir / "01_login_redirect.png"))
                browser.close()
                return False

            # Find file upload input element (supports both main page and embedded iframes)
            logger.info("Locating file upload element for: %s", video_path.name)
            file_input = None
            for _ in range(6):
                try:
                    file_input = page.wait_for_selector('input[type="file"]', timeout=4000)
                    if file_input:
                        break
                except Exception:
                    pass

                for frame in page.frames:
                    try:
                        file_input = frame.query_selector('input[type="file"]')
                        if file_input:
                            logger.info("Found input[type='file'] inside frame: %s", frame.name or frame.url)
                            break
                    except Exception:
                        pass
                if file_input:
                    break
                time.sleep(2)

            if not file_input:
                logger.error("Could not find file input element on page or iframes.")
                page.screenshot(path=str(screenshot_dir / "missing_file_input.png"))
                browser.close()
                return False

            file_input.set_input_files(str(video_path))
            logger.info("Video file attached. Waiting for TikTok to process upload...")
            page.screenshot(path=str(screenshot_dir / "02_file_selected.png"))

            # Wait for upload indicator / caption editor
            page.wait_for_timeout(8000)

            # Locate caption editor container (supports both contenteditable and input/textarea across frames)
            caption_selector = (
                'div[contenteditable="true"], div.DraftEditor-root, div.notranslate[contenteditable="true"], '
                'textarea[placeholder*="caption"], div[data-placeholder]'
            )
            caption_el = page.query_selector(caption_selector)
            if not caption_el:
                for frame in page.frames:
                    caption_el = frame.query_selector(caption_selector)
                    if caption_el:
                        break

            if caption_el:
                logger.info("Entering caption and viral hashtags into description editor...")
                caption_el.click()
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                page.wait_for_timeout(500)
                caption_el.fill(caption)
                page.wait_for_timeout(1000)
            else:
                logger.warning("Could not find dedicated caption element; checking generic inputs.")

            page.screenshot(path=str(screenshot_dir / "03_caption_entered.png"))

            # Wait for Post button to become enabled (wait up to 75s for video upload/transcoding)
            logger.info("Waiting for video upload to finalize and Post button to become enabled...")
            post_button = None
            for attempt in range(25):
                for target in [page] + page.frames:
                    btn = (
                        target.query_selector('button:has-text("Post")')
                        or target.query_selector('button[data-e2e="post_video_button"]')
                        or target.query_selector('div.btn-post button')
                        or target.query_selector('button.btn-post')
                    )
                    if btn:
                        is_disabled = (
                            btn.get_attribute("disabled") is not None
                            or btn.get_attribute("aria-disabled") == "true"
                            or "disabled" in (btn.get_attribute("class") or "").lower()
                        )
                        if not is_disabled:
                            post_button = btn
                            break
                if post_button:
                    break
                time.sleep(3)

            if post_button:
                logger.info("Post button is enabled! Clicking Post...")
                post_button.click()
                page.wait_for_timeout(8000)
                page.screenshot(path=str(screenshot_dir / "04_after_post.png"))

                # Check for post-upload modals or confirm dialogs
                for confirm_label in ["Manage your posts", "View video", "Confirm", "Post anyway"]:
                    confirm_el = page.query_selector(f'button:has-text("{confirm_label}")')
                    if confirm_el:
                        logger.info("Clicked confirmation dialog: '%s'", confirm_label)
                        confirm_el.click()
                        page.wait_for_timeout(3000)

                page.screenshot(path=str(screenshot_dir / "05_final_published.png"))
                logger.info("Playwright auto-publish workflow completed successfully.")
                browser.close()
                return True
            else:
                logger.warning("Post button was not enabled within the timeout.")
                page.screenshot(path=str(screenshot_dir / "post_timeout.png"))
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

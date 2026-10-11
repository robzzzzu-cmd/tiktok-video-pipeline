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

def parse_cookie_data(cookie_input: str) -> list[dict]:
    """Parses cookie data from JSON array, Netscape format, or cookie header string."""
    if not cookie_input:
        return []

    cookie_input = cookie_input.strip()

    # 1. Try JSON format (e.g. from Cookie-Editor 'Export as JSON')
    try:
        data = json.loads(cookie_input)
        if isinstance(data, list):
            cleaned = []
            for c in data:
                if isinstance(c, dict) and "name" in c and "value" in c:
                    cookie_dict = {
                        "name": str(c["name"]).strip(),
                        "value": str(c["value"]).strip(),
                        "domain": str(c.get("domain", ".tiktok.com")),
                        "path": str(c.get("path", "/")),
                    }
                    if "secure" in c:
                        cookie_dict["secure"] = bool(c["secure"])
                    if "httpOnly" in c:
                        cookie_dict["httpOnly"] = bool(c["httpOnly"])
                    cleaned.append(cookie_dict)
            if cleaned:
                logger.info("Successfully parsed %d cookies from JSON format.", len(cleaned))
                return cleaned
    except Exception:
        pass

    # 2. Try Netscape format (tab-separated lines from cookies.txt)
    netscape_cookies = []
    lines = cookie_input.splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            netscape_cookies.append({
                "domain": parts[0],
                "httpOnly": parts[1].upper() == "TRUE",
                "path": parts[2],
                "secure": parts[3].upper() == "TRUE",
                "name": parts[5],
                "value": parts[6],
            })
    if netscape_cookies:
        logger.info("Successfully parsed %d cookies from Netscape cookies.txt format.", len(netscape_cookies))
        return netscape_cookies

    # 3. Try Header String format: 'name=value; name2=value2'
    header_cookies = []
    pairs = cookie_input.split(";")
    for pair in pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and v:
                header_cookies.append({
                    "name": k,
                    "value": v,
                    "domain": ".tiktok.com",
                    "path": "/",
                })
    if header_cookies:
        logger.info("Successfully parsed %d cookies from header string format.", len(header_cookies))
        return header_cookies

    return []


def upload_via_playwright(video_path: Path, title: str) -> bool:
    """Uploads video using Playwright headless browser with session cookies or full cookie export."""
    has_session = bool(config.tiktok_session_id and config.tiktok_session_id.strip())
    has_cookies = bool(config.tiktok_cookies and config.tiktok_cookies.strip())

    if not (has_session or has_cookies):
        logger.info("No TIKTOK_SESSION_ID or TIKTOK_COOKIES provided; skipping Playwright upload.")
        return False

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is not installed; skipping browser fallback.")
        return False

    caption = build_caption(title)
    screenshot_dir = config.temp_dir / "playwright_debug"
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # Prepare cookies list
    cookies_to_inject = []
    if has_cookies:
        cookies_to_inject = parse_cookie_data(config.tiktok_cookies or "")

    if not cookies_to_inject and has_session:
        session_id = (config.tiktok_session_id or "").strip()
        cookies_to_inject = [
            {"name": "sessionid", "value": session_id, "domain": ".tiktok.com", "path": "/", "httpOnly": True, "secure": True},
            {"name": "sessionid_ss", "value": session_id, "domain": ".tiktok.com", "path": "/", "httpOnly": True, "secure": True},
            {"name": "sid_tt", "value": session_id, "domain": ".tiktok.com", "path": "/", "httpOnly": True, "secure": True},
        ]

    logger.info("Starting headless Playwright upload with %d authenticated cookies...", len(cookies_to_inject))

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

            # Set cookies into context
            if cookies_to_inject:
                context.add_cookies(cookies_to_inject)

            page = context.new_page()

            # First navigate to homepage to establish session tokens
            try:
                logger.info("Establishing initial TikTok session...")
                page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(2000)
            except Exception as e_home:
                logger.info("Homepage handshake note: %s", e_home)

            # Re-ensure cookies are set
            if cookies_to_inject:
                try:
                    context.add_cookies(cookies_to_inject)
                except Exception:
                    pass

            # Try upload portals
            upload_urls = [
                "https://www.tiktok.com/tiktokstudio/upload",
                "https://www.tiktok.com/upload",
                "https://www.tiktok.com/creator-center/upload",
            ]
            upload_loaded = False
            for target_url in upload_urls:
                logger.info("Navigating to upload portal: %s...", target_url)
                try:
                    page.goto(target_url, wait_until="networkidle", timeout=40000)
                except Exception:
                    pass

                page.screenshot(path=str(screenshot_dir / "01_page_loaded.png"))

                if "login" not in page.url.lower():
                    upload_loaded = True
                    break
                logger.warning("Portal %s bounced to login; trying alternative...", target_url)

            if not upload_loaded or "login" in page.url.lower():
                logger.error("Session cookies rejected by TikTok; redirected to login: %s", page.url)
                page.screenshot(path=str(screenshot_dir / "01_login_redirect.png"))
                browser.close()
                return False

            # Find file upload input element (supports both main page and embedded iframes)
            logger.info("Locating file upload element for: %s", video_path.name)
            file_input = None
            upload_frame = None

            for _ in range(6):
                try:
                    file_input = page.wait_for_selector('input[type="file"]', timeout=4000)
                    if file_input:
                        upload_frame = page
                        break
                except Exception:
                    pass

                for frame in page.frames:
                    try:
                        file_input = frame.query_selector('input[type="file"]')
                        if file_input:
                            logger.info("Found input[type='file'] inside frame: %s", frame.name or frame.url)
                            upload_frame = frame
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

            # Wait for upload indicator / caption editor to render
            page.wait_for_timeout(10000)

            # Prioritize searching inside the active upload frame where file was selected
            search_scopes = ([upload_frame] if upload_frame and upload_frame != page else []) + [page] + page.frames

            # Dismiss any guide popups, cookie banners, or modal overlays
            for scope in search_scopes:
                for dismiss_selector in [
                    'button:has-text("Got it")',
                    'button:has-text("Close")',
                    'button:has-text("Dismiss")',
                    'div[role="dialog"] button',
                    'button[aria-label="Close"]',
                ]:
                    try:
                        btn = scope.query_selector(dismiss_selector)
                        if btn:
                            btn.click(timeout=1500, force=True)
                    except Exception:
                        pass

            # Dismiss any accidental exit dialog if already showing
            for scope in search_scopes:
                try:
                    cancel_btn = scope.query_selector('div[role="dialog"] button:has-text("Cancel")')
                    if cancel_btn:
                        cancel_btn.click(timeout=1500, force=True)
                except Exception:
                    pass

            # Locate caption editor container (supports DraftEditor, contenteditable, textarea)
            caption_selector = (
                'div[contenteditable="true"], div.DraftEditor-editorContainer div[contenteditable="true"], '
                'div.notranslate[contenteditable="true"], textarea[placeholder*="caption"], textarea'
            )
            caption_el = None
            for scope in search_scopes:
                try:
                    caption_el = scope.query_selector(caption_selector)
                    if caption_el:
                        break
                except Exception:
                    pass

            clean_title = title.strip()
            if len(clean_title) > 100:
                clean_title = clean_title[:97] + "..."
            tags_str = " ".join(config.hashtags)

            if caption_el:
                logger.info("Entering caption and viral hashtags into description editor...")
                try:
                    caption_el.click(timeout=4000, force=True)
                    page.wait_for_timeout(300)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.wait_for_timeout(200)
                    page.keyboard.type(clean_title, delay=10)
                    page.keyboard.press("Enter")
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(100)
                    page.keyboard.type(tags_str, delay=10)
                    page.wait_for_timeout(500)
                except Exception as e_fill:
                    logger.warning("Keyboard typing notice (%s); attempting evaluate text dispatch...", e_fill)
                    try:
                        active_scope = upload_frame or page
                        full_caption = f"{clean_title}\n\n{tags_str}"
                        active_scope.evaluate(
                            """(text) => {
                                const el = document.querySelector('div[contenteditable="true"], textarea');
                                if (el) {
                                    el.focus();
                                    document.execCommand('selectAll', false, null);
                                    document.execCommand('insertText', false, text);
                                }
                            }""",
                            full_caption,
                        )
                    except Exception as e_ev:
                        logger.warning("Could not set caption via evaluate: %s", e_ev)
            else:
                logger.warning("Could not find dedicated caption element; checking generic inputs.")

            page.screenshot(path=str(screenshot_dir / "03_caption_entered.png"))

            # Scroll down to reveal bottom footer action bar (Post button)
            logger.info("Scrolling down to reveal footer action buttons...")
            for s in search_scopes:
                try:
                    s.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    s.evaluate("""() => {
                        const els = document.querySelectorAll('div, main, section');
                        for (const el of els) {
                            if (el.scrollHeight > el.clientHeight && el.clientHeight > 200) {
                                el.scrollTop = el.scrollHeight;
                            }
                        }
                    }""")
                except Exception:
                    pass
            page.wait_for_timeout(1500)

            # Helper to find authentic Post button (excludes sidebar nav)
            def find_real_post_button(scopes):
                # Dismiss accidental exit dialog if showing
                for s in scopes:
                    try:
                        cancel_exit = s.query_selector('div[role="dialog"] button:has-text("Cancel")')
                        if cancel_exit:
                            logger.info("Dismissing accidental exit confirmation dialog...")
                            cancel_exit.click(timeout=1500, force=True)
                            page.wait_for_timeout(500)
                    except Exception:
                        pass

                post_selectors = [
                    'button[data-e2e="post_video_button"]',
                    'div[class*="btn-post"] button',
                    'button[class*="btn-post"]',
                    'div[class*="button-group"] button',
                    'div[class*="btn-group"] button',
                    'div[class*="footer"] button',
                    'div[class*="action"] button',
                    'button:text-is("Post")',
                    'button',
                ]
                for s in scopes:
                    for sel in post_selectors:
                        try:
                            candidates = s.query_selector_all(sel)
                            for btn in candidates:
                                text = (btn.inner_text() or "").strip()
                                if text.upper() != "POST":
                                    continue
                                box = btn.bounding_box()
                                if not box:
                                    continue
                                # Exclude left navigation sidebar (x is 0..100px)
                                if box["x"] < 120:
                                    continue
                                # Must not be disabled
                                is_disabled = (
                                    btn.get_attribute("disabled") is not None
                                    or btn.get_attribute("aria-disabled") == "true"
                                    or "disabled" in (btn.get_attribute("class") or "").lower()
                                )
                                if not is_disabled:
                                    logger.info(
                                        "Found authentic Post button: selector='%s', text='%s', box=%s",
                                        sel,
                                        text,
                                        box,
                                    )
                                    return btn
                        except Exception:
                            pass
                return None

            # Wait for Post button to become enabled (wait up to 90s for video transcoding)
            logger.info("Waiting for video upload to finalize and authentic Post button to become enabled...")
            post_button = None
            for attempt in range(30):
                post_button = find_real_post_button(search_scopes)
                if post_button:
                    break
                # Periodically re-scroll in case dynamic content shifted
                if attempt % 3 == 0:
                    for s in search_scopes:
                        try:
                            s.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        except Exception:
                            pass
                time.sleep(3)

            if post_button:
                logger.info("Authentic Post button is enabled! Clicking Post...")
                try:
                    post_button.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    post_button.click(timeout=8000, force=True)
                except Exception:
                    post_button.click(force=True)

                page.wait_for_timeout(8000)
                page.screenshot(path=str(screenshot_dir / "04_after_post.png"))

                # Check if exit dialog popped up; if so, dismiss and retry post
                for s in search_scopes:
                    try:
                        cancel_exit = s.query_selector('div[role="dialog"] button:has-text("Cancel")')
                        if cancel_exit:
                            logger.warning("Exit dialog appeared; dismissing Cancel and re-clicking Post button...")
                            cancel_exit.click(timeout=1500, force=True)
                            page.wait_for_timeout(1000)
                            retry_btn = find_real_post_button(search_scopes)
                            if retry_btn:
                                retry_btn.scroll_into_view_if_needed()
                                retry_btn.click(force=True)
                                page.wait_for_timeout(6000)
                    except Exception:
                        pass

                # Check for post-upload modals or confirm dialogs
                for target in search_scopes:
                    for confirm_label in ["Manage your posts", "Upload another video", "View video", "Post anyway"]:
                        try:
                            confirm_el = target.query_selector(f'button:has-text("{confirm_label}")')
                            if confirm_el:
                                logger.info("Detected post-upload dialog button: '%s'", confirm_label)
                                page.wait_for_timeout(1000)
                        except Exception:
                            pass

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
    if config.tiktok_session_id or config.tiktok_cookies:
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
        "note": "Configure TIKTOK_ACCESS_TOKEN, TIKTOK_COOKIES, or TIKTOK_SESSION_ID in secrets to enable auto-posting.",
    }

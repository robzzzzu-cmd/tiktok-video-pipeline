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
    tags = list(hashtags or config.hashtags)
    # Auto-tag part tags for viral search discovery & profile cross-traffic
    if "[part 1]" in title.lower() and "#part1" not in [t.lower() for t in tags]:
        tags.insert(0, "#part1")
    elif "[part 2]" in title.lower() and "#part2" not in [t.lower() for t in tags]:
        tags.insert(0, "#part2")

    tags_str = " ".join(tags)
    clean_title = title.strip()
    if len(clean_title) > 100:
        clean_title = clean_title[:97] + "..."
    return f"{clean_title}\n\n{tags_str}"


def safe_screenshot(target, path: Path, timeout: int = 5000) -> None:
    """Takes a non-fatal debug screenshot without crashing if web fonts or network requests stall."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        target.screenshot(path=str(path), timeout=timeout)
    except Exception as e_ss:
        logger.debug("Debug screenshot skipped (%s): %s", path.name, e_ss)


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


def upload_via_tiktok_api(video_path: Path, title: str):
    """Uploads video using the TikTok Official Content Posting API v2."""
    if not config.tiktok_access_token:
        logger.info("No TIKTOK_ACCESS_TOKEN provided; skipping API upload method.")
        return False, "no_access_token", "No TIKTOK_ACCESS_TOKEN configured"

    token = config.tiktok_access_token
    file_size = video_path.stat().st_size
    caption = build_caption(title)

    init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }
    privacy_setting = (
        "SELF_ONLY"
        if config.tiktok_privacy_level in ("SELF_ONLY", "PRIVATE", "private", "self", "draft")
        else "PUBLIC_TO_EVERYONE"
    )
    payload = {
        "post_info": {
            "title": caption,
            "privacy_level": privacy_setting,
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
            return False, "api_init_error", f"TikTok API init error: {res_data}"

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
            return False, "chunk_upload_failed", f"HTTP {put_res.status_code}: {put_res.text}"

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
                    return True, "success", "Published via TikTok Official API"
                if status_str in ("FAILED", "PUBLISH_FAILED"):
                    logger.error("TikTok publish failed: %s", st_data.get("fail_reason"))
                    return False, "api_publish_failed", str(st_data.get("fail_reason"))

        return True, "success", "Published via TikTok Official API"

    except Exception as e:
        logger.error("TikTok API upload failed with exception: %s", e)
        return False, "api_exception", str(e)


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
                    raw_domain = str(c.get("domain", ".tiktok.com")).strip()
                    if raw_domain in ("www.tiktok.com", "tiktok.com") or ("tiktok.com" in raw_domain and not raw_domain.startswith(".")):
                        raw_domain = ".tiktok.com"

                    cookie_dict = {
                        "name": str(c["name"]).strip(),
                        "value": str(c["value"]).strip(),
                        "domain": raw_domain,
                        "path": str(c.get("path", "/")),
                    }
                    if "secure" in c:
                        cookie_dict["secure"] = bool(c["secure"])
                    if "httpOnly" in c:
                        cookie_dict["httpOnly"] = bool(c["httpOnly"])

                    same_site = str(c.get("sameSite", "")).capitalize()
                    if same_site in ("Strict", "Lax", "None"):
                        cookie_dict["sameSite"] = same_site

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

    # 3. Try Header String format: 'Cookie: name=value; name2=value2' or 'name=value; name2=value2'
    raw_str = cookie_input
    if raw_str.lower().startswith("cookie:"):
        raw_str = raw_str[7:].strip()
    if "-H 'cookie:" in raw_str.lower() or '-h "cookie:' in raw_str.lower() or "-h 'cookie:" in raw_str.lower():
        import re
        m = re.search(r'-H\s+[\'"]cookie:\s*([^\'"]+)[\'"]', raw_str, re.IGNORECASE)
        if m:
            raw_str = m.group(1).strip()

    header_cookies = []
    pairs = raw_str.split(";")
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


def verify_and_resolve_proxy(
    raw_server: str,
    raw_username: str = "",
    raw_password: str = "",
) -> tuple[dict | None, str, str]:
    """
    Parses and pre-flight probes proxy connectivity before launching Playwright.
    - Extracts host, port, scheme, and credentials
    - Tests candidate ports (auto-fallbacks from port 80 to 8080 if Azure blocks port 80 CONNECT)
    - Tests candidate username suffixes (e.g. -us for Webshare US geo-targeting)
    - Verifies egress IP and alerts if country blocks TikTok
    Returns: (playwright_proxy_dict_or_None, status_code, message)
    """
    if not raw_server or not raw_server.strip():
        return None, "empty_proxy", "No proxy server specified"

    raw = raw_server.strip()
    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks5://")):
        raw = f"http://{raw}"

    try:
        from urllib.parse import urlparse, quote
        parsed = urlparse(raw)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or ""
        port = parsed.port
        user = parsed.username or (raw_username.strip() if raw_username else "")
        pwd = parsed.password or (raw_password.strip() if raw_password else "")
    except Exception as e_p:
        return None, "invalid_proxy_url", f"Failed to parse proxy URL '{raw_server}': {e_p}"

    if not host:
        return None, "invalid_proxy_host", f"Could not extract valid hostname from '{raw_server}'"

    # Determine candidate ports
    candidate_ports = []
    if port:
        candidate_ports.append(port)
    else:
        candidate_ports.append(80)

    # For webshare.io, supported ports are 80, 3128, and 1080 (8080 is not supported on p.webshare.io)
    if "webshare" in host:
        for alt_port in (80, 3128, 1080):
            if alt_port not in candidate_ports:
                candidate_ports.append(alt_port)

    # Determine candidate usernames
    candidate_users = []
    if "webshare" in host and user:
        clean_user = user
        for suffix in ("-us-rotate", "-rotate-us", "-rotate", "-us", "-0", "-1"):
            if clean_user.endswith(suffix):
                clean_user = clean_user[:-len(suffix)]
                break
        # Webshare free/standard tier:
        # Priority 1: Persistent static proxy '-0' (keeps the EXACT same IP across all chunks and websockets, preventing ByteDance TOS upload failures)
        candidate_users.append(f"{clean_user}-0")
        # Priority 2: US-targeted rotating IP
        candidate_users.append(f"{clean_user}-us-rotate")
        # Priority 3: General rotating IP
        candidate_users.append(f"{clean_user}-rotate")
        # Priority 4: Raw user as provided
        if user not in candidate_users:
            candidate_users.append(user)
    elif user:
        candidate_users.append(user)
    else:
        candidate_users.append("")

    import requests

    last_error = ""
    for test_port in candidate_ports:
        for test_user in candidate_users:
            auth_str = f"{quote(test_user)}:{quote(pwd)}@" if (test_user and pwd) else ""
            # Proxy tunnel connection scheme to proxy gateway MUST be http://
            proxy_url = f"http://{auth_str}{host}:{test_port}"
            proxies = {"http": proxy_url, "https": proxy_url}

            user_disp = f"{test_user[:3]}***" if test_user else "none"
            logger.info("Probing proxy connectivity: %s://%s:%s (user: %s)...", scheme, host, test_port, user_disp)
            try:
                # 1. Test public IP reachability (short 7s timeout)
                resp = requests.get("https://api.ipify.org?format=json", proxies=proxies, timeout=7)
                if resp.status_code == 200:
                    ip_data = resp.json()
                    egress_ip = ip_data.get("ip", "unknown")
                    logger.info("Proxy pre-flight check SUCCESS via %s:%s! Public egress IP: %s", host, test_port, egress_ip)

                    # 2. Check egress country and timezone to ensure environment alignment
                    egress_country = "unknown"
                    egress_timezone = "America/Los_Angeles"
                    try:
                        c_resp = requests.get(f"http://ip-api.com/json/{egress_ip}?fields=countryCode,timezone", timeout=4)
                        if c_resp.status_code == 200:
                            c_data = c_resp.json()
                            egress_country = c_data.get("countryCode", "unknown")
                            egress_timezone = c_data.get("timezone", "America/Los_Angeles")
                            logger.info("Proxy egress country: %s | timezone: %s", egress_country, egress_timezone)
                            if egress_country.upper() in ("IN", "HK"):
                                logger.warning(
                                    "::warning::Proxy egress IP is located in '%s' where TikTok is blocked! "
                                    "Consider setting a US proxy or appending '-us' to your Webshare username.",
                                    egress_country,
                                )
                    except Exception:
                        pass

                    clean_server = f"{scheme}://{host}:{test_port}"
                    pw_dict = {"server": clean_server}
                    if test_user:
                        pw_dict["username"] = test_user
                    if pwd:
                        pw_dict["password"] = pwd
                    pw_dict["_timezone"] = egress_timezone

                    return pw_dict, "ok", f"Proxy verified ({clean_server}, egress: {egress_ip}, country: {egress_country}, tz: {egress_timezone})"
            except requests.exceptions.ProxyError as e_proxy:
                last_error = f"Proxy authentication / handshake failed ({e_proxy})"
                if "407" in str(e_proxy):
                    logger.error(
                        "::error::TikTok Proxy returned HTTP 407 (Proxy Authentication Required). "
                        "Verify TIKTOK_PROXY_USERNAME and TIKTOK_PROXY_PASSWORD in GitHub Secrets, "
                        "and ensure 'Password' authentication is enabled in your Webshare dashboard."
                    )
            except (requests.exceptions.ConnectTimeout, requests.exceptions.ReadTimeout, requests.exceptions.Timeout) as e_to:
                last_error = f"Proxy connection timed out on {host}:{test_port} ({e_to})"
                logger.warning("Proxy probe timed out on %s:%s", host, test_port)
            except Exception as e_other:
                last_error = f"Proxy probe error on {host}:{test_port}: {e_other}"
                logger.warning("Proxy probe error on %s:%s: %s", host, test_port, e_other)

    logger.error("All proxy probes failed! Last error: %s", last_error)
    return None, "proxy_failed", last_error


def upload_via_playwright(video_path: Path, title: str) -> bool:
    """Uploads video using Playwright headless browser with session cookies or full cookie export."""
    has_session = bool(config.tiktok_session_id and config.tiktok_session_id.strip())
    has_cookies = bool(config.tiktok_cookies and config.tiktok_cookies.strip())

    if not (has_session or has_cookies):
        logger.info("No TIKTOK_SESSION_ID or TIKTOK_COOKIES provided; skipping Playwright upload.")
        return False, "no_credentials", "No TIKTOK_SESSION_ID or TIKTOK_COOKIES provided"

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("Playwright is not installed; skipping browser fallback.")
        return False, "playwright_not_installed", "Playwright is not installed"

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

    # Configure and verify proxy if provided (critical for bypassing cloud datacenter IP blacklists)
    proxy_settings = None
    if config.tiktok_proxy_server:
        proxy_settings, p_status, p_msg = verify_and_resolve_proxy(
            config.tiktok_proxy_server,
            config.tiktok_proxy_username,
            config.tiktok_proxy_password,
        )
        if not proxy_settings:
            logger.error(
                "::error::TikTok Proxy Pre-flight check failed (%s). Aborting upload to prevent session revocation from datacenter IP.",
                p_msg,
            )
            return False, "proxy_failed", f"Proxy pre-flight failed: {p_msg}"
        logger.info("Configured Playwright with verified proxy: %s", proxy_settings.get("server"))

    try:
        with sync_playwright() as p:
            pw_proxy = {k: v for k, v in proxy_settings.items() if not k.startswith("_")} if proxy_settings else None
            browser = p.chromium.launch(
                headless=True,
                proxy=pw_proxy,
                args=[
                    "--headless=new",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                    "--window-size=1920,1080",
                ],
            )
            detected_tz = (proxy_settings.get("_timezone") if proxy_settings else None) or "America/Los_Angeles"
            # Emulate real Windows 10/11 64-bit Google Chrome (matching desktop session cookies)
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id=detected_tz,
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                },
            )
            # Comprehensive anti-detection & browser stealth injection
            context.add_init_script("""
                // Mask platform to Win32
                Object.defineProperty(Navigator.prototype, 'platform', {
                    get: () => 'Win32',
                    configurable: true
                });

                // Mask webdriver prototype
                Object.defineProperty(Navigator.prototype, 'webdriver', {
                    get: () => undefined,
                    configurable: true
                });

                // Mock window.chrome
                window.chrome = {
                    runtime: {
                        OnInstalledReason: { INSTALL: 'install', UPDATE: 'update', CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update' },
                        OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
                        PlatformOs: { MAC: 'mac', WIN: 'win', ANDROID: 'android', CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd' },
                        PlatformArch: { ARM: 'arm', ARM64: 'arm64', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' },
                        PlatformNaclArch: { ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64', MIPS: 'mips', MIPS64: 'mips64' }
                    },
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };

                // Mock navigator.plugins
                Object.defineProperty(Navigator.prototype, 'plugins', {
                    get: () => [
                        { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
                        { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' }
                    ]
                });

                // Mock languages
                Object.defineProperty(Navigator.prototype, 'languages', {
                    get: () => ['en-US', 'en']
                });

                // Mask WebGL vendor/renderer away from SwiftShader/llvmpipe
                const getParameter = WebGLRenderingContext.prototype.getParameter;
                WebGLRenderingContext.prototype.getParameter = function(parameter) {
                    if (parameter === 37445) return 'Google Inc. (NVIDIA)';
                    if (parameter === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                    return getParameter.apply(this, arguments);
                };

                // Seed localStorage from cookies to prevent empty-profile transplanted cookie detection
                try {
                    const rawCookies = document.cookie.split('; ');
                    for (const c of rawCookies) {
                        const [k, v] = c.split('=');
                        if (k && v && (k.includes('webid') || k.includes('user_id') || k.includes('csrf') || k.includes('tt_'))) {
                            localStorage.setItem(k, v);
                        }
                    }
                } catch (e) {}
            """)

            # Set cookies into context once before opening page
            if cookies_to_inject:
                try:
                    context.add_cookies(cookies_to_inject)
                    logger.info("Injected %d cookies into browser context.", len(cookies_to_inject))
                except Exception as e_cook:
                    logger.warning("Error injecting initial cookies into browser context: %s", e_cook)

            page = context.new_page()

            # Apply Playwright Stealth evasions
            try:
                from playwright_stealth import stealth_sync
                stealth_sync(page)
                logger.info("Applied Playwright Stealth evasions successfully.")
            except Exception as e_stealth:
                logger.debug("Stealth injection notice: %s", e_stealth)

            page.on("console", lambda msg: logger.info("Browser console [%s]: %s", msg.type, msg.text[:200]) if msg.type in ("warning", "error") else None)
            page.on("response", lambda resp: logger.warning("HTTP %s on %s", resp.status, resp.url[:120]) if resp.status >= 400 and any(k in resp.url for k in ["upload", "tos", "byteoversea", "video", "post", "project", "web"]) else None)
            page.on("requestfailed", lambda req: logger.warning("Playwright request failed: %s | %s", req.url[:120], req.failure))

            # Navigate directly to Creator Studio upload portal (skipping consumer homepage to avoid session conflicts)
            def dismiss_overlays(scopes):
                for s in scopes:
                    for sel in [
                        'button:has-text("Got it")',
                        'button:has-text("Get started")',
                        'button:has-text("Get Started")',
                        'button:has-text("Close")',
                        'button:has-text("Dismiss")',
                        'button:has-text("Not now")',
                        'button:has-text("Later")',
                        'button:has-text("OK")',
                        'button:has-text("Agree")',
                        'button:has-text("Accept all")',
                        'button:has-text("Allow all")',
                        'button[aria-label="Close"]',
                    ]:
                        try:
                            btn = s.query_selector(sel)
                            if btn and btn.is_visible():
                                logger.info("Dismissing overlay dialog button: '%s'", sel)
                                btn.click(timeout=1500, force=True)
                                page.wait_for_timeout(400)
                        except Exception:
                            pass

            def handle_error_or_retry(scopes):
                for s in scopes:
                    try:
                        # 1. Dialog with "Retry"
                        retry_modal_btn = s.query_selector('div[role="dialog"] button:has-text("Retry")')
                        if retry_modal_btn and retry_modal_btn.is_visible():
                            logger.warning("Detected 'Retry' button in upload error dialog; clicking...")
                            retry_modal_btn.click(timeout=3000, force=True)
                            page.wait_for_timeout(2000)
                            return True
                        # 2. Upload card with "Retry"
                        retry_card_btn = s.query_selector('div[class*="upload"] button:has-text("Retry"), button:has-text("Retry")')
                        if retry_card_btn and retry_card_btn.is_visible():
                            logger.warning("Detected 'Retry' button on upload card; clicking...")
                            retry_card_btn.click(timeout=3000, force=True)
                            page.wait_for_timeout(2000)
                            return True
                    except Exception:
                        pass
                return False

            def try_attach_file(scopes, file_path: Path):
                # A: Direct file input
                for s in scopes:
                    for sel in [
                        'input[type="file"]',
                        'input[accept*="video"]',
                        'input[accept*="mp4"]',
                        'input[data-e2e*="upload"]',
                    ]:
                        try:
                            el = s.query_selector(sel)
                            if el:
                                logger.info("Found file input via selector '%s' in frame '%s'", sel, s.name or "main")
                                el.set_input_files(str(file_path))
                                try:
                                    el.evaluate("""(input) => {
                                        input.dispatchEvent(new Event('input', { bubbles: true }));
                                        input.dispatchEvent(new Event('change', { bubbles: true }));
                                    }""")
                                except Exception:
                                    pass
                                return True, s
                        except Exception as e_inp:
                            logger.debug("Input selector '%s' error: %s", sel, e_inp)

                # B: Click upload button to trigger file chooser
                for s in scopes:
                    for btn_sel in [
                        'button:has-text("Select video")',
                        'button:has-text("Select videos")',
                        'button:has-text("Select file")',
                        'button:has-text("Select files")',
                        'button:has-text("Upload")',
                        'div[role="button"]:has-text("Select video")',
                        'div[role="button"]:has-text("Select file")',
                        'div[role="button"]:has-text("Select files")',
                        'div[class*="upload-card"]',
                        'div[class*="upload-btn"]',
                        'div[class*="upload"] button',
                        'button[class*="upload"]',
                    ]:
                        try:
                            btn = s.query_selector(btn_sel)
                            if btn and btn.is_visible():
                                logger.info("Found upload button '%s'; triggering file chooser...", btn_sel)
                                with page.expect_file_chooser(timeout=6000) as fc_info:
                                    btn.click(force=True)
                                fc = fc_info.value
                                fc.set_files(str(file_path))
                                logger.info("Attached file via file chooser on button '%s'!", btn_sel)
                                return True, s
                        except Exception as e_fc:
                            logger.debug("File chooser on '%s' notice: %s", btn_sel, e_fc)

                return False, None

            # Navigate through candidate upload portals
            upload_urls = [
                "https://www.tiktok.com/creator-center/upload",
                "https://www.tiktok.com/upload?lang=en",
                "https://www.tiktok.com/tiktokstudio/upload",
            ]
            # Warm up the browser session on the main explore page so cookies settle,
            # edge CDN validates the session, and localStorage is naturally hydrated.
            logger.info("Warming up browser session on tiktok.com explore...")
            try:
                page.goto("https://www.tiktok.com/explore", wait_until="commit", timeout=30000)
                page.wait_for_timeout(3000)
                safe_screenshot(page, screenshot_dir / "00_session_warmed.png")
            except Exception as e_warm:
                logger.debug("Session warm-up notice: %s", e_warm)

            file_attached = False
            upload_frame = None

            for target_url in upload_urls:
                logger.info("Navigating to upload portal: %s...", target_url)
                nav_ok = False
                for nav_attempt in range(1, 3):
                    try:
                        # wait_until="commit" finishes as soon as HTTP 200 response headers arrive,
                        # avoiding hangs caused by slow background analytics or tracking scripts.
                        page.goto(target_url, wait_until="commit", timeout=60000)
                        nav_ok = True
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=25000)
                        except Exception:
                            logger.info("DOM loading state took longer on %s; proceeding...", target_url)
                        page.wait_for_timeout(3000)
                        break
                    except Exception as e_nav:
                        logger.warning("Navigation notice for %s (attempt %d/2): %s", target_url, nav_attempt, e_nav)
                        page.wait_for_timeout(4000)

                safe_screenshot(page, screenshot_dir / "01_page_loaded.png")

                if not nav_ok or "about:blank" in page.url.lower():
                    logger.warning("Portal %s failed to load or remained blank; trying alternative...", target_url)
                    continue

                if "login" in page.url.lower():
                    logger.warning("Portal %s bounced to login; trying alternative...", target_url)
                    continue

                # Check if TikTok presented a CAPTCHA challenge
                for s in [page] + page.frames:
                    try:
                        captcha_el = s.query_selector(
                            'div[id*="captcha"], div[class*="captcha"], iframe[src*="captcha"], '
                            'div[class*="verify-wrap"], div[id*="verify-bar"], div:has-text("Verify to continue")'
                        )
                        if captcha_el:
                            logger.error("TikTok presented an interactive CAPTCHA verification challenge.")
                            safe_screenshot(page, screenshot_dir / "captcha_detected.png")
                            browser.close()
                            return False, "captcha_challenge", "Interactive CAPTCHA verification requested by TikTok on this runner IP."
                    except Exception:
                        pass

                # Dismiss any initial onboarding guide popups, cookie consent, or modal overlays
                current_scopes = [page] + page.frames
                dismiss_overlays(current_scopes)

                logger.info("Locating file upload element on %s for: %s", target_url, video_path.name)
                for attempt in range(6):
                    current_scopes = [page] + page.frames
                    dismiss_overlays(current_scopes)
                    file_attached, upload_frame = try_attach_file(current_scopes, video_path)
                    if file_attached:
                        break
                    time.sleep(2)

                if file_attached:
                    logger.info("Successfully attached video file on portal: %s!", target_url)
                    break
                else:
                    logger.warning("Could not locate file upload element on %s; trying next portal...", target_url)

            if not file_attached:
                logger.error("Could not find file input element on any upload portal.")
                safe_screenshot(page, screenshot_dir / "missing_file_input.png")
                browser.close()
                return False, "missing_file_input", "Could not locate file upload element on TikTok upload portal."

            logger.info("Video file attached. Waiting for TikTok to process upload...")
            safe_screenshot(page, screenshot_dir / "02_file_selected.png")

            # Wait for upload indicator / caption editor to render
            page.wait_for_timeout(10000)

            # Prioritize searching inside the active upload frame where file was selected
            search_scopes = ([upload_frame] if upload_frame and upload_frame != page else []) + [page] + page.frames
            dismiss_overlays(search_scopes)

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
            full_caption = f"{clean_title}\n\n{tags_str}"

            # Clear any error popups or overlays before editing caption
            handle_error_or_retry(search_scopes)
            dismiss_overlays(search_scopes)

            if caption_el:
                logger.info("Entering caption and viral hashtags into description editor...")
                try:
                    caption_el.scroll_into_view_if_needed()
                    caption_el.click(timeout=4000, force=True)
                    page.wait_for_timeout(300)
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                    page.wait_for_timeout(200)
                    page.keyboard.type(clean_title, delay=12)
                    page.keyboard.press("Enter")
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(100)
                    page.keyboard.type(tags_str, delay=12)
                    page.wait_for_timeout(300)
                    page.keyboard.press("Escape")
                    page.wait_for_timeout(400)
                except Exception as e_fill:
                    logger.warning("Keyboard typing notice (%s); attempting evaluate text dispatch...", e_fill)

                # Check if description box actually has our title; fallback to direct DOM execCommand
                current_text = caption_el.inner_text() or ""
                if clean_title[:15] not in current_text:
                    logger.info("Description editor text was '%s'; forcing full caption via DOM execCommand...", current_text[:40])
                    try:
                        active_scope = upload_frame or page
                        active_scope.evaluate(
                            """({ text }) => {
                                const el = document.querySelector('div[contenteditable="true"], textarea');
                                if (el) {
                                    el.focus();
                                    document.execCommand('selectAll', false, null);
                                    document.execCommand('delete', false, null);
                                    document.execCommand('insertText', false, text);
                                    el.dispatchEvent(new Event('input', { bubbles: true }));
                                    el.dispatchEvent(new Event('change', { bubbles: true }));
                                }
                            }""",
                            {"text": full_caption},
                        )
                    except Exception as e_ev:
                        logger.warning("Could not set caption via evaluate: %s", e_ev)
            else:
                logger.warning("Could not find dedicated caption element; checking generic inputs.")

            safe_screenshot(page, screenshot_dir / "03_caption_entered.png")

            # Configure Privacy Level ("Only you" / "Private" for manual phone draft review)
            if config.tiktok_privacy_level in ("SELF_ONLY", "PRIVATE", "private", "self", "draft"):
                logger.info("Configuring TikTok privacy setting to Private ('Only you' / Draft)...")
                privacy_set = False
                for s in search_scopes:
                    if privacy_set:
                        break
                    try:
                        # 1. Direct radio/label selection
                        for label in ["Only you", "Private", "Only me"]:
                            el = (
                                s.query_selector(f'label:has-text("{label}")')
                                or s.query_selector(f'div[role="radio"]:has-text("{label}")')
                                or s.query_selector(f'span:text-is("{label}")')
                            )
                            if el:
                                el.scroll_into_view_if_needed()
                                el.click(timeout=2000, force=True)
                                logger.info("Clicked privacy option '%s'", label)
                                page.wait_for_timeout(500)
                                privacy_set = True
                                break
                    except Exception as e_p1:
                        logger.debug("Privacy radio option check: %s", e_p1)

                if not privacy_set:
                    # 2. Dropdown menu selection
                    for s in search_scopes:
                        if privacy_set:
                            break
                        try:
                            dropdown = (
                                s.query_selector('div[class*="select"]:has-text("Public")')
                                or s.query_selector('div[role="combobox"]:has-text("Public")')
                                or s.query_selector('div[class*="select"]:has-text("Friends")')
                            )
                            if dropdown:
                                dropdown.scroll_into_view_if_needed()
                                dropdown.click(timeout=2000, force=True)
                                page.wait_for_timeout(600)
                                for opt_label in ["Only you", "Private", "Only me"]:
                                    opt = page.query_selector(
                                        f'div[role="option"]:has-text("{opt_label}"), li:has-text("{opt_label}")'
                                    )
                                    if opt:
                                        opt.click(timeout=2000, force=True)
                                        logger.info("Selected dropdown privacy option '%s'", opt_label)
                                        page.wait_for_timeout(500)
                                        privacy_set = True
                                        break
                        except Exception as e_p2:
                            logger.debug("Privacy dropdown check: %s", e_p2)

                safe_screenshot(page, screenshot_dir / "03b_privacy_configured.png")

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
                    'button:text-is("Post")',
                    'button[data-e2e="post_video_button"]',
                    'div[class*="btn-post"] button',
                    'button[class*="btn-post"]',
                    'div[class*="button-group"] button',
                    'div[class*="btn-group"] button',
                    'div[class*="footer"] button',
                    'div[class*="action"] button',
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
                                # Exclude left navigation sidebar (sidebar width is ~60px, x <= 65px)
                                if box["x"] < 65:
                                    continue
                                # Must not be disabled
                                is_disabled = (
                                    btn.get_attribute("disabled") is not None
                                    or btn.get_attribute("aria-disabled") == "true"
                                    or "disabled" in (btn.get_attribute("class") or "").lower()
                                )
                                logger.info(
                                    "Candidate Post button found: selector='%s', text='%s', box=%s, disabled=%s",
                                    sel,
                                    text,
                                    box,
                                    is_disabled,
                                )
                                if not is_disabled:
                                    return btn
                        except Exception:
                            pass
                return None

            # Wait for Post button to become enabled (wait up to 240s for video upload and transcoding)
            logger.info("Waiting for video upload to finalize and authentic Post button to become enabled...")
            post_button = None
            for attempt in range(80):
                # Check and auto-retry if transient "Couldn't upload" or network error dialog appears
                handle_error_or_retry(search_scopes)

                post_button = find_real_post_button(search_scopes)
                if post_button:
                    break

                # Check and log upload status / progress every few attempts
                if attempt % 4 == 0:
                    for s in search_scopes:
                        try:
                            for sel in ['div:has-text("%")', 'div:has-text("Uploaded")', 'div:has-text("Uploading")', 'div[class*="progress"]']:
                                el = s.query_selector(sel)
                                if el and el.is_visible():
                                    t = (el.inner_text() or "").strip().replace("\n", " ")
                                    if len(t) < 80 and ("%" in t or "upload" in t.lower()):
                                        logger.info("TikTok upload progress notice: '%s'", t)
                                        break
                        except Exception:
                            pass

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
                safe_screenshot(page, screenshot_dir / "04_after_post.png")

                # Handle post-click dialogs:
                # 1. TikTok "Continue to post?" modal (appears when Content Check Lite is in progress)
                # It has [Cancel] and a red [Post now] button. We MUST click [Post now]!
                for _ in range(6):
                    dialog_handled = False
                    for s in search_scopes:
                        try:
                            for post_confirm in ["Post now", "Post anyway", "Continue to post"]:
                                confirm_btn = s.query_selector(f'div[role="dialog"] button:has-text("{post_confirm}")') or s.query_selector(f'button:has-text("{post_confirm}")')
                                if confirm_btn and confirm_btn.is_visible():
                                    logger.info("Detected post confirmation modal button: '%s'! Clicking to confirm publication...", post_confirm)
                                    confirm_btn.click(timeout=5000, force=True)
                                    page.wait_for_timeout(4000)
                                    dialog_handled = True
                                    break
                        except Exception as e_conf:
                            logger.debug("Confirm dialog check error: %s", e_conf)
                    if dialog_handled:
                        break
                    time.sleep(2)

                page.wait_for_timeout(5000)
                safe_screenshot(page, screenshot_dir / "04b_dialog_confirmed.png")

                # 2. Only if an actual EXIT/DISCARD warning dialog appeared, cancel it and retry
                for s in search_scopes:
                    try:
                        dialog = s.query_selector('div[role="dialog"]')
                        if dialog:
                            dialog_text = (dialog.inner_text() or "").lower()
                            if "exit" in dialog_text or "not be saved" in dialog_text:
                                cancel_exit = dialog.query_selector('button:has-text("Cancel")')
                                if cancel_exit:
                                    logger.warning("Exit dialog appeared; dismissing Cancel and re-clicking Post button...")
                                    cancel_exit.click(timeout=1500, force=True)
                                    page.wait_for_timeout(1000)
                                    retry_btn = find_real_post_button(search_scopes)
                                    if retry_btn:
                                        retry_btn.scroll_into_view_if_needed()
                                        retry_btn.click(force=True)
                                        page.wait_for_timeout(6000)
                            elif "continue to post" in dialog_text or "post now" in dialog_text:
                                post_now = dialog.query_selector('button:has-text("Post now")')
                                if post_now:
                                    logger.info("Found 'Post now' button inside dialog; clicking...")
                                    post_now.click(timeout=5000, force=True)
                                    page.wait_for_timeout(5000)
                    except Exception:
                        pass

                # 3. Check for final post-upload success dialogs or confirmation banners
                for target in search_scopes:
                    for success_label in ["Manage your posts", "Upload another video", "View video", "Uploaded"]:
                        try:
                            success_el = target.query_selector(f'button:has-text("{success_label}")') or target.query_selector(f'text="{success_label}"')
                            if success_el:
                                logger.info("Detected post-upload success element: '%s'", success_label)
                                page.wait_for_timeout(1000)
                        except Exception:
                            pass

                safe_screenshot(page, screenshot_dir / "05_final_published.png")
                logger.info("Playwright auto-publish workflow completed successfully.")
                browser.close()
                return True, "success", "Video published successfully via Playwright."
            else:
                logger.warning("Post button was not enabled within the timeout.")
                safe_screenshot(page, screenshot_dir / "post_timeout.png")
                browser.close()
                return False, "post_timeout", "Video processing or transcoding timed out before Post button became active."

    except Exception as e:
        logger.error("Playwright upload failed: %s", e)
        return False, "exception", str(e)


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
    api_failure_reason = None
    if config.tiktok_access_token:
        logger.info("Attempting TikTok Official Content Posting API (Method A)...")
        res_api = upload_via_tiktok_api(video_path, title)
        api_ok = res_api[0] if isinstance(res_api, tuple) else bool(res_api)
        if api_ok:
            return {
                "status": "success",
                "method": "official_api",
                "video_path": str(video_path),
            }
        api_failure_reason = res_api[1] if isinstance(res_api, tuple) else "api_failed"
        api_msg = res_api[2] if isinstance(res_api, tuple) else "TikTok API upload failed"
        logger.warning("Method A failed (%s: %s); falling back to Method B (Playwright)...", api_failure_reason, api_msg)

    # 2. Try Playwright Session Automation (Method B)
    if config.tiktok_session_id or config.tiktok_cookies:
        logger.info("Attempting TikTok Playwright Browser Automation (Method B)...")
        max_attempts = 2
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                logger.info("Retrying Playwright upload (attempt %d/%d) after brief pause...", attempt, max_attempts)
                time.sleep(10)

            res_pw = upload_via_playwright(video_path, title)
            pw_ok = res_pw[0] if isinstance(res_pw, tuple) else bool(res_pw)
            if pw_ok:
                return {
                    "status": "success",
                    "method": "playwright_session",
                    "video_path": str(video_path),
                }
            pw_reason = res_pw[1] if isinstance(res_pw, tuple) else "playwright_failed"
            pw_msg = res_pw[2] if isinstance(res_pw, tuple) else "Playwright upload failed"

            # Do not retry fatal non-network errors
            if pw_reason in ("no_credentials", "playwright_not_installed", "captcha_challenge"):
                break

        return {
            "status": "failed",
            "method": "playwright_session",
            "reason": pw_reason,
            "message": pw_msg,
            "video_path": str(video_path),
        }

    # No valid publish credentials found
    logger.warning("No TikTok credentials configured. Video is saved locally in %s", video_path)
    return {
        "status": "unauthenticated",
        "method": "none",
        "reason": "no_credentials",
        "message": "Configure TIKTOK_ACCESS_TOKEN, TIKTOK_COOKIES, or TIKTOK_SESSION_ID in secrets to enable auto-posting.",
        "video_path": str(video_path),
    }

"""Unit tests for TikTok uploader helper functions and cookie parsers."""

from pathlib import Path
from src.tiktok_uploader import build_caption, parse_cookie_data


def test_build_caption():
    title = "My dramatic reddit story title"
    tags = ["#fyp", "#storytime", "#viral"]
    caption = build_caption(title, hashtags=tags)
    assert title in caption
    assert "#fyp #storytime #viral" in caption
    assert "\n\n" in caption


def test_build_caption_truncates_long_titles():
    long_title = "A" * 120
    caption = build_caption(long_title, hashtags=["#fyp"])
    # 97 chars + ... = 100
    assert "..." in caption.split("\n\n")[0]
    assert len(caption.split("\n\n")[0]) == 100


def test_parse_cookie_data_json_format():
    cookie_json = '[{"name": "sessionid", "value": "xyz123", "domain": ".tiktok.com", "path": "/"}]'
    cookies = parse_cookie_data(cookie_json)
    assert len(cookies) == 1
    assert cookies[0]["name"] == "sessionid"
    assert cookies[0]["value"] == "xyz123"
    assert cookies[0]["domain"] == ".tiktok.com"


def test_parse_cookie_data_header_format():
    cookie_header = "sessionid=xyz123; ttwid=abc456"
    cookies = parse_cookie_data(cookie_header)
    assert len(cookies) == 2
    names = [c["name"] for c in cookies]
    assert "sessionid" in names
    assert "ttwid" in names


def test_tiktok_config_defaults():
    from src.config import config
    assert config.tiktok_privacy_level == "PUBLIC_TO_EVERYONE"
    assert config.include_background_music is False
    assert "#redditstories" in config.hashtags
    assert "#restockasmrcompilation" not in config.hashtags
    assert config.tts_rate == "+13%"


def test_parse_cookie_data_domain_and_samesite_normalization():
    cookie_json = '[{"name": "sessionid", "value": "xyz123", "domain": "www.tiktok.com", "sameSite": "no_restriction"}]'
    cookies = parse_cookie_data(cookie_json)
    assert len(cookies) == 1
    assert cookies[0]["name"] == "sessionid"
    # www.tiktok.com should be normalized to .tiktok.com for cross-subdomain coverage
    assert cookies[0]["domain"] == ".tiktok.com"
    # invalid Playwright sameSite should be safely omitted
    assert "sameSite" not in cookies[0]


def test_publish_to_tiktok_dry_run(tmp_path: Path):
    from src.tiktok_uploader import publish_to_tiktok
    dummy_video = tmp_path / "test.mp4"
    dummy_video.write_bytes(b"dummy")
    res = publish_to_tiktok(dummy_video, title="Dry run story", dry_run=True)
    assert res["status"] == "simulated"
    assert res["method"] == "dry_run"


def test_proxy_url_parsing():
    from urllib.parse import urlparse
    raw_server = "http://bvgafxhc:b3j3wowo1icd@p.webshare.io:80/"
    parsed = urlparse(raw_server)
    assert parsed.username == "bvgafxhc"
    assert parsed.password == "b3j3wowo1icd"
    assert parsed.hostname == "p.webshare.io"
    assert parsed.port == 80


def test_verify_and_resolve_proxy_empty():
    from src.tiktok_uploader import verify_and_resolve_proxy
    proxy_dict, reason, msg = verify_and_resolve_proxy("")
    assert proxy_dict is None
    assert reason == "empty_proxy"


def test_verify_and_resolve_proxy_mocked(monkeypatch):
    from src.tiktok_uploader import verify_and_resolve_proxy
    import requests

    class DummyResponse:
        def __init__(self, json_data, status_code=200):
            self._json = json_data
            self.status_code = status_code

        def json(self):
            return self._json

    def mock_get(url, **kwargs):
        if "ipify.org" in url:
            return DummyResponse({"ip": "185.199.229.10"})
        if "ip-api.com" in url:
            return DummyResponse({"countryCode": "US", "timezone": "America/Los_Angeles"})
        return DummyResponse({}, 200)

    monkeypatch.setattr(requests, "get", mock_get)

    proxy_dict, reason, msg = verify_and_resolve_proxy(
        "http://bvgafxhc:b3j3wowo1icd@p.webshare.io:80/"
    )
    assert proxy_dict is not None
    assert reason == "ok"
    assert "p.webshare.io" in proxy_dict["server"]
    assert proxy_dict["username"] == "bvgafxhc-0"
    assert proxy_dict["password"] == "b3j3wowo1icd"
    assert proxy_dict["_timezone"] == "America/Los_Angeles"
    assert "US" in msg




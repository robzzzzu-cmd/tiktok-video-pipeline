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

"""Generates authentic Dark-Mode Reddit post card graphics for viral video hooks."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional, Tuple, List

from PIL import Image, ImageDraw, ImageFont

from src.config import config

logger = logging.getLogger(__name__)


def _load_font(font_path: Optional[Path], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Attempts to load a TrueType font, falling back to system or default fonts."""
    candidates = []
    if font_path and font_path.exists():
        candidates.append(str(font_path))

    # Check project fonts directory
    repo_font = config.fonts_dir / "Montserrat-ExtraBold.ttf"
    if repo_font.exists():
        candidates.append(str(repo_font))

    # Common system fonts on Linux and Windows
    candidates.extend([
        "/usr/share/fonts/truetype/montserrat/Montserrat-Bold.ttf",
        "/usr/share/fonts/truetype/montserrat/Montserrat-ExtraBold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
        "Arial Bold",
    ])

    for c in candidates:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue

    try:
        return ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    """Word-wraps text so each line fits within max_width pixels."""
    words = text.strip().split()
    if not words:
        return []

    lines = []
    current_line = []

    for word in words:
        test_line = " ".join(current_line + [word])
        # Measure text width
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except AttributeError:
            w = len(test_line) * 18

        if w <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []

    if current_line:
        lines.append(" ".join(current_line))

    return lines


def generate_reddit_card(
    title: str,
    subreddit: str = "r/AITAH",
    part_num: int = 1,
    category: str = "aitah",
    output_path: Optional[Path] = None,
    canvas_size: Tuple[int, int] = (1080, 1920),
) -> Path:
    """Renders a high-resolution, authentic dark-mode Reddit post card centered on a 1080x1920 canvas.

    - Canvas: 1080x1920 transparent PNG (matches vertical video resolution)
    - Card: Centered horizontally in the upper-middle Safe Zone
    - Styling: Reddit Dark Mode (#1A1A1B), subtle border, orange upvotes, flair badge
    """
    canvas_w, canvas_h = canvas_size
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Fonts
    title_font = _load_font(config.fonts_dir / "Montserrat-ExtraBold.ttf", size=36)
    sub_font = _load_font(None, size=26)
    meta_font = _load_font(None, size=21)
    badge_font = _load_font(None, size=20)
    action_font = _load_font(None, size=22)

    # Clean subreddit label
    sub_clean = subreddit.strip()
    if not sub_clean.startswith("r/"):
        sub_clean = f"r/{sub_clean}"

    # Clean story title (truncate if absurdly long)
    title_clean = title.strip()
    if "[part 1]" in title_clean.lower():
        title_clean = title_clean.replace("[Part 1]", "").replace("[part 1]", "").strip()
    elif "[part 2]" in title_clean.lower():
        title_clean = title_clean.replace("[Part 2]", "").replace("[part 2]", "").strip()

    if len(title_clean) > 130:
        title_clean = title_clean[:127] + "..."

    # Card dimensions
    card_w = 940
    card_x = (canvas_w - card_w) // 2
    inner_padding = 34

    # Calculate title height
    max_text_w = card_w - (inner_padding * 2)
    wrapped_lines = _wrap_text(title_clean, title_font, max_text_w)
    # Cap to max 4 lines
    if len(wrapped_lines) > 4:
        wrapped_lines = wrapped_lines[:3] + [wrapped_lines[3] + "..."]

    line_height = 48
    title_total_h = len(wrapped_lines) * line_height

    # Card layout coordinates
    header_h = 56
    footer_h = 54
    spacing = 22
    card_h = header_h + spacing + title_total_h + spacing + footer_h + (inner_padding * 2)

    # Vertically place in upper-middle Safe Zone (y: ~560-620)
    card_y = 580

    # Colors (Reddit Dark Mode)
    bg_color = (26, 26, 27, 250)        # #1A1A1B with high opacity
    border_color = (52, 53, 54, 255)    # #343536
    shadow_color = (0, 0, 0, 110)       # Drop shadow
    text_white = (245, 247, 248, 255)   # #F5F7F8
    text_gray = (130, 134, 138, 255)    # #82868A
    reddit_orange = (255, 69, 0, 255)   # #FF4500
    pill_bg = (38, 38, 40, 255)         # Action pill background

    # 1. Drop shadow behind card
    shadow_offset = 8
    draw.rounded_rectangle(
        [card_x + shadow_offset, card_y + shadow_offset, card_x + card_w + shadow_offset, card_y + card_h + shadow_offset],
        radius=26,
        fill=shadow_color,
    )

    # 2. Card background and border
    draw.rounded_rectangle(
        [card_x, card_y, card_x + card_w, card_y + card_h],
        radius=24,
        fill=bg_color,
        outline=border_color,
        width=2,
    )

    # 3. Header row: Subreddit avatar + name + metadata + Part flair badge
    cur_y = card_y + inner_padding
    cur_x = card_x + inner_padding

    # Avatar: Orange circle with white "r/" monogram
    avatar_radius = 21
    avatar_cx = cur_x + avatar_radius
    avatar_cy = cur_y + avatar_radius
    draw.ellipse(
        [avatar_cx - avatar_radius, avatar_cy - avatar_radius, avatar_cx + avatar_radius, avatar_cy + avatar_radius],
        fill=reddit_orange,
    )
    draw.text((avatar_cx - 9, avatar_cy - 14), "r/", fill=(255, 255, 255, 255), font=sub_font)

    # Subreddit name & metadata
    text_start_x = cur_x + (avatar_radius * 2) + 14
    draw.text((text_start_x, cur_y), sub_clean, fill=text_white, font=sub_font)

    meta_str = "• Posted by u/anon • 4h ago"
    draw.text((text_start_x, cur_y + 30), meta_str, fill=text_gray, font=meta_font)

    # Part badge pill (top right)
    badge_label = f"PART {part_num}" if part_num == 1 else "PART 2 • UPDATE"
    try:
        b_bbox = badge_font.getbbox(badge_label)
        b_w = b_bbox[2] - b_bbox[0] + 24
    except Exception:
        b_w = len(badge_label) * 12 + 24
    b_h = 32
    b_x = card_x + card_w - inner_padding - b_w
    b_y = cur_y + 6
    draw.rounded_rectangle(
        [b_x, b_y, b_x + b_w, b_y + b_h],
        radius=14,
        fill=(40, 25, 22, 255) if part_num == 1 else (30, 38, 50, 255),
        outline=reddit_orange if part_num == 1 else (56, 138, 255, 255),
        width=1,
    )
    b_color = reddit_orange if part_num == 1 else (80, 160, 255, 255)
    draw.text((b_x + 12, b_y + 4), badge_label, fill=b_color, font=badge_font)

    # 4. Title block
    title_start_y = cur_y + header_h + spacing - 4
    for i, line in enumerate(wrapped_lines):
        line_y = title_start_y + (i * line_height)
        draw.text((cur_x, line_y), line, fill=text_white, font=title_font)

    # 5. Footer row: Upvote pill, Comments pill, Share pill
    footer_y = title_start_y + title_total_h + spacing + 6

    # Random realistic viral upvote/comment metrics
    upvote_val = f"{random.randint(14, 28)}.{random.randint(1, 9)}k"
    comment_val = f"{random.randint(11, 24)}00"

    # Upvotes pill
    upvote_text = f"▲  {upvote_val}  ▼"
    try:
        up_bbox = action_font.getbbox(upvote_text)
        up_w = up_bbox[2] - up_bbox[0] + 32
    except Exception:
        up_w = 160
    draw.rounded_rectangle(
        [cur_x, footer_y, cur_x + up_w, footer_y + 44],
        radius=22,
        fill=pill_bg,
        outline=border_color,
        width=1,
    )
    draw.text((cur_x + 16, footer_y + 9), upvote_text, fill=reddit_orange, font=action_font)

    # Comments pill
    comments_x = cur_x + up_w + 14
    comments_text = f"💬  {comment_val}"
    try:
        com_bbox = action_font.getbbox(comments_text)
        com_w = com_bbox[2] - com_bbox[0] + 32
    except Exception:
        com_w = 140
    draw.rounded_rectangle(
        [comments_x, footer_y, comments_x + com_w, footer_y + 44],
        radius=22,
        fill=pill_bg,
        outline=border_color,
        width=1,
    )
    draw.text((comments_x + 16, footer_y + 9), comments_text, fill=text_gray, font=action_font)

    # Share pill
    share_x = comments_x + com_w + 14
    share_text = "↗  Share"
    try:
        sh_bbox = action_font.getbbox(share_text)
        sh_w = sh_bbox[2] - sh_bbox[0] + 32
    except Exception:
        sh_w = 120
    draw.rounded_rectangle(
        [share_x, footer_y, share_x + sh_w, footer_y + 44],
        radius=22,
        fill=pill_bg,
        outline=border_color,
        width=1,
    )
    draw.text((share_x + 16, footer_y + 9), share_text, fill=text_gray, font=action_font)

    # Save output
    if output_path is None:
        output_path = config.temp_dir / f"reddit_card_part{part_num}.png"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG")
    logger.info("Generated authentic Reddit Card graphic at: %s", output_path)

    return output_path

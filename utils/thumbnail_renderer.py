"""
Aaruu Music - Dynamic Player Thumbnail Renderer
Composites a track's raw YouTube/JioSaavn thumbnail into a polished "now playing"
card: darkened background, title/artist text, and a progress bar overlay -- similar
in spirit to Spotify/Apple Music's now-playing card, instead of sending the bare
scraped thumbnail image as-is.

Generated once per track start (not on every progress-bar tick) to keep this cheap:
each render does one image download + composite + one Telegram upload.
"""

import io
import os
import urllib.request
from typing import Optional, Tuple

from utils.logging import logger

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

CARD_SIZE = (1280, 720)
FONT_DIR = "/usr/share/fonts/truetype/dejavu"
FONT_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
FONT_REGULAR = os.path.join(FONT_DIR, "DejaVuSans.ttf")

DEFAULT_THUMBNAIL = (
    "https://images.unsplash.com/photo-1511671782779-c97d3d27a1d4?w=800&auto=format&fit=crop&q=80"
)


def _load_font(path: str, size: int) -> "ImageFont.ImageFont":
    """Loads a TTF font at the given size, falling back to Pillow's built-in
    bitmap font if DejaVu isn't installed in this environment (e.g. missing
    the fonts-dejavu-core apt package)."""
    try:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.load_default(size=size)
    except Exception:
        return ImageFont.load_default()


def _fetch_image_bytes(url: str, timeout: int = 8) -> Optional[bytes]:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except Exception as e:
        logger.debug("[THUMBNAIL-RENDER] Failed to fetch source image '%s': %s", url, str(e))
        return None


def _truncate_to_width(draw: "ImageDraw.ImageDraw", text: str, font: "ImageFont.ImageFont", max_width: int) -> str:
    """Shortens text with an ellipsis so it fits within max_width pixels."""
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if draw.textlength(candidate, font=font) <= max_width:
            lo = mid + 1
        else:
            hi = mid
    return text[: max(lo - 1, 0)].rstrip() + ellipsis


def render_player_card(
    thumbnail_url: str,
    title: str,
    artist: str,
    duration_seconds: int,
    position_seconds: float,
    requester_name: str = "",
    is_paused: bool = False,
    is_video: bool = False,
) -> Optional[bytes]:
    """
    Builds a composited "now playing" card image and returns it as PNG bytes,
    or None if rendering isn't possible (Pillow missing, image fetch failed, etc.)
    so callers can gracefully fall back to sending the raw thumbnail URL instead.
    """
    if not PIL_AVAILABLE:
        logger.debug("[THUMBNAIL-RENDER] Pillow not installed; skipping dynamic card render.")
        return None

    source_url = thumbnail_url if (thumbnail_url and thumbnail_url.startswith(("http://", "https://"))) else DEFAULT_THUMBNAIL
    raw_bytes = _fetch_image_bytes(source_url)
    if not raw_bytes:
        raw_bytes = _fetch_image_bytes(DEFAULT_THUMBNAIL)
    if not raw_bytes:
        return None

    try:
        source_img = Image.open(io.BytesIO(raw_bytes)).convert("RGB")
    except Exception as e:
        logger.debug("[THUMBNAIL-RENDER] Failed to decode source image: %s", str(e))
        return None

    try:
        # 1. Background: cover-crop the source thumbnail to fill the whole card,
        #    then darken + blur it so foreground text stays readable regardless
        #    of the thumbnail's own colors/contrast.
        bg = ImageOps.fit(source_img, CARD_SIZE, method=Image.LANCZOS)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=18))

        darken = Image.new("RGB", CARD_SIZE, (0, 0, 0))
        bg = Image.blend(bg, darken, alpha=0.45)

        card = bg.convert("RGBA")

        # Bottom gradient so text has a guaranteed-readable band even on busy images.
        gradient = Image.new("L", (1, CARD_SIZE[1]), color=0)
        for y in range(CARD_SIZE[1]):
            # Fade in starting ~45% down the card, fully dark by the bottom.
            t = max(0.0, (y - CARD_SIZE[1] * 0.40) / (CARD_SIZE[1] * 0.60))
            gradient.putpixel((0, y), int(200 * min(1.0, t)))
        gradient = gradient.resize(CARD_SIZE)
        shadow = Image.new("RGBA", CARD_SIZE, (0, 0, 0, 255))
        shadow.putalpha(gradient)
        card = Image.alpha_composite(card, shadow)

        # 2. Foreground: crisp (non-blurred) square cover art, bottom-left, like an
        #    album-art tile, so the track's actual thumbnail is still clearly visible.
        cover_size = 280
        cover = ImageOps.fit(source_img, (cover_size, cover_size), method=Image.LANCZOS)
        cover_pos = (56, CARD_SIZE[1] - cover_size - 56)

        # Rounded-corner mask for the cover tile.
        mask = Image.new("L", (cover_size, cover_size), 0)
        mdraw = ImageDraw.Draw(mask)
        mdraw.rounded_rectangle([0, 0, cover_size, cover_size], radius=24, fill=255)
        card.paste(cover, cover_pos, mask)

        draw = ImageDraw.Draw(card)

        text_x = cover_pos[0] + cover_size + 40
        text_max_width = CARD_SIZE[0] - text_x - 56

        # 3. "NOW PLAYING" / "PAUSED" eyebrow label.
        label_font = _load_font(FONT_BOLD, 28)
        label_text = ("⏸ PAUSED" if is_paused else "▶ NOW PLAYING") + ("  •  VIDEO" if is_video else "")
        label_color = (255, 200, 90) if is_paused else (110, 231, 150)
        draw.text((text_x, cover_pos[1] + 8), label_text, font=label_font, fill=label_color)

        # 4. Title (bold, larger) + artist (regular, dimmer), each truncated to fit.
        title_font = _load_font(FONT_BOLD, 52)
        artist_font = _load_font(FONT_REGULAR, 34)

        title_display = _truncate_to_width(draw, title or "Unknown Track", title_font, text_max_width)
        artist_display = _truncate_to_width(draw, artist or "Unknown Artist", artist_font, text_max_width)

        draw.text((text_x, cover_pos[1] + 50), title_display, font=title_font, fill=(255, 255, 255))
        draw.text((text_x, cover_pos[1] + 118), artist_display, font=artist_font, fill=(200, 200, 205))

        if requester_name:
            req_font = _load_font(FONT_REGULAR, 24)
            req_display = _truncate_to_width(draw, f"Requested by {requester_name}", req_font, text_max_width)
            draw.text((text_x, cover_pos[1] + 168), req_display, font=req_font, fill=(150, 150, 158))

        # 5. Progress bar spanning the width under the cover+text row.
        bar_x0 = 56
        bar_x1 = CARD_SIZE[0] - 56
        bar_y = CARD_SIZE[1] - 28
        bar_height = 8

        safe_duration = max(1, int(duration_seconds or 0))
        progress_ratio = 0.0 if safe_duration <= 0 else min(1.0, max(0.0, position_seconds / safe_duration))
        fill_x = bar_x0 + int((bar_x1 - bar_x0) * progress_ratio)

        draw.rounded_rectangle(
            [bar_x0, bar_y, bar_x1, bar_y + bar_height], radius=bar_height // 2, fill=(255, 255, 255, 70)
        )
        if fill_x > bar_x0:
            draw.rounded_rectangle(
                [bar_x0, bar_y, fill_x, bar_y + bar_height], radius=bar_height // 2, fill=(110, 231, 150, 255)
            )
        # Playhead dot
        draw.ellipse(
            [fill_x - 7, bar_y - 3, fill_x + 7, bar_y + bar_height + 3], fill=(255, 255, 255)
        )

        # Time labels under the bar ends.
        time_font = _load_font(FONT_REGULAR, 22)
        pos_str = _format_seconds(position_seconds)
        dur_str = _format_seconds(safe_duration)
        draw.text((bar_x0, bar_y - 32), pos_str, font=time_font, fill=(200, 200, 205))
        dur_w = draw.textlength(dur_str, font=time_font)
        draw.text((bar_x1 - dur_w, bar_y - 32), dur_str, font=time_font, fill=(200, 200, 205))

        out = io.BytesIO()
        card.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()

    except Exception as e:
        logger.warning("[THUMBNAIL-RENDER] Failed to composite player card: %s", str(e))
        return None


def _format_seconds(total_seconds: float) -> str:
    total_seconds = max(0, int(total_seconds or 0))
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

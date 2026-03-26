"""Image generation via DeepInfra API (OpenAI-compatible FLUX-1-schnell).

Architecture note:
    - ACTION_PROMPTS lives in prompt_builder.py (source of truth).
    - This module handles: API communication, image decoding, pixelation.
    - All prompt construction lives in prompt_builder.py.
"""

import base64
import hashlib
import io
import json
import os
import time
import requests
from pathlib import Path
from typing import List, Optional, Tuple


from PIL import Image, ImageFilter

DEFAULT_CONFIG = "config.json"
# Cache directory for reproducible generation (DeepInfra FLUX-schnell ignores seeds)
_CACHE_DIR = Path(__file__).parent / ".frame_cache"


# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    path = Path(__file__).parent / DEFAULT_CONFIG
    return json.loads(path.read_text())

def _cache_key(prompt: str, seed: int) -> str:
    """Deterministic cache key from prompt + seed."""
    data = f"{prompt}:{seed}".encode()
    return hashlib.sha256(data).hexdigest()[:32]

def _cache_get(key: str) -> Optional[bytes]:
    """Return cached bytes if exists, else None."""
    path = _CACHE_DIR / f"{key}.bin"
    if path.exists():
        return path.read_bytes()
    return None

def _cache_set(key: str, data: bytes) -> None:
    """Store bytes in cache."""
    _CACHE_DIR.mkdir(exist_ok=True)
    path = _CACHE_DIR / f"{key}.bin"
    path.write_bytes(data)



# ── Generation ────────────────────────────────────────────────────────────────

def generate_frame(
    prompt: str,
    size: int = 512,
    config: dict = None,
    seed: Optional[int] = None,
) -> bytes:
    """Generate a single image frame from a text prompt.

    Args:
        prompt: Full enhanced prompt (prompt_builder.py handles enhancement).
        size: API image size in pixels (512 = 512×512).
        config: Config dict. If None, loaded from config.json.
        seed: Optional integer seed for reproducibility.

    Returns:
        Raw PNG image bytes (decoded from base64).

    Raises:
        RuntimeError: On API error or non-200 response.
    """
    if config is None:
        config = load_config()

    headers = {
        "Authorization": f"Bearer {config['deepinfra_api_key']}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": config.get("model", "black-forest-labs/FLUX-1-schnell"),
        "prompt": prompt,
        "image_size": str(size),
        "num_inference_steps": config.get("generation_steps", 4),
        "response_format": "b64_json",
    }

    # Check cache first — DeepInfra FLUX-schnell ignores seeds, so we
    # simulate reproducibility by caching results keyed on (prompt, seed).
    cache_key = _cache_key(prompt, seed) if seed is not None else None
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    if seed is not None:
        payload["seed"] = int(seed)

    api_url = config.get(
        "deepinfra_base_url",
        "https://api.deepinfra.com/v1/openai/images/generations",
    )

    response = requests.post(
        api_url,
        headers=headers,
        json=payload,
        timeout=config.get("generation_timeout", 120),
    )

    if response.status_code != 200:
        raise RuntimeError(f"DeepInfra API error {response.status_code}: {response.text}")

    data = response.json()
    b64 = data["data"][0]["b64_json"]
    result = base64.b64decode(b64)
    if cache_key:
        _cache_set(cache_key, result)
    return result


# ── Pixel art conversion ───────────────────────────────────────────────────────

def _remove_background(img: Image.Image, threshold: int = 15) -> Image.Image:
    """Remove near-white/solid background from a sprite image.

    Samples the four corners to detect the background color, then
    sets any pixel matching that color (within threshold) to transparent.
    Uses per-pixel max channel distance — more accurate than RGB distance.

    Only removes background that is VERY close to the corner sample
    (threshold=15 in 0-255 scale) to avoid eating into dark characters.

    Args:
        img: PIL Image in RGBA mode.
        threshold: Color distance tolerance for background matching.

    Returns:
        PIL Image with transparent background, RGBA mode.
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # Sample background from corners
    w, h = img.size
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
        img.getpixel((w // 2, 0)),
        img.getpixel((w // 2, h - 1)),
        img.getpixel((0, h // 2)),
        img.getpixel((w - 1, h // 2)),
    ]

    # Find the most common corner color (likely the background)
    from collections import Counter
    corner_rgbas = [c[:4] for c in corners]
    bg_rgba = Counter(corner_rgbas).most_common(1)[0][0]

    # Only proceed if the background is actually light/neutral
    # If corners are dark (character fills frame), skip removal entirely
    bg_r, bg_g, bg_b = bg_rgba[0], bg_rgba[1], bg_rgba[2]
    bg_brightness = (bg_r + bg_g + bg_b) / 3
    if bg_brightness < 40:
        # Dark background — likely a dark scene, don't remove
        return img

    # Build a clean alpha channel using per-channel max distance
    r, g, b, _ = img.split()

    new_alpha = Image.new("L", img.size, 255)  # default: opaque

    for y in range(h):
        for x in range(w):
            px_r, px_g, px_b = r.getpixel((x, y)), g.getpixel((x, y)), b.getpixel((x, y))
            # Only transparent if ALL channels are close to background AND bg is light
            if (abs(px_r - bg_r) <= threshold and
                abs(px_g - bg_g) <= threshold and
                abs(px_b - bg_b) <= threshold):
                new_alpha.putpixel((x, y), 0)  # transparent
            else:
                new_alpha.putpixel((x, y), 255)  # opaque

    return Image.merge("RGBA", (r, g, b, new_alpha))


def pixelate_image(image_bytes: bytes, target_size: int) -> Image.Image:
    """Convert an AI-generated image to a pixel art sprite.

    Does four things in order:
    1. Open and convert to RGBA.
    2. Remove background at native resolution (512px) — more precise.
    3. Resize down to target_size using nearest-neighbor.
    4. Normalize: anchor character feet at consistent Y position.

    Args:
        image_bytes: Raw PNG/JPEG bytes from the API.
        target_size: Target sprite dimension in pixels (e.g. 64 = 64×64).

    Returns:
        PIL Image in RGBA mode with transparent background.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    # Step 1: Remove background at full resolution (512px) before pixelation
    # This is more accurate — more pixels to detect background vs character
    img = _remove_background(img, threshold=20)

    # Step 2: Nearest-neighbor resize — critical for pixel art look
    img_small = img.resize((target_size, target_size), Image.Resampling.NEAREST)

    # Step 3: Sharpen to recover crispness after resize
    img_small = img_small.filter(ImageFilter.SHARPEN)

    # NOTE: Do NOT normalize here — normalization requires a shared reference
    # feet_y across all frames. Call normalize_sprite(img, ref_feet_y=REF) after QC.
    return img_small




def normalize_sprite(img: Image.Image, target_size: int = 64,
                    reference_feet_y: int = None) -> Image.Image:
    """Normalize a sprite frame so the character is consistently anchored.

    Finds the non-transparent content bounding box and shifts the character
    so feet land at the same Y position as the reference frame.
    This fixes FLUX's tendency to place characters at slightly different
    vertical positions in each frame, which breaks animation.

    Call this AFTER pixelation.

    Args:
        img: PIL RGBA image (sprite frame at target_size).
        target_size: Frame size in pixels.
        reference_feet_y: Target feet Y position (from first good frame).
                          If None, uses default anchor (82% from top).

    Returns:
        Normalized PIL RGBA image.
    """
    w, h = target_size, target_size
    frame = img.convert("RGBA")
    if frame.size != (w, h):
        frame = frame.resize((w, h), Image.Resampling.NEAREST)

    # Find content bounding box
    y_min, y_max = h, 0
    x_min, x_max = w, 0
    for y in range(h):
        for x in range(w):
            _, _, _, a = frame.getpixel((x, y))
            if a > 10:
                if y < y_min: y_min = y
                if y > y_max: y_max = y
                if x < x_min: x_min = x
                if x > x_max: x_max = x

    if y_max <= y_min:
        return frame  # empty frame

    # Anchor feet at reference_feet_y if provided, else default (82% from top)
    if reference_feet_y is not None:
        anchor_y = reference_feet_y
    else:
        anchor_y = int(target_size * 0.82)

    content_bottom = y_max
    vertical_shift = anchor_y - content_bottom

    # Center horizontally
    paste_x = (w - (x_max - x_min + 1)) // 2
    paste_y = y_min + vertical_shift

    # Clamp to stay in bounds
    paste_x = max(0, min(w - (x_max - x_min + 1), paste_x))
    paste_y = max(0, min(h - (y_max - y_min + 1), paste_y))

    # Extract content and place it
    content = frame.crop((x_min, y_min, x_max + 1, y_max + 1))
    normalized = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    normalized.paste(content, (paste_x, paste_y), content)
    return normalized


# ── Frame I/O ──────────────────────────────────────────────────────────────────

def save_frames(
    frames: List[tuple],
    output_dir: Optional[str] = None,
) -> List[str]:
    """Save a list of (label, PIL.Image) frames as numbered PNGs.

    Args:
        frames: List of (label, Image) tuples. The label is ignored;
            frames are numbered by position.
        output_dir: Destination directory. Defaults to ``frames/`` next to
            this module.

    Returns:
        List of saved file paths as strings.
    """
    if output_dir is None:
        output_dir = Path(__file__).parent / "frames"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Clear old numbered frames to avoid stale files
    for f in output_dir.glob("frame_*.png"):
        f.unlink()

    paths = []
    for i, (_, img) in enumerate(frames):
        path = output_dir / f"frame_{i:03d}.png"
        img.save(str(path), "PNG")
        paths.append(str(path))

    return paths


# ── Batch generation ───────────────────────────────────────────────────────────

def generate_batch(
    frames: List[Tuple[str, str]],
    size: int = 512,
    config: dict = None,
) -> List[Tuple[str, Image.Image]]:
    """Generate multiple frames in sequence.

    Args:
        frames: List of (action_name, prompt) tuples.
        size: API image size in pixels.
        config: Config dict. If None, loaded from config.json.

    Returns:
        List of (action_name, PIL.Image) tuples in the same order as input.
    """
    if config is None:
        config = load_config()

    results = []
    errors = []
    for action_name, prompt in frames:
        try:
            raw_bytes = generate_frame(prompt, size=size, config=config)
            img = pixelate_image(raw_bytes, size)
            results.append((action_name, img))
        except Exception as exc:
            errors.append({"action": action_name, "prompt": prompt[:80], "error": str(exc)})
            # Append None as placeholder so results align with input
            results.append((action_name, None))

    if errors:
        # Log but don't abort — partial results are still useful
        for e in errors:
            print(f"[generate_batch] ERROR for '{e['action']}': {e['error']}")

    return results


# ── Frame QC ─────────────────────────────────────────────────────────────────

class FrameQCResult:
    """Result of quality control check on a sprite frame."""
    def __init__(self, passed: bool, reasons: list = None,
                 content_bbox: tuple = None, aspect: float = 0,
                 feet_y: int = 0, centered_x: int = 0):
        self.passed = passed
        self.reasons = reasons or ([] if passed else ["unknown"])
        self.content_bbox = content_bbox or (0, 0, 0, 0)
        self.aspect = aspect
        self.feet_y = feet_y
        self.centered_x = centered_x
        # Score 0-10 derived from QC state
        self.score: float = 0.0 if not passed else 7.0  # base score for passed frames

    def set_score(self, score: float):
        """Set the score (used by external ranker)."""
        self.score = max(0.0, min(10.0, score))


def validate_frame(img: Image.Image, target_size: int = 64,
                   min_aspect: float = 0.25, max_aspect: float = 0.9,
                   max_feet_range: int = 6,
                   reference_feet_y: int = None) -> FrameQCResult:
    """Check a sprite frame for quality issues.

    QC checks:
    1. Corner transparency — corners must be transparent (no background)
    2. Content size — character must occupy at least 25% of frame
    3. Aspect ratio — character should be taller than wide (0.25-0.9 for humanoid)
    4. Feet position — character feet should align with reference (within max_feet_range)
    5. Off-center — character should be roughly centered horizontally

    Args:
        img: PIL RGBA image at target_size.
        target_size: Expected frame size (e.g. 64).
        min_aspect: Minimum width/height ratio (prevents squished sprites).
        max_aspect: Maximum width/height ratio (prevents fat sprites).
        max_feet_range: Max Y deviation from reference feet position.
        reference_feet_y: Expected feet Y from first good frame.

    Returns:
        FrameQCResult with pass/fail and details.
    """
    w, h = img.size
    reasons = []

    # 1. Corner transparency
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    corner_alpha = [c[3] for c in corners]
    if not all(a < 5 for a in corner_alpha):
        reasons.append(f"bg_corners:{corner_alpha}")

    # 2. Content bounding box
    min_x, max_x = w, 0
    min_y, max_y = h, 0
    for y in range(h):
        for x in range(w):
            if img.getpixel((x, y))[3] > 10:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y

    if max_x <= min_x or max_y <= min_y:
        reasons.append("empty_frame")
        return FrameQCResult(False, reasons)

    content_w = max_x - min_x + 1
    content_h = max_y - min_y + 1
    content_ratio = (content_w * content_h) / (w * h)
    aspect = content_w / max(content_h, 1)
    feet_y = max_y
    centered_x = (min_x + max_x) // 2

    # 3. Content size check
    if content_ratio < 0.20:
        reasons.append(f"too_small:{content_ratio:.2f}")

    # 4. Aspect ratio check
    if not (min_aspect <= aspect <= max_aspect):
        reasons.append(f"bad_aspect:{aspect:.2f}")

    # 5. Feet position check
    if reference_feet_y is not None:
        if abs(feet_y - reference_feet_y) > max_feet_range:
            reasons.append(f"feet_off:{feet_y}vs{reference_feet_y}")

    # 6. Off-center check
    off_center = abs(centered_x - w // 2)
    if off_center > 12:
        reasons.append(f"off_center:{off_center}")

    passed = len(reasons) == 0
    return FrameQCResult(
        passed=passed,
        reasons=reasons,
        content_bbox=(min_x, min_y, max_x, max_y),
        aspect=aspect,
        feet_y=feet_y,
        centered_x=centered_x,
    )


def extract_character_region(img: Image.Image, bbox: tuple, target_size: int) -> Image.Image:
    """Extract character from bbox and normalize to consistent size/position.

    Crops character from bbox, then pastes into a new frame so the
    character's feet land at a consistent anchor point.
    """
    from PIL import Image as PILImage
    min_x, min_y, max_x, max_y = bbox
    content_w = max_x - min_x + 1
    content_h = max_y - min_y + 1

    # Anchor feet at 85% from top
    anchor_y = int(target_size * 0.85)
    feet_offset = max_y - anchor_y  # negative = move up

    # Create normalized frame
    normalized = PILImage.new("RGBA", (target_size, target_size), (0, 0, 0, 0))

    # Calculate paste position
    paste_x = (target_size - content_w) // 2
    paste_y = min_y + feet_offset

    # Clamp to stay within frame
    paste_x = max(0, min(target_size - content_w, paste_x))
    paste_y = max(0, min(target_size - content_h, paste_y))

    # Crop and paste
    char_img = img.crop((min_x, min_y, max_x + 1, max_y + 1))
    normalized.paste(char_img, (paste_x, paste_y), char_img)
    return normalized

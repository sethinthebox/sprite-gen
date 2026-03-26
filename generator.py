"""Image generation via DeepInfra API (OpenAI-compatible FLUX-1-schnell).

Architecture note:
    - ACTION_PROMPTS lives in prompt_builder.py (source of truth).
    - This module handles: API communication, image decoding, pixelation.
    - All prompt construction lives in prompt_builder.py.
"""

import base64
import io
import json
import time
import requests
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageFilter

DEFAULT_CONFIG = "config.json"

# ── Config ─────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    path = Path(__file__).parent / DEFAULT_CONFIG
    return json.loads(path.read_text())


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
    return base64.b64decode(b64)


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

    # Step 4: Normalize — anchor character feet at consistent Y position
    img_small = normalize_sprite(img_small, target_size)

    return img_small




def normalize_sprite(img: Image.Image, target_size: int = 64) -> Image.Image:
    """Normalize a sprite frame so the character is consistently anchored.

    Finds the non-transparent content bounding box and shifts the character
    so feet land at a consistent Y position (82% from top = typical standing height).
    This fixes FLUX's tendency to place characters at slightly different
    vertical positions in each frame, which breaks animation.

    Call this AFTER pixelation.

    Args:
        img: PIL RGBA image (sprite frame at target_size).
        target_size: Frame size in pixels.

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

    # Anchor feet at 82% from top
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

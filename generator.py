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
from typing import Optional

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

def pixelate_image(image_bytes: bytes, target_size: int) -> Image.Image:
    """Convert an AI-generated image to a pixel art sprite.

    Does two things:
    1. Resize down to target_size × target_size using nearest-neighbor
       (preserves hard pixel edges — no smoothing).
    2. Apply a mild sharpen to crisp up the pixel boundaries.

    Args:
        image_bytes: Raw PNG/JPEG bytes from the API.
        target_size: Target sprite dimension in pixels (e.g. 64 = 64×64).

    Returns:
        PIL Image in RGBA mode.
    """
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    # Nearest-neighbor resize — critical for pixel art look
    img_small = img.resize(
        (target_size, target_size),
        Image.Resampling.NEAREST,
    )

    # Sharpen slightly to recover crispness after resize
    img_small = img_small.filter(ImageFilter.SHARPEN)

    return img_small


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

"""
EvoLink-based sprite generation pipeline.
Uses FLUX for initial generation, EvoLink for reference-based consistent frames.

Two modes:
- SANDBOX (default): 0.5K quality, cheap for testing
- PRODUCTION: 1K quality, full resolution

Cost per frame:
  0.5K: ~2.58 credits = ~$0.036
  1K:   ~3.87 credits = ~$0.054
  2K:   ~5.81 credits = ~$0.081
"""
import base64
import io
import json
import os
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from generator import generate_frame as flux_generate, load_config, pixelate_image
from evolink_gen import generate as evolink_generate, download_image


# ─── Mode configuration ────────────────────────────────────────────────────────
SANDBOX_QUALITY = "0.5K"
PROD_QUALITY = "1K"


# ─── EvoLink generation with reference ─────────────────────────────────────────
def evolink_generate_sprite(
    prompt: str,
    reference_images: list = None,
    quality: str = SANDBOX_QUALITY,
    size: str = "1:1",
    api_key: str = None,
) -> bytes:
    """
    Generate a sprite using EvoLink with reference images for consistency.
    Returns raw PNG bytes.
    """
    key = api_key or os.environ.get("EVOLINK_API_KEY")
    if not key:
        raise ValueError("No EVOLINK_API_KEY set")

    # Upload reference images to accessible URLs first
    ref_urls = []
    if reference_images:
        for ref_img in reference_images:
            # Save to VPS publicly accessible path
            saved_url = _upload_reference(ref_img)
            if saved_url:
                ref_urls.append(saved_url)

    # Submit generation task
    result = evolink_generate(
        prompt=prompt,
        quality=quality,
        size=size,
        reference_urls=ref_urls if ref_urls else None,
        api_key=key,
    )

    if not result.get("image_url"):
        raise RuntimeError(f"No image URL in result: {result}")

    # Download result
    img_bytes = download_image(result["image_url"])
    return img_bytes


def _upload_reference(img_bytes_or_path) -> Optional[str]:
    """Upload a reference image to a publicly accessible URL."""
    import shutil

    # If it's bytes, save to temp file
    if isinstance(img_bytes_or_path, bytes):
        tmp_path = f"/tmp/evolink_ref_{uuid.uuid4().hex[:8]}.png"
        with open(tmp_path, "wb") as f:
            f.write(img_bytes_or_path)
        img_bytes_or_path = tmp_path

    # Upload to VPS
    dest = f"/var/www/tricorder/releases/v0.5/evoref_{uuid.uuid4().hex[:8]}.png"
    try:
        shutil.copy(img_bytes_or_path, dest)
        # Return public URL
        return f"http://69.48.207.73/tricorder/releases/v0.5/{Path(dest).name}"
    except Exception as e:
        print(f"  [evolink] WARNING: could not upload reference: {e}")
        return None


# ─── Hybrid generation pipeline ────────────────────────────────────────────────
def generate_reference_character(
    base_character: str,
    style_suffix: str = "pixel art style, clean lines, transparent background",
    api_key: str = None,
) -> tuple[Image.Image, str]:
    """
    Generate the first reference frame using FLUX.
    Returns (PIL Image, cache_key) for subsequent EvoLink calls.
    """
    prompt = f"{base_character}, {style_suffix}"

    # Use FLUX (free)
    config = load_config()
    raw_bytes = flux_generate(
        prompt=prompt,
        size=1024,
        config=config,
        seed=None,  # Random for variety
    )

    # Pixelate to sprite size
    sprite = pixelate_image(raw_bytes, target_size=64)

    # Upload as reference for EvoLink
    ref_url = _upload_reference(_pil_to_bytes(sprite))
    print(f"  [evolink] Reference uploaded: {ref_url}")

    cache_key = f"ref_{uuid.uuid4().hex[:8]}"
    return sprite, cache_key


def generate_variant_frame(
    prompt: str,
    reference_images: list,
    quality: str = SANDBOX_QUALITY,
    pixelate_to: int = 64,
    api_key: str = None,
) -> Image.Image:
    """
    Generate a variant frame using EvoLink with reference images.
    Returns a PIL Image at pixelate_to resolution.
    """
    raw_bytes = evolink_generate_sprite(
        prompt=prompt,
        reference_images=reference_images,
        quality=quality,
        api_key=api_key,
    )

    # Convert bytes to PIL
    img = Image.open(io.BytesIO(raw_bytes)).convert("RGBA")

    # Pixelate to target sprite size
    if pixelate_to:
        img = _pixelate(img, pixelate_to)

    return img


def generate_sprite_sheet_evolink(
    base_character: str,
    actions: list,
    sprite_size: int = 64,
    quality: str = SANDBOX_QUALITY,
    frames_per_action: int = 4,
    directions: int = 8,
    api_key: str = None,
    output_dir: str = "output",
) -> dict:
    """
    Generate a full sprite sheet using hybrid FLUX + EvoLink pipeline.

    Strategy:
    1. Generate 1 reference character with FLUX
    2. Generate all other frames with EvoLink using reference
    """
    key = api_key or os.environ.get("EVOLINK_API_KEY")

    # Step 1: Generate reference with FLUX
    print(f"  [evolink] Generating reference character with FLUX...")
    ref_sprite, ref_key = generate_reference_character(
        base_character=base_character,
        api_key=key,
    )

    # Step 2: Generate all frames with EvoLink using reference
    # For now, we'll generate in sequence
    all_frames = []
    action_seeds = {}

    from prompt_builder import build_action_prompt

    for action_idx, action in enumerate(actions):
        action_seed = int(uuid.uuid4().hex[:8], 16) % 1000000
        action_seeds[action] = action_seed

        print(f"  [evolink] Generating {action} ({frames_per_action} frames)...")

        for frame_idx in range(frames_per_action):
            pose_prompt = build_action_prompt(action, frame_idx, frames_per_action)
            full_prompt = f"{base_character}, {pose_prompt}, pixel art style"

            # Generate with reference
            frame = generate_variant_frame(
                prompt=full_prompt,
                reference_images=[_pil_to_bytes(ref_sprite)],
                quality=quality,
                pixelate_to=sprite_size,
                api_key=key,
            )

            all_frames.append(frame)
            print(f"    [evolink] {action} frame {frame_idx+1}/{frames_per_action} done")

    # Step 3: Assemble sprite sheet
    return assemble_sprite_sheet(all_frames, actions, frames_per_action,
                                  sprite_size, output_dir)


# ─── Utilities ───────────────────────────────────────────────────────────────
def _pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _pixelate(img: Image.Image, size: int) -> Image.Image:
    """Pixelate an image to the target size."""
    # Downscale fast
    small = img.resize((size // 4, size // 4), Image.Resampling.NEAREST)
    # Upscale back (pixelated look)
    result = small.resize((size, size), Image.Resampling.NEAREST)
    return result


def assemble_sprite_sheet(
    frames: list,
    actions: list,
    frames_per_action: int,
    frame_size: int,
    output_dir: str,
) -> dict:
    """Assemble frames into a sprite sheet."""
    from generator import assemble_spritesheet

    action_frames = []
    idx = 0
    for action in actions:
        action_frame_paths = []
        for _ in range(frames_per_action):
            if idx < len(frames):
                # Save temp frame
                tmp_path = f"/tmp/frame_{idx:03d}.png"
                frames[idx].save(tmp_path)
                action_frame_paths.append(tmp_path)
                idx += 1
        action_frames.append((action, action_frame_paths))

    output_path = Path(output_dir) / f"evolink_sprite_{uuid.uuid4().hex[:8]}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result = assemble_spritesheet(
        action_frames=action_frames,
        output_name=str(output_path).replace(".png", ""),
        frame_size=frame_size,
        frames_per_row=frames_per_action,
        output_dir=str(output_path.parent),
    )

    return result

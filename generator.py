"""Image generation via DeepInfra API (OpenAI-compatible)."""

import base64
import json
import time
import requests
from pathlib import Path
from typing import List, Tuple
from PIL import Image, ImageFilter
import io


DEFAULT_CONFIG = "config.json"


def load_config() -> dict:
    path = Path(__file__).parent / DEFAULT_CONFIG
    return json.loads(path.read_text())


def enhance_prompt(prompt: str) -> str:
    """Append pixel art keywords to improve sprite-relevant generation."""
    additions = [
        "pixel art",
        "game sprite",
        "transparent background",
        "clean lines",
        "crisp edges",
        "no dithering",
        "clean pixel art style",
    ]
    # Avoid doubling keywords if already present
    prompt_lower = prompt.lower()
    for kw in additions:
        if kw.lower() not in prompt_lower:
            prompt = f"{prompt}, {kw}"
    return prompt


def generate_frame(prompt: str, size: int = 512, config: dict = None, seed: int = None) -> bytes:
    """Generate a single image frame from a prompt."""
    if config is None:
        config = load_config()

    headers = {
        "Authorization": f"Bearer {config['deepinfra_api_key']}",
        "Content-Type": "application/json",
    }

    enhanced = enhance_prompt(prompt)

    payload = {
        "model": config["model"],
        "prompt": enhanced,
        "image_size": str(size),
        "num_inference_steps": config["generation_steps"],
        "response_format": "b64_json",
    }

    if seed is not None:
        payload["seed"] = int(seed)

    api_url = config.get("deepinfra_base_url", "https://api.deepinfra.com/v1/openai/images/generations")

    response = requests.post(
        api_url,
        headers=headers,
        json=payload,
        timeout=config.get("generation_timeout", 120)
    )

    if response.status_code != 200:
        raise RuntimeError(f"API error {response.status_code}: {response.text}")

    data = response.json()
    b64 = data["data"][0]["b64_json"]
    return base64.b64decode(b64)


def pixelate_image(image_bytes: bytes, target_size: int) -> Image.Image:
    """Convert an AI-generated image to a pixel art sprite at target_size."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

    # Resize down to target size with nearest-neighbor (pixel art scaling)
    img_small = img.resize((target_size, target_size), Image.Resampling.NEAREST)

    # Slightly sharpen to crisp pixel edges
    img_small = img_small.filter(ImageFilter.SHARPEN)

    return img_small


def generate_sprite_frames(
    base_prompt: str,
    frame_prompts: List[str],
    sprite_size: int = 64,
    config: dict = None,
    progress_callback=None
) -> List[Tuple[str, Image.Image]]:
    """Generate multiple sprite frames from prompts.
    
    Returns list of (prompt_label, PIL Image) tuples.
    """
    if config is None:
        config = load_config()

    results = []

    for i, frame_prompt in enumerate(frame_prompts):
        full_prompt = f"{base_prompt}, {frame_prompt}"

        if progress_callback:
            progress_callback(i, len(frame_prompts), f"Generating frame {i+1}/{len(frame_prompts)}...")

        try:
            raw_bytes = generate_frame(full_prompt, size=512, config=config)
            sprite_img = pixelate_image(raw_bytes, sprite_size)
            results.append((frame_prompt, sprite_img))
        except Exception as e:
            raise RuntimeError(f"Failed to generate frame {i+1} ('{frame_prompt}'): {e}")

    return results


def save_frames(frames: List[Tuple[str, Image.Image]], output_dir: str = None) -> List[str]:
    """Save frames as numbered PNGs, return list of paths."""
    if output_dir is None:
        output_dir = Path(__file__).parent / "frames"
    else:
        output_dir = Path(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Clear old frames
    for f in output_dir.glob("frame_*.png"):
        f.unlink()

    paths = []
    for i, (_, img) in enumerate(frames):
        path = output_dir / f"frame_{i:03d}.png"
        img.save(str(path), "PNG")
        paths.append(str(path))

    return paths


# Action prompt modifiers — these get appended to the base prompt per frame
ACTION_PROMPTS = {
    "idle": "standing idle, neutral stance, subtle breathing motion",
    "walk": "walking animation frame, mid-stride",
    "run": "running animation frame, fast motion",
    "attack": "attacking with weapon, arm extended",
    "cast": "casting spell, arms raised, magic gesture",
    "jump": "jumping, legs tucked, arms up",
    "dance": "dancing pose, energetic movement",
    "death": "defeated pose, falling down",
    "dodge": "dodging, quick evasive lean",
    "hurt": "injured, recoiling",
    "block": "blocking with weapon, defensive stance",
}

DEFAULT_ACTIONS = ["idle", "walk", "run", "attack"]

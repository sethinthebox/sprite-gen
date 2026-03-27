"""
Hybrid Sprite Generation Pipeline with Validation

Strategy: Fail cheap, succeed expensively.
1. Test FLUX prompt (FREE) — validates the character description works
2. Test ONE EvoLink frame (~$0.036) — validates reference + prompt
3. Generate full sheet if validated (31 more frames at $0.036 each)

Cost to validate: 1 frame = $0.036
Cost if validated: 32 total frames = ~$1.15
"""
import io
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from PIL import Image

from evolink_gen import (
    generate as evolink_generate,
    download_image,
    submit_generation,
    poll_task,
    estimate_cost,
)
from generator import (
    generate_frame as flux_generate,
    load_config,
    pixelate_image,
    assemble_spritesheet,
)


# ─── Constants ────────────────────────────────────────────────────────────────
SANDBOX_QUALITY = "0.5K"
EVOLINK_API_KEY = os.environ.get(
    "EVOLINK_API_KEY",
    Path("/opt/sprite-gen/.evolink_key").expanduser().read_text().strip()
    if Path("/opt/sprite-gen/.evolink_key").expanduser().exists()
    else ""
)

# VPS public URL for reference uploads
VPS_REF_URL = "http://69.48.207.73/tricorder/releases/v0.5/"

# ─── Step 1: FLUX validation (FREE) ─────────────────────────────────────────
def validate_flux_prompt(
    base_character: str,
    style_suffix: str = "pixel art style, clean lines, transparent background",
    size: int = 512,
) -> dict:
    """
    Test if FLUX can generate a valid character from the prompt.
    This is FREE — we just want to validate the prompt works.

    Returns:
        {
            "success": bool,
            "frame": PIL Image or None,
            "error": str or None,
            "pixelate_time": float,
        }
    """
    prompt = f"{base_character}, {style_suffix}"

    try:
        config = load_config()
        start = datetime.now()
        raw_bytes = flux_generate(
            prompt=prompt,
            size=size,
            config=config,
            seed=None,
        )
        elapsed = (datetime.now() - start).total_seconds()

        sprite = pixelate_image(raw_bytes, target_size=64)
        pixelate_time = (datetime.now() - datetime.fromtimestamp(start.timestamp())).total_seconds()

        # Quick sanity check — does it have content?
        pixels = list(sprite.getdata())
        opaque = sum(1 for p in pixels if p[3] > 10)
        if opaque < 500:
            return {
                "success": False,
                "frame": sprite,
                "error": f"Generated sprite has very few opaque pixels ({opaque})",
                "pixelate_time": pixelate_time,
            }

        return {
            "success": True,
            "frame": sprite,
            "error": None,
            "elapsed": elapsed,
            "pixelate_time": pixelate_time,
        }

    except Exception as e:
        return {
            "success": False,
            "frame": None,
            "error": str(e),
            "elapsed": 0,
            "pixelate_time": 0,
        }


# ─── Step 2: EvoLink single-frame validation (~$0.036) ─────────────────────
def _upload_ref_image(img: Image.Image, prefix: str = "evoref") -> Optional[str]:
    """Upload a PIL image to VPS and return public URL."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    filename = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
    local_path = f"/tmp/{filename}"
    vps_dest = f"/var/www/tricorder/releases/v0.5/{filename}"

    with open(local_path, "wb") as f:
        f.write(buf.read())

    try:
        shutil.copy(local_path, vps_dest)
        return VPS_REF_URL + filename
    except Exception as e:
        print(f"  [pipeline] WARNING: could not upload ref: {e}")
        return None


def validate_evolink_reference(
    base_character: str,
    reference_frame: Image.Image,
    test_pose: str = "standing neutral, facing camera",
    quality: str = SANDBOX_QUALITY,
) -> dict:
    """
    Test if EvoLink can generate a consistent frame using the reference.
    Costs: 1 frame = ~2.58 credits = ~$0.036

    Returns:
        {
            "success": bool,
            "frame": PIL Image or None,
            "error": str or None,
            "task_id": str or None,
            "cost": float (credits),
        }
    """
    prompt = f"{base_character}, {test_pose}, pixel art style"

    # Upload reference
    ref_url = _upload_ref_image(reference_frame, prefix="evoref")
    if not ref_url:
        return {
            "success": False,
            "frame": None,
            "error": "Failed to upload reference image",
            "task_id": None,
            "cost": 0,
        }

    try:
        cost_estimate = estimate_cost(1, quality)
        task_id = submit_generation(
            prompt=prompt,
            quality=quality,
            size="1:1",
            reference_urls=[ref_url],
            api_key=EVOLINK_API_KEY,
        )

        result = poll_task(task_id, api_key=EVOLINK_API_KEY, max_wait=120)

        if result.get("status") != "completed":
            return {
                "success": False,
                "frame": None,
                "error": f"Task {result.get('status')}: {result.get('error', 'unknown')}",
                "task_id": task_id,
                "cost": cost_estimate,
            }

        image_url = result["results"][0]
        img_bytes = download_image(image_url)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")

        # Pixelate to sprite size
        sprite = img.resize((64, 64), Image.Resampling.NEAREST)
        sprite_2x = img.resize((128, 128), Image.Resampling.NEAREST)

        # Quick sanity — does it look like pixel art?
        pixels = list(sprite.getdata())
        opaque = sum(1 for p in pixels if p[3] > 10)
        if opaque < 500:
            return {
                "success": False,
                "frame": sprite,
                "error": f"EvoLink output has few opaque pixels ({opaque})",
                "task_id": task_id,
                "cost": cost_estimate,
            }

        return {
            "success": True,
            "frame": sprite,
            "error": None,
            "task_id": task_id,
            "cost": cost_estimate,
            "raw": img,
            "raw_2x": sprite_2x,
        }

    except Exception as e:
        return {
            "success": False,
            "frame": None,
            "error": str(e),
            "task_id": None,
            "cost": estimate_cost(1, quality),
        }


# ─── Full validation + generation pipeline ────────────────────────────────────
def generate_character_sprite_sheet(
    base_character: str,
    actions: list = None,
    sprite_size: int = 64,
    quality: str = SANDBOX_QUALITY,
    output_dir: str = "output",
    max_frames_per_action: int = 4,
    directions: int = 8,
) -> dict:
    """
    Full pipeline with validation:

    1. FLUX validation (FREE) — test prompt
    2. EvoLink validation (~$0.036) — test 1 frame with reference
    3. Full generation if validated — rest of frames

    Returns:
        {
            "status": "validated" | "failed" | "error",
            "flax_valid": bool,
            "evolink_valid": bool,
            "sheet_url": str or None,
            "cost_total": float (credits),
            "cost_breakdown": dict,
            "errors": list,
        }
    """
    if actions is None:
        actions = ["idle", "walk"]

    result = {
        "status": "error",
        "flux_valid": False,
        "evolink_valid": False,
        "sheet_url": None,
        "cost_total": 0.0,
        "cost_breakdown": {"flux": 0, "evolink_validation": 0, "evolink_full": 0},
        "errors": [],
        "validation_frames": {},
    }

    generation_id = uuid.uuid4().hex[:8]

    # ── STEP 1: FLUX validation ──────────────────────────────────────────────
    print(f"[pipeline] Step 1: FLUX prompt validation (FREE)...")
    flux_result = validate_flux_prompt(base_character)

    if not flux_result["success"]:
        result["errors"].append(f"FLUX failed: {flux_result['error']}")
        print(f"[pipeline]   FAILED: {flux_result['error']}")
        return result

    result["flux_valid"] = True
    reference_frame = flux_result["frame"]
    print(f"[pipeline]   OK — FLUX generated valid sprite ({flux_result.get('elapsed', '?')}s)")

    # Save reference preview
    ref_preview = reference_frame.copy()
    ref_preview.save(f"/tmp/{generation_id}_ref_preview.png")

    # ── STEP 2: EvoLink validation (1 frame) ───────────────────────────────────
    print(f"[pipeline] Step 2: EvoLink reference validation (~$0.036)...")
    evo_result = validate_evolink_reference(
        base_character=base_character,
        reference_frame=reference_frame,
        test_pose="standing neutral, arms at sides, facing camera",
        quality=quality,
    )

    result["cost_breakdown"]["evolink_validation"] = evo_result["cost"]
    result["cost_total"] += evo_result["cost"]

    if not evo_result["success"]:
        result["errors"].append(f"EvoLink failed: {evo_result['error']}")
        print(f"[pipeline]   FAILED: {evo_result['error']}")
        result["status"] = "failed"
        return result

    result["evolink_valid"] = True
    print(f"[pipeline]   OK — EvoLink generated consistent frame")

    # Save validation frame
    if evo_result.get("raw_2x"):
        evo_result["raw_2x"].save(f"/tmp/{generation_id}_evo_validation.png")

    # ── STEP 3: Full sheet generation ────────────────────────────────────────
    print(f"[pipeline] Step 3: Generating full sheet...")

    # Calculate total frames
    total_frames = len(actions) * max_frames_per_action
    remaining_frames = total_frames - 1  # Already generated 1 for validation
    remaining_cost = estimate_cost(remaining_frames, quality)
    result["cost_breakdown"]["evolink_full"] = remaining_cost
    result["cost_total"] += remaining_cost

    print(f"[pipeline]   {remaining_frames} more frames = ~{remaining_cost:.2f} credits")

    # Upload reference for batch generation
    ref_url = _upload_ref_image(reference_frame, prefix=f"evoref_{generation_id}")
    if not ref_url:
        result["errors"].append("Failed to upload reference for batch")
        result["status"] = "error"
        return result

    # Generate remaining frames in batch
    all_frames = [evo_result["frame"]]  # Start with validated frame
    frame_costs = [evo_result["cost"]]

    for action in actions:
        for frame_idx in range(max_frames_per_action):
            if action == actions[0] and frame_idx == 0:
                continue  # Skip first — already validated

            pose_prompt = _build_frame_prompt(action, frame_idx, max_frames_per_action)
            full_prompt = f"{base_character}, {pose_prompt}, pixel art style"

            print(f"[pipeline]   Generating {action} frame {frame_idx+1}/{max_frames_per_action}...")

            try:
                task_id = submit_generation(
                    prompt=full_prompt,
                    quality=quality,
                    size="1:1",
                    reference_urls=[ref_url],
                    api_key=EVOLINK_API_KEY,
                )
                evo_frame = poll_task(task_id, api_key=EVOLINK_API_KEY, max_wait=120)

                if evo_frame.get("status") != "completed":
                    print(f"[pipeline]     WARNING: frame failed: {evo_frame.get('error')}")
                    # Use previous frame as fallback
                    all_frames.append(all_frames[-1])
                    frame_costs.append(0)
                    continue

                img_bytes = download_image(evo_frame["results"][0])
                img = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
                sprite = img.resize((sprite_size, sprite_size), Image.Resampling.NEAREST)
                all_frames.append(sprite)
                frame_costs.append(estimate_cost(1, quality))

            except Exception as e:
                print(f"[pipeline]     ERROR: {e}")
                all_frames.append(all_frames[-1])  # Fallback to previous
                frame_costs.append(0)

    # ── Assemble sheet ──────────────────────────────────────────────────────
    output_path = Path(output_dir) / f"hybrid_{generation_id}.png"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create grid sheet
    cols = max_frames_per_action
    rows = len(actions)
    sheet = Image.new("RGBA", (sprite_size * cols, sprite_size * rows), (0, 0, 0, 0))

    idx = 0
    for row, action in enumerate(actions):
        for col in range(max_frames_per_action):
            if idx < len(all_frames):
                sheet.paste(all_frames[idx], (col * sprite_size, row * sprite_size))
                idx += 1

    # 2x preview
    sheet_2x = sheet.resize((sheet.width * 2, sheet.height * 2), Image.Resampling.NEAREST)

    sheet.save(str(output_path))
    sheet_2x.save(str(output_path).replace(".png", "_2x.png"))

    # Upload to VPS
    vps_path = f"/var/www/tricorder/releases/v0.5/hybrid_{generation_id}.png"
    vps_path_2x = f"/var/www/tricorder/releases/v0.5/hybrid_{generation_id}_2x.png"
    try:
        sheet.save(vps_path)
        sheet_2x.save(vps_path_2x)
        result["sheet_url"] = VPS_REF_URL + f"hybrid_{generation_id}.png"
        result["sheet_url_2x"] = VPS_REF_URL + f"hybrid_{generation_id}_2x.png"
    except Exception as e:
        result["errors"].append(f"Upload failed: {e}")

    result["status"] = "validated"
    result["frame_costs"] = frame_costs
    print(f"[pipeline] DONE — Sheet: {result.get('sheet_url')}")
    print(f"[pipeline] Total cost: {result['cost_total']:.2f} credits")

    return result


def _build_frame_prompt(action: str, frame_idx: int, total_frames: int) -> str:
    """Build pose description for a specific frame."""
    phase = frame_idx / total_frames  # 0.0 to 1.0

    prompts = {
        "idle": [
            "standing neutral, arms relaxed at sides",
            "slight weight shift, one shoulder slightly raised",
            "arms hanging naturally, relaxed stance",
            "subtle breathing motion, slight chest rise",
        ],
        "walk": [
            "left foot lifted, right arm forward",
            "left foot forward and planted, right foot lifting back",
            "right foot lifted, left arm forward",
            "right foot forward and planted, left foot lifting back",
        ],
        "run": [
            "body leaning forward, left foot pushing off",
            "mid-stride, both feet briefly off ground",
            "body leaning forward, right foot pushing off",
            "recovery stride, arms pumping",
        ],
        "attack": [
            "weapon raised high, ready to strike",
            "weapon mid-swing, body rotated",
            "weapon descending, impact moment",
            "follow-through, returning to ready stance",
        ],
        "jump": [
            "crouch before jump, knees bent",
            "feet leaving ground, arms up",
            "peak of jump, body fully extended",
            "landing, knees bending to absorb impact",
        ],
    }

    default_prompts = [
        "slight pose shift, weight distribution change",
        "continuing motion, natural movement",
        "mid-action, dynamic pose",
        "action completion, returning to neutral",
    ]

    action_prompts = prompts.get(action, default_prompts)
    idx = frame_idx % len(action_prompts)
    return action_prompts[idx]


# ─── CLI test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--character", required=True)
    parser.add_argument("--actions", default="idle,walk")
    parser.add_argument("--quality", default="0.5K")
    args = parser.parse_args()

    result = generate_character_sprite_sheet(
        base_character=args.character,
        actions=args.actions.split(","),
        quality=args.quality,
    )

    print(json.dumps(result, indent=2, default=str))

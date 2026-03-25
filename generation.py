"""Generation orchestration layer — coordinates the full sprite generation pipeline."""

import json
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

from style import load_style as _load_style, get_style_keywords
from reference import get_reference
from consistency import build_consistent_prompt, apply_modifications
from prompt_builder import build_full_prompt, build_action_prompt, estimate_quality
from generator import (
    generate_frame,
    pixelate_image,
    save_frames,
    load_config,
)
from prompt_builder import ACTION_PROMPTS
from assembler import assemble_spritesheet


DEFAULT_STYLE_PATH = Path(__file__).parent / "defaults" / "style-guide-default.json"
LOG_FILE = Path(__file__).parent / "generation-log.jsonl"


def _log_generation(entry: dict):
    """Append a generation log entry to the JSONL log file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def generate_sprite_sheet(
    description: str,
    actions: List[str],
    grid_size: int,
    sprite_size: int,
    style_guide: Optional[dict] = None,
    reference_id: Optional[str] = None,
    modifications: Optional[str] = None,
    seed: Optional[int] = None,
    config: Optional[dict] = None,
    api_key: Optional[str] = None,
) -> dict:
    """Full sprite sheet generation pipeline.

    Args:
        description: Base character/object description
        actions: List of action names (e.g. ["idle", "walk", "attack"])
        grid_size: NxN grid for the sprite sheet (e.g. 4 = 4x4)
        sprite_size: Pixel size of each sprite (e.g. 64)
        style_guide: Style guide dict (loaded from default if None)
        reference_id: Reference image ID for style matching
        modifications: Natural language modifications to apply
        seed: Random seed for reproducibility
        config: Config dict (loaded from config.json if None)
        api_key: DeepInfra API key override

    Returns:
        dict with paths and metadata:
            - sheet_path, json_path, frames_paths, generation_id
            - prompt_quality, style_consistency, timing
    """
    generation_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    # 1. Load style guide (or use default)
    if style_guide is None:
        if DEFAULT_STYLE_PATH.exists():
            style_guide = _load_style(str(DEFAULT_STYLE_PATH))

    # 2. Load config
    if config is None:
        config = load_config()

    # Override API key if provided
    if api_key:
        config["deepinfra_api_key"] = api_key

    # 3. Load reference if provided
    reference = None
    if reference_id:
        reference = get_reference(reference_id)

    # 4. Build prompts for each frame
    # Determine base prompt with consistency
    base_prompt = description
    if reference_id or modifications:
        base_prompt = build_consistent_prompt(
            original_prompt=description,
            reference_id=reference_id,
            style_guide=style_guide,
            modifications=modifications,
        )

    # Build action prompts for each frame
    frame_prompts = []
    for action in actions:
        action_desc = build_action_prompt(action, sprite_size)
        full_frame_prompt = f"{base_prompt}, {action_desc}"
        frame_prompts.append((action, full_frame_prompt))

    # 5. Generate each frame via DeepInfra API
    generated_frames = []
    errors = []

    for i, (action, prompt) in enumerate(frame_prompts):
        try:
            raw_bytes = generate_frame(prompt, size=512, config=config)
            sprite_img = pixelate_image(raw_bytes, sprite_size)
            generated_frames.append((action, sprite_img))
        except Exception as e:
            errors.append({"frame": i, "action": action, "error": str(e)})

    if not generated_frames:
        raise RuntimeError(f"All frames failed to generate. Errors: {errors}")

    # 6. Save frames
    frames_dir = Path(config.get("frames_dir", "frames"))
    frame_paths = save_frames(generated_frames, output_dir=str(frames_dir))

    # 7. Assemble sprite sheet
    output_dir = Path(config.get("output_dir", "output"))
    output_name = f"sprite_{generation_id}"
    sheet_result = assemble_spritesheet(
        frame_paths=frame_paths,
        grid_size=grid_size,
        output_name=output_name,
        output_dir=str(output_dir),
    )

    # 8. Estimate prompt quality
    avg_quality = sum(
        estimate_quality(p, style_guide) for _, p in frame_prompts
    ) / len(frame_prompts) if frame_prompts else 0

    # 9. Log generation
    elapsed = time.time() - start_time
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "generation_id": generation_id,
        "description": description,
        "actions": actions,
        "grid_size": grid_size,
        "sprite_size": sprite_size,
        "reference_id": reference_id,
        "modifications": modifications,
        "seed": seed,
        "style_guide_name": style_guide.get("name") if style_guide else None,
        "prompts": [{"action": a, "prompt": p} for a, p in frame_prompts],
        "errors": errors,
        "frame_count": len(generated_frames),
        "sheet_path": sheet_result["sheet_path"],
        "json_path": sheet_result["json_path"],
        "prompt_quality_score": round(avg_quality, 1),
        "elapsed_seconds": round(elapsed, 2),
    }
    _log_generation(log_entry)

    return {
        "generation_id": generation_id,
        "sheet_path": sheet_result["sheet_path"],
        "json_path": sheet_result["json_path"],
        "frames_paths": frame_paths,
        "frame_count": len(generated_frames),
        "error_count": len(errors),
        "errors": errors,
        "prompt_quality_score": round(avg_quality, 1),
        "elapsed_seconds": round(elapsed, 2),
        "config": {
            "description": description,
            "actions": actions,
            "grid_size": grid_size,
            "sprite_size": sprite_size,
            "reference_id": reference_id,
            "modifications": modifications,
        },
    }


def regenerate_frame(
    frame_index: int,
    previous_result: dict,
    new_action: Optional[str] = None,
    modifications: Optional[str] = None,
    config: Optional[dict] = None,
    api_key: Optional[str] = None,
) -> dict:
    """Regenerate a single frame from a previous generation.

    Useful for fixing a bad frame without regenerating the entire sheet.
    """
    if config is None:
        config = load_config()
    if api_key:
        config["deepinfra_api_key"] = api_key

    # Get the original prompt for this frame
    prompts = previous_result.get("config", {}).get("actions", [])
    original_description = previous_result.get("config", {}).get("description", "")

    if new_action:
        action = new_action
    elif frame_index < len(prompts):
        action = prompts[frame_index]
    else:
        raise ValueError(f"Frame index {frame_index} out of range")

    # Build new prompt
    if modifications:
        prompt = f"{original_description}, {modifications}, {build_action_prompt(action, previous_result.get('config', {}).get('sprite_size', 64))}"
    else:
        prompt = f"{original_description}, {build_action_prompt(action, previous_result.get('config', {}).get('sprite_size', 64))}"

    # Generate
    raw_bytes = generate_frame(prompt, size=512, config=config)
    sprite_img = pixelate_image(raw_bytes, previous_result.get("config", {}).get("sprite_size", 64))

    return {
        "frame_index": frame_index,
        "action": action,
        "prompt": prompt,
        "image": sprite_img,
    }


def get_recent_generations(count: int = 10) -> List[dict]:
    """Get the N most recent generation log entries."""
    if not LOG_FILE.exists():
        return []

    entries = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    return entries[-count:]


def get_generation_stats() -> dict:
    """Get aggregate statistics from the generation log."""
    if not LOG_FILE.exists():
        return {"total_generations": 0}

    entries = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if not entries:
        return {"total_generations": 0}

    total_frames = sum(e.get("frame_count", 0) for e in entries)
    total_errors = sum(e.get("error_count", 0) for e in entries)
    avg_quality = sum(e.get("prompt_quality_score", 0) for e in entries) / len(entries)
    avg_time = sum(e.get("elapsed_seconds", 0) for e in entries) / len(entries)

    return {
        "total_generations": len(entries),
        "total_frames_generated": total_frames,
        "total_errors": total_errors,
        "average_quality_score": round(avg_quality, 1),
        "average_generation_time_seconds": round(avg_time, 1),
    }

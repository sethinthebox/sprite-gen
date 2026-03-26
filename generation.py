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
from prompt_builder import (
    build_full_prompt,
    build_action_prompt,
    build_sheet_prompt,
    build_base_character,
    estimate_quality,
    ACTION_PROMPTS,
)
from generator import (
    generate_frame,
    generate_batch,
    pixelate_image,
    save_frames,
    load_config,
)
from assembler import assemble_spritesheet, generate_gif_from_actions


DEFAULT_STYLE_PATH = Path(__file__).parent / "defaults" / "style-guide-default.json"
LOG_FILE = Path(__file__).parent / "generation-log.jsonl"


def _log_generation(entry: dict):
    """Append a generation log entry to the JSONL log file."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def generate_sprite_sheet(
    base_character: str,
    actions: List[str],
    sprite_size: int = 64,
    style_suffix: str = "retro pixel art, no background, transparent PNG",
    config_path: str = None,
) -> dict:
    """Generate a full sprite sheet with all actions as rows.

    Steps:
        1. Build prompts for all (action, frame) combinations
        2. Generate all frames via batch API
        3. Assemble into sheet (each action = 1 row, 4 frames per row)
        4. Generate GIF preview
        5. Return paths to sheet, JSON metadata, GIF

    Args:
        base_character: The character description (e.g.
            "isometric pixel art older businessman, late 50s, gray temples...").
        actions: List of action names, e.g. ["idle", "walk", "run", "jump"].
        sprite_size: Pixel size of each frame (default 64).
        style_suffix: Additional style keywords (default includes transparent PNG).
        config_path: Optional path to config dict.

    Returns:
        dict with keys: generation_id, sheet_path, metadata_path, gif_path,
        frames_paths, frames_per_row, actions_config, elapsed_seconds.
    """
    generation_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    frames_per_row = 4

    # 1. Load config
    config = load_config()

    # 2. Normalize base character
    base = build_base_character(base_character)

    # 3. Build all (action, prompt) tuples for all frames
    # Each action gets 4 frames (0,1,2,3)
    frame_tasks = []  # List of (label, prompt) for generator.generate_batch
    frame_labels = []  # parallel list of (action, frame_num) for organization

    for action in actions:
        for frame_num in range(frames_per_row):
            prompt = build_sheet_prompt(
                base_character=base,
                action=action,
                frame_number=frame_num,
                total_frames=frames_per_row,
                style_suffix=style_suffix,
            )
            frame_tasks.append((f"{action}_{frame_num}", prompt))
            frame_labels.append((action, frame_num))

    # 4. Generate all frames in batch
    generated = generate_batch(frame_tasks, size=512, config=config)
    # generated is List[Tuple[str, PIL.Image]] — same order as frame_tasks

    # 5. Save frames — save_frames returns flat list of paths
    # We need to organize them per-action for the assembler
    frame_paths = save_frames(generated, output_dir=str(Path(config.get("frames_dir", "frames"))))

    # 6. Build action_frames for assembler: [(action, [path, path, path, path]), ...]
    action_frames: List[tuple] = []
    for i, action in enumerate(actions):
        action_path_start = i * frames_per_row
        action_path_list = frame_paths[action_path_start:action_path_start + frames_per_row]
        action_frames.append((action, action_path_list))

    # 7. Assemble sprite sheet
    output_dir = Path(config.get("output_dir", "output"))
    output_name = f"sprite_{generation_id}"
    sheet_result = assemble_spritesheet(
        action_frames=action_frames,
        output_name=output_name,
        frame_size=sprite_size,
        frames_per_row=frames_per_row,
        output_dir=str(output_dir),
    )

    # 8. Generate GIF preview — cycle through first action's frames for a quick preview
    gif_path = output_dir / f"{output_name}.gif"
    # Generate GIF that cycles through all actions
    gif_path = output_dir / f"{output_name}.gif"
    gif_result = generate_gif_from_actions(action_frames, str(gif_path), delay_per_frame=120, loop=0)

    # 9. Log
    elapsed = time.time() - start_time
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "generation_id": generation_id,
        "base_character": base_character,
        "actions": actions,
        "sprite_size": sprite_size,
        "style_suffix": style_suffix,
        "frame_count": len(frame_paths),
        "sheet_path": sheet_result["sheet_path"],
        "metadata_path": sheet_result["metadata_path"],
        "gif_path": str(gif_path) if gif_result else None,
        "elapsed_seconds": round(elapsed, 2),
    }
    _log_generation(log_entry)

    return {
        "generation_id": generation_id,
        "sheet_path": sheet_result["sheet_path"],
        "metadata_path": sheet_result["metadata_path"],
        "gif_path": str(gif_path) if gif_result else None,
        "frames_paths": frame_paths,
        "frames_per_row": frames_per_row,
        "actions_config": [{"name": a, "frames": list(range(i * frames_per_row, (i + 1) * frames_per_row))} for i, a in enumerate(actions)],
        "elapsed_seconds": round(elapsed, 2),
    }


# ── Legacy compatibility: old generate_sprite_sheet signature ─────────────────

def generate_sprite_sheet_legacy(
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
    """Legacy sprite sheet generation (grid-based, one action per N frames).

    This function is kept for backwards compatibility. New code should use
    :func:`generate_sprite_sheet` which generates one action per row.
    """
    generation_id = str(uuid.uuid4())[:8]
    start_time = time.time()

    if style_guide is None and DEFAULT_STYLE_PATH.exists():
        style_guide = _load_style(str(DEFAULT_STYLE_PATH))

    if config is None:
        config = load_config()
    if api_key:
        config["deepinfra_api_key"] = api_key

    base_prompt = description
    if reference_id or modifications:
        base_prompt = build_consistent_prompt(
            original_prompt=description,
            reference_id=reference_id,
            style_guide=style_guide,
            modifications=modifications,
        )

    frame_prompts = []
    for action in actions:
        action_desc = build_action_prompt(action, sprite_size)
        full_frame_prompt = f"{base_prompt}, {action_desc}"
        frame_prompts.append((action, full_frame_prompt))

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

    frames_dir = Path(config.get("frames_dir", "frames"))
    frame_paths = save_frames(generated_frames, output_dir=str(frames_dir))

    output_dir = Path(config.get("output_dir", "output"))
    output_name = f"sprite_{generation_id}"

    # Build action_frames for the new assembler format
    action_frames = []
    for i, action in enumerate(actions):
        start = i * grid_size
        end = start + grid_size
        action_frames.append((action, frame_paths[start:end]))

    sheet_result = assemble_spritesheet(
        action_frames=action_frames,
        output_name=output_name,
        frame_size=sprite_size,
        frames_per_row=grid_size,
        output_dir=str(output_dir),
    )

    avg_quality = sum(estimate_quality(p) for _, p in frame_prompts) / max(len(frame_prompts), 1)

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
        "json_path": sheet_result["metadata_path"],
        "prompt_quality_score": round(avg_quality, 1),
        "elapsed_seconds": round(elapsed, 2),
    }
    _log_generation(log_entry)

    return {
        "generation_id": generation_id,
        "sheet_path": sheet_result["sheet_path"],
        "json_path": sheet_result["metadata_path"],
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
    """Regenerate a single frame from a previous generation."""
    if config is None:
        config = load_config()
    if api_key:
        config["deepinfra_api_key"] = api_key

    prompts = previous_result.get("config", {}).get("actions", [])
    original_description = previous_result.get("config", {}).get("description", "")

    if new_action:
        action = new_action
    elif frame_index < len(prompts):
        action = prompts[frame_index]
    else:
        raise ValueError(f"Frame index {frame_index} out of range")

    if modifications:
        prompt = f"{original_description}, {modifications}, {build_action_prompt(action, previous_result.get('config', {}).get('sprite_size', 64))}"
    else:
        prompt = f"{original_description}, {build_action_prompt(action, previous_result.get('config', {}).get('sprite_size', 64))}"

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
    avg_quality = sum(e.get("prompt_quality_score", 0) for e in entries) / max(len(entries), 1)
    avg_time = sum(e.get("elapsed_seconds", 0) for e in entries) / max(len(entries), 1)

    return {
        "total_generations": len(entries),
        "total_frames_generated": total_frames,
        "total_errors": total_errors,
        "average_quality_score": round(avg_quality, 1),
        "average_generation_time_seconds": round(avg_time, 1),
    }

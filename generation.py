"""Generation orchestration layer — coordinates the full sprite generation pipeline."""

import json
import os
import time
import uuid
import random
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict

from style import load_style as _load_style, get_style_keywords
from reference import get_reference
from consistency import build_consistent_prompt, apply_modifications
from prompt_builder import (
    build_full_prompt,
    build_action_prompt,
    build_base_character,
    estimate_quality,
    ACTION_PROMPTS,
)
from generator import (
    generate_frame,
    pixelate_image,
    load_config,
    validate_frame,
    normalize_sprite,
)

MAX_RETRIES = 3

# Frame ranker: use vision model when available, QC rules as fallback
try:
    from frame_ranker import select_candidates, select_best, qc_score
    RANKER_AVAILABLE = True
except Exception as e:
    print(f"  [ranker] could not import: {e}")
    RANKER_AVAILABLE = False
    select_candidates = None
    qc_score = None

# Number of candidate frames to generate per animation frame
# More candidates = better selection, slower generation
N_CANDIDATES = int(os.environ.get("FRAME_N_CANDIDATES", "3")) if os.environ.get("FRAME_N_CANDIDATES") else 3
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
    user_seed: Optional[int] = None,
    config_path: str = None,
) -> dict:
    """Generate a full sprite sheet with all actions as rows.

    Strategy: Generate each action's 4 frames as a group, using a shared
    seed per action. This keeps FLUX consistent within an action while
    allowing different actions to vary (e.g. walk pose vs run pose).

    Steps:
        1. Build prompts for each action (4 distinct pose descriptions per action)
        2. Generate each action's frames sequentially with a shared seed
        3. Pixelate and save frames
        4. Assemble into sprite sheet (each action = 1 row)
        5. Generate GIF preview cycling through all actions
        6. Return paths to sheet, JSON metadata, GIF

    Args:
        base_character: The character description (e.g.
            "isometric pixel art older businessman, late 50s, gray temples...").
        actions: List of action names, e.g. ["idle", "walk", "run", "jump"].
        sprite_size: Pixel size of each frame (default 64).
        style_suffix: Additional style keywords.

    Returns:
        dict with keys: generation_id, sheet_path, metadata_path, gif_path,
        frames_paths, frames_per_row, actions_config, elapsed_seconds.
    """
    generation_id = str(uuid.uuid4())[:8]
    start_time = time.time()
    frames_per_row = 4

    config = load_config()
    base = build_base_character(base_character)

    # Pre-generate seeds: one per action, stored for reproducibility
    # Use user_seed as base if provided, otherwise random
    base_seed = user_seed if user_seed is not None else random.randint(0, 2**31)
    # Derive one seed per action from base seed
    seeds = [(base_seed + i) % (2**31) for i in range(len(actions))]

    # Collect all action_frames for assembler
    action_frames: List[tuple] = []

    # Generate each action's 4 frames as a group
    for action_idx, action in enumerate(actions):
        # Use the pre-generated seed for this action (reproducible)
        action_seed = seeds[action_idx]

        print(f"\n[{action_idx+1}/{len(actions)}] Generating action: {action} (seed={action_seed})")

        # Build 4 distinct pose prompts for this action
        # Each frame gets a specific pose description (not "frame 0/4" which FLUX ignores)
        frame_prompts = _build_action_frame_prompts(base, action, style_suffix)

        # Shared reference feet_y: first action's first good frame anchors all others
        # Stored on the function object for cross-action sharing
        ref_feet_y = getattr(generate_sprite_sheet, '_ref_feet_y', None)
        if ref_feet_y is None and action_idx > 0:
            # Should have been set by first action — warn
            print(f"  WARNING: no reference feet_y from action 0, using default")

        action_imgs = []
        for frame_idx, (pose_desc, full_prompt) in enumerate(frame_prompts):
            print(f"  [{frame_idx+1}/{frames_per_row}] {pose_desc[:60]}...")
            is_first_frame = (action_idx == 0 and frame_idx == 0)
            frame_ref_feet_y = ref_feet_y  # None = use default in validate_frame

            # ── Generate N candidate frames ────────────────────────────────────
            # Generate multiple candidates and pick the best via vision model
            candidates: List[tuple] = []  # (sprite_img, qc_result, seed)

            for cand_idx in range(N_CANDIDATES):
                try:
                    seed = action_seed + cand_idx
                    raw = generate_frame(
                        prompt=full_prompt,
                        size=512,
                        config=config,
                        seed=seed,
                    )
                    sprite = pixelate_image(raw, sprite_size)
                    qc = validate_frame(sprite, sprite_size,
                                      reference_feet_y=frame_ref_feet_y)
                    candidates.append((sprite, qc, seed))
                    if qc.passed:
                        print(f"    candidate {cand_idx+1}: QC passed (feet={qc.feet_y})")
                    else:
                        print(f"    candidate {cand_idx+1}: QC fail — {qc.reasons}")
                except Exception as e:
                    print(f"    candidate {cand_idx+1}: ERROR — {e}")

            if not candidates:
                print(f"    WARNING: no candidates generated, skipping")
                action_imgs.append(None)
                continue

            # ── Set reference feet_y from first good candidate of first frame ─
            if is_first_frame and ref_feet_y is None:
                good = next((c for c in candidates if c[1].passed), None)
                if good:
                    ref_feet_y = good[1].feet_y
                    generate_sprite_sheet._ref_feet_y = ref_feet_y
                    print(f"    Reference feet_y={ref_feet_y} set")

            # ── Select best candidate ─────────────────────────────────────────
            best_sprite = None  # type: ignore
            best_result = None  # type: ignore
            if len(candidates) == 1:
                best_sprite, best_result, _ = candidates[0]
            elif RANKER_AVAILABLE and select_candidates is not None:
                # Use vision model to pick best (override QC scores with ranker assessment)
                imgs = [c[0] for c in candidates]
                try:
                    best_idx, ranker_scores = select_candidates(imgs, action)
                    # Override QC scores with ranker scores and pick best
                    for i, (c, ranker_score) in enumerate(zip(candidates, ranker_scores)):
                        c[1].score = ranker_score  # c[1] is the QCResult (mutable)
                    best_idx, _ = max(enumerate(ranker_scores), key=lambda x: x[1])
                    best_sprite, best_result, _ = candidates[best_idx]
                    print(f"    [ranker] → selected (score={best_result.score:.1f})")
                except Exception as e:
                    print(f"    [ranker] fallback to QC: {e}")
                    scores = [(i, c[1].score) for i, c in enumerate(candidates)]
                    best_idx = max(scores, key=lambda x: x[1])[0]
                    best_sprite, best_result, _ = candidates[best_idx]
            else:
                # No ranker: pick best QC score
                scores = [(i, c[1].score) for i, c in enumerate(candidates)]
                best_idx = max(scores, key=lambda x: x[1])[0]
                best_sprite, best_result, _ = candidates[best_idx]

            # ── Normalize with shared reference after selection ───────────────
            normalized = normalize_sprite(best_sprite, sprite_size,
                                         reference_feet_y=ref_feet_y)

            print(f"    → selected (QC={best_result.score:.1f}, feet={best_result.feet_y})")
            action_imgs.append(normalized)

        # Save action frames to disk
        frames_dir = Path(config.get("frames_dir", "frames"))
        frame_paths = _save_action_frames(action, action_idx, action_imgs, frames_dir)
        action_frames.append((action, frame_paths))

    # Assemble sprite sheet
    output_dir = Path(config.get("output_dir", "output"))
    output_name = f"sprite_{generation_id}"
    sheet_result = assemble_spritesheet(
        action_frames=action_frames,
        output_name=output_name,
        frame_size=sprite_size,
        frames_per_row=frames_per_row,
        output_dir=str(output_dir),
    )

    # Generate GIF cycling through all actions
    gif_path = output_dir / f"{output_name}.gif"
    gif_result = generate_gif_from_actions(
        action_frames, str(gif_path), delay_per_frame=120, loop=0
    )

    # Collect flat list of frame paths
    all_frame_paths = []
    for _, paths in action_frames:
        all_frame_paths.extend(paths)

    elapsed = time.time() - start_time
    _log_generation({
        "timestamp": datetime.utcnow().isoformat(),
        "generation_id": generation_id,
        "base_character": base_character,
        "actions": actions,
        "action_seeds": {action: seeds[i] for i, action in enumerate(actions)},
        "sprite_size": sprite_size,
        "style_suffix": style_suffix,
        "frame_count": len(all_frame_paths),
        "sheet_path": sheet_result["sheet_path"],
        "metadata_path": sheet_result["metadata_path"],
        "gif_path": str(gif_path) if gif_result else None,
        "elapsed_seconds": round(elapsed, 2),
    })

    return {
        "generation_id": generation_id,
        "sheet_path": sheet_result["sheet_path"],
        "metadata_path": sheet_result["metadata_path"],
        "gif_path": str(gif_path) if gif_result else None,
        "frames_paths": all_frame_paths,
        "frames_per_row": frames_per_row,
        "actions_config": [
            {"name": a, "frames": list(range(i * frames_per_row, (i + 1) * frames_per_row))}
            for i, a in enumerate(actions)
        ],
        "action_seeds": {action: seeds[i] for i, action in enumerate(actions)},
        "elapsed_seconds": round(elapsed, 2),
    }


def _build_action_frame_prompts(
    base_character: str,
    action: str,
    style_suffix: str,
) -> List[tuple]:
    """Build 4 distinct pose prompts for one action's animation cycle.

    Each frame gets a specific pose description rather than "frame N/4".
    Returns list of (pose_description, full_prompt) tuples.

    The FLUX model understands sequential animation descriptions but not
    fractional frame numbers — describing the actual pose is more reliable.
    """
    # Pose descriptions per action — 4 distinct frames per animation cycle
    poses_by_action = {
        "idle": [
            "standing neutral, arms relaxed at sides, slight weight on right leg",
            "standing, slight chest rise, shoulders up, natural breathing in",
            "standing neutral, arms relaxed, balanced stance",
            "standing, slight chest fall, shoulders down, natural breathing out",
        ],
        "walk": [
            "walking cycle, left foot forward, right arm forward, step forward pose",
            "walking cycle, both feet passing, arms swinging naturally, mid-stride",
            "walking cycle, right foot forward, left arm forward, step forward pose",
            "walking cycle, both feet passing, arms swinging through, mid-stride",
        ],
        "run": [
            "running cycle, left leg extended forward, arms pumping, leaning forward",
            "running cycle, peak stride, both feet off ground briefly, dynamic pose",
            "running cycle, right leg extended forward, arms pumping, leaning forward",
            "running cycle, peak stride opposite, both feet off ground, full extension",
        ],
        "jump": [
            "jumping, crouch before takeoff, knees bent, arms pulled back",
            "jumping, leaving ground, legs tucking under, arms rising",
            "jumping, peak of jump, legs fully tucked under body, arms up",
            "jumping, descending, legs extending for landing, arms out for balance",
        ],
        "attack": [
            "attacking, weapon drawn back, wind-up pose before strike",
            "attacking, weapon at peak extension, full strike moment",
            "attacking, weapon following through, recovering from strike",
            "attacking, weapon returning to ready position, brief reset",
        ],
        "cast": [
            "casting spell, hands together gathering energy, concentration pose",
            "casting spell, arms thrust forward, energy release moment",
            "casting spell, energy dissipating, arms pulling back, fade pose",
            "casting spell, hands parting, magic fading, return to ready",
        ],
        "dance": [
            "dancing, arms raised, weight on left foot, rhythmic opening pose",
            "dancing, body twisted left, arms sweeping, peak movement",
            "dancing, arms lowered, weight shifting right, transition",
            "dancing, body twisted right, arms through sweep, closing pose",
        ],
        "death": [
            "dying, impact frame, torso recoiling, arms flung wide",
            "dying, stumbling backward, legs buckling, desperate expression",
            "dying, falling to knees, one hand reaching out",
            "dying, collapsed on ground, lying prone, final moment",
        ],
        "dodge": [
            "dodging, body shifting left, weight transferring, lean start",
            "dodging, body fully leaning left, legs crossed, quick evade peak",
            "dodging, returning to neutral, weight shifting back",
            "dodging, body shifted right, ready stance, dodge complete",
        ],
        "hurt": [
            "hurt, impact frame, recoiling from hit, arms up defensively",
            "hurt, stumbling back, clutching affected area, grimacing",
            "hurt, staggering, trying to recover, off-balance pose",
            "hurt, leaning forward, bracing, pain reaction",
        ],
        "block": [
            "blocking, shield raised high, defensive stance, guarding",
            "blocking, shield at full extension, absorbing impact, braced",
            "blocking, shield lowering, recovering from block",
            "blocking, shield at ready position, alert stance",
        ],
    }

    default_poses = [
        f"{action} animation, frame 1 of 4, dynamic pose",
        f"{action} animation, frame 2 of 4, peak action pose",
        f"{action} animation, frame 3 of 4, follow-through pose",
        f"{action} animation, frame 4 of 4, recovery pose",
    ]

    poses = poses_by_action.get(action.lower(), default_poses)

    result = []
    for i, pose in enumerate(poses):
        full_prompt = f"{base_character}, {pose}, {style_suffix}"
        result.append((pose, full_prompt))

    return result


def _save_action_frames(
    action: str,
    action_idx: int,
    imgs: List,
    frames_dir: Path,
) -> List[str]:
    """Save a list of PIL Images as numbered PNG frames.

    Returns list of file paths in order.
    """
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths = []

    for frame_idx, img in enumerate(imgs):
        if img is None:
            # Create a placeholder — 1x1 transparent PNG
            from PIL import Image
            img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))

        global_idx = action_idx * 4 + frame_idx
        path = frames_dir / f"frame_{global_idx:03d}.png"
        img.save(str(path), "PNG")
        paths.append(str(path))

    return paths


# ── Legacy compatibility ──────────────────────────────────────────────────────

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
    """Legacy grid-based sprite sheet generation."""
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
    frame_paths = _save_action_frames("legacy", 0, [img for _, img in generated_frames], frames_dir)

    output_dir = Path(config.get("output_dir", "output"))
    output_name = f"sprite_{generation_id}"

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
    _log_generation({
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
    })

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

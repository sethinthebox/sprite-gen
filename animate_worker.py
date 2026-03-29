#!/usr/bin/env python3
"""
Background worker for directional animation.
Run as: python3 animate_worker.py <job_id>

Generates 8 directions × 4 frames × N actions = 32+ FLUX calls.
Updates job state after each frame so the UI can poll progress.
"""
import sys
import os
import time
import traceback
from pathlib import Path

# ── Ensure sprite-gen is on path ───────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from generator import generate_frame, load_config, pixelate_image
from raw_qc import qc_raw_flux_image
from assembler import assemble_spritesheet, generate_gif
import animate_jobs

DIRECTIONS = {
    "N":  "facing north, back to viewer, walking away",
    "NE": "facing northeast, back-right to viewer, 3/4 angle",
    "E":  "facing east, side profile, right arm visible",
    "SE": "facing southeast, front-right to viewer",
    "SW": "facing southwest, front-left to viewer",
    "S":  "facing south, front to viewer, walking toward",
    "W":  "facing west, side profile, left arm visible",
    "NW": "facing northwest, back-left to viewer, 3/4 angle",
}

WALK_FRAME_DETAILS = {
    0: "left foot forward, right arm forward, step forward, stride beginning",
    1: "both feet passing, mid-stride, maximum leg separation",
    2: "right foot forward, left arm forward, opposite of frame 0",
    3: "both feet passing, returning to starting position",
}

IDLE_FRAME_DETAILS = {
    0: "standing neutral, arms relaxed at sides",
    1: "slight chest rise, shoulders up, breathing in",
    2: "standing neutral, arms relaxed, balanced stance",
    3: "slight chest fall, shoulders down, breathing out",
}

ACTION_FRAME_DETAILS = {
    "idle": IDLE_FRAME_DETAILS,
    "walk": WALK_FRAME_DETAILS,
}

FRAME_DETAILS = {
    "idle": IDLE_FRAME_DETAILS,
    "walk": WALK_FRAME_DETAILS,
    "run": {
        0: "left leg extended forward, leaning into stride",
        1: "peak stride, both feet off ground briefly",
        2: "right leg extended back, left arm forward",
        3: "mid-stride passing, arms pumping",
    },
}


def build_directional_prompt(base_character: str, action: str, frame_idx: int, direction_desc: str) -> str:
    """Build full prompt for a direction + action + frame combination."""
    frame_map = FRAME_DETAILS.get(action, WALK_FRAME_DETAILS)
    frame_detail = frame_map.get(frame_idx, f"animation frame {frame_idx}")
    action_word = {"idle": "standing", "walk": "walking", "run": "running"}.get(action, "walking")

    parts = [
        base_character.strip(),
        direction_desc,
        frame_detail,
        f"retro pixel art, no background, transparent PNG",
    ]
    return ", ".join(p for p in parts if p)


def generate_with_retry(prompt: str, seed: int, config: dict, max_retries: int = 3) -> bytes:
    """Generate a frame with raw QC retry. Returns raw bytes."""
    for attempt in range(max_retries):
        raw_bytes = generate_frame(prompt=prompt, size=512, config=config, seed=seed)
        if qc_raw_flux_image is not None:
            qc_result = qc_raw_flux_image(raw_bytes, expected_figures=1)
            if not qc_result.passed:
                seed = (seed + 1) % (2**31)
                continue
        return raw_bytes
    # All retries failed — return last raw
    return raw_bytes


def run_job(job_id: str):
    """Main worker loop for a single job."""
    job = animate_jobs.get_job(job_id)
    if not job:
        print(f"[worker] Job {job_id} not found")
        return

    base_character = job["base_character"]
    actions = job["actions"]
    base_seed = job["seed"] or int(time.time()) % (2**31)
    sprite_size = job.get("sprite_size", 64)

    config = load_config()
    output_dir = Path(config.get("output_dir", "output"))
    output_dir.mkdir(exist_ok=True)

    print(f"[worker] Starting job {job_id}: {actions}, seed={base_seed}")

    animate_jobs.update_job(job_id, status="running")

    total = job["progress"]["total"]
    frames_generated = 0
    all_frame_paths = {}  # {(action, direction, frame_idx): path}
    errors = []

    dir_keys = list(DIRECTIONS.keys())

    try:
        for action_idx, action in enumerate(actions):
            frame_map = FRAME_DETAILS.get(action, WALK_FRAME_DETAILS)

            for dir_idx, (dir_name, dir_desc) in enumerate(DIRECTIONS.items()):
                for frame_idx in range(4):
                    prompt = build_directional_prompt(base_character, action, frame_idx, dir_desc)
                    seed = (base_seed + dir_idx * 4 + frame_idx + action_idx * 32) % (2**31)

                    try:
                        raw_bytes = generate_with_retry(prompt, seed, config)
                        sprite = pixelate_image(raw_bytes, sprite_size)

                        filename = f"dir_{job_id}_{action}_{dir_name}_f{frame_idx}.png"
                        frame_path = output_dir / filename
                        sprite.save(str(frame_path), format="PNG")

                        all_frame_paths[(action, dir_name, frame_idx)] = str(frame_path)
                        frames_generated += 1

                        # Update progress
                        pct = int(100 * frames_generated / total)
                        animate_jobs.update_job(
                            job_id,
                            status="running",
                            frames_generated=frames_generated,
                            progress={"current": frames_generated, "total": total, "pct": pct},
                        )
                        print(f"[worker] {job_id}: {frames_generated}/{total} — {action}/{dir_name}/f{frame_idx}")

                    except Exception as e:
                        err_msg = f"{action}/{dir_name}/f{frame_idx}: {e}"
                        errors.append(err_msg)
                        print(f"[worker] ERROR {err_msg}")
                        # Continue with next frame rather than aborting whole job

        # ── Assemble results ──────────────────────────────────────────────────

        print(f"[worker] {job_id}: All frames generated. Assembling sheets...")

        # Group by action: each action gets a row of 8 directions × 4 frames
        gif_urls = {}

        for action in actions:
            # Collect 8 directions × 4 frames for this action
            action_frames = []
            for dir_name in dir_keys:
                dir_frames = []
                for frame_idx in range(4):
                    path = all_frame_paths.get((action, dir_name, frame_idx))
                    if path:
                        dir_frames.append(path)
                if dir_frames:
                    action_frames.extend(dir_frames)

            if not action_frames:
                continue

            # Assemble into a sheet: 8 cols (directions) × 4 rows (frames)
            # But simpler: just make a GIF of all 32 frames in order
            gif_path = output_dir / f"dir_{job_id}_{action}.gif"
            gif_result = generate_gif(action_frames, str(gif_path), delay=100)
            if gif_result:
                gif_urls[action] = f"/sprite/output/{gif_path.name}"
            else:
                gif_urls[action] = None

        # Sheet: assemble per direction (each direction = one row, 4 frames across)
        sheet_frames = []
        sheet_rows = []
        for dir_name in dir_keys:
            dir_row = []
            for frame_idx in range(4):
                path = all_frame_paths.get((action, dir_name, frame_idx))
                if path:
                    dir_row.append(path)
            if dir_row:
                sheet_rows.append((dir_name, dir_row))

        if sheet_rows:
            sheet_name = f"dir_{job_id}_sheet.png"
            sheet_path = output_dir / sheet_name

            # Assemble with each direction as a row, 4 frames per row
            action_frames_for_asm = [(f"dir_{d}", fps) for d, fps in sheet_rows if len(fps) == 4]
            if action_frames_for_asm:
                try:
                    asm_result = assemble_spritesheet(
                        action_frames=action_frames_for_asm,
                        output_name=f"dir_{job_id}_sheet",
                        frame_size=sprite_size,
                        frames_per_row=4,
                        output_dir=str(output_dir),
                    )
                    sheet_url = f"/sprite/output/{Path(asm_result['sheet_path']).name}"
                except Exception as e:
                    print(f"[worker] Assembly error: {e}")
                    sheet_url = None
            else:
                sheet_url = None
        else:
            sheet_url = None

        result = {
            "gif_urls": gif_urls,
            "sheet_url": sheet_url,
            "frames_generated": frames_generated,
            "total_frames": total,
            "errors": errors[:10],  # cap errors in result
        }

        animate_jobs.update_job(
            job_id,
            status="done",
            result=result,
            frames_generated=frames_generated,
            progress={"current": total, "total": total, "pct": 100},
        )

        print(f"[worker] Job {job_id} DONE. {frames_generated} frames. Errors: {len(errors)}")

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[worker] Job {job_id} ERROR: {e}\n{tb}")
        animate_jobs.update_job(
            job_id,
            status="error",
            error=str(e),
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 animate_worker.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    run_job(job_id)

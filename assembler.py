"""Sprite sheet assembly — pure Python / PIL (no Aseprite required).

Produces:
    - A sprite sheet PNG (grid of frames, one row per action)
    - A JSON metadata file
    - Optionally an animated GIF

No external tools needed.
"""

import json
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image


def assemble_spritesheet(
    action_frames: List[Tuple[str, List[str]]],
    output_name: str,
    frame_size: int = 64,
    frames_per_row: int = 4,
    output_dir: Optional[str] = None,
) -> dict:
    """Assemble frames into a sheet where each action gets its own row.

    Args:
        action_frames: List of (action_name, [frame_paths]) tuples.
            Each action has exactly 4 frames.
            Example:
                [
                    ("idle", ["frame_000.png", "frame_001.png", "frame_002.png", "frame_003.png"]),
                    ("walk", ["frame_004.png", "frame_005.png", "frame_006.png", "frame_007.png"]),
                    ("run",  ["frame_008.png", ...]),
                ]
        output_name: Base filename for output (no extension).
        frame_size: Size of each frame in pixels (default 64).
        frames_per_row: Always 4 for standard layout.
        output_dir: Output directory. Defaults to ``../output/`` relative to this file.

    Returns:
        dict with keys: sheet_path, metadata_path, grid_cols, grid_rows,
        frames (list of frame info), sheet_size.
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "output"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not action_frames:
        raise ValueError("No action_frames provided")

    # Determine sheet dimensions: frames_per_row columns, one row per action
    cols = frames_per_row
    rows = len(action_frames)

    sheet_w = cols * frame_size
    sheet_h = rows * frame_size

    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

    frames_meta = []
    global_frame_index = 0

    for row_idx, (action_name, frame_paths) in enumerate(action_frames):
        if len(frame_paths) != frames_per_row:
            raise ValueError(
                f"Action '{action_name}' has {len(frame_paths)} frames, "
                f"expected {frames_per_row}"
            )
        for col_idx, path_str in enumerate(frame_paths):
            frame_img = Image.open(path_str).convert("RGBA")
            x = col_idx * frame_size
            y = row_idx * frame_size
            sheet.paste(frame_img, (x, y), frame_img)

            frames_meta.append({
                "action": action_name,
                "row": row_idx,
                "col": col_idx,
                "global_index": global_frame_index,
                "path": path_str,
            })
            global_frame_index += 1

    sheet_path = output_dir / f"{output_name}.png"
    sheet.save(str(sheet_path), "PNG", optimize=False)

    # Build JSON metadata with per-action rows
    actions_meta = []
    frame_index = 0
    for row_idx, (action_name, frame_paths) in enumerate(action_frames):
        actions_meta.append({
            "row": row_idx,
            "name": action_name,
            "frames": list(range(frame_index, frame_index + len(frame_paths))),
        })
        frame_index += len(frame_paths)

    metadata = {
        "name": output_name,
        "frame_width": frame_size,
        "frame_height": frame_size,
        "frames_per_row": cols,
        "grid_cols": cols,
        "grid_rows": rows,
        "sheet_width": sheet_w,
        "sheet_height": sheet_h,
        "actions": actions_meta,
    }

    metadata_path = output_dir / f"{output_name}.json"
    metadata_path.write_text(json.dumps(metadata, indent=2))

    return {
        "sheet_path": str(sheet_path),
        "metadata_path": str(metadata_path),
        "sheet_size": (sheet_w, sheet_h),
        "frame_size": (frame_size, frame_size),
        "grid_cols": cols,
        "grid_rows": rows,
        "frames": frames_meta,
    }


def generate_gif(
    frame_paths: List[str],
    output_path: str,
    delay: int = 100,
    loop: int = 0,
) -> Optional[str]:
    """Build an animated GIF from a list of frame images.

    Transparent pixels are composited over a black background since
    GIF does not support full RGBA.

    Args:
        frame_paths: Ordered list of PNG frame file paths.
        output_path: Destination path for the GIF file.
        delay: Frame display time in milliseconds.
        loop: Loop count (0 = infinite).

    Returns:
        The output path if the file was saved, else None.
    """
    gif_frames = []
    for path in frame_paths:
        img = Image.open(path).convert("RGBA")
        if img.mode != "RGB":
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background
        gif_frames.append(img)

    if not gif_frames:
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    gif_frames[0].save(
        str(output_path),
        save_all=True,
        append_images=gif_frames[1:],
        duration=delay,
        loop=loop,
        optimize=False,
    )

    return str(output_path) if output_path.exists() else None

"""Sprite sheet assembly — pure Python / PIL (no Aseprite required).

Produces:
    - A sprite sheet PNG (grid of frames)
    - A JSON metadata file (Aseprite-compatible format)
    - Optionally an animated GIF

No external tools needed.
"""

import io
import json
import math
from pathlib import Path
from typing import List, Optional

from PIL import Image


def assemble_spritesheet(
    frame_paths: List[str],
    grid_size: int,
    output_name: str,
    output_dir: Optional[str] = None,
    frame_padding: int = 0,
) -> dict:
    """Lay out frames in a grid and save as sprite sheet PNG + JSON.

    The JSON format is compatible with Aseprite's sprite sheet data format.

    Args:
        frame_paths: Ordered list of paths to frame PNG files.
        grid_size: Number of columns in the grid (e.g. 4 = 4×4).
        output_name: Base filename for output (no extension).
        output_dir: Output directory. Defaults to ``../output/`` relative to this file.
        frame_padding: Extra transparent pixels between frames.

    Returns:
        dict with keys: ``sheet_path``, ``json_path``, ``sheet_size``,
        ``frame_size``, ``grid``.
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "output"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not frame_paths:
        raise ValueError("No frame paths provided")

    frames = [Image.open(p).convert("RGBA") for p in frame_paths]
    if not frames:
        raise ValueError("No frames could be loaded")

    frame_w, frame_h = frames[0].size
    total_frames = len(frames)
    cols = grid_size
    rows = math.ceil(total_frames / cols)

    sheet_w = cols * (frame_w + frame_padding) - frame_padding
    sheet_h = rows * (frame_h + frame_padding) - frame_padding

    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    for i, frame in enumerate(frames):
        row, col = divmod(i, cols)
        sheet.paste(frame, (col * (frame_w + frame_padding), row * (frame_h + frame_padding)), frame)

    sheet_path = output_dir / f"{output_name}.png"
    sheet.save(str(sheet_path), "PNG", optimize=False)

    # Build frame metadata
    frames_meta = {}
    for i in range(total_frames):
        row, col = divmod(i, cols)
        frame_key = f"frame_{i:03d}.png"
        frames_meta[frame_key] = {
            "frame": {
                "x": col * (frame_w + frame_padding),
                "y": row * (frame_h + frame_padding),
                "w": frame_w,
                "h": frame_h,
            },
            "rotated": False,
            "trimmed": False,
            "spriteSourceSize": {"x": 0, "y": 0, "w": frame_w, "h": frame_h},
            "sourceSize": {"w": frame_w, "h": frame_h},
            "duration": 100,
        }

    metadata = {
        "frames": frames_meta,
        "meta": {
            "app": "sprite-gen",
            "version": "1.0",
            "image": f"{output_name}.png",
            "format": "RGBA8888",
            "size": {"w": sheet_w, "h": sheet_h},
            "scale": "1",
        },
    }

    json_path = output_dir / f"{output_name}.json"
    json_path.write_text(json.dumps(metadata, indent=2))

    return {
        "sheet_path": str(sheet_path),
        "json_path": str(json_path),
        "sheet_size": (sheet_w, sheet_h),
        "frame_size": (frame_w, frame_h),
        "grid": (cols, rows),
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
    frames = []
    for path in frame_paths:
        img = Image.open(path).convert("RGBA")
        if img.mode != "RGB":
            # Composite transparent areas over black for GIF compatibility
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background
        frames.append(img)

    if not frames:
        return None

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    frames[0].save(
        str(output_path),
        save_all=True,
        append_images=frames[1:],
        duration=delay,
        loop=loop,
        optimize=False,
    )

    return str(output_path) if output_path.exists() else None

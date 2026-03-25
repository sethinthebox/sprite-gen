"""Sprite sheet assembly — pure Python (no Aseprite required)."""

import json
import math
from pathlib import Path
from PIL import Image
from typing import List, Tuple, Optional


def assemble_spritesheet(
    frame_paths: List[str],
    grid_size: int,
    output_name: str,
    output_dir: str = None,
    frame_padding: int = 0,
) -> dict:
    """Assemble frames into a sprite sheet PNG + JSON metadata.
    
    Pure Python implementation using PIL — no Aseprite required.
    
    Args:
        frame_paths: List of paths to frame PNG files
        grid_size: NxN grid (e.g. 4 = 4x4)
        output_name: Base name for output files (no extension)
        output_dir: Directory for output files
        frame_padding: Extra pixels between frames
    
    Returns:
        dict with 'sheet_path', 'json_path'
    """
    if output_dir is None:
        output_dir = Path(__file__).parent.parent / "output"
    else:
        output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if not frame_paths:
        raise ValueError("No frame paths provided")

    # Load all frames
    frames = []
    for path in frame_paths:
        img = Image.open(path).convert("RGBA")
        frames.append(img)

    if not frames:
        raise ValueError("No frames could be loaded")

    frame_w, frame_h = frames[0].size
    total_frames = len(frames)

    # Determine grid dimensions
    cols = grid_size
    rows = math.ceil(total_frames / cols)

    # Calculate sheet dimensions
    sheet_w = cols * (frame_w + frame_padding) - frame_padding
    sheet_h = rows * (frame_h + frame_padding) - frame_padding

    # Create sheet
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

    # Paste frames into grid
    for i, frame in enumerate(frames):
        row = i // cols
        col = i % cols
        x = col * (frame_w + frame_padding)
        y = row * (frame_h + frame_padding)
        sheet.paste(frame, (x, y), frame)

    # Save sheet
    sheet_path = output_dir / f"{output_name}.png"
    sheet.save(str(sheet_path), "PNG", optimize=False)

    # Generate JSON metadata
    frames_meta = []
    for i in range(total_frames):
        row = i // cols
        col = i % cols
        frames_meta.append({
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
        })

    json_path = output_dir / f"{output_name}.json"
    metadata = {
        "frames": {f"frame_{i:03d}.png": fm for i, fm in enumerate(frames_meta)},
        "meta": {
            "app": "sprite-gen",
            "version": "1.0",
            "image": f"{output_name}.png",
            "format": "RGBA8888",
            "size": {"w": sheet_w, "h": sheet_h},
            "scale": "1",
            "frameTags": _build_frame_tags(total_frames, cols),
        }
    }

    json_path.write_text(json.dumps(metadata, indent=2))

    return {
        "sheet_path": str(sheet_path),
        "json_path": str(json_path),
        "sheet_size": (sheet_w, sheet_h),
        "frame_size": (frame_w, frame_h),
        "grid": (cols, rows),
    }


def _build_frame_tags(total_frames: int, cols: int) -> List[dict]:
    """Build Aseprite-compatible frame tags for animation."""
    # Common action cycle — we tag each frame's action based on its position
    tags = []
    return tags


def generate_gif_pil(
    frame_paths: List[str],
    output_path: str,
    delay: int = 100,
    loop: int = 0,
) -> Optional[str]:
    """Generate an animated GIF from frames using PIL.
    
    Args:
        frame_paths: List of frame PNG paths
        output_path: Output GIF path
        delay: Frame delay in ms
        loop: 0 = infinite loop
    """
    frames = []
    for path in frame_paths:
        img = Image.open(path).convert("RGBA")
        # GIF doesn't support RGBA properly, convert to RGB + transparency handling
        if img.mode == "RGBA":
            # Make transparent areas white (or use as transparency index)
            background = Image.new("RGB", img.size, (0, 0, 0))
            background.paste(img, mask=img.split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")
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


def extract_frame_from_sheet(
    sheet_path: str,
    frame_index: int,
    grid_cols: int,
    frame_w: int,
    frame_h: int,
    padding: int = 0,
) -> Image.Image:
    """Extract a single frame from a sprite sheet."""
    sheet = Image.open(sheet_path).convert("RGBA")
    row = frame_index // grid_cols
    col = frame_index % grid_cols
    x = col * (frame_w + padding)
    y = row * (frame_h + padding)
    return sheet.crop((x, y, x + frame_w, y + frame_h))


def preview_grid(
    frame_paths: List[str],
    grid_size: int,
    max_width: int = 800,
) -> bytes:
    """Generate a preview image of the grid layout as PNG bytes."""
    if not frame_paths:
        return b""

    frames = [Image.open(p).convert("RGBA") for p in frame_paths]
    frame_w, frame_h = frames[0].size
    cols = grid_size
    rows = math.ceil(len(frames) / cols)

    # Scale down for preview if needed
    scale = 1
    if frame_w * cols > max_width:
        scale = max_width / (frame_w * cols)
        if scale < 1:
            frame_w = int(frame_w * scale)
            frame_h = int(frame_h * scale)
            frames = [f.resize((frame_w, frame_h), Image.Resampling.NEAREST) for f in frames]

    sheet_w = cols * frame_w
    sheet_h = rows * frame_h
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

    for i, frame in enumerate(frames):
        row = i // cols
        col = i % cols
        sheet.paste(frame, (col * frame_w, row * frame_h), frame)

    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()

"""Reference image system — extract and store visual characteristics from reference sprites."""

import json
import hashlib
import os
from pathlib import Path
from typing import Optional, List, Dict

try:
    from PIL import Image
    import numpy as np
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

REF_LIBRARY_DIR = Path(__file__).parent / "reference-library"
METADATA_FILE = REF_LIBRARY_DIR / "references.json"


def _ensure_library_dir():
    """Ensure the reference library directory exists."""
    REF_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    if not METADATA_FILE.exists():
        with open(METADATA_FILE, "w", encoding="utf-8") as f:
            json.dump({}, f)


def _load_metadata() -> dict:
    _ensure_library_dir()
    with open(METADATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_metadata(metadata: dict):
    _ensure_library_dir()
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)


def extract_palette(image_path: str, num_colors: int = 8) -> List[str]:
    """Extract dominant colors from an image as hex strings.

    Algorithm: resize to 8x8, collect all unique pixel colors,
    cluster by proximity, return the N most dominant.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL and numpy are required for palette extraction")

    img = Image.open(image_path).convert("RGBA")
    img_small = img.resize((8, 8), Image.Resampling.NEAREST)
    pixels = np.array(img_small)

    # Collect unique RGBA pixels as hex strings
    unique_colors = {}
    for row in pixels:
        for pixel in row:
            key = tuple(int(c) for c in pixel)
            if key[3] > 10:  # Skip nearly-transparent pixels
                hex_str = f"#{key[0]:02x}{key[1]:02x}{key[2]:02x}"
                unique_colors[hex_str] = unique_colors.get(hex_str, 0) + 1

    if not unique_colors:
        return []

    # Sort by frequency
    sorted_colors = sorted(unique_colors.items(), key=lambda x: -x[1])

    # If we have fewer colors than requested, return all
    if len(sorted_colors) <= num_colors:
        return [c[0] for c in sorted_colors]

    # Simple clustering: take evenly-spaced samples from sorted by frequency
    # to get a good spread of light/dark/mid
    step = max(1, len(sorted_colors) // num_colors)
    selected = []
    for i in range(0, len(sorted_colors), step):
        if len(selected) >= num_colors:
            break
        selected.append(sorted_colors[i][0])

    return selected


def extract_style_hints(image_path: str) -> dict:
    """Extract style hints from a reference image.

    Detects: pixel density, color temperature, brightness,
    outline presence, palette size estimate.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL is required for style hint extraction")

    img = Image.open(image_path).convert("RGBA")
    w, h = img.size
    pixels = np.array(img)

    # Count non-transparent pixels
    opaque_count = np.sum(pixels[:, :, 3] > 10)
    total_pixels = w * h
    fill_ratio = opaque_count / total_pixels

    # Average brightness (only opaque pixels)
    opaque_mask = pixels[:, :, 3] > 10
    if opaque_mask.sum() > 0:
        brightness = np.mean(pixels[opaque_mask, :3])
    else:
        brightness = 0

    # Color temperature (warm vs cool — ratio of R to B channel)
    if opaque_mask.sum() > 0:
        avg_r = np.mean(pixels[opaque_mask, 0])
        avg_b = np.mean(pixels[opaque_mask, 2])
        temp = avg_r / (avg_b + 1e-6)
    else:
        temp = 1.0

    # Detect outline presence (look for high-contrast edges)
    gray = np.mean(pixels[:, :, :3], axis=2)
    edge_variance = 0
    if w > 1 and h > 1:
        dx = np.abs(np.diff(gray, axis=1))
        dy = np.abs(np.diff(gray, axis=0))
        edge_variance = float(np.mean(dx) + np.mean(dy))

    # Estimate unique colors
    unique_colors = len(set(
        tuple(pixels[r, c, :3])
        for r in range(h)
        for c in range(w)
        if pixels[r, c, 3] > 10
    ))

    # Detect if likely transparent background
    corner_pixels = [
        pixels[0, 0],
        pixels[0, -1],
        pixels[-1, 0],
        pixels[-1, -1],
    ]
    likely_transparent_bg = all(p[3] < 10 for p in corner_pixels)

    return {
        "dimensions": {"width": w, "height": h},
        "fill_ratio": round(fill_ratio, 3),
        "brightness": round(float(brightness), 1),
        "color_temperature": round(temp, 3),  # >1 warm, <1 cool
        "edge_variance": round(edge_variance, 3),
        "unique_colors_estimate": unique_colors,
        "likely_transparent_bg": likely_transparent_bg,
        "pixel_density": "high" if w >= 64 else ("medium" if w >= 32 else "low"),
    }


def save_reference(image_bytes: bytes, reference_id: str, metadata: Optional[dict] = None) -> str:
    """Save a reference image and extract its characteristics.

    Returns the path where the image was saved.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL is required for reference image handling")

    _ensure_library_dir()

    # Determine format from bytes
    img = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    ext = _detect_image_format(image_bytes)

    filename = f"{reference_id}.{ext}"
    image_path = REF_LIBRARY_DIR / filename
    image_path.write_bytes(image_bytes)

    # Extract characteristics
    palette = extract_palette(str(image_path))
    hints = extract_style_hints(str(image_path))

    # Update metadata
    meta = _load_metadata()
    meta[reference_id] = {
        "path": str(image_path),
        "filename": filename,
        "palette": palette,
        "hints": hints,
        "metadata": metadata or {},
    }
    _save_metadata(meta)

    return str(image_path)


def _detect_image_format(image_bytes: bytes) -> str:
    """Detect image format from magic bytes."""
    magic = image_bytes[:8]
    if magic.startswith(b"\x89PNG"):
        return "png"
    elif magic.startswith(b"\xff\xd8\xff"):
        return "jpg"
    elif magic.startswith(b"GIF"):
        return "gif"
    elif magic.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "png"


def get_reference(reference_id: str) -> Optional[dict]:
    """Get a reference's full info by ID. Returns None if not found."""
    meta = _load_metadata()
    return meta.get(reference_id)


def list_references() -> List[dict]:
    """List all references with their IDs and basic info."""
    meta = _load_metadata()
    return [
        {
            "reference_id": rid,
            "palette": info.get("palette", []),
            "hints": info.get("hints", {}),
            "metadata": info.get("metadata", {}),
        }
        for rid, info in meta.items()
    ]


def delete_reference(reference_id: str) -> bool:
    """Delete a reference by ID. Returns True if deleted, False if not found."""
    meta = _load_metadata()
    if reference_id not in meta:
        return False

    # Delete image file
    image_path = Path(meta[reference_id]["path"])
    if image_path.exists():
        image_path.unlink()

    # Remove from metadata
    del meta[reference_id]
    _save_metadata(meta)
    return True


# Lazy import for io
import io

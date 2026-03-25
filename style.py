"""Style guide system — defines the visual language for sprite generations."""

import json
from pathlib import Path
from typing import Optional

try:
    from PIL import Image, ImageDraw
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


DEFAULT_STYLE_PATH = Path(__file__).parent / "defaults" / "style-guide-default.json"


def load_style(path: Optional[str] = None) -> dict:
    """Load a style guide from JSON. Falls back to default if no path given."""
    if path is None:
        style_path = DEFAULT_STYLE_PATH
    else:
        style_path = Path(path)

    if not style_path.exists():
        raise FileNotFoundError(f"Style file not found: {style_path}")

    with open(style_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_style(style_dict: dict, path: Optional[str] = None) -> str:
    """Save a style guide to JSON. Returns the path saved to."""
    if path is None:
        style_path = DEFAULT_STYLE_PATH
    else:
        style_path = Path(path)

    style_path.parent.mkdir(parents=True, exist_ok=True)

    with open(style_path, "w", encoding="utf-8") as f:
        json.dump(style_dict, f, indent=2)

    return str(style_path)


def validate_style(style_dict: dict) -> list:
    """Validate a style guide dict. Returns list of issues (empty = valid)."""
    issues = []

    # Required top-level keys
    for key in ("name", "version", "palette", "art_style", "constraints", "keywords"):
        if key not in style_dict:
            issues.append(f"Missing required key: '{key}'")

    if "palette" in style_dict:
        palette = style_dict["palette"]
        for color_key in ("primary", "secondary", "accent", "background", "highlight", "shadow"):
            if color_key in palette:
                if not _is_valid_hex(palette[color_key]):
                    issues.append(f"Palette color '{color_key}' is not valid hex: {palette[color_key]}")
        if "skin_tones" in palette:
            for i, tone in enumerate(palette["skin_tones"]):
                if not _is_valid_hex(tone):
                    issues.append(f"Skin tone[{i}] is not valid hex: {tone}")
        if "forbidden_colors" in palette:
            for i, fc in enumerate(palette["forbidden_colors"]):
                if not _is_valid_hex(fc):
                    issues.append(f"Forbidden color[{i}] is not valid hex: {fc}")

    if "art_style" in style_dict:
        art = style_dict["art_style"]
        valid_pixel_sizes = ("tiny", "small", "medium", "large", "huge")
        if "pixel_size" in art and art["pixel_size"] not in valid_pixel_sizes:
            issues.append(f"pixel_size must be one of {valid_pixel_sizes}, got: {art['pixel_size']}")
        valid_shading = ("flat", "gradient", "cell-shaded")
        if "shading" in art and art["shading"] not in valid_shading:
            issues.append(f"shading must be one of {valid_shading}, got: {art['shading']}")

    if "constraints" in style_dict:
        cons = style_dict["constraints"]
        if "max_colors_per_sprite" in cons:
            if not isinstance(cons["max_colors_per_sprite"], int) or cons["max_colors_per_sprite"] < 1:
                issues.append("constraints.max_colors_per_sprite must be a positive integer")
        if "preferred_sprite_sizes" in cons:
            if not isinstance(cons["preferred_sprite_sizes"], list):
                issues.append("constraints.preferred_sprite_sizes must be a list")
            elif not all(isinstance(s, int) and s > 0 for s in cons["preferred_sprite_sizes"]):
                issues.append("constraints.preferred_sprite_sizes must contain positive integers")

    if "keywords" in style_dict:
        kw = style_dict["keywords"]
        if "always_include" in kw and not isinstance(kw["always_include"], list):
            issues.append("keywords.always_include must be a list")
        if "never_include" in kw and not isinstance(kw["never_include"], list):
            issues.append("keywords.never_include must be a list")

    return issues


def _is_valid_hex(color: str) -> bool:
    """Check if a string is a valid 6-digit hex color."""
    if not isinstance(color, str):
        return False
    color = color.strip()
    if not (color.startswith("#") and len(color) == 7):
        return False
    try:
        int(color[1:], 16)
        return True
    except ValueError:
        return False


def get_style_keywords(style: dict) -> str:
    """Extract style keywords as a comma-separated string to prepend to prompts."""
    parts = []

    if "keywords" in style and "always_include" in style["keywords"]:
        parts.extend(style["keywords"]["always_include"])

    if "art_style" in style:
        art = style["art_style"]
        if "outline" in art:
            parts.append(f"{art['outline']} outline")
        if "shading" in art:
            parts.append(f"{art['shading']} shading")
        if "dithering" in art and art["dithering"] != "none":
            parts.append(f"{art['dithering']} dithering")

    if "constraints" in style:
        cons = style["constraints"]
        if "max_colors_per_sprite" in cons:
            parts.append(f"max {cons['max_colors_per_sprite']} colors")

    return ", ".join(parts)


def export_style_as_png_palette(style: dict, output_path: str) -> str:
    """Generate a PNG swatch of the palette colors from a style guide.

    Creates a horizontal strip of color swatches labeled with their roles.
    """
    if not _PIL_AVAILABLE:
        raise RuntimeError("PIL is required for palette export. Install with: pip install Pillow")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    palette = style.get("palette", {})
    swatch_w = 120
    swatch_h = 80
    label_h = 20
    margin = 4

    color_keys = ["primary", "secondary", "accent", "background", "highlight", "shadow"]
    skin_tones = palette.get("skin_tones", [])
    all_colors = [(k, palette[k]) for k in color_keys if k in palette]
    all_colors += [(f"skin_tone_{i}", t) for i, t in enumerate(skin_tones)]

    if not all_colors:
        raise ValueError("No palette colors found in style")

    total_w = len(all_colors) * swatch_w + (len(all_colors) - 1) * margin
    total_h = swatch_h + label_h + margin

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    for i, (name, hex_color) in enumerate(all_colors):
        x = i * (swatch_w + margin)
        rgb = _hex_to_rgb(hex_color)

        # Draw swatch
        draw.rectangle([x, 0, x + swatch_w - 1, swatch_h - 1], fill=rgb + (255,))

        # Draw label bar below
        draw.rectangle([x, swatch_h, x + swatch_w - 1, swatch_h + label_h - 1], fill=(40, 40, 40, 255))

        # Draw text
        short_name = name.replace("_", " ").title()
        # Simple text without fonts — use default
        draw.text((x + 4, swatch_h + 2), short_name, fill=(220, 220, 220, 255))

    img.save(str(output_path), "PNG")
    return str(output_path)


def _hex_to_rgb(hex_color: str) -> tuple:
    """Convert '#RRGGBB' string to (R, G, B) tuple."""
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

"""Style consistency engine — generate new sprites that match a reference's visual style."""

import re
from typing import Optional, List, Dict
from pathlib import Path

from style import load_style, get_style_keywords
from reference import get_reference


def build_consistent_prompt(
    original_prompt: str,
    reference_id: Optional[str],
    style_guide: Optional[dict],
    modifications: Optional[str] = None,
) -> str:
    """Build an enhanced prompt that maintains style consistency with a reference.

    Takes the original prompt, pulls the reference's palette and style hints,
    applies modifications, and combines with the style guide keywords.

    Args:
        original_prompt: The base prompt to base the generation on
        reference_id: ID of a reference sprite to match style from
        style_guide: Style guide dict (or None to skip)
        modifications: Natural language modifications (e.g. "same character but with red armor")

    Returns:
        Full enhanced prompt string
    """
    parts = []

    # Start with the original prompt (with modifications applied if any)
    if modifications:
        prompt_text = apply_modifications(original_prompt, modifications)
    else:
        prompt_text = original_prompt

    parts.append(prompt_text)

    # Add reference characteristics if available
    if reference_id:
        ref = get_reference(reference_id)
        if ref:
            # Add palette colors
            palette = ref.get("palette", [])
            if palette:
                # Take up to 5 colors from the palette
                palette_str = ", ".join(palette[:5])
                parts.append(f"Color palette: {palette_str}")

            # Add style hints
            hints = ref.get("hints", {})
            if hints.get("pixel_density"):
                parts.append(f"pixel density: {hints['pixel_density']}")
            if hints.get("likely_transparent_bg"):
                parts.append("transparent background")

    # Add style guide keywords
    if style_guide:
        keywords = get_style_keywords(style_guide)
        if keywords:
            parts.append(keywords)

    return ", ".join(parts)


def detect_character_components(prompt: str) -> dict:
    """Parse a prompt into structured character components.

    Returns a dict with: subject, clothing, accessories, pose, view_angle, style
    This enables "same character, different pose" operations.
    """
    result = {
        "subject": None,
        "clothing": None,
        "accessories": None,
        "pose": None,
        "view_angle": None,
        "style": None,
        "raw_parts": [],
    }

    prompt_lower = prompt.lower()

    # View angles
    view_angles = [
        "front view", "side view", "3/4 view", "back view",
        "overhead view", "low angle", "top-down", "isometric",
        "facing front", "facing left", "facing right", "facing away",
    ]
    for va in view_angles:
        if va in prompt_lower:
            result["view_angle"] = va
            break

    # Common style keywords
    style_keywords = [
        "pixel art", "clean pixel art", "game sprite", "isometric",
        "flat shading", "cell-shaded", "hand-drawn", "cartoon",
    ]
    for sk in style_keywords:
        if sk in prompt_lower:
            result["style"] = sk
            break

    # Pose/action keywords
    pose_keywords = [
        "idle", "standing", "walking", "running", "attacking", "casting",
        "jumping", "dancing", "death", "dying", "dodging", "hurt", "blocking",
        "defeated", "victorious", "mid-air", "crouching", "sitting",
    ]
    for pose in pose_keywords:
        if pose in prompt_lower:
            result["pose"] = pose
            break

    # Try to extract subject (first noun phrase before common descriptors)
    # This is a heuristic — look for character types
    character_types = [
        "warrior", "mage", "rogue", "ranger", "knight", "archer",
        "dragon", "beast", "monster", "enemy", "boss", "character",
        "sprite", "creature", "humanoid", "goblin", "skeleton", "zombie",
        "soldier", "guard", "wizard", "witch", "elf", "dwarf", "orc",
    ]
    for char_type in character_types:
        if char_type in prompt_lower:
            result["subject"] = char_type
            break

    # Try to extract clothing/armor
    clothing_words = [
        "armor", "plate", "chainmail", "robe", "cloak", "leather",
        "clothes", "clothing", "dress", "armor", "shield", "sword",
        "staff", "wand", "bow", "axe", "spear", "helmet", "crown",
    ]
    for clothing in clothing_words:
        if clothing in prompt_lower:
            if result["clothing"]:
                result["clothing"] += f", {clothing}"
            else:
                result["clothing"] = clothing
            break

    # Accessories
    accessory_words = [
        "scar", "tattoo", "jewelry", "amulet", "ring", "belt", "boots",
        "gloves", "helmet", "horns", "wings", "tail", "wings",
    ]
    for acc in accessory_words:
        if acc in prompt_lower:
            if result["accessories"]:
                result["accessories"] += f", {acc}"
            else:
                result["accessories"] = acc

    return result


def apply_modifications(base_prompt: str, modifications: str) -> str:
    """Apply natural language modifications to a base prompt.

    "same character but [change]"

    This is a heuristic approach — extracts the subject from the base
    and appends the modification. For more complex modifications,
    consider using an LLM.
    """
    components = detect_character_components(base_prompt)

    if components["subject"]:
        # Replace vague subject with the detected one + modifications
        # Simple approach: keep everything before common descriptors, add modifications
        parts = base_prompt.split(",")
        core_parts = []

        for part in parts:
            part_lower = part.lower().strip()
            skip = False
            # Skip view angle and pose from base (will be overridden)
            for skip_word in ["view", "pose", "angle", "idle", "standing"]:
                if skip_word in part_lower:
                    skip = True
                    break
            if not skip:
                core_parts.append(part.strip())

        base_core = ", ".join(core_parts)
        if modifications:
            return f"{base_core}, {modifications}"
        return base_core
    else:
        # No clear subject detected, just append modifications
        if modifications:
            return f"{base_prompt}, {modifications}"
        return base_prompt


def style_distance(prompt1: str, prompt2: str) -> float:
    """Measure stylistic similarity between two prompts.

    Returns a float 0.0 (identical style keywords) to 1.0 (completely different).

    Looks at:
    - Presence/absence of style keywords
    - Pixel art / game sprite / flat shading / etc.
    - Palette color mentions
    - View angle keywords
    """
    STYLE_KEYWORDS = [
        "pixel art", "game sprite", "clean pixel", "flat shading",
        "cell-shaded", "dithering", "1px outline", "2px outline",
        "transparent background", "no anti-aliasing", "hand-drawn",
        "cartoon", "isometric", "retro", "16-bit", "8-bit",
        "crisp edges", "crisp pixels", "limited palette", "max colors",
    ]

    # View angles
    VIEW_ANGLES = [
        "front view", "side view", "3/4 view", "back view",
        "top-down", "isometric", "overhead", "low angle",
    ]

    p1_lower = prompt1.lower()
    p2_lower = prompt2.lower()

    def get_style_set(prompt):
        found = set()
        prompt_l = prompt.lower()
        for kw in STYLE_KEYWORDS:
            if kw in prompt_l:
                found.add(kw)
        for va in VIEW_ANGLES:
            if va in prompt_l:
                found.add(va)
        return found

    set1 = get_style_set(prompt1)
    set2 = get_style_set(prompt2)

    if not set1 and not set2:
        return 0.0
    if not set1 or not set2:
        return 1.0

    # Jaccard distance: 1 - (intersection / union)
    intersection = len(set1 & set2)
    union = len(set1 | set2)

    return 1.0 - (intersection / union)


def build_variation_prompt(
    base_prompt: str,
    variation_type: str = "pose",
    style_guide: Optional[dict] = None,
) -> str:
    """Build a prompt variation for the same character with a specific change.

    variation_type: 'pose', 'view_angle', 'action', 'expression', 'accessory'
    """
    components = detect_character_components(base_prompt)

    # Core description (subject + clothing + accessories)
    core_parts = []
    if components["subject"]:
        core_parts.append(components["subject"])
    if components["clothing"]:
        core_parts.append(components["clothing"])
    if components["accessories"]:
        core_parts.append(components["accessories"])

    core = ", ".join(core_parts) if core_parts else base_prompt

    # Add variation-specific description
    variation_additions = {
        "pose": "dynamic pose, action stance",
        "view_angle": "3/4 view",
        "action": "mid-action, caught in motion",
        "expression": "expressive face, detailed emotion",
        "accessory": "additional gear, extra details",
    }

    parts = [core]
    if variation_type in variation_additions:
        parts.append(variation_additions[variation_type])
    if components["style"]:
        parts.append(components["style"])

    prompt = ", ".join(parts)

    # Add style guide keywords if available
    if style_guide:
        keywords = get_style_keywords(style_guide)
        if keywords:
            prompt = f"{prompt}, {keywords}"

    return prompt

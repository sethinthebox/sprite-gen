"""Smart prompt construction — builds optimized prompts from components."""

import json
import re
import requests
from pathlib import Path
from typing import Optional, List, Dict

from style import get_style_keywords
from reference import get_reference


DEFAULT_TEMPLATES_PATH = Path(__file__).parent / "defaults" / "prompt-templates.json"


# Action pose descriptions — per sprite size
ACTION_PROMPTS = {
    "idle": {
        "small": "standing idle, neutral stance, subtle breathing",
        "large": "standing idle, neutral stance, subtle breathing motion, relaxed shoulders, alert eyes",
    },
    "walk": {
        "small": "walking, mid-stride",
        "large": "walking animation frame, one foot forward, arms swinging naturally, mid-stride pose",
    },
    "run": {
        "small": "running, fast motion blur suggestion",
        "large": "running at full speed, legs extended, arms pumping, dynamic forward lean, motion frame",
    },
    "attack": {
        "small": "attacking, weapon extended",
        "large": "attacking with weapon, arm fully extended, weapon swung forward, powerful strike pose",
    },
    "cast": {
        "small": "casting spell, arms raised, magic gesture",
        "large": "casting spell, both arms raised dramatically, magical energy emanating from hands, concentration pose",
    },
    "jump": {
        "small": "jumping, legs tucked, arms up",
        "large": "jumping upwards, legs tucked under body, arms raised overhead, peak of jump pose",
    },
    "dance": {
        "small": "dancing, energetic pose",
        "large": "energetic dance pose, one arm raised, dynamic body twist, fluid movement frozen frame",
    },
    "death": {
        "small": "defeated pose, falling",
        "large": "defeated, falling backwards, arms flailing, impact about to happen, dramatic death pose",
    },
    "dodge": {
        "small": "dodging, quick evasive lean",
        "large": "dodging, quick evasive movement, body angled sharply to side, quick reaction pose",
    },
    "hurt": {
        "small": "injured, recoiling, pain expression",
        "large": "injured and recoiling, clutching wound, grimacing in pain, knocked back pose",
    },
    "block": {
        "small": "blocking, defensive stance, weapon raised",
        "large": "blocking with weapon, defensive stance, shield or weapon raised high, protective pose",
    },
}


def _load_templates() -> dict:
    """Load prompt templates from JSON."""
    if not DEFAULT_TEMPLATES_PATH.exists():
        return {}
    with open(DEFAULT_TEMPLATES_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def build_full_prompt(
    base_description: str,
    options: Optional[dict] = None,
) -> str:
    """Build a complete, optimized prompt from components.

    Args:
        base_description: Core character/object description
        options: dict with keys:
            - actions: list of action strings (e.g. ["idle", "walk"])
            - style_guide: style guide dict
            - reference_id: reference image ID for palette hints
            - seed_prompt: a previous prompt to maintain consistency with
            - view_angle: preferred view angle (e.g. "side view")
            - size_hint: sprite size hint for detail level

    Returns:
        Full optimized prompt string
    """
    if options is None:
        options = {}

    parts = []

    # Start with base description
    parts.append(base_description)

    # Add view angle if specified
    view_angle = options.get("view_angle")
    if view_angle:
        parts.append(view_angle)

    # Add reference palette colors if available
    reference_id = options.get("reference_id")
    if reference_id:
        ref = get_reference(reference_id)
        if ref and ref.get("palette"):
            palette = ref["palette"]
            # Pick a representative subset
            palette_str = ", ".join(palette[:4])
            parts.append(f"colors: {palette_str}")

    # Add style guide keywords
    style_guide = options.get("style_guide")
    if style_guide:
        keywords = get_style_keywords(style_guide)
        if keywords:
            parts.append(keywords)

    # Add seed prompt consistency if provided
    seed_prompt = options.get("seed_prompt")
    if seed_prompt:
        # Extract only the style-relevant parts from seed
        parts.append(f"style match: {seed_prompt}")

    return ", ".join(parts)


def build_action_prompt(action: str, sprite_size: int) -> str:
    """Build a pose description for a specific action.

    Uses more detailed descriptions for larger sprites.
    """
    if action == "custom":
        return "dynamic custom pose, detailed action"

    action_lower = action.lower()
    if action_lower not in ACTION_PROMPTS:
        return f"{action_lower} pose"

    size_key = "large" if sprite_size >= 64 else "small"
    return ACTION_PROMPTS[action_lower][size_key]


def suggest_improvements(prompt: str, ollama_url: str = "http://localhost:11434") -> str:
    """Use Ollama to analyze and improve a pixel art prompt.

    Returns the improved prompt, or the original if Ollama is unavailable.
    """
    improvement_prompt = (
        'You are a pixel art game sprite prompt expert. '
        'Improve this prompt for better generation results. '
        'Make it more specific, add game sprite keywords, '
        'include view angle and style details. '
        'Keep it concise (under 200 characters). '
        'Return ONLY the improved prompt, nothing else.\n\n'
        f'Original prompt: {prompt}'
    )

    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": "llama3.2",
                "prompt": improvement_prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 200},
            },
            timeout=30,
        )
        if response.status_code == 200:
            result = response.json()
            improved = result.get("response", "").strip()
            # Validate it's not empty and not too long
            if improved and len(improved) < 500 and len(improved) > 5:
                return improved
    except Exception:
        pass

    # Fallback: return original
    return prompt


def estimate_quality(prompt: str, style_guide: Optional[dict] = None) -> int:
    """Estimate prompt quality for pixel art sprite generation.

    Returns a score 0-100 based on presence of key quality indicators.
    """
    score = 0
    prompt_lower = prompt.lower()

    # Has pixel art / game sprite keywords: +30
    pixel_keywords = ["pixel art", "game sprite", "pixel art style", "pixel art game"]
    if any(kw in prompt_lower for kw in pixel_keywords):
        score += 30

    # Has style keywords from art_style: +20
    if style_guide and "art_style" in style_guide:
        art = style_guide["art_style"]
        art_keywords = [
            art.get("shading", ""),
            art.get("outline", ""),
            art.get("dithering", ""),
        ]
        for kw in art_keywords:
            if kw and kw != "none" and kw in prompt_lower:
                score += 7
        if score > 20:
            score = min(score, 20)

    # Has color specifications: +15
    color_indicators = ["color", "colors", "palette", "red", "blue", "green", "golden", "dark", "light"]
    if any(ind in prompt_lower for ind in color_indicators):
        score += 15

    # Has view angle: +10
    view_angles = ["front view", "side view", "3/4 view", "back view", "top-down", "isometric"]
    if any(va in prompt_lower for va in view_angles):
        score += 10

    # Has action/pose: +10
    action_words = ["idle", "walk", "run", "attack", "cast", "jump", "dance", "death", "dodge", "hurt", "block"]
    if any(a in prompt_lower for a in action_words):
        score += 10

    # Missing "pixel art": -50
    if "pixel" not in prompt_lower and "sprite" not in prompt_lower:
        score -= 50

    # Vague description ("a character", "some person"): -30
    vague_phrases = ["a character", "some character", "a person", "a figure", "a sprite", "thing", "creature"]
    if any(phrase in prompt_lower for phrase in vague_phrases):
        score -= 30

    # Too short (< 20 chars): -20
    if len(prompt) < 20:
        score -= 20

    return max(0, min(100, score))


def apply_template(
    template_name: str,
    variables: Optional[dict] = None,
    base_overrides: Optional[dict] = None,
) -> str:
    """Apply a named prompt template with variable substitution.

    Templates are stored in defaults/prompt-templates.json.
    """
    templates = _load_templates()

    if template_name not in templates:
        raise ValueError(f"Unknown template: {template_name}. Available: {list(templates.keys())}")

    template = templates[template_name]
    template_str = template.get("template", "")

    # Merge variables (template defaults + provided overrides)
    all_vars = {**template.get("variables", {}), **(variables or {})}

    # Replace placeholders like [CLOTHING] with values
    result = template_str
    for key, value in all_vars.items():
        placeholder = f"[{key}]"
        result = result.replace(placeholder, str(value))

    # Apply any base overrides (e.g., change VIEW_ANGLE)
    if base_overrides:
        for key, value in base_overrides.items():
            placeholder = f"[{key}]"
            result = result.replace(placeholder, str(value))

    return result


def list_templates() -> List[str]:
    """List all available prompt template names."""
    templates = _load_templates()
    return list(templates.keys())

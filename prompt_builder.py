"""Smart prompt construction — builds optimized prompts from components.

Single source of truth for:
    - ACTION_PROMPTS: pose descriptions per action (size-aware)
    - Prompt assembly: base + action + style + reference + template
    - Ollama improvements: refine prompts with local LLM
    - Quality scoring: evaluate prompt completeness before generation
    - Template system: reusable partial prompts per archetype
"""

import json
import requests
from pathlib import Path
from typing import Optional, List

from style import get_style_keywords
from reference import get_reference


DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "defaults"
DEFAULT_STYLE_GUIDE = DEFAULT_TEMPLATES_DIR / "style-guide-default.json"

# ── Action poses ────────────────────────────────────────────────────────────────
# Size-aware: "small" for sprites < 64px, "large" for 64px+
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
        "small": "running, fast motion",
        "large": "running at full speed, legs extended, arms pumping, dynamic forward lean, peak action frame",
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
        "small": "injured, recoiling",
        "large": "injured and recoiling, clutching wound, grimacing in pain, knocked back pose",
    },
    "block": {
        "small": "blocking, defensive stance, weapon raised",
        "large": "blocking with weapon, defensive stance, shield or weapon raised high, protective pose",
    },
    "custom": {
        "small": "dynamic custom pose",
        "large": "dynamic custom pose, detailed action",
    },
}

DEFAULT_ACTIONS = ["idle", "walk", "run", "attack"]


# ── Prompt building ─────────────────────────────────────────────────────────────

def build_full_prompt(
    base_description: str,
    options: Optional[dict] = None,
) -> str:
    """Assemble a complete generation prompt from components.

    Build order:
        1. Base description
        2. View angle (if specified)
        3. Reference palette colors (if a reference is set)
        4. Style guide keywords
        5. Seed consistency string (if continuing a series)

    Args:
        base_description: Core character/object description.
        options: Dict with keys:
            - ``view_angle``: e.g. "side view", "front view"
            - ``reference_id``: reference image ID for palette hints
            - ``style_guide``: style guide dict
            - ``seed_prompt``: previous prompt to match style with

    Returns:
        Comma-separated prompt string, ready to send to the API.
    """
    if options is None:
        options = {}

    parts = [base_description]

    if options.get("view_angle"):
        parts.append(options["view_angle"])

    if options.get("reference_id"):
        ref = get_reference(options["reference_id"])
        if ref and ref.get("palette"):
            palette_str = ", ".join(ref["palette"][:4])
            parts.append(f"colors: {palette_str}")

    if options.get("style_guide"):
        keywords = get_style_keywords(options["style_guide"])
        if keywords:
            parts.append(keywords)

    if options.get("seed_prompt"):
        parts.append(f"style match: {options['seed_prompt']}")

    return ", ".join(parts)


def build_action_prompt(action: str, sprite_size: int) -> str:
    """Get the pose description for an action, scaled to sprite size.

    Larger sprites (64+) get more detailed pose descriptions to take
    advantage of the additional pixel resolution.

    Args:
        action: Action name (e.g. "idle", "walk", "attack").
        sprite_size: Sprite pixel size (e.g. 64).

    Returns:
        Pose description string to append to a prompt.
    """
    action_key = action.lower()
    if action_key not in ACTION_PROMPTS:
        return f"{action_key} pose"

    size_key = "large" if sprite_size >= 64 else "small"
    return ACTION_PROMPTS[action_key][size_key]


# ── Ollama improvements ─────────────────────────────────────────────────────────

def suggest_improvements(
    prompt: str,
    ollama_url: str = "http://localhost:11434",
    model: str = "llama3.2",
) -> str:
    """Use Ollama to refine a prompt for better pixel art results.

    Sends the prompt to a local Ollama instance and asks it to
    improve specificity, add game-sprite keywords, and include
    view angle and style details.

    Args:
        prompt: The raw user prompt to improve.
        ollama_url: Ollama API base URL.
        model: Model name to use.

    Returns:
        The improved prompt, or the original if Ollama is unavailable.
    """
    system = (
        "You are a pixel art game sprite prompt expert. "
        "Improve this prompt for better generation results. "
        "Make it more specific, add game sprite keywords, "
        "include view angle and style details. "
        "Keep it concise (under 200 characters). "
        "Return ONLY the improved prompt text, nothing else. "
        "Do not wrap it in quotes or add explanation."
    )

    try:
        response = requests.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model,
                "prompt": f"{system}\n\nPrompt: {prompt}",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 200},
            },
            timeout=30,
        )
        if response.status_code == 200:
            result = response.json()
            improved = result.get("response", "").strip().strip('"')
            if improved and 5 < len(improved) < 500:
                return improved
    except (requests.RequestException, ValueError, KeyError) as exc:
        print(f"[suggest_improvements] API error: {exc}")

    return prompt


# ── Quality scoring ─────────────────────────────────────────────────────────────

def estimate_quality(
    prompt: str,
    style_guide: Optional[dict] = None,
) -> int:
    """Score a prompt 0–100 on completeness for pixel art generation.

    Scoring:
        +30  Has "pixel art" or "game sprite" keyword
        +20  Has style keywords from the art_style block
        +15  Has color specifications
        +10  Has a view angle
        +10  Has an action/pose
        −50  Missing "pixel" or "sprite" entirely
        −30  Vague ("a character", "some person")
        −20  Too short (< 20 characters)

    Args:
        prompt: The prompt to score.
        style_guide: Optional style guide dict for style keyword matching.

    Returns:
        Integer score 0–100.
    """
    score = 0
    p = prompt.lower()

    # Positive indicators
    if any(kw in p for kw in ["pixel art", "game sprite", "pixel art style"]):
        score += 30
    if any(kw in p for kw in ["color", "red", "blue", "green", "golden", "dark", "light", "palette"]):
        score += 15
    if any(va in p for va in ["front view", "side view", "3/4 view", "back view", "top-down", "isometric"]):
        score += 10
    if any(a in p for a in ["idle", "walk", "run", "attack", "cast", "jump", "dance", "death", "dodge", "hurt", "block"]):
        score += 10

    # Style guide keyword match
    if style_guide and "art_style" in style_guide:
        art_keywords = [
            style_guide["art_style"].get("shading", ""),
            style_guide["art_style"].get("outline", ""),
            style_guide["art_style"].get("dithering", ""),
        ]
        matches = sum(1 for kw in art_keywords if kw and kw != "none" and kw in p)
        score += min(matches * 7, 20)

    # Penalties
    if "pixel" not in p and "sprite" not in p:
        score -= 50
    if any(phrase in p for phrase in ["a character", "some character", "a person", "a figure", "thing"]):
        score -= 30
    if len(prompt) < 20:
        score -= 20

    return max(0, min(100, score))


# ── Template system ─────────────────────────────────────────────────────────────

def _load_templates() -> dict:
    """Load all templates from defaults/prompt-templates.json."""
    path = DEFAULT_TEMPLATES_DIR / "prompt-templates.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def apply_template(
    name: str,
    variables: Optional[dict] = None,
    base_overrides: Optional[dict] = None,
) -> str:
    """Apply a named template with variable substitution.

    Templates live in ``defaults/prompt-templates.json`` and look like::

        {
          "Character - Warrior": {
            "template": "[SUBJECT], battle-worn [CLOTHING], [WEAPON], [ACCESSORY], [VIEW_ANGLE]",
            "variables": {
              "SUBJECT": "fierce warrior",
              "CLOTHING": "plate armor",
              "WEAPON": "sword and shield",
              "ACCESSORY": "battle scars",
              "VIEW_ANGLE": "side view"
            }
          }
        }

    Args:
        name: Template name (key in prompt-templates.json).
        variables: Override values for ``[PLACEHOLDER]`` tokens.
        base_overrides: Additional overrides merged after ``variables``.

    Returns:
        The filled-in prompt string.

    Raises:
        ValueError: If the template name is not found.
    """
    templates = _load_templates()
    if name not in templates:
        raise ValueError(f"Unknown template: {name}. Available: {list(templates.keys())}")

    template = templates[name]
    variables = {**template.get("variables", {}), **(variables or {})}
    result = template.get("template", template.get("partial", ""))

    for key, value in variables.items():
        result = result.replace(f"[{key}]", str(value))
    if base_overrides:
        for key, value in base_overrides.items():
            result = result.replace(f"[{key}]", str(value))

    return result


def list_templates() -> List[str]:
    """Return names of all available templates."""
    return list(_load_templates().keys())


# ── Row-based sheet prompts ─────────────────────────────────────────────────────

def build_base_character(description: str) -> str:
    """Prepend 'isometric pixel art' if not present, ensure consistent format."""
    d = description.strip()
    if not d:
        return d
    lower = d.lower()
    if "pixel art" not in lower and "sprite" not in lower:
        d = "isometric pixel art " + d
    return d


def build_sheet_prompt(
    base_character: str,
    action: str,
    frame_number: int,
    total_frames: int = 4,
    style_suffix: str = "retro pixel art, no background, transparent PNG",
) -> str:
    """Build a prompt for a single frame in a row-based sprite sheet.

    Args:
        base_character: The character description, e.g.
            "isometric pixel art older businessman, late 50s, gray temples..."
        action: The animation action, e.g. "idle" or "walk"
        frame_number: Which frame in the animation (0-3)
        total_frames: Always 4 for standard grid
        style_suffix: Additional style keywords to append

    Returns:
        Full prompt string for this frame, including frame position in cycle.
    """
    prompt = f"{base_character}, {action} animation, frame {frame_number}/{total_frames}, {style_suffix}"
    return prompt

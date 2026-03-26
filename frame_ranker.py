"""
Frame Ranker integration — smart frame selection for sprite generation.

Strategy:
- For each animation frame, generate N candidates (different seeds)
- Score each with vision model
- Pick the highest-scoring candidate
- Falls back to QC rules if ranker is unavailable
"""
import base64, io, os, hashlib
from PIL import Image
from typing import List, Optional

# ─── Ollama-backed ranker ─────────────────────────────────────────────────────
RANKER_MODEL = os.environ.get("FRAME_RANKER_MODEL", "llama3.2-vision")
RANKER_PROMPT = """Rate this game sprite frame 0-10 for animation quality.

0-3: Broken, wrong pose, major artifacts, unclear character
4-6: Acceptable but has issues (proportions, centering, style)
7-10: Clean, correct pose, good proportions, pixel art style, centered

Animation type to expect: {animation_type}

Return ONLY a single integer 0-10. Nothing else."""


def _encode_image(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _score_with_ollama(img: Image.Image, animation_type: str) -> Optional[float]:
    """Ask Ollama vision model to score a frame. Returns None on failure."""
    try:
        import ollama
        response = ollama.chat(
            model=RANKER_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": RANKER_PROMPT.format(animation_type=animation_type),
                    "images": [_encode_image(img)],
                }
            ],
            options={"temperature": 0.1},
        )
        content = response["message"]["content"].strip()
        import re
        numbers = re.findall(r"\d+(?:\.\d+)?", content)
        if numbers:
            return min(10.0, max(0.0, float(numbers[0])))
    except Exception as e:
        print(f"    [ranker] ollama error: {e}")
    return None


def rank_frames(
    candidates: List[Image.Image],
    animation_type: str,
    top_k: int = 1,
) -> List[tuple[int, float]]:
    """
    Score and rank candidate frames.

    Args:
        candidates: List of PIL Images
        animation_type: Expected animation type (idle, walk, run, etc.)
        top_k: Return top K candidates (default 1 = just the best)

    Returns:
        List of (index, score) tuples sorted by score descending
    """
    if not candidates:
        return []
    if len(candidates) == 1:
        score = _score_with_ollama(candidates[0], animation_type)
        return [(0, score or 5.0)]

    print(f"    [ranker] scoring {len(candidates)} candidates ({animation_type})...")
    results = []
    for i, img in enumerate(candidates):
        score = _score_with_ollama(img, animation_type)
        if score is None:
            score = 5.0  # Neutral on ranker failure
        results.append((i, score))
        print(f"    [ranker]   frame {i+1}: {score:.1f}/10")

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def select_best(
    candidates: List[Image.Image],
    animation_type: str,
) -> int:
    """
    Select the best candidate frame by vision model scoring.
    Returns the index of the best candidate.
    """
    if not candidates:
        raise ValueError("No candidates")
    if len(candidates) == 1:
        return 0
    ranked = rank_frames(candidates, animation_type, top_k=1)
    best_idx = ranked[0][0]
    print(f"    [ranker] → selected index {best_idx} (score={ranked[0][1]:.1f})")
    return best_idx


# ─── Fallback: rule-based QC ──────────────────────────────────────────────────
def qc_score(img: Image.Image, reference_feet_y: Optional[int] = None) -> float:
    """
    Rule-based frame scoring (fallback when vision model unavailable).
    Returns 0-10 score.
    """
    w, h = img.size
    score = 10.0
    reasons = []

    # Corner transparency
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    if any(c[3] >= 5 for c in corners):
        score -= 3
        reasons.append("colored_corners")

    # Content bounding box
    min_x, max_x = w, 0
    min_y, max_y = h, 0
    for y in range(h):
        for x in range(w):
            if img.getpixel((x, y))[3] > 10:
                if x < min_x:
                    min_x = x
                if x > max_x:
                    max_x = x
                if y < min_y:
                    min_y = y
                if y > max_y:
                    max_y = y

    if max_x > min_x:
        content_w = max_x - min_x + 1
        content_h = max_y - min_y + 1
        aspect = content_w / max(content_h, 1)
        area_ratio = (content_w * content_h) / (w * h)
        center_x = (min_x + max_x) / 2
        center_dev = abs(center_x - w / 2)

        if aspect < 0.25 or aspect > 0.9:
            score -= 2
            reasons.append(f"bad_aspect:{aspect:.2f}")
        if area_ratio < 0.20:
            score -= 2
            reasons.append(f"too_small:{area_ratio:.2f}")
        if center_dev > 12:
            score -= 2
            reasons.append(f"off_center:{center_dev:.1f}px")
        if reference_feet_y is not None:
            feet_diff = abs(max_y - reference_feet_y)
            if feet_diff > 6:
                score -= 2
                reasons.append(f"feet_off:{max_y}vs{ref}")
    else:
        score = 0
        reasons.append("no_content")

    return max(0.0, score)


# ─── Combined selector ────────────────────────────────────────────────────────
def select_candidates(
    raw_frames: List[tuple[Image.Image, int]],
    animation_type: str,
    use_ranker: bool = True,
    reference_feet_y: Optional[int] = None,
) -> tuple[int, List[float]]:
    """
    Select best frame from candidates using vision model (preferred) or QC rules.

    Args:
        raw_frames: List of (PIL Image, seed) tuples
        animation_type: Animation type string
        use_ranker: Use Ollama vision model (True) or fallback QC (False)
        reference_feet_y: Reference feet Y for QC fallback scoring

    Returns:
        (best_index, all_scores)
    """
    if not raw_frames:
        raise ValueError("No candidates")
    if len(raw_frames) == 1:
        img = raw_frames[0][0]
        if use_ranker:
            score = _score_with_ollama(img, animation_type)
            return 0, [score or 5.0]
        else:
            return 0, [qc_score(img, reference_feet_y)]

    imgs = [r[0] for r in raw_frames]

    if use_ranker:
        # Try Ollama vision model
        scores = []
        for img in imgs:
            s = _score_with_ollama(img, animation_type)
            scores.append(s if s is not None else qc_score(img, reference_feet_y))
            print(f"    [ranker]   score: {scores[-1]:.1f}/10")
        best_idx = int(max(range(len(scores)), key=lambda i: scores[i]))
        print(f"    [ranker] → best index: {best_idx} ({scores[best_idx]:.1f}/10)")
        return best_idx, scores
    else:
        # Fallback to rule-based QC
        scores = [qc_score(img, reference_feet_y) for img in imgs]
        best_idx = int(max(range(len(scores)), key=lambda i: scores[i]))
        return best_idx, scores

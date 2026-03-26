"""
Frame Ranker — vision-model scorer for sprite frame quality.
Uses Ollama multimodal model to score and rank generated frames.
Replaces hard QC rules with learned selection.
"""
import base64
import io
import re
import time
from typing import List, Optional

from PIL import Image

import ollama

# Model: any Ollama vision model (llama3.2-vision, llava, etc.)
RANKER_MODEL = "llama3.2-vision"
_ranker_client = None  # Lazy-initialized


def _get_client():
    global _ranker_client
    if _ranker_client is None:
        _ranker_client = ollama
    return _ranker_client


def _encode_image(img: Image.Image) -> str:
    """Encode PIL Image as base64 PNG string for Ollama."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def qc_score(img: Image.Image, action: str) -> float:
    """
    Quick quality score for a single frame (0-10 scale).
    Used by generation.py to sort candidates before ranker is called.
    Returns a float 0-10.
    """
    # Fast heuristic: corner transparency + aspect ratio + content ratio
    w, h = img.size
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    corner_ok = all(c[3] < 5 for c in corners)

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
        content_ratio = (content_w * content_h) / (w * h)
        aspect = content_w / content_h if content_h > 0 else 0
    else:
        content_ratio = 0
        aspect = 0

    # Heuristic score
    score = 5.0
    if not corner_ok:
        score -= 2.0
    if not (0.25 <= aspect <= 0.9):
        score -= 1.5
    if content_ratio < 0.20:
        score -= 1.5
    # Bonus for good aspect ratio in humanoid range
    if 0.30 <= aspect <= 0.70:
        score += 0.5

    return max(0.0, min(10.0, score))


def _score_with_vision(img: Image.Image, action: str, model: str) -> float:
    """
    Ask Ollama vision model to score a sprite frame.
    Returns float 0-10.
    """
    prompt = f"""Rate this sprite frame from 0-10 for use in a {action} animation.

Scoring guide:
- 0-3: Broken sprite, wrong pose, major artifacts, unreadable character
- 4-6: Acceptable but flawed — off-center, wrong proportions, style mismatch
- 7-10: Clean, correct {action} pose, good proportions, pixel art style, centered

Return ONLY a single number between 0 and 10. No explanation."""

    try:
        client = _get_client()
        response = client.chat(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                    "images": [_encode_image(img)],
                }
            ],
            options={"temperature": 0.1, "num_predict": 8},
        )
        text = response["message"]["content"].strip()
        numbers = re.findall(r"\d+(?:\.\d+)?", text)
        if numbers:
            return min(10.0, max(0.0, float(numbers[0])))
        return 5.0
    except Exception as e:
        print(f"    [ranker] vision score failed: {e}")
        return 5.0


def consensus_score(frames: List[Image.Image]) -> List[float]:
    """
    Score frames by how much they agree with each other.
    Frames that look like outliers (different from the consensus) score lower.
    Uses simple pixel difference — no numpy needed.
    """
    if len(frames) < 2:
        return [5.0]
    if len(frames) == 2:
        return [5.0, 5.0]

    # Resize all to same size for comparison
    target_size = (16, 16)
    resized = [f.resize(target_size, Image.Resampling.LANCZOS).convert("L") for f in frames]

    # Compute mean image (consensus) pixel by pixel
    w, h = target_size
    mean_pixels: List[List[int]] = [[0] * w for _ in range(h)]
    for r in resized:
        for y in range(h):
            row = list(r.getdata())
            for x in range(w):
                mean_pixels[y][x] += row[y * w + x]

    for y in range(h):
        for x in range(w):
            mean_pixels[y][x] //= len(resized)

    # Each frame's score = negative distance from mean (closer = higher score)
    scores: List[float] = []
    for r in resized:
        total_diff = 0
        data = list(r.getdata())
        for y in range(h):
            for x in range(w):
                total_diff += abs(data[y * w + x] - mean_pixels[y][x])
        # Convert to 0-10 scale: diff=0 → 10, diff=255*16*16 → 0
        max_diff = 255 * h * w
        score = max(0.0, min(10.0, 10.0 - (total_diff / max_diff * 10.0)))
        scores.append(score)

    return scores


def select_candidates(
    frames: List[Image.Image],
    action: str,
    model: str = RANKER_MODEL,
) -> tuple[int, List[float]]:
    """
    Rank candidate frames using consensus scoring (fast, no GPU).
    Falls back to per-frame heuristic scoring if vision model available.
    Vision model scoring takes priority if available and model is ready.
    """
    if not frames:
        raise ValueError("No frames to rank")
    if len(frames) == 1:
        return 0, [5.0]

    # Check if vision model is available
    vision_available = _vision_model_ready(model)

    if vision_available:
        # Use vision model (slower but smarter)
        return _select_with_vision(frames, action, model)
    else:
        # Fallback: consensus + heuristic scoring
        return _select_with_consensus(frames)


def _vision_model_ready(model: str) -> bool:
    """Check if a vision model is loaded and ready."""
    try:
        client = _get_client()
        client.chat(model=model, messages=[{"role": "user", "content": "hi"}],
                   options={"num_predict": 3})
        return True
    except:
        return False


def _select_with_vision(frames, action, model) -> tuple[int, List[float]]:
    """Use Ollama vision model to score frames."""
    print(f"    [ranker] scoring {len(frames)} candidates ({action}) with {model}...")
    all_scores: List[float] = []

    for i, frame in enumerate(frames):
        score = _score_with_vision(frame, action, model)
        all_scores.append(score)
        print(f"    [ranker]   candidate {i + 1}: {score:.1f}/10")
        time.sleep(0.1)

    best_idx = max(range(len(all_scores)), key=lambda i: all_scores[i])
    print(f"    [ranker] → best index: {best_idx} ({all_scores[best_idx]:.1f}/10)")
    return best_idx, all_scores


def _select_with_consensus(frames) -> tuple[int, List[float]]:
    """
    Fallback: score frames by consensus (outlier detection).
    Frame most similar to the average gets highest score.
    """
    print(f"    [ranker] consensus scoring {len(frames)} candidates (no vision model)...")
    scores = consensus_score(frames)
    for i, s in enumerate(scores):
        print(f"    [ranker]   candidate {i + 1}: {s:.1f}/10")
    best_idx = max(range(len(scores)), key=lambda i: scores[i])
    print(f"    [ranker] → best index: {best_idx} ({scores[best_idx]:.1f}/10)")
    return best_idx, scores


# ─── CLI test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, os

    test_frame = os.path.join(os.path.dirname(__file__), "frames", "frame_000.png")
    if os.path.exists(test_frame):
        img = Image.open(test_frame).convert("RGBA")
        score = qc_score(img, "walk")
        print(f"QC score for frame_000: {score}/10")
    else:
        print("No test frame, skipping")

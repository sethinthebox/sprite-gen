"""
Frame Ranker — unified scoring for sprite frame quality.
Combines three signals:
1. QC rules (corners, aspect, content ratio, feet)
2. Trained tiny CNN (sprite quality classifier)
3. Ollama vision model (best but needs GPU)

Each returns a 0-10 score. The final score is a weighted combination.
"""
import base64
import io
import os
import re
import time
from pathlib import Path
from typing import List, Optional

import torch
import torch.nn as nn
from PIL import Image

# ─── Tiny CNN model ──────────────────────────────────────────────────────────
class SpriteQC(nn.Module):
    """~122K param CNN for sprite quality scoring. CPU-compatible."""
    def __init__(self):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv2d(4, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.Conv2d(64, 64, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
            nn.AdaptiveAvgPool2d(4),
        )
        self.fc = nn.Sequential(
            nn.Flatten(),
            nn.Linear(64 * 4 * 4, 64), nn.ReLU(), nn.Dropout(0.3),
            nn.Linear(64, 1), nn.Sigmoid(),
        )

    def forward(self, x):
        return self.fc(self.conv(x)).squeeze(1)


# ─── Global model (lazy loaded) ───────────────────────────────────────────────
_qc_model: Optional[SpriteQC] = None
_model_path = Path(__file__).parent / "sprite_qc_model.pth"


def _load_qc_model() -> Optional[SpriteQC]:
    global _qc_model
    if _qc_model is not None:
        return _qc_model
    if not _model_path.exists():
        return None
    try:
        model = SpriteQC()
        ckpt = torch.load(_model_path, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["m"])
        model.eval()
        _qc_model = model
        return model
    except Exception as e:
        print(f"  [ranker] WARNING: could not load QC model: {e}")
        return None


# ─── Rule-based QC score (0-10) ───────────────────────────────────────────────
def qc_score(img: Image.Image, action: str = "walk") -> float:
    """
    Fast heuristic QC score. No ML needed.
    Covers: corners, aspect ratio, content size, off-center.
    """
    w, h = img.size

    # Corner alpha
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    corner_ok = all(c[3] < 5 for c in corners)

    # Content bbox
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
        center_x = (min_x + max_x) // 2
        off_center = abs(center_x - w // 2)
    else:
        content_ratio = 0
        aspect = 0
        off_center = 0

    # Score components (0-10 each, averaged)
    score = 7.0  # Base: passed QC
    if not corner_ok:
        score -= 1.5
    if not (0.20 <= aspect <= 0.95):
        score -= 1.5
    if content_ratio < 0.15:
        score -= 1.5
    if off_center > 15:
        score -= 1.0
    # Bonus for humanoid aspect
    if 0.35 <= aspect <= 0.70:
        score += 0.5

    return max(0.0, min(10.0, score))


# ─── CNN model score (0-10) ──────────────────────────────────────────────────
def model_score(img: Image.Image) -> Optional[float]:
    """
    Get quality score from trained CNN model.
    Returns None if model not available.
    """
    model = _load_qc_model()
    if model is None:
        return None

    try:
        w, h = img.size
        if w != 64 or h != 64:
            img = img.resize((64, 64), Image.Resampling.LANCZOS)
        if img.mode != "RGBA":
            img = img.convert("RGBA")

        import numpy as np
        arr = np.array(img, dtype=np.float32) / 255.0
        tensor = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)

        with torch.no_grad():
            raw = model(tensor).item()

        # Convert [0,1] to [0,10] scale
        return raw * 10.0
    except Exception as e:
        print(f"    [ranker] CNN score error: {e}")
        return None


# ─── Consensus outlier detection ──────────────────────────────────────────────
def consensus_score(frames: List[Image.Image]) -> List[float]:
    """
    Score frames by pixel-level consensus.
    Frames that differ most from the average get lower scores.
    Returns per-frame scores in original order.
    """
    if len(frames) < 2:
        return [5.0] * len(frames)
    if len(frames) == 2:
        return [5.0, 5.0]

    target = (16, 16)
    resized = [f.resize(target, Image.Resampling.LANCZOS).convert("L") for f in frames]
    w, h = target

    # Mean
    mean_pix = [[0] * w for _ in range(h)]
    for r in resized:
        data = list(r.getdata())
        for y in range(h):
            for x in range(w):
                mean_pix[y][x] += data[y * w + x]
    for y in range(h):
        for x in range(w):
            mean_pix[y][x] //= len(resized)

    # Per-frame distance from mean
    scores = []
    for r in resized:
        data = list(r.getdata())
        diff = sum(abs(data[y * w + x] - mean_pix[y][x])
                   for y in range(h) for x in range(w))
        max_diff = 255 * h * w
        score = max(0.0, 10.0 - (diff / max_diff * 10.0))
        scores.append(score)

    return scores


# ─── Ollama vision model scoring ──────────────────────────────────────────────
_ollama_available = None


def _check_ollama() -> bool:
    global _ollama_available
    if _ollama_available is None:
        try:
            import ollama
            ollama.chat(model="llama3.2-vision",
                       messages=[{"role": "user", "content": "hi"}],
                       options={"num_predict": 5})
            _ollama_available = True
        except Exception:
            _ollama_available = False
    return _ollama_available


def vision_score(img: Image.Image, action: str) -> Optional[float]:
    """
    Ask Ollama vision model to score the frame.
    Returns None if unavailable.
    """
    if not _check_ollama():
        return None

    prompt = f"""Rate this sprite frame for use in a {action} animation. 0-10.

Return ONLY a number. No explanation."""

    try:
        import ollama
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        resp = ollama.chat(
            model="llama3.2-vision",
            messages=[{"role": "user", "content": prompt, "images": [b64]}],
            options={"temperature": 0.1, "num_predict": 8},
        )
        text = resp["message"]["content"].strip()
        nums = re.findall(r"\d+(?:\.\d+)?", text)
        if nums:
            return min(10.0, max(0.0, float(nums[0])))
    except Exception as e:
        print(f"    [ranker] vision error: {e}")
    return None


# ─── Combined ranking ─────────────────────────────────────────────────────────
def select_candidates(
    frames: List[Image.Image],
    action: str,
) -> tuple[int, List[float]]:
    """
    Rank candidate frames using all available signals.
    Returns (best_index, all_scores).

    Scoring priority:
    1. Vision model (best) + CNN model (if available) → combined
    2. CNN model alone (if available)
    3. QC rules + consensus (fallback)
    """
    if not frames:
        raise ValueError("No frames to rank")
    if len(frames) == 1:
        return 0, [qc_score(frames[0], action)]

    # Get vision scores if available
    vision_scores: List[Optional[float]] = [None] * len(frames)
    cnn_scores: List[Optional[float]] = [None] * len(frames)

    # Vision model scoring (slow, do first if available)
    vision_available = _check_ollama()
    if vision_available:
        print(f"    [ranker] scoring {len(frames)} with vision model...")
        for i, frame in enumerate(frames):
            vision_scores[i] = vision_score(frame, action)
            print(f"    [ranker]   vision[{i+1}]: {vision_scores[i]}")
            time.sleep(0.05)

    # CNN model scoring (fast, always try)
    model = _load_qc_model()
    if model is not None:
        for i, frame in enumerate(frames):
            cnn_scores[i] = model_score(frame)

    # Combine scores
    final_scores: List[float] = []
    for i in range(len(frames)):
        vs = vision_scores[i]
        cs = cnn_scores[i]

        if vs is not None and cs is not None:
            # Vision is more reliable — weight it higher
            combined = vs * 0.7 + cs * 0.3
            final_scores.append(combined)
            print(f"    [ranker]   combined[{i+1}]: vision={vs:.1f} cnn={cs:.1f} → {combined:.1f}")
        elif cs is not None:
            # CNN only — use as-is (already 0-10)
            final_scores.append(cs)
            print(f"    [ranker]   cnn[{i+1}]: {cs:.1f}")
        else:
            # Fallback to QC rules
            qc = qc_score(frames[i], action)
            final_scores.append(qc)
            print(f"    [ranker]   qc[{i+1}]: {qc:.1f}")

    best_idx = max(range(len(final_scores)), key=lambda i: final_scores[i])
    print(f"    [ranker] → best index: {best_idx} ({final_scores[best_idx]:.1f}/10)")
    return best_idx, final_scores

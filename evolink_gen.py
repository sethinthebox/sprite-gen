"""
EvoLink / Gemini Nano Banana 2 integration for sprite generation.
Provides reference-based generation for character consistency.
"""
import base64
import io
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

import requests

# ─── Configuration ────────────────────────────────────────────────────────────
API_KEY = os.environ.get("EVOLINK_API_KEY", "")
API_BASE = "https://api.evolink.ai/v1"

# Quality settings (0.5K = 3x cheaper than 1K for sandbox)
QUALITY_COSTS = {
    "0.5K": 2.5817,   # credits per image
    "1K":   3.8708,
    "2K":   5.8061,
    "4K":   8.7092,
}
DEFAULT_QUALITY = "0.5K"  # Sandbox mode = cheap

# ─── Core API ────────────────────────────────────────────────────────────────
def submit_generation(
    prompt: str,
    model: str = "gemini-3.1-flash-image-preview",
    quality: str = DEFAULT_QUALITY,
    size: str = "1:1",
    reference_urls: list = None,
    api_key: str = None,
) -> str:
    """
    Submit an image generation task. Returns task_id.
    Call poll_task(task_id) to get the result.
    """
    key = api_key or API_KEY
    if not key:
        raise ValueError("No EvoLink API key set. Set EVOLINK_API_KEY env var.")

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "quality": quality,
    }

    if reference_urls:
        payload["image_urls"] = reference_urls

    resp = requests.post(
        f"{API_BASE}/images/generations",
        headers=headers,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if data.get("error"):
        raise RuntimeError(f"EvoLink error: {data['error']}")

    task_id = data.get("id")
    if not task_id:
        raise RuntimeError(f"No task_id in response: {data}")

    return task_id


def poll_task(
    task_id: str,
    api_key: str = None,
    max_wait: int = 120,
    poll_interval: int = 10,
) -> dict:
    """
    Poll a task until completion. Returns the full task result dict.
    """
    key = api_key or API_KEY
    headers = {"Authorization": f"Bearer {key}"}

    elapsed = 0
    while elapsed < max_wait:
        resp = requests.get(
            f"{API_BASE}/tasks/{task_id}",
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        status = data.get("status")
        if status == "completed":
            return data
        elif status == "failed":
            raise RuntimeError(f"Task failed: {data.get('error', 'unknown')}")

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Task {task_id} did not complete within {max_wait}s")


def generate(
    prompt: str,
    model: str = "gemini-3.1-flash-image-preview",
    quality: str = DEFAULT_QUALITY,
    size: str = "1:1",
    reference_urls: list = None,
    api_key: str = None,
    max_wait: int = 120,
) -> dict:
    """
    One-shot generate + poll. Returns result dict with 'image_url' and 'task_id'.
    """
    task_id = submit_generation(
        prompt=prompt,
        model=model,
        quality=quality,
        size=size,
        reference_urls=reference_urls,
        api_key=api_key,
    )
    result = poll_task(task_id, api_key=api_key, max_wait=max_wait)

    image_url = None
    if result.get("results"):
        image_url = result["results"][0]

    return {
        "task_id": task_id,
        "status": result.get("status"),
        "image_url": image_url,
        "full_result": result,
    }


def download_image(url: str, timeout: int = 30) -> bytes:
    """Download image bytes from a URL."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def generate_and_save(
    prompt: str,
    output_path: str,
    model: str = "gemini-3.1-flash-image-preview",
    quality: str = DEFAULT_QUALITY,
    size: str = "1:1",
    reference_urls: list = None,
    api_key: str = None,
) -> dict:
    """
    Generate image and save to output_path. Returns metadata dict.
    """
    result = generate(
        prompt=prompt,
        model=model,
        quality=quality,
        size=size,
        reference_urls=reference_urls,
        api_key=api_key,
        max_wait=120,
    )

    if result["image_url"]:
        img_bytes = download_image(result["image_url"])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(img_bytes)
        result["saved_to"] = output_path

    return result


# ─── Cost estimation ──────────────────────────────────────────────────────────
def estimate_cost(n_images: int, quality: str = DEFAULT_QUALITY) -> float:
    """Estimate cost in credits for n images at given quality."""
    return n_images * QUALITY_COSTS.get(quality, 3.8708)


def credits_to_dollars(credits: float, price_per_credit: float = 0.014) -> float:
    """Rough estimate: EvoLink credits cost ~$0.014 each (varies by purchase size)."""
    return credits * price_per_credit


# ─── CLI test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys, argparse

    parser = argparse.ArgumentParser(description="Test EvoLink generation")
    parser.add_argument("--prompt", default="a small red robot, pixel art style, on white background")
    parser.add_argument("--quality", default="0.5K", choices=list(QUALITY_COSTS.keys()))
    parser.add_argument("--output", default="/tmp/evolink_test.png")
    parser.add_argument("--api-key", default=None)
    args = parser.parse_args()

    cost = estimate_cost(1, args.quality)
    print(f"Estimated cost: {cost:.2f} credits")

    result = generate_and_save(
        prompt=args.prompt,
        output_path=args.output,
        quality=args.quality,
        api_key=args.api_key,
    )
    print(f"Status: {result['status']}")
    print(f"Task: {result['task_id']}")
    print(f"Image: {result.get('saved_to', 'not saved')}")
    print(f"URL: {result.get('image_url', 'none')}")

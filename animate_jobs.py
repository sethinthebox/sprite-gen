"""
Background job registry for directional animation.
Job state is persisted to disk so it survives Flask worker restarts.
"""
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any

BASE_DIR = Path(__file__).parent
JOBS_DIR = BASE_DIR / "animate_jobs"
REGISTRY_FILE = JOBS_DIR / "registry.json"

# ── Registry helpers ───────────────────────────────────────────────────────────

def _ensure_dirs():
    JOBS_DIR.mkdir(exist_ok=True)
    if not REGISTRY_FILE.exists():
        REGISTRY_FILE.write_text(json.dumps({}, indent=2))


def _read_registry() -> dict:
    _ensure_dirs()
    try:
        return json.loads(REGISTRY_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return {}


def _write_registry(reg: dict):
    _ensure_dirs()
    REGISTRY_FILE.write_text(json.dumps(reg, indent=2))


# ── Job lifecycle ─────────────────────────────────────────────────────────────

def create_job(
    base_character: str,
    actions: list,
    seed: Optional[int],
    sprite_size: int = 64,
    reference_sprite_url: str = "",
) -> str:
    """Create a new animate job. Returns job_id."""
    job_id = uuid.uuid4().hex[:12]

    job_state = {
        "job_id": job_id,
        "status": "pending",          # pending → running → done | error
        "progress": {
            "current": 0,
            "total": _total_frames(len(actions)),
            "pct": 0,
        },
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "base_character": base_character,
        "actions": actions,
        "seed": seed,
        "sprite_size": sprite_size,
        "reference_sprite_url": reference_sprite_url,
        "result": None,
        "error": None,
        "frames_generated": 0,
    }

    # Write job state file
    job_file = JOBS_DIR / f"{job_id}.json"
    job_file.write_text(json.dumps(job_state, indent=2))

    # Register in registry
    reg = _read_registry()
    reg[job_id] = {
        "status": "pending",
        "created_at": job_state["created_at"],
        "actions": actions,
    }
    _write_registry(reg)

    return job_id


def update_job(job_id: str, **updates) -> Optional[dict]:
    """Update fields on a job. Returns updated job dict or None if not found."""
    job_file = JOBS_DIR / f"{job_id}.json"
    if not job_file.exists():
        return None

    state = json.loads(job_file.read_text())
    for key, value in updates.items():
        if key in ("progress", "result", "error"):
            state[key] = value
        elif key == "status":
            state["status"] = value
        elif key == "frames_generated":
            state["frames_generated"] = value

    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    job_file.write_text(json.dumps(state, indent=2))

    # Update registry
    reg = _read_registry()
    if job_id in reg:
        reg[job_id]["status"] = state["status"]
        _write_registry(reg)

    return state


def get_job(job_id: str) -> Optional[dict]:
    """Get job state dict or None."""
    job_file = JOBS_DIR / f"{job_id}.json"
    if not job_file.exists():
        return None
    try:
        return json.loads(job_file.read_text())
    except json.JSONDecodeError:
        return None


def list_jobs(limit: int = 50) -> list:
    """List recent jobs (newest first)."""
    _ensure_dirs()
    reg = _read_registry()
    jobs = []
    for job_id, info in reg.items():
        job = get_job(job_id)
        if job:
            jobs.append(job)
    jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
    return jobs[:limit]


def _total_frames(n_actions: int) -> int:
    """Total FLUX calls = n_actions × 8 directions × 4 frames."""
    return n_actions * 8 * 4

#!/usr/bin/env python3
"""Patch app.py to add background animate job system."""
import re

APP_PATH = '/opt/sprite-gen/app.py'
app_py = open(APP_PATH).read()

# ── 1. Add new imports ────────────────────────────────────────────────────────
old_imports = "import json, time, traceback, uuid"
new_imports = "import json, time, traceback, uuid, subprocess, threading, os, sys"
if new_imports not in app_py:
    app_py = app_py.replace(old_imports, new_imports)
    print("✓ Added imports")

# ── 2. Replace the broken /animate endpoint ──────────────────────────────────
old_animate = '''@app.route(f"{PREFIX}/animate", methods=["POST"])
def animate():
    """Generate 8-directional animation from selected sprite + character."""
    data = request.get_json() or {}
    base_character = data.get("base_character", "").strip()
    reference_sprite_url = data.get("reference_sprite_url", "").strip()
    actions = data.get("actions", ["idle", "walk"])
    sprite_size = int(data.get("sprite_size", 64))
    if not base_character:
        return jsonify({"error": "base_character is required"}), 400
    try:
        import directional as _directional
        info = _directional.generate_directional_spritesheet(
            base_character=base_character,
            reference_sprite_path=None,
            actions=actions,
            sprite_size=sprite_size,
        )
        gif_urls = {}
        for action_name, rows in info["action_dirs"].items():
            direction_frames = [fps[0] for d, fps in rows if fps]
            if direction_frames:
                gif_path = OUTPUT_DIR / f"directions_{action_name}_{info['base_seed']}.gif"
                generate_gif(direction_frames, str(gif_path), delay=150)
                gif_urls[action_name] = _u(f"/output/{gif_path.name}")
        return jsonify({
            "status": "ok",
            "directions": info["directions"],
            "actions": info["actions"],
            "base_seed": info["base_seed"],
            "gif_urls": gif_urls,
        })
    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


    return send_file(file_path, as_attachment=True)'''

new_animate = '''@app.route(f"{PREFIX}/animate", methods=["POST"])
def animate():
    """Start a background directional animation job. Returns job_id immediately."""
    data = request.get_json() or {}
    base_character = data.get("base_character", "").strip()
    if not base_character:
        return jsonify({"error": "base_character is required"}), 400

    actions = data.get("actions", ["idle", "walk"])
    if isinstance(actions, str):
        try: actions = json.loads(actions)
        except: actions = [a.strip() for a in actions.split(",")]
    sprite_size = int(data.get("sprite_size", 64))
    reference_sprite_url = data.get("reference_sprite_url", "")
    user_seed = int(data["seed"]) if data.get("seed") else None

    try:
        import animate_jobs as _aj

        job_id = _aj.create_job(
            base_character=base_character,
            actions=actions,
            seed=user_seed,
            sprite_size=sprite_size,
            reference_sprite_url=reference_sprite_url,
        )

        # Spawn background worker — runs in subprocess, does NOT block HTTP response
        worker_script = str(BASE / "animate_worker.py")
        subprocess.Popen(
            [sys.executable, worker_script, job_id],
            cwd=str(BASE),
            stdout=open(str(BASE / "animate_jobs" / f"{job_id}.log"), "w"),
            stderr=subprocess.STDOUT,
        )

        return jsonify({
            "status": "started",
            "job_id": job_id,
            "message": f"Job started. Poll /animate/status/{job_id} for progress.",
        })

    except Exception as e:
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route(f"{PREFIX}/animate/status/<job_id>", methods=["GET"])
def animate_status(job_id):
    """Poll job progress. Returns current status."""
    try:
        import animate_jobs as _aj
        job = _aj.get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404
        return jsonify({
            "job_id": job["job_id"],
            "status": job["status"],
            "progress": job["progress"],
            "created_at": job["created_at"],
            "updated_at": job.get("updated_at", ""),
            "frames_generated": job.get("frames_generated", 0),
            "error": job.get("error"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route(f"{PREFIX}/animate/result/<job_id>", methods=["GET"])
def animate_result(job_id):
    """Get completed job result with output URLs."""
    try:
        import animate_jobs as _aj
        job = _aj.get_job(job_id)
        if not job:
            return jsonify({"error": "Job not found"}), 404

        if job["status"] == "running":
            return jsonify({"status": "running", "progress": job["progress"]}), 200

        if job["status"] == "error":
            return jsonify({
                "status": "error",
                "error": job.get("error", "Unknown error"),
                "progress": job["progress"],
            }), 200

        # done
        return jsonify({
            "status": "done",
            "job_id": job["job_id"],
            "actions": job["actions"],
            "result": job.get("result"),
            "progress": job["progress"],
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500'''

if old_animate in app_py:
    app_py = app_py.replace(old_animate, new_animate)
    print("✓ Replaced /animate endpoint + added status/result routes")
else:
    print("WARNING: Could not find exact /animate block to replace")
    # Try a simpler approach - just insert after the route definition
    print("  Trying alternate insertion strategy...")

# ── 3. Ensure animate_jobs directory is created on startup ─────────────────────
old_init = '''if __name__ == "__main__":\n    init()'''
new_init = '''def _init_animate_jobs():
    (BASE / "animate_jobs").mkdir(exist_ok=True)

if __name__ == "__main__":\n    init()\n    _init_animate_jobs()'''
if '_init_animate_jobs' not in app_py:
    app_py = app_py.replace(old_init, new_init)
    print("✓ Added animate_jobs init")

open(APP_PATH, 'w').write(app_py)
print(f"✓ Written {APP_PATH}")

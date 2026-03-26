#!/usr/bin/env python3
"""Flask web UI for sprite generator — enhanced with style guides, templates, and reference images."""

import json, time, traceback, uuid
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_file, make_response

from generation import generate_sprite_sheet
from generator import load_config
from prompt_builder import ACTION_PROMPTS
from assembler import generate_gif

app = Flask(__name__, template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

BASE = Path(__file__).parent
OUTPUT_DIR = BASE / "output"
FRAMES_DIR = BASE / "frames"
REF_DIR = BASE / "reference-library"

# URL prefix for deployment (Flask runs behind nginx at /sprite/)
SPRITE_PREFIX = "/sprite"
TEMPLATES_DIR = BASE / "prompt-templates"
STYLE_GUIDE_FILE = BASE / "style-guide.json"
GEN_LOG_FILE = BASE / "generation-log.jsonl"
CONFIG_FILE = BASE / "config.json"


# ── Init dirs ─────────────────────────────────────────────────────────────────

def init():
    for d in [OUTPUT_DIR, FRAMES_DIR, REF_DIR, TEMPLATES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
    if not STYLE_GUIDE_FILE.exists():
        STYLE_GUIDE_FILE.write_text(json.dumps({"current": "", "presets": {}}, indent=2))
    if not GEN_LOG_FILE.exists():
        GEN_LOG_FILE.write_text("")
    _ensure_default_templates()


def _ensure_default_templates():
    defaults = {
        "Character - Warrior": {
            "partial": "warrior character, armored, powerful stance, battle-ready, RPG hero sprite, transparent background"
        },
        "Character - Mage": {
            "partial": "mage character, wizard robes, mystical aura, holding staff, spell caster, flowing cape, RPG sprite, transparent background"
        },
        "Character - Rogue": {
            "partial": "rogue character, stealthy assassin, dark cloak, dual daggers, sneaky posture, agile build, RPG sprite, transparent background"
        },
        "Enemy - Beast": {
            "partial": "fierce beast monster, sharp claws, fangs bared, aggressive pose, pixel art enemy, RPG sprite, transparent background"
        },
        "Enemy - Undead": {
            "partial": "undead creature, zombie or skeleton, decaying flesh or bone, glowing eyes, menacing pose, pixel art enemy, transparent background"
        },
        "Item - Weapon": {
            "partial": "game weapon item, sword or axe or bow, shiny metal, detailed pixel art, RPG item icon, transparent background, centered"
        },
        "Item - Potion": {
            "partial": "potion or consumable item, glowing liquid, glass bottle, pixel art RPG item, transparent background, centered"
        },
        "Environment - Dungeon": {
            "partial": "dungeon tile or environment piece, stone floor, brick wall section, dark atmospheric, pixel art game background tile, transparent edges"
        },
    }
    changed = False
    for name, data in defaults.items():
        if not (TEMPLATES_DIR / f"{name}.json").exists():
            (TEMPLATES_DIR / f"{name}.json").write_text(json.dumps(data, indent=2))
            changed = True
    return changed


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


# ── Config ─────────────────────────────────────────────────────────────────────

@app.route("/config", methods=["GET", "POST"])
def manage_config():
    """GET: return public config (no API key). POST: update config fields."""
    if request.method == "GET":
        cfg = load_config()
        # Mask API key
        if cfg.get("deepinfra_api_key"):
            cfg["deepinfra_api_key"] = mask_key(cfg["deepinfra_api_key"])
        return jsonify(cfg)

    data = request.get_json() or {}
    cfg = load_config()
    for key in ["deepinfra_api_key", "deepinfra_base_url", "model",
                "generation_steps", "default_sprite_size", "generation_timeout",
                "ollama_endpoint"]:
        if key in data:
            cfg[key] = data[key]
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))
    return jsonify({"status": "ok"})


def mask_key(key: str) -> str:
    if len(key) > 8:
        return key[:4] + "*" * (len(key) - 8) + key[-4:]
    return "****"


# ── Style Guide ────────────────────────────────────────────────────────────────

@app.route("/style-guide", methods=["GET", "POST"])
def style_guide():
    if request.method == "GET":
        return jsonify(json.loads(STYLE_GUIDE_FILE.read_text()))

    data = request.get_json() or {}
    sg = json.loads(STYLE_GUIDE_FILE.read_text())
    if "current" in data:
        sg["current"] = data["current"]
    if "presets" in data:
        sg["presets"] = data["presets"]
    STYLE_GUIDE_FILE.write_text(json.dumps(sg, indent=2))
    return jsonify({"status": "ok"})


# ── Templates ──────────────────────────────────────────────────────────────────

@app.route("/templates", methods=["GET"])
def list_templates():
    templates = {}
    for f in sorted(TEMPLATES_DIR.glob("*.json")):
        try:
            templates[f.stem] = json.loads(f.read_text())
        except Exception:
            pass
    return jsonify(templates)


@app.route("/templates", methods=["POST"])
def save_template():
    """Save or delete a template. DELETE removes it."""
    data = request.get_json() or {}
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Template name required"}), 400

    if request.headers.get("X-HTTP-Method") == "DELETE" or data.get("_delete"):
        path = TEMPLATES_DIR / f"{name}.json"
        if path.exists():
            path.unlink()
        return jsonify({"status": "deleted"})

    partial = data.get("partial", "")
    (TEMPLATES_DIR / f"{name}.json").write_text(json.dumps({"partial": partial}, indent=2))
    return jsonify({"status": "ok", "name": name})


# ── Reference Images ───────────────────────────────────────────────────────────

@app.route("/upload-reference", methods=["POST"])
def upload_reference():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file selected"}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return jsonify({"error": "Unsupported image type"}), 400

    ref_id = str(uuid.uuid4())[:8]
    filename = f"{ref_id}{ext}"
    path = REF_DIR / filename
    file.save(str(path))

    return jsonify({
        "reference_id": ref_id,
        "filename": filename,
        "url": f"{SPRITE_PREFIX}/reference/{filename}",
    })


@app.route("/reference/<filename>")
def serve_reference(filename):
    safe = REF_DIR / Path(filename).name
    if not safe.exists() or not safe.is_relative_to(REF_DIR):
        return jsonify({"error": "Not found"}), 404
    return send_file(safe)


@app.route("/references", methods=["GET"])
def list_references():
    refs = []
    for f in sorted(REF_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        refs.append({
            "reference_id": f.stem,
            "filename": f.name,
            "url": f"{SPRITE_PREFIX}/reference/{f.name}",
            "mtime": f.stat().st_mtime,
        })
    return jsonify(refs)


@app.route("/reference/<ref_id>", methods=["DELETE"])
def delete_reference(ref_id):
    for f in REF_DIR.iterdir():
        if f.stem == ref_id:
            f.unlink()
            return jsonify({"status": "ok"})
    return jsonify({"error": "Not found"}), 404


# ── Generation Log ─────────────────────────────────────────────────────────────

@app.route("/generation-log", methods=["GET", "DELETE"])
def get_generation_log():
    if request.method == "DELETE":
        GEN_LOG_FILE.write_text("")
        return jsonify({"status": "ok"})

    entries = []
    if GEN_LOG_FILE.exists():
        for line in GEN_LOG_FILE.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except Exception:
                    pass
    # Return most recent first, limit 50
    return jsonify(list(reversed(entries[-50:])))


def append_log(entry: dict):
    GEN_LOG_FILE.open("a").write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Generate ───────────────────────────────────────────────────────────────────

@app.route("/generate", methods=["POST"])
def generate():
    """Row-based sprite sheet generation.

    Accepts either the new row-based format (base_character + actions) or
    the legacy grid-based format (prompt + grid_size) for backwards compatibility.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data"}), 400

    config = load_config()

    if not config.get("deepinfra_api_key") or config["deepinfra_api_key"] == "YOUR_API_KEY_HERE":
        return jsonify({
            "error": "DeepInfra API key not set. "
                     "Edit sprite-gen/config.json and add your key from https://deepinfra.com"
        }), 400

    base_character = data.get("base_character", "").strip()
    actions_input = data.get("actions")
    sprite_size = int(data.get("sprite_size", 64))
    style_suffix = data.get("style_suffix", "retro pixel art, no background, transparent PNG").strip()

    # ── New row-based format ─────────────────────────────────────────────────
    if base_character and actions_input:
        if isinstance(actions_input, str):
            try:
                actions = json.loads(actions_input)
            except json.JSONDecodeError:
                actions = [a.strip() for a in actions_input.split(",")]
        else:
            actions = list(actions_input)

        if not actions:
            return jsonify({"error": "At least one action is required"}), 400

        sprite_size = max(16, min(256, sprite_size))
        output_name = f"sprite_{int(time.time())}"

        try:
            result = generate_sprite_sheet(
                base_character=base_character,
                actions=actions,
                sprite_size=sprite_size,
                style_suffix=style_suffix,
            )

            response_data = {
                "status": "done",
                "output_name": result["generation_id"],
                "sheet_url": f"{SPRITE_PREFIX}/output/{Path(result['sheet_path']).name}",
                "metadata_url": f"{SPRITE_PREFIX}/output/{Path(result['metadata_path']).name}",
                "gif_url": f"{SPRITE_PREFIX}/output/{Path(result["gif_path"]).name}" if result.get("gif_path") else None,
                "frame_urls": [f"{SPRITE_PREFIX}/frames/{Path(p).name}" for p in result["frames_paths"]],
                "frames_per_row": result["frames_per_row"],
                "actions_config": result["actions_config"],
                "sprite_size": sprite_size,
                "total_frames": len(result["frames_paths"]),
            }

            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "output_name": result["generation_id"],
                "base_character": base_character,
                "actions": actions,
                "sprite_size": sprite_size,
                "style_suffix": style_suffix,
                "sheet_url": response_data["sheet_url"],
                "gif_url": response_data["gif_url"],
            }
            append_log(log_entry)

            return jsonify(response_data)

        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    # ── Legacy grid-based format ─────────────────────────────────────────────
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Prompt is required"}), 400

    actions = data.get("actions", ["idle"])
    if isinstance(actions, str):
        actions = [actions]
    grid_size = int(data.get("grid_size", 4))
    sprite_size = int(data.get("sprite_size", 64))
    style_guide_override = data.get("style_guide_override", "")
    reference_image_id = data.get("reference_image_id")
    template_name = data.get("template_name")
    seed = data.get("seed")
    steps_override = data.get("steps")

    grid_size = max(2, min(6, grid_size))
    if sprite_size not in (16, 32, 64, 128):
        sprite_size = 64
    if not actions:
        actions = ["idle"]

    total_frames = grid_size * grid_size
    output_name = f"sprite_{int(time.time())}"

    # Apply steps override
    if steps_override and isinstance(steps_override, int):
        config = dict(config)
        config["generation_steps"] = max(1, min(8, steps_override))

    # Prepend style guide if set
    style_guide = ""
    sg_path = BASE / "style-guide.json"
    if sg_path.exists():
        sg_data = json.loads(sg_path.read_text())
        style_guide = sg_data.get("current", "").strip()
    if style_guide_override:
        style_guide = style_guide_override.strip()

    # Load template partial if set
    template_partial = ""
    if template_name:
        tp_path = TEMPLATES_DIR / f"{template_name}.json"
        if tp_path.exists():
            template_partial = json.loads(tp_path.read_text()).get("partial", "")

    # Build reference image URL if provided
    ref_url = ""
    if reference_image_id:
        for f in REF_DIR.iterdir():
            if f.stem == reference_image_id:
                ref_url = f"/reference/{f.name}"
                break

    try:
        from prompt_builder import ACTION_PROMPTS
        from generator import generate_frame, pixelate_image, save_frames
        from assembler import assemble_spritesheet as _asm

        frame_prompts = []
        for i in range(total_frames):
            action = actions[i % len(actions)]
            frame_prompts.append(ACTION_PROMPTS.get(action, action))

        frames = []
        for i, frame_prompt in enumerate(frame_prompts):
            parts = []
            if template_partial:
                parts.append(template_partial)
            if style_guide:
                parts.append(style_guide)
            parts.append(prompt)
            parts.append(frame_prompt)
            full = " ".join(parts)
            print(f"[{i+1}/{total_frames}] {full[:80]}...")
            raw = generate_frame(full, size=512, config=config, seed=seed)
            sprite = pixelate_image(raw, sprite_size)
            frames.append((frame_prompt, sprite))

        frame_paths = save_frames(frames, FRAMES_DIR)
        print(f"Saved {len(frame_paths)} frames")

        # Build action_frames for assembler: each action gets 4 frames
        action_frames = []
        for i, action in enumerate(actions):
            start = i * grid_size
            end = start + grid_size
            if end <= len(frame_paths):
                action_frames.append((action, frame_paths[start:end]))

        result = _asm(
            action_frames=action_frames,
            output_name=output_name,
            frame_size=sprite_size,
            frames_per_row=grid_size,
            output_dir=str(OUTPUT_DIR),
        )
        print(f"Sprite sheet: {result['sheet_path']}")

        gif_path = OUTPUT_DIR / f"{output_name}.gif"
        gif_result = generate_gif(
            [str(Path(p)) for p in frame_paths],
            str(gif_path),
            delay=100,
        )
        if gif_result:
            print(f"GIF: {gif_result}")

        response_data = {
            "status": "done",
            "output_name": output_name,
            "sheet_url": f"{SPRITE_PREFIX}/output/{Path(result['sheet_path']).name}",
            "metadata_url": f"{SPRITE_PREFIX}/output/{Path(result['metadata_path']).name}",
            "gif_url": f"{SPRITE_PREFIX}/output/{gif_path.name}" if gif_result else None,
            "total_frames": total_frames,
            "grid_size": grid_size,
            "sprite_size": sprite_size,
            "frame_urls": [f"{SPRITE_PREFIX}/frames/{Path(p).name}" for p in frame_paths],
        }

        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "output_name": output_name,
            "prompt": prompt,
            "style_guide": style_guide,
            "template": template_name or "",
            "reference_image_id": reference_image_id or "",
            "actions": actions,
            "grid_size": grid_size,
            "sprite_size": sprite_size,
            "seed": seed or "",
            "sheet_url": response_data["sheet_url"],
            "gif_url": response_data["gif_url"],
        }
        append_log(log_entry)

        return jsonify(response_data)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Regenerate single frame ───────────────────────────────────────────────────

@app.route("/regenerate-frame", methods=["POST"])
def regenerate_frame():
    """Regenerate a single frame by index and return its URL."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON data"}), 400

    frame_index = int(data.get("frame_index", 0))
    prompt = data.get("prompt", "").strip()
    action = data.get("action", "idle")
    sprite_size = int(data.get("sprite_size", 64))
    style_guide_override = data.get("style_guide_override", "")
    template_name = data.get("template_name")
    seed = data.get("seed")
    steps_override = data.get("steps")
    output_name = data.get("output_name", f"regen_{int(time.time())}")

    config = load_config()
    if steps_override and isinstance(steps_override, int):
        config = dict(config)
        config["generation_steps"] = max(1, min(8, steps_override))

    if not prompt:
        return jsonify({"error": "Prompt required"}), 400

    if not config.get("deepinfra_api_key") or config["deepinfra_api_key"] == "YOUR_API_KEY_HERE":
        return jsonify({"error": "API key not configured"}), 400

    # Load style guide
    style_guide = ""
    if STYLE_GUIDE_FILE.exists():
        sg_data = json.loads(STYLE_GUIDE_FILE.read_text())
        style_guide = sg_data.get("current", "").strip()
    if style_guide_override:
        style_guide = style_guide_override.strip()

    # Load template
    template_partial = ""
    if template_name:
        tp_path = TEMPLATES_DIR / f"{template_name}.json"
        if tp_path.exists():
            template_partial = json.loads(tp_path.read_text()).get("partial", "")

    frame_prompt = ACTION_PROMPTS.get(action, action)
    parts = []
    if template_partial:
        parts.append(template_partial)
    if style_guide:
        parts.append(style_guide)
    parts.append(prompt)
    parts.append(frame_prompt)
    full = " ".join(parts)

    try:
        from generator import generate_frame, pixelate_image
        raw = generate_frame(full, size=512, config=config, seed=seed)
        sprite = pixelate_image(raw, sprite_size)

        # Save as regen frame
        frame_path = FRAMES_DIR / f"frame_{frame_index:03d}.png"
        sprite.save(str(frame_path), "PNG")

        return jsonify({
            "status": "ok",
            "frame_url": f"{SPRITE_PREFIX}/frames/{frame_path.name}",
            "frame_index": frame_index,
        })
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ── Download routes ─────────────────────────────────────────────────────────────

@app.route("/output/<filename>")
def download_output(filename):
    file_path = OUTPUT_DIR / Path(filename).name
    if not file_path.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(file_path, as_attachment=True)


@app.route("/frames/<filename>")
def download_frame(filename):
    file_path = FRAMES_DIR / Path(filename).name
    if not file_path.exists():
        return jsonify({"error": "Not found"}), 404
    return send_file(file_path, as_attachment=True)


@app.route("/actions")
def list_actions():
    return jsonify({"actions": list(ACTION_PROMPTS.keys())})


# ── Rebuild sprite sheet from current frames ───────────────────────────────────

@app.route("/rebuild-sheet", methods=["POST"])
def rebuild_sheet():
    """Rebuild sprite sheet from existing numbered frames in frames/ directory."""
    data = request.get_json() or {}
    grid_size = int(data.get("grid_size", 4))  # frames per row
    output_name = data.get("output_name", f"rebuild_{int(time.time())}")

    # Collect numbered frames in order
    frame_paths = sorted([str(p) for p in FRAMES_DIR.glob("frame_*.png")])
    if not frame_paths:
        return jsonify({"error": "No frames found"}), 400

    # Convert flat frame list to action_frames format (one unnamed action)
    # Assumes frames are sequential: 000,001,002... grouped by grid_size
    action_frames = [(f"row_{i//grid_size}", frame_paths[i:i+grid_size])
                     for i in range(0, len(frame_paths), grid_size)]
    action_frames = [(name, paths) for name, paths in action_frames if len(paths) == grid_size]

    if not action_frames:
        return jsonify({"error": f"Need {grid_size} frames to build a row"}), 400

    result = assemble_spritesheet(
        action_frames=action_frames,
        output_name=output_name,
        frame_size=64,
        frames_per_row=grid_size,
        output_dir=str(OUTPUT_DIR),
    )

    gif_result = generate_gif(frame_paths, str(OUTPUT_DIR / f"{output_name}.gif"), delay=100)

    return jsonify({
        "status": "ok",
        "sheet_url": f"{SPRITE_PREFIX}/output/{Path(result['sheet_path']).name}",
        "gif_url": f"{SPRITE_PREFIX}/output/{output_name}.gif" if gif_result else None,
        "frame_urls": [f"{SPRITE_PREFIX}/frames/{Path(p).name}" for p in frame_paths],
    })


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init()
    port = 5000
    print(f"Sprite Generator → http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)

# Sprite Generator — Design Document
**Version:** 1.0
**Date:** 2026-03-29
**Status:** Authoritative Reference

---

## 1. Concept & Goals

A local-first pixel art sprite sheet generator for game developers.
Describe a character in plain text → get a game-ready sprite sheet with animation frames, metadata, and preview GIF.

**Cost target:** ~$0.0005 per frame (DeepInfra FLUX-1-schnell at 512×512, pixelated to 64px).

**Non-goals:** Not a pixel art editor. Not a game engine. Everything except image generation runs locally on CPU.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser / Client                          │
│  templates/index.html  (Flask-rendered, vanilla JS SPA)          │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTP JSON
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Flask app.py  (:5000)                        │
│                    Gunicorn 4 workers, timeout=300s              │
│                  nginx reverse-proxy at /sprite/                  │
└───────┬──────────────┬──────────────┬──────────────────────────┘
        │              │              │
        ▼              ▼              ▼
   generation.py   candidates.py  directional.py
        │              │              │
        ▼              ▼              ▼
   generator.py   generator.py   generator.py
        │              │              │
        ▼              ▼              ▼
  DeepInfra API  DeepInfra API  DeepInfra API
  FLUX schnell   FLUX schnell   FLUX schnell
        │
        ▼
  raw_qc.py  (pixel heuristic, fast)
        │
        ▼
  pixelate_image()  (PIL resize+sharpen, local)
        │
        ▼
  frame_ranker.py  (CNN quality scoring)
        │
        ▼
  assembler.py  (PNG sheet + JSON metadata + GIF)
        │
        ▼
  output/  /frames/  generation-log.jsonl
```

### Directory Structure

```
/opt/sprite-gen/               ← VPS live code
├── app.py                      Flask web UI + all HTTP routes
├── generator.py                DeepInfra API client + PIL pixelation
├── generation.py              Pipeline orchestration (txt2img→QC→pixelate→rank→assemble)
├── assembler.py               Sprite sheet PNG + JSON + GIF assembly
├── prompt_builder.py          ACTION_PROMPTS dict + prompt construction
├── frame_ranker.py            CNN ranker + QC scoring + feet normalization
├── raw_qc.py                  Pixel-heuristic QC (fast, catches empty/missing strips)
├── vision_qc.py               Ollama LLaVA semantic QC (DISABLED — too slow on CPU)
├── candidates.py              N-candidate generation for user selection
├── directional.py             8-directional sprite generation
├── style.py                   Style guide load/save
├── reference.py               Reference image palette extraction
├── consistency.py             Style consistency engine (img2img prompt building)
├── sprite_qc_model.pth         Trained CNN model (493KB, 83.3% accuracy, n=141)
│
├── defaults/
│   ├── style-guide-default.json
│   └── prompt-templates.json
├── prompt-templates/           User-saved template partials (9 built-in)
├── reference-library/          Uploaded reference sprites
├── output/                      Generated sprite sheets + GIFs + metadata
├── frames/                      Per-frame PNGs (numbered frame_XXX.png)
├── candidates/                  Candidate sprites per action/frame
├── .frame_cache/                Cached raw FLUX outputs (binary, ~600 entries)
├── templates/index.html         Web UI (single-page app)
├── config.json                  API keys + settings
├── generation-log.jsonl         Append-only generation history
├── PLAN.md                      "Select → Animate" workflow spec
└── MANUAL.md                    User-facing documentation

/mnt/d/openclaw-workspaces/theengineer/sprite-gen/  ← Local dev workspace
(same structure, synced to VPS via rsync)
```

---

## 3. Core Data Flow

### 3A. Standard Sprite Sheet Generation (`/sprite/generate`)

```
User input:
  base_character: "isometric pixel art female flight attendant..."
  actions: ["idle", "walk"]
  sprite_size: 64

Generation pipeline (per action, 4 frames each):
┌──────────────────────────────────────────────────────────────────┐
│  For frame_idx = 0..3:                                           │
│    seed = action_seed + frame_idx                                │
│    prompt = base_character + ", " + ACTION_PROMPTS[action][idx]  │
│    ┌─────────────────────────────────────────────────────────┐   │
│    │  txt2img: generate_frame(prompt, seed)  → raw_bytes    │   │
│    │  raw_qc: qc_raw_flux_image(raw_bytes)    → pass/fail    │   │
│    │  retry up to MAX_RETRIES if fail                          │   │
│    └─────────────────────────────────────────────────────────┘   │
│    ↓ raw_bytes (512×512 RGB)                                     │
│    pixelate_image(raw_bytes, 64) → sprite (64×64 RGBA)           │
│    ┌─────────────────────────────────────────────────────────┐   │
│    │  CNN ranker: qc_score(sprite, action) → 0.0–10.0         │   │
│    │  Normalize feet_y to reference frame                     │   │
│    └─────────────────────────────────────────────────────────┘   │
│    ↓ sprite_path                                                  │
│  All 4 frames → assemble_spritesheet() → sheet.png + metadata   │
│  → generate_gif() → preview.gif                                  │
└──────────────────────────────────────────────────────────────────┘

Output:
  sheet_url: /sprite/output/sprite_<hash>.png
  gif_url:   /sprite/output/sprite_<hash>.gif
  frame_urls: [/sprite/frames/frame_000.png, ...]
  metadata_url: /sprite/output/sprite_<hash>.json
  actions_config: [{action, seed, frames}, ...]
```

**Timing:** ~20s/frame × 4 frames × 2 actions = ~160s (with QC retries)
**QC chain:** raw_qc (fast pixel) → CNN ranker (scoring) — vision_qc is DISABLED

### 3B. Candidate Generation (`/sprite/candidates`)

```
User input:
  base_character: "isometric pixel art businessman..."
  action: "idle"
  animation_frame: 0          ← which of the 4 walk-cycle poses
  n_candidates: 6             ← how many variations to show

Pipeline:
  prompt = base_character + ", " + _get_frame_detail(action, frame)
  For i = 0..n_candidates-1:
    seed = base_seed + i
    raw_bytes = generate_frame(prompt, seed)
    if not raw_qc(raw_bytes).passed: retry
    sprite = pixelate_image(raw_bytes, 64)
    qc = qc_score(sprite, action)
    Save → candidates/<action>_<frame>/candidate_XX.png

Output:
  candidates: [{index, url, qc_score, qc_passed, seed}, ...]
  n_generated: count
```

**Timing:** ~20s/candidate × 6 = ~120s for one frame position
**Use case:** User picks best of 6 → that candidate's seed is used for animate

### 3C. Directional Animation (`/sprite/animate`) ← BROKEN

```
User input:
  base_character: "..."
  actions: ["idle", "walk"]
  reference_sprite_url: "..."   ← optional, for consistency

Pipeline (SYNCHRONOUS — this is the bug):
  For each action:
    For each of 8 directions (N NE E SE S SW W NW):
      For each of 4 animation frames:
        seed = base_seed + direction_idx*4 + frame_idx
        prompt = directional_prompt(base_character, action, frame, direction)
        raw = generate_frame(prompt, seed)
        sprite = pixelate_image(raw, 64)
        Save → output/<action>_<direction>_<frame>.png

  Total: len(actions) × 8 × 4 = 32–64 FLUX API calls
  Timing: ~10s/call × 32 = ~320s > 300s gunicorn timeout → REQUEST KILLED

Output (never reached due to timeout):
  gif_urls: {action: url, ...}
  directions: [N, NE, E, ...]
  base_seed: ...
```

**Bug:** Synchronous 32 FLUX calls in a single HTTP request. Gunicorn worker dies.

### 3D. img2img Consistency Engine (STAGED, not fully deployed)

```
Goal: Frames 1-3 of an action should look like frame 0 (same character).

Method:
  Frame 0: txt2img only (establishes character identity)
  Frames 1-3: img2img from frame 0's raw output
    - prompt includes frame-specific pose detail
    - denoising_strength = 0.3 (30% change from reference)
    - Preserves character identity, changes pose

API confirmed: DeepInfra FLUX schnell supports img2img.
  POST to same endpoint with `image` param (base64 PNG)
  Output is always 1024×1024 RGB (ignores image_size param)

Status: Local workspace has the code. VPS deployment blocked by:
  1. img2img output is 1024×1024 (not 512×512) — need to handle resize
  2. Pixelation pipeline expects 512×512 input — need to add pre-processing
  3. generation.py on VPS has the helpers but calling code has signature bugs
```

---

## 4. HTTP API Reference

All routes prefixed with `/sprite`. nginx passes full path to Flask on port 5000.

### Generation

| Method | Route | Purpose | Timing |
|--------|-------|---------|--------|
| POST | `/sprite/generate` | Full sprite sheet (row-based) | ~90s |
| POST | `/sprite/regenerate-frame` | Single frame redo | ~30s |
| POST | `/sprite/rebuild-sheet` | Reassemble from existing frames | ~5s |

### Candidate Selection

| Method | Route | Purpose | Timing |
|--------|-------|---------|--------|
| POST | `/sprite/candidates` | Generate N candidates for 1 frame | ~120s |

### Animation

| Method | Route | Purpose | Timing |
|--------|-------|---------|--------|
| POST | `/sprite/animate` | 8-directional sprites | **BROKEN** (~320s, times out) |

### Asset Management

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/sprite/config` | Public config (API key masked) |
| POST | `/sprite/config` | Update config |
| GET | `/sprite/style-guide` | Current style guide |
| POST | `/sprite/style-guide` | Update style guide |
| GET | `/sprite/templates` | List all templates |
| POST | `/sprite/templates` | Save a template |
| POST | `/sprite/upload-reference` | Upload a reference image |
| GET | `/sprite/references` | List references |
| DELETE | `/sprite/reference/<id>` | Delete a reference |
| GET | `/sprite/actions` | List available action types |
| GET | `/sprite/generation-log` | Last 50 generation entries |

### Static Serving

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/sprite/output/<filename>` | Download output file |
| GET | `/sprite/frames/<filename>` | Download frame file |
| GET | `/sprite/reference/<filename>` | Serve reference image |

---

## 5. Quality Control Pipeline

### 5.1 Raw QC (raw_qc.py) — FAST, always on

Pixel-heuristic check on 512×512 FLUX output before pixelation.

**Checks:**
- Image is not empty (total dark pixel %)
- Each quadrant has sufficient content (no erased strips)
- Expected figures present (1 for single-character prompts)

**Thresholds:**
- `dark_threshold=0.005` (0.5% dark pixels = possibly empty)
- `expected_figures=1` (corrected from old value of 4)
- `min_figure_height=100` pixels

**Integration:** Inside retry loop — if raw QC fails, retry with new seed immediately.

### 5.2 Vision QC (vision_qc.py) — SLOW, DISABLED

Ollama LLaVA semantic check. Asks: "Does this image show a correct pixel art character?"

**Status:** DISABLED. LLaVA on VPS CPU takes 3-5 minutes per frame. Not practical.

**If re-enabled:**
- Runs AFTER raw QC passes (not inside retry loop)
- Prompt: "Question: Does this image show a correct pixel art sprite? Answer: yes or no."
- Parses: `r"PASS:\s*(yes|no)"` (case-insensitive)

### 5.3 CNN Ranker (frame_ranker.py) — FAST, always on

Trained PyTorch CNN that scores frame quality 0.0–10.0.

**Model:** `sprite_qc_model.pth` (493KB, 83.3% accuracy, n=141 training samples)

**Scoring logic:**
```python
# 1. CNN forward pass → raw sigmoid score (often near 0 for real FLUX output)
# 2. Replace None/0.0 CNN scores with 5.0 (neutral fallback)
# 3. Normalize CNN scores within batch: cnn_norm = (score - min) / (max - min + eps)
# 4. Filter to QC-passing candidates only
# 5. Final score = cnn_norm × QC_score (tiebreaking within passing candidates)
# 6. If CNN unavailable or uniform: fall back entirely to max(qc_scores)
```

**Known issues:**
- Model was trained on synthetic corruptions, not real FLUX failures
- Real FLUX output gets near-0 CNN scores → treated as fallback
- Works in practice because QC filtering is the primary gate

### 5.4 Feet Normalization

**Goal:** All frames in an animation should have feet at the same Y coordinate.

**Method:**
- Per-frame: detect bottom-most non-transparent row → `feet_y`
- First frame establishes `reference_feet_y`
- Subsequent frames: if `feet_y` differs, shift sprite vertically to align
- Tolerance: within 2px triggers shift; otherwise flag for review

**Variance observed:** 56–62px across frames (needs further investigation)

---

## 6. The Select → Animate Workflow (PLAN.md)

**Goal:** User picks the best character → system generates all 8 directions.

### Step 1: Candidate Generation
- User enters character description
- System generates 6 candidates for frame 0 (or any frame)
- User clicks best candidate
- **Status:** `/sprite/candidates` works. UI integration PENDING.

### Step 2: Background Animate Job
- User clicks "Animate" button
- `POST /sprite/animate` returns `job_id` immediately (not blocking)
- Background worker runs 32 FLUX calls
- Client polls `GET /animate/status/<job_id>` every 10s
- **Status:** PENDING. `/sprite/animate` is synchronous and broken.

### Step 3: Result Preview
- `GET /animate/status/<job_id>` returns `done` + result URLs
- UI shows: directional GIF + sprite sheet
- **Status:** PENDING.

### Job State Schema (planned)
```json
{
  "job_id": "abc123",
  "status": "running|done|error",
  "progress": {"current": 5, "total": 32, "pct": 15},
  "created_at": "ISO timestamp",
  "base_character": "...",
  "seed": 12345,
  "actions": ["idle", "walk"],
  "result": {
    "sheet_path": "...",
    "gif_urls": {"idle": "...", "walk": "..."},
    "frame_count": 32
  },
  "error": null
}
```

### Files Needed
- `animate_jobs.json` — job registry (maps job_id → state file)
- `animate_jobs/<job_id>.json` — per-job state
- `animate_worker.py` — background subprocess (fork from Flask)

---

## 7. UI Structure (templates/index.html)

Single-page app with tab navigation:

| Tab ID | Name | Purpose |
|--------|------|---------|
| `tab-spro` | Sprite Sheet Pro | Character description → generate + candidate picker |
| `tab-generate` | Generate | Legacy grid-based generation |
| `tab-templates` | Templates | Create/edit prompt templates |
| `tab-guide` | Style Guide | Edit style guide |
| `tab-log` | Log | View generation history |

**tab-spro workflow (current state):**
```
[Character textarea] → [idle] [walk] action checkboxes
↓
[⭐ Generate Sprite Sheet] button
↓
Results: sprite sheet preview + GIF + metadata
```

**tab-spro workflow (planned):**
```
[Character textarea]
↓
[🎭 Generate Candidates] → shows 6 candidate frames in a row
↓
[User clicks best] → highlighted with border
↓
[▶ Animate] button → fires /animate with selected seed
↓
[Polling spinner] → "Generating 8 directions..."
↓
[Result: directional GIF + sprite sheet download]
```

---

## 8. Template System

9 built-in templates in `/opt/sprite-gen/prompt-templates/`:

| Template | Partial Prompt |
|----------|---------------|
| Character - Warrior | warrior, armored, powerful stance, battle-ready |
| Character - Mage | mage, wizard robes, mystical aura, holding staff |
| Character - Rogue | rogue, stealthy assassin, dark cloak, dual daggers |
| Enemy - Beast | fierce beast monster, sharp claws, fangs bared |
| Enemy - Undead | undead creature, zombie or skeleton, glowing eyes |
| Item - Weapon | game weapon item, sword or axe or bow, shiny metal |
| Item - Potion | potion, glowing liquid, glass bottle, pixel art RPG item |
| Environment - Dungeon | dungeon tile, stone floor, brick wall section |

Each stored as `Template Name.json` → `{"partial": "prompt text"}`

---

## 9. Configuration

**config.json** (VPS: `/opt/sprite-gen/config.json`):
```json
{
  "deepinfra_api_key": "D4CiLpyiLNWbzhCBrKhSIEdjnLacsUbk",
  "deepinfra_base_url": "https://api.deepinfra.com/v1/openai/images/generations",
  "model": "black-forest-labs/FLUX-1-schnell",
  "generation_steps": 4,
  "default_sprite_size": 64,
  "generation_timeout": 300,
  "ollama_endpoint": "http://127.0.0.1:11434"
}
```

**Environment variables:**
- `DISABLE_VISION_QC=1` — disables Ollama LLaVA (default: 1=disabled)
- `FRAME_N_CANDIDATES=2` — candidates per frame for ranker selection

---

## 10. Known Gaps & Missing Components

### A. `/animate` Background Jobs — NOT BUILT
**Severity:** HIGH — blocks the core Select→Animate workflow.

The endpoint exists but runs synchronously and times out. Needs:
1. `POST /animate` → fork background process → return `job_id` immediately
2. `GET /animate/status/<job_id>` → read job state from disk
3. `GET /animate/result/<job_id>` → return result URLs
4. `animate_worker.py` → does the actual 32-frame generation
5. Job state files: `animate_jobs/` directory + registry

### B. Candidate Picker UI — PARTIAL
**Severity:** HIGH — candidates endpoint works but not integrated into browser UI.

`/sprite/candidates` works. Browser UI (`tab-spro`) doesn't call it.
Needs: button to generate candidates → grid display → click to select → pass seed to animate.

### C. img2img Consistency — STAGED, NOT FULLY DEPLOYED
**Severity:** MEDIUM — code exists locally but has signature bugs and is not on VPS.

Local `generation.py` has img2img helpers but:
1. `_generate_until_qc` signature mismatch between local and VPS
2. img2img output is 1024×1024 RGB (needs resize to 512×512 before pixelation)
3. Not tested end-to-end in production

### D. Feet Normalization — INCOMPLETE
**Severity:** MEDIUM.

Variance of 56–62px observed across frames. Current normalization is simple
(bottom-row detection + vertical shift). May need:
- Per-frame feet detection with body-aware masking
- Articulation-aware normalization (shift torso, not just crop)
- Validation that normalized frames still look correct

### E. Vision QC — DISABLED
**Severity:** LOW (for now).

LLaVA on VPS CPU is too slow (3-5 min/frame). Re-enable when:
- Faster model available, OR
- Cloud vision API authorized (~$0.01-0.05/image with Gemini 1.5 Flash)

### F. CNN Ranker Retraining — NEEDED
**Severity:** LOW-MEDIUM.

Current model trained on synthetic corruptions, not real FLUX failures.
Retrain with actual FLUX outputs labeled good/bad for better tiebreaking.

### G. systemd Service — MISSING
**Severity:** LOW (operational).

Gunicorn started manually via nohup. No systemd unit file.
If server restarts, sprite-gen won't auto-start.

### H. `run_gen.py` Bug — EXISTS
**Severity:** LOW (script is reference only, not used by web UI).

Uses `result.get('sprite_sheet')` but actual key is `result.get('sheet_path')`.
Prints "NONE" even on successful generation.

---

## 11. Prompt Engineering Notes

### What Works
- `isometric pixel art [subject]` — produces full-height figures
- `retro pixel art, no background, transparent PNG` — clean output
- "navy blue" (not "navy") — avoids DeepInfra content filter
- "flight attendant" (not "stewardess") — avoids content filter
- Single figure per prompt (NOT "4 frames" or "walking, 4 frames") — FLUX can't render multiple figures reliably

### FLUX Limitations
- Cannot reliably draw 4 walking figures in one horizontal strip
- "4 frames" keyword makes it WORSE (renders 4 tiny figures side by side)
- Aspect ratio varies: 0.76–1.0 for single-figure prompts
- Content filter triggers on: "navy" (alone), "stewardess", "uniform"

### Animation Frame Prompts
Each action has 4 frame-specific poses in `prompt_builder.py`:
- `idle`: neutral → breathe in → neutral → breathe out
- `walk`: left foot forward → passing → right foot forward → passing
- `run`: stride start → peak stride → opposite stride → mid-stride passing

---

## 12. VPS Infrastructure

| Item | Value |
|------|-------|
| Provider | AlmaLinux 9.7 |
| IP | 69.48.207.73 |
| SSH | root@69.48.207.73 (key auth) |
| CPU | 4-core AMD EPYC |
| RAM | 7.5GB |
| Disk | 239GB (17GB used, 7%) |
| Ollama | 0.18.3 at 127.0.0.1:11434 |
| Vision model | llava (4.7GB, hash 8dd30f6b0cb1) |
| Gunicorn | 4 workers, timeout=300s, PID 660492 |
| nginx | Proxy to Flask at 127.0.0.1:5000 |
| Web root | /var/www/tricorder/releases/v0.5/ |
| App URL | http://69.48.207.73/sprite/ |
| Live sprite | http://69.48.207.73/latest_sprite.png |

---

## 13. Git & Deployment

**GitHub repos:**
- `github.com/sethinthebox/sprite-gen` (public)
- Backup: `github.com/sethinthebox/larson-openclaw-backup`

**Deploy procedure:**
```bash
# 1. Edit local files at /mnt/d/openclaw-workspaces/theengineer/sprite-gen/
# 2. Rsync to VPS:
rsync -avz -e "ssh -o StrictHostKeyChecking=no" \
  --exclude='.git' --exclude='output/' --exclude='frames/' \
  --exclude='__pycache__/' --exclude='*.pyc' \
  /mnt/d/openclaw-workspaces/theengineer/sprite-gen/ \
  root@69.48.207.73:/opt/sprite-gen/
# 3. Clear bytecode cache on VPS:
ssh root@69.48.207.73 "find /opt/sprite-gen -name '*.pyc' -delete; find /opt/sprite-gen -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null"
# 4. Reload gunicorn (graceful):
ssh root@69.48.207.73 "kill -USR1 \$(cat /var/run/gunicorn.pid)"
```

# Sprite Generator — SPEC

## Concept & Vision

A local web UI that generates pixel art sprite sheets from text prompts, mirroring spritegenerator.online. 
Sends prompts to a cloud AI API (DeepInfra/Flux), then assembles the resulting frames into a sprite sheet + JSON metadata using Aseprite CLI. All orchestration and UI is local Python.

**Philosophy:** Fast iteration, no GPU required, pay-per-image at ~$0.0005/sprite.

## Architecture

```
sprite-gen/
├── SPEC.md
├── app.py              # Flask web UI
├── generator.py         # Core generation logic
├── assembler.py         # Aseprite CLI sprite sheet assembly
├── config.json         # API keys, paths
├── templates/
│   └── index.html      # Web UI
├── output/             # Generated sprites
└── frames/             # Temp frame storage
```

## Data Flow

1. User submits prompt + settings (grid size, actions, size)
2. For each frame needed: POST to DeepInfra API → receive PNG
3. Save frames to `frames/` directory
4. Run Aseprite CLI to assemble sprite sheet + JSON
5. Offer download of sprite sheet + JSON + individual frames
6. (Optional) Run pixel-art reduction pass

## API Design

### Web UI
- `GET /` — Serve the web UI
- `POST /generate` — Generate sprite sheet
  - Body: `{prompt, grid_size, sprite_size, actions}`
  - Response: `{status, output_files: [...]}`
- `GET /output/<filename>` — Download generated file

### Generation API (internal)
- `generate_frames(prompt, count, size)` → list of PNG bytes
- `assemble_spritesheet(frame_paths, grid_size, output_path)` → sprite sheet + JSON

## Image Generation

**Provider:** DeepInfra (OpenAI-compatible API)
**Model:** `black-forest-labs/FLUX-1-schnell` (fast, cheap)
**Settings:**
- Size: 512×512 (then crop/resize to sprite pixel size)
- Steps: 4 (schnell is fast at low steps)
- Prompt enhancement: append "pixel art, game sprite, transparent background, clean lines"

**Pixel art conversion:**
After generation, use PIL to:
1. Resize to sprite pixel size (e.g., 64×64)
2. Reduce colors to a game-appropriate palette
3. Apply slight sharpen

## Sprite Sheet Assembly

Use Aseprite CLI:
```
aseprite -b --sheet sheet.png --data sheet.json \
  --sheet-type rows \
  --filename-format "{frame}" \
  frame1.png frame2.png ...
```

## UI Design

Simple, single-page:
- Prompt textarea (with example)
- Grid size selector (2×2, 3×3, 4×4, 5×5, 6×6)
- Sprite size selector (16, 32, 64, 128 px)
- Actions multiselect: Idle, Walk, Run, Attack, Cast, Jump, Dance, Death, Dodge
- "Generate" button → shows progress
- Output: preview of sprite sheet + download buttons

## Pricing Estimate

Per frame (512×512, FLUX-1-schnell, 4 steps):
- $0.0005 × (512/1024)² × 4 = ~$0.0005
- 16 frames = $0.008 (0.8 cents)

## Design Decisions

1. **Flask over nothing** — simple HTTP server, no JS framework needed
2. **DeepInfra over OpenAI** — 10x cheaper, same API format
3. **Aseprite for assembly** — proven CLI, handles PNG→sprite sheet cleanly
4. **PIL for pixel art conversion** — resize + palette reduction
5. **frames/ as temp** — cleanup after each generation

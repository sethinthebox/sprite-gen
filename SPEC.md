# Sprite Generator — Specification

## Concept & Vision

A local-first pixel art sprite sheet generator for game developers.
Type a character description, pick actions, and get a game-ready sprite sheet
with style consistency across generations. Cloud API handles image generation;
everything else runs locally with no GPU required.

**Design goal:** Spend $0.01 generating sprites, not $50/month on software licenses.

---

## Architecture

```
sprite-gen/
├── app.py                  Flask web UI (HTTP API + serving)
├── generator.py            DeepInfra API client + pixelation
├── assembler.py            Sprite sheet PNG + JSON + GIF assembly
├── prompt_builder.py       Prompt construction + action poses + quality scoring
├── style.py                Style guide load/save/validate
├── reference.py            Reference image palette extraction
├── consistency.py          Style consistency engine ("same character but...")
├── generation.py           High-level pipeline + generation log
│
├── defaults/
│   ├── style-guide-default.json    Default "Classic Pixel RPG" style
│   └── prompt-templates.json       18 archetype templates
│
├── reference-library/       Uploaded reference sprites
├── output/                  Generated sprite sheets
├── frames/                  Per-frame PNGs (temp)
├── style-guide.json         Active style guide
├── generation-log.jsonl     Generation history (append-only JSONL)
├── config.json              API keys + settings (not committed)
└── config.example.json      Template for config.json
```

### Dependency graph

```
app.py
  ├── generator.py           (API + pixelation)
  ├── assembler.py           (PNG/JSON/GIF assembly)
  │     └── PIL/Pillow
  ├── prompt_builder.py      (ACTION_PROMPTS, build_full_prompt, quality scoring)
  │     ├── style.py         (get_style_keywords)
  │     └── reference.py     (get_reference → palette hints)
  ├── style.py
  ├── reference.py
  ├── consistency.py
  │     ├── style.py
  │     └── reference.py
  └── generation.py          (orchestration layer)
        ├── generator.py
        ├── assembler.py
        ├── prompt_builder.py
        ├── style.py
        └── consistency.py
```

**Key rule:** `generator.py` knows nothing about prompts.
`prompt_builder.py` knows nothing about APIs.

---

## Data Flow

```
User prompt + settings
       │
       ▼
prompt_builder.py          ← builds full prompt with style + action + reference
       │
       ▼
generator.py              ← sends to DeepInfra, receives PNG bytes
       │
       ▼
pixelate_image()          ← resize to sprite size, sharpen
       │
       ▼
assembler.py              ← lay out frames → sprite sheet PNG + JSON
       │
       ▼
generate_gif()            ← optional animated GIF preview
       │
       ▼
generation-log.jsonl      ← record what was generated
```

---

## Image Generation

| | |
|---|---|
| **Provider** | DeepInfra (OpenAI-compatible API) |
| **Model** | `black-forest-labs/FLUX-1-schnell` |
| **Cost** | ~$0.0005/frame (512×512, 4 steps) |
| **API size** | 512×512 (then pixelated down to sprite size) |
| **Pixelation** | PIL `NEAREST` resize + `SHARPEN` filter |

---

## Sprite Sheet Format

Output PNG is a grid of N×N frames. JSON metadata uses Aseprite-compatible format:

```json
{
  "frames": {
    "frame_000.png": {
      "frame": {"x": 0, "y": 0, "w": 64, "h": 64},
      "sourceSize": {"w": 64, "h": 64},
      "duration": 100
    }
  },
  "meta": {
    "image": "sheet.png",
    "size": {"w": 256, "h": 256},
    "format": "RGBA8888"
  }
}
```

Import directly into Unity, Godot, Aseprite, or any engine with sprite sheet support.

---

## Style Guide System

A style guide is a JSON file that defines your project's visual contract:

```json
{
  "name": "Dark Fantasy RPG",
  "version": "1.0",
  "palette": {
    "primary": "#4a6741",
    "secondary": "#8b7355",
    "accent": "#d4a574",
    "background": "#1a1a2e"
  },
  "art_style": {
    "outline": "1px",
    "shading": "flat",
    "dithering": "none"
  },
  "constraints": {
    "max_colors_per_sprite": 24,
    "transparent_bg": true
  },
  "keywords": {
    "always_include": ["clean pixel art", "game sprite"],
    "never_include": ["photorealistic", "3d render"]
  }
}
```

Set once in **Settings → Style Guide** → every generation automatically includes it.

---

## Reference Image System

Upload a sprite you're happy with:

1. The system extracts its **dominant palette** (8 colors)
2. Extracts **style hints** — brightness, edge variance, pixel density
3. All future generations using this reference get color hints prepended

Workflow: *"Generate → find the best frame → upload as reference → generate variations"*

---

## Consistency Engine

The `consistency.py` module enables **"same character, new pose"** generation:

- `detect_character_components()` — parses a prompt into structured parts (subject, clothing, accessories, pose, view angle)
- `apply_modifications()` — handles "same character but with [X]" transformations
- `build_variation_prompt()` — scaffold prompts for pose/view/accessory variations
- `style_distance()` — measure how stylistically similar two prompts are (0.0 = identical, 1.0 = completely different)

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `POST` | `/generate` | Generate sprite sheet |
| `POST` | `/regenerate-frame` | Regenerate single frame |
| `POST` | `/rebuild-sheet` | Rebuild sheet from existing frames |
| `GET/POST` | `/config` | Get/update API key + settings |
| `GET/POST` | `/style-guide` | Get/update active style guide |
| `GET` | `/templates` | List prompt templates |
| `POST` | `/upload-reference` | Upload reference image |
| `GET` | `/references` | List reference library |
| `DELETE` | `/reference/<id>` | Delete reference |
| `GET` | `/generation-log` | Fetch generation history |
| `GET` | `/actions` | List available actions |
| `GET` | `/output/<filename>` | Download output file |
| `GET` | `/frames/<filename>` | Download individual frame |

---

## Design Decisions

1. **PIL over Aseprite CLI** — no external GUI tool dependency, runs on any OS
2. **DeepInfra over OpenAI** — ~10× cheaper, same API shape
3. **JSONL for logging** — append-only, human-readable, trivially filterable with `grep`
4. **Style guide as JSON** — lets you version-control your project's visual contract
5. **`ACTION_PROMPTS` in one place** — `prompt_builder.py`; everything else imports from there
6. **`reference.py` for all reference operations** — palette extraction, metadata, CRUD; `consistency.py` consumes it

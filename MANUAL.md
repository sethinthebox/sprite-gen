# Sprite Generator — User Manual

## What this is

A local web app that generates pixel art sprite sheets from text descriptions.
You describe a character, pick animation actions, and get back:

- A **sprite sheet** (PNG) — grid of animation frames
- A **metadata file** (JSON) — Aseprite-compatible, ready for import into any game engine
- An **animated GIF** — quick preview of the animation
- Individual **frame PNGs** — for frame-by-frame editing

Cost: ~$0.0005 per frame (DeepInfra FLUX-1-schnell, 512×512 → pixelated down).

---

## Quick Start

1. Open the app at **http://localhost:5000**
2. In the **Generate** tab, enter a prompt:  
   `pixel art warrior, silver plate armor, determined expression, holding sword and shield, side view`
3. Select actions: `Idle`, `Walk`, `Run`, `Attack`
4. Set grid to **4×4** (16 frames = 4 cycles of your 4 actions)
5. Set sprite size to **64px**
6. Click **Generate**
7. Download the sprite sheet, JSON metadata, or individual frames

---

## Writing Good Prompts

The FLUX model is good at following specific visual instructions. Be concrete.

### Good prompt structure

```
[subject], [clothing/accessories], [colors], [view angle], [style keywords]
```

### Examples

| What you want | Prompt |
|---------------|--------|
| Front-facing hero | `pixel art hero, blue tunic, brown boots, silver sword, front view, clean pixel art style` |
| Fire mage | `pixel art mage, red robes, golden trim, white beard, holding staff with fire orb, casting pose, side view` |
| Cute slime | `pixel art slime monster, bright green, translucent gel body, cute cartoon eyes, bouncing pose, 4-frame animation` |
| Armored boss | `pixel art dark knight, black spiked armor, red cape, skull motif on chestplate, massive sword, imposing stance, front view` |

### Keywords that help

Always include:
- `pixel art` — tells the model what style you want
- `game sprite` — reinforces game art context
- `transparent background` — no background clutter to mask out

Helpful specifics:
- `clean lines`, `crisp edges`, `no dithering`
- `flat shading` or `cell-shaded`
- `max 16 colors` (or 24, 32 — pick a limit)
- `front view`, `side view`, `3/4 view`, `isometric`
- `clean pixel art style`

### Keywords to avoid

- `photorealistic`, `3D render`, `photograph`, `cinematic` — these push toward non-pixel-art styles
- `blurry`, `smooth` — you want crisp pixels

### View angle matters

If you need a character from multiple angles, generate each angle separately:
- `side view` for walking/running
- `front view` for idle/attack
- `3/4 view` for RPG battle sprites

---

## Actions

Each action adds a pose description to your prompt. Actions cycle through your grid.

| Action | Pose added to prompt |
|--------|---------------------|
| `idle` | Standing still, subtle breathing |
| `walk` | Mid-stride walking pose |
| `run` | Full-speed running pose |
| `attack` | Weapon extended, strike pose |
| `cast` | Arms raised, magic gesture |
| `jump` | Legs tucked, peak of jump |
| `dance` | Energetic dance pose |
| `death` | Defeated, falling pose |
| `dodge` | Quick evasive lean |
| `hurt` | Recoiling from injury |
| `block` | Defensive stance with shield |

For sprites **64px and larger**, the model gets more detailed pose descriptions.
For **32px and smaller**, keep it simple — there's not enough resolution for fine details.

### Making custom action sequences

Combine actions to build animation cycles:

```
Grid 4×4 with [Idle, Walk, Run, Attack] = 4 idle frames, 4 walk frames, 4 run frames, 4 attack frames
Grid 2×2 with [Idle, Walk] = 2 idle frames, 2 walk frames (1 complete cycle each)
```

---

## Grid Size

| Grid | Frames | Use case |
|------|--------|----------|
| 2×2 | 4 | 1 complete cycle of 2 actions, or 4 poses |
| 3×3 | 9 | 3-action cycle × 3, or 9 individual poses |
| 4×4 | 16 | 4-action cycle × 4 (most common) |
| 5×5 | 25 | 5-action cycle × 5 |
| 6×6 | 36 | 6-action cycle × 6 |

**Tip:** For a walking cycle, you typically need 4–8 frames. Set grid to 4×4 with `walk` selected, then use only the first 4–8 frames in your engine and loop them.

---

## Sprite Sizes

| Size | Best for |
|------|----------|
| **16px** | Icons, status indicators, tiny enemies, projectiles |
| **32px** | Small characters, NPC sprites, items |
| **64px** | Main character sprites (standard for retro RPGs) |
| **128px** | Large characters, bosses, detailed animations |

The API always generates at 512×512, then pixelates down to your chosen size.
Larger sizes capture more detail from the original generation.

---

## Style Guides — Consistent Art for Your Project

If you're building a full game, all sprites should share the same visual language.
Set it once in the **Style Guide** and it applies to every generation.

### Writing a style guide

In **Settings → Style Guide**, describe your project's aesthetic:

```
Dark fantasy pixel art, limited to 24 colors, 1px black outlines,
flat shading only, no dithering, slightly desaturated palette,
medieval European armor and clothing styles.
```

Or use the built-in "Classic Pixel RPG" style as a starting point and customize it.

### What goes in a style guide

- **Color palette** — define primary/secondary/accent colors
- **Outline style** — `1px black outlines`, `no outlines`, `2px dark outlines`
- **Shading** — `flat shading`, `gradient shading`, `cell-shaded`
- **Dithering** — `none`, `ordered`, `random` (note: dithering in pixel art is stylistic, not an error)
- **Max colors** — `16 colors max`, `24 colors max`
- **Forbidden colors** — things your palette never uses (e.g., `pure white #ffffff`)

The style guide keywords are **prepended to every prompt automatically**.

---

## Reference Images — Consistent Characters

Upload a sprite you're happy with to establish a visual baseline.
The system extracts its color palette and style hints, then uses them
to generate variations that match.

### How to use references

1. Generate a character → pick the best frame
2. Upload it as a reference (drag-and-drop in the **Reference** panel)
3. When generating next time, select that reference
4. The new generation gets color hints from the reference prepended to the prompt

### Generating variations

Once you have a character you like:

- **"Same character, new pose"** — generate the same description but pick a different action
- **"Same character, new equipment"** — "same knight but with red cape and no shield"
- **"Same character, different angle"** — "same knight, 3/4 view instead of side view"

The `consistency.py` module parses your prompt into components (subject, clothing, accessories, pose, view angle) and applies modifications while preserving the visual style.

---

## Prompt Templates

Pre-built partial prompts for common archetypes. Click a template to load it, then customize the description.

| Template | Partial prompt |
|----------|----------------|
| Character - Warrior | Armored warrior, powerful stance, battle-ready, RPG hero sprite |
| Character - Mage | Wizard robes, mystical aura, staff, flowing cape, spell caster |
| Character - Rogue | Dark cloak, dual daggers, sneaky posture, agile build |
| Enemy - Beast | Fierce beast, sharp claws, fangs bared, aggressive pose |
| Enemy - Undead | Skeleton or zombie, glowing eyes, decaying, menacing |
| Item - Weapon | Shiny sword/axe/bow, detailed pixel art, RPG item icon |
| Item - Potion | Glowing liquid in glass bottle, RPG item |
| Environment - Dungeon | Stone floor, brick wall section, dark atmospheric tile |

### Creating custom templates

In **Settings → Templates**, create your own:

```json
{
  "name": "My Game - Goblin",
  "partial": "goblin creature, green skin, ragged cloth tunic, crude wooden club, big pointed ears, malicious grin, [VIEW_ANGLE]"
}
```

Use `[PLACEHOLDER]` syntax for variables you want to fill in each time.

---

## Generation Log — Iterating on Designs

Every generation is logged with its full prompt, settings, and output paths.
Click any entry in the **History** tab to reload the form state and regenerate.

This is useful when:
- You want to revisit a character you generated last week
- A frame turned out bad and you want to regenerate just that one
- You're comparing different prompt phrasings for the same character

The log is stored in `generation-log.jsonl` — human-readable JSON Lines format.

---

## Regenerating Individual Frames

If one frame in a sprite sheet looks wrong:

1. In the **Output** section, click the **↻** button on that frame
2. The frame is regenerated with the same prompt but a new random seed
3. Click **Rebuild Sheet** to reassemble from all current frames

This is faster and cheaper than regenerating the entire sheet.

---

## Troubleshooting

### "API key not set"
Go to **Settings** → enter your DeepInfra API key.
Get one free at https://deepinfra.com → Sign in → API Keys.

### Slow generation
FLUX-1-schnell is fast (4 steps). If it's slow:
- Check your internet connection (the API is cloud-based)
- DeepInfra may be under load — try again in a few minutes

### Sprites come out blurry or non-pixel-art
Add to your prompt:
```
pixel art, clean lines, crisp edges, no anti-aliasing, transparent background
```

### All frames look the same (no animation variation)
This is normal — each frame is generated independently.
For variation, use a larger grid and **different actions** (not the same action repeated).

### The model ignores part of my prompt
FLUX can be inconsistent about following complex descriptions.
Try:
- Shorter, simpler prompts
- More specific color names (`crimson` not just `red`)
- Use the **Style Guide** to lock in style keywords you always want

### Frames don't line up in my game engine
- All frames in a sprite sheet are the same size
- The JSON metadata tells any modern engine the exact frame dimensions
- Import the JSON alongside the PNG — most engines (Godot, Unity, Phaser) auto-detect sprite sheets

---

## Tips & Tricks

**Test cheaply first** — generate 2×2 grids while refining your prompt.
Once it looks right, generate the full 4×4.

**Generate at 64px minimum** for main characters, then scale down if needed.
Smaller sizes lose detail and are harder for the model to render correctly.

**Use consistent view angles** — if your game uses side-view sprites,
always say `side view` in your prompt. Mixing view angles in one character sheet causes animation problems.

**Save good reference sprites** — the first generation is often not the best.
Pick the best frame from several generations, upload it as a reference, then use it to generate consistent variations.

**Batch similar sprites** — if you need 20 goblins with different equipment,
generate one you're happy with, upload as reference, then generate each variant with the reference selected.

**Color palette discipline** — if your game uses a restricted palette (e.g., Game Boy Color's 32 colors), include `max 32 colors` in your style guide. This prevents the model from generating neon colors that can't be used in your game.

---

## Deployment

To run on a server/VPS:

```bash
# Install dependencies
pip install flask pillow requests

# Run with Gunicorn (production WSGI server)
gunicorn -w 4 -b 0.0.0.0:5000 app:app

# Or run directly (for testing)
python3 app.py
```

The app serves its own static files — no Nginx needed for a simple internal deployment.
For public-facing, put Gunicorn behind Nginx for TLS termination.

---

## File Locations

| File | Purpose |
|------|---------|
| `config.json` | API key, model settings |
| `style-guide.json` | Active style guide |
| `generation-log.jsonl` | Generation history |
| `reference-library/` | Uploaded reference sprites |
| `output/` | Generated sprite sheets |
| `frames/` | Temporary frame PNGs |

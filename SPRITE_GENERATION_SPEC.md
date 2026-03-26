# Sprite Sheet Specification for Engineer

## Overview
We need to generate a **SINGLE sprite sheet** containing **all animations for the businessman character** in one organized grid — matching the output style of the reference sprites we downloaded from spritegenerator.online.

---

## Target Output Format

### Sheet Dimensions
- **2048×2048 pixels** total
- **512×512 pixels per frame**
- **4×4 grid = 16 frames total**

### Animation Layout (Row by Row)

| Row | Animation | Frames | Description |
|-----|-----------|--------|-------------|
| **Row 1** | IDLE | 4 frames | Standing still, subtle breathing (0-3) |
| **Row 2** | WALK | 4 frames | Normal walking pace (4-7) |
| **Row 3** | RUN | 4 frames | Full sprint (8-11) |
| **Row 4** | DEATH/DAMAGE | 4 frames | Getting hit → falling (12-15) |

### Visual Style (MUST MATCH Reference)

The character is **THE BUSINESSMAN** — our hero character:

```
CHARACTER: Older businessman, late 50s
- Gray temples, salt and pepper hair
- Navy suit jacket (slightly open)
- White shirt (untucked as damage increases)
- RED tie (loosens as damage increases)
- Tan/khaki dress pants
- Dark dress shoes
- Carrying a BRIEFCASE (Stages 1-3 only)
- Expression: Weary, determined, then desperate

AGE: Late 50s (NOT middle-aged, NOT young)
SUIT: Navy blue
TIE: RED (distinctive)
ACCESSORY: Brown leather briefcase
```

### Transformation Stages (for future sheets)

For now, generate **STAGE 1 (Pristine)**:
- Clean navy suit
- Tie knotted properly
- Shoes polished
- Briefcase in hand
- Expression: Professional but weary

---

## Prompt Template

```
isometric pixel art older businessman, late 50s, gray temples, salt and pepper hair, pristine navy suit jacket, white shirt, red tie, tan dress pants, dark shoes, holding brown leather briefcase, [ANIMATION], [SUBTLE_DETAIL], front facing, clean retro pixel art style, transparent background, [ACTION_NUMBER]/4 frames
```

### Per-Row Prompts

**ROW 1 — IDLE (4 frames):**
```
isometric pixel art older businessman, late 50s, gray temples, salt and pepper hair, pristine navy suit, white shirt, red tie, polished shoes, holding briefcase, standing idle, subtle breathing animation, front facing, clean retro pixel art, transparent background
```
- Frame 0: Standing neutral
- Frame 1: Slight chest rise (breathing in)
- Frame 2: Standing neutral
- Frame 3: Slight chest fall (breathing out)

**ROW 2 — WALK (4 frames):**
```
isometric pixel art older businessman, late 50s, gray temples, salt and pepper hair, pristine navy suit, white shirt, red tie, polished shoes, holding briefcase, walking animation, mid-stride poses, front facing, clean retro pixel art, transparent background
```
- Frame 4: Left foot forward
- Frame 5: Both feet neutral (passing)
- Frame 6: Right foot forward
- Frame 7: Both feet neutral (passing)

**ROW 3 — RUN (4 frames):**
```
isometric pixel art older businessman, late 50s, gray temples, salt and pepper hair, pristine navy suit jacket open, white shirt, red tie loosened, polished shoes, holding briefcase, running animation, dynamic stride, front facing, clean retro pixel art, transparent background
```
- Frame 8: Left leg forward, leaning
- Frame 9: Peak stride
- Frame 10: Right leg forward, leaning
- Frame 11: Peak stride (opposite)

**ROW 4 — DEATH/DAMAGE (4 frames):**
```
isometric pixel art older businessman, late 50s, gray temples, salt and pepper hair, navy suit jacket torn, shirt bloody, red tie loose, clutching briefcase, hit by attack animation, recoiling, desperate expression, front facing, clean retro pixel art, transparent background
```
- Frame 12: Impact frame (recoiling)
- Frame 13: Stumbling backward
- Frame 14: Falling to knees
- Frame 15: On ground, briefcase clutched

---

## What Went Wrong With Previous Generation

### Problem 1: Wrong Grid Structure
**Previous:** Each action was a separate generation with 2×2 grid
**Required:** One 4×4 sheet with ALL actions in rows

### Problem 2: Isometric View Unclear
**Previous:** We said "isometric" but got something closer to front-facing
**Required:** The character should appear to be in a 3/4 isometric view with visible depth — the reference shows this clearly. The body is angled slightly, showing side and front simultaneously.

### Problem 3: Same Pose for All 4 Frames
**Previous:** All 4 frames looked like variations of standing still
**Required:** Each frame in an animation should be a distinct pose in the movement cycle

### Problem 4: Briefcase Visibility
**Previous:** Briefcase was hit or miss
**Required:** The brown leather briefcase should be clearly visible in EVERY frame, held in the hand closest to the viewer's side

---

## Technical Requirements

### Grid: 4×4 (16 frames total)
```
┌─────────┬─────────┬─────────┬─────────┐
│  IDLE   │  IDLE   │  IDLE   │  IDLE   │
│ frame 0 │ frame 1 │ frame 2 │ frame 3 │
├─────────┼─────────┼─────────┼─────────┤
│  WALK   │  WALK   │  WALK   │  WALK   │
│ frame 4 │ frame 5 │ frame 6 │ frame 7 │
├─────────┼─────────┼─────────┼─────────┤
│  RUN    │  RUN    │  RUN    │  RUN    │
│ frame 8 │ frame 9 │ frame 10│ frame 11│
├─────────┼─────────┼─────────┼─────────┤
│ DEATH   │ DEATH   │ DEATH   │ DEATH   │
│ frame 12│ frame 13│ frame 14│ frame 15│
└─────────┴─────────┴─────────┴─────────┘
```

### Frame Size: 512×512 pixels
- Sheet total: 2048×2048
- Each frame: 512×512

### Output Files Needed
1. **sprite_sheet.png** — The 2048×2048 grid
2. **sprite_sheet.json** — Metadata with frame coordinates:
```json
{
  "name": "businessman",
  "frame_width": 512,
  "frame_height": 512,
  "frames": [
    {"row": 0, "col": 0, "name": "idle_0"},
    {"row": 0, "col": 1, "name": "idle_1"},
    ...
  ]
}
```
3. **preview.gif** — Animated preview looping through all frames

---

## Reference Style Notes

The reference sprites we downloaded show:
- **Isometric 3/4 view** — character is angled, not pure front
- **Narrow proportions** — Karateka-style, empty space around figure
- **Clear silhouette** — figure reads clearly even at small sizes
- **Consistent frame size** — body stays centered, only pose changes
- **Action clarity** — you can tell walking vs running vs idle from silhouettes alone

### Proportions (from reference)
- Body height: ~85% of frame height
- Body width: ~45% of frame width
- Centered horizontally
- Slight vertical offset (feet near bottom)

---

## Style Guide for All Generations

**PERMANENT STYLE GUIDE (save to config):**
```
retro 1980s pixel art, isometric 3/4 view, Karateka-style proportions,
narrow sprites with empty space, clean lines, no dithering,
flat shading, max 32 colors, 1px black outlines on characters,
airport terminal setting, dark atmospheric lighting
```

---

## Generation Settings

- **Model:** FLUX-1-schnell (DeepInfra)
- **Steps:** 4-6 (more steps = more detail but slower)
- **Size:** 512×512 per frame (generate at this size, don't scale up)
- **Grid:** 4×4
- **Total frames:** 16
- **Cost estimate:** ~$0.008 (16 frames × $0.0005)

---

## Feedback for Engineer

### What's Working
- Local generation is FAST (~15 seconds)
- Cost is negligible
- Reference image upload is a great feature
- Individual frame regeneration is excellent

### Requests
1. **4×4 grid with row-based animations** — main priority
2. **Pre-built prompt template** for isometric businessman
3. **"Download All" button** — zip with sheet + JSON + GIF + frames
4. **Sheet preview with grid overlay** — see frame boundaries
5. **Save Style Guide to workspace file** — backup with git

### Visual Issues to Fix
1. **Isometric view** — needs to be more pronounced 3/4 angle
2. **Animation cycle** — 4 distinct poses per action, not variations of same pose
3. **Briefcase consistency** — always visible, same position
4. **Character age** — late 50s with gray temples, not middle-aged

---

## Next Steps

1. Generate businessman sheet with correct structure
2. Review output against reference
3. If good: generate Stage 2 (jacket torn) and Stage 3 (pants ripped)
4. Then move to flight attendant, enemies, etc.

---

## Contact
This spec created: 2026-03-25
For project: Hellport Game
Location: /mnt/d/openclaw-workspaces/theassistant/hellport-assets/

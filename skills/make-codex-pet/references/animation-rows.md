# Official animation rows

Read this file only after the base character is approved, and keep this order exactly. Do not invent, rename, omit, reorder, or add states.

| Atlas row | State | Frames | Required semantics |
|---:|---|---:|---|
| 0 | `idle` | 6 | Neutral standing loop with subtle breathing or weight shift. |
| 1 | `running-right` | 8 | Full locomotion cycle facing and traveling toward screen-right. |
| 2 | `running-left` | 8 | Full locomotion cycle facing and traveling toward screen-left; generate independently rather than mirror or copy the right-facing strip. |
| 3 | `waving` | 4 | Stationary friendly greeting with a clearly readable hand wave. |
| 4 | `jumping` | 5 | Centered crouch, liftoff, apex, descent, and landing sequence. |
| 5 | `failed` | 8 | Readable nonviolent failure/error reaction that settles back toward neutral. |
| 6 | `waiting` | 6 | Patient, expectant waiting loop distinct from ordinary idle. |
| 7 | `running` | 6 | Task working in progress/busy state. Keep the character centered; this is not directional locomotion and is not a substitute for either running direction. |
| 8 | `review` | 6 | Inspecting, checking, or reviewing result loop, visually distinct from waiting and waving. |

## Fixed atlas geometry

- Final atlas: exactly 1536×1872 pixels, RGBA PNG, at most 20 MiB.
- Grid: exactly 8×9 cells; each cell is exactly 192×208 pixels.
- Each state occupies its fixed row and uses frames from left to right.
- Rows with fewer than eight frames must leave every unused cell fully transparent. Do not duplicate the last frame into unused cells.
- Every used cell must contain one complete, consistently scaled character. No content may bleed into a neighboring cell.

The build scripts create the atlas and transparent unused cells. Image generation supplies one exact-frame-count horizontal strip per official state; it does not redefine the atlas layout.

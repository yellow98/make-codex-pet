# Image generation contract

Read this file after base approval and apply it to every official animation-row job.

## Required inputs

- Use the installed `imagegen` Skill for every generation or edit; never call an image API directly.
- Start from the matching `<run-dir>/prompts/<job-id>.md` prompt.
- Attach every original reference as actual image input on every row job.
- Also attach the approved `decoded/base.png` as actual image input on every row job. A file path mentioned only in prompt text is not an attachment.
- Keep the run's 3–5 identity lock features explicit. The primary reference decides conflicting clothing; the approved base decides the rendered character design.
- Process one job per generation/record lifecycle. Do not request several strips in one canvas or record one output against several jobs.

## Output contract

Request exactly the state and exact frame count defined in `animation-rows.md`, arranged left-to-right as one horizontal sprite strip. Use a perfectly uniform solid chroma-key background matching `pet_request.json`; do not use gradients, scenery, transparency, shadows cast onto the background, texture, or halos.

Keep face, hair, clothing, accessories, palette, body proportions, scale, baseline, camera, and line/render style consistent with the original reference images and approved base. Show the complete body in every separated frame. Keep motion readable as a smooth loop and avoid unintended frame-to-frame position or scale jumps.

Do not add text, labels, frame numbers, borders, UI, extra characters, duplicated limbs, detached particles, speed lines, dust, glow, or overlapping/cropped frames. Generate `running-left` independently with a true left-facing action; do not mirror the completed `running-right` artwork.

## Targeted retry

If QA finds identity drift, the wrong action/direction, cropping, overlap, background contamination, or jitter, regenerate only that one job. Reattach the original references and approved base, restate the failed constraint, and replace the result with `record_job.py complete --force`. Do not regenerate unaffected rows.

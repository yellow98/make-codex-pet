# Make Codex Pet Speed Optimization Design

## Goal

Reduce the normal end-to-end generation time from roughly 43 minutes in the first real run to 10–15 minutes when the first pass succeeds and 15–25 minutes when limited repair is needed, without changing the user's two-message interaction or removing any of the nine official animation rows.

## Confirmed product behavior

The user still supplies one or more reference images and asks for a Q-style Codex pet in ordinary language. Codex generates one base preview and waits for exactly one approval. After the user replies `可以`, the Skill generates all nine official rows, builds the atlas, validates it, performs visual QA, installs the pet locally, and tells the user to select it under **Settings > Pets** before entering `/pet`.

No extra intake form, animation selection, progress confirmation, runtime installation, API key, standalone desktop program, or community upload is added.

## Chosen approach

### Two-worker row generation

When collaboration workers are available, the parent task assigns at most two explicit row jobs at a time. Each worker receives exactly one job ID, the original reference images, the approved base image, and that row's prompt. Workers do not choose jobs, modify the manifest, build the atlas, or install the pet.

The parent task records each returned image immediately, reloads the manifest, and assigns the next dependency-ready rows until all nine are complete. When collaboration workers are unavailable, the existing sequential path remains valid.

Two workers are the fixed limit. Three or more workers risk image-generation congestion and add coordination overhead without a reliable improvement.

### Local background cleanup before regeneration

The build step removes non-uniform chroma-key backgrounds using pixels connected to the frame boundary instead of relying only on distance from one exact RGB value. This targets the green gradients and haze observed in the real run while preserving foreground pixels that are not connected to the outer background.

The cleanup remains deterministic and local. It does not call image generation and does not install another dependency. The existing RGB-distance removal remains part of the mask calculation for exact and near-exact key colors.

Small isolated foreground fragments may be removed only when they are clearly separate from the dominant character component and below a conservative size threshold. Character limbs, props, and accessories near or connected to the main component must remain untouched.

### Targeted QA repair

Visual QA continues to reject identity drift, wrong direction or action, cropping, overlap, and severe frame jitter. Background haze that the deterministic cleanup can remove does not trigger a new image-generation call.

When regeneration is required, only the affected row is regenerated. A completed unaffected row is never regenerated. The parent records the replacement, rebuilds once, and rechecks the result.

## Data flow

1. Prepare or resume the run and generate the base preview.
2. Wait for the user's single base approval.
3. Read the manifest and assign up to two explicit row jobs.
4. Record each completed row immediately and continue filling the two-worker queue.
5. Build the atlas with deterministic boundary-connected chroma cleanup.
6. Run deterministic validation and visual QA.
7. Regenerate only rows with genuine character or motion defects.
8. Install the validated package and clean the run directory.

## Failure and resume behavior

Each worker handles one image-generation attempt. The parent remains the only manifest writer, so two workers cannot claim or overwrite the same row. A failed transfer is recorded immediately and retried once as before. On a later task, the manifest remains the source of truth and only incomplete dependency-ready rows are scheduled.

If a worker or collaboration capability is unavailable, the parent falls back to sequential generation without asking the user to continue. If local cleanup cannot produce a valid nonempty frame, the build fails before installation and the existing targeted row repair path applies.

## Tests

The Skill contract tests will require explicit two-worker scheduling language, parent-only recording, continuous queue refill, and sequential fallback. They will also forbid asking the user for additional progress confirmations.

Build tests will cover:

- exact chroma-key removal;
- non-uniform edge-connected green background removal;
- preservation of foreground colors not connected to the frame boundary;
- conservative removal of tiny detached fragments;
- preservation of legitimate character components;
- successful construction of all 57 frames and the 1536×1872 atlas.

The complete repository test suite and Skill validator must pass. A real image-generation run is not required for every code edit because it is slow and consumes image-generation capacity; the already captured real-run artifacts provide the regression shape for local cleanup tests.

## Success criteria

- The user interaction remains one natural-language request plus one base approval.
- All nine official rows and 57 frames remain present.
- At most two row generations run concurrently.
- The parent is the only manifest writer.
- Ordinary green haze is removed locally rather than causing row regeneration.
- Deterministic validation and visual QA still gate installation.
- Expected first-pass duration is 10–15 minutes; limited targeted repair remains within 15–25 minutes under similar image-generation latency.

## Non-goals

- Generating several animation rows in one image.
- Using three or more concurrent workers.
- Mirroring `running-right` to create `running-left`.
- Removing official animation rows.
- Adding a standalone Windows desktop pet application.
- Adding third-party runtimes or packages for end users.

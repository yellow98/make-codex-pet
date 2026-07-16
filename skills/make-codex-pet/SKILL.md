---
name: make-codex-pet
description: Use when a user wants to turn one or more reference photos or a character image into a local animated Codex pet, including Q-style, pixel, sticker, custom likeness, sprite-sheet repair, or pet installation requests.
---

# Make Codex Pet

Turn attached references into the official nine-row Codex sprite atlas, validate it, and install it only as a local Codex pet. A request as short as “把这几张照片做成一个 Q 版桌宠” plus the images is sufficient.

## Resolve inputs, paths, and runtime

Do not send an intake form. Infer a short name and description, map Q/萌系/卡通 to `q-cartoon`, and use `auto` when no style is specified. Extract 3–5 strong visible identity features. Use the first or clearest image as primary; when clothing conflicts, follow it.

Ask exactly one focused question only when multiple people make the subject ambiguous, photos appear to show different people, severe blur/occlusion prevents an identity lock, or a user-required key element is unrecognizable. Otherwise proceed.

Set `<skill-dir>` to the absolute directory containing this `SKILL.md`. Define `<codex-home>` as `CODEX_HOME` when set, otherwise the `.codex` directory under the current user's home. Resolve it with current-host path rules to an absolute host-native path; it is not shell syntax.

Resolve `<python>` without installing dependencies. Only if the `load_workspace_dependencies` tool is available, call it and prefer its bundled Python. Otherwise probe only Python already available in the current Codex environment and use one for which `from PIL import Image` succeeds. Never ask the user to install Python, Node, ImageMagick, packages, or an API key. If none works, stop and suggest updating Codex.

All command blocks below are conceptual command templates, not cross-platform paste-ready shell. Replace placeholders, invoke the executable with the current host shell's correct syntax, and quote every concrete executable, script, and path argument. Parse script stdout JSON directly; never depend on `jq`.

Before creating a run, inspect manifests under `<codex-home>/pet-runs`. Resume the newest matching incomplete run; never overwrite or resume an unrelated run. For a new run:

```text
"<python>" "<skill-dir>/scripts/prepare_run.py" --pet-name "<name>" --description "<description>" --style <style> --identity-feature "<feature-1>" --identity-feature "<feature-2>" --identity-feature "<feature-3>" --reference "<reference-1>" --reference "<reference-2>"
```

Repeat `--identity-feature` for all 3–5 features and `--reference` for every reference; omit the second reference when only one exists. Take stdout JSON `run_dir` as `<run-dir>`.

## Approve the base before animation

Before the first imagegen call, read [privacy-and-rights.md](references/privacy-and-rights.md) and give its one-time data-flow notice. Before every base generation or edit, also tell the user: “角色预览会作为本轮最后输出；出现后请回复‘可以’或修改意见”。 Say both notices before calling imagegen; neither requires a confirmation response before generation.

**REQUIRED SUB-SKILL:** Use the installed `imagegen` Skill for every visual generation or edit. Do not call an image API directly and do not request an API key.

Call imagegen for the base and obtain the selected PNG, attaching all original references and using `<run-dir>/prompts/base.md`. Do not add user-visible text or questions after imagegen. Immediately after imagegen returns, silently record the selected PNG in the same orchestration turn:

```text
"<python>" "<skill-dir>/scripts/record_job.py" complete --run-dir "<run-dir>" --job-id base --source "<generated-png>" --remove-source
```

The generated preview remains the last user-visible output of the turn; necessary local tool calls may continue silently. Then wait for the user's confirmation or modification message. For a replacement preview, imagegen-edit it and immediately record it silently with:

```text
"<python>" "<skill-dir>/scripts/record_job.py" complete --run-dir "<run-dir>" --job-id base --source "<generated-png>" --force --remove-source
```

**BASE CONFIRMATION GATE (exactly once):** Because recording already finished silently, treat the user's `可以` as approval. If the user requests changes, use all original references plus the recorded base for an imagegen edit, repeat the pre-call preview notice, silently record the replacement with `--force` immediately after imagegen, and wait again. Never generate rows until the user approves the base. No row or final-package confirmation gate is allowed.

## Generate or resume the nine rows

After base approval, read [animation-rows.md](references/animation-rows.md) and [prompt-contract.md](references/prompt-contract.md) completely. Generate only the official jobs; generate `running-left` independently rather than mirroring `running-right`. Attach every original reference and the approved `<run-dir>/decoded/base.png` to every row.

Before a job, reload `imagegen_jobs.json`. Process it only when it is not `complete` and every dependency is `complete`. For each eligible row, call imagegen and then immediately and silently record its selected PNG before starting the next row. Do not add user-visible text or questions after imagegen. Continue automatically through eligible rows; never wait for the user to send “继续”.

```text
"<python>" "<skill-dir>/scripts/record_job.py" complete --run-dir "<run-dir>" --job-id <job-id> --source "<generated-png>" --remove-source
"<python>" "<skill-dir>/scripts/record_job.py" fail --run-dir "<run-dir>" --job-id <job-id> --message "<error>"
```

When collaboration worker tools are callable, this Skill explicitly permits isolated row workers. Keep exactly two worker slots full while at least two dependency-ready rows remain. The parent assigns one explicit job ID, the original references, the approved base, and that row's prompt to each worker. A worker generates that row only and returns the selected image; it never edits the manifest and never builds, validates, or installs. Imagegen is the worker's final user-visible output. The parent is the only manifest writer: the parent flow immediately records the worker's selected PNG, must record each returned image immediately, reload the manifest, and refill the open slot. When only one row remains, use one worker. If collaboration workers are unavailable, generate sequentially without asking the user to continue.

Immediately record an image-transfer failure, retry the same row once, and immediately record a second failure if it fails again. Every generation attempt reaches `complete` or `fail` in the manifest immediately, minimizing the interruption window. Then stop and report the row and error; never ask for animation names. On resume, trust the manifest: never regenerate completed jobs and run only dependency-ready incomplete jobs. If base approval is uncertain across sessions, show the existing recorded base once, do not regenerate it, and wait only for `可以` or modification instructions.

## Build, inspect, install, and clean

After all jobs are complete, build:

```text
"<python>" "<skill-dir>/scripts/build_pet.py" --run-dir "<run-dir>"
```

Then run deterministic validation:

```text
"<python>" "<skill-dir>/scripts/validate_pet.py" "<run-dir>/final/spritesheet.png"
```

Perform visual QA on the contact sheet and all nine GIFs. Identity drift, wrong action/direction, cropping, or frame-to-frame jumping fails QA even when validation passes. Regenerate only the affected row and immediately record it silently with `--force`, then rebuild and validate again:

```text
"<python>" "<skill-dir>/scripts/record_job.py" complete --run-dir "<run-dir>" --job-id <job-id> --source "<generated-png>" --force --remove-source
```

Install only after deterministic validation and visual QA both pass, then clean the run:

```text
"<python>" "<skill-dir>/scripts/install_pet.py" "<run-dir>"
"<python>" "<skill-dir>/scripts/cleanup_run.py" "<run-dir>"
```

If cleanup exits 2 or returns `status: "pending"`, retry once. If still pending, report JSON `cleanup_pending_path` exactly.

The installed package directory is `<codex-home>/pets/<id>` and must contain exactly `pet.json` and `spritesheet.png`. Tell the user to enter `/pet` in Codex. Never create a standalone desktop app, installer, shortcut, startup item, alternate package location, or community upload.

# Skill baseline

Baseline runs were recorded before replacing the generated placeholder Skill. The full agent responses are intentionally omitted; this note keeps only the observed failure modes that the Skill must prevent.

## Scenario 1: one-line Q-style request with photos

Prompt shape: the user attaches several reference photos and asks, “把这几张照片做成一个 Q 版桌宠。”

Observed failures:

- Asked a long intake questionnaire about authorization, style, retained features, target platform, animation names, size, and transparent background instead of inferring defaults.
- Invented `blink`, `working`, and `sleep` actions and arbitrary canvas sizes.
- Designed a standalone WPF desktop pet installed under `%LOCALAPPDATA%`, with shortcut/startup implications, rather than a Codex Pets package.
- Did not know the official 1536×1872 atlas, 8×9 grid, 192×208 cells, or fixed nine-row state order.

## Scenario 2: interrupted run resumed in a later session

Prompt shape: generation stopped after some artifacts were already complete, then the user asked to continue.

Observed failures:

- Did not inspect the existing manifest before planning work.
- Asked the user to name the remaining animations even though the official row set is fixed.
- Proposed regenerating the base or already completed actions instead of resuming only dependency-ready, incomplete jobs.
- Lacked the one-row retry, failure-recording, targeted QA repair, deterministic validation, and cleanup-pending behavior required for a safe resume.

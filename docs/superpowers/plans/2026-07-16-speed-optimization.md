# Make Codex Pet Speed Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut normal pet generation to 10–15 minutes by generating animation rows two at a time and by cleaning ordinary non-uniform chroma backgrounds locally instead of regenerating them.

**Architecture:** Keep image generation orchestration in `SKILL.md`: the parent assigns explicit jobs to two isolated workers and remains the only manifest writer. Extend `build_pet.py` with boundary-connected chroma cleanup and conservative tiny-component removal, then feed the cleaned frames into the existing normalization, atlas, QA, validation, and installation pipeline.

**Tech Stack:** Codex Skill Markdown, Python 3, Pillow, `unittest`.

---

### Task 1: Specify two-worker orchestration

**Files:**
- Modify: `skills/make-codex-pet/tests/test_skill_contract.py`
- Modify: `skills/make-codex-pet/SKILL.md`

- [ ] **Step 1: Write the failing Skill contract test**

Add a test that requires the Skill to state all of the following: collaboration tools are explicit permission for isolated workers; the parent assigns one explicit job ID per worker; exactly two worker slots are used when at least two jobs remain; the parent is the only manifest writer; completed workers are recorded immediately and the queue is refilled; sequential fallback requires no user wakeup.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_skill_contract.py -v
```

Expected: FAIL because the current Skill only says workers may run when the environment explicitly permits them and does not define a continuously refilled two-slot queue or parent-only manifest ownership.

- [ ] **Step 3: Write the minimal Skill instructions**

Replace the optional concurrency paragraph with imperative instructions equivalent to:

```markdown
When collaboration worker tools are callable, this Skill explicitly permits isolated row workers. Keep exactly two worker slots full while at least two dependency-ready rows remain. The parent assigns one explicit job ID and its inputs to each worker. A worker generates that row only and returns the selected image; it never edits the manifest, builds, validates, or installs. The parent is the only manifest writer: record each returned image immediately, reload the manifest, and refill the open slot. If workers are unavailable, generate sequentially without asking the user to continue.
```

- [ ] **Step 4: Run the focused contract test and verify GREEN**

Run the command from Step 2. Expected: PASS.

### Task 2: Remove non-uniform edge-connected chroma backgrounds

**Files:**
- Modify: `skills/make-codex-pet/tests/test_build_pet.py`
- Modify: `skills/make-codex-pet/scripts/build_pet.py`

- [ ] **Step 1: Write failing background-cleanup tests**

Add one test that creates a pale-green gradient connected to every frame edge with a centered gray subject and asserts that all background alpha becomes zero while the subject stays opaque. Add a second test with a green foreground patch isolated from the boundary and assert that it remains opaque.

- [ ] **Step 2: Run the two focused tests and verify RED**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_build_pet.py -v
```

Expected: at least the gradient test FAILS because RGB distance 36 plus feather 24 does not remove pale green.

- [ ] **Step 3: Implement minimal boundary-connected cleanup**

In `remove_chroma`, classify a pixel as a background candidate when it is transparent, within the existing key tolerance/feather range, or has the same dominant channel as the chroma key with a conservative dominance margin. Flood-fill candidates from the four frame edges using eight-neighbor connectivity. Set only connected candidates transparent; preserve isolated foreground candidates. Keep the function signature and input immutability unchanged.

- [ ] **Step 4: Run the focused tests and existing chroma tests**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_build_pet.py -v
```

Expected: PASS.

### Task 3: Remove only clearly tiny detached fragments

**Files:**
- Modify: `skills/make-codex-pet/tests/test_build_pet.py`
- Modify: `skills/make-codex-pet/scripts/build_pet.py`

- [ ] **Step 1: Write failing component-cleanup tests**

Add a test with one large character component and a remote two-pixel fragment; the fragment must become transparent. Add another test with a large character and a detached accessory larger than the conservative cutoff; both must remain.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_build_pet.py -v
```

Expected: FAIL because `remove_tiny_components` does not exist.

- [ ] **Step 3: Implement conservative component cleanup**

Add `remove_tiny_components(image, max_pixels=16)` using eight-neighbor alpha connectivity. Preserve the largest component and every component above `max_pixels`; make only smaller components transparent. Call it after `remove_chroma` and before the empty-frame check in `build_pet`.

- [ ] **Step 4: Run focused and atlas build tests**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_build_pet.py -v
```

Expected: PASS.

### Task 4: Validate the Skill and repository

**Files:**
- Modify only if validation identifies a contract mismatch.

- [ ] **Step 1: Run the complete test suite**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -v
```

Expected: all tests PASS with no failures or errors.

- [ ] **Step 2: Run the official Skill validator**

Run:

```powershell
python C:/Users/Administrator/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/make-codex-pet
```

Expected: validation succeeds.

- [ ] **Step 3: Inspect the final diff**

Run:

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only the planned Skill, build, test, and plan files differ.

### Task 5: Deploy the optimized Skill

**Files:**
- Update local installation: `C:/Users/Administrator/.codex/skills/make-codex-pet`

- [ ] **Step 1: Commit the implementation**

Stage the planned files and commit with:

```powershell
git commit -m "perf: speed up pet generation"
```

- [ ] **Step 2: Push the branch**

Push `codex/speed-up-pet-generation` to `origin` and verify the remote commit.

- [ ] **Step 3: Refresh the local Skill installation**

Use the official Skill installer or replace the installed Skill from the verified repository checkout, then run the Skill validator against `C:/Users/Administrator/.codex/skills/make-codex-pet`.

- [ ] **Step 4: Report measured scope honestly**

Report the deterministic test evidence and the expected 10–15/15–25 minute targets. Do not claim a measured real-run time unless another full image-generation run is actually performed.

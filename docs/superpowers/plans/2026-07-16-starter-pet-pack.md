# Starter Pet Pack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship four ready-to-use classic Q-style pets with a single cross-platform natural-language installation flow.

**Architecture:** Store each finished pet as an immutable two-file package under `assets/starter-pets`. A new standard-library Python installer validates the declared packages, publishes them under the resolved Codex Pets directory, and leaves unrelated pets untouched. The Skill routes starter-pack requests to this installer and keeps photo generation unchanged.

**Tech Stack:** Codex Skill Markdown, Python 3 standard library, Pillow already bundled by Codex, `unittest`, imagegen for authored assets.

---

### Task 1: Cross-platform starter package installer

**Files:**
- Create: `skills/make-codex-pet/tests/test_install_starter_pets.py`
- Create: `skills/make-codex-pet/scripts/install_starter_pets.py`

- [ ] **Step 1: Write failing installer tests**

Create temporary starter packages with valid 1536×1872 atlases. Require exactly four declared IDs, install into an explicit temporary Codex home, preserve an unrelated pet, report a second run as unchanged, and replace one changed declared package. Require compact JSON stdout on CLI success and stderr-only errors.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_install_starter_pets.py -v
```

Expected: FAIL because `install_starter_pets.py` does not exist.

- [ ] **Step 3: Implement the minimal installer**

Implement these stable entry points:

```python
STARTER_PET_IDS = (
    "classic-messi",
    "classic-ronaldo",
    "classic-elon-musk",
    "classic-sam-altman",
)

def install_starter_pets(
    codex_home: Path | None = None,
    assets_root: Path | None = None,
) -> dict[str, object]:
    ...
```

Resolve `CODEX_HOME` through `pet_common.resolve_codex_home`. Validate package contents, pet schema, safe ID, and atlas with `validate_pet.validate_atlas(..., require_build_state=False)`. Stage each package under the Pets directory, replace only its declared ID, and return `ok`, `installed`, `unchanged`, and `packages`. Use only cross-platform Python APIs.

- [ ] **Step 4: Verify GREEN**

Run the command from Step 2. Expected: PASS.

### Task 2: Skill routing and repository documentation

**Files:**
- Modify: `skills/make-codex-pet/tests/test_skill_contract.py`
- Modify: `skills/make-codex-pet/tests/test_repository_docs.py`
- Modify: `skills/make-codex-pet/SKILL.md`
- Modify: `README.md`
- Modify: `ACCEPTABLE_USE.md`

- [ ] **Step 1: Write failing contract tests**

Require the trigger description to include bundled/starter/classic pet installation. Require the Skill to run `install_starter_pets.py` without imagegen for that request and to tell the user **Settings > Pets > Refresh** followed by `/pet`. Require README's natural-language command and the no-endorsement/publicity-rights notice.

- [ ] **Step 2: Verify RED**

Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p "test_*docs.py" -v
python -m unittest discover -s skills/make-codex-pet/tests -p test_skill_contract.py -v
```

Expected: FAIL because the starter-pack branch and notices are absent.

- [ ] **Step 3: Add minimal routing and documentation**

Add a short starter-pack branch before the generation workflow. Keep existing commands and generation gates unchanged. Document the one-sentence user flow and state that the Apache code license grants no publicity, personality, trademark, sponsorship, endorsement, or commercial-use rights in depicted people or brands.

- [ ] **Step 4: Verify GREEN**

Run the commands from Step 2. Expected: PASS.

### Task 3: Generate and approve four base designs

**Files:**
- Temporary local run directories only.

- [ ] **Step 1: Read the imagegen Skill and prepare four pet runs**

Create independent Q-cartoon run manifests for Messi, Ronaldo, Elon Musk, and Sam Altman using the recognizable directions in the approved design. Use no official logos and do not commit reference photographs.

- [ ] **Step 2: Generate four base previews**

Use imagegen once per person, record each base immediately, assemble a labeled four-up preview, and present it as the final visual output.

- [ ] **Step 3: Pause for one approval**

Treat `可以` as approval for all four bases. If the user requests changes, regenerate only named previews and show the updated four-up preview.

### Task 4: Build the four complete starter pets

**Files:**
- Create: `skills/make-codex-pet/assets/starter-pets/classic-messi/pet.json`
- Create: `skills/make-codex-pet/assets/starter-pets/classic-messi/spritesheet.png`
- Create: equivalent two-file packages for the other three declared IDs.
- Create: `skills/make-codex-pet/tests/test_starter_pet_assets.py`

- [ ] **Step 1: Generate official rows**

For each approved pet, generate all nine official rows using the two-worker queue, parent-only recording, and targeted repair rules from `make-codex-pet`.

- [ ] **Step 2: Build and validate every pet**

Run build, deterministic validation, and visual QA for each. Install only validated results locally.

- [ ] **Step 3: Copy final two-file packages into assets**

Copy only `pet.json` and `spritesheet.png`, using the four declared IDs and descriptions. Do not commit run manifests, references, prompts, previews, or downloaded source images.

- [ ] **Step 4: Write and run asset contract tests**

Require exact package contents, safe unique IDs, expected display names, valid atlas geometry, occupied official cells, transparent unused cells, and no EXIF. Run:

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -p test_starter_pet_assets.py -v
```

Expected: PASS.

### Task 5: Verify, review, deploy

**Files:**
- Modify only if verification exposes a planned contract mismatch.

- [ ] **Step 1: Run complete verification**

```powershell
python -m unittest discover -s skills/make-codex-pet/tests -v
$env:PYTHONUTF8='1'
python C:/Users/Administrator/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/make-codex-pet
git diff --check
```

Expected: all tests and Skill validation PASS with no whitespace errors.

- [ ] **Step 2: Request independent code and asset review**

Review installer behavior, platform-neutral paths, package validity, visual identity, action semantics, and rights documentation. Fix every Critical or Important issue through a failing regression test.

- [ ] **Step 3: Commit, merge, and push after approval**

Commit implementation on `codex/starter-pet-pack`, merge to `main`, rerun the full suite, push `main`, and refresh the locally installed Skill through the verified installer workflow.

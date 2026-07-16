# Starter Pet Pack Design

## Goal

Bundle four ready-to-use Q-style Codex pets inspired by Lionel Messi, Cristiano Ronaldo, Elon Musk, and Sam Altman with `make-codex-pet`, then let a user install all four locally with one natural-language request after installing the Skill.

## Product boundary

Installing a Codex Skill only places files under `<codex-home>/skills`; it does not execute bundled code or register pets. Therefore the repository cannot make starter pets appear immediately when the GitHub Skill download finishes.

The supported user flow is:

1. Install `make-codex-pet` from GitHub normally.
2. Open a Codex task and say “安装这个 Skill 自带的经典宠物”.
3. The Skill runs its bundled installer with Codex's existing Python runtime.
4. The user opens **Settings > Pets**, selects **Refresh**, and chooses a starter pet.
5. The user enters `/pet` to wake the selected pet.

No separate Python, Node, ImageMagick, API key, shell knowledge, or manual file copy is required.

## Cross-platform implementation rule

All runtime code shipped by this Skill must be platform-neutral. Prefer Codex's bundled Python and the Python standard library, including `pathlib`, `json`, `hashlib`, `shutil`, `tempfile`, and `os`, with host-native paths resolved at runtime.

Do not ship separate `.bat`, PowerShell, Bash, WPF, AppleScript, or platform-specific installer implementations. Do not hard-code Windows drive letters, `%LOCALAPPDATA%`, macOS home paths, or Linux home paths. Resolve `CODEX_HOME` when set and otherwise use the current user's `.codex` directory.

The same installer code and bundled `pet.json`/`spritesheet.png` packages must work on supported Windows, macOS, and Linux Codex surfaces that provide Pets and the bundled Python/Pillow runtime. Platform/version availability of Codex Pets remains a product prerequisite rather than something this repository installs.

## Bundled pets

The first pack contains exactly four pets:

| Package ID | Display name | Recognizable design direction |
|---|---|---|
| `classic-messi` | 梅西 Q版 | Short dark hair, trimmed beard, compact athletic build, sky-blue-and-white football palette, number 10 without team crests. |
| `classic-ronaldo` | C罗 Q版 | Styled dark hair, clean athletic silhouette, red-and-green football palette, number 7 without team crests. |
| `classic-elon-musk` | 马斯克 Q版 | Short swept hair, dark minimalist jacket or T-shirt, technology and space-exploration character cues without company logos. |
| `classic-sam-altman` | 山姆·奥特曼 Q版 | Short dark hair, understated gray hoodie or casual jacket, calm technology-founder character cues without company logos. |

Each package contains exactly `pet.json` and `spritesheet.png`. Each sprite sheet uses the existing official 1536×1872, 8×9, nine-state Codex atlas contract. The four pets differ in identity and styling while retaining the same official action semantics.

## Asset authoring and approval

Generate one Q-style base preview for each person. Present the four labeled previews together and accept one user reply of `可以` as approval for all four; requested changes identify the affected person and regenerate only that preview.

After approval, generate the nine official animation rows for each pet using the optimized two-worker queue. Build, validate, and visually inspect every atlas. Commit only the final local pet packages and required attribution/rights notes; do not commit downloaded reference photographs, temporary prompts, run manifests, or service outputs outside the final packages.

Avoid official club, national-team, corporate, or product logos. Names and likenesses remain recognizable, but the assets must not imply endorsement or official affiliation.

## Repository layout

```text
skills/make-codex-pet/
├── assets/
│   └── starter-pets/
│       ├── classic-messi/
│       │   ├── pet.json
│       │   └── spritesheet.png
│       ├── classic-ronaldo/
│       ├── classic-elon-musk/
│       └── classic-sam-altman/
├── scripts/
│   └── install_starter_pets.py
└── tests/
    └── test_install_starter_pets.py
```

## Installer behavior

`install_starter_pets.py` resolves `<codex-home>` using the same rule as the rest of the Skill. It discovers only the four declared starter package IDs, validates each `pet.json` schema and atlas contract, and then publishes each package under `<codex-home>/pets/<id>`.

The installer is repeatable. If the installed package already has the same atlas hash and metadata, report it as unchanged. If the same starter ID contains an older bundled version, replace that package through a staging directory. Never modify unrelated pets.

Print one compact JSON result containing `ok`, `installed`, `unchanged`, and absolute package paths. On failure, write the error to stderr and leave already unrelated pets untouched.

## Skill behavior

Extend the Skill trigger description to cover requests to install bundled, starter, classic, or example pets. Add a short branch before the photo-to-pet workflow:

- When the user asks to install the starter pack, resolve the existing runtime, run `install_starter_pets.py`, and do not invoke image generation.
- Tell the user to open **Settings > Pets**, select **Refresh**, choose a pet, and then enter `/pet`.
- For all ordinary reference-photo requests, keep the current generation workflow unchanged.

## Documentation and rights

README installation and usage sections document the one-sentence starter-pack command and the Settings refresh step. `ACCEPTABLE_USE.md` states that the portraits are unofficial stylized fan-art, no endorsement is claimed, and the Apache-2.0 code license does not grant publicity, personality, trademark, sponsorship, or commercial-use rights in a depicted person or brand.

These notices are informational and do not add a blocking confirmation gate to installation.

## Tests

Automated tests cover:

- all four asset directories and exact package contents;
- unique safe IDs and valid `pet.json` schemas;
- RGBA 1536×1872 atlases with all required rows occupied and unused cells transparent;
- installing all four packages into a temporary Codex home;
- a second installation reporting all four as unchanged;
- replacement of only an outdated starter package;
- preservation of unrelated pets;
- JSON-only CLI success and stderr-only failure;
- Skill trigger and natural-language installation instructions;
- README and acceptable-use notices.

## Success criteria

- A newly installed Skill can install all four bundled pets after one natural-language request.
- No image-generation call occurs during starter-pack installation.
- The user installs no additional runtime and performs no manual file copy.
- One Python implementation handles supported Windows, macOS, and Linux paths without shell-specific scripts.
- All four pets appear after **Settings > Pets > Refresh**.
- Existing custom or built-in pets remain unchanged.
- The existing photo-to-pet workflow and 148-test baseline remain intact.

## Non-goals

- Executing code automatically at GitHub Skill download time.
- Shipping an independent Windows installer or desktop application.
- Adding arbitrary extra animation states beyond the official nine rows.
- Claiming endorsement by any depicted person, team, company, or organization.
- Bundling source photographs or brand logos.

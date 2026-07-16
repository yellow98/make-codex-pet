from __future__ import annotations

from pathlib import Path
import re
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILL = SKILL_DIR / "SKILL.md"
REFERENCES = SKILL_DIR / "references"
REFERENCE_NAMES = (
    "animation-rows.md",
    "prompt-contract.md",
    "privacy-and-rights.md",
)
EXPECTED_ROWS = (
    (0, "idle", 6),
    (1, "running-right", 8),
    (2, "running-left", 8),
    (3, "waving", 4),
    (4, "jumping", 5),
    (5, "failed", 8),
    (6, "waiting", 6),
    (7, "running", 6),
    (8, "review", 6),
)


class SkillContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.skill = SKILL.read_text(encoding="utf-8")
        cls.reference_text: dict[str, str] = {}
        for name in REFERENCE_NAMES:
            path = REFERENCES / name
            if path.is_file():
                cls.reference_text[name] = path.read_text(encoding="utf-8")

    def test_frontmatter_is_exact(self) -> None:
        expected_frontmatter = """---
name: make-codex-pet
description: Use when a user wants to turn one or more reference photos or a character image into a local animated Codex pet, or install the bundled starter/classic pets, including Q-style, pixel, sticker, custom likeness, sprite-sheet repair, or pet installation requests.
---"""
        self.assertTrue(self.skill.startswith(expected_frontmatter))

    def test_bundled_starter_install_has_a_short_non_imagegen_route(self) -> None:
        starter_position = self.skill.find("## Install the bundled starter pets")
        generation_position = self.skill.find("## Resolve inputs, paths, and runtime")
        self.assertGreaterEqual(starter_position, 0)
        self.assertLess(starter_position, generation_position)
        for marker in (
            "安装这个 Skill 自带的经典宠物",
            '"<skill-dir>/scripts/install_starter_pets.py"',
            "Do not call imagegen",
            "Settings > Pets > Refresh",
            "`/pet`",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.skill)

    def test_all_references_exist_and_skill_names_each_one(self) -> None:
        for name in REFERENCE_NAMES:
            with self.subTest(name=name):
                self.assertTrue((REFERENCES / name).is_file())
                self.assertIn(f"(references/{name})", self.skill)

    def test_animation_table_has_only_the_official_order_and_frame_counts(self) -> None:
        animation = self.reference_text["animation-rows.md"]
        parsed = tuple(
            (int(row), state, int(frames))
            for row, state, frames in re.findall(
                r"^\|\s*(\d+)\s*\|\s*`([^`]+)`\s*\|\s*(\d+)\s*\|",
                animation,
                flags=re.MULTILINE,
            )
        )
        self.assertEqual(parsed, EXPECTED_ROWS)
        for marker in ("1536×1872", "8×9", "192×208", "20 MiB", "fully transparent"):
            with self.subTest(marker=marker):
                self.assertIn(marker, animation)

    def test_base_turn_contract_and_pipeline_are_in_required_order(self) -> None:
        ordered_markers = (
            "角色预览会作为本轮最后输出",
            "Call imagegen for the base and obtain the selected PNG",
            "Immediately after imagegen returns, silently record the selected PNG in the same orchestration turn",
            "**BASE CONFIRMATION GATE (exactly once):**",
            "## Generate or resume the nine rows",
            "For each eligible row, call imagegen and then immediately and silently record its selected PNG",
            '"<skill-dir>/scripts/build_pet.py"',
            '"<skill-dir>/scripts/validate_pet.py"',
            "Perform visual QA",
            '"<skill-dir>/scripts/install_pet.py"',
            '"<skill-dir>/scripts/cleanup_run.py"',
        )
        positions = []
        for marker in ordered_markers:
            with self.subTest(marker=marker):
                position = self.skill.find(marker)
                self.assertGreaterEqual(position, 0)
                positions.append(position)
        self.assertEqual(positions, sorted(positions))

        self.assertIn(
            "The generated preview remains the last user-visible output of the turn; necessary local tool calls may continue silently.",
            self.skill,
        )
        self.assertIn(
            "Do not add user-visible text or questions after imagegen.",
            self.skill,
        )
        self.assertIn("Continue automatically through eligible rows", self.skill)
        self.assertIn("the parent flow immediately records the worker's selected PNG", self.skill)
        self.assertIn(
            "Every generation attempt reaches `complete` or `fail` in the manifest immediately",
            self.skill,
        )

        lowered = self.skill.lower()
        for forbidden in (
            "at the start of the next turn",
            "on the next turn",
            "at the next turn's start",
            "at the start of its following turn",
            "sequentially across turns",
            "wait for the user to continue",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, lowered)

    def test_only_base_has_a_confirmation_gate_and_resume_does_not_regenerate_it(self) -> None:
        self.assertEqual(self.skill.count("BASE CONFIRMATION GATE"), 1)
        self.assertIn(
            "If base approval is uncertain across sessions, show the existing recorded base once, "
            "do not regenerate it, and wait only for `可以` or modification instructions.",
            self.skill,
        )
        self.assertIn("No row or final-package confirmation gate is allowed.", self.skill)

    def test_two_worker_queue_is_explicit_and_parent_owned(self) -> None:
        for marker in (
            "this Skill explicitly permits isolated row workers",
            "Keep exactly two worker slots full",
            "one explicit job ID",
            "The parent is the only manifest writer",
            "record each returned image immediately",
            "refill the open slot",
            "generate sequentially without asking the user to continue",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.skill)

        self.assertIn("never edits the manifest", self.skill)
        self.assertIn("never builds, validates, or installs", self.skill)

    def test_host_paths_runtime_and_command_quoting_are_explicit(self) -> None:
        self.assertIn("Define `<codex-home>`", self.skill)
        self.assertIn("`<codex-home>/pet-runs`", self.skill)
        self.assertIn("`<codex-home>/pets/<id>`", self.skill)
        self.assertIn(
            "Only if the `load_workspace_dependencies` tool is available, call it",
            self.skill,
        )
        self.assertIn("from PIL import Image", self.skill)
        self.assertIn("conceptual command templates", self.skill)
        self.assertIn("current host shell", self.skill)

        command_lines = [line for line in self.skill.splitlines() if "/scripts/" in line]
        self.assertGreaterEqual(len(command_lines), 8)
        for line in command_lines:
            with self.subTest(line=line):
                self.assertIn('"<python>"', line)
                self.assertRegex(line, r'"<skill-dir>/scripts/[a-z_]+\.py"')
                if "--run-dir" in line:
                    self.assertIn('--run-dir "<run-dir>"', line)
                if "--source" in line:
                    self.assertIn('--source "<generated-png>"', line)
        self.assertIn('"<skill-dir>/scripts/validate_pet.py" "<run-dir>/final/spritesheet.png"', self.skill)
        self.assertIn('"<skill-dir>/scripts/install_pet.py" "<run-dir>"', self.skill)
        self.assertIn('"<skill-dir>/scripts/cleanup_run.py" "<run-dir>"', self.skill)

    def test_privacy_warns_about_server_records_without_a_confirmation_gate(self) -> None:
        privacy = self.reference_text["privacy-and-rights.md"]
        self.assertIn("sent to OpenAI", privacy)
        self.assertIn("not uploaded to the community", privacy)
        self.assertIn("Codex task server-side records", privacy)
        self.assertIn("deleting local files cannot retroactively delete", privacy)
        self.assertIn("Do not ask the user to confirm", privacy)
        self.assertIn("public figure", privacy)

    def test_prompt_contract_locks_inputs_background_and_one_job(self) -> None:
        prompt = self.reference_text["prompt-contract.md"]
        for marker in (
            "original reference",
            "approved `decoded/base.png`",
            "solid chroma-key background",
            "identity lock",
            "one job",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, prompt)

    def test_operational_docs_do_not_contain_forbidden_implementations_or_states(self) -> None:
        operational = "\n".join((self.skill, *self.reference_text.values()))
        self.assertNotIn("${CODEX_HOME:-~/.codex}", operational)
        self.assertNotIn("api.openai.com", operational.lower())
        self.assertNotRegex(operational.lower(), r"\b(?:pip|npm)\s+install\b")
        self.assertNotIn("localappdata", operational.lower())
        self.assertNotIn("wpf", operational.lower())
        self.assertNotRegex(operational.lower(), r"`(?:blink|working|sleep)`")


if __name__ == "__main__":
    unittest.main()

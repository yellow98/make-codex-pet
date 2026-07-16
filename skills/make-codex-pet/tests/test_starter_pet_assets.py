from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
ASSETS_DIR = SKILL_DIR / "assets" / "starter-pets"
EXPECTED_PETS = {
    "classic-argentina-10": (
        "阿根廷10号",
        "蓝白球衣、10号、短深色头发和短胡须的原创 Q 版球星。",
    ),
    "classic-portugal-7": (
        "葡萄牙7号",
        "红绿球衣、7号、上梳深色头发的原创 Q 版球星。",
    ),
    "classic-norway-9": (
        "挪威9号",
        "红蓝球衣、9号、浅金色长发的原创 Q 版中锋。",
    ),
    "classic-france-10": (
        "法国10号",
        "蓝色球衣、10号、短黑发的原创 Q 版前锋。",
    ),
}


def load_validate():
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location(
        "validate_starter_pet_assets", SCRIPTS_DIR / "validate_pet.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class StarterPetAssetsTests(unittest.TestCase):
    def test_bundles_four_complete_valid_packages(self):
        validate = load_validate()

        self.assertEqual(
            {entry.name for entry in ASSETS_DIR.iterdir() if entry.is_dir()},
            set(EXPECTED_PETS),
        )
        for pet_id, (display_name, description) in EXPECTED_PETS.items():
            package = ASSETS_DIR / pet_id
            self.assertEqual(
                {entry.name for entry in package.iterdir()},
                {"pet.json", "spritesheet.png"},
            )
            self.assertEqual(
                json.loads((package / "pet.json").read_text(encoding="utf-8")),
                {
                    "id": pet_id,
                    "displayName": display_name,
                    "description": description,
                    "spritesheetPath": "spritesheet.png",
                },
            )

            report = validate.validate_atlas(
                package / "spritesheet.png", require_build_state=False
            )
            self.assertTrue(report["ok"], report["errors"])
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["warnings"], [])
            self.assertEqual(report["dimensions"], [1536, 1872])
            self.assertEqual(report["mode"], "RGBA")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
INSTALLER = SCRIPTS_DIR / "install_starter_pets.py"
PET_IDS = (
    "classic-messi",
    "classic-ronaldo",
    "classic-elon-musk",
    "classic-sam-altman",
)
ANIMATION_ROWS = (6, 8, 8, 4, 5, 8, 6, 6, 6)


def load_installer():
    if not INSTALLER.is_file():
        raise AssertionError(f"missing implementation: {INSTALLER}")
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("install_starter_pets", INSTALLER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def valid_atlas_bytes() -> bytes:
    atlas = Image.new("RGBA", (1536, 1872), (0, 0, 0, 0))
    draw = ImageDraw.Draw(atlas)
    for row, frame_count in enumerate(ANIMATION_ROWS):
        for column in range(frame_count):
            left = column * 192 + 20
            top = row * 208 + 24
            draw.rectangle((left, top, left + 31, top + 39), fill=(40, 80, 120, 255))
    output = BytesIO()
    atlas.save(output, format="PNG")
    return output.getvalue()


class InstallStarterPetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.assets_root = self.root / "starter-pets"
        self.codex_home = self.root / "codex-home"
        for pet_id in PET_IDS:
            self.write_package(pet_id)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_package(self, pet_id: str, description: str | None = None) -> None:
        package = self.assets_root / pet_id
        package.mkdir(parents=True, exist_ok=True)
        (package / "pet.json").write_text(
            json.dumps(
                {
                    "id": pet_id,
                    "displayName": pet_id.removeprefix("classic-").title() + " Q版",
                    "description": description or f"Bundled {pet_id} pet.",
                    "spritesheetPath": "spritesheet.png",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (package / "spritesheet.png").write_bytes(valid_atlas_bytes())

    def test_installs_exact_declared_packages_and_preserves_unrelated_pet(self):
        unrelated = self.codex_home / "pets" / "my-own-pet"
        unrelated.mkdir(parents=True)
        (unrelated / "keep.txt").write_text("keep", encoding="utf-8")

        installer = load_installer()
        result = installer.install_starter_pets(self.codex_home, self.assets_root)

        self.assertEqual(installer.STARTER_PET_IDS, PET_IDS)
        self.assertTrue(result["ok"])
        self.assertEqual(result["installed"], list(PET_IDS))
        self.assertEqual(result["unchanged"], [])
        self.assertEqual(set(result["packages"]), set(PET_IDS))
        self.assertEqual((unrelated / "keep.txt").read_text(encoding="utf-8"), "keep")
        for pet_id in PET_IDS:
            package = self.codex_home / "pets" / pet_id
            self.assertEqual({item.name for item in package.iterdir()}, {"pet.json", "spritesheet.png"})
            self.assertEqual(json.loads((package / "pet.json").read_text(encoding="utf-8"))["id"], pet_id)

    def test_second_run_is_unchanged_and_changed_package_is_replaced(self):
        installer = load_installer()
        installer.install_starter_pets(self.codex_home, self.assets_root)

        second = installer.install_starter_pets(self.codex_home, self.assets_root)
        self.assertEqual(second["installed"], [])
        self.assertEqual(second["unchanged"], list(PET_IDS))

        self.write_package("classic-messi", "Updated bundled pet.")
        third = installer.install_starter_pets(self.codex_home, self.assets_root)
        self.assertEqual(third["installed"], ["classic-messi"])
        self.assertEqual(third["unchanged"], list(PET_IDS[1:]))
        installed = json.loads(
            (self.codex_home / "pets" / "classic-messi" / "pet.json").read_text(encoding="utf-8")
        )
        self.assertEqual(installed["description"], "Updated bundled pet.")

    def test_rejects_invalid_or_incomplete_asset_package(self):
        installer = load_installer()
        (self.assets_root / "classic-messi" / "extra.txt").write_text("extra", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "exactly"):
            installer.install_starter_pets(self.codex_home, self.assets_root)

        self.assertFalse((self.codex_home / "pets").exists())

    def test_cli_prints_compact_json_and_uses_stderr_for_errors(self):
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        success = subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--codex-home",
                str(self.codex_home),
                "--assets-root",
                str(self.assets_root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stderr, "")
        self.assertNotIn("\n ", success.stdout)
        self.assertTrue(json.loads(success.stdout)["ok"])

        failed = subprocess.run(
            [sys.executable, str(INSTALLER), "--assets-root", str(self.root / "missing")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error", failed.stderr.lower())


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

from datetime import datetime
from contextlib import redirect_stderr, redirect_stdout
from functools import lru_cache
import hashlib
from io import BytesIO, StringIO
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
INSTALL_SCRIPT = SCRIPTS_DIR / "install_pet.py"
CLEANUP_SCRIPT = SCRIPTS_DIR / "cleanup_run.py"

ATLAS_WIDTH = 1536
ATLAS_HEIGHT = 1872
CELL_WIDTH = 192
CELL_HEIGHT = 208
ANIMATION_ROWS = (
    ("idle", 6),
    ("running-right", 8),
    ("running-left", 8),
    ("waving", 4),
    ("jumping", 5),
    ("failed", 8),
    ("waiting", 6),
    ("running", 6),
    ("review", 6),
)


def load_script(name: str, path: Path):
    if not path.is_file():
        raise AssertionError(f"missing implementation: {path}")
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def valid_atlas_bytes() -> bytes:
    atlas = Image.new("RGBA", (ATLAS_WIDTH, ATLAS_HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(atlas)
    for row, (_, frame_count) in enumerate(ANIMATION_ROWS):
        for column in range(frame_count):
            left = column * CELL_WIDTH + 20
            top = row * CELL_HEIGHT + 24
            draw.rectangle((left, top, left + 31, top + 39), fill=(40, 80, 120, 255))
    output = BytesIO()
    atlas.save(output, format="PNG")
    return output.getvalue()


class PetRunFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.run_dir = self.root / "run"
        self.codex_home = self.root / "codex-home"
        self.reference = self.root / "private" / "reference face.png"
        self.reference.parent.mkdir()
        self.reference.write_bytes(b"private-reference")
        self.make_complete_run()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_complete_run(self) -> None:
        atlas_path = self.run_dir / "final" / "spritesheet.png"
        atlas_path.parent.mkdir(parents=True)
        atlas_path.write_bytes(valid_atlas_bytes())
        atlas_sha = hashlib.sha256(atlas_path.read_bytes()).hexdigest()
        (self.run_dir / "build_state.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "complete",
                    "build_id": "build-test",
                    "spritesheet": "final/spritesheet.png",
                    "spritesheet_sha256": atlas_sha,
                }
            ),
            encoding="utf-8",
        )
        request = {
            "schema_version": 1,
            "pet": {
                "id": "pixel-pal",
                "display_name": "Pixel Pal",
                "description": "A tiny test companion.",
                "style": "flat pixel art",
            },
            "identity_features": ["round glasses", "orange hoodie", "short hair"],
            "references": [str(self.reference.resolve())],
            "chroma_key": "#00FF00",
            "created_at": "2026-07-15T00:00:00Z",
        }
        (self.run_dir / "pet_request.json").write_text(
            json.dumps(request), encoding="utf-8"
        )
        qa = self.run_dir / "qa"
        (qa / "previews").mkdir(parents=True)
        (qa / "character-preview.png").write_bytes(b"character-preview")
        (qa / "contact-sheet.png").write_bytes(b"contact-sheet")
        for state, _ in ANIMATION_ROWS:
            (qa / "previews" / f"{state}.gif").write_bytes(state.encode("ascii"))

    def install(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        return install, install.install_pet(self.run_dir, self.codex_home)


class InstallPetTests(PetRunFixture):
    def test_refuses_install_when_validation_is_not_ok(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        state_path = self.run_dir / "build_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "building"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        with mock.patch.object(
            install.validate_pet,
            "validate_atlas",
            wraps=install.validate_pet.validate_atlas,
        ) as validate:
            with self.assertRaisesRegex(ValueError, "valid"):
                install.install_pet(self.run_dir, self.codex_home)

        validate.assert_called_once_with(
            self.run_dir / "final" / "spritesheet.png",
            require_build_state=True,
        )
        self.assertFalse((self.codex_home / "pets").exists())
        report = json.loads(
            (self.run_dir / "final" / "validation.json").read_text(encoding="utf-8")
        )
        self.assertFalse(report["ok"])

    def test_installs_pet_json_and_relative_spritesheet_path(self):
        _, summary = self.install()

        package = self.codex_home / "pets" / "pixel-pal"
        self.assertTrue(summary["ok"])
        self.assertEqual({path.name for path in package.iterdir()}, {"pet.json", "spritesheet.png"})
        self.assertEqual(
            json.loads((package / "pet.json").read_text(encoding="utf-8")),
            {
                "id": "pixel-pal",
                "displayName": "Pixel Pal",
                "description": "A tiny test companion.",
                "spritesheetPath": "spritesheet.png",
            },
        )
        self.assertEqual(
            hashlib.sha256((package / "spritesheet.png").read_bytes()).hexdigest(),
            hashlib.sha256((self.run_dir / "final" / "spritesheet.png").read_bytes()).hexdigest(),
        )

    def test_backs_up_existing_pet_before_replacement(self):
        old_package = self.codex_home / "pets" / "pixel-pal"
        old_package.mkdir(parents=True)
        (old_package / "pet.json").write_text('{"old":true}', encoding="utf-8")
        (old_package / "spritesheet.png").write_bytes(b"old-atlas")
        (old_package / "keep.txt").write_text("old-package", encoding="utf-8")

        _, summary = self.install()

        backup = Path(summary["backup"])
        self.assertEqual(backup.parent.resolve(), (self.codex_home / "pet-backups").resolve())
        self.assertTrue(backup.name.startswith("pixel-pal-"))
        self.assertEqual((backup / "keep.txt").read_text(encoding="utf-8"), "old-package")
        self.assertFalse((self.codex_home / "pets" / "pixel-pal" / "keep.txt").exists())

    def test_publish_second_step_failure_rolls_back_old_target(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        target = (self.codex_home / "pets" / "pixel-pal").resolve()
        target.mkdir(parents=True)
        old_pet = b'{"old":true}'
        old_atlas = b"old-atlas"
        (target / "pet.json").write_bytes(old_pet)
        (target / "spritesheet.png").write_bytes(old_atlas)
        real_replace = install.os.replace
        failed = False

        def fail_staging_publish(source, destination):
            nonlocal failed
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                not failed
                and destination_path == target
                and source_path.name.startswith(".install-staging-")
            ):
                failed = True
                raise OSError("simulated publish failure")
            return real_replace(source, destination)

        with mock.patch.object(install.os, "replace", side_effect=fail_staging_publish):
            with self.assertRaisesRegex(OSError, "publish failure"):
                install.install_pet(self.run_dir, self.codex_home)

        self.assertTrue(failed)
        self.assertEqual((target / "pet.json").read_bytes(), old_pet)
        self.assertEqual((target / "spritesheet.png").read_bytes(), old_atlas)
        pets = self.codex_home / "pets"
        self.assertEqual(list(pets.glob(".install-staging-*")), [])

    def test_rejects_target_and_pets_reparse_points_without_touching_external_data(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        for case in ("target", "pets"):
            with self.subTest(case=case):
                codex_home = self.root / f"codex-{case}"
                external = self.root / f"external-{case}"
                external.mkdir()
                marker = external / "marker.txt"
                marker.write_text("preserve", encoding="utf-8")
                if case == "target":
                    pets = codex_home / "pets"
                    pets.mkdir(parents=True)
                    link = pets / "pixel-pal"
                else:
                    codex_home.mkdir()
                    link = codex_home / "pets"
                try:
                    link.symlink_to(external, target_is_directory=True)
                except (OSError, NotImplementedError) as error:
                    self.skipTest(f"directory symlinks unavailable: {error}")

                with self.assertRaisesRegex(ValueError, "reparse|symbolic|link"):
                    install.install_pet(self.run_dir, codex_home)

                self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
                self.assertTrue(link.is_symlink())

    def test_writes_fresh_validation_report_and_install_summary_without_references(self):
        _, summary = self.install()

        report_path = self.run_dir / "final" / "validation.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        written_summary = json.loads(
            (self.run_dir / "qa" / "run-summary.json").read_text(encoding="utf-8")
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["sha256"], hashlib.sha256(valid_atlas_bytes()).hexdigest())
        self.assertEqual(summary, written_summary)
        self.assertEqual(
            set(summary),
            {
                "schema_version",
                "ok",
                "run_dir",
                "spritesheet",
                "validation",
                "contact_sheet",
                "previews",
                "package",
                "backup",
                "installed_at",
            },
        )
        datetime.fromisoformat(summary["installed_at"].replace("Z", "+00:00"))
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(str(self.reference.resolve()), serialized)
        self.assertNotIn("references", serialized.lower())

    def test_rejects_atlas_snapshot_when_sha_no_longer_matches_validation(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        atlas_path = self.run_dir / "final" / "spritesheet.png"
        real_snapshot = install.validate_pet._read_regular_snapshot
        atlas_reads = 0

        def change_second_atlas_snapshot(path, max_bytes, root):
            nonlocal atlas_reads
            result = real_snapshot(path, max_bytes, root)
            if Path(path) == atlas_path:
                atlas_reads += 1
                if atlas_reads == 2:
                    return result + b"changed-after-validation"
            return result

        with mock.patch.object(
            install.validate_pet,
            "_read_regular_snapshot",
            side_effect=change_second_atlas_snapshot,
        ):
            with self.assertRaisesRegex(ValueError, "SHA|sha"):
                install.install_pet(self.run_dir, self.codex_home)

        self.assertEqual(atlas_reads, 2)
        self.assertFalse((self.codex_home / "pets").exists())

    def test_rejects_unsafe_request_before_creating_pets(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        request_path = self.run_dir / "pet_request.json"
        original = json.loads(request_path.read_text(encoding="utf-8"))
        for unsafe_id in ("../escape", "-", "---"):
            with self.subTest(unsafe_id=unsafe_id):
                request = json.loads(json.dumps(original))
                request["pet"]["id"] = unsafe_id
                request_path.write_text(json.dumps(request), encoding="utf-8")

                with self.assertRaisesRegex(ValueError, "id"):
                    install.install_pet(self.run_dir, self.codex_home)

                self.assertFalse((self.codex_home / "pets").exists())
                self.assertFalse((self.root / "escape").exists())

    def test_explicit_codex_home_wins_over_environment(self):
        install = load_script("install_pet", INSTALL_SCRIPT)
        environment_home = self.root / "environment-home"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(environment_home)}):
            summary = install.install_pet(self.run_dir, self.codex_home)

        self.assertEqual(
            Path(summary["package"]).parent.resolve(),
            (self.codex_home / "pets").resolve(),
        )
        self.assertFalse((environment_home / "pets").exists())

    def test_install_cli_emits_json_and_errors_on_stderr(self):
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        success = subprocess.run(
            [
                sys.executable,
                str(INSTALL_SCRIPT),
                str(self.run_dir),
                "--codex-home",
                str(self.codex_home),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )

        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stderr, "")
        self.assertTrue(json.loads(success.stdout)["ok"])

        bad_run = self.root / "bad-run"
        bad = subprocess.run(
            [sys.executable, str(INSTALL_SCRIPT), str(bad_run), "--codex-home", str(self.codex_home)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertEqual(bad.stdout, "")
        self.assertIn("error", bad.stderr.lower())


class CleanupRunTests(PetRunFixture):
    def setUp(self) -> None:
        super().setUp()
        install = load_script("install_pet", INSTALL_SCRIPT)
        self.install_summary = install.install_pet(self.run_dir, self.codex_home)

    def add_cleanup_targets(self) -> None:
        (self.run_dir / "imagegen_jobs.json").write_text('{"private":true}', encoding="utf-8")
        for name in ("prompts", "decoded", "frames", "references"):
            target = self.run_dir / name
            target.mkdir()
            (target / "sensitive.txt").write_text(name, encoding="utf-8")
        stale = self.run_dir / ".build-staging-stale-random"
        stale.mkdir()
        (stale / "temporary.txt").write_text("stale", encoding="utf-8")
        (self.run_dir / ".pet_request.json.old.random.tmp").write_text(
            "atomic-temp", encoding="utf-8"
        )
        final = self.run_dir / "final"
        (final / ".spritesheet.png.random.tmp").write_text("temp", encoding="utf-8")
        (final / "obsolete-tool-output.png").write_text("obsolete", encoding="utf-8")
        qa = self.run_dir / "qa"
        (qa / ".contact-sheet.png.random.tmp").write_text("temp", encoding="utf-8")
        (qa / "obsolete-tool-output.json").write_text("{}", encoding="utf-8")
        previews = qa / "previews"
        (previews / ".idle.gif.random.tmp").write_text("temp", encoding="utf-8")
        (previews / "extra.gif").write_text("extra", encoding="utf-8")
        (previews / "tool-record.json").write_text("{}", encoding="utf-8")
        (self.run_dir / "unknown-reference-copy.png").write_bytes(b"private-copy")
        unknown_directory = self.run_dir / "unrecognized-tool-output"
        unknown_directory.mkdir()
        (unknown_directory / "copied-reference.jpg").write_bytes(b"private-reference")

    def test_cleanup_keeps_review_assets_and_removes_sensitive_manifests(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        self.add_cleanup_targets()

        result = cleanup.cleanup_run(self.run_dir)

        self.assertTrue(result["ok"])
        for relative in (
            "pet_request_summary.json",
            "build_state.json",
            "final/spritesheet.png",
            "final/validation.json",
            "qa/character-preview.png",
            "qa/contact-sheet.png",
            "qa/previews/idle.gif",
            "qa/run-summary.json",
        ):
            self.assertTrue((self.run_dir / relative).exists(), relative)
        for relative in (
            "pet_request.json",
            "imagegen_jobs.json",
            "prompts",
            "decoded",
            "frames",
            "references",
            ".build-staging-stale-random",
            ".pet_request.json.old.random.tmp",
            "final/.spritesheet.png.random.tmp",
            "final/obsolete-tool-output.png",
            "qa/.contact-sheet.png.random.tmp",
            "qa/obsolete-tool-output.json",
            "qa/previews/.idle.gif.random.tmp",
            "qa/previews/extra.gif",
            "qa/previews/tool-record.json",
            "unknown-reference-copy.png",
            "unrecognized-tool-output",
        ):
            self.assertFalse((self.run_dir / relative).exists(), relative)

    def test_cleanup_writes_summary_without_absolute_reference_paths(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)

        cleanup.cleanup_run(self.run_dir)

        summary = json.loads(
            (self.run_dir / "pet_request_summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(summary),
            {
                "schema_version",
                "pet",
                "style",
                "identity_features",
                "reference_count",
                "reference_basenames",
                "cleaned_at",
            },
        )
        self.assertEqual(
            summary["pet"],
            {
                "id": "pixel-pal",
                "display_name": "Pixel Pal",
                "description": "A tiny test companion.",
            },
        )
        self.assertEqual(summary["style"], "flat pixel art")
        self.assertEqual(summary["reference_count"], 1)
        self.assertEqual(summary["reference_basenames"], ["reference face.png"])
        serialized = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(str(self.reference.resolve()), serialized)
        run_summary = (self.run_dir / "qa" / "run-summary.json").read_text(encoding="utf-8")
        self.assertNotIn(str(self.reference.resolve()), run_summary)
        self.assertNotIn('"references"', run_summary.lower())

    def test_cleanup_refuses_run_without_successful_install(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        (self.run_dir / "qa" / "run-summary.json").unlink()

        with self.assertRaisesRegex(ValueError, "install|summary"):
            cleanup.cleanup_run(self.run_dir)

        self.assertTrue((self.run_dir / "pet_request.json").is_file())

    def test_cleanup_rejects_symlink_target_without_deleting_external_data(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        external = self.root / "external-prompts"
        external.mkdir()
        marker = external / "marker.txt"
        marker.write_text("preserve", encoding="utf-8")
        link = self.run_dir / "prompts"
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "reparse|symbolic|link"):
            cleanup.cleanup_run(self.run_dir)

        self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.run_dir / "pet_request.json").is_file())

    def test_cleanup_isolation_move_failure_rolls_back_all_targets(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        self.add_cleanup_targets()
        real_replace = cleanup.os.replace
        imagegen = self.run_dir / "imagegen_jobs.json"

        def fail_imagegen_move(source, destination):
            if Path(source) == imagegen and ".cleanup-staging-" in str(destination):
                raise OSError("simulated isolation failure")
            return real_replace(source, destination)

        with mock.patch.object(cleanup.os, "replace", side_effect=fail_imagegen_move):
            with self.assertRaisesRegex(OSError, "isolation failure"):
                cleanup.cleanup_run(self.run_dir)

        for relative in (
            "pet_request.json",
            "imagegen_jobs.json",
            "prompts",
            "decoded",
            "frames",
            "references",
            ".build-staging-stale-random",
        ):
            self.assertTrue((self.run_dir / relative).exists(), relative)
        self.assertEqual(list(self.run_dir.glob(".cleanup-staging-*")), [])

    def test_cleanup_rmtree_failure_returns_pending_warning_and_next_call_clears_it(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        self.add_cleanup_targets()

        with mock.patch.object(cleanup.shutil, "rmtree", side_effect=OSError("locked")):
            first = cleanup.cleanup_run(self.run_dir)

        pending = Path(first["cleanup_pending_path"])
        self.assertFalse(first["ok"])
        self.assertEqual(first["status"], "pending")
        self.assertTrue(first["warnings"])
        self.assertTrue(pending.is_dir())
        self.assertFalse((self.run_dir / "pet_request.json").exists())
        pending_run_summary = json.loads(
            (self.run_dir / "qa" / "run-summary.json").read_text(encoding="utf-8")
        )
        self.assertTrue(pending_run_summary["ok"])
        self.assertFalse(pending_run_summary["cleanup"]["ok"])
        self.assertEqual(pending_run_summary["cleanup"]["status"], "pending")

        second = cleanup.cleanup_run(self.run_dir)

        self.assertTrue(second["ok"])
        self.assertEqual(second["status"], "complete")
        self.assertFalse(pending.exists())
        self.assertNotIn("cleanup_pending_path", second)

    def test_cleanup_retry_rewrites_extra_fields_and_rejects_unsafe_basenames(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        cleanup.cleanup_run(self.run_dir)
        summary_path = self.run_dir / "pet_request_summary.json"
        allowed_fields = {
            "schema_version",
            "pet",
            "style",
            "identity_features",
            "reference_count",
            "reference_basenames",
            "cleaned_at",
        }
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["references"] = [str(self.reference.resolve())]
        summary["reference_paths"] = [str(self.reference.resolve())]
        summary_path.write_text(json.dumps(summary), encoding="utf-8")

        cleanup.cleanup_run(self.run_dir)

        rewritten = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(set(rewritten), allowed_fields)
        self.assertNotIn(str(self.reference.resolve()), json.dumps(rewritten))

        rewritten["reference_basenames"] = ["../private/reference face.png"]
        summary_path.write_text(json.dumps(rewritten), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "basename|summary"):
            cleanup.cleanup_run(self.run_dir)

    def test_cleanup_cli_emits_json(self):
        cleanup = load_script("cleanup_run", CLEANUP_SCRIPT)
        self.add_cleanup_targets()
        pending_stdout = StringIO()
        pending_stderr = StringIO()
        with (
            mock.patch.object(cleanup.shutil, "rmtree", side_effect=OSError("locked")),
            redirect_stdout(pending_stdout),
            redirect_stderr(pending_stderr),
        ):
            pending_exit = cleanup.main([str(self.run_dir)])
        pending_output = pending_stdout.getvalue()

        self.assertEqual(pending_exit, 2)
        self.assertFalse(json.loads(pending_output)["ok"])
        self.assertEqual(json.loads(pending_output)["status"], "pending")

        completed = subprocess.run(
            [sys.executable, str(CLEANUP_SCRIPT), str(self.run_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertTrue(json.loads(completed.stdout)["ok"])
        self.assertEqual(json.loads(completed.stdout)["status"], "complete")
        self.assertFalse((self.run_dir / "pet_request.json").exists())


if __name__ == "__main__":
    unittest.main()

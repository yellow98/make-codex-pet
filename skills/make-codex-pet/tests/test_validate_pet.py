from __future__ import annotations

from datetime import datetime
import hashlib
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
VALIDATE_SCRIPT = SCRIPTS_DIR / "validate_pet.py"

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


def load_validate():
    if not VALIDATE_SCRIPT.is_file():
        raise AssertionError(f"missing implementation: {VALIDATE_SCRIPT}")
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("validate_pet", VALIDATE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class ValidatePetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def make_run(
        self,
        name: str = "run",
        *,
        mode: str = "RGBA",
        size: tuple[int, int] = (ATLAS_WIDTH, ATLAS_HEIGHT),
        exif: Image.Exif | None = None,
    ) -> tuple[Path, Path]:
        run_dir = self.root / name
        atlas_path = run_dir / "final" / "spritesheet.png"
        atlas_path.parent.mkdir(parents=True)
        background = (0, 0, 0, 0) if mode == "RGBA" else (0, 0, 0)
        atlas = Image.new(mode, size, background)
        if size == (ATLAS_WIDTH, ATLAS_HEIGHT):
            draw = ImageDraw.Draw(atlas)
            fill = (40, 80, 120, 255) if mode == "RGBA" else (40, 80, 120)
            for row, (_, frame_count) in enumerate(ANIMATION_ROWS):
                for column in range(frame_count):
                    left = column * CELL_WIDTH + 20
                    top = row * CELL_HEIGHT + 24
                    draw.rectangle((left, top, left + 31, top + 39), fill=fill)
        save_kwargs = {"format": "PNG"}
        if exif is not None:
            save_kwargs["exif"] = exif
        atlas.save(atlas_path, **save_kwargs)
        self.write_complete_state(run_dir, atlas_path)
        return run_dir, atlas_path

    @staticmethod
    def write_complete_state(run_dir: Path, atlas_path: Path) -> None:
        state = {
            "schema_version": 1,
            "status": "complete",
            "build_id": "build-123",
            "spritesheet": "final/spritesheet.png",
            "spritesheet_sha256": hashlib.sha256(atlas_path.read_bytes()).hexdigest(),
        }
        (run_dir / "build_state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    def mutate_atlas(self, run_dir: Path, atlas_path: Path, mutate) -> None:
        with Image.open(atlas_path) as source:
            atlas = source.copy()
        mutate(atlas)
        atlas.save(atlas_path, format="PNG")
        self.write_complete_state(run_dir, atlas_path)

    def test_accepts_valid_atlas(self):
        validate = load_validate()
        _, atlas_path = self.make_run()

        report = validate.validate_atlas(atlas_path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["format"], "PNG")
        self.assertEqual(report["mode"], "RGBA")
        self.assertEqual(report["dimensions"], [ATLAS_WIDTH, ATLAS_HEIGHT])
        self.assertEqual(report["build_id"], "build-123")

    def test_accepts_valid_atlas_without_build_state_when_explicitly_disabled(self):
        validate = load_validate()
        _, original = self.make_run()
        atlas_path = self.root / "standalone.png"
        atlas_path.write_bytes(original.read_bytes())

        report = validate.validate_atlas(atlas_path, require_build_state=False)

        self.assertTrue(report["ok"], report["errors"])
        self.assertIsNone(report["build_id"])

    def test_rejects_wrong_dimensions(self):
        validate = load_validate()
        _, atlas_path = self.make_run(size=(512, 512))

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("1536x1872", " ".join(report["errors"]))
        self.assertEqual(report["dimensions"], [512, 512])

    def test_wrong_dimensions_are_rejected_from_header_without_decoding_pixels(self):
        validate = load_validate()
        _, atlas_path = self.make_run("header-only", size=(512, 512))

        with mock.patch.object(
            Image.Image, "load", side_effect=AssertionError("pixels decoded")
        ) as load:
            report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("1536x1872", " ".join(report["errors"]))
        load.assert_not_called()

    def test_rejects_rgb_without_silently_adding_alpha(self):
        validate = load_validate()
        _, atlas_path = self.make_run(mode="RGB")

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertEqual(report["mode"], "RGB")
        self.assertIn("RGBA", " ".join(report["errors"]))
        self.assertEqual(report["rows"], [])

    def test_rejects_disguised_and_corrupt_png(self):
        validate = load_validate()
        cases = {
            "disguised": lambda path: Image.new("RGB", (16, 16), "red").save(
                path, format="JPEG"
            ),
            "corrupt": lambda path: path.write_bytes(
                b"\x89PNG\r\n\x1a\nnot-a-complete-png"
            ),
        }
        for case, write in cases.items():
            with self.subTest(case=case):
                run_dir = self.root / case
                atlas_path = run_dir / "final" / "spritesheet.png"
                atlas_path.parent.mkdir(parents=True)
                write(atlas_path)
                self.write_complete_state(run_dir, atlas_path)

                report = validate.validate_atlas(atlas_path)

                self.assertFalse(report["ok"])
                self.assertTrue(report["errors"])
                if case == "disguised":
                    self.assertEqual(report["format"], "JPEG")
                    self.assertIn("PNG", " ".join(report["errors"]))

    def test_rejects_two_frame_apng(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("animated")
        with Image.open(atlas_path) as source:
            first = source.copy()
        second = first.copy()
        second.putpixel((25, 30), (255, 0, 0, 255))
        first.save(
            atlas_path,
            format="PNG",
            save_all=True,
            append_images=[second],
            duration=100,
            loop=0,
        )
        self.write_complete_state(run_dir, atlas_path)

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertRegex(" ".join(report["errors"]).lower(), r"animated|frame")

    def test_rejects_empty_required_cell(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run()

        def erase_required(atlas: Image.Image) -> None:
            draw = ImageDraw.Draw(atlas)
            draw.rectangle(
                (2 * CELL_WIDTH, 0, 3 * CELL_WIDTH - 1, CELL_HEIGHT - 1),
                fill=(0, 0, 0, 0),
            )

        self.mutate_atlas(run_dir, atlas_path, erase_required)
        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("idle", " ".join(report["errors"]))
        self.assertIn("2", " ".join(report["errors"]))
        self.assertEqual(report["rows"][0]["occupied_cells"], [0, 1, 3, 4, 5])

    def test_rejects_single_nearly_transparent_pixel_in_required_cell(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("faint-required")

        def leave_faint_pixel(atlas: Image.Image) -> None:
            draw = ImageDraw.Draw(atlas)
            draw.rectangle(
                (0, 0, CELL_WIDTH - 1, CELL_HEIGHT - 1), fill=(0, 0, 0, 0)
            )
            atlas.putpixel((40, 40), (255, 255, 255, 1))

        self.mutate_atlas(run_dir, atlas_path, leave_faint_pixel)
        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("idle[0]", " ".join(report["errors"]))

    def test_rejects_sixteen_visible_pixels_in_one_pixel_high_required_bbox(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("flat-required")

        def leave_flat_line(atlas: Image.Image) -> None:
            draw = ImageDraw.Draw(atlas)
            draw.rectangle(
                (0, 0, CELL_WIDTH - 1, CELL_HEIGHT - 1), fill=(0, 0, 0, 0)
            )
            for x in range(40, 56):
                atlas.putpixel((x, 40), (255, 255, 255, 16))

        self.mutate_atlas(run_dir, atlas_path, leave_flat_line)
        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("idle[0]", " ".join(report["errors"]))

    def test_accepts_exactly_sixteen_visible_pixels_in_small_required_block(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("minimum-readable")

        def leave_small_block(atlas: Image.Image) -> None:
            draw = ImageDraw.Draw(atlas)
            draw.rectangle(
                (0, 0, CELL_WIDTH - 1, CELL_HEIGHT - 1), fill=(0, 0, 0, 0)
            )
            for y in range(40, 44):
                for x in range(40, 44):
                    atlas.putpixel((x, y), (255, 255, 255, 16))

        self.mutate_atlas(run_dir, atlas_path, leave_small_block)
        report = validate.validate_atlas(atlas_path)

        self.assertTrue(report["ok"], report["errors"])

    def test_rejects_nontransparent_unused_cell(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run()

        def fill_unused(atlas: Image.Image) -> None:
            ImageDraw.Draw(atlas).rectangle(
                (6 * CELL_WIDTH + 30, 30, 6 * CELL_WIDTH + 50, 60),
                fill=(255, 0, 0, 255),
            )

        self.mutate_atlas(run_dir, atlas_path, fill_unused)
        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("unused", " ".join(report["errors"]).lower())
        self.assertEqual(report["rows"][0]["unexpected_cells"], [6])
        self.assertEqual(report["rows"][0]["occupied_cells"], list(range(7)))

    def test_rejects_oversize_file_before_image_loading(self):
        validate = load_validate()
        _, atlas_path = self.make_run()
        file_size = atlas_path.stat().st_size

        with mock.patch.object(validate.Image, "open", side_effect=AssertionError("opened")):
            report = validate.validate_atlas(atlas_path, max_bytes=file_size - 1)

        self.assertFalse(report["ok"])
        self.assertEqual(report["file_size"], file_size)
        self.assertEqual(report["max_file_size"], file_size - 1)
        self.assertIn("size", " ".join(report["errors"]).lower())

    def test_rejects_missing_incomplete_and_mismatched_build_state(self):
        validate = load_validate()
        cases = ("missing", "building", "failed", "missing-build-id", "hash-mismatch")
        for case in cases:
            with self.subTest(case=case):
                run_dir, atlas_path = self.make_run(case)
                state_path = run_dir / "build_state.json"
                if case == "missing":
                    state_path.unlink()
                else:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    if case in {"building", "failed"}:
                        state["status"] = case
                    elif case == "missing-build-id":
                        state["build_id"] = ""
                    else:
                        state["spritesheet_sha256"] = "0" * 64
                    state_path.write_text(json.dumps(state), encoding="utf-8")

                report = validate.validate_atlas(atlas_path)

                self.assertFalse(report["ok"])
                self.assertTrue(report["errors"])
                self.assertIn(
                    "build" if case != "hash-mismatch" else "sha256",
                    " ".join(report["errors"]).lower(),
                )

    def test_rejects_final_directory_symlink_before_reading_atlas(self):
        validate = load_validate()
        run_dir, _ = self.make_run("linked-final")
        final_dir = run_dir / "final"
        real_final = run_dir / "real-final"
        final_dir.rename(real_final)
        try:
            final_dir.symlink_to(real_final, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            real_final.rename(final_dir)
            self.skipTest(f"directory symlinks unavailable: {error}")

        report = validate.validate_atlas(final_dir / "spritesheet.png")

        self.assertFalse(report["ok"])
        self.assertRegex(" ".join(report["errors"]).lower(), r"symbolic|reparse|link")

    def test_rejects_symlink_or_reparse_build_state(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("linked-state")
        state_path = run_dir / "build_state.json"
        real_state = run_dir / "real-build-state.json"
        state_path.replace(real_state)
        try:
            state_path.symlink_to(real_state)
        except (OSError, NotImplementedError) as error:
            real_state.replace(state_path)
            self.skipTest(f"file symlinks unavailable: {error}")

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertRegex(" ".join(report["errors"]).lower(), r"symbolic|reparse|link")

    def test_rejects_build_state_larger_than_one_mibibyte(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("oversize-state")
        state_path = run_dir / "build_state.json"
        state = state_path.read_bytes()
        state_path.write_bytes(
            state + b" " * (1024 * 1024 + 1 - len(state))
        )

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("size", " ".join(report["errors"]).lower())

    def test_deeply_nested_build_state_returns_invalid_report_and_cli_exit_one(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run("deep-state")
        state_path = run_dir / "build_state.json"
        state_path.write_bytes(b"[" * 2000 + b"0" + b"]" * 2000)

        with mock.patch.object(
            validate.json,
            "loads",
            side_effect=RecursionError("maximum JSON nesting exceeded"),
        ):
            report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertRegex(
            " ".join(report["errors"]).lower(), r"build_state|build state"
        )
        self.assertIn("invalid", " ".join(report["errors"]).lower())

        output = self.root / "deep-state-validation.json"
        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(atlas_path),
                "--json-out",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertFalse(json.loads(completed.stdout)["ok"])
        self.assertIn("invalid", completed.stderr.lower())
        written = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(written["ok"])
        self.assertIn("invalid", " ".join(written["errors"]).lower())

    def test_rejects_atlas_swapped_between_lstat_and_open_when_identity_is_available(self):
        validate = load_validate()
        _, atlas_path = self.make_run("swapped-atlas")
        replacement = self.root / "replacement.png"
        replacement.write_bytes(atlas_path.read_bytes())
        original_stat = atlas_path.stat()
        replacement_stat = replacement.stat()
        if (
            not original_stat.st_ino
            or not replacement_stat.st_ino
            or original_stat.st_ino == replacement_stat.st_ino
        ):
            self.skipTest("stable file identities are unavailable")
        os.utime(
            replacement,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        real_open = validate.os.open

        def swap_then_open(path, flags, *args):
            os.replace(replacement, atlas_path)
            return real_open(path, flags, *args)

        with mock.patch.object(validate.os, "open", side_effect=swap_then_open):
            report = validate.validate_atlas(atlas_path, require_build_state=False)

        self.assertFalse(report["ok"])
        self.assertIn("changed", " ".join(report["errors"]).lower())

    def test_requires_exact_final_spritesheet_location(self):
        validate = load_validate()
        _, original = self.make_run()
        wrong_path = self.root / "wrong" / "spritesheet.png"
        wrong_path.parent.mkdir()
        wrong_path.write_bytes(original.read_bytes())

        report = validate.validate_atlas(wrong_path)

        self.assertFalse(report["ok"])
        self.assertIn("final", " ".join(report["errors"]))

    def test_warns_when_required_alpha_bbox_touches_cell_boundary(self):
        validate = load_validate()
        run_dir, atlas_path = self.make_run()

        def touch_left_boundary(atlas: Image.Image) -> None:
            ImageDraw.Draw(atlas).rectangle(
                (0, 40, 12, 70), fill=(255, 255, 255, 255)
            )

        self.mutate_atlas(run_dir, atlas_path, touch_left_boundary)
        report = validate.validate_atlas(atlas_path)

        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertIn("boundary", report["warnings"][0].lower())
        self.assertIn("idle", report["warnings"][0])

    def test_rejects_exif_metadata(self):
        validate = load_validate()
        exif = Image.Exif()
        exif[0x010E] = "private atlas metadata"
        _, atlas_path = self.make_run(exif=exif)

        report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("EXIF", " ".join(report["errors"]))

    def test_report_has_stable_fields_and_row_occupancy(self):
        validate = load_validate()
        _, atlas_path = self.make_run()

        report = validate.validate_atlas(atlas_path)

        expected_fields = {
            "schema_version",
            "ok",
            "atlas_path",
            "checked_at",
            "format",
            "mode",
            "dimensions",
            "file_size",
            "max_file_size",
            "sha256",
            "build_id",
            "rows",
            "errors",
            "warnings",
        }
        self.assertEqual(set(report), expected_fields)
        self.assertEqual(report["schema_version"], 1)
        self.assertTrue(Path(report["atlas_path"]).is_absolute())
        self.assertEqual(Path(report["atlas_path"]).resolve(), atlas_path.resolve())
        self.assertTrue(report["checked_at"].endswith("Z"))
        datetime.fromisoformat(report["checked_at"].replace("Z", "+00:00"))
        self.assertEqual(report["file_size"], atlas_path.stat().st_size)
        self.assertEqual(
            report["sha256"], hashlib.sha256(atlas_path.read_bytes()).hexdigest()
        )
        self.assertEqual(len(report["rows"]), 9)
        for row, (state, frame_count) in zip(report["rows"], ANIMATION_ROWS):
            self.assertEqual(
                row,
                {
                    "state": state,
                    "expected_frames": frame_count,
                    "occupied_cells": list(range(frame_count)),
                    "unexpected_cells": [],
                },
            )

    def test_input_failures_return_reports_instead_of_raising(self):
        validate = load_validate()
        missing = self.root / "missing" / "final" / "spritesheet.png"
        directory = self.root / "directory" / "final" / "spritesheet.png"
        directory.mkdir(parents=True)

        for atlas_path in (missing, directory):
            with self.subTest(path=atlas_path):
                report = validate.validate_atlas(atlas_path)
                self.assertFalse(report["ok"])
                self.assertTrue(report["errors"])
                self.assertEqual(Path(report["atlas_path"]).resolve(), atlas_path.resolve())

    def test_embedded_null_path_returns_report_instead_of_raising(self):
        validate = load_validate()

        report = validate.validate_atlas(Path("bad\0path"))

        self.assertFalse(report["ok"])
        self.assertIn("inspected", " ".join(report["errors"]))

    def test_rejects_symlink_or_reparse_atlas_before_reading(self):
        validate = load_validate()
        _, original = self.make_run("original")
        linked_run = self.root / "linked"
        linked_atlas = linked_run / "final" / "spritesheet.png"
        linked_atlas.parent.mkdir(parents=True)
        try:
            linked_atlas.symlink_to(original)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"file symlinks unavailable: {error}")

        report = validate.validate_atlas(linked_atlas)

        self.assertFalse(report["ok"])
        self.assertIn("link", " ".join(report["errors"]).lower())

    def test_decompression_bomb_warning_becomes_validation_error(self):
        validate = load_validate()
        _, atlas_path = self.make_run()

        with mock.patch.object(validate.Image, "MAX_IMAGE_PIXELS", 1_500_000):
            report = validate.validate_atlas(atlas_path)

        self.assertFalse(report["ok"])
        self.assertIn("bomb", " ".join(report["errors"]).lower())

    def test_atomic_report_write_failure_preserves_old_report_and_cleans_temp(self):
        validate = load_validate()
        output = self.root / "validation.json"
        old_bytes = b'{"old":true}\n'
        output.write_bytes(old_bytes)

        with mock.patch.object(
            validate.pet_common.os, "replace", side_effect=OSError("replace failed")
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                validate.write_validation_report({"ok": True}, output)

        self.assertEqual(output.read_bytes(), old_bytes)
        self.assertEqual(list(self.root.glob(".validation.json.*.tmp")), [])

    def test_cli_writes_reports_and_emits_compact_json_for_valid_and_invalid(self):
        load_validate()
        _, valid_atlas = self.make_run("cli-valid")
        valid_report = self.root / "valid-report.json"
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"

        valid = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(valid_atlas),
                "--json-out",
                str(valid_report),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )

        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertEqual(valid.stderr, "")
        valid_summary = json.loads(valid.stdout)
        self.assertTrue(valid_summary["ok"])
        self.assertEqual(
            valid.stdout,
            json.dumps(valid_summary, ensure_ascii=False, separators=(",", ":")) + "\n",
        )
        self.assertTrue(json.loads(valid_report.read_text(encoding="utf-8"))["ok"])

        invalid_report = self.root / "invalid-report.json"
        invalid = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(valid_atlas),
                "--json-out",
                str(invalid_report),
                "--max-bytes",
                "1",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )

        self.assertEqual(invalid.returncode, 1)
        invalid_summary = json.loads(invalid.stdout)
        self.assertFalse(invalid_summary["ok"])
        self.assertIn("invalid", invalid.stderr.lower())
        self.assertFalse(json.loads(invalid_report.read_text(encoding="utf-8"))["ok"])

    def test_cli_rejects_json_out_that_resolves_to_atlas_without_overwriting_it(self):
        load_validate()
        run_dir, atlas_path = self.make_run("cli-atlas-conflict")
        atlas_before = atlas_path.read_bytes()
        state_path = run_dir / "build_state.json"
        state_before = state_path.read_bytes()
        alias_parent = self.root / "atlas-parent-alias"
        try:
            alias_parent.symlink_to(atlas_path.parent, target_is_directory=True)
            output = alias_parent / atlas_path.name
        except (OSError, NotImplementedError):
            output = atlas_path.parent / ".." / "final" / atlas_path.name

        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(atlas_path),
                "--json-out",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("json", completed.stderr.lower())
        self.assertEqual(atlas_path.read_bytes(), atlas_before)
        self.assertEqual(state_path.read_bytes(), state_before)

    def test_cli_rejects_json_out_that_normalizes_to_build_state_without_overwriting_it(self):
        load_validate()
        run_dir, atlas_path = self.make_run("cli-state-conflict")
        atlas_before = atlas_path.read_bytes()
        state_path = run_dir / "build_state.json"
        state_before = state_path.read_bytes()
        if os.path.normcase("BUILD_STATE.JSON") != "BUILD_STATE.JSON":
            output = run_dir / "BUILD_STATE.JSON"
        else:
            output = run_dir / "final" / ".." / state_path.name

        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(atlas_path),
                "--json-out",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertIn("json", completed.stderr.lower())
        self.assertEqual(atlas_path.read_bytes(), atlas_before)
        self.assertEqual(state_path.read_bytes(), state_before)

    def test_cli_rejects_symlink_or_reparse_json_out_leaf_without_overwriting_target(self):
        load_validate()
        _, atlas_path = self.make_run("cli-linked-report")
        target = self.root / "preserve.json"
        old_bytes = b'{"preserve":true}\n'
        target.write_bytes(old_bytes)
        output = self.root / "validation-link.json"
        try:
            output.symlink_to(target)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"file symlinks unavailable: {error}")

        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATE_SCRIPT),
                str(atlas_path),
                "--json-out",
                str(output),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "")
        self.assertRegex(completed.stderr.lower(), r"symbolic|reparse|link")
        self.assertTrue(output.is_symlink())
        self.assertEqual(target.read_bytes(), old_bytes)


if __name__ == "__main__":
    unittest.main()

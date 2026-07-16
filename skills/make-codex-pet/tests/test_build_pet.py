from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc
import unittest
from unittest import mock

from PIL import Image, ImageDraw


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
BUILD_SCRIPT = SCRIPTS_DIR / "build_pet.py"


def load_build():
    if not BUILD_SCRIPT.is_file():
        raise AssertionError(f"missing implementation: {BUILD_SCRIPT}")
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("build_pet", BUILD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


build = load_build()


class BuildPetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def job(
        job_id: str,
        *,
        status: str = "complete",
        output_path: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": job_id,
            "kind": "base" if job_id == "base" else "animation-row",
            "status": status,
            "depends_on": [] if job_id == "base" else ["base"],
            "prompt_file": f"prompts/{job_id}.md",
            "output_path": output_path or f"decoded/{job_id}.png",
            "attempts": 1,
            "last_error": None,
        }

    @staticmethod
    def make_strip(path: Path, frame_count: int, *, empty_frame: int | None = None) -> None:
        frame_width = 24
        height = 32
        strip = Image.new("RGBA", (frame_width * frame_count, height), (0, 255, 0, 255))
        for index in range(frame_count):
            if index == empty_frame:
                continue
            left = index * frame_width + 4 + index % 3
            top = 4 + index % 2
            right = index * frame_width + 19
            bottom = 28
            color = (
                40 + (index * 29) % 180,
                20 + (index * 37) % 180,
                30 + (index * 43) % 180,
                255,
            )
            for y in range(top, bottom):
                for x in range(left, right):
                    strip.putpixel((x, y), color)
            # Make every GIF frame observably distinct even when the body bounds match.
            strip.putpixel((index * frame_width + 5 + index % 10, 5), (255, 255, 255, 255))
        path.parent.mkdir(parents=True, exist_ok=True)
        strip.save(path, format="PNG")

    def make_run(
        self,
        name: str = "run",
        *,
        style: str = "q-cartoon",
        empty_state: str | None = None,
        empty_frame: int = 0,
    ) -> Path:
        run_dir = self.root / name
        decoded = run_dir / "decoded"
        decoded.mkdir(parents=True)
        request = {
            "schema_version": 1,
            "pet": {"id": "demo", "display_name": "Demo", "style": style},
            "chroma_key": "#00FF00",
        }
        (run_dir / "pet_request.json").write_text(
            json.dumps(request), encoding="utf-8"
        )
        jobs = [self.job("base")]
        Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(decoded / "base.png")
        for state, frame_count in build.ANIMATION_ROWS:
            jobs.append(self.job(state))
            self.make_strip(
                decoded / f"{state}.png",
                frame_count,
                empty_frame=empty_frame if state == empty_state else None,
            )
        (run_dir / "imagegen_jobs.json").write_text(
            json.dumps({"schema_version": 1, "jobs": jobs}), encoding="utf-8"
        )
        return run_dir

    @staticmethod
    def alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
        return image.getchannel("A").getbbox()

    def test_remove_chroma_handles_transparency_feather_and_original_alpha(self):
        source = Image.new("RGBA", (4, 1))
        source.putdata(
            [
                (0, 0, 0, 255),
                (20, 0, 0, 200),
                (40, 0, 0, 123),
                (0, 0, 0, 0),
            ]
        )
        before = source.tobytes()

        result = build.remove_chroma(source, (0, 0, 0), tolerance=10, feather=20)

        self.assertEqual(result.mode, "RGBA")
        self.assertEqual(source.tobytes(), before)
        self.assertEqual([result.getpixel((x, 0))[3] for x in range(4)], [0, 100, 123, 0])
        for argument in ("tolerance", "feather"):
            with self.subTest(argument=argument):
                kwargs = {"tolerance": 10, "feather": 20, argument: 256}
                with self.assertRaisesRegex(ValueError, argument):
                    build.remove_chroma(source, (0, 0, 0), **kwargs)

    def test_pillow_11_compatible_chroma_code_uses_pixel_access(self):
        source = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("output_pixels: list", source)

        image = Image.new("RGBA", (2, 1))
        image.putpixel((0, 0), (0, 255, 0, 255))
        image.putpixel((1, 0), (255, 0, 0, 255))
        result = build.remove_chroma(image, (0, 255, 0))
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((1, 0))[3], 255)

    def test_remove_chroma_removes_edge_connected_green_gradient(self):
        source = Image.new("RGBA", (9, 9))
        for y in range(9):
            for x in range(9):
                source.putpixel((x, y), (90 + x * 8, 255, 90 + y * 8, 255))
        for y in range(3, 6):
            for x in range(3, 6):
                source.putpixel((x, y), (120, 120, 120, 255))

        result = build.remove_chroma(source, (0, 255, 0))

        for y in range(9):
            for x in range(9):
                expected_alpha = 255 if 3 <= x < 6 and 3 <= y < 6 else 0
                self.assertEqual(result.getpixel((x, y))[3], expected_alpha)

    def test_remove_chroma_preserves_isolated_green_foreground(self):
        source = Image.new("RGBA", (9, 9), (0, 255, 0, 255))
        for y in range(2, 7):
            for x in range(2, 7):
                source.putpixel((x, y), (180, 40, 40, 255))
        source.putpixel((4, 4), (0, 255, 0, 255))

        result = build.remove_chroma(source, (0, 255, 0))

        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((4, 4)), (0, 255, 0, 255))

    def test_remove_chroma_fully_clears_connected_green_feather_band(self):
        source = Image.new("RGBA", (3, 3), (30, 255, 30, 255))
        source.putpixel((1, 1), (120, 120, 120, 255))

        result = build.remove_chroma(source, (0, 255, 0))

        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((1, 1))[3], 255)

    def test_remove_tiny_components_drops_remote_noise(self):
        cleanup = getattr(build, "remove_tiny_components", None)
        self.assertIsNotNone(cleanup, "build_pet must expose remove_tiny_components")
        source = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
        source.paste((120, 120, 120, 255), (10, 8, 20, 24))
        source.putpixel((2, 2), (255, 0, 0, 255))
        source.putpixel((3, 2), (255, 0, 0, 255))

        result = cleanup(source)

        self.assertEqual(result.getpixel((2, 2))[3], 0)
        self.assertEqual(result.getpixel((15, 15))[3], 255)

    def test_remove_tiny_components_keeps_meaningful_accessory(self):
        cleanup = getattr(build, "remove_tiny_components", None)
        self.assertIsNotNone(cleanup, "build_pet must expose remove_tiny_components")
        source = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
        source.paste((120, 120, 120, 255), (10, 8, 20, 24))
        source.paste((30, 30, 30, 255), (2, 2, 7, 7))

        result = cleanup(source)

        self.assertEqual(result.getpixel((4, 4))[3], 255)
        self.assertEqual(result.getpixel((15, 15))[3], 255)

    def test_remove_tiny_components_keeps_small_nearby_accessory(self):
        source = Image.new("RGBA", (30, 30), (0, 0, 0, 0))
        source.paste((120, 120, 120, 255), (10, 8, 20, 24))
        source.paste((30, 30, 30, 255), (5, 10, 9, 14))

        result = build.remove_tiny_components(source)

        self.assertEqual(result.getpixel((6, 11))[3], 255)
        self.assertEqual(result.getpixel((15, 15))[3], 255)

    def test_remove_tiny_components_bounds_memory_for_large_character(self):
        source = Image.new("RGBA", (256, 256), (120, 120, 120, 255))

        tracemalloc.start()
        try:
            result = build.remove_tiny_components(source)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        self.assertEqual(result.getchannel("A").getbbox(), (0, 0, 256, 256))
        self.assertLess(peak, 4 * 1024 * 1024)

    def test_split_strip_uses_rounded_boundaries_and_rejects_empty_slots(self):
        strip = Image.new("RGB", (10, 1))
        strip.putdata([(x, 0, 0) for x in range(10)])

        frames = build.split_strip(strip, 3)

        self.assertEqual([frame.width for frame in frames], [3, 4, 3])
        self.assertEqual([frame.getpixel((0, 0))[0] for frame in frames], [0, 3, 7])
        with self.assertRaisesRegex(ValueError, "frame_count"):
            build.split_strip(strip, 0)
        with self.assertRaisesRegex(ValueError, "at least 1 pixel"):
            build.split_strip(Image.new("RGB", (2, 1)), 3)

    def test_uses_one_scale_and_baseline_per_row(self):
        narrow = Image.new("RGBA", (30, 50), (0, 0, 0, 0))
        narrow.paste((255, 0, 0, 255), (5, 5, 25, 45))
        wide = Image.new("RGBA", (50, 30), (0, 0, 0, 0))
        wide.paste((0, 0, 255, 255), (5, 5, 45, 25))

        normalized = build.normalize_row([narrow, wide], padding=12)
        boxes = [self.alpha_bbox(frame) for frame in normalized]

        self.assertEqual([frame.size for frame in normalized], [(192, 208), (192, 208)])
        self.assertEqual([box[3] for box in boxes if box], [196, 196])
        self.assertEqual(boxes[0][2] - boxes[0][0], 84)
        self.assertEqual(boxes[0][3] - boxes[0][1], 168)
        self.assertEqual(boxes[1][2] - boxes[1][0], 168)
        self.assertEqual(boxes[1][3] - boxes[1][1], 84)
        self.assertTrue(all("exif" not in frame.info for frame in normalized))
        with self.assertRaisesRegex(ValueError, "padding"):
            build.normalize_row([narrow], padding=96)
        with self.assertRaisesRegex(ValueError, "padding"):
            build.normalize_row([narrow], padding=0)

    def test_nearest_resampling_preserves_pixel_palette(self):
        frame = Image.new("RGBA", (2, 2))
        frame.putdata(
            [
                (255, 0, 0, 255),
                (0, 255, 0, 255),
                (0, 0, 255, 255),
                (255, 255, 0, 255),
            ]
        )

        normalized = build.normalize_row(
            [frame], padding=90, resample=Image.Resampling.NEAREST
        )[0]
        colors = {
            normalized.getpixel((x, y))
            for y in range(normalized.height)
            for x in range(normalized.width)
            if normalized.getpixel((x, y))[3]
        }

        self.assertEqual(
            colors,
            {
                (255, 0, 0, 255),
                (0, 255, 0, 255),
                (0, 0, 255, 255),
                (255, 255, 0, 255),
            },
        )

    def test_builds_1536_by_1872_rgba_atlas_with_transparent_unused_cells(self):
        cell = Image.new("RGBA", (192, 208), (120, 40, 20, 255))
        rows = [[cell] * count for _, count in build.ANIMATION_ROWS]

        atlas = build.compose_atlas(rows)

        self.assertEqual(atlas.mode, "RGBA")
        self.assertEqual(atlas.size, (1536, 1872))
        occupied = 0
        transparent = 0
        for row_index, (_, frame_count) in enumerate(build.ANIMATION_ROWS):
            for column in range(8):
                box = (column * 192, row_index * 208, (column + 1) * 192, (row_index + 1) * 208)
                if self.alpha_bbox(atlas.crop(box)) is None:
                    transparent += 1
                else:
                    occupied += 1
                    self.assertLess(column, frame_count)
        self.assertEqual(occupied, 57)
        self.assertEqual(transparent, 15)

    def test_preview_rejects_identical_frames_instead_of_modifying_pixels(self):
        frame = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
        frame.paste((255, 0, 0, 255), (70, 30, 120, 196))
        before = frame.tobytes()

        with self.assertRaisesRegex(ValueError, r"idle.*(?:static|duplicate|identical)"):
            build.render_previews(
                {"idle": [frame, frame.copy(), frame.copy()]},
                self.root / "identical-previews",
                duration_ms=120,
            )
        self.assertEqual(frame.tobytes(), before)
        self.assertFalse((self.root / "identical-previews" / "idle.gif").exists())

    def test_preview_saves_rgb_frames_over_checkerboard_background(self):
        frames: list[Image.Image] = []
        for index, color in enumerate(((255, 0, 0, 255), (0, 0, 255, 255))):
            frame = Image.new("RGBA", (192, 208), (0, 0, 0, 0))
            frame.paste(color, (70 + index, 30, 120 + index, 196))
            frames.append(frame)

        with mock.patch.object(build, "_stage_image", wraps=build._stage_image) as stage:
            previews = build.render_previews(
                {"idle": frames}, self.root / "rgb-previews", duration_ms=120
            )

        first_frame = stage.call_args.args[0]
        appended = stage.call_args.kwargs["save_kwargs"]["append_images"]
        self.assertEqual(first_frame.mode, "RGB")
        self.assertTrue(all(frame.mode == "RGB" for frame in appended))
        with Image.open(previews["idle"]) as gif:
            self.assertEqual(gif.n_frames, 2)
            checker_a = gif.convert("RGB").getpixel((2, 2))
            checker_b = gif.convert("RGB").getpixel((18, 2))
            self.assertNotEqual(checker_a, checker_b)

    def test_creates_contact_sheet_nine_gifs_frames_manifest_and_pixel_atlas(self):
        run_dir = self.make_run(style="pixel")

        with mock.patch.object(build, "normalize_row", wraps=build.normalize_row) as normalize:
            summary = build.build_pet(run_dir)

        spritesheet = Path(summary["spritesheet"])
        contact_sheet = Path(summary["contact_sheet"])
        manifest_path = Path(summary["frames_manifest"])
        self.assertTrue(summary["ok"])
        self.assertTrue(all(Path(summary[key]).is_absolute() for key in ("run_dir", "spritesheet", "contact_sheet", "frames_manifest")))
        self.assertTrue(spritesheet.is_file())
        self.assertTrue(contact_sheet.is_file())
        with Image.open(spritesheet) as atlas:
            self.assertEqual(atlas.mode, "RGBA")
            self.assertEqual(atlas.size, (1536, 1872))
        self.assertEqual(len(summary["previews"]), 9)
        for state, frame_count in build.ANIMATION_ROWS:
            preview = Path(summary["previews"][state])
            self.assertTrue(preview.is_absolute())
            with Image.open(preview) as gif:
                self.assertEqual(gif.format, "GIF")
                self.assertEqual(gif.n_frames, frame_count)
                for index in range(frame_count):
                    gif.seek(index)
                    self.assertEqual(gif.info["duration"], 120)
                self.assertEqual(gif.info["loop"], 0)
        frames_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(sum(row["frame_count"] for row in frames_manifest["rows"]), 57)
        self.assertEqual(
            len(list((run_dir / "frames").glob("*/*.png"))),
            57,
        )
        self.assertEqual(len(normalize.call_args_list), 9)
        self.assertTrue(
            all(call.kwargs["resample"] == Image.Resampling.NEAREST for call in normalize.call_args_list)
        )

    def test_contact_sheet_has_checkerboard_grid_and_all_row_labels(self):
        atlas = Image.new("RGBA", (1536, 1872), (0, 0, 0, 0))
        output = self.root / "contact-sheet.png"

        with mock.patch.object(
            ImageDraw.ImageDraw, "text", autospec=True
        ) as draw_text:
            build.render_contact_sheet(atlas, output)

        labels = [call.args[2] for call in draw_text.call_args_list]
        self.assertEqual(labels, [state for state, _ in build.ANIMATION_ROWS])
        with Image.open(output) as contact:
            self.assertEqual(contact.getpixel((build.CONTACT_LABEL_WIDTH + 8, 8)), (224, 224, 224, 255))
            self.assertEqual(contact.getpixel((build.CONTACT_LABEL_WIDTH + 24, 8)), (184, 184, 184, 255))
            self.assertEqual(
                contact.getpixel((build.CONTACT_LABEL_WIDTH + build.CELL_WIDTH, 50)),
                (0, 0, 0, 255),
            )

    def test_build_selects_resampling_from_each_approved_style(self):
        styles = {
            "auto": Image.Resampling.LANCZOS,
            "q-cartoon": Image.Resampling.LANCZOS,
            "sticker": Image.Resampling.LANCZOS,
            "pixel": Image.Resampling.NEAREST,
        }
        for index, (style, expected) in enumerate(styles.items()):
            with self.subTest(style=style):
                run_dir = self.make_run(f"resample-{index}", style=style)
                with mock.patch.object(
                    build, "normalize_row", wraps=build.normalize_row
                ) as normalize:
                    build.build_pet(run_dir)
                self.assertEqual(len(normalize.call_args_list), 9)
                self.assertTrue(
                    all(
                        call.kwargs["resample"] == expected
                        for call in normalize.call_args_list
                    )
                )

    def test_fails_for_missing_pending_duplicate_and_wrong_output_jobs(self):
        cases = {
            "missing": lambda jobs: jobs.pop(2),
            "pending": lambda jobs: jobs[2].update(status="pending"),
            "duplicate": lambda jobs: jobs.append(dict(jobs[2])),
            "wrong-output": lambda jobs: jobs[2].update(output_path="decoded/not-the-state.png"),
            "traversal": lambda jobs: jobs[2].update(output_path="../escape.png"),
        }
        for index, (case, mutate) in enumerate(cases.items()):
            with self.subTest(case=case):
                run_dir = self.make_run(f"invalid-{index}")
                manifest_path = run_dir / "imagegen_jobs.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                mutate(manifest["jobs"])
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises((OSError, ValueError), msg=case):
                    build.build_pet(run_dir)

    def test_fails_for_non_png_empty_frame_and_incomplete_base(self):
        non_png = self.make_run("non-png")
        Image.new("RGB", (24 * 6, 32), "red").save(
            non_png / "decoded" / "idle.png", format="JPEG"
        )
        with self.assertRaisesRegex(ValueError, "PNG"):
            build.build_pet(non_png)

        empty = self.make_run("empty", empty_state="idle", empty_frame=2)
        with self.assertRaisesRegex(ValueError, r"idle.*frame 2"):
            build.build_pet(empty)

        incomplete = self.make_run("incomplete-base")
        manifest_path = incomplete / "imagegen_jobs.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["jobs"][0]["status"] = "pending"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "base.*complete"):
            build.build_pet(incomplete)

    def test_fails_for_missing_wrong_path_and_non_png_base_output(self):
        cases = ("missing", "wrong-path", "non-png")
        for index, case in enumerate(cases):
            with self.subTest(case=case):
                run_dir = self.make_run(f"invalid-base-{index}")
                base_path = run_dir / "decoded" / "base.png"
                if case == "missing":
                    base_path.unlink()
                elif case == "wrong-path":
                    manifest_path = run_dir / "imagegen_jobs.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["jobs"][0]["output_path"] = "../base.png"
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                else:
                    Image.new("RGB", (16, 16), "red").save(base_path, format="JPEG")

                with self.assertRaisesRegex(ValueError, r"base.*(?:PNG|output_path)"):
                    build.build_pet(run_dir)

    def test_limited_png_loader_rejects_bytes_dimensions_pixels_and_bombs(self):
        run_dir = self.make_run("limited-loader")
        source = run_dir / "decoded" / "base.png"
        loader = getattr(build, "_load_png", None)
        self.assertIsNotNone(loader, "build_pet must expose one bounded _load_png helper")
        error_type = getattr(build, "BuildError", ValueError)

        cases = (
            ("bytes", "MAX_INPUT_BYTES", 8, r"size|bytes"),
            ("side", "MAX_IMAGE_SIDE", 8, r"dimension|side"),
            ("pixels", "MAX_IMAGE_PIXELS", 128, r"pixel"),
        )
        for case, constant, limit, message in cases:
            with self.subTest(case=case):
                with mock.patch.object(build, constant, limit, create=True):
                    with self.assertRaisesRegex(error_type, message):
                        loader(run_dir, "base", source)

        with mock.patch.object(build.Image, "MAX_IMAGE_PIXELS", 200):
            with self.assertRaisesRegex(error_type, r"bomb|unsafe"):
                loader(run_dir, "base", source)

    @staticmethod
    def seed_old_outputs(run_dir: Path, marker: str) -> dict[str, bytes]:
        snapshots: dict[str, bytes] = {}
        for directory_name in ("frames", "final", "qa"):
            directory = run_dir / directory_name
            directory.mkdir(parents=True, exist_ok=True)
            marker_path = directory / f"old-{directory_name}.txt"
            marker_bytes = f"{marker}:{directory_name}".encode("utf-8")
            marker_path.write_bytes(marker_bytes)
            snapshots[f"{directory_name}/{marker_path.name}"] = marker_bytes
        return snapshots

    def assert_old_outputs_unchanged(
        self, run_dir: Path, snapshots: dict[str, bytes]
    ) -> None:
        for relative_path, expected in snapshots.items():
            self.assertEqual((run_dir / relative_path).read_bytes(), expected)
        self.assertFalse((run_dir / "final" / "spritesheet.png").exists())
        self.assertFalse((run_dir / "qa" / "contact-sheet.png").exists())
        self.assertEqual(list(run_dir.glob(".build-staging-*")), [])

    def test_contact_or_gif_staging_failure_preserves_all_old_outputs(self):
        failures = {
            "contact": ("render_contact_sheet", OSError("contact failed")),
            "gif": ("render_previews", OSError("gif failed")),
        }
        for index, (case, (target, failure)) in enumerate(failures.items()):
            with self.subTest(case=case):
                run_dir = self.make_run(f"staging-failure-{index}")
                snapshots = self.seed_old_outputs(run_dir, case)
                with mock.patch.object(build, target, side_effect=failure):
                    with self.assertRaisesRegex(OSError, f"{case} failed"):
                        build.build_pet(run_dir)
                self.assert_old_outputs_unchanged(run_dir, snapshots)
                state = json.loads((run_dir / "build_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["status"], "failed")

    def test_second_directory_publish_failure_rolls_back_every_output(self):
        run_dir = self.make_run("publish-rollback")
        snapshots = self.seed_old_outputs(run_dir, "rollback")
        real_replace = build.os.replace

        def fail_new_final(source, destination):
            source_path = Path(source)
            destination_path = Path(destination).resolve()
            if (
                source_path.name == "final"
                and source_path.parent.name.startswith(".build-staging-")
                and destination_path == (run_dir / "final").resolve()
            ):
                raise OSError("second directory publish failed")
            return real_replace(source, destination)

        with mock.patch.object(build.os, "replace", side_effect=fail_new_final):
            with self.assertRaisesRegex(OSError, "second directory publish failed"):
                build.build_pet(run_dir)

        self.assert_old_outputs_unchanged(run_dir, snapshots)
        state = json.loads((run_dir / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")

    def test_success_state_contains_build_id_and_current_spritesheet_sha(self):
        run_dir = self.make_run("state-complete")

        build.build_pet(run_dir)

        state = json.loads((run_dir / "build_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "complete")
        self.assertRegex(state["build_id"], r"^[0-9a-f]{32}$")
        expected = hashlib.sha256((run_dir / "final" / "spritesheet.png").read_bytes()).hexdigest()
        self.assertEqual(state["spritesheet_sha256"], expected)

    def test_cleanup_failure_after_complete_keeps_committed_outputs(self):
        cleanup_modes = ("full", "partial")
        real_rmtree = build.shutil.rmtree
        for index, mode in enumerate(cleanup_modes):
            with self.subTest(mode=mode):
                run_dir = self.make_run(f"cleanup-{index}")
                self.seed_old_outputs(run_dir, mode)

                def fail_cleanup(path):
                    cleanup_root = Path(path)
                    if mode == "partial":
                        old_frames = cleanup_root / "_backups" / "frames"
                        if old_frames.is_dir():
                            real_rmtree(old_frames)
                    raise OSError(f"{mode} cleanup failed")

                with mock.patch.object(build.shutil, "rmtree", side_effect=fail_cleanup):
                    summary = build.build_pet(run_dir)

                self.assertTrue(summary["ok"])
                self.assertIn(f"{mode} cleanup failed", " ".join(summary.get("warnings", [])))
                state = json.loads((run_dir / "build_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state["status"], "complete")
                self.assertTrue((run_dir / "final" / "spritesheet.png").is_file())
                self.assertTrue((run_dir / "frames" / "frames-manifest.json").is_file())
                self.assertTrue((run_dir / "qa" / "contact-sheet.png").is_file())
                self.assertFalse((run_dir / "frames" / "old-frames.txt").exists())
                self.assertFalse((run_dir / "final" / "old-final.txt").exists())
                self.assertFalse((run_dir / "qa" / "old-qa.txt").exists())
                self.assertNotEqual(list(run_dir.glob(".build-staging-*")), [])

    def test_rejects_linked_output_directory_before_writing(self):
        run_dir = self.make_run("linked-output")
        external = self.root / "external-qa"
        external.mkdir()
        qa = run_dir / "qa"
        try:
            qa.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"directory symlinks unavailable: {error}")

        error_type = getattr(build, "BuildError", ValueError)
        with self.assertRaisesRegex(error_type, r"symbolic|reparse|link"):
            build.build_pet(run_dir)
        self.assertEqual(list(external.iterdir()), [])

    def test_generated_outputs_do_not_inherit_source_exif(self):
        run_dir = self.make_run("source-exif")
        exif = Image.Exif()
        exif[0x010E] = "private source metadata"
        for state, _ in build.ANIMATION_ROWS:
            source = run_dir / "decoded" / f"{state}.png"
            with Image.open(source) as strip:
                clean = strip.convert("RGBA")
            clean.save(source, format="PNG", exif=exif)

        build.build_pet(run_dir)

        outputs = [
            run_dir / "final" / "spritesheet.png",
            run_dir / "frames" / "idle" / "00.png",
            run_dir / "qa" / "contact-sheet.png",
            run_dir / "qa" / "previews" / "idle.gif",
        ]
        for output in outputs:
            with self.subTest(output=output.name), Image.open(output) as image:
                self.assertEqual(len(image.getexif()), 0)
                self.assertNotIn("exif", image.info)

    def test_cli_success_is_json_only_and_errors_use_stderr(self):
        run_dir = self.make_run("cli")
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        completed = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT), "--run-dir", str(run_dir)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertTrue(json.loads(completed.stdout)["ok"])

        failed = subprocess.run(
            [sys.executable, str(BUILD_SCRIPT), "--run-dir", str(self.root / "missing")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, "")
        self.assertIn("error:", failed.stderr.lower())


if __name__ == "__main__":
    unittest.main()

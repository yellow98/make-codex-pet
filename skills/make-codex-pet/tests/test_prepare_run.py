from __future__ import annotations

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


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
COMMON_SCRIPT = SCRIPTS_DIR / "pet_common.py"
PREPARE_SCRIPT = SCRIPTS_DIR / "prepare_run.py"


def load_common():
    if not COMMON_SCRIPT.is_file():
        raise AssertionError(f"missing implementation: {COMMON_SCRIPT}")
    spec = importlib.util.spec_from_file_location("pet_common", COMMON_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PrepareRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.reference = self.root / "reference photo.png"
        self.reference.write_bytes(b"reference-image")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def run_cli(
        self,
        *,
        name: str = "小橙",
        pet_id: str | None = None,
        style: str = "q-cartoon",
        identity: list[str] | None = None,
        references: list[Path] | None = None,
        output_dir: Path | None = None,
        chroma_key: str = "#00FF00",
        force: bool = False,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        identity = identity if identity is not None else ["短黑发", "圆框眼镜", "橙色卫衣"]
        references = references if references is not None else [self.reference]
        command = [
            sys.executable,
            str(PREPARE_SCRIPT),
            "--pet-name",
            name,
            "--style",
            style,
            "--chroma-key",
            chroma_key,
        ]
        if pet_id is not None:
            command.extend(["--pet-id", pet_id])
        for feature in identity:
            command.extend(["--identity-feature", feature])
        for reference in references:
            command.extend(["--reference", str(reference)])
        if output_dir is not None:
            command.extend(["--output-dir", str(output_dir)])
        if force:
            command.append("--force")

        run_env = os.environ.copy()
        run_env["PYTHONUTF8"] = "1"
        if env:
            run_env.update(env)
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=run_env,
            check=False,
        )

    def successful_run(self, **kwargs):
        kwargs.setdefault("output_dir", self.root / "run")
        result = self.run_cli(**kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(set(summary), {"ok", "run_dir", "request", "jobs"})
        self.assertTrue(summary["ok"])
        run_dir = Path(summary["run_dir"])
        request = json.loads(Path(summary["request"]).read_text(encoding="utf-8"))
        jobs = json.loads(Path(summary["jobs"]).read_text(encoding="utf-8"))
        return run_dir, request, jobs

    def test_contract_constants_and_animation_row_order(self):
        common = load_common()
        self.assertEqual(common.ATLAS_COLUMNS, 8)
        self.assertEqual(common.ATLAS_ROWS, 9)
        self.assertEqual((common.CELL_WIDTH, common.CELL_HEIGHT), (192, 208))
        self.assertEqual((common.ATLAS_WIDTH, common.ATLAS_HEIGHT), (1536, 1872))
        self.assertEqual(common.MAX_ATLAS_BYTES, 20 * 1024 * 1024)
        self.assertEqual(
            common.ANIMATION_ROWS,
            (
                ("idle", 6),
                ("running-right", 8),
                ("running-left", 8),
                ("waving", 4),
                ("jumping", 5),
                ("failed", 8),
                ("waiting", 6),
                ("running", 6),
                ("review", 6),
            ),
        )

    def test_creates_base_and_nine_resumable_jobs(self):
        run_dir, request, manifest = self.successful_run()
        self.assertEqual(request["schema_version"], 1)
        self.assertIn("schema_version", manifest)
        self.assertEqual(manifest["schema_version"], 1)
        jobs = manifest["jobs"]
        self.assertEqual(
            [job["id"] for job in jobs],
            [
                "base",
                "idle",
                "running-right",
                "running-left",
                "waving",
                "jumping",
                "failed",
                "waiting",
                "running",
                "review",
            ],
        )
        self.assertEqual(jobs[0]["depends_on"], [])
        self.assertEqual(jobs[0]["kind"], "base")
        for job in jobs[1:]:
            self.assertEqual(job["depends_on"], ["base"])
            self.assertEqual(job["kind"], "animation-row")
        for job in jobs:
            self.assertEqual(job["status"], "pending")
            self.assertEqual(job["attempts"], 0)
            self.assertIsNone(job["last_error"])
            self.assertTrue((run_dir / job["prompt_file"]).is_file())
            self.assertTrue(job["output_path"].startswith("decoded/"))
        for directory in ("decoded", "frames", "final", "qa"):
            self.assertTrue((run_dir / directory).is_dir())

    def test_non_ascii_name_gets_stable_safe_hash_id(self):
        common = load_common()
        first = common.slugify_pet_id("小橙")
        second = common.slugify_pet_id("小橙")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^pet-[0-9a-f]{8}$")
        self.assertRegex(first, r"^[a-z0-9-]+$")

    def test_ascii_name_is_slugged_and_explicit_safe_id_is_preserved(self):
        common = load_common()
        self.assertEqual(common.slugify_pet_id("  Orange Cat  "), "orange-cat")
        self.assertEqual(common.slugify_pet_id("ignored", "orange-cat-2"), "orange-cat-2")
        _, request, _ = self.successful_run(pet_id="orange-cat-2")
        self.assertEqual(request["pet"]["id"], "orange-cat-2")

    def test_rejects_invalid_explicit_pet_id(self):
        result = self.run_cli(pet_id="../Orange Cat", output_dir=self.root / "bad-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pet id", result.stderr.lower())

    def test_pet_ids_are_limited_to_sixty_four_characters(self):
        common = load_common()
        long_name = "A" * 80 + " Cat"
        expected_suffix = hashlib.sha256(long_name.encode("utf-8")).hexdigest()[:8]
        first = common.slugify_pet_id(long_name)
        second = common.slugify_pet_id(long_name)
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), 64)
        self.assertTrue(first.endswith(f"-{expected_suffix}"))

        with self.assertRaisesRegex(ValueError, "64"):
            common.slugify_pet_id("ignored", "a" * 65)
        result = self.run_cli(pet_id="a" * 65, output_dir=self.root / "long-id")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("64", result.stderr)

    def test_rejects_fewer_than_three_identity_features(self):
        result = self.run_cli(identity=["短黑发", "圆框眼镜"], output_dir=self.root / "too-few")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("3 to 5 identity features", result.stderr)

    def test_rejects_more_than_five_identity_features(self):
        result = self.run_cli(identity=["a", "b", "c", "d", "e", "f"], output_dir=self.root / "too-many")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("3 to 5 identity features", result.stderr)

    def test_rejects_invalid_style(self):
        result = self.run_cli(style="photorealistic", output_dir=self.root / "bad-style")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_rejects_invalid_chroma_key(self):
        result = self.run_cli(chroma_key="green", output_dir=self.root / "bad-chroma")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("chroma key", result.stderr.lower())

    def test_rejects_missing_reference_file(self):
        missing = self.root / "missing.png"
        result = self.run_cli(references=[missing], output_dir=self.root / "missing-ref")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reference", result.stderr.lower())
        self.assertIn("regular file", result.stderr.lower())

    def test_rejects_nonempty_output_without_force(self):
        output_dir = self.root / "occupied"
        output_dir.mkdir()
        (output_dir / "keep.txt").write_text("occupied", encoding="utf-8")
        result = self.run_cli(output_dir=output_dir)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-empty", result.stderr)

    def test_force_rejects_unmarked_nonempty_output(self):
        output_dir = self.root / "unmarked"
        output_dir.mkdir()
        marker = output_dir / "keep.txt"
        marker.write_text("not a pet run", encoding="utf-8")
        forced = self.run_cli(output_dir=output_dir, force=True)
        self.assertNotEqual(forced.returncode, 0)
        self.assertIn("valid pet run", forced.stderr.lower())
        self.assertEqual(marker.read_text(encoding="utf-8"), "not a pet run")

    def test_force_cleans_only_known_artifacts_from_valid_run(self):
        output_dir = self.root / "valid-run"
        self.successful_run(output_dir=output_dir)
        stale_files = (
            output_dir / "decoded" / "idle.png",
            output_dir / "frames" / "idle-00.png",
            output_dir / "final" / "spritesheet.png",
            output_dir / "qa" / "old-report.json",
        )
        for stale in stale_files:
            stale.parent.mkdir(parents=True, exist_ok=True)
            stale.write_bytes(b"stale")
        unrelated = output_dir / "keep.txt"
        unrelated.write_text("preserve me", encoding="utf-8")

        forced = self.run_cli(output_dir=output_dir, force=True)
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertTrue(all(not stale.exists() for stale in stale_files))
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "preserve me")
        for directory in ("prompts", "decoded", "frames", "final", "qa"):
            self.assertTrue((output_dir / directory).is_dir())

    def test_atomic_json_replace_failure_preserves_target_and_cleans_temp(self):
        common = load_common()
        destination = self.root / "atomic.json"
        destination.write_text('{"old":true}\n', encoding="utf-8")
        with mock.patch.object(common.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                common.write_json(destination, {"new": True})
        self.assertEqual(destination.read_text(encoding="utf-8"), '{"old":true}\n')
        self.assertEqual(list(self.root.glob(f".{destination.name}.*.tmp")), [])

    def test_atomic_text_replace_failure_preserves_target_and_cleans_temp(self):
        common = load_common()
        self.assertTrue(hasattr(common, "write_text_atomic"), "missing write_text_atomic implementation")
        destination = self.root / "atomic.md"
        destination.write_text("old prompt\n", encoding="utf-8")
        with mock.patch.object(common.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                common.write_text_atomic(destination, "new prompt\n")
        self.assertEqual(destination.read_text(encoding="utf-8"), "old prompt\n")
        self.assertEqual(list(self.root.glob(f".{destination.name}.*.tmp")), [])

    def test_prompts_repeat_identity_lock_style_chroma_and_exact_frames(self):
        identity = ["short black hair", "round glasses", "orange hoodie"]
        run_dir, _, _ = self.successful_run(identity=identity, chroma_key="#12abEF")
        base_prompt = (run_dir / "prompts" / "base.md").read_text(encoding="utf-8")
        for feature in identity:
            self.assertIn(feature, base_prompt)
        self.assertIn("q-cartoon", base_prompt)
        self.assertIn("#12ABEF", base_prompt)

        common = load_common()
        for state, frame_count in common.ANIMATION_ROWS:
            prompt = (run_dir / "prompts" / f"{state}.md").read_text(encoding="utf-8")
            for feature in identity:
                self.assertIn(feature, prompt)
            self.assertIn(f"Action: {state}", prompt)
            self.assertIn(f"exactly {frame_count} separate animation frames", prompt)
            self.assertIn("No text", prompt)
            self.assertIn("borders", prompt)
            self.assertIn("overlap", prompt)
            self.assertIn("cropping", prompt)
            self.assertIn("detached effects", prompt)

    def test_default_run_dir_uses_codex_home_environment(self):
        codex_home = self.root / "custom-codex-home"
        result = self.run_cli(env={"CODEX_HOME": str(codex_home)})
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(result.stdout)
        run_dir = Path(summary["run_dir"])
        self.assertTrue(run_dir.is_relative_to(codex_home.resolve() / "pet-runs"))
        self.assertRegex(run_dir.name, r"^pet-[0-9a-f]{8}-\d{8}T\d{6}Z$")

    def test_request_stores_absolute_reference_paths_without_copying_sources(self):
        run_dir, request, _ = self.successful_run()
        self.assertEqual(request["references"], [str(self.reference.resolve())])
        self.assertEqual(request["pet"]["display_name"], "小橙")
        self.assertEqual(request["pet"]["style"], "q-cartoon")
        self.assertEqual(request["identity_features"], ["短黑发", "圆框眼镜", "橙色卫衣"])
        self.assertEqual(request["chroma_key"], "#00FF00")
        copied_files = [path for path in run_dir.rglob("*") if path.is_file()]
        self.assertFalse(any(path.read_bytes() == b"reference-image" for path in copied_files))


if __name__ == "__main__":
    unittest.main()

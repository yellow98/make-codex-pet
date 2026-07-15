from __future__ import annotations

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from PIL import Image


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
RECORD_SCRIPT = SCRIPTS_DIR / "record_job.py"


def load_record():
    if not RECORD_SCRIPT.is_file():
        raise AssertionError(f"missing implementation: {RECORD_SCRIPT}")
    scripts_path = str(SCRIPTS_DIR)
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    spec = importlib.util.spec_from_file_location("record_job", RECORD_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


record = load_record()


class RecordJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.manifest_path = self.run_dir / "imagegen_jobs.json"
        self.write_manifest()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def job(
        job_id: str,
        *,
        status: str = "pending",
        attempts: int = 0,
        depends_on: list[str] | None = None,
        output_path: str | None = None,
    ) -> dict[str, object]:
        return {
            "id": job_id,
            "kind": "base" if job_id == "base" else "animation-row",
            "status": status,
            "depends_on": [] if depends_on is None else depends_on,
            "prompt_file": f"prompts/{job_id}.md",
            "output_path": output_path or f"decoded/{job_id}.png",
            "attempts": attempts,
            "last_error": None,
        }

    def write_manifest(self, jobs: list[dict[str, object]] | None = None) -> None:
        if jobs is None:
            jobs = [self.job("base"), self.job("idle", depends_on=["base"])]
        self.manifest_path.write_text(
            json.dumps({"schema_version": 1, "jobs": jobs}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_manifest(self) -> dict[str, object]:
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    @staticmethod
    def make_png(path: Path, color: tuple[int, int, int, int] = (255, 0, 0, 255)) -> bytes:
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (4, 3), color).save(path, format="PNG")
        return path.read_bytes()

    def test_record_complete_copies_png_and_marks_only_selected_job(self):
        source = self.root / "generated source.png"
        expected_png = self.make_png(source)
        before = self.read_manifest()

        destination = record.record_complete(self.run_dir, "base", source)

        self.assertEqual(destination, (self.run_dir / "decoded" / "base.png").resolve())
        self.assertEqual(destination.read_bytes(), expected_png)
        after = self.read_manifest()
        selected = after["jobs"][0]
        self.assertEqual(selected["status"], "complete")
        self.assertEqual(selected["attempts"], 1)
        self.assertIsNone(selected["last_error"])
        self.assertEqual(selected["source_basename"], source.name)
        self.assertRegex(selected["completed_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertEqual(after["jobs"][1], before["jobs"][1])
        self.assertNotIn(str(source.resolve()), self.manifest_path.read_text(encoding="utf-8"))

    def test_rejects_file_that_is_not_a_real_png(self):
        source = self.root / "fake.png"
        source.write_bytes(b"not a PNG")
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "PNG"):
            record.record_complete(self.run_dir, "base", source)

        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertFalse((self.run_dir / "decoded" / "base.png").exists())

    def test_rejects_missing_or_non_file_source(self):
        source_dir = self.root / "source-dir"
        source_dir.mkdir()
        for source in (self.root / "missing.png", source_dir):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "regular file"):
                    record.record_complete(self.run_dir, "base", source)

    def test_rejects_unknown_job_id(self):
        source = self.root / "source.png"
        self.make_png(source)
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "unknown job"):
            record.record_complete(self.run_dir, "missing", source)
        with self.assertRaisesRegex(ValueError, "unknown job"):
            record.record_failure(self.run_dir, "missing", "failed")

        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_rejects_output_outside_run_directory(self):
        source = self.root / "source.png"
        self.make_png(source)
        unsafe_outputs = ["../escaped.png", str((self.root / "absolute.png").resolve())]
        for output_path in unsafe_outputs:
            with self.subTest(output_path=output_path):
                self.write_manifest([self.job("base", output_path=output_path)])
                before = self.manifest_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "output_path"):
                    record.record_complete(self.run_dir, "base", source)
                self.assertEqual(self.manifest_path.read_bytes(), before)
                self.assertFalse((self.root / "escaped.png").exists())

    def test_rejects_manifest_with_invalid_schema(self):
        source = self.root / "source.png"
        self.make_png(source)
        invalid_documents = [
            {"schema_version": 2, "jobs": []},
            {"schema_version": 1, "jobs": "not-a-list"},
            {"schema_version": 1, "jobs": [{"id": "base"}]},
            {
                "schema_version": 1,
                "jobs": [
                    {
                        key: value
                        for key, value in self.job("base").items()
                        if key != "kind"
                    }
                ],
            },
            {
                "schema_version": 1,
                "jobs": [{**self.job("base"), "last_error": 123}],
            },
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                self.manifest_path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "manifest"):
                    record.record_complete(self.run_dir, "base", source)

    def test_rejects_job_until_all_dependencies_are_complete(self):
        source = self.root / "idle.png"
        self.make_png(source)
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "depend"):
            record.record_complete(self.run_dir, "idle", source)

        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertFalse((self.run_dir / "decoded" / "idle.png").exists())

    def test_complete_job_requires_force_before_replacement(self):
        jobs = [self.job("base", status="complete", attempts=1)]
        jobs[0]["completed_at"] = "2026-01-01T00:00:00Z"
        jobs[0]["source_basename"] = "old.png"
        self.write_manifest(jobs)
        destination = self.run_dir / "decoded" / "base.png"
        old_png = self.make_png(destination, (255, 0, 0, 255))
        source = self.root / "new.png"
        new_png = self.make_png(source, (0, 0, 255, 255))

        with self.assertRaisesRegex(ValueError, "force"):
            record.record_complete(self.run_dir, "base", source)
        self.assertEqual(destination.read_bytes(), old_png)

        result = record.record_complete(self.run_dir, "base", source, force=True)

        self.assertEqual(result.read_bytes(), new_png)
        selected = self.read_manifest()["jobs"][0]
        self.assertEqual(selected["status"], "complete")
        self.assertEqual(selected["attempts"], 2)
        self.assertEqual(selected["source_basename"], "new.png")

    def test_record_failure_increments_attempts_and_preserves_completed_jobs(self):
        base = self.job("base", status="complete", attempts=1)
        base["completed_at"] = "2026-01-01T00:00:00Z"
        idle = self.job("idle", attempts=2, depends_on=["base"])
        self.write_manifest([base, idle])
        before = copy.deepcopy(self.read_manifest())
        message = "first line\nsecond line\rthird line\u2028fourth line " + ("x" * 600)

        record.record_failure(self.run_dir, "idle", message)

        after = self.read_manifest()
        self.assertEqual(after["jobs"][0], before["jobs"][0])
        failed = after["jobs"][1]
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["attempts"], 3)
        self.assertLessEqual(len(failed["last_error"]), 500)
        self.assertNotIn("\n", failed["last_error"])
        self.assertNotIn("\r", failed["last_error"])
        self.assertEqual(len(failed["last_error"].splitlines()), 1)
        self.assertRegex(failed["failed_at"], r"^\d{4}-\d{2}-\d{2}T.*Z$")

    def test_completed_job_cannot_be_downgraded_to_failed(self):
        base = self.job("base", status="complete", attempts=1)
        base["completed_at"] = "2026-01-01T00:00:00Z"
        self.write_manifest([base])
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "complete"):
            record.record_failure(self.run_dir, "base", "late failure")

        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_completing_base_retains_character_preview(self):
        source = self.root / "base.png"
        expected_png = self.make_png(source, (3, 4, 5, 255))

        destination = record.record_complete(self.run_dir, "base", source)

        preview = self.run_dir / "qa" / "character-preview.png"
        self.assertEqual(destination.read_bytes(), expected_png)
        self.assertEqual(preview.read_bytes(), expected_png)
        self.assertNotEqual(destination.resolve(), preview.resolve())

    def test_remove_source_only_deletes_generated_images_source(self):
        codex_home = self.root / "codex-home"
        generated_source = codex_home / "generated_images" / "nested" / "base.png"
        self.make_png(generated_source)

        record.record_complete(
            self.run_dir,
            "base",
            generated_source,
            remove_source=True,
            codex_home=codex_home,
        )

        self.assertFalse(generated_source.exists())
        outside_source = self.root / "outside.png"
        self.make_png(outside_source)
        record.record_complete(
            self.run_dir,
            "idle",
            outside_source,
            remove_source=True,
            codex_home=codex_home,
        )
        self.assertTrue(outside_source.exists())

    def test_replace_failure_preserves_old_target_and_manifest(self):
        destination = self.run_dir / "decoded" / "base.png"
        old_png = self.make_png(destination, (255, 0, 0, 255))
        source = self.root / "new.png"
        self.make_png(source, (0, 255, 0, 255))
        before = self.manifest_path.read_bytes()
        real_replace = os.replace

        def fail_destination(src, dst):
            if Path(dst).resolve() == destination.resolve():
                raise OSError("replace failed")
            return real_replace(src, dst)

        with mock.patch.object(record.os, "replace", side_effect=fail_destination):
            with self.assertRaisesRegex(OSError, "replace failed"):
                record.record_complete(self.run_dir, "base", source)

        self.assertEqual(destination.read_bytes(), old_png)
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertEqual(list(destination.parent.glob(f".{destination.name}.*.tmp")), [])

    def test_concurrent_completions_preserve_both_job_updates(self):
        first = self.job("first")
        second = self.job("second")
        self.write_manifest([first, second])
        first_source = self.root / "first.png"
        second_source = self.root / "second.png"
        self.make_png(first_source, (1, 2, 3, 255))
        self.make_png(second_source, (4, 5, 6, 255))
        first_ready = self.root / "first.ready"
        second_ready = self.root / "second.ready"
        worker = r"""
import json
from pathlib import Path
import sys
import time

scripts_dir, run_dir, job_id, source, own_ready, peer_ready = sys.argv[1:]
sys.path.insert(0, scripts_dir)
import record_job

real_open = record_job.Image.open
def synchronized_open(*args, **kwargs):
    Path(own_ready).touch()
    deadline = time.monotonic() + 10
    while not Path(peer_ready).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("peer did not reach PNG verification")
        time.sleep(0.01)
    return real_open(*args, **kwargs)

record_job.Image.open = synchronized_open
output = record_job.record_complete(Path(run_dir), job_id, Path(source))
print(json.dumps({"output": str(output)}))
"""
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        commands = [
            [
                sys.executable,
                "-c",
                worker,
                str(SCRIPTS_DIR),
                str(self.run_dir),
                "first",
                str(first_source),
                str(first_ready),
                str(second_ready),
            ],
            [
                sys.executable,
                "-c",
                worker,
                str(SCRIPTS_DIR),
                str(self.run_dir),
                "second",
                str(second_source),
                str(second_ready),
                str(first_ready),
            ],
        ]
        processes = [
            subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=environment,
            )
            for command in commands
        ]
        results = [process.communicate(timeout=20) for process in processes]
        for process, (stdout, stderr) in zip(processes, results):
            self.assertEqual(process.returncode, 0, stderr)
            json.loads(stdout)

        statuses = {job["id"]: job["status"] for job in self.read_manifest()["jobs"]}
        self.assertEqual(statuses, {"first": "complete", "second": "complete"})
        self.assertTrue((self.run_dir / ".record-job.lock").is_file())

    def test_initializes_empty_lock_file_only_after_lock_acquisition(self):
        events: list[str] = []

        class RaceSensitiveLockFile:
            def __init__(self) -> None:
                self.locked = False
                self.size = 0

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                return False

            def seek(self, offset, whence=os.SEEK_SET):
                return offset

            def tell(self):
                return self.size

            def write(self, data):
                events.append("write")
                self.size += len(data)
                return len(data)

            def flush(self):
                events.append("flush")
                if not self.locked:
                    raise PermissionError("another process locked the empty file")

        lock_file = RaceSensitiveLockFile()

        def acquire(candidate):
            events.append("lock")
            candidate.locked = True
            return True

        def release(candidate):
            events.append("unlock")
            candidate.locked = False

        with (
            mock.patch.object(record.Path, "open", return_value=lock_file),
            mock.patch.object(record, "_try_lock", side_effect=acquire),
            mock.patch.object(record, "_unlock", side_effect=release),
        ):
            try:
                with record._record_lock(self.run_dir):
                    events.append("yield")
            except PermissionError as error:
                self.fail(f"lock file was initialized before acquisition: {error}")

        self.assertEqual(events, ["lock", "write", "flush", "yield", "unlock"])

    def test_base_preview_replace_failure_rolls_back_all_outputs_and_manifest(self):
        destination = self.run_dir / "decoded" / "base.png"
        preview = self.run_dir / "qa" / "character-preview.png"
        old_destination = self.make_png(destination, (10, 20, 30, 255))
        old_preview = self.make_png(preview, (40, 50, 60, 255))
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source, (70, 80, 90, 255))
        before_manifest = self.manifest_path.read_bytes()
        real_replace = os.replace

        def fail_preview(src, dst):
            if Path(dst).resolve() == preview.resolve():
                raise OSError("preview replace failed")
            return real_replace(src, dst)

        with mock.patch.object(record.os, "replace", side_effect=fail_preview):
            with self.assertRaisesRegex(OSError, "preview replace failed"):
                record.record_complete(
                    self.run_dir,
                    "base",
                    source,
                    remove_source=True,
                    codex_home=codex_home,
                )

        self.assertEqual(destination.read_bytes(), old_destination)
        self.assertEqual(preview.read_bytes(), old_preview)
        self.assertEqual(self.manifest_path.read_bytes(), before_manifest)
        self.assertTrue(source.exists())

    def test_manifest_replace_failure_rolls_back_base_and_preview(self):
        destination = self.run_dir / "decoded" / "base.png"
        preview = self.run_dir / "qa" / "character-preview.png"
        old_destination = self.make_png(destination, (10, 20, 30, 255))
        old_preview = self.make_png(preview, (40, 50, 60, 255))
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source, (70, 80, 90, 255))
        before_manifest = self.manifest_path.read_bytes()
        real_replace = os.replace

        def fail_manifest(src, dst):
            if Path(dst).resolve() == self.manifest_path.resolve():
                raise OSError("manifest replace failed")
            return real_replace(src, dst)

        with mock.patch.object(record.os, "replace", side_effect=fail_manifest):
            with self.assertRaisesRegex(OSError, "manifest replace failed"):
                record.record_complete(
                    self.run_dir,
                    "base",
                    source,
                    remove_source=True,
                    codex_home=codex_home,
                )

        self.assertEqual(destination.read_bytes(), old_destination)
        self.assertEqual(preview.read_bytes(), old_preview)
        self.assertEqual(self.manifest_path.read_bytes(), before_manifest)
        self.assertTrue(source.exists())

    def test_rejects_reserved_and_noncanonical_output_paths(self):
        unsafe_outputs = [
            "imagegen_jobs.json",
            "qa/character-preview.png",
            "decoded/other.png",
            "decoded/base.png:stream",
            "CON",
        ]
        for output_path in unsafe_outputs:
            with self.subTest(output_path=output_path):
                self.write_manifest([self.job("base", output_path=output_path)])
                before = self.manifest_path.read_bytes()
                with self.assertRaisesRegex(ValueError, "output_path"):
                    record.record_failure(self.run_dir, "base", "failed")
                self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_rejects_duplicate_output_paths(self):
        self.write_manifest(
            [
                self.job("base"),
                self.job("idle", output_path="decoded/base.png"),
            ]
        )
        before = self.manifest_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "output_path"):
            record.record_failure(self.run_dir, "base", "failed")

        self.assertEqual(self.manifest_path.read_bytes(), before)

    def test_rejects_decoded_directory_that_resolves_outside_run(self):
        external = self.root / "external-decoded"
        external.mkdir()
        decoded = self.run_dir / "decoded"
        try:
            decoded.symlink_to(external, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"directory symlinks unavailable: {error}")

        with self.assertRaisesRegex(ValueError, "output_path|outside"):
            record.record_failure(self.run_dir, "base", "failed")

        self.assertEqual(list(external.iterdir()), [])

    def test_rejects_output_leaf_symlink_to_reserved_file(self):
        reserved = self.run_dir / "pet_request.json"
        reserved.write_text('{"keep":true}\n', encoding="utf-8")
        decoded = self.run_dir / "decoded"
        decoded.mkdir()
        output = decoded / "base.png"
        try:
            output.symlink_to(reserved)
        except OSError as error:
            self.skipTest(f"file symlinks unavailable: {error}")
        before_manifest = self.manifest_path.read_bytes()
        before_reserved = reserved.read_bytes()

        with self.assertRaisesRegex(ValueError, "symbolic|reparse|output_path"):
            record.record_failure(self.run_dir, "base", "failed")

        self.assertEqual(self.manifest_path.read_bytes(), before_manifest)
        self.assertEqual(reserved.read_bytes(), before_reserved)

    def test_rejects_preview_leaf_symlink_to_reserved_file(self):
        reserved = self.run_dir / "pet_request.json"
        reserved.write_text('{"keep":true}\n', encoding="utf-8")
        qa = self.run_dir / "qa"
        qa.mkdir()
        preview = qa / "character-preview.png"
        try:
            preview.symlink_to(reserved)
        except OSError as error:
            self.skipTest(f"file symlinks unavailable: {error}")
        source = self.root / "base.png"
        self.make_png(source)
        before_manifest = self.manifest_path.read_bytes()
        before_reserved = reserved.read_bytes()

        with self.assertRaisesRegex(ValueError, "symbolic|reparse|preview"):
            record.record_complete(self.run_dir, "base", source)

        self.assertEqual(self.manifest_path.read_bytes(), before_manifest)
        self.assertEqual(reserved.read_bytes(), before_reserved)
        self.assertFalse((self.run_dir / "decoded" / "base.png").exists())

    def test_second_staging_failure_cleans_temps_and_preserves_transaction(self):
        destination = self.run_dir / "decoded" / "base.png"
        preview = self.run_dir / "qa" / "character-preview.png"
        old_destination = self.make_png(destination, (10, 20, 30, 255))
        old_preview = self.make_png(preview, (40, 50, 60, 255))
        source = self.root / "source.png"
        self.make_png(source, (70, 80, 90, 255))
        before_manifest = self.manifest_path.read_bytes()
        real_stage = record._stage_replacement
        stage_count = 0

        def fail_second_stage(path, data):
            nonlocal stage_count
            stage_count += 1
            if stage_count == 2:
                raise OSError("second staging failed")
            return real_stage(path, data)

        with mock.patch.object(record, "_stage_replacement", side_effect=fail_second_stage):
            with self.assertRaisesRegex(OSError, "second staging failed"):
                record.record_complete(self.run_dir, "base", source)

        self.assertEqual(destination.read_bytes(), old_destination)
        self.assertEqual(preview.read_bytes(), old_preview)
        self.assertEqual(self.manifest_path.read_bytes(), before_manifest)
        self.assertEqual(list(destination.parent.glob(".*.new.*.tmp")), [])
        self.assertEqual(list(destination.parent.glob(".*.old.*.tmp")), [])

    def test_source_replaced_after_verification_does_not_change_staged_png(self):
        source = self.root / "base.png"
        original = self.make_png(source, (1, 2, 3, 255))
        replacement = self.root / "replacement.png"
        replacement_bytes = self.make_png(replacement, (200, 201, 202, 255))
        real_image_open = record.Image.open

        def open_then_swap_after_verify(*args, **kwargs):
            image = real_image_open(*args, **kwargs)
            real_verify = image.verify

            def verify_then_swap():
                result = real_verify()
                os.replace(replacement, source)
                return result

            image.verify = verify_then_swap
            return image

        with mock.patch.object(record.Image, "open", side_effect=open_then_swap_after_verify):
            destination = record.record_complete(self.run_dir, "base", source)

        preview = self.run_dir / "qa" / "character-preview.png"
        self.assertEqual(destination.read_bytes(), original)
        self.assertEqual(preview.read_bytes(), original)
        self.assertEqual(source.read_bytes(), replacement_bytes)

    def test_source_identity_change_before_cleanup_preserves_replacement(self):
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source, (1, 2, 3, 255))
        replacement = self.root / "replacement.png"
        replacement_bytes = self.make_png(replacement, (200, 201, 202, 255))
        real_write_json = record.write_json

        def write_manifest_then_replace(*args, **kwargs):
            result = real_write_json(*args, **kwargs)
            os.replace(replacement, source)
            return result

        with mock.patch.object(record, "write_json", side_effect=write_manifest_then_replace):
            record.record_complete(
                self.run_dir,
                "base",
                source,
                remove_source=True,
                codex_home=codex_home,
            )

        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), replacement_bytes)

    def test_in_place_source_rewrite_before_cleanup_is_not_deleted(self):
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source, (1, 2, 3, 255))
        replacement = self.root / "replacement.png"
        replacement_bytes = self.make_png(replacement, (200, 201, 202, 255))
        real_write_json = record.write_json

        def write_manifest_then_rewrite(*args, **kwargs):
            result = real_write_json(*args, **kwargs)
            source.write_bytes(replacement_bytes)
            return result

        with mock.patch.object(record, "write_json", side_effect=write_manifest_then_rewrite):
            record.record_complete(
                self.run_dir,
                "base",
                source,
                remove_source=True,
                codex_home=codex_home,
            )

        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), replacement_bytes)

    def test_source_swap_during_quarantine_preserves_new_content(self):
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source, (1, 2, 3, 255))
        replacement = self.root / "replacement.png"
        replacement_bytes = self.make_png(replacement, (200, 201, 202, 255))
        real_replace = os.replace
        source_resolved = source.resolve()

        def swap_before_quarantine(src, dst):
            source_path = Path(src)
            destination_path = Path(dst)
            if (
                source_path.resolve() == source_resolved
                and destination_path.parent.resolve() == source_resolved.parent
            ):
                real_replace(replacement, source_resolved)
            return real_replace(src, dst)

        with mock.patch.object(record.os, "replace", side_effect=swap_before_quarantine):
            record.record_complete(
                self.run_dir,
                "base",
                source,
                remove_source=True,
                codex_home=codex_home,
            )

        self.assertTrue(source.is_file())
        self.assertEqual(source.read_bytes(), replacement_bytes)

    def test_remove_source_waits_for_manifest_update_success(self):
        codex_home = self.root / "codex-home"
        source = codex_home / "generated_images" / "base.png"
        self.make_png(source)

        with mock.patch.object(record, "write_json", side_effect=OSError("manifest failed")):
            with self.assertRaisesRegex(OSError, "manifest failed"):
                record.record_complete(
                    self.run_dir,
                    "base",
                    source,
                    remove_source=True,
                    codex_home=codex_home,
                )

        self.assertTrue(source.exists())

    def test_cli_complete_prints_only_json_and_cli_errors_use_stderr(self):
        source = self.root / "source.png"
        self.make_png(source)
        command = [
            sys.executable,
            str(RECORD_SCRIPT),
            "complete",
            "--run-dir",
            str(self.run_dir),
            "--job-id",
            "base",
            "--source",
            str(source),
        ]
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"

        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        summary = json.loads(completed.stdout)
        self.assertEqual(summary["ok"], True)
        self.assertEqual(summary["job_id"], "base")
        self.assertEqual(Path(summary["output"]), (self.run_dir / "decoded" / "base.png").resolve())

        failed = subprocess.run(
            command[:-4] + ["--job-id", "missing", "--source", str(source)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout, "")
        self.assertIn("unknown job", failed.stderr.lower())

    def test_cli_fail_prints_only_json(self):
        command = [
            sys.executable,
            str(RECORD_SCRIPT),
            "fail",
            "--run-dir",
            str(self.run_dir),
            "--job-id",
            "base",
            "--message",
            "generation failed",
        ]
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertEqual(json.loads(completed.stdout), {"ok": True, "job_id": "base", "status": "failed"})


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any, Iterator, NamedTuple

from PIL import Image, UnidentifiedImageError

from pet_common import is_relative_to, read_json, resolve_codex_home, write_json


SCHEMA_VERSION = 1
MANIFEST_NAME = "imagegen_jobs.json"
LOCK_NAME = ".record-job.lock"
LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.05
CHARACTER_PREVIEW = "qa/character-preview.png"
VALID_STATUSES = {"pending", "failed", "complete"}
MAX_ERROR_LENGTH = 500
_SAFE_JOB_ID = re.compile(r"[a-z0-9-]+\Z")


class _SourceSnapshot(NamedTuple):
    path: Path
    basename: str
    data: bytes
    identity: tuple[int, int]
    digest: bytes


class _StagedReplacement:
    def __init__(
        self,
        destination: Path,
        staged: Path,
        backup: Path | None,
        existed: bool,
    ) -> None:
        self.destination = destination
        self.staged = staged
        self.backup = backup
        self.existed = existed
        self.committed = False


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _file_identity(file_stat: os.stat_result) -> tuple[int, int]:
    return file_stat.st_dev, file_stat.st_ino


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & reparse_flag)


def _reject_reparse_components(run_dir: Path, target: Path, *, label: str) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as error:
        raise ValueError(f"{label} is outside the run directory") from error
    current = run_dir
    paths = [current]
    for part in relative.parts:
        current = current / part
        paths.append(current)
    for path in paths:
        try:
            file_stat = os.lstat(path)
        except FileNotFoundError:
            break
        if _is_reparse_point(file_stat):
            raise ValueError(f"{label} contains a symbolic link or reparse point: {path}")


def _resolve_run_dir(run_dir: Path) -> Path:
    unresolved = Path(os.path.abspath(Path(run_dir).expanduser()))
    _reject_reparse_components(unresolved, unresolved, label="run directory")
    resolved = unresolved.resolve()
    if not resolved.is_dir():
        raise ValueError(f"run directory must be an existing directory: {resolved}")
    return resolved


def _try_lock(lock_file) -> bool:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    import fcntl

    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(lock_file) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _record_lock(run_dir: Path) -> Iterator[None]:
    if not run_dir.is_dir():
        raise ValueError(f"run directory must be an existing directory: {run_dir}")
    lock_path = run_dir / LOCK_NAME
    with lock_path.open("a+b") as lock_file:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        acquired = False
        while not acquired:
            acquired = _try_lock(lock_file)
            if acquired:
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"timed out waiting for record job lock after {LOCK_TIMEOUT_SECONDS:.1f}s: "
                    f"{lock_path}"
                )
            time.sleep(LOCK_POLL_SECONDS)
        try:
            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            yield
        finally:
            _unlock(lock_file)


def _canonical_output_path(run_dir: Path, job_id: str, raw_path: Any) -> Path:
    expected = f"decoded/{job_id}.png"
    if raw_path != expected:
        raise ValueError(f"manifest job {job_id!r} output_path must be exactly {expected!r}")
    decoded_dir = run_dir / "decoded"
    decoded_dir.mkdir(parents=True, exist_ok=True)
    unresolved_destination = decoded_dir / f"{job_id}.png"
    _reject_reparse_components(
        run_dir,
        unresolved_destination,
        label="manifest output_path",
    )
    decoded_resolved = decoded_dir.resolve()
    if not is_relative_to(decoded_resolved, run_dir) or decoded_resolved == run_dir:
        raise ValueError("manifest output_path decoded directory resolves outside the run directory")
    destination = (decoded_resolved / f"{job_id}.png").resolve()
    if not is_relative_to(destination, run_dir):
        raise ValueError("manifest job output_path resolves outside the run directory")
    if destination.is_symlink():
        raise ValueError("manifest job output_path must not be a symbolic link")
    return destination


def _preview_path(run_dir: Path) -> Path:
    qa_dir = run_dir / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    unresolved_preview = qa_dir / "character-preview.png"
    _reject_reparse_components(run_dir, unresolved_preview, label="character preview")
    qa_resolved = qa_dir.resolve()
    if not is_relative_to(qa_resolved, run_dir) or qa_resolved == run_dir:
        raise ValueError("character preview directory resolves outside the run directory")
    preview = (qa_resolved / "character-preview.png").resolve()
    if not is_relative_to(preview, run_dir):
        raise ValueError("character preview resolves outside the run directory")
    if preview.is_symlink():
        raise ValueError("character preview must not be a symbolic link")
    return preview


def _load_manifest(
    run_dir: Path,
) -> tuple[Path, dict[str, Any], dict[str, dict[str, Any]], dict[str, Path]]:
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError(f"manifest is missing or unsafe: {manifest_path}")
    try:
        document = read_json(manifest_path)
    except (OSError, ValueError) as error:
        raise ValueError(f"manifest cannot be read: {manifest_path}") from error
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document["schema_version"] != SCHEMA_VERSION
    ):
        raise ValueError(f"manifest must use schema_version {SCHEMA_VERSION}")
    jobs = document.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("manifest jobs must be a list")

    jobs_by_id: dict[str, dict[str, Any]] = {}
    output_paths: dict[str, Path] = {}
    seen_output_paths: set[str] = set()
    required_fields = {
        "id",
        "kind",
        "status",
        "depends_on",
        "prompt_file",
        "output_path",
        "attempts",
        "last_error",
    }
    for job in jobs:
        if not isinstance(job, dict) or not required_fields.issubset(job):
            raise ValueError("manifest contains an invalid job")
        job_id = job["id"]
        if (
            not isinstance(job_id, str)
            or not _SAFE_JOB_ID.fullmatch(job_id)
            or job_id in jobs_by_id
        ):
            raise ValueError("manifest job ids must be safe, non-empty, and unique")
        if job["kind"] not in {"base", "animation-row"}:
            raise ValueError(f"manifest job {job_id!r} has an invalid kind")
        if job["status"] not in VALID_STATUSES:
            raise ValueError(f"manifest job {job_id!r} has an invalid status")
        if (
            not isinstance(job["attempts"], int)
            or isinstance(job["attempts"], bool)
            or job["attempts"] < 0
        ):
            raise ValueError(f"manifest job {job_id!r} has invalid attempts")
        dependencies = job["depends_on"]
        if not isinstance(dependencies, list) or any(
            not isinstance(dependency, str) or not dependency for dependency in dependencies
        ):
            raise ValueError(f"manifest job {job_id!r} has invalid dependencies")
        if not isinstance(job["prompt_file"], str) or not job["prompt_file"]:
            raise ValueError(f"manifest job {job_id!r} has an invalid prompt_file")
        if job["last_error"] is not None and not isinstance(job["last_error"], str):
            raise ValueError(f"manifest job {job_id!r} has an invalid last_error")
        raw_output_path = job["output_path"]
        if raw_output_path in seen_output_paths:
            raise ValueError(f"manifest contains duplicate output_path: {raw_output_path!r}")
        output_paths[job_id] = _canonical_output_path(run_dir, job_id, raw_output_path)
        seen_output_paths.add(raw_output_path)
        jobs_by_id[job_id] = job

    for job_id, job in jobs_by_id.items():
        if any(dependency not in jobs_by_id for dependency in job["depends_on"]):
            raise ValueError(f"manifest job {job_id!r} has an unknown dependency")
    return manifest_path, document, jobs_by_id, output_paths


def _read_png_source(source: Path) -> _SourceSnapshot:
    source_path = Path(source).expanduser()
    if source_path.is_symlink():
        raise ValueError(f"source must be an existing regular file: {source}")
    try:
        resolved = source_path.resolve(strict=True)
        with resolved.open("rb") as source_file:
            source_stat = os.fstat(source_file.fileno())
            if not stat.S_ISREG(source_stat.st_mode):
                raise ValueError(f"source must be an existing regular file: {source}")
            data = source_file.read()
    except ValueError:
        raise
    except OSError as error:
        raise ValueError(f"source must be an existing regular file: {source}") from error

    try:
        with Image.open(BytesIO(data)) as image:
            image_format = image.format
            image.verify()
    except (OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise ValueError(f"source must be a valid PNG file: {source}") from error
    if image_format != "PNG":
        raise ValueError(f"source must be a valid PNG file: {source}")
    return _SourceSnapshot(
        path=resolved,
        basename=resolved.name,
        data=data,
        identity=_file_identity(source_stat),
        digest=hashlib.sha256(data).digest(),
    )


def _write_temp_bytes(destination: Path, data: bytes, *, role: str) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.{role}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output_file:
            output_file.write(data)
            output_file.flush()
            os.fsync(output_file.fileno())
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _stage_replacement(destination: Path, data: bytes) -> _StagedReplacement:
    if destination.is_symlink() or (destination.exists() and not destination.is_file()):
        raise ValueError(f"output target must be a regular file or absent: {destination}")
    staged = _write_temp_bytes(destination, data, role="new")
    existed = destination.is_file()
    backup: Path | None = None
    try:
        if existed:
            backup = _write_temp_bytes(destination, destination.read_bytes(), role="old")
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return _StagedReplacement(destination, staged, backup, existed)


def _cleanup_staged(replacements: list[_StagedReplacement]) -> None:
    for replacement in replacements:
        replacement.staged.unlink(missing_ok=True)
        if replacement.backup is not None:
            replacement.backup.unlink(missing_ok=True)


def _rollback_replacements(replacements: list[_StagedReplacement]) -> None:
    rollback_error: OSError | None = None
    for replacement in reversed(replacements):
        if not replacement.committed:
            continue
        try:
            if replacement.existed:
                assert replacement.backup is not None
                os.replace(replacement.backup, replacement.destination)
                replacement.backup = None
            else:
                replacement.destination.unlink(missing_ok=True)
        except OSError as error:
            if rollback_error is None:
                rollback_error = error
    if rollback_error is not None:
        raise OSError(f"failed to roll back job outputs: {rollback_error}") from rollback_error


def _commit_complete(
    manifest_path: Path,
    updated_manifest: dict[str, Any],
    destinations: list[Path],
    source_data: bytes,
) -> None:
    replacements: list[_StagedReplacement] = []
    try:
        for destination in destinations:
            replacement = _stage_replacement(destination, source_data)
            replacements.append(replacement)
        for replacement in replacements:
            os.replace(replacement.staged, replacement.destination)
            replacement.committed = True
        write_json(manifest_path, updated_manifest)
    except BaseException as error:
        try:
            _rollback_replacements(replacements)
        except OSError as rollback_error:
            raise rollback_error from error
        raise
    finally:
        _cleanup_staged(replacements)


def _restore_quarantine(quarantine: Path, source_path: Path) -> None:
    try:
        os.link(quarantine, source_path)
    except OSError:
        return
    quarantine.unlink()


def _remove_source_if_unchanged(
    snapshot: _SourceSnapshot,
    *,
    codex_home: Path | None,
    copied_destinations: set[Path],
) -> None:
    generated_images = resolve_codex_home(codex_home) / "generated_images"
    if not is_relative_to(snapshot.path, generated_images) or snapshot.path in copied_destinations:
        return
    descriptor, quarantine_name = tempfile.mkstemp(
        dir=snapshot.path.parent,
        prefix=f".{snapshot.path.name}.quarantine.",
        suffix=".tmp",
    )
    os.close(descriptor)
    quarantine = Path(quarantine_name)
    try:
        os.replace(snapshot.path, quarantine)
    except FileNotFoundError:
        quarantine.unlink(missing_ok=True)
        return
    except BaseException:
        quarantine.unlink(missing_ok=True)
        raise

    try:
        quarantined_stat = os.lstat(quarantine)
        if _is_reparse_point(quarantined_stat) or not stat.S_ISREG(quarantined_stat.st_mode):
            _restore_quarantine(quarantine, snapshot.path)
            return
        quarantined_digest = hashlib.sha256(quarantine.read_bytes()).digest()
    except OSError:
        _restore_quarantine(quarantine, snapshot.path)
        return
    if quarantined_digest == snapshot.digest:
        quarantine.unlink()
    else:
        _restore_quarantine(quarantine, snapshot.path)


def record_complete(
    run_dir: Path,
    job_id: str,
    source: Path,
    *,
    force: bool = False,
    remove_source: bool = False,
    codex_home: Path | None = None,
) -> Path:
    run_path = _resolve_run_dir(Path(run_dir))
    source_snapshot = _read_png_source(Path(source))
    preview: Path | None = None
    with _record_lock(run_path):
        manifest_path, manifest, jobs_by_id, output_paths = _load_manifest(run_path)
        if job_id not in jobs_by_id:
            raise ValueError(f"unknown job id: {job_id}")
        job = jobs_by_id[job_id]
        if job["status"] == "complete" and not force:
            raise ValueError(f"job {job_id!r} is already complete; pass force to replace it")
        incomplete_dependencies = [
            dependency
            for dependency in job["depends_on"]
            if jobs_by_id[dependency]["status"] != "complete"
        ]
        if incomplete_dependencies:
            raise ValueError(
                f"job {job_id!r} has incomplete dependencies: {', '.join(incomplete_dependencies)}"
            )

        destination = output_paths[job_id]
        destinations = [destination]
        if job_id == "base":
            preview = _preview_path(run_path)
            destinations.append(preview)
        updated_manifest = copy.deepcopy(manifest)
        selected = next(
            candidate for candidate in updated_manifest["jobs"] if candidate["id"] == job_id
        )
        selected["status"] = "complete"
        selected["attempts"] += 1
        selected["last_error"] = None
        selected["source_basename"] = source_snapshot.basename
        selected["completed_at"] = _utc_timestamp()
        _commit_complete(manifest_path, updated_manifest, destinations, source_snapshot.data)

    if remove_source:
        copied_destinations = {destination.resolve()}
        if preview is not None:
            copied_destinations.add(preview.resolve())
        _remove_source_if_unchanged(
            source_snapshot,
            codex_home=codex_home,
            copied_destinations=copied_destinations,
        )
    return destination


def record_failure(run_dir: Path, job_id: str, message: str) -> None:
    if not isinstance(message, str):
        raise ValueError("failure message must be text")
    run_path = _resolve_run_dir(Path(run_dir))
    single_line = " ".join(message.splitlines())[:MAX_ERROR_LENGTH]
    with _record_lock(run_path):
        manifest_path, manifest, jobs_by_id, _ = _load_manifest(run_path)
        if job_id not in jobs_by_id:
            raise ValueError(f"unknown job id: {job_id}")
        if jobs_by_id[job_id]["status"] == "complete":
            raise ValueError(f"job {job_id!r} is complete and cannot be marked failed")

        updated_manifest = copy.deepcopy(manifest)
        selected = next(
            candidate for candidate in updated_manifest["jobs"] if candidate["id"] == job_id
        )
        selected["status"] = "failed"
        selected["attempts"] += 1
        selected["last_error"] = single_line
        selected["failed_at"] = _utc_timestamp()
        write_json(manifest_path, updated_manifest)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record an image generation job result.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    complete = subparsers.add_parser("complete", help="Copy a generated PNG and complete a job.")
    complete.add_argument("--run-dir", type=Path, required=True)
    complete.add_argument("--job-id", required=True)
    complete.add_argument("--source", type=Path, required=True)
    complete.add_argument("--force", action="store_true")
    complete.add_argument("--remove-source", action="store_true")
    complete.add_argument("--codex-home", type=Path)

    fail = subparsers.add_parser("fail", help="Record an image generation failure.")
    fail.add_argument("--run-dir", type=Path, required=True)
    fail.add_argument("--job-id", required=True)
    fail.add_argument("--message", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "complete":
            output = record_complete(
                args.run_dir,
                args.job_id,
                args.source,
                force=args.force,
                remove_source=args.remove_source,
                codex_home=args.codex_home,
            )
            summary = {"ok": True, "job_id": args.job_id, "output": str(output)}
        else:
            record_failure(args.run_dir, args.job_id, args.message)
            summary = {"ok": True, "job_id": args.job_id, "status": "failed"}
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

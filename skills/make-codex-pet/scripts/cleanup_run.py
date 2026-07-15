from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any

import pet_common
import validate_pet


SCHEMA_VERSION = 1
SUMMARY_MAX_BYTES = 1024 * 1024
REQUEST_MAX_BYTES = 1024 * 1024
_FINAL_KEEP = {"spritesheet.png", "validation.json"}
_QA_KEEP = {
    "character-preview.png",
    "contact-sheet.png",
    "previews",
    "run-summary.json",
}
_PREVIEW_KEEP = {f"{state}.gif" for state, _ in pet_common.ANIMATION_ROWS}
_ROOT_KEEP = {"pet_request_summary.json", "build_state.json", "final", "qa"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _lstat_optional(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise ValueError(f"path could not be inspected safely: {path}: {error}") from error


def _reject_reparse(path: Path, file_stat: os.stat_result) -> None:
    if stat.S_ISLNK(file_stat.st_mode) or _is_reparse_point(file_stat):
        raise ValueError(f"cleanup target must not be a symbolic link or reparse point: {path}")


def _load_json_snapshot(path: Path, max_bytes: int, root: Path, label: str) -> dict[str, Any]:
    try:
        payload = validate_pet._read_regular_snapshot(path, max_bytes, root)
    except validate_pet.SnapshotError as error:
        raise ValueError(f"{label} is unsafe or unreadable: {error}") from error
    try:
        value = json.loads(payload)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError, RecursionError) as error:
        raise ValueError(f"{label} is invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _assert_plain_directory(path: Path) -> None:
    file_stat = _lstat_optional(path)
    if file_stat is None:
        raise ValueError(f"directory is missing: {path}")
    _reject_reparse(path, file_stat)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise ValueError(f"path must be an ordinary directory: {path}")


def _assert_no_reparse_tree(root: Path) -> None:
    _assert_plain_directory(root)
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise ValueError(f"cleanup target could not be inspected: {current}: {error}") from error
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"cleanup target could not be inspected: {entry_path}: {error}") from error
            _reject_reparse(entry_path, entry_stat)
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(entry_path)


def _validate_cleanup_target(path: Path) -> None:
    file_stat = _lstat_optional(path)
    if file_stat is None:
        return
    _reject_reparse(path, file_stat)
    if stat.S_ISDIR(file_stat.st_mode):
        _assert_no_reparse_tree(path)
    elif not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"cleanup target must be a regular file or directory: {path}")


def _validate_atomic_output(path: Path) -> None:
    file_stat = _lstat_optional(path)
    if file_stat is None:
        return
    _reject_reparse(path, file_stat)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"summary output must be a regular file or absent: {path}")


def _installed_summary(run_dir: Path) -> tuple[dict[str, Any], Path]:
    summary = _load_json_snapshot(
        run_dir / "qa" / "run-summary.json",
        SUMMARY_MAX_BYTES,
        run_dir,
        "qa/run-summary.json",
    )
    if summary.get("ok") is not True:
        raise ValueError("cleanup requires a successful install summary")
    package_value = summary.get("package")
    if not isinstance(package_value, str) or not package_value.strip():
        raise ValueError("cleanup requires an installed package path")
    package_input = Path(package_value).expanduser()
    if not package_input.is_absolute():
        raise ValueError("installed package path must be absolute")
    package = _absolute_path(package_input)
    package_stat = _lstat_optional(package)
    if package_stat is None:
        raise ValueError("cleanup requires the installed package to exist")
    _reject_reparse(package, package_stat)
    if not stat.S_ISDIR(package_stat.st_mode):
        raise ValueError("installed package must be an ordinary directory")
    return summary, package


def _redacted_request_summary(request: dict[str, Any], cleaned_at: str) -> dict[str, Any]:
    pet = request.get("pet")
    if not isinstance(pet, dict):
        raise ValueError("pet_request.json pet must be an object")
    pet_summary: dict[str, str] = {}
    for key in ("id", "display_name", "description"):
        value = pet.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"pet_request.json pet {key} must be nonempty")
        pet_summary[key] = value.strip()
    style = pet.get("style")
    if not isinstance(style, str) or not style.strip():
        raise ValueError("pet_request.json pet style must be nonempty")
    features = request.get("identity_features")
    if not isinstance(features, list) or not all(
        isinstance(feature, str) and feature.strip() for feature in features
    ):
        raise ValueError("pet_request.json identity_features must be nonempty strings")
    references = request.get("references")
    if not isinstance(references, list) or not all(
        isinstance(reference, str) and reference.strip() for reference in references
    ):
        raise ValueError("pet_request.json references must be path strings")
    basenames = [Path(reference).name for reference in references]
    if any(not basename for basename in basenames):
        raise ValueError("pet_request.json references must have basenames")
    return {
        "schema_version": SCHEMA_VERSION,
        "pet": pet_summary,
        "style": style.strip(),
        "identity_features": [feature.strip() for feature in features],
        "reference_count": len(references),
        "reference_basenames": basenames,
        "cleaned_at": cleaned_at,
    }


def _is_pure_basename(value: str) -> bool:
    if not value or value in {".", ".."} or "\0" in value:
        return False
    if "/" in value or "\\" in value:
        return False
    drive, _ = os.path.splitdrive(value)
    return not drive and not Path(value).is_absolute() and Path(value).name == value


def _rebuild_redacted_summary(existing: dict[str, Any], cleaned_at: str) -> dict[str, Any]:
    if existing.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("pet_request_summary.json schema_version is invalid")
    pet = existing.get("pet")
    if not isinstance(pet, dict):
        raise ValueError("pet_request_summary.json pet must be an object")
    rebuilt_pet: dict[str, str] = {}
    for key in ("id", "display_name", "description"):
        value = pet.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"pet_request_summary.json pet {key} must be nonempty")
        rebuilt_pet[key] = value.strip()
    style = existing.get("style")
    if not isinstance(style, str) or not style.strip():
        raise ValueError("pet_request_summary.json style must be nonempty")
    features = existing.get("identity_features")
    if not isinstance(features, list) or not all(
        isinstance(feature, str) and feature.strip() for feature in features
    ):
        raise ValueError("pet_request_summary.json identity_features are invalid")
    basenames = existing.get("reference_basenames")
    if not isinstance(basenames, list) or not all(
        isinstance(basename, str) and _is_pure_basename(basename)
        for basename in basenames
    ):
        raise ValueError("pet_request_summary.json reference basename is invalid")
    reference_count = existing.get("reference_count")
    if (
        isinstance(reference_count, bool)
        or not isinstance(reference_count, int)
        or reference_count < 0
        or reference_count != len(basenames)
    ):
        raise ValueError("pet_request_summary.json reference count is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "pet": rebuilt_pet,
        "style": style.strip(),
        "identity_features": [feature.strip() for feature in features],
        "reference_count": reference_count,
        "reference_basenames": list(basenames),
        "cleaned_at": cleaned_at,
    }


def _write_or_validate_redacted_summary(run_dir: Path, cleaned_at: str) -> None:
    request_path = run_dir / "pet_request.json"
    request_stat = _lstat_optional(request_path)
    output = run_dir / "pet_request_summary.json"
    _validate_atomic_output(output)
    if request_stat is not None:
        request = _load_json_snapshot(
            request_path,
            REQUEST_MAX_BYTES,
            run_dir,
            "pet_request.json",
        )
        pet_common.write_json(output, _redacted_request_summary(request, cleaned_at))
        return
    existing = _load_json_snapshot(
        output,
        SUMMARY_MAX_BYTES,
        run_dir,
        "pet_request_summary.json",
    )
    pet_common.write_json(output, _rebuild_redacted_summary(existing, cleaned_at))


def _list_pending_staging(run_dir: Path) -> list[Path]:
    pending: list[Path] = []
    try:
        entries = list(os.scandir(run_dir))
    except OSError as error:
        raise ValueError(f"run directory could not be inspected: {error}") from error
    for entry in entries:
        if entry.name.startswith(".cleanup-staging-"):
            pending.append(Path(entry.path))
    return sorted(pending, key=lambda path: path.name)


def _safe_remove_cleanup_staging(path: Path, run_dir: Path) -> None:
    try:
        relative = path.relative_to(run_dir)
    except ValueError as error:
        raise ValueError(f"refusing to delete cleanup staging outside run: {path}") from error
    if len(relative.parts) != 1 or not path.name.startswith(".cleanup-staging-"):
        raise ValueError(f"refusing to delete unrecognized cleanup staging: {path}")
    if _lstat_optional(path) is None:
        return
    _assert_no_reparse_tree(path)
    shutil.rmtree(path)


def _cleanup_targets(run_dir: Path) -> list[Path]:
    targets: list[Path] = []
    try:
        entries = list(os.scandir(run_dir))
    except OSError as error:
        raise ValueError(f"run directory could not be inspected: {error}") from error
    for entry in entries:
        if entry.name not in _ROOT_KEEP and not entry.name.startswith(
            ".cleanup-staging-"
        ):
            targets.append(Path(entry.path))

    for directory, keep_names in (
        (run_dir / "final", _FINAL_KEEP),
        (run_dir / "qa", _QA_KEEP),
        (run_dir / "qa" / "previews", _PREVIEW_KEEP),
    ):
        directory_stat = _lstat_optional(directory)
        if directory_stat is None:
            continue
        _reject_reparse(directory, directory_stat)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise ValueError(f"tool output path must be an ordinary directory: {directory}")
        try:
            directory_entries = list(os.scandir(directory))
        except OSError as error:
            raise ValueError(f"tool output directory could not be inspected: {directory}: {error}") from error
        for entry in directory_entries:
            entry_path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"tool output could not be inspected: {entry_path}: {error}") from error
            _reject_reparse(entry_path, entry_stat)
            if entry.name not in keep_names:
                targets.append(entry_path)
    existing: list[Path] = []
    seen: set[str] = set()
    for target in targets:
        normalized = os.path.normcase(str(target))
        if normalized in seen or _lstat_optional(target) is None:
            continue
        seen.add(normalized)
        existing.append(target)
    for target in existing:
        _validate_cleanup_target(target)
    return existing


def _isolate_targets(run_dir: Path, targets: list[Path]) -> Path | None:
    if not targets:
        return None
    staging = Path(tempfile.mkdtemp(prefix=".cleanup-staging-", dir=run_dir))
    moved: list[tuple[Path, Path]] = []
    try:
        for source in targets:
            destination = staging / source.relative_to(run_dir)
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(source, destination)
            moved.append((source, destination))
    except BaseException as error:
        rollback_errors: list[str] = []
        for source, destination in reversed(moved):
            try:
                os.replace(destination, source)
            except OSError as rollback_error:
                rollback_errors.append(f"{source.name}: {rollback_error}")
        if not rollback_errors:
            try:
                shutil.rmtree(staging)
            except OSError as cleanup_error:
                rollback_errors.append(f"staging: {cleanup_error}")
        if rollback_errors:
            raise OSError("cleanup isolation rollback failed: " + "; ".join(rollback_errors)) from error
        raise
    return staging


def _sanitized_run_summary(
    run_dir: Path,
    installed: dict[str, Any],
    package: Path,
    cleanup_state: dict[str, Any],
) -> dict[str, Any]:
    backup = installed.get("backup")
    if backup is not None and not isinstance(backup, str):
        backup = None
    installed_at = installed.get("installed_at")
    if not isinstance(installed_at, str):
        installed_at = ""
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "run_dir": str(run_dir),
        "spritesheet": str(run_dir / "final" / "spritesheet.png"),
        "validation": str(run_dir / "final" / "validation.json"),
        "contact_sheet": str(run_dir / "qa" / "contact-sheet.png"),
        "previews": {
            state: str(run_dir / "qa" / "previews" / f"{state}.gif")
            for state, _ in pet_common.ANIMATION_ROWS
        },
        "package": str(package),
        "backup": backup,
        "installed_at": installed_at,
        "cleanup": cleanup_state,
    }


def _finish_cleanup(
    run_dir: Path,
    installed: dict[str, Any],
    package: Path,
    cleaned_at: str,
    warnings: list[str],
    pending_paths: list[Path],
) -> dict[str, Any]:
    pending = bool(pending_paths)
    status = "pending" if pending else "complete"
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "ok": not pending,
        "status": status,
        "run_dir": str(run_dir),
        "cleaned_at": cleaned_at,
    }
    cleanup_state: dict[str, Any] = {
        "ok": not pending,
        "status": status,
        "cleaned_at": cleaned_at,
    }
    if warnings:
        result["warnings"] = warnings
        cleanup_state["warnings"] = warnings
    if pending_paths:
        pending_path = str(pending_paths[0])
        result["cleanup_pending_path"] = pending_path
        cleanup_state["cleanup_pending_path"] = pending_path

    run_summary_path = run_dir / "qa" / "run-summary.json"
    _validate_atomic_output(run_summary_path)
    pet_common.write_json(
        run_summary_path,
        _sanitized_run_summary(run_dir, installed, package, cleanup_state),
    )
    return result


def cleanup_run(run_dir: Path) -> dict[str, Any]:
    """Isolate and remove sensitive/reproducible artifacts from an installed run."""
    selected_run_dir = _absolute_path(run_dir)
    installed, package = _installed_summary(selected_run_dir)
    warnings: list[str] = []
    pending_paths: list[Path] = []

    for pending in _list_pending_staging(selected_run_dir):
        try:
            _safe_remove_cleanup_staging(pending, selected_run_dir)
        except OSError as error:
            warnings.append(f"pending cleanup could not be removed: {error}")
            pending_paths.append(pending)

    cleaned_at = _utc_now()
    if pending_paths:
        return _finish_cleanup(
            selected_run_dir,
            installed,
            package,
            cleaned_at,
            warnings,
            pending_paths,
        )

    _write_or_validate_redacted_summary(selected_run_dir, cleaned_at)
    targets = _cleanup_targets(selected_run_dir)
    staging = _isolate_targets(selected_run_dir, targets)
    if staging is not None:
        try:
            _safe_remove_cleanup_staging(staging, selected_run_dir)
        except OSError as error:
            warnings.append(f"isolated cleanup could not be deleted: {error}")
            pending_paths.append(staging)

    return _finish_cleanup(
        selected_run_dir,
        installed,
        package,
        cleaned_at,
        warnings,
        pending_paths,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean sensitive Codex pet run artifacts")
    parser.add_argument("run_dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = cleanup_run(arguments.run_dir)
    except (OSError, ValueError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if not summary["ok"]:
        print(
            f"warning: cleanup is pending at {summary['cleanup_pending_path']}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

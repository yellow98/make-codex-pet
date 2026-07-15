from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any

import pet_common
import validate_pet


SCHEMA_VERSION = 1
REQUEST_MAX_BYTES = 1024 * 1024
PET_JSON_MAX_BYTES = 64 * 1024
_SAFE_ID = re.compile(r"[a-z0-9-]{1,64}\Z")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp_for_path(when: str) -> str:
    parsed = datetime.fromisoformat(when.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
        raise ValueError(f"path must not be a symbolic link or reparse point: {path}")


def _assert_plain_directory(path: Path, *, allow_absent: bool = False) -> bool:
    file_stat = _lstat_optional(path)
    if file_stat is None:
        if allow_absent:
            return False
        raise ValueError(f"directory is missing: {path}")
    _reject_reparse(path, file_stat)
    if not stat.S_ISDIR(file_stat.st_mode):
        raise ValueError(f"path must be an ordinary directory: {path}")
    return True


def _assert_no_reparse_tree(root: Path) -> None:
    """Reject links and reparse points anywhere under an existing directory."""
    _assert_plain_directory(root)
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as error:
            raise ValueError(f"directory could not be inspected safely: {current}: {error}") from error
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise ValueError(f"path could not be inspected safely: {entry_path}: {error}") from error
            _reject_reparse(entry_path, entry_stat)
            if stat.S_ISDIR(entry_stat.st_mode):
                pending.append(entry_path)


def _validate_atomic_output(path: Path) -> None:
    file_stat = _lstat_optional(path)
    if file_stat is None:
        return
    _reject_reparse(path, file_stat)
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError(f"output must be a regular file or absent: {path}")


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


def _validated_pet_request(run_dir: Path) -> dict[str, str]:
    request = _load_json_snapshot(
        run_dir / "pet_request.json",
        REQUEST_MAX_BYTES,
        run_dir,
        "pet_request.json",
    )
    pet = request.get("pet")
    if not isinstance(pet, dict):
        raise ValueError("pet_request.json pet must be an object")
    pet_id = pet.get("id")
    if (
        not isinstance(pet_id, str)
        or _SAFE_ID.fullmatch(pet_id) is None
        or not any(character.isalnum() for character in pet_id)
    ):
        raise ValueError(
            "pet id must be 1..64 lowercase a-z, digits, or hyphens "
            "and contain a letter or digit"
        )
    display_name = pet.get("display_name")
    if not isinstance(display_name, str) or not display_name.strip():
        raise ValueError("pet display_name must be nonempty")
    description = pet.get("description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError("pet description must be nonempty")
    return {
        "id": pet_id,
        "displayName": display_name.strip(),
        "description": description.strip(),
        "spritesheetPath": "spritesheet.png",
    }


def _write_bytes_atomic(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _safe_remove_staging(staging: Path, pets_dir: Path) -> None:
    try:
        staging.relative_to(pets_dir)
    except ValueError as error:
        raise ValueError(f"refusing to remove staging outside pets directory: {staging}") from error
    if not staging.name.startswith(".install-staging-"):
        raise ValueError(f"refusing to remove unrecognized staging directory: {staging}")
    if _lstat_optional(staging) is None:
        return
    _assert_no_reparse_tree(staging)
    shutil.rmtree(staging)


def _prepare_pets_directory(codex_home: Path) -> Path:
    skill_dir = Path(__file__).resolve().parents[1]
    try:
        codex_home.relative_to(skill_dir)
    except ValueError:
        pass
    else:
        raise ValueError("Codex home must not be inside the program installation directory")

    codex_home.mkdir(parents=True, exist_ok=True)
    _assert_plain_directory(codex_home)
    pets_dir = codex_home / "pets"
    pets_stat = _lstat_optional(pets_dir)
    if pets_stat is None:
        pets_dir.mkdir()
    else:
        _reject_reparse(pets_dir, pets_stat)
        if not stat.S_ISDIR(pets_stat.st_mode):
            raise ValueError(f"pets path must be an ordinary directory: {pets_dir}")
    _assert_plain_directory(pets_dir)
    return pets_dir


def _unique_backup_path(codex_home: Path, pet_id: str, installed_at: str) -> Path:
    backups_dir = codex_home / "pet-backups"
    backups_stat = _lstat_optional(backups_dir)
    if backups_stat is None:
        backups_dir.mkdir()
    else:
        _reject_reparse(backups_dir, backups_stat)
        if not stat.S_ISDIR(backups_stat.st_mode):
            raise ValueError(f"pet-backups path must be an ordinary directory: {backups_dir}")
    stem = f"{pet_id}-{_timestamp_for_path(installed_at)}"
    candidate = backups_dir / stem
    suffix = 1
    while _lstat_optional(candidate) is not None:
        candidate = backups_dir / f"{stem}-{suffix}"
        suffix += 1
    return candidate


def _verify_package(package: Path, expected_pet: dict[str, str], atlas_sha256: str) -> None:
    _assert_no_reparse_tree(package)
    entries = {entry.name for entry in os.scandir(package)}
    if entries != {"pet.json", "spritesheet.png"}:
        raise ValueError("installed package must contain exactly pet.json and spritesheet.png")
    installed_pet = _load_json_snapshot(
        package / "pet.json",
        PET_JSON_MAX_BYTES,
        package,
        "installed pet.json",
    )
    if installed_pet != expected_pet or set(installed_pet) != {
        "id",
        "displayName",
        "description",
        "spritesheetPath",
    }:
        raise ValueError("installed pet.json does not match the required schema")
    try:
        installed_atlas = validate_pet._read_regular_snapshot(
            package / "spritesheet.png",
            pet_common.MAX_ATLAS_BYTES,
            package,
        )
    except validate_pet.SnapshotError as error:
        raise ValueError(f"installed spritesheet is unsafe or unreadable: {error}") from error
    if hashlib.sha256(installed_atlas).hexdigest() != atlas_sha256:
        raise ValueError("installed spritesheet SHA256 does not match the validated atlas")


def _rollback_publish(
    *,
    target: Path,
    staging: Path,
    backup: Path | None,
    old_moved: bool,
    new_published: bool,
) -> None:
    rollback_errors: list[str] = []
    if new_published and _lstat_optional(target) is not None:
        try:
            os.replace(target, staging)
        except OSError as error:
            rollback_errors.append(f"could not quarantine failed package: {error}")
    if old_moved and backup is not None and _lstat_optional(backup) is not None:
        try:
            os.replace(backup, target)
        except OSError as error:
            rollback_errors.append(f"could not restore old package: {error}")
    if rollback_errors:
        raise OSError("install rollback failed: " + "; ".join(rollback_errors))


def _ensure_summary_output(run_dir: Path) -> Path:
    qa_dir = run_dir / "qa"
    qa_stat = _lstat_optional(qa_dir)
    if qa_stat is None:
        qa_dir.mkdir()
    else:
        _reject_reparse(qa_dir, qa_stat)
        if not stat.S_ISDIR(qa_stat.st_mode):
            raise ValueError(f"qa path must be an ordinary directory: {qa_dir}")
    output = qa_dir / "run-summary.json"
    _validate_atomic_output(output)
    return output


def install_pet(
    run_dir: Path,
    codex_home: Path | None = None,
) -> dict[str, Any]:
    """Validate, stage, atomically publish, and verify one Codex pet package."""
    selected_run_dir = _absolute_path(run_dir)
    atlas_path = selected_run_dir / "final" / "spritesheet.png"
    validation_path = selected_run_dir / "final" / "validation.json"

    report = validate_pet.validate_atlas(atlas_path, require_build_state=True)
    _validate_atomic_output(validation_path)
    validate_pet.write_validation_report(report, validation_path)
    if not report.get("ok"):
        first_error = report.get("errors", ["unknown validation error"])
        detail = first_error[0] if first_error else "unknown validation error"
        raise ValueError(f"atlas validation failed: {detail}")

    try:
        atlas_payload = validate_pet._read_regular_snapshot(
            atlas_path,
            pet_common.MAX_ATLAS_BYTES,
            selected_run_dir,
        )
    except validate_pet.SnapshotError as error:
        raise ValueError(f"atlas snapshot is unsafe or unreadable: {error}") from error
    atlas_sha256 = hashlib.sha256(atlas_payload).hexdigest()
    if atlas_sha256 != report.get("sha256"):
        raise ValueError("atlas SHA256 changed after validation")

    pet_json = _validated_pet_request(selected_run_dir)
    summary_output = _ensure_summary_output(selected_run_dir)
    selected_codex_home = pet_common.resolve_codex_home(codex_home)
    pets_dir = _prepare_pets_directory(selected_codex_home)
    target = pets_dir / pet_json["id"]
    target_stat = _lstat_optional(target)
    if target_stat is not None:
        _reject_reparse(target, target_stat)
        if not stat.S_ISDIR(target_stat.st_mode):
            raise ValueError(f"existing pet target must be an ordinary directory: {target}")
        _assert_no_reparse_tree(target)

    staging = Path(tempfile.mkdtemp(prefix=".install-staging-", dir=pets_dir))
    backup: Path | None = None
    old_moved = False
    new_published = False
    committed = False
    try:
        pet_common.write_json(staging / "pet.json", pet_json)
        _write_bytes_atomic(staging / "spritesheet.png", atlas_payload)
        _fsync_directory(staging)
        _verify_package(staging, pet_json, atlas_sha256)

        installed_at = _utc_now()
        if target_stat is not None:
            backup = _unique_backup_path(selected_codex_home, pet_json["id"], installed_at)
            os.replace(target, backup)
            old_moved = True
        os.replace(staging, target)
        new_published = True
        _fsync_directory(pets_dir)
        _verify_package(target, pet_json, atlas_sha256)

        summary = {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "run_dir": str(selected_run_dir),
            "spritesheet": str(atlas_path),
            "validation": str(validation_path),
            "contact_sheet": str(selected_run_dir / "qa" / "contact-sheet.png"),
            "previews": {
                state: str(selected_run_dir / "qa" / "previews" / f"{state}.gif")
                for state, _ in pet_common.ANIMATION_ROWS
            },
            "package": str(target),
            "backup": None if backup is None else str(backup),
            "installed_at": installed_at,
        }
        pet_common.write_json(summary_output, summary)
        committed = True
        return summary
    except BaseException as error:
        try:
            _rollback_publish(
                target=target,
                staging=staging,
                backup=backup,
                old_moved=old_moved,
                new_published=new_published,
            )
        except OSError as rollback_error:
            raise rollback_error from error
        raise
    finally:
        if not committed and _lstat_optional(staging) is not None:
            _safe_remove_staging(staging, pets_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install a validated Codex pet")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--codex-home", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        summary = install_pet(arguments.run_dir, arguments.codex_home)
    except (OSError, ValueError, TypeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

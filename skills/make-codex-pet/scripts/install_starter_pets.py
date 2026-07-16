from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
import uuid

import pet_common
import validate_pet


STARTER_PET_IDS = (
    "classic-messi",
    "classic-ronaldo",
    "classic-elon-musk",
    "classic-sam-altman",
)
PET_JSON_FIELDS = {"id", "displayName", "description", "spritesheetPath"}


def _default_assets_root() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "starter-pets"


def _validate_source_packages(assets_root: Path) -> dict[str, tuple[dict[str, str], bytes, bytes]]:
    if not assets_root.is_dir():
        raise ValueError(f"starter pet assets directory does not exist: {assets_root}")

    packages: dict[str, tuple[dict[str, str], bytes, bytes]] = {}
    for pet_id in STARTER_PET_IDS:
        package = assets_root / pet_id
        if not package.is_dir():
            raise ValueError(f"starter pet package is missing: {pet_id}")
        entries = {entry.name for entry in package.iterdir()}
        if entries != {"pet.json", "spritesheet.png"}:
            raise ValueError(f"starter pet package {pet_id} must contain exactly pet.json and spritesheet.png")

        pet_bytes = (package / "pet.json").read_bytes()
        try:
            pet = json.loads(pet_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"starter pet package {pet_id} has invalid pet.json") from error
        if not isinstance(pet, dict) or set(pet) != PET_JSON_FIELDS:
            raise ValueError(f"starter pet package {pet_id} has an invalid pet.json schema")
        if not all(isinstance(value, str) and value.strip() for value in pet.values()):
            raise ValueError(f"starter pet package {pet_id} has empty pet.json fields")
        if pet["id"] != pet_id or pet["spritesheetPath"] != "spritesheet.png":
            raise ValueError(f"starter pet package {pet_id} has mismatched pet.json values")
        pet_common.slugify_pet_id(pet["displayName"], pet_id)

        atlas_path = package / "spritesheet.png"
        report = validate_pet.validate_atlas(atlas_path, require_build_state=False)
        if not report.get("ok"):
            errors = report.get("errors") or ["unknown validation error"]
            raise ValueError(f"starter pet package {pet_id} has an invalid atlas: {errors[0]}")
        packages[pet_id] = (pet, pet_bytes, atlas_path.read_bytes())
    return packages


def _package_matches(target: Path, pet_bytes: bytes, atlas_bytes: bytes) -> bool:
    if not target.is_dir():
        return False
    if {entry.name for entry in target.iterdir()} != {"pet.json", "spritesheet.png"}:
        return False
    return (
        (target / "pet.json").read_bytes() == pet_bytes
        and (target / "spritesheet.png").read_bytes() == atlas_bytes
    )


def _publish_package(target: Path, pet_bytes: bytes, atlas_bytes: bytes) -> None:
    pets_dir = target.parent
    staging = Path(tempfile.mkdtemp(prefix=".starter-staging-", dir=pets_dir))
    backup: Path | None = None
    try:
        (staging / "pet.json").write_bytes(pet_bytes)
        (staging / "spritesheet.png").write_bytes(atlas_bytes)
        if target.exists():
            if not target.is_dir():
                raise ValueError(f"existing pet target is not a directory: {target}")
            backup = pets_dir / f".starter-backup-{target.name}-{uuid.uuid4().hex}"
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except BaseException:
            if backup is not None:
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        if backup is not None and backup.exists() and not target.exists():
            os.replace(backup, target)


def install_starter_pets(
    codex_home: Path | None = None,
    assets_root: Path | None = None,
) -> dict[str, object]:
    """Validate and install the four bundled starter pets."""
    selected_assets = (_default_assets_root() if assets_root is None else Path(assets_root)).resolve()
    source_packages = _validate_source_packages(selected_assets)
    selected_codex_home = pet_common.resolve_codex_home(codex_home)
    pets_dir = selected_codex_home / "pets"
    pets_dir.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    unchanged: list[str] = []
    package_paths: dict[str, str] = {}
    for pet_id in STARTER_PET_IDS:
        _, pet_bytes, atlas_bytes = source_packages[pet_id]
        target = pets_dir / pet_id
        package_paths[pet_id] = str(target)
        if _package_matches(target, pet_bytes, atlas_bytes):
            unchanged.append(pet_id)
            continue
        _publish_package(target, pet_bytes, atlas_bytes)
        installed.append(pet_id)

    return {
        "ok": True,
        "installed": installed,
        "unchanged": unchanged,
        "packages": package_paths,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install bundled Make Codex Pet starter pets")
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--assets-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = install_starter_pets(args.codex_home, args.assets_root)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

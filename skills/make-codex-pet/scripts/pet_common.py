from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping
import unicodedata


ATLAS_COLUMNS = 8
ATLAS_ROWS = 9
CELL_WIDTH = 192
CELL_HEIGHT = 208
ATLAS_WIDTH = 1536
ATLAS_HEIGHT = 1872
MAX_ATLAS_BYTES = 20 * 1024 * 1024
MAX_PET_ID_LENGTH = 64
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

_SAFE_PET_ID = re.compile(r"[a-z0-9-]+\Z")
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")


def read_json(path: str | os.PathLike[str]) -> Any:
    with Path(path).open("r", encoding="utf-8") as source:
        return json.load(source)


def write_json(path: str | os.PathLike[str], value: Any) -> None:
    """Write UTF-8 JSON by replacing the destination with a complete temp file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def write_text_atomic(path: str | os.PathLike[str], value: str) -> None:
    """Write UTF-8 text by replacing the destination with a complete temp file."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            target.write(value)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def slugify_pet_id(display_name: str, pet_id: str | None = None) -> str:
    """Return a safe id, validating an explicit id when one is supplied."""
    if pet_id is not None:
        if len(pet_id) > MAX_PET_ID_LENGTH:
            raise ValueError(f"pet id must be at most {MAX_PET_ID_LENGTH} characters")
        if not pet_id or not _SAFE_PET_ID.fullmatch(pet_id) or not any(char.isalnum() for char in pet_id):
            raise ValueError("pet id must contain only lowercase a-z, digits, and hyphens")
        return pet_id

    name = display_name.strip()
    if not name:
        raise ValueError("pet name must not be empty")
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if slug:
        if len(slug) > MAX_PET_ID_LENGTH:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
            prefix_length = MAX_PET_ID_LENGTH - len(digest) - 1
            slug = f"{slug[:prefix_length].rstrip('-')}-{digest}"
        return slug
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
    return f"pet-{digest}"


def parse_hex_color(value: str) -> tuple[int, int, int]:
    if not _HEX_COLOR.fullmatch(value):
        raise ValueError("chroma key must use #RRGGBB hexadecimal format")
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def resolve_codex_home(
    explicit: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if environ is None else environ
    if explicit is not None and str(explicit).strip():
        selected = Path(explicit)
    elif environment.get("CODEX_HOME"):
        selected = Path(environment["CODEX_HOME"])
    else:
        selected = Path.home() / ".codex"
    return selected.expanduser().resolve()


def is_relative_to(path: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    candidate = Path(path).expanduser().resolve()
    base = Path(parent).expanduser().resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True

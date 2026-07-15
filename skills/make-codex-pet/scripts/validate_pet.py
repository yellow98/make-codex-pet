from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any
import warnings

from PIL import Image, UnidentifiedImageError

import pet_common
from pet_common import (
    ANIMATION_ROWS,
    ATLAS_COLUMNS,
    ATLAS_HEIGHT,
    ATLAS_WIDTH,
    CELL_HEIGHT,
    CELL_WIDTH,
    MAX_ATLAS_BYTES,
)


SCHEMA_VERSION = 1
BUILD_STATE_MAX_BYTES = 1024 * 1024
SNAPSHOT_CHUNK_BYTES = 64 * 1024
VISIBLE_ALPHA_THRESHOLD = 16
MIN_VISIBLE_PIXELS = 16
MIN_VISIBLE_BBOX_SIDE = 2
_VISIBLE_ALPHA_LUT = [
    0 if alpha < VISIBLE_ALPHA_THRESHOLD else 255 for alpha in range(256)
]


class SnapshotError(ValueError):
    """Raised when a path cannot be read as one bounded, stable file snapshot."""

    def __init__(self, message: str, *, file_size: int | None = None) -> None:
        super().__init__(message)
        self.file_size = file_size


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(Path(path).expanduser())))


def _empty_report(path: Path, max_bytes: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "atlas_path": str(path),
        "checked_at": _utc_now(),
        "format": None,
        "mode": None,
        "dimensions": [None, None],
        "file_size": None,
        "max_file_size": max_bytes,
        "sha256": None,
        "build_id": None,
        "rows": [],
        "errors": [],
        "warnings": [],
    }


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _snapshot_mtime_ns(file_stat: os.stat_result) -> int:
    return getattr(file_stat, "st_mtime_ns", round(file_stat.st_mtime * 1_000_000_000))


def _same_snapshot_stat(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        _snapshot_mtime_ns(left),
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        _snapshot_mtime_ns(right),
    )


def _read_regular_snapshot(path: Path, max_bytes: int, root: Path) -> bytes:
    """Read one stable bounded snapshot after rejecting links from root to leaf."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    selected_path = _absolute_path(path)
    selected_root = _absolute_path(root)
    try:
        relative = selected_path.relative_to(selected_root)
    except ValueError as error:
        raise SnapshotError(f"path is outside snapshot root: {selected_path}") from error

    components = [selected_root]
    current = selected_root
    for part in relative.parts:
        current = current / part
        components.append(current)

    leaf_stat: os.stat_result | None = None
    for component in components:
        try:
            component_stat = os.lstat(component)
        except FileNotFoundError:
            break
        except (OSError, ValueError) as error:
            raise SnapshotError(f"path component could not be inspected: {component}: {error}") from error
        if stat.S_ISLNK(component_stat.st_mode) or _is_reparse_point(component_stat):
            raise SnapshotError(
                f"path contains a symbolic link or reparse point: {component}"
            )
        if component == selected_path:
            leaf_stat = component_stat
        elif not stat.S_ISDIR(component_stat.st_mode):
            raise SnapshotError(f"path ancestor must be a directory: {component}")

    if leaf_stat is None:
        try:
            leaf_stat = os.lstat(selected_path)
        except FileNotFoundError as error:
            raise SnapshotError(f"file is missing: {selected_path}") from error
        except (OSError, ValueError) as error:
            raise SnapshotError(f"file could not be inspected: {selected_path}: {error}") from error
        if stat.S_ISLNK(leaf_stat.st_mode) or _is_reparse_point(leaf_stat):
            raise SnapshotError(
                f"file must not be a symbolic link or reparse point: {selected_path}"
            )
    if not stat.S_ISREG(leaf_stat.st_mode):
        raise SnapshotError(f"file must be a regular file: {selected_path}", file_size=leaf_stat.st_size)
    if leaf_stat.st_size > max_bytes:
        raise SnapshotError(
            f"file size {leaf_stat.st_size} exceeds maximum {max_bytes} bytes",
            file_size=leaf_stat.st_size,
        )

    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(selected_path, flags)
    except (OSError, ValueError) as error:
        raise SnapshotError(f"file could not be opened safely: {selected_path}: {error}") from error
    try:
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            raise SnapshotError(f"opened file is not regular: {selected_path}")
        if not _same_snapshot_stat(leaf_stat, opened_stat):
            raise SnapshotError(
                f"file changed between lstat and open: {selected_path}",
                file_size=opened_stat.st_size,
            )

        chunks: list[bytes] = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(
                descriptor,
                min(SNAPSHOT_CHUNK_BYTES, max_bytes + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        finished_stat = os.fstat(descriptor)
        if not _same_snapshot_stat(opened_stat, finished_stat):
            raise SnapshotError(
                f"file changed while being read: {selected_path}",
                file_size=finished_stat.st_size,
            )
    except SnapshotError:
        raise
    except OSError as error:
        raise SnapshotError(f"file could not be read safely: {selected_path}: {error}") from error
    finally:
        os.close(descriptor)

    payload = b"".join(chunks)
    if len(payload) > max_bytes:
        raise SnapshotError(
            f"file size exceeds maximum {max_bytes} bytes",
            file_size=finished_stat.st_size,
        )
    if len(payload) != finished_stat.st_size:
        raise SnapshotError(
            f"file snapshot size changed while being read: {selected_path}",
            file_size=finished_stat.st_size,
        )
    return payload


def _check_build_state(run_dir: Path, report: dict[str, Any]) -> None:
    state_path = run_dir / "build_state.json"
    try:
        payload = _read_regular_snapshot(
            state_path,
            BUILD_STATE_MAX_BYTES,
            run_dir,
        )
    except SnapshotError as error:
        report["errors"].append(f"build_state.json is unsafe or unreadable: {error}")
        return
    try:
        state = json.loads(payload)
    except (
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        RecursionError,
    ) as error:
        report["errors"].append(f"build state JSON is invalid: {error}")
        return

    if not isinstance(state, dict):
        report["errors"].append(
            "build state JSON is invalid: top-level value must be an object"
        )
        return

    build_id = state.get("build_id")
    if isinstance(build_id, str) and build_id.strip():
        report["build_id"] = build_id
    else:
        report["errors"].append("build_state.json build_id must be nonempty")

    status = state.get("status")
    if status != "complete":
        report["errors"].append(
            f"build_state.json status must be complete, not {status!r}"
        )

    expected_sha256 = state.get("spritesheet_sha256")
    actual_sha256 = report["sha256"]
    if not isinstance(expected_sha256, str) or not expected_sha256:
        report["errors"].append("build_state.json spritesheet_sha256 must be nonempty")
    elif actual_sha256 is not None and expected_sha256 != actual_sha256:
        report["errors"].append(
            "build_state.json spritesheet_sha256 does not match the atlas SHA256"
        )


def _inspect_rows(image: Image.Image, report: dict[str, Any]) -> None:
    alpha = image.getchannel("A")
    rows: list[dict[str, Any]] = []
    for row_index, (state, expected_frames) in enumerate(ANIMATION_ROWS):
        occupied_cells: list[int] = []
        unexpected_cells: list[int] = []
        for column in range(ATLAS_COLUMNS):
            left = column * CELL_WIDTH
            top = row_index * CELL_HEIGHT
            cell_alpha = alpha.crop(
                (left, top, left + CELL_WIDTH, top + CELL_HEIGHT)
            )
            extrema = cell_alpha.getextrema()
            maximum_alpha = extrema[1] if extrema is not None else 0
            occupied = maximum_alpha > 0
            if occupied:
                occupied_cells.append(column)

            if column < expected_frames:
                histogram = cell_alpha.histogram()
                visible_pixels = sum(histogram[VISIBLE_ALPHA_THRESHOLD:])
                visible_alpha = cell_alpha.point(_VISIBLE_ALPHA_LUT)
                bounding_box = visible_alpha.getbbox()
                bbox_width = 0 if bounding_box is None else bounding_box[2] - bounding_box[0]
                bbox_height = 0 if bounding_box is None else bounding_box[3] - bounding_box[1]
                readable = (
                    visible_pixels >= MIN_VISIBLE_PIXELS
                    and bbox_width >= MIN_VISIBLE_BBOX_SIDE
                    and bbox_height >= MIN_VISIBLE_BBOX_SIDE
                )
                if not readable:
                    report["errors"].append(
                        f"required cell {state}[{column}] is not readable: "
                        f"alpha>={VISIBLE_ALPHA_THRESHOLD} pixels={visible_pixels}, "
                        f"bbox={bbox_width}x{bbox_height}"
                    )
                    continue
                if bounding_box is not None:
                    bbox_left, bbox_top, bbox_right, bbox_bottom = bounding_box
                    if (
                        bbox_left == 0
                        or bbox_top == 0
                        or bbox_right == CELL_WIDTH
                        or bbox_bottom == CELL_HEIGHT
                    ):
                        report["warnings"].append(
                            f"required cell {state}[{column}] alpha bbox touches a cell boundary; "
                            "sprite may be clipped"
                        )
            elif occupied:
                unexpected_cells.append(column)
                report["errors"].append(
                    f"unused cell {state}[{column}] must be transparent; max alpha is {maximum_alpha}"
                )

        rows.append(
            {
                "state": state,
                "expected_frames": expected_frames,
                "occupied_cells": occupied_cells,
                "unexpected_cells": unexpected_cells,
            }
        )
    report["rows"] = rows


def _inspect_image(payload: bytes, report: dict[str, Any]) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as verifying_image:
                report["format"] = verifying_image.format
                report["mode"] = verifying_image.mode
                report["dimensions"] = [verifying_image.width, verifying_image.height]
                header_errors: list[str] = []
                if verifying_image.format != "PNG":
                    header_errors.append(
                        f"atlas format must be PNG, not {verifying_image.format!r}"
                    )
                if verifying_image.mode != "RGBA":
                    header_errors.append(
                        f"atlas mode must be RGBA, not {verifying_image.mode!r}"
                    )
                if verifying_image.size != (ATLAS_WIDTH, ATLAS_HEIGHT):
                    header_errors.append(
                        f"atlas dimensions must be {ATLAS_WIDTH}x{ATLAS_HEIGHT}, "
                        f"not {verifying_image.width}x{verifying_image.height}"
                    )
                frame_count = getattr(verifying_image, "n_frames", 1)
                if frame_count != 1 or bool(
                    getattr(verifying_image, "is_animated", False)
                ):
                    header_errors.append(
                        f"atlas must be a single-frame PNG; found {frame_count} frames or animation"
                    )
                report["errors"].extend(header_errors)
                if header_errors:
                    return
                verifying_image.verify()

            with Image.open(BytesIO(payload)) as image:
                image.load()
                if len(image.getexif()) > 0:
                    report["errors"].append("atlas must not contain EXIF metadata")
                _inspect_rows(image, report)
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        report["errors"].append(f"unsafe image: decompression bomb: {error}")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        report["errors"].append(f"atlas image is invalid: {error}")


def validate_atlas(
    path: Path,
    max_bytes: int = MAX_ATLAS_BYTES,
    *,
    require_build_state: bool = True,
) -> dict[str, Any]:
    """Return a stable validation report without modifying the atlas or build state."""
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise ValueError("max_bytes must be a nonnegative integer")
    if not isinstance(require_build_state, bool):
        raise TypeError("require_build_state must be a bool")

    atlas_path = _absolute_path(path)
    report = _empty_report(atlas_path, max_bytes)
    location_is_valid = (
        atlas_path.name == "spritesheet.png" and atlas_path.parent.name == "final"
    )
    if require_build_state:
        if location_is_valid:
            run_dir = atlas_path.parent.parent
            atlas_root = run_dir
        else:
            report["errors"].append("atlas must be located at <run>/final/spritesheet.png")
            run_dir = None
            atlas_root = atlas_path.parent
    else:
        run_dir = None
        atlas_root = atlas_path.parent

    try:
        payload = _read_regular_snapshot(atlas_path, max_bytes, atlas_root)
    except SnapshotError as error:
        report["file_size"] = error.file_size
        report["errors"].append(f"atlas file is unsafe or unreadable: {error}")
        payload = None
    else:
        report["file_size"] = len(payload)
        report["sha256"] = hashlib.sha256(payload).hexdigest()

    if run_dir is not None:
        _check_build_state(run_dir, report)
    if payload is not None:
        _inspect_image(payload, report)
    report["ok"] = not report["errors"]
    return report


def write_validation_report(report: dict[str, Any], output: Path) -> None:
    """Atomically replace a JSON validation report."""
    pet_common.write_json(output, report)


def _max_bytes_argument(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from error
    if result < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate a Codex pet sprite atlas")
    parser.add_argument("atlas", type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument(
        "--max-bytes",
        type=_max_bytes_argument,
        default=MAX_ATLAS_BYTES,
        help=argparse.SUPPRESS,
    )
    return parser


def _normalized_resolved_path(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False)))


def _validate_report_output(output: Path, atlas_path: Path) -> None:
    try:
        output_stat = os.lstat(output)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as error:
        raise ValueError(f"JSON output could not be inspected: {error}") from error
    else:
        if stat.S_ISLNK(output_stat.st_mode) or _is_reparse_point(output_stat):
            raise ValueError("JSON output must not be a symbolic link or reparse point")
        if not stat.S_ISREG(output_stat.st_mode):
            raise ValueError("JSON output must be a regular file or absent")

    try:
        normalized_output = _normalized_resolved_path(output)
        protected_paths = {
            _normalized_resolved_path(atlas_path): "atlas",
            _normalized_resolved_path(
                atlas_path.parent.parent / "build_state.json"
            ): "build_state.json",
        }
    except (OSError, RuntimeError, ValueError) as error:
        raise ValueError(f"JSON output could not be resolved: {error}") from error
    protected_name = protected_paths.get(normalized_output)
    if protected_name is not None:
        raise ValueError(f"JSON output must not overwrite {protected_name}")


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    atlas_path = _absolute_path(arguments.atlas)
    output = (
        _absolute_path(arguments.json_out)
        if arguments.json_out is not None
        else atlas_path.parent / "validation.json"
    )
    report = validate_atlas(atlas_path, max_bytes=arguments.max_bytes)
    try:
        _validate_report_output(output, atlas_path)
        write_validation_report(report, output)
    except (OSError, ValueError) as error:
        print(f"error: could not write validation report: {error}", file=sys.stderr)
        return 2

    summary = {
        "ok": report["ok"],
        "atlas_path": report["atlas_path"],
        "report_path": str(output),
        "error_count": len(report["errors"]),
        "warning_count": len(report["warnings"]),
    }
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    if report["ok"]:
        return 0

    first_error = report["errors"][0] if report["errors"] else "unknown validation error"
    print(
        f"error: invalid atlas ({len(report['errors'])} error(s)): {first_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

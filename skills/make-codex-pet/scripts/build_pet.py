from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Mapping, Sequence
from io import BytesIO
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any
import uuid
import warnings

from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from pet_common import (
    ANIMATION_ROWS,
    ATLAS_COLUMNS,
    ATLAS_HEIGHT,
    ATLAS_ROWS,
    ATLAS_WIDTH,
    CELL_HEIGHT,
    CELL_WIDTH,
    is_relative_to,
    parse_hex_color,
    read_json,
    write_json,
)


APPROVED_STYLES = {"auto", "q-cartoon", "pixel", "sticker"}
DEFAULT_DURATION_MS = 120
CONTACT_LABEL_WIDTH = 128
CHECKER_SIZE = 16
PREVIEW_CHECKER_SIZE = 16
MAX_INPUT_BYTES = 40 * 1024 * 1024
MAX_IMAGE_SIDE = 16_384
MAX_IMAGE_PIXELS = 32_000_000
OUTPUT_DIRECTORIES = ("frames", "final", "qa")


class BuildError(ValueError):
    """Raised when inputs or generated artifacts cannot satisfy the build contract."""


def _byte_parameter(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 255:
        raise ValueError(f"{name} must be an integer from 0 to 255")
    return value


def _rgb_key(key: str | Sequence[int]) -> tuple[int, int, int]:
    if isinstance(key, str):
        return parse_hex_color(key)
    if len(key) != 3:
        raise ValueError("chroma key must contain exactly three RGB components")
    components = tuple(_byte_parameter("chroma key component", component) for component in key)
    return components[0], components[1], components[2]


def _valid_padding(padding: int) -> int:
    if isinstance(padding, bool) or not isinstance(padding, int):
        raise ValueError("padding must be an integer")
    if padding < 1 or padding * 2 >= CELL_WIDTH or padding * 2 >= CELL_HEIGHT:
        raise ValueError(
            f"padding must leave positive space inside {CELL_WIDTH}x{CELL_HEIGHT} cells"
        )
    return padding


def remove_chroma(
    image: Image.Image,
    key: str | Sequence[int],
    tolerance: int = 36,
    feather: int = 24,
) -> Image.Image:
    """Remove edge-connected chroma while preserving isolated foreground colors."""
    tolerance = _byte_parameter("tolerance", tolerance)
    feather = _byte_parameter("feather", feather)
    key_red, key_green, key_blue = _rgb_key(key)
    source = image.convert("RGBA")
    source_pixels = source.load()
    feather_limit = tolerance + feather
    width, height = source.size
    candidates = bytearray(width * height)
    dominant_index = max(range(3), key=lambda index: (key_red, key_green, key_blue)[index])
    sorted_key = sorted((key_red, key_green, key_blue), reverse=True)
    has_dominant_key = sorted_key[0] - sorted_key[1] >= 64

    for y in range(source.height):
        for x in range(source.width):
            red, green, blue, alpha = source_pixels[x, y]
            distance = math.sqrt(
                (red - key_red) ** 2
                + (green - key_green) ** 2
                + (blue - key_blue) ** 2
            )
            channels = (red, green, blue)
            dominant = channels[dominant_index]
            other_max = max(
                channel for index, channel in enumerate(channels) if index != dominant_index
            )
            same_dominance = (
                has_dominant_key and dominant >= 96 and dominant - other_max >= 24
            )
            if alpha == 0 or distance <= feather_limit or same_dominance:
                candidates[y * width + x] = 1

    queue: deque[tuple[int, int]] = deque()
    visited = bytearray(width * height)

    def enqueue(x: int, y: int) -> None:
        index = y * width + x
        if candidates[index] and not visited[index]:
            visited[index] = 1
            queue.append((x, y))

    for x in range(width):
        enqueue(x, 0)
        if height > 1:
            enqueue(x, height - 1)
    for y in range(1, height - 1):
        enqueue(0, y)
        if width > 1:
            enqueue(width - 1, y)

    while queue:
        x, y = queue.popleft()
        for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
            for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                if neighbor_x != x or neighbor_y != y:
                    enqueue(neighbor_x, neighbor_y)

    result = source.copy()
    result_pixels = result.load()
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = source_pixels[x, y]
            if alpha == 0:
                output_alpha = 0
            elif visited[y * width + x]:
                distance = math.sqrt(
                    (red - key_red) ** 2
                    + (green - key_green) ** 2
                    + (blue - key_blue) ** 2
                )
                channels = (red, green, blue)
                dominant = channels[dominant_index]
                other_max = max(
                    channel
                    for index, channel in enumerate(channels)
                    if index != dominant_index
                )
                same_dominance = (
                    has_dominant_key and dominant >= 96 and dominant - other_max >= 24
                )
                if (
                    same_dominance
                    or distance <= tolerance
                    or distance >= feather_limit
                    or not feather
                ):
                    output_alpha = 0
                else:
                    output_alpha = int(round(alpha * (distance - tolerance) / feather))
            else:
                output_alpha = alpha
            result_pixels[x, y] = (red, green, blue, output_alpha)
    return result


def remove_tiny_components(image: Image.Image, max_pixels: int = 16) -> Image.Image:
    """Remove isolated alpha components no larger than max_pixels, except the largest."""
    if isinstance(max_pixels, bool) or not isinstance(max_pixels, int) or max_pixels < 0:
        raise ValueError("max_pixels must be a nonnegative integer")
    source = image.convert("RGBA")
    width, height = source.size
    alpha = source.getchannel("A")
    alpha_pixels = alpha.load()
    visited = bytearray(width * height)
    result = source.copy()
    result_pixels = result.load()
    largest_size = 0
    largest_small_component: list[int] | None = None

    def erase(pixel_indices: Sequence[int]) -> None:
        for pixel_index in pixel_indices:
            x = pixel_index % width
            y = pixel_index // width
            red, green, blue, _ = result_pixels[x, y]
            result_pixels[x, y] = (red, green, blue, 0)

    for start_y in range(height):
        for start_x in range(width):
            start_index = start_y * width + start_x
            if visited[start_index] or alpha_pixels[start_x, start_y] == 0:
                continue
            visited[start_index] = 1
            queue: deque[int] = deque((start_index,))
            component_size = 0
            small_component: list[int] = []
            while queue:
                pixel_index = queue.popleft()
                x = pixel_index % width
                y = pixel_index // width
                component_size += 1
                if component_size <= max_pixels:
                    small_component.append(pixel_index)
                elif small_component:
                    small_component.clear()
                for neighbor_y in range(max(0, y - 2), min(height, y + 3)):
                    for neighbor_x in range(max(0, x - 2), min(width, x + 3)):
                        index = neighbor_y * width + neighbor_x
                        if (
                            not visited[index]
                            and alpha_pixels[neighbor_x, neighbor_y] > 0
                        ):
                            visited[index] = 1
                            queue.append(index)

            if component_size > largest_size:
                if largest_small_component is not None:
                    erase(largest_small_component)
                largest_size = component_size
                largest_small_component = (
                    small_component if component_size <= max_pixels else None
                )
            elif component_size <= max_pixels:
                erase(small_component)
    return result


def split_strip(strip: Image.Image, frame_count: int) -> list[Image.Image]:
    """Split a horizontal strip at round(i * width / count) boundaries."""
    if isinstance(frame_count, bool) or not isinstance(frame_count, int) or frame_count <= 0:
        raise ValueError("frame_count must be a positive integer")
    width, height = strip.size
    boundaries = [round(index * width / frame_count) for index in range(frame_count + 1)]
    if height < 1 or any(right <= left for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("every frame slot must be at least 1 pixel wide and high")
    return [
        strip.crop((left, 0, right, height)).copy()
        for left, right in zip(boundaries, boundaries[1:])
    ]


def _alpha_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    return image.convert("RGBA").getchannel("A").getbbox()


def normalize_row(
    frames: Sequence[Image.Image],
    padding: int = 12,
    resample: Image.Resampling = Image.Resampling.LANCZOS,
) -> list[Image.Image]:
    """Normalize a row with one scale and a common bottom baseline."""
    padding = _valid_padding(padding)
    if not frames:
        raise ValueError("frames must not be empty")
    rgba_frames = [frame.convert("RGBA") for frame in frames]
    boxes = [_alpha_bbox(frame) for frame in rgba_frames]
    if any(box is None for box in boxes):
        empty_index = boxes.index(None)
        raise ValueError(f"frame {empty_index} has no nontransparent pixels")
    concrete_boxes = [box for box in boxes if box is not None]
    maximum_width = max(right - left for left, _, right, _ in concrete_boxes)
    maximum_height = max(bottom - top for _, top, _, bottom in concrete_boxes)
    available_width = CELL_WIDTH - 2 * padding
    available_height = CELL_HEIGHT - 2 * padding
    scale = min(available_width / maximum_width, available_height / maximum_height)
    baseline = CELL_HEIGHT - padding

    normalized: list[Image.Image] = []
    for frame, box in zip(rgba_frames, concrete_boxes):
        cropped = frame.crop(box)
        resized_width = max(1, round(cropped.width * scale))
        resized_height = max(1, round(cropped.height * scale))
        resized = cropped.resize((resized_width, resized_height), resample=resample)
        left = (CELL_WIDTH - resized_width) // 2
        top = baseline - resized_height
        canvas = Image.new("RGBA", (CELL_WIDTH, CELL_HEIGHT), (0, 0, 0, 0))
        canvas.paste(resized, (left, top))
        normalized.append(canvas)
    return normalized


def compose_atlas(rows: Sequence[Sequence[Image.Image]]) -> Image.Image:
    """Place normalized rows into an 8x9 transparent atlas."""
    if len(rows) > ATLAS_ROWS:
        raise ValueError(f"atlas supports at most {ATLAS_ROWS} rows")
    atlas = Image.new("RGBA", (ATLAS_WIDTH, ATLAS_HEIGHT), (0, 0, 0, 0))
    for row_index, frames in enumerate(rows):
        if len(frames) > ATLAS_COLUMNS:
            raise ValueError(f"atlas row {row_index} supports at most {ATLAS_COLUMNS} frames")
        for column, frame in enumerate(frames):
            if frame.size != (CELL_WIDTH, CELL_HEIGHT):
                raise ValueError(
                    f"atlas frame {row_index}/{column} must be {CELL_WIDTH}x{CELL_HEIGHT}"
                )
            atlas.paste(frame.convert("RGBA"), (column * CELL_WIDTH, row_index * CELL_HEIGHT))
    return atlas


def _stage_image(
    image: Image.Image,
    output: Path,
    *,
    image_format: str,
    save_kwargs: Mapping[str, Any] | None = None,
) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        clean_image = Image.new(image.mode, image.size)
        clean_image.paste(image)
        clean_image.save(temporary, format=image_format, **dict(save_kwargs or {}))
        with temporary.open("r+b") as staged_file:
            os.fsync(staged_file.fileno())
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _save_image_atomic(
    image: Image.Image,
    output: str | os.PathLike[str],
    *,
    image_format: str,
    save_kwargs: Mapping[str, Any] | None = None,
) -> Path:
    destination = Path(output)
    temporary = _stage_image(
        image,
        destination,
        image_format=image_format,
        save_kwargs=save_kwargs,
    )
    try:
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _replace_staged(temporary: Path, destination: Path) -> Path:
    try:
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def render_contact_sheet(atlas: Image.Image, output: str | os.PathLike[str]) -> Path:
    """Render a labeled checkerboard QA view without changing the atlas."""
    checker = Image.new("RGBA", (ATLAS_WIDTH, ATLAS_HEIGHT), (224, 224, 224, 255))
    draw = ImageDraw.Draw(checker)
    for top in range(0, ATLAS_HEIGHT, CHECKER_SIZE):
        for left in range(0, ATLAS_WIDTH, CHECKER_SIZE):
            if (left // CHECKER_SIZE + top // CHECKER_SIZE) % 2:
                draw.rectangle(
                    (left, top, left + CHECKER_SIZE - 1, top + CHECKER_SIZE - 1),
                    fill=(184, 184, 184, 255),
                )
    checker.alpha_composite(atlas.convert("RGBA"))
    sheet = Image.new(
        "RGBA",
        (CONTACT_LABEL_WIDTH + ATLAS_WIDTH, ATLAS_HEIGHT),
        (36, 36, 36, 255),
    )
    sheet.paste(checker, (CONTACT_LABEL_WIDTH, 0))
    sheet_draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for row_index, (state, _) in enumerate(ANIMATION_ROWS):
        top = row_index * CELL_HEIGHT
        sheet_draw.text((8, top + 8), state, fill=(255, 255, 255, 255), font=font)
        sheet_draw.line(
            (CONTACT_LABEL_WIDTH, top, CONTACT_LABEL_WIDTH + ATLAS_WIDTH, top),
            fill=(0, 0, 0, 255),
            width=2,
        )
    for column in range(ATLAS_COLUMNS + 1):
        left = CONTACT_LABEL_WIDTH + column * CELL_WIDTH
        sheet_draw.line((left, 0, left, ATLAS_HEIGHT), fill=(0, 0, 0, 255), width=2)
    destination = Path(output)
    return _save_image_atomic(sheet, destination, image_format="PNG")


def _named_rows(
    rows: Mapping[str, Sequence[Image.Image]] | Sequence[Sequence[Image.Image]],
) -> list[tuple[str, Sequence[Image.Image]]]:
    if isinstance(rows, Mapping):
        return list(rows.items())
    if len(rows) != len(ANIMATION_ROWS):
        raise ValueError(f"previews require exactly {len(ANIMATION_ROWS)} rows")
    return [(state, frames) for (state, _), frames in zip(ANIMATION_ROWS, rows)]


def render_previews(
    rows: Mapping[str, Sequence[Image.Image]] | Sequence[Sequence[Image.Image]],
    output_dir: str | os.PathLike[str],
    duration_ms: int = DEFAULT_DURATION_MS,
) -> dict[str, Path]:
    """Write one atomic looping GIF per row and return its path by state."""
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms <= 0:
        raise ValueError("duration_ms must be a positive integer")
    destination_dir = Path(output_dir)
    result: dict[str, Path] = {}
    for state, frames in _named_rows(rows):
        if not frames:
            raise ValueError(f"preview row {state!r} must not be empty")
        rgba_frames: list[Image.Image] = []
        for frame in frames:
            source = frame.convert("RGBA")
            prepared = Image.new("RGB", source.size, (232, 232, 232))
            checker_draw = ImageDraw.Draw(prepared)
            for top in range(0, prepared.height, PREVIEW_CHECKER_SIZE):
                for left in range(0, prepared.width, PREVIEW_CHECKER_SIZE):
                    if (
                        left // PREVIEW_CHECKER_SIZE + top // PREVIEW_CHECKER_SIZE
                    ) % 2:
                        checker_draw.rectangle(
                            (
                                left,
                                top,
                                left + PREVIEW_CHECKER_SIZE - 1,
                                top + PREVIEW_CHECKER_SIZE - 1,
                            ),
                            fill=(192, 192, 192),
                        )
            prepared.paste(source.convert("RGB"), mask=source.getchannel("A"))
            rgba_frames.append(prepared)
        destination = destination_dir / f"{state}.gif"
        temporary = _stage_image(
            rgba_frames[0],
            destination,
            image_format="GIF",
            save_kwargs={
                "save_all": True,
                "append_images": rgba_frames[1:],
                "duration": duration_ms,
                "loop": 0,
                "disposal": 2,
                "optimize": False,
            },
        )
        try:
            with Image.open(temporary) as preview:
                actual_frame_count = getattr(preview, "n_frames", 1)
            if actual_frame_count != len(rgba_frames):
                raise BuildError(
                    f"preview row {state!r} contains static, duplicate, or identical frames; "
                    f"expected {len(rgba_frames)} frames but GIF retained {actual_frame_count}"
                )
            result[state] = _replace_staged(temporary, destination)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    return result


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def _read_build_inputs(run_dir: Path) -> tuple[tuple[int, int, int], str, dict[str, dict[str, Any]]]:
    request = _require_mapping(read_json(run_dir / "pet_request.json"), "pet_request.json")
    pet = _require_mapping(request.get("pet"), "pet_request.json pet")
    style = pet.get("style")
    if style not in APPROVED_STYLES:
        raise ValueError(f"pet_request.json style must be one of {sorted(APPROVED_STYLES)}")
    chroma_key = request.get("chroma_key")
    if not isinstance(chroma_key, str):
        raise ValueError("pet_request.json chroma_key must be a #RRGGBB string")
    key = parse_hex_color(chroma_key)

    manifest = _require_mapping(read_json(run_dir / "imagegen_jobs.json"), "imagegen_jobs.json")
    raw_jobs = manifest.get("jobs")
    if not isinstance(raw_jobs, list):
        raise ValueError("imagegen_jobs.json jobs must be a list")
    jobs: dict[str, dict[str, Any]] = {}
    for index, raw_job in enumerate(raw_jobs):
        job = _require_mapping(raw_job, f"imagegen_jobs.json job {index}")
        job_id = job.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise ValueError(f"imagegen_jobs.json job {index} has an invalid id")
        if job_id in jobs:
            raise ValueError(f"imagegen_jobs.json contains duplicate job id {job_id!r}")
        jobs[job_id] = job

    required_ids = ["base", *(state for state, _ in ANIMATION_ROWS)]
    missing = [job_id for job_id in required_ids if job_id not in jobs]
    if missing:
        raise ValueError(f"imagegen_jobs.json is missing required job(s): {', '.join(missing)}")
    if jobs["base"].get("status") != "complete":
        raise ValueError("base job must be complete before building the pet")
    if jobs["base"].get("output_path") != "decoded/base.png":
        raise ValueError("base job output_path must be exactly 'decoded/base.png'")
    for state, _ in ANIMATION_ROWS:
        job = jobs[state]
        if job.get("status") != "complete":
            raise ValueError(f"animation row {state!r} must be complete")
        expected = f"decoded/{state}.png"
        if job.get("output_path") != expected:
            raise ValueError(f"animation row {state!r} output_path must be exactly {expected!r}")
    return key, style, jobs


def _is_reparse_point(file_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & reparse_flag)


def _reject_reparse_components(run_dir: Path, target: Path, *, label: str) -> None:
    try:
        relative = target.relative_to(run_dir)
    except ValueError as error:
        raise BuildError(f"{label} is outside the run directory") from error
    current = run_dir
    for path in (run_dir, *(run_dir / Path(*relative.parts[:index]) for index in range(1, len(relative.parts) + 1))):
        try:
            file_stat = os.lstat(path)
        except FileNotFoundError:
            continue
        if _is_reparse_point(file_stat):
            raise BuildError(f"{label} contains a symbolic link or reparse point: {path}")


def _resolve_run_dir(run_dir: str | os.PathLike[str]) -> Path:
    unresolved = Path(os.path.abspath(Path(run_dir).expanduser()))
    try:
        file_stat = os.lstat(unresolved)
    except FileNotFoundError as error:
        raise BuildError(f"run directory must be an existing directory: {unresolved}") from error
    if _is_reparse_point(file_stat):
        raise BuildError(f"run directory must not be a symbolic link or reparse point: {unresolved}")
    if not stat.S_ISDIR(file_stat.st_mode):
        raise BuildError(f"run directory must be an existing directory: {unresolved}")
    return unresolved.resolve()


def _validate_output_targets(run_dir: Path) -> None:
    try:
        run_stat = os.lstat(run_dir)
    except FileNotFoundError as error:
        raise BuildError(f"run directory disappeared during build: {run_dir}") from error
    if _is_reparse_point(run_stat) or not stat.S_ISDIR(run_stat.st_mode):
        raise BuildError(f"run directory must remain a regular directory: {run_dir}")
    for name in (*OUTPUT_DIRECTORIES, "build_state.json"):
        target = run_dir / name
        try:
            target_stat = os.lstat(target)
        except FileNotFoundError:
            continue
        if _is_reparse_point(target_stat):
            raise BuildError(f"output {name} must not be a symbolic link or reparse point")
        if name in OUTPUT_DIRECTORIES and not stat.S_ISDIR(target_stat.st_mode):
            raise BuildError(f"output {name} must be a directory")
        if name == "build_state.json" and not stat.S_ISREG(target_stat.st_mode):
            raise BuildError("build_state.json must be a regular file")
        if not is_relative_to(target.resolve(), run_dir):
            raise BuildError(f"output {name} resolves outside the run directory")


def _load_png(run_dir: Path, label: str, path: Path) -> Image.Image:
    """Read one bounded PNG snapshot and return a metadata-free RGBA image."""
    _reject_reparse_components(run_dir, path, label=f"{label} output")
    try:
        file_stat = os.lstat(path)
    except FileNotFoundError as error:
        raise BuildError(f"{label} output is missing or not a regular PNG: {path}") from error
    if _is_reparse_point(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise BuildError(f"{label} output is missing or not a regular PNG: {path}")
    resolved = path.resolve()
    if not is_relative_to(resolved, run_dir):
        raise BuildError(f"{label} output resolves outside the run directory")
    if file_stat.st_size > MAX_INPUT_BYTES:
        raise BuildError(
            f"{label} output size exceeds the {MAX_INPUT_BYTES} byte input limit"
        )
    try:
        data = resolved.read_bytes()
    except OSError as error:
        raise BuildError(f"cannot read {label} output PNG: {error}") from error
    if len(data) > MAX_INPUT_BYTES:
        raise BuildError(
            f"{label} output size exceeds the {MAX_INPUT_BYTES} byte input limit"
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format != "PNG":
                    raise BuildError(f"{label} output must be a real PNG file")
                width, height = source.size
                if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
                    raise BuildError(
                        f"{label} output dimensions exceed the {MAX_IMAGE_SIDE} pixel side limit"
                    )
                if width * height > MAX_IMAGE_PIXELS:
                    raise BuildError(
                        f"{label} output pixel count exceeds the {MAX_IMAGE_PIXELS} pixel limit"
                    )
                source.load()
                converted = source.convert("RGBA")
                result = Image.new("RGBA", converted.size, (0, 0, 0, 0))
                result.paste(converted)
                return result
    except BuildError:
        raise
    except (Image.DecompressionBombWarning, Image.DecompressionBombError) as error:
        raise BuildError(f"{label} output is an unsafe decompression bomb") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise BuildError(f"{label} output must be a valid PNG file") from error


def _safe_remove_tree(path: Path, *, run_dir: Path, staging_root: Path) -> None:
    absolute = Path(os.path.abspath(path))
    fixed_outputs = {run_dir / name for name in OUTPUT_DIRECTORIES}
    if absolute not in fixed_outputs:
        try:
            absolute.relative_to(staging_root)
        except ValueError as error:
            raise BuildError(f"refusing to recursively clean unsafe path: {absolute}") from error
    try:
        file_stat = os.lstat(absolute)
    except FileNotFoundError:
        return
    if _is_reparse_point(file_stat):
        if stat.S_ISDIR(file_stat.st_mode):
            os.rmdir(absolute)
        else:
            absolute.unlink()
        return
    if not stat.S_ISDIR(file_stat.st_mode):
        raise BuildError(f"refusing to recursively clean non-directory path: {absolute}")
    shutil.rmtree(absolute)


def _rollback_published(
    run_dir: Path,
    staging_root: Path,
    records: list[dict[str, Any]],
) -> None:
    rollback_errors: list[str] = []
    for record in reversed(records):
        destination: Path = record["destination"]
        backup: Path = record["backup"]
        try:
            if record["new_published"] and destination.exists():
                _safe_remove_tree(destination, run_dir=run_dir, staging_root=staging_root)
            if record["old_moved"] and backup.exists():
                os.replace(backup, destination)
        except OSError as error:
            rollback_errors.append(f"{destination.name}: {error}")
    records.clear()
    if rollback_errors:
        raise BuildError(f"build rollback failed: {'; '.join(rollback_errors)}")


def _publish_staged(
    run_dir: Path,
    staging_root: Path,
    records: list[dict[str, Any]],
) -> None:
    backups = staging_root / "_backups"
    backups.mkdir()
    for name in OUTPUT_DIRECTORIES:
        _validate_output_targets(run_dir)
        destination = run_dir / name
        staged = staging_root / name
        backup = backups / name
        record = {
            "destination": destination,
            "backup": backup,
            "old_moved": False,
            "new_published": False,
        }
        records.append(record)
        if destination.exists():
            os.replace(destination, backup)
            record["old_moved"] = True
        os.replace(staged, destination)
        record["new_published"] = True


def _write_build_state(
    run_dir: Path,
    *,
    status: str,
    build_id: str,
    **fields: Any,
) -> None:
    _validate_output_targets(run_dir)
    write_json(
        run_dir / "build_state.json",
        {
            "schema_version": 1,
            "status": status,
            "build_id": build_id,
            **fields,
        },
    )


def build_pet(
    run_dir: str | os.PathLike[str],
    tolerance: int = 36,
    feather: int = 24,
    padding: int = 12,
) -> dict[str, Any]:
    """Build normalized frames, QA artifacts, and the final sprite atlas."""
    tolerance = _byte_parameter("tolerance", tolerance)
    feather = _byte_parameter("feather", feather)
    padding = _valid_padding(padding)
    selected_run_dir = _resolve_run_dir(run_dir)
    _validate_output_targets(selected_run_dir)
    build_id = uuid.uuid4().hex
    _write_build_state(selected_run_dir, status="building", build_id=build_id)
    staging_root: Path | None = None
    publish_records: list[dict[str, Any]] = []
    committed = False
    try:
        staging_root = Path(
            tempfile.mkdtemp(
                prefix=f".build-staging-{build_id}-",
                dir=selected_run_dir,
            )
        )
        stage_frames = staging_root / "frames"
        stage_final = staging_root / "final"
        stage_qa = staging_root / "qa"
        for directory in (stage_frames, stage_final, stage_qa):
            directory.mkdir()

        key, style, _ = _read_build_inputs(selected_run_dir)
        _load_png(selected_run_dir, "base", selected_run_dir / "decoded" / "base.png")
        resample = (
            Image.Resampling.NEAREST if style == "pixel" else Image.Resampling.LANCZOS
        )

        normalized_by_state: dict[str, list[Image.Image]] = {}
        for state, frame_count in ANIMATION_ROWS:
            strip_path = selected_run_dir / "decoded" / f"{state}.png"
            strip = _load_png(selected_run_dir, state, strip_path)
            extracted: list[Image.Image] = []
            for frame_index, frame in enumerate(split_strip(strip, frame_count)):
                transparent = remove_tiny_components(
                    remove_chroma(
                        frame,
                        key,
                        tolerance=tolerance,
                        feather=feather,
                    )
                )
                if _alpha_bbox(transparent) is None:
                    raise BuildError(
                        f"animation row {state!r} frame {frame_index} "
                        "has no nontransparent pixels"
                    )
                extracted.append(transparent)
            normalized_by_state[state] = normalize_row(
                extracted,
                padding=padding,
                resample=resample,
            )

        ordered_rows = [normalized_by_state[state] for state, _ in ANIMATION_ROWS]
        atlas = compose_atlas(ordered_rows)

        frames_manifest_rows: list[dict[str, Any]] = []
        for state, frames in normalized_by_state.items():
            frame_paths: list[str] = []
            for frame_index, frame in enumerate(frames):
                relative_path = Path("frames") / state / f"{frame_index:02d}.png"
                _save_image_atomic(
                    frame,
                    staging_root / relative_path,
                    image_format="PNG",
                )
                frame_paths.append(relative_path.as_posix())
            frames_manifest_rows.append(
                {
                    "state": state,
                    "frame_count": len(frames),
                    "frames": frame_paths,
                }
            )

        staged_frames_manifest = stage_frames / "frames-manifest.json"
        write_json(
            staged_frames_manifest,
            {
                "schema_version": 1,
                "cell": {"width": CELL_WIDTH, "height": CELL_HEIGHT},
                "atlas": {
                    "columns": ATLAS_COLUMNS,
                    "rows": ATLAS_ROWS,
                    "width": ATLAS_WIDTH,
                    "height": ATLAS_HEIGHT,
                },
                "rows": frames_manifest_rows,
            },
        )

        staged_contact_sheet = stage_qa / "contact-sheet.png"
        render_contact_sheet(atlas, staged_contact_sheet)
        render_previews(
            normalized_by_state,
            stage_qa / "previews",
            duration_ms=DEFAULT_DURATION_MS,
        )

        staged_spritesheet = stage_final / "spritesheet.png"
        _save_image_atomic(atlas, staged_spritesheet, image_format="PNG")
        spritesheet_sha256 = hashlib.sha256(staged_spritesheet.read_bytes()).hexdigest()

        _validate_output_targets(selected_run_dir)
        _publish_staged(
            selected_run_dir,
            staging_root,
            publish_records,
        )
        try:
            _write_build_state(
                selected_run_dir,
                status="complete",
                build_id=build_id,
                spritesheet="final/spritesheet.png",
                spritesheet_sha256=spritesheet_sha256,
            )
        except BaseException:
            _rollback_published(selected_run_dir, staging_root, publish_records)
            raise

        committed = True
        publish_records.clear()

        spritesheet = selected_run_dir / "final" / "spritesheet.png"
        contact_sheet = selected_run_dir / "qa" / "contact-sheet.png"
        frames_manifest = selected_run_dir / "frames" / "frames-manifest.json"
        preview_paths = {
            state: selected_run_dir / "qa" / "previews" / f"{state}.gif"
            for state, _ in ANIMATION_ROWS
        }
        cleanup_warnings: list[str] = []
        try:
            _safe_remove_tree(
                staging_root,
                run_dir=selected_run_dir,
                staging_root=staging_root,
            )
            staging_root = None
        except OSError as cleanup_error:
            cleanup_warnings.append(f"staging cleanup failed: {cleanup_error}")
        summary = {
            "ok": True,
            "run_dir": str(selected_run_dir),
            "spritesheet": str(spritesheet.resolve()),
            "contact_sheet": str(contact_sheet.resolve()),
            "previews": {
                state: str(path.resolve()) for state, path in preview_paths.items()
            },
            "frames_manifest": str(frames_manifest.resolve()),
        }
        if cleanup_warnings:
            summary["warnings"] = cleanup_warnings
        return summary
    except BaseException as error:
        if committed:
            raise
        rollback_error: BaseException | None = None
        if staging_root is not None and publish_records:
            try:
                _rollback_published(
                    selected_run_dir,
                    staging_root,
                    publish_records,
                )
            except BaseException as caught_rollback_error:
                rollback_error = caught_rollback_error
        if staging_root is not None:
            try:
                _safe_remove_tree(
                    staging_root,
                    run_dir=selected_run_dir,
                    staging_root=staging_root,
                )
            except BaseException as cleanup_error:
                if rollback_error is None:
                    rollback_error = cleanup_error
        failure_message = str(rollback_error or error)[:500]
        try:
            _write_build_state(
                selected_run_dir,
                status="failed",
                build_id=build_id,
                error=failure_message,
            )
        except BaseException as state_error:
            if rollback_error is None:
                rollback_error = state_error
        if rollback_error is not None:
            raise rollback_error from error
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic Codex pet sprite atlas.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--tolerance", type=int, default=36)
    parser.add_argument("--feather", type=int, default=24)
    parser.add_argument("--padding", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = build_pet(
            args.run_dir,
            tolerance=args.tolerance,
            feather=args.feather,
            padding=args.padding,
        )
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

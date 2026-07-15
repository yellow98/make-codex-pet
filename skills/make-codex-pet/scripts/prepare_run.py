from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

from pet_common import (
    ANIMATION_ROWS,
    parse_hex_color,
    read_json,
    resolve_codex_home,
    slugify_pet_id,
    write_json,
    write_text_atomic,
)


APPROVED_STYLES = ("auto", "q-cartoon", "pixel", "sticker")
SCHEMA_VERSION = 1
KNOWN_RUN_ARTIFACTS = (
    "pet_request.json",
    "imagegen_jobs.json",
    "prompts",
    "decoded",
    "frames",
    "final",
    "qa",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a resumable Codex pet generation run.")
    parser.add_argument("--pet-name", required=True)
    parser.add_argument("--pet-id")
    parser.add_argument("--description", default="")
    parser.add_argument("--style", choices=APPROVED_STYLES, default="auto")
    parser.add_argument("--identity-feature", action="append", required=True)
    parser.add_argument("--reference", action="append", required=True)
    parser.add_argument("--chroma-key", default="#00FF00")
    parser.add_argument("--output-dir")
    parser.add_argument("--force", action="store_true")
    return parser


def normalize_identity_features(features: list[str]) -> list[str]:
    if not 3 <= len(features) <= 5:
        raise ValueError("provide 3 to 5 identity features")
    normalized = [feature.strip() for feature in features]
    if any(not feature for feature in normalized):
        raise ValueError("identity features must not be empty")
    return normalized


def resolve_references(references: list[str]) -> list[Path]:
    resolved: list[Path] = []
    for raw_path in references:
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"reference must be an existing regular file: {raw_path}")
        resolved.append(path)
    return resolved


def normalize_chroma_key(value: str) -> str:
    red, green, blue = parse_hex_color(value)
    return f"#{red:02X}{green:02X}{blue:02X}"


def make_base_prompt(
    *,
    display_name: str,
    description: str,
    style: str,
    identity_features: list[str],
    references: list[Path],
    chroma_key: str,
) -> str:
    identity_lines = "\n".join(f"- {feature}" for feature in identity_features)
    reference_lines = "\n".join(f"- {reference}" for reference in references)
    description_line = description or "No additional description."
    return (
        "# Character base\n\n"
        f"Create one consistent, full-body character preview for {display_name}.\n"
        f"Description: {description_line}\n"
        f"Style: {style}\n"
        f"Chroma key: {chroma_key}\n\n"
        "Identity lock:\n"
        f"{identity_lines}\n\n"
        "Original reference files:\n"
        f"{reference_lines}\n\n"
        "Keep the face, hair, clothing, colors, and body proportions faithful to the identity lock. "
        "Use a flat chroma-key background. Show the whole body without cropping. "
        "No text, labels, borders, or UI.\n"
    )


def make_animation_prompt(
    *,
    state: str,
    frame_count: int,
    display_name: str,
    style: str,
    identity_features: list[str],
    references: list[Path],
    chroma_key: str,
) -> str:
    identity_lines = "\n".join(f"- {feature}" for feature in identity_features)
    reference_lines = "\n".join(f"- {reference}" for reference in references)
    return (
        f"# Animation row: {state}\n\n"
        f"Create a horizontal sprite strip for {display_name}.\n"
        f"Action: {state}\n"
        f"Create exactly {frame_count} separate animation frames.\n"
        f"Style: {style}\n"
        f"Chroma key: {chroma_key}\n\n"
        "Identity lock:\n"
        f"{identity_lines}\n\n"
        "Use both the approved base character and these original reference files:\n"
        f"{reference_lines}\n\n"
        "Keep the face, hair, clothing, colors, and body proportions identical in every frame. "
        "Show the complete body in every frame. Keep frames clearly separated on a single row. "
        "No text, labels, numbers, UI, or borders. No frame overlap. No cropping. "
        "No detached effects such as speed lines, dust, glows, or particles separated from the body.\n"
    )


def make_job(job_id: str, kind: str, depends_on: list[str]) -> dict[str, object]:
    return {
        "id": job_id,
        "kind": kind,
        "status": "pending",
        "depends_on": depends_on,
        "prompt_file": f"prompts/{job_id}.md",
        "output_path": f"decoded/{job_id}.png",
        "attempts": 0,
        "last_error": None,
    }


def validate_existing_run(run_dir: Path) -> None:
    for filename in ("pet_request.json", "imagegen_jobs.json"):
        marker = run_dir / filename
        if not marker.is_file():
            raise ValueError(f"--force requires a valid pet run with {filename}")
        try:
            document = read_json(marker)
        except (OSError, ValueError) as error:
            raise ValueError(f"--force requires a valid pet run; cannot parse {filename}") from error
        if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"--force requires a valid pet run with schema_version {SCHEMA_VERSION}")


def clean_known_run_artifacts(run_dir: Path) -> None:
    for artifact_name in KNOWN_RUN_ARTIFACTS:
        artifact = run_dir / artifact_name
        if artifact.is_symlink() or artifact.is_file():
            artifact.unlink()
        elif artifact.is_dir():
            shutil.rmtree(artifact)


def prepare_run(args: argparse.Namespace) -> dict[str, object]:
    display_name = args.pet_name.strip()
    if not display_name:
        raise ValueError("pet name must not be empty")
    pet_id = slugify_pet_id(display_name, args.pet_id)
    identity_features = normalize_identity_features(args.identity_feature)
    references = resolve_references(args.reference)
    chroma_key = normalize_chroma_key(args.chroma_key)

    now = datetime.now(timezone.utc)
    created_at = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    if args.output_dir:
        run_dir = Path(args.output_dir).expanduser().resolve()
    else:
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        run_dir = resolve_codex_home() / "pet-runs" / f"{pet_id}-{timestamp}"

    if run_dir.exists() and not run_dir.is_dir():
        raise ValueError(f"output path is not a directory: {run_dir}")
    if run_dir.is_dir() and any(run_dir.iterdir()):
        if not args.force:
            raise ValueError(f"output directory is non-empty; pass --force to overwrite: {run_dir}")
        validate_existing_run(run_dir)
        clean_known_run_artifacts(run_dir)

    prompts_dir = run_dir / "prompts"
    for directory in (
        prompts_dir,
        run_dir / "decoded",
        run_dir / "frames",
        run_dir / "final",
        run_dir / "qa",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    request = {
        "schema_version": SCHEMA_VERSION,
        "pet": {
            "id": pet_id,
            "display_name": display_name,
            "description": args.description,
            "style": args.style,
        },
        "identity_features": identity_features,
        "references": [str(reference) for reference in references],
        "chroma_key": chroma_key,
        "created_at": created_at,
    }

    base_prompt = make_base_prompt(
        display_name=display_name,
        description=args.description,
        style=args.style,
        identity_features=identity_features,
        references=references,
        chroma_key=chroma_key,
    )
    write_text_atomic(prompts_dir / "base.md", base_prompt)

    jobs = [make_job("base", "base", [])]
    for state, frame_count in ANIMATION_ROWS:
        prompt = make_animation_prompt(
            state=state,
            frame_count=frame_count,
            display_name=display_name,
            style=args.style,
            identity_features=identity_features,
            references=references,
            chroma_key=chroma_key,
        )
        write_text_atomic(prompts_dir / f"{state}.md", prompt)
        jobs.append(make_job(state, "animation-row", ["base"]))

    request_path = run_dir / "pet_request.json"
    jobs_path = run_dir / "imagegen_jobs.json"
    write_json(request_path, request)
    write_json(jobs_path, {"schema_version": SCHEMA_VERSION, "jobs": jobs})

    return {
        "ok": True,
        "run_dir": str(run_dir),
        "request": str(request_path),
        "jobs": str(jobs_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        summary = prepare_run(args)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import shlex
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "weights" / "manifest.json"
INCLUDE_PATTERNS = (
    ".gitattributes",
    ".github/workflows/*.yml",
    "app/*.py",
    "app/policies/*.json",
    "app/static/*.html",
    "ci/*.py",
    "ci/*.sh",
    "deployment/*.example",
    "deployment/*.yml",
    "docs/*.md",
    "monitoring/*.config",
    "monitoring/*.yml",
    "monitoring/grafana/dashboards/*.json",
    "monitoring/grafana/provisioning/**/*.yml",
    "monitoring/*.json",
    "postman/*.json",
    "terraform/.terraform.lock.hcl",
    "terraform/*.tf",
    "tests/*.py",
    "gdw_x/**/*.py",
    "weights/cardinality-baseline.json",
    "Dockerfile",
    "README.md",
    "pyproject.toml",
)
LIMITS = {
    "max_file_bytes": 512_000,
    "max_total_bytes": 2_500_000,
}


def binary_attribute_patterns() -> tuple[str, ...]:
    patterns: list[str] = []
    attributes_path = ROOT / ".gitattributes"
    if not attributes_path.is_file():
        return ()
    for raw_line in attributes_path.read_text(encoding="utf-8").splitlines():
        fields = shlex.split(raw_line, comments=True)
        if len(fields) < 2:
            continue
        pattern, *attributes = fields
        if "binary" in attributes or "-text" in attributes:
            patterns.append(pattern)
    return tuple(patterns)


def path_is_binary(path: Path) -> bool:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    return any(
        fnmatch.fnmatchcase(relative, pattern)
        or ("/" not in pattern and fnmatch.fnmatchcase(path.name, pattern))
        for pattern in binary_attribute_patterns()
    )


def canonical_artifact_bytes(path: Path, content: bytes) -> bytes:
    """Normalize governed UTF-8 text while leaving binary content untouched."""
    if path_is_binary(path) or b"\x00" in content:
        return content
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    return text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")


def governed_bytes(path: Path) -> bytes:
    return canonical_artifact_bytes(path, path.read_bytes())


def tracked_paths() -> list[Path]:
    paths: set[Path] = set()
    for pattern in INCLUDE_PATTERNS:
        paths.update(path for path in ROOT.glob(pattern) if path.is_file())
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def build_manifest() -> dict:
    artifacts = []
    for path in tracked_paths():
        content = governed_bytes(path)
        artifacts.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "schema_version": 1,
        "generated_by": "python ci/update_weight_manifest.py --write",
        "content_policy": {
            "binary": "raw bytes",
            "text": "UTF-8 with LF line endings",
        },
        "limits": LIMITS,
        "artifacts": artifacts,
    }


def serialized_manifest() -> str:
    return json.dumps(build_manifest(), indent=2, sort_keys=True) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review or update the deterministic artifact-weight manifest.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if the checked-in manifest is stale.")
    mode.add_argument("--write", action="store_true", help="Explicitly rewrite the checked-in manifest.")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = serialized_manifest()
    if args.write:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(expected, encoding="utf-8", newline="\n")
        print(f"wrote {MANIFEST_PATH.relative_to(ROOT)}")
        return 0
    actual = MANIFEST_PATH.read_text(encoding="utf-8") if MANIFEST_PATH.exists() else ""
    if actual != expected:
        print("artifact-weight manifest is stale; review the diff, then run with --write")
        return 1
    print("artifact-weight manifest is current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

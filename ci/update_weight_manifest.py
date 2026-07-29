from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "weights" / "manifest.json"
INCLUDE_PATTERNS = (
    ".github/workflows/*.yml",
    "app/*.py",
    "ci/*.py",
    "ci/*.sh",
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
    "weights/cardinality-baseline.json",
    "Dockerfile",
    "README.md",
    "pyproject.toml",
)
LIMITS = {
    "max_file_bytes": 512_000,
    "max_total_bytes": 2_500_000,
}


def tracked_paths() -> list[Path]:
    paths: set[Path] = set()
    for pattern in INCLUDE_PATTERNS:
        paths.update(path for path in ROOT.glob(pattern) if path.is_file())
    return sorted(paths, key=lambda path: path.relative_to(ROOT).as_posix())


def build_manifest() -> dict:
    artifacts = []
    for path in tracked_paths():
        content = path.read_bytes()
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
        MANIFEST_PATH.write_text(expected, encoding="utf-8")
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

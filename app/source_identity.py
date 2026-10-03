from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_REVISION_PATTERN = re.compile(rb"[0-9a-f]{40}\n\Z")


def deployed_source_sha() -> str:
    """Read an opt-in, publisher-generated revision without trusting stale env claims."""
    revision_file = os.environ.get("GDW_SOURCE_REVISION_FILE")
    if revision_file is None:
        return os.getenv("GDW_SOURCE_SHA", "UNAVAILABLE")
    if not revision_file.strip():
        raise RuntimeError("GDW_SOURCE_REVISION_FILE must name a revision file")
    path = Path(revision_file)
    if not path.is_absolute():
        raise RuntimeError("GDW_SOURCE_REVISION_FILE must be an absolute path")

    try:
        with path.open("rb") as stream:
            content = stream.read(42)
    except OSError as error:
        raise RuntimeError("GDW_SOURCE_REVISION_FILE is unavailable") from error
    if SOURCE_REVISION_PATTERN.fullmatch(content) is None:
        raise RuntimeError(
            "GDW_SOURCE_REVISION_FILE must contain one lowercase 40-hex SHA and LF"
        )

    source_sha = content[:40].decode("ascii")
    environment_sha = os.getenv("GDW_SOURCE_SHA")
    if environment_sha not in (None, "", "UNAVAILABLE", source_sha):
        raise RuntimeError("GDW_SOURCE_SHA conflicts with GDW_SOURCE_REVISION_FILE")
    return source_sha


def source_tree_digest(root: Path = ROOT) -> str:
    paths = [
        root / "README.md",
        root / "pyproject.toml",
        *sorted((root / "app").glob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in paths:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


if __name__ == "__main__":
    print(source_tree_digest())
